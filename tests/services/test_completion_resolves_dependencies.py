"""A unit's completion resolves the `work_unit` dependencies that name it (ADR-0055 decision 3).

In the completing transaction, through the human completion gate, and only for a dependency whose
condition is `completed`. Each test pairs the resolution with a control the rule must leave alone.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Dependency, Event, WorkUnit
from orchestrator.services.intake.packages import DependencySpec, record_approval
from orchestrator.services.lifecycle.lifecycle import transition_unit
from tests._support.seeding import register_unit
from tests.services.test_lifecycle_guards import (
    FixedClock,
    add_adjudication,
    completion_command,
)


def _predecessor(session: Session, key: str) -> WorkUnit:
    """A SUBMITTED unit whose one criterion has passed, so a human may complete it."""
    unit = register_unit(session, key)
    unit.state = WorkUnitState.SUBMITTED
    add_adjudication(session, unit)
    session.commit()
    return unit


def _dependent(
    session: Session, key: str, *dependencies: DependencySpec, approve: bool = True
) -> WorkUnit:
    """A DRAFT unit whose authority is approved, held only by its dependencies."""
    unit = register_unit(session, key, dependencies=dependencies)
    if approve:
        record_approval(
            session,
            unit_id=unit.id,
            subject_type="authority",
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            reason="Envelope reviewed.",
            idempotency_key=f"{key}-authority",
            expected_version=unit.version,
        )
    session.commit()
    assert unit.state == WorkUnitState.DRAFT
    return unit


def _dependency(session: Session, unit: WorkUnit) -> Dependency:
    row = session.scalar(select(Dependency).where(Dependency.work_unit_id == unit.id))
    assert row is not None
    return row


def _complete(session: Session, unit: WorkUnit, key: str | None = None) -> None:
    command = completion_command(unit)
    if key is not None:
        command = type(command)(
            command.unit_id, command.target, command.actor, command.expected_version, key
        )
    transition_unit(session, command, clock=FixedClock())


def test_completion_satisfies_the_dependency_and_readies_the_dependent(
    migrated_session: Session,
) -> None:
    before = _predecessor(migrated_session, "resolve-before")
    after = _dependent(
        migrated_session,
        "resolve-after",
        DependencySpec.work_unit(before.id, "completed"),
    )

    _complete(migrated_session, before, "complete-before")

    with Session(migrated_session.get_bind()) as reader:
        dependency = _dependency(reader, after)
        assert (dependency.status, dependency.resolved_by) == ("satisfied", "human-1")
        assert dependency.detail == {"completed_unit_id": str(before.id)}
        dependent = reader.get(WorkUnit, after.id)
        assert dependent is not None and dependent.state == WorkUnitState.READY
        resolved = reader.scalar(
            select(Event).where(
                Event.idempotency_key == f"complete-before:dependency:{dependency.id}"
            )
        )
        assert resolved is not None and resolved.action == "dependency.resolved"
        assert resolved.id == dependency.resolution_event_id
        assert resolved.subject_id == after.id


def test_two_dependents_of_one_unit_are_both_resolved(migrated_session: Session) -> None:
    """Each resolution derives its own keys, so the second dependent's auto-ready cannot collide."""
    before = _predecessor(migrated_session, "fan-before")
    first = _dependent(
        migrated_session, "fan-first", DependencySpec.work_unit(before.id, "completed")
    )
    second = _dependent(
        migrated_session, "fan-second", DependencySpec.work_unit(before.id, "completed")
    )

    _complete(migrated_session, before)

    with Session(migrated_session.get_bind()) as reader:
        for unit in (first, second):
            stored = reader.get(WorkUnit, unit.id)
            assert stored is not None and stored.state == WorkUnitState.READY


@pytest.mark.parametrize("condition", ["approved", "verified", "Completed"])
def test_a_dependency_naming_another_condition_is_left_pending(
    migrated_session: Session, condition: str
) -> None:
    before = _predecessor(migrated_session, f"other-before-{condition}")
    after = _dependent(
        migrated_session,
        f"other-after-{condition}",
        DependencySpec.work_unit(before.id, condition),
    )

    _complete(migrated_session, before)

    with Session(migrated_session.get_bind()) as reader:
        assert _dependency(reader, after).status == "pending"


def test_an_external_dependency_is_left_pending(migrated_session: Session) -> None:
    before = _predecessor(migrated_session, "external-before")
    after = _dependent(
        migrated_session,
        "external-after",
        DependencySpec.work_unit(before.id, "completed"),
        DependencySpec.external("human/console-mint", "completed"),
    )

    _complete(migrated_session, before)

    with Session(migrated_session.get_bind()) as reader:
        statuses = sorted(
            row.status
            for row in reader.scalars(select(Dependency).where(Dependency.work_unit_id == after.id))
        )
        assert statuses == ["pending", "satisfied"]
        dependent = reader.get(WorkUnit, after.id)
        assert dependent is not None and dependent.state == WorkUnitState.DRAFT


def test_a_dependency_on_another_unit_is_left_pending(migrated_session: Session) -> None:
    before = _predecessor(migrated_session, "unrelated-before")
    elsewhere = register_unit(migrated_session, "unrelated-elsewhere")
    after = _dependent(
        migrated_session, "unrelated-after", DependencySpec.work_unit(elsewhere.id, "completed")
    )

    _complete(migrated_session, before)

    with Session(migrated_session.get_bind()) as reader:
        assert _dependency(reader, after).status == "pending"


def test_a_dependency_already_resolved_is_not_rewritten(migrated_session: Session) -> None:
    before = _predecessor(migrated_session, "resolved-before")
    after = _dependent(
        migrated_session, "resolved-after", DependencySpec.work_unit(before.id, "completed")
    )
    dependency = _dependency(migrated_session, after)
    dependency.status = "failed"
    dependency.resolved_by = "human-2"
    migrated_session.commit()

    _complete(migrated_session, before)

    with Session(migrated_session.get_bind()) as reader:
        stored = _dependency(reader, after)
        assert (stored.status, stored.resolved_by) == ("failed", "human-2")


def test_an_unapproved_dependent_is_resolved_but_stays_draft(migrated_session: Session) -> None:
    before = _predecessor(migrated_session, "unready-before")
    after = _dependent(
        migrated_session,
        "unready-after",
        DependencySpec.work_unit(before.id, "completed"),
        approve=False,
    )

    _complete(migrated_session, before)

    with Session(migrated_session.get_bind()) as reader:
        assert _dependency(reader, after).status == "satisfied"
        dependent = reader.get(WorkUnit, after.id)
        assert dependent is not None and dependent.state == WorkUnitState.DRAFT


def test_a_replayed_completion_resolves_once(migrated_session: Session) -> None:
    before = _predecessor(migrated_session, "replay-before")
    after = _dependent(
        migrated_session, "replay-after", DependencySpec.work_unit(before.id, "completed")
    )
    command = completion_command(before)

    transition_unit(migrated_session, command, clock=FixedClock())
    transition_unit(migrated_session, command, clock=FixedClock())

    resolved = migrated_session.scalars(
        select(Event).where(Event.action == "dependency.resolved", Event.subject_id == after.id)
    ).all()
    assert len(resolved) == 1


def test_the_resolution_rolls_back_with_its_completion(migrated_session: Session) -> None:
    """The resolution shares the completion's transaction: a completion that fails after it ran
    leaves the dependency pending and the dependent in DRAFT."""
    before = _predecessor(migrated_session, "rollback-before")
    after = _dependent(
        migrated_session, "rollback-after", DependencySpec.work_unit(before.id, "completed")
    )

    def fail_after_resolution(session: Session, unit: WorkUnit) -> None:
        assert _dependency(session, after).status == "satisfied"
        raise DomainError("injected_failure", "fails after the resolution ran", None)

    with pytest.raises(DomainError, match="fails after the resolution ran"):
        transition_unit(
            migrated_session,
            completion_command(before),
            clock=FixedClock(),
            after=fail_after_resolution,
        )

    with Session(migrated_session.get_bind()) as reader:
        assert _dependency(reader, after).status == "pending"
        dependent = reader.get(WorkUnit, after.id)
        assert dependent is not None and dependent.state == WorkUnitState.DRAFT


def test_only_a_work_unit_dependency_is_resolved_by_completion(migrated_session: Session) -> None:
    """Written around the service, which would refuse this shape: the database allows a
    `decision` dependency that names a unit, and completion must still leave it to a human."""
    before = _predecessor(migrated_session, "kind-before")
    after = _dependent(migrated_session, "kind-after", DependencySpec.external("x", "completed"))
    before_id = before.id  # read first: reading it mid-edit would autoflush a half-written row
    row = _dependency(migrated_session, after)
    row.kind = "decision"
    row.external_ref = None
    row.depends_on_work_unit_id = before_id
    migrated_session.commit()

    _complete(migrated_session, before)

    with Session(migrated_session.get_bind()) as reader:
        assert _dependency(reader, after).status == "pending"


def test_a_dependent_with_two_dependencies_on_one_unit_is_readied_once(
    migrated_session: Session,
) -> None:
    before = _predecessor(migrated_session, "twice-before")
    after = _dependent(
        migrated_session,
        "twice-after",
        DependencySpec.work_unit(before.id, "completed"),
        DependencySpec.work_unit(before.id, "completed"),
    )

    _complete(migrated_session, before, "complete-twice")

    with Session(migrated_session.get_bind()) as reader:
        statuses = [
            row.status
            for row in reader.scalars(select(Dependency).where(Dependency.work_unit_id == after.id))
        ]
        assert statuses == ["satisfied", "satisfied"]
        readied = reader.scalars(
            select(Event).where(Event.subject_id == after.id, Event.to_state == "ready")
        ).all()
        assert [event.idempotency_key for event in readied] == [
            f"complete-twice:dependent:{after.id}:auto-ready"
        ]
