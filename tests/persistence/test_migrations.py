import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.decomposition import (
    AcMapping,
    DecompositionProposalCommand,
    ProposedDependency,
    ProposedUnit,
    RetainedAc,
    approve_decomposition_proposal,
    submit_decomposition_proposal,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.package_intake import register_package_intake
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.production_drills import start_production_drill
from tests.conftest import TEST_DATABASE_URL
from tests.persistence.conftest import alembic_config
from tests.services.test_package_intake import acceptance_criterion, intake_command
from tests.services.test_package_registration import AUTHORITY, NOW, register_test_revision
from tests.services.test_production_drills import (
    command as production_drill_command,
)
from tests.services.test_production_drills import (
    revision as production_drill_revision,
)
from tests.services.test_production_drills import (
    runtime_observation,
)


def column_default(engine, table: str, column: str) -> str | None:
    return next(
        item["default"] for item in inspect(engine).get_columns(table) if item["name"] == column
    )


def test_alembic_upgrades_empty_database() -> None:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    config = alembic_config()
    command.upgrade(config, "head")

    with engine.connect() as connection:
        current_revision = MigrationContext.configure(connection).get_current_revision()
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    engine.dispose()

    assert current_revision == head_revision


def test_alembic_downgrade_removes_schema_and_can_reupgrade(migrated_engine) -> None:
    config = alembic_config()

    command.downgrade(config, "base")

    inspector = inspect(migrated_engine)
    assert not {
        "work_packages",
        "work_package_revisions",
        "work_units",
        "dependencies",
        "claims",
        "approvals",
        "evidence",
        "adjudications",
        "events",
    }.intersection(inspector.get_table_names())
    with migrated_engine.connect() as connection:
        remaining_functions = connection.scalars(
            text(
                "SELECT proname FROM pg_proc WHERE proname IN "
                "('reject_append_only_mutation', 'enforce_work_unit_revision_immutable', "
                "'set_work_unit_updated_at')"
            )
        ).all()
    assert remaining_functions == []

    command.upgrade(config, "head")

    assert set(inspect(migrated_engine).get_table_names()) >= {
        "work_packages",
        "work_package_revisions",
        "work_units",
        "dependencies",
        "claims",
        "approvals",
        "evidence",
        "adjudications",
        "events",
    }


def test_default_attempt_budget_migration_is_reversible(migrated_engine) -> None:
    config = alembic_config()

    assert column_default(migrated_engine, "work_units", "max_attempts") == "3"

    command.downgrade(config, "0001_ws31_core")
    assert column_default(migrated_engine, "work_units", "max_attempts") == "1"

    command.upgrade(config, "head")
    assert column_default(migrated_engine, "work_units", "max_attempts") == "3"


def test_runtime_observations_downgrade_restores_prior_provenance_trigger(migrated_engine) -> None:
    with Session(migrated_engine) as session:
        package_revision = production_drill_revision(session)
        observation_id = runtime_observation(session, key="migration-downgrade")
        run = start_production_drill(
            session,
            production_drill_command(
                package_revision.id,
                key="migration-downgrade",
                runtime_observation_id=observation_id,
            ),
        )
        assert not isinstance(run, DomainError)
        run_id = run.id
        session.commit()

    config = alembic_config()
    try:
        command.downgrade(config, "0016_production_drill_resources")

        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE production_drill_runs "
                    "SET status = 'closed', closed_at = now(), closure_reason = 'completed' "
                    "WHERE id = :id"
                ),
                {"id": run_id},
            )

        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE production_drill_runs SET image_digest = 'changed' WHERE id = :id"
                    ),
                    {"id": run_id},
                )
    finally:
        command.upgrade(config, "head")


def test_ws32_tables_exist_after_upgrade(migrated_session) -> None:
    tables = {
        row[0]
        for row in migrated_session.execute(
            text("select tablename from pg_tables where schemaname = 'public'")
        )
    }
    assert "package_acceptance_criteria" in tables
    assert "decomposition_proposals" in tables
    assert "decomposition_proposal_units" in tables
    assert "decomposition_proposal_dependencies" in tables
    assert "decomposition_proposal_ac_mappings" in tables
    assert "decomposition_proposal_retained_acs" in tables
    assert "approved_decompositions" in tables


def test_ws33_context_snapshot_tables_and_links_exist(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    columns = {column["name"] for column in inspector.get_columns("context_snapshots")}
    assert {
        "id",
        "work_package_revision_id",
        "work_unit_id",
        "claim_id",
        "attempt",
        "actor_id",
        "actor_role",
        "context",
        "context_fingerprint",
        "classification",
        "decision",
        "approval_id",
        "event_id",
        "idempotency_key",
        "created_at",
    } <= columns

    claim_columns = {column["name"]: column for column in inspector.get_columns("claims")}
    assert {"context_snapshot_id", "execution_context_snapshot_id"} <= claim_columns.keys()
    assert claim_columns["context_snapshot_id"]["nullable"] is True
    assert claim_columns["execution_context_snapshot_id"]["nullable"] is True

    evidence_columns = {column["name"]: column for column in inspector.get_columns("evidence")}
    assert "context_snapshot_id" in evidence_columns
    assert evidence_columns["context_snapshot_id"]["nullable"] is True

    claim_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in inspector.get_foreign_keys("claims")
    }
    assert (
        claim_foreign_keys["fk_claims_context_snapshot_id"]["referred_table"] == "context_snapshots"
    )
    assert claim_foreign_keys["fk_claims_context_snapshot_id"]["referred_columns"] == ["id"]
    assert claim_foreign_keys["fk_claims_execution_context_snapshot_id"]["referred_table"] == (
        "context_snapshots"
    )
    assert claim_foreign_keys["fk_claims_execution_context_snapshot_id"]["referred_columns"] == [
        "id"
    ]

    evidence_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in inspector.get_foreign_keys("evidence")
    }
    assert evidence_foreign_keys["fk_evidence_context_snapshot_id"]["referred_table"] == (
        "context_snapshots"
    )
    assert evidence_foreign_keys["fk_evidence_context_snapshot_id"]["referred_columns"] == ["id"]


def test_ws34_event_publication_table_exists(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "event_publications" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("event_publications")}
    assert {
        "id",
        "source_system",
        "source_kind",
        "source_id",
        "source_action",
        "event_id",
        "mapping_version",
        "status",
        "skip_reason",
        "factory_event",
        "export_ref",
        "attempt_count",
        "last_error",
        "created_at",
        "updated_at",
        "last_attempted_at",
        "published_at",
    } <= columns


def test_ws62_knowledge_promotion_tables_exist(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "knowledge_promotion_proposals" in inspector.get_table_names()
    assert "knowledge_promotion_proposal_actions" in inspector.get_table_names()
    proposal_columns = {
        column["name"] for column in inspector.get_columns("knowledge_promotion_proposals")
    }
    assert {
        "id",
        "correlation_identity",
        "source_observation_ids",
        "source_observation_hashes",
        "correlation_summary",
        "target_brain",
        "target_type",
        "authority",
        "applicability",
        "proposed_payload",
        "provenance",
        "proposal_hash",
        "proposed_by",
        "proposed_at",
        "event_id",
        "idempotency_key",
    } <= proposal_columns
    action_columns = {
        column["name"] for column in inspector.get_columns("knowledge_promotion_proposal_actions")
    }
    assert {
        "id",
        "proposal_id",
        "action",
        "brain_record_id",
        "brain_status",
        "brain_response",
        "action_by",
        "action_at",
        "event_id",
        "idempotency_key",
    } <= action_columns

    with migrated_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgname IN ("
                    "'reject_knowledge_promotion_proposals_mutation', "
                    "'reject_knowledge_promotion_proposal_actions_mutation')"
                )
            )
        )
    assert triggers == {
        "reject_knowledge_promotion_proposals_mutation",
        "reject_knowledge_promotion_proposal_actions_mutation",
    }


def test_ws42_dispatch_records_table_exists(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "dispatch_records" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("dispatch_records")}
    assert {
        "id",
        "work_unit_id",
        "work_package_revision_id",
        "runner_attempt",
        "status",
        "reason_code",
        "idempotency_key",
        "target_repository",
        "workflow_id",
        "workflow_ref",
        "github_run_id",
        "github_run_url",
        "failure_signature",
        "payload",
        "event_id",
        "created_at",
        "updated_at",
    } <= columns

    unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("dispatch_records")
    }
    assert ("idempotency_key",) in unique_constraints
    assert ("work_unit_id", "runner_attempt") in unique_constraints


def test_ws44_infra_lane_links_table_exists(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "infra_lane_links" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("infra_lane_links")}
    assert {
        "id",
        "work_unit_id",
        "work_package_revision_id",
        "attempt",
        "status",
        "change_manager_ref",
        "change_manager_url",
        "infraops_ref",
        "approval_ref",
        "rollback_ref",
        "verify_ref",
        "final_evidence_ref",
        "payload",
        "recorded_by",
        "recorded_at",
        "event_id",
        "idempotency_key",
    } <= columns

    unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("infra_lane_links")
    }
    assert ("idempotency_key",) in unique_constraints


def test_ws52_release_artifact_bindings_table_exists(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "release_artifact_bindings" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("release_artifact_bindings")}
    assert {
        "id",
        "work_unit_id",
        "work_package_revision_id",
        "package_revision_hash",
        "source_repository",
        "implementation_pr_number",
        "source_commit",
        "merge_commit",
        "artifact_registry",
        "artifact_repository",
        "artifact_name",
        "artifact_digest",
        "artifact_tag",
        "workflow_run_id",
        "workflow_run_attempt",
        "workflow_path",
        "workflow_ref",
        "workflow_run_url",
        "builder_id",
        "builder_class",
        "provenance_ref",
        "provenance_digest",
        "sbom_ref",
        "sbom_digest",
        "summary",
        "recorded_by",
        "recorded_at",
        "event_id",
        "evidence_id",
        "idempotency_key",
    } <= columns

    unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("release_artifact_bindings")
    }
    assert ("idempotency_key",) in unique_constraints
    assert (
        "work_package_revision_id",
        "work_unit_id",
        "source_repository",
        "merge_commit",
        "source_commit",
        "artifact_registry",
        "artifact_repository",
        "artifact_name",
    ) in unique_constraints


def test_ws53_deployment_observations_table_exists(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "deployment_observations" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("deployment_observations")}
    assert {
        "id",
        "release_artifact_binding_id",
        "implementation_work_unit_id",
        "work_package_revision_id",
        "package_revision_hash",
        "post_deploy_work_unit_id",
        "environment",
        "base_url",
        "observed_artifact_digest",
        "deployment_ref",
        "deployment_url",
        "deployer",
        "observed_at",
        "probe_summary",
        "route_summary",
        "auth_summary",
        "dispatch_summary",
        "status_summary",
        "recorded_by",
        "recorded_at",
        "event_id",
        "post_deploy_event_id",
        "evidence_ids",
        "idempotency_key",
    } <= columns

    unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("deployment_observations")
    }
    assert ("idempotency_key",) in unique_constraints
    assert ("release_artifact_binding_id", "environment") in unique_constraints

    foreign_keys = {
        tuple(key["constrained_columns"]): key["referred_table"]
        for key in inspector.get_foreign_keys("deployment_observations")
    }
    assert foreign_keys[("release_artifact_binding_id",)] == "release_artifact_bindings"
    assert foreign_keys[("implementation_work_unit_id",)] == "work_units"
    assert foreign_keys[("post_deploy_work_unit_id",)] == "work_units"
    assert foreign_keys[("work_package_revision_id",)] == "work_package_revisions"
    assert foreign_keys[("event_id",)] == "events"
    assert foreign_keys[("post_deploy_event_id",)] == "events"


def test_ws32_package_cli_intake_requires_verified_mode(migrated_session) -> None:
    package_id = register_test_revision(migrated_session).work_package_id
    migrated_session.commit()

    manual_revision_id = package_id.__class__(int=10)
    migrated_session.execute(
        text(
            "INSERT INTO work_package_revisions "
            "(id, work_package_id, revision, content_hash, source_path, source_commit, "
            "approved_by, approved_at, approval_event_id, enforcement_snapshot, "
            "authority_fingerprint, registry_version, registered_by, intake_source, "
            "verification_mode) VALUES "
            "(:id, :work_package_id, 2, 'sha256:manual', 'intent.md', 'def456', "
            "'human-1', now(), :approval_event_id, '{}', 'authority', 1, 'human-1', "
            "'manual_ws31', NULL)"
        ),
        {
            "id": manual_revision_id,
            "work_package_id": package_id,
            "approval_event_id": package_id.__class__(int=11),
        },
    )
    migrated_session.commit()

    migrated_session.execute(
        text(
            "INSERT INTO work_package_revisions "
            "(id, work_package_id, revision, content_hash, source_path, source_commit, "
            "approved_by, approved_at, approval_event_id, enforcement_snapshot, "
            "authority_fingerprint, registry_version, registered_by, intake_source, "
            "verification_mode) VALUES "
            "(:id, :work_package_id, 5, 'sha256:fixture', 'intent.md', 'mno345', "
            "'human-1', now(), :approval_event_id, '{}', 'authority', 1, 'human-1', "
            "'protocol_fixture', NULL)"
        ),
        {
            "id": package_id.__class__(int=16),
            "work_package_id": package_id,
            "approval_event_id": package_id.__class__(int=17),
        },
    )
    migrated_session.commit()

    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                "INSERT INTO work_package_revisions "
                "(id, work_package_id, revision, content_hash, source_path, source_commit, "
                "approved_by, approved_at, approval_event_id, enforcement_snapshot, "
                "authority_fingerprint, registry_version, registered_by, intake_source, "
                "verification_mode) VALUES "
                "(:id, :work_package_id, 3, 'sha256:cli-missing', 'intent.md', 'ghi789', "
                "'human-1', now(), :approval_event_id, '{}', 'authority', 1, 'human-1', "
                "'package_cli', NULL)"
            ),
            {
                "id": package_id.__class__(int=12),
                "work_package_id": package_id,
                "approval_event_id": package_id.__class__(int=13),
            },
        )
        migrated_session.commit()
    migrated_session.rollback()

    migrated_session.execute(
        text(
            "INSERT INTO work_package_revisions "
            "(id, work_package_id, revision, content_hash, source_path, source_commit, "
            "approved_by, approved_at, approval_event_id, enforcement_snapshot, "
            "authority_fingerprint, registry_version, registered_by, intake_source, "
            "verification_mode) VALUES "
            "(:id, :work_package_id, 4, 'sha256:cli-verified', 'intent.md', 'jkl012', "
            "'human-1', now(), :approval_event_id, '{}', 'authority', 1, 'human-1', "
            "'package_cli', 'caller_attested_cli_verified')"
        ),
        {
            "id": package_id.__class__(int=14),
            "work_package_id": package_id,
            "approval_event_id": package_id.__class__(int=15),
        },
    )
    migrated_session.commit()


def test_ws32_approved_decomposition_must_match_proposal_revision(migrated_session) -> None:
    revision_one = register_test_revision(migrated_session)
    migrated_session.commit()
    revision_two = register_revision(
        migrated_session,
        package_id="pkg-2",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:two",
        source_path="intent.md",
        source_commit="def456",
        approved_by="human-2",
        approved_at=NOW,
        approval_event_id=str(revision_one.id.__class__(int=2)),
        enforcement_snapshot={"acceptance_criteria": ["ac-2"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-2",
        actor_role=ActorRole.HUMAN,
    )
    migrated_session.commit()

    proposal_id = revision_one.id.__class__(int=3)
    approved_id = revision_one.id.__class__(int=4)
    migrated_session.execute(
        text(
            "INSERT INTO decomposition_proposals "
            "(id, work_package_revision_id, proposal_number, state, rationale, "
            "proposed_by, proposed_actor_role, idempotency_key) "
            "VALUES (:id, :revision_id, 1, 'proposed', 'rationale', "
            "'human-1', 'human', :idempotency_key)"
        ),
        {
            "id": proposal_id,
            "revision_id": revision_one.id,
            "idempotency_key": "proposal-1",
        },
    )
    migrated_session.commit()

    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                "INSERT INTO approved_decompositions "
                "(id, work_package_revision_id, proposal_id, approved_by) "
                "VALUES (:id, :revision_id, :proposal_id, 'human-2')"
            ),
            {
                "id": approved_id,
                "revision_id": revision_two.id,
                "proposal_id": proposal_id,
            },
        )
        migrated_session.commit()
    migrated_session.rollback()


def test_work_unit_authority_upgrade_prefers_proposal_unit_authority_when_available(
    migrated_engine,
) -> None:
    raw_unit_authority = {
        "capabilities": {
            "repo.read": "allowed",
            "repo.edit": "allowed",
            "command.run": "allowed",
        },
        "budgets": {"max_attempts": 2, "max_llm_calls": 5},
        "constraints": {
            "target_repository": "AlobarQuest/orchestrator",
            "allowed_commands": ["make check"],
        },
    }
    fallback_authority = {
        "capabilities": {"repository_write": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {"target_repository": "AlobarQuest/fallback"},
    }
    with Session(migrated_engine) as session:
        manual_revision = register_revision(
            session,
            package_id="pkg-manual-authority-upgrade",
            source_repository="owner/manual",
            revision=1,
            content_hash="sha256:manual-authority-upgrade",
            source_path="intent.md",
            source_commit="manual123",
            approved_by="human-1",
            approved_at=NOW,
            approval_event_id="evt-manual-authority-upgrade",
            enforcement_snapshot={"authority": fallback_authority},
            authority=normalize_authority(fallback_authority),
            registry_version=1,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )
        register_approved_unit(
            session,
            revision_id=manual_revision.id,
            unit_key="manual-unit",
            title="Manual unit",
            outcome="Fallback authority should come from the revision.",
            required_capability="repository_write",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

        intake_revision = register_package_intake(
            session,
            intake_command(
                package_id="pkg-decomposition-authority-upgrade",
                idempotency_key="package-intake-authority-upgrade",
                content_hash="sha256:decomposition-authority-upgrade",
                authority=AUTHORITY,
                acceptance_criteria=(
                    acceptance_criterion("AC-001"),
                    acceptance_criterion("AC-002"),
                ),
            ),
            ActorContext("human-1", ActorRole.HUMAN),
        )
        ac_rows = tuple(
            session.execute(
                text(
                    "SELECT id, ac_id FROM package_acceptance_criteria "
                    "WHERE work_package_revision_id = :revision_id ORDER BY ac_id"
                ),
                {"revision_id": intake_revision.id},
            )
        )
        ac_ids = {row.ac_id: row.id for row in ac_rows}
        proposal = submit_decomposition_proposal(
            session,
            DecompositionProposalCommand(
                work_package_revision_id=intake_revision.id,
                rationale="Split by authority-bearing unit.",
                proposed_units=(
                    ProposedUnit(
                        unit_key="unit-1",
                        title="Implement service",
                        outcome="Service persists proposals.",
                        required_capability="repository_write",
                        authority=normalize_authority(raw_unit_authority),
                        authority_payload=raw_unit_authority,
                        max_attempts=2,
                    ),
                    ProposedUnit(
                        unit_key="unit-2",
                        title="Implement tests",
                        outcome="Service is covered by focused tests.",
                        required_capability="repository_write",
                        authority=normalize_authority(fallback_authority),
                        authority_payload=fallback_authority,
                    ),
                ),
                dependencies=(
                    ProposedDependency(
                        source_unit_key="unit-2",
                        kind="work_unit",
                        required_state_or_condition="completed",
                        target_unit_key="unit-1",
                    ),
                ),
                ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="unit-1"),),
                retained_acs=(
                    RetainedAc(
                        ac_id=str(ac_ids["AC-002"]),
                        rationale="Leave AC-002 at package scope.",
                    ),
                ),
                idempotency_key="proposal-authority-upgrade",
            ),
            ActorContext("worker-1", ActorRole.WORKER),
        )
        approved = approve_decomposition_proposal(
            session,
            proposal.id,
            actor=ActorContext("human-1", ActorRole.HUMAN),
            reason="Approve for authority backfill test.",
            idempotency_key="proposal-authority-upgrade-approve",
        )
        session.commit()
        assert isinstance(approved.created_work_unit_ids, dict)
        decomposition_unit_id = approved.created_work_unit_ids["unit-1"]

    config = alembic_config()
    command.downgrade(config, "0006_approval_event_id_text")
    command.upgrade(config, "head")

    with Session(migrated_engine) as session:
        units = {
            unit.unit_key: unit
            for unit in session.scalars(
                select(WorkUnit)
                .where(
                    WorkUnit.unit_key.in_(("manual-unit", "unit-1")),
                )
                .order_by(WorkUnit.unit_key)
            )
        }

    assert str(units["unit-1"].id) == decomposition_unit_id
    assert units["unit-1"].authority == {
        **raw_unit_authority,
        "constraints": {
            **raw_unit_authority["constraints"],
            "work_unit_id": decomposition_unit_id,
        },
    }
    assert units["manual-unit"].authority == normalize_authority(fallback_authority).normalized()


def test_ws61_observations_table_exists(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "observations" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("observations")}
    assert {
        "id",
        "source_system",
        "source_reference",
        "source_url",
        "trust_classification",
        "subject_type",
        "subject_reference",
        "environment",
        "observation_type",
        "status",
        "severity",
        "observed_at",
        "received_at",
        "summary",
        "facts",
        "normalized_fact_hash",
        "payload_digest",
        "recorded_by",
        "event_id",
        "idempotency_key",
    } <= columns

    unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("observations")
    }
    assert ("idempotency_key",) in unique_constraints
    assert ("source_system", "source_reference", "normalized_fact_hash") in unique_constraints


def test_wsp21_recovery_tables_exist(migrated_engine) -> None:
    inspector = inspect(migrated_engine)
    tables = inspector.get_table_names()

    assert "reconciliation_conditions" in tables
    assert "reconciliation_resolutions" in tables
    assert "unit_pr_binding" in tables

    condition_columns = {c["name"] for c in inspector.get_columns("reconciliation_conditions")}
    assert {
        "id",
        "work_unit_id",
        "observation_kind",
        "observation_id",
        "deployment_observation_id",
        "condition_type",
        "stored_state",
        "observed_state",
        "lineage_hash",
        "resolution_generation",
        "normalized_divergence_hash",
        "detail",
        "detected_at",
        "event_id",
        "idempotency_key",
    } <= condition_columns

    condition_uniques = {
        tuple(c["column_names"])
        for c in inspector.get_unique_constraints("reconciliation_conditions")
    }
    assert ("idempotency_key",) in condition_uniques
    assert ("work_unit_id", "observation_kind", "normalized_divergence_hash") in condition_uniques

    resolution_uniques = {
        tuple(c["column_names"])
        for c in inspector.get_unique_constraints("reconciliation_resolutions")
    }
    # A condition is resolvable exactly once; a recurrence mints a NEW condition.
    assert ("condition_id",) in resolution_uniques
    assert ("idempotency_key",) in resolution_uniques

    with migrated_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgname IN ("
                    "'reject_reconciliation_conditions_mutation', "
                    "'reject_reconciliation_resolutions_mutation', "
                    "'reject_unit_pr_binding_mutation')"
                )
            )
        )
    # unit_pr_binding is deliberately NOT append-only: head_sha is mutable (design 1.6).
    assert triggers == {
        "reject_reconciliation_conditions_mutation",
        "reject_reconciliation_resolutions_mutation",
    }


def test_wsp21_evidence_unsuperseded_head_index_exists(migrated_engine) -> None:
    """The partial unique index is what structurally forecloses a SECOND supersession head.

    Two heads make _terminal raise, which means the AC can never be adjudicated and no further
    evidence can be written -- and `evidence` is append-only, so the row could never be repaired
    and the unit could never complete.
    """
    indexes = {i["name"]: i for i in inspect(migrated_engine).get_indexes("evidence")}
    head_index = indexes["uq_evidence_unsuperseded_head"]

    assert head_index["unique"] is True
    assert head_index["column_names"] == [
        "work_package_revision_id",
        "work_unit_id",
        "ac_id",
    ]
    assert "supersedes_evidence_id IS NULL" in head_index["dialect_options"]["postgresql_where"]
