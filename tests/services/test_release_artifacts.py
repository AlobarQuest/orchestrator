import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, Evidence, ReleaseArtifactBinding, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.release_artifacts import (
    ReleaseArtifactCommand,
    _stored_command,
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


# ---------------------------------------------------------------------------
# ADR-0030: the machine-local kind.
#
# The subject is the estate's SECOND activation model -- a change becomes live on the operator
# machine when the code is pulled into a working copy. It reuses this table with an explicit kind
# discriminator, and every test below exists because a placeholder in one of the registry columns
# would make the two models indistinguishable in exactly the columns a reader separates them by.
# ---------------------------------------------------------------------------

MACHINE_DIGEST = "sha256:" + "9" * 64


def machine_local_command(
    unit: WorkUnit, *, key: str = "machine-activation-1"
) -> ReleaseArtifactCommand:
    """A machine-local binding, with the registry three ABSENT rather than blanked."""
    return replace(
        command(unit, key=key),
        kind="machine_local",
        artifact_registry=None,
        artifact_repository=None,
        artifact_name=None,
        artifact_digest=MACHINE_DIGEST,
        artifact_tag=None,
        workflow_run_id=None,
        workflow_run_attempt=None,
        workflow_path=None,
        workflow_ref=None,
        workflow_run_url=None,
        builder_id=None,
        builder_class=None,
        provenance_ref=None,
        provenance_digest=None,
        sbom_ref=None,
        sbom_digest=None,
        summary={"activation": {"path": "/Users/x/Projects/orchestrator", "head": "a" * 40}},
    )


def test_a_machine_local_binding_is_recorded_with_no_registry_coordinates(
    migrated_session: Session,
) -> None:
    unit = completed_unit(migrated_session, key="machine-local-unit")

    binding = record_release_artifact(migrated_session, machine_local_command(unit))

    assert isinstance(binding, ReleaseArtifactBinding)
    assert binding.kind == "machine_local"
    assert binding.artifact_registry is None
    assert binding.artifact_repository is None
    assert binding.artifact_name is None
    assert binding.artifact_digest == MACHINE_DIGEST


def test_a_container_binding_and_a_machine_local_binding_differ_in_one_readable_field(
    migrated_session: Session,
) -> None:
    """Acceptance 3: told apart by reading ONE field, without knowing which repository is which.

    Both bindings are recorded against the SAME unit deliberately. A unit can legitimately reach a
    registry image and a working copy, so the discriminator has to separate them where nothing
    else does -- not merely where the repositories happen to differ.
    """
    unit = completed_unit(migrated_session, key="both-kinds-unit")

    container = record_release_artifact(migrated_session, command(unit, key="container-1"))
    machine = record_release_artifact(migrated_session, machine_local_command(unit))

    assert isinstance(container, ReleaseArtifactBinding)
    assert isinstance(machine, ReleaseArtifactBinding)
    assert {container.kind, machine.kind} == {"container_image", "machine_local"}
    rows = list_release_artifacts(migrated_session, unit.id)
    assert isinstance(rows, tuple)
    assert len(rows) == 2


def test_a_machine_local_binding_refuses_a_registry_placeholder(
    migrated_session: Session,
) -> None:
    """REFUSED, not ignored. Silently dropping "local" would let the caller believe it landed."""
    unit = completed_unit(migrated_session, key="placeholder-unit")

    error = record_release_artifact(
        migrated_session,
        replace(machine_local_command(unit), artifact_registry="local"),
    )

    assert isinstance(error, DomainError)
    assert error.code == "release_artifact_invalid"
    assert "artifact_registry" in error.message


def test_a_container_binding_still_requires_its_registry_coordinates(
    migrated_session: Session,
) -> None:
    """The control for the test above: relaxing the columns must not relax the container path."""
    unit = completed_unit(migrated_session, key="container-required-unit")

    error = record_release_artifact(
        migrated_session, replace(command(unit), artifact_registry="  ")
    )

    assert isinstance(error, DomainError)
    assert error.code == "release_artifact_invalid"
    assert "artifact_registry" in error.message


def test_an_unknown_kind_is_refused(migrated_session: Session) -> None:
    unit = completed_unit(migrated_session, key="unknown-kind-unit")

    error = record_release_artifact(migrated_session, replace(command(unit), kind="working_copy"))

    assert isinstance(error, DomainError)
    assert error.code == "release_artifact_kind_invalid"


def test_a_machine_local_binding_still_refuses_a_bare_commit_sha(
    migrated_session: Session,
) -> None:
    """Acceptance 4: `_validate_digests` is unchanged and still refuses a commit.

    This is the control for the whole design. ADR-0030 reused this table rather than relaxing the
    validator, precisely because a digest column silently holding a commit is what would make the
    two models indistinguishable in the data. A machine-local binding therefore supplies a real
    content digest, and a commit is refused for it exactly as for a container image.
    """
    unit = completed_unit(migrated_session, key="bare-commit-unit")

    error = record_release_artifact(
        migrated_session,
        replace(machine_local_command(unit), artifact_digest=MERGE_COMMIT),
    )

    assert isinstance(error, DomainError)
    assert error.code == "release_artifact_digest_invalid"


def test_recording_a_machine_local_binding_twice_replays_rather_than_duplicating(
    migrated_session: Session,
) -> None:
    """Acceptance 6, the deliberate replay path."""
    unit = completed_unit(migrated_session, key="replay-unit")

    first = record_release_artifact(migrated_session, machine_local_command(unit))
    second = record_release_artifact(migrated_session, machine_local_command(unit))

    assert isinstance(first, ReleaseArtifactBinding)
    assert isinstance(second, ReleaseArtifactBinding)
    assert first.id == second.id
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(ReleaseArtifactBinding)
            .where(ReleaseArtifactBinding.work_unit_id == unit.id)
        )
        == 1
    )


def test_a_stored_command_recorded_before_kind_existed_reads_as_a_container_image() -> None:
    """A stored payload with no `kind` must not turn an honest replay into a conflict.

    Every command payload written before ADR-0030 omits the key, and `_validate_idempotent_replay`
    compares the stored payload against the command being replayed -- so without this the
    idempotency guarantee breaks for exactly the historical rows nobody can rewrite.

    ASSERTED ON THE RULE RATHER THAN END TO END, and the reason is a property of the database:
    `events` is append-only (`reject_append_only_mutation`), so no test can age a real event's
    payload into the historical shape. The rule is what a mutation would have to delete, and this
    is what would red.
    """
    modern = {"command": {"artifact_digest": DIGEST, "kind": "machine_local"}}
    historical = {"command": {"artifact_digest": DIGEST}}

    assert _stored_command(modern) == {"artifact_digest": DIGEST, "kind": "machine_local"}
    assert _stored_command(historical) == {
        "artifact_digest": DIGEST,
        "kind": "container_image",
    }
    # A payload that is not a command at all is passed through untouched, so the comparison
    # against a real command fails and the conflict is raised -- never quietly repaired.
    assert _stored_command({"command": "not-a-mapping"}) == "not-a-mapping"
    assert _stored_command({}) is None


def test_the_service_deduplicates_a_repeated_source_tuple_carrying_nulls(
    migrated_session: Session,
) -> None:
    """The SERVICE's source-tuple branch, reached under a different idempotency key.

    THIS DOES NOT MEASURE `NULLS NOT DISTINCT`, and the docstring said it did until a mutation
    proved otherwise: `_existing_source_tuple` compares with `IS NULL` in Python, so the service
    finds the row whether or not the database constraint would. The constraint is the backstop
    beneath this, and it has its own test below. Two claims, two tests -- the merged version was
    correct about the wrong noun.
    """
    unit = completed_unit(migrated_session, key="null-tuple-unit")
    first = record_release_artifact(migrated_session, machine_local_command(unit))
    assert isinstance(first, ReleaseArtifactBinding)

    same = record_release_artifact(
        migrated_session, machine_local_command(unit, key="machine-activation-2")
    )
    conflicting = record_release_artifact(
        migrated_session,
        replace(
            machine_local_command(unit, key="machine-activation-3"),
            artifact_digest=OTHER_DIGEST,
        ),
    )

    assert isinstance(same, ReleaseArtifactBinding)
    assert same.id == first.id
    assert isinstance(conflicting, DomainError)
    assert conflicting.code == "release_artifact_conflict"


def test_a_machine_local_binding_names_the_repository_rather_than_a_registry_path(
    migrated_session: Session,
) -> None:
    """The evidence `stable_ref`.

    Interpolating the three absent columns would render `None/None/None@sha256:...`, which reads
    as a registry reference and is not one.
    """
    unit = completed_unit(migrated_session, key="stable-ref-unit")

    binding = record_release_artifact(migrated_session, machine_local_command(unit))

    assert isinstance(binding, ReleaseArtifactBinding)
    evidence = migrated_session.get(Evidence, binding.evidence_id)
    assert evidence is not None
    assert evidence.stable_ref is not None
    assert evidence.stable_ref == f"machine_local:AlobarQuest/orchestrator@{MACHINE_DIGEST}"
    assert "None" not in evidence.stable_ref


def test_the_source_tuple_constraint_itself_refuses_a_duplicate_carrying_nulls(
    migrated_session: Session,
) -> None:
    """NULLS NOT DISTINCT, measured at the DATABASE by writing round the service.

    Postgres treats NULLs in a unique constraint as distinct by DEFAULT, so making three of this
    tuple's eight columns nullable would silently stop it deduplicating every machine-local row --
    an existing guarantee weakened as a side effect of a migration about something else. The
    service cannot show that: it does the `IS NULL` comparison itself and dedupes either way. So
    this inserts the second row directly, which is the only reader that sees the constraint.

    `release_artifact_bindings` carries no append-only trigger, so the insert is possible; the FK
    columns are reused from the row the service wrote, since neither is part of the tuple.
    """
    unit = completed_unit(migrated_session, key="db-constraint-unit")
    row = record_release_artifact(migrated_session, machine_local_command(unit))
    assert isinstance(row, ReleaseArtifactBinding)

    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO release_artifact_bindings (
                    id, work_unit_id, work_package_revision_id, package_revision_hash,
                    source_repository, source_commit, merge_commit, kind, artifact_digest,
                    summary, recorded_by, recorded_at, event_id, evidence_id, idempotency_key
                ) VALUES (
                    :id, :work_unit_id, :revision_id, :package_revision_hash,
                    :source_repository, :source_commit, :merge_commit, 'machine_local', :digest,
                    '{}'::jsonb, 'system', now(), :event_id, :evidence_id, :idempotency_key
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "work_unit_id": row.work_unit_id,
                "revision_id": row.work_package_revision_id,
                "package_revision_hash": row.package_revision_hash,
                "source_repository": row.source_repository,
                "source_commit": row.source_commit,
                "merge_commit": row.merge_commit,
                "digest": OTHER_DIGEST,
                "event_id": row.event_id,
                "evidence_id": row.evidence_id,
                "idempotency_key": "a-second-key-entirely",
            },
        )
    migrated_session.rollback()
