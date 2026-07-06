import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from orchestrator.kernel.states import ActorRole
from orchestrator.services.packages import register_revision
from tests.conftest import TEST_DATABASE_URL
from tests.persistence.conftest import alembic_config
from tests.services.test_package_registration import AUTHORITY, NOW, register_test_revision


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

    claim_columns = {
        column["name"]: column for column in inspector.get_columns("claims")
    }
    assert {"context_snapshot_id", "execution_context_snapshot_id"} <= claim_columns.keys()
    assert claim_columns["context_snapshot_id"]["nullable"] is True
    assert claim_columns["execution_context_snapshot_id"]["nullable"] is True

    evidence_columns = {
        column["name"]: column for column in inspector.get_columns("evidence")
    }
    assert "context_snapshot_id" in evidence_columns
    assert evidence_columns["context_snapshot_id"]["nullable"] is True

    claim_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in inspector.get_foreign_keys("claims")
    }
    assert claim_foreign_keys["fk_claims_context_snapshot_id"]["referred_table"] == "context_snapshots"
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
        approval_event_id=revision_one.id.__class__(int=2),
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
