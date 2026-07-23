import uuid
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, Evidence, ReleaseArtifactBinding, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.release_artifacts import (
    ReleaseArtifactCommand,
    list_release_artifacts,
    record_release_artifact,
)
from tests.services.test_package_registration import AUTHORITY

NOW = datetime(2026, 7, 8, tzinfo=UTC)
HUMAN = ActorContext("human-1", ActorRole.HUMAN)
SYSTEM = ActorContext("system", ActorRole.SYSTEM)
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
MERGE_COMMIT = "4cd4132" + "c" * 33
SOURCE_COMMIT = "4cd4132" + "d" * 33
PACKAGE_HASH = "sha256:release-package"


def completed_unit(session: Session, *, key: str = "release-unit") -> WorkUnit:
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="AlobarQuest/orchestrator",
        revision=1,
        content_hash=PACKAGE_HASH,
        source_path="intent.md",
        source_commit=SOURCE_COMMIT,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-release"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title="Release unit",
        outcome="Immutable release artifact is recorded",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit.state = WorkUnitState.COMPLETED
    session.commit()
    return unit


def command(unit: WorkUnit, *, key: str = "release-artifact-1") -> ReleaseArtifactCommand:
    return ReleaseArtifactCommand(
        work_unit_id=unit.id,
        actor=SYSTEM,
        package_revision_id=unit.work_package_revision_id,
        package_revision_hash=PACKAGE_HASH,
        source_repository="AlobarQuest/orchestrator",
        implementation_pr_number=20,
        source_commit=SOURCE_COMMIT,
        merge_commit=MERGE_COMMIT,
        artifact_registry="ghcr.io",
        artifact_repository="alobarquest/orchestrator",
        artifact_name="orchestrator",
        artifact_digest=DIGEST,
        artifact_tag="a04d094-ws52",
        workflow_run_id="123456789",
        workflow_run_attempt=1,
        workflow_path=".github/workflows/build.yml",
        workflow_ref="refs/heads/main",
        workflow_run_url="https://github.com/AlobarQuest/orchestrator/actions/runs/123456789",
        builder_id="github-actions",
        builder_class="github-hosted",
        provenance_ref="ghcr.io/alobarquest/orchestrator@sha256:" + "c" * 64,
        provenance_digest="sha256:" + "d" * 64,
        sbom_ref="ghcr.io/alobarquest/orchestrator/sbom@sha256:" + "e" * 64,
        sbom_digest="sha256:" + "f" * 64,
        summary={"status": "published", "checks": 12},
        idempotency_key=key,
        expected_version=unit.version,
    )


def test_records_release_artifact_binding_with_evidence_event_and_no_lifecycle_mutation(
    migrated_session: Session,
) -> None:
    unit = completed_unit(migrated_session)
    original_state = unit.state
    original_version = unit.version

    binding = record_release_artifact(migrated_session, command(unit))

    assert isinstance(binding, ReleaseArtifactBinding)
    assert binding.work_unit_id == unit.id
    assert binding.work_package_revision_id == unit.work_package_revision_id
    assert binding.package_revision_hash == PACKAGE_HASH
    assert binding.source_repository == "AlobarQuest/orchestrator"
    assert binding.implementation_pr_number == 20
    assert binding.merge_commit == MERGE_COMMIT
    assert binding.artifact_digest == DIGEST
    assert binding.artifact_tag == "a04d094-ws52"
    assert binding.recorded_by == SYSTEM.actor_id
    assert unit.state == original_state
    assert unit.version == original_version

    event = migrated_session.get(Event, binding.event_id)
    assert event is not None
    assert event.action == "release_artifact.bound"
    assert event.subject_type == "release_artifact_binding"
    assert event.subject_id == binding.id
    assert event.payload["command"]["artifact_digest"] == DIGEST
    assert "token" not in str(event.payload).lower()

    evidence = migrated_session.get(Evidence, binding.evidence_id)
    assert evidence is not None
    assert evidence.evidence_type == "release.artifact_bound"
    assert evidence.ac_id == "release-artifact"
    assert evidence.stable_ref == f"ghcr.io/alobarquest/orchestrator/orchestrator@{DIGEST}"
    assert evidence.payload is not None
    assert evidence.payload["artifact_digest"] == DIGEST
    assert evidence.payload["package_revision_hash"] == PACKAGE_HASH
    assert evidence.payload["workflow"]["run_id"] == "123456789"


def test_release_artifact_rejects_missing_or_mutable_digest(migrated_session: Session) -> None:
    unit = completed_unit(migrated_session, key="digest-required")

    missing = record_release_artifact(
        migrated_session,
        replace(command(unit, key="missing-digest"), artifact_digest=""),
    )
    mutable = record_release_artifact(
        migrated_session,
        replace(command(unit, key="mutable-digest"), artifact_digest="a04d094-ws52"),
    )

    assert isinstance(missing, DomainError)
    assert missing.code == "release_artifact_digest_invalid"
    assert isinstance(mutable, DomainError)
    assert mutable.code == "release_artifact_digest_invalid"


def test_release_artifact_rejects_unknown_and_non_completed_work_units(
    migrated_session: Session,
) -> None:
    completed = completed_unit(migrated_session, key="unknown-reference")
    unknown = record_release_artifact(
        migrated_session,
        replace(command(completed, key="unknown-work-unit"), work_unit_id=uuid.uuid4()),
    )
    ready = completed_unit(migrated_session, key="not-completed")
    ready.state = WorkUnitState.READY
    migrated_session.commit()
    non_completed = record_release_artifact(
        migrated_session,
        replace(command(ready, key="not-completed"), expected_version=ready.version),
    )

    assert isinstance(unknown, DomainError)
    assert unknown.code == "work_unit_not_found"
    assert isinstance(non_completed, DomainError)
    assert non_completed.code == "work_unit_not_completed"


def test_release_artifact_rejects_package_hash_or_revision_mismatch(
    migrated_session: Session,
) -> None:
    unit = completed_unit(migrated_session, key="mismatch")

    wrong_hash = record_release_artifact(
        migrated_session,
        replace(command(unit, key="wrong-hash"), package_revision_hash="sha256:wrong"),
    )
    wrong_revision = record_release_artifact(
        migrated_session,
        replace(command(unit, key="wrong-revision"), package_revision_id=uuid.uuid4()),
    )

    assert isinstance(wrong_hash, DomainError)
    assert wrong_hash.code == "release_artifact_package_hash_mismatch"
    assert isinstance(wrong_revision, DomainError)
    assert wrong_revision.code == "release_artifact_revision_mismatch"


def test_release_artifact_rejects_secret_shaped_metadata(migrated_session: Session) -> None:
    unit = completed_unit(migrated_session, key="secret-shaped")

    secret_key = record_release_artifact(
        migrated_session,
        replace(
            command(unit, key="secret-key"),
            summary={"api_token": "not-a-real-token-fixture"},
        ),
    )
    bearer_value = record_release_artifact(
        migrated_session,
        replace(
            command(unit, key="bearer-value"),
            workflow_run_url="Authorization: Bearer not-a-real-token-fixture",
        ),
    )

    assert isinstance(secret_key, DomainError)
    assert secret_key.code == "release_artifact_secret_rejected"
    assert isinstance(bearer_value, DomainError)
    assert bearer_value.code == "release_artifact_secret_rejected"


def test_release_artifact_replay_is_idempotent_and_conflict_rejects_digest_change(
    migrated_session: Session,
) -> None:
    unit = completed_unit(migrated_session, key="idempotent")

    first = record_release_artifact(migrated_session, command(unit))
    replay = record_release_artifact(migrated_session, command(unit))
    same_tuple_changed_digest = record_release_artifact(
        migrated_session,
        replace(command(unit, key="changed-digest"), artifact_digest=OTHER_DIGEST),
    )
    same_key_changed_command = record_release_artifact(
        migrated_session,
        replace(command(unit), implementation_pr_number=21),
    )

    assert isinstance(first, ReleaseArtifactBinding)
    assert isinstance(replay, ReleaseArtifactBinding)
    assert replay.id == first.id
    assert isinstance(same_tuple_changed_digest, DomainError)
    assert same_tuple_changed_digest.code == "release_artifact_conflict"
    assert isinstance(same_key_changed_command, DomainError)
    assert same_key_changed_command.code == "idempotency_conflict"
    event_count = migrated_session.scalar(
        select(func.count()).where(Event.action == "release_artifact.bound")
    )
    assert event_count == 1


def test_release_artifact_lists_bindings_for_work_unit(migrated_session: Session) -> None:
    unit = completed_unit(migrated_session, key="list-bindings")
    first = record_release_artifact(migrated_session, command(unit, key="first"))
    second = record_release_artifact(
        migrated_session,
        replace(
            command(unit, key="second"),
            merge_commit="5cd4132" + "c" * 33,
            artifact_digest=OTHER_DIGEST,
        ),
    )

    rows = list_release_artifacts(migrated_session, unit.id)
    missing = list_release_artifacts(migrated_session, uuid.uuid4())

    assert isinstance(first, ReleaseArtifactBinding)
    assert isinstance(second, ReleaseArtifactBinding)
    assert not isinstance(rows, DomainError)
    assert [row.id for row in rows] == [first.id, second.id]
    assert isinstance(missing, DomainError)
    assert missing.code == "work_unit_not_found"
