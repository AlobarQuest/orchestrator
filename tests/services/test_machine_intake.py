"""A machine may register an intake, and must name the change record that caused it. ADR-0027.

The guard removed here was protecting a transcription: every intake in production was authored
by an AI and typed into a form by a person. What replaces it is an asymmetry, and the asymmetry
is what these tests are about -- a rule that applied to both registrars would break the
hand-registration escape hatch, and a rule that applied to neither would admit canonical work
with no decision behind it.
"""

import uuid

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import WorkPackageRevision
from orchestrator.services.decomposition import approve_decomposition_proposal
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.package_intake import register_package_intake
from orchestrator.services.packages import record_approval, register_revision
from tests.services.test_package_intake import human_actor, intake_command

CHANGE_RECORD = 8801


def system_actor() -> ActorContext:
    return ActorContext("orchestrator-system", ActorRole.SYSTEM)


def _revisions(session: Session) -> list[WorkPackageRevision]:
    return list(session.scalars(select(WorkPackageRevision)))


def test_the_system_actor_registers_an_intake_that_names_its_change_record(
    migrated_session: Session,
) -> None:
    """The whole increment, in one call: no human, and the revision names its cause."""
    revision = register_package_intake(
        migrated_session,
        intake_command(change_record_id=CHANGE_RECORD),
        system_actor(),
    )
    migrated_session.commit()

    assert revision.change_record_id == CHANGE_RECORD


def test_a_machine_intake_without_a_change_record_is_refused(migrated_session: Session) -> None:
    """The fail-open ADR-0027 names: canonical work with no decision behind it."""
    with pytest.raises(DomainError) as error:
        register_package_intake(migrated_session, intake_command(), system_actor())

    assert error.value.code == "intake_change_record_required"


def test_a_machine_intake_naming_change_record_zero_is_refused(
    migrated_session: Session,
) -> None:
    """Zero is not a record. The schema bounds it at the wire (`gt=0`), and the service is
    reachable without the schema -- so the guard tests the value, not merely its absence."""
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session, intake_command(change_record_id=0), system_actor()
        )

    assert error.value.code == "intake_change_record_required"


def test_a_refused_machine_intake_writes_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Ordering: the guard runs before the write, so a refusal leaves no revision behind.

    Re-read through a DIFFERENT session, because a flushed-but-uncommitted row is visible to
    the session that wrote it.
    """
    with pytest.raises(DomainError):
        register_package_intake(migrated_session, intake_command(), system_actor())
    migrated_session.rollback()

    with Session(migrated_engine) as reader:
        assert _revisions(reader) == []


def test_a_human_may_still_register_without_a_change_record(migrated_session: Session) -> None:
    """The escape hatch. Applying the requirement to both registrars would be a symmetry, and
    the asymmetry is deliberate: a person registering by hand has no record to name, and every
    intake in production before ADR-0026 named none."""
    revision = register_package_intake(migrated_session, intake_command(), human_actor())
    migrated_session.commit()

    assert revision.change_record_id is None


def test_a_worker_may_not_register_an_intake(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(change_record_id=CHANGE_RECORD),
            ActorContext("factory-runner", ActorRole.WORKER),
        )

    assert error.value.code == "intake_registrar_invalid"


def test_a_verifier_may_not_register_an_intake(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(change_record_id=CHANGE_RECORD),
            ActorContext("orchestrator-verifier", ActorRole.VERIFIER),
        )

    assert error.value.code == "intake_registrar_invalid"


def test_an_observer_may_not_register_an_intake(migrated_session: Session) -> None:
    """ADR-0026 gave OBSERVER leave to propose. Registering is a different verb, and ADR-0027
    says so explicitly. `_confine_observer` refuses it at the wire as well; this is the
    service-level refusal, which does not depend on a route template being in an allowlist.
    """
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(change_record_id=CHANGE_RECORD),
            ActorContext("drift-reconciler", ActorRole.OBSERVER),
        )

    assert error.value.code == "intake_registrar_invalid"


def test_an_intake_with_no_actor_at_all_is_refused(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(change_record_id=CHANGE_RECORD),
            ActorContext("", ActorRole.SYSTEM),
        )

    assert error.value.code == "intake_registrar_invalid"


def test_register_revision_still_refuses_the_system_actor_by_default(
    migrated_session: Session,
) -> None:
    """The split, in the direction that matters. `register_revision` is the WS-3.1 bootstrap
    lane as well as the intake path's writer, and widening its DEFAULT would make a lane that
    skips every intake validation reachable by a machine. The refusal is byte-identical to the
    one it gave before ADR-0027, code included.
    """
    command = intake_command()
    with pytest.raises(DomainError) as error:
        register_revision(
            migrated_session,
            package_id=command.package_id,
            source_repository=command.source_repository,
            revision=command.revision,
            content_hash=command.content_hash,
            source_path=command.source_path,
            source_commit=command.source_commit,
            approved_by=command.approved_by,
            approved_at=command.approved_at,
            approval_event_id=command.approval_event_id,
            enforcement_snapshot=command.enforcement_snapshot,
            authority=command.authority,
            registry_version=command.registry_version,
            actor_id="orchestrator-system",
            actor_role=ActorRole.SYSTEM,
        )

    assert error.value.code == "human_actor_required"


def test_authority_approval_still_refuses_the_system_actor(migrated_session: Session) -> None:
    """ADR-0027 narrows ADR-0006; it does not overturn it. The authority approval is a decision
    and stays human-only, and it must be shown refusing rather than assumed to."""
    with pytest.raises(DomainError) as error:
        record_approval(
            migrated_session,
            unit_id=uuid.uuid4(),
            subject_type="authority",
            actor_id="orchestrator-system",
            actor_role=ActorRole.SYSTEM,
            reason="the machine approves its own envelope",
            idempotency_key="machine-authority-approval",
            expected_version=0,
        )

    assert error.value.code == "human_actor_required"


def test_decomposition_approval_still_refuses_the_system_actor(
    migrated_session: Session,
) -> None:
    """The other decision gate. It refuses before it reads the proposal, which is why a
    nonexistent id still produces the role refusal rather than a not-found."""
    with pytest.raises(DomainError) as error:
        approve_decomposition_proposal(
            migrated_session,
            uuid.uuid4(),
            actor=system_actor(),
            reason="the machine approves its own breakdown",
            idempotency_key="machine-decomposition-approval",
        )

    assert error.value.code == "human_actor_required"
