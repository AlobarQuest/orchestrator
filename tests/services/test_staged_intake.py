"""Staged intakes: SYSTEM stages, a HUMAN confirms, and the confirm is the ordinary registration."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.routes.intake import package_intake_command
from orchestrator.api.schemas.intake import PackageIntakeRegistration
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import Event, StagedPackageIntake, WorkPackageRevision
from orchestrator.services.intake.package_intake import PackageIntakeCommand
from orchestrator.services.intake.staged_intake import (
    confirm_staged_intake,
    stage_package_intake,
    staged_intake,
)
from tests.api.test_package_intake_api import intake_payload

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
HUMAN = ActorContext("devon", ActorRole.HUMAN)


def staged_payload(**overrides: object) -> dict[str, Any]:
    """The body as the API route stores it: validated, dumped as JSON, unset fields left out."""
    return PackageIntakeRegistration.model_validate(
        intake_payload(idempotency_key=f"staged-{uuid.uuid4().hex}", **overrides)
    ).model_dump(mode="json", exclude_unset=True)


def command_for(payload: dict[str, Any]) -> PackageIntakeCommand:
    return package_intake_command(PackageIntakeRegistration.model_validate(payload))


def _stage(engine: Engine, payload: dict[str, Any]) -> uuid.UUID:
    with Session(engine) as session:
        staged = stage_package_intake(session, payload, SYSTEM)
        session.commit()
        return staged.id


def test_staging_holds_the_payload_verbatim_and_registers_nothing(
    migrated_engine: Engine,
) -> None:
    payload = staged_payload()

    staged_id = _stage(migrated_engine, payload)

    with Session(migrated_engine) as session:
        staged = session.get(StagedPackageIntake, staged_id)
        assert staged is not None
        assert staged.payload == payload
        assert staged.state == "staged"
        assert staged.staged_by == "orchestrator-system"
        assert staged.idempotency_key == payload["idempotency_key"]
        assert staged.registered_revision_id is None
        assert session.scalars(select(WorkPackageRevision)).all() == []
        assert session.scalars(select(Event)).all() == []


@pytest.mark.parametrize(
    "actor",
    [
        HUMAN,
        ActorContext("worker", ActorRole.WORKER),
        ActorContext("verifier", ActorRole.VERIFIER),
        ActorContext("observer", ActorRole.OBSERVER),
        ActorContext("", ActorRole.SYSTEM),
    ],
)
def test_only_the_system_actor_may_stage(migrated_session: Session, actor: ActorContext) -> None:
    with pytest.raises(DomainError) as refused:
        stage_package_intake(migrated_session, staged_payload(), actor)

    assert refused.value.code == "role_forbidden"


def test_restaging_the_same_payload_replays_the_row(migrated_engine: Engine) -> None:
    payload = staged_payload()
    first = _stage(migrated_engine, payload)

    assert _stage(migrated_engine, payload) == first


def test_restaging_the_key_with_a_different_payload_is_a_conflict(
    migrated_engine: Engine,
) -> None:
    payload = staged_payload()
    _stage(migrated_engine, payload)

    with pytest.raises(DomainError) as refused:
        _stage(migrated_engine, {**payload, "revision": 2})

    assert refused.value.code == "idempotency_conflict"


def test_a_concurrent_duplicate_staging_writes_one_row(migrated_engine: Engine) -> None:
    payload = staged_payload()
    barrier = Barrier(2)

    def submit() -> uuid.UUID:
        with Session(migrated_engine) as session:
            barrier.wait(timeout=15)
            staged = stage_package_intake(session, payload, SYSTEM)
            session.commit()
            return staged.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=30) for f in [pool.submit(submit), pool.submit(submit)]]

    assert results[0] == results[1]
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(StagedPackageIntake)) == 1


def test_confirming_registers_as_the_person_under_the_staged_key(
    migrated_engine: Engine,
) -> None:
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)

    with Session(migrated_engine) as session:
        revision = confirm_staged_intake(session, staged_id, command_for(payload), HUMAN)
        session.commit()
        revision_id = revision.id

    with Session(migrated_engine) as session:
        staged = session.get(StagedPackageIntake, staged_id)
        assert staged is not None
        assert staged.state == "registered"
        assert staged.registered_revision_id == revision_id
        revision = session.get(WorkPackageRevision, revision_id)
        assert revision is not None and revision.registered_by == "devon"
        event = session.scalars(select(Event).where(Event.subject_id == revision_id)).one()
        assert event.idempotency_key == payload["idempotency_key"]
        assert event.payload["staged_intake_id"] == str(staged_id)
        assert event.payload["command"]["actor_role"] == ActorRole.HUMAN


def test_a_second_confirm_returns_the_same_revision_and_writes_nothing(
    migrated_engine: Engine,
) -> None:
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)
    revisions = []
    for _ in range(2):
        with Session(migrated_engine) as session:
            revisions.append(
                confirm_staged_intake(session, staged_id, command_for(payload), HUMAN).id
            )
            session.commit()

    assert revisions[0] == revisions[1]
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 1


def test_two_concurrent_confirms_register_one_revision(migrated_engine: Engine) -> None:
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)
    barrier = Barrier(2)

    def press() -> uuid.UUID:
        with Session(migrated_engine) as session:
            barrier.wait(timeout=15)
            revision = confirm_staged_intake(session, staged_id, command_for(payload), HUMAN)
            session.commit()
            return revision.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=30) for f in [pool.submit(press), pool.submit(press)]]

    assert results[0] == results[1]
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(WorkPackageRevision)) == 1
        assert session.scalar(select(func.count()).select_from(Event)) == 1


def test_a_press_that_loaded_the_row_before_another_registered_it_replays(
    migrated_engine: Engine,
) -> None:
    # The web route reads the row to build the command, and holds it, before the service locks
    # it. The locked read must refresh that held object, or the waiting press sees `staged` and
    # registers again -- which, as a different person, is an idempotency conflict rather than the
    # replay it should be. The reference is held on purpose: the identity map is weak, so an
    # unreferenced row is simply reloaded and the defect would not show.
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)
    with Session(migrated_engine) as waiting:
        held = staged_intake(waiting, staged_id)
        assert held.state == "staged"
        with Session(migrated_engine) as first:
            registered = confirm_staged_intake(first, staged_id, command_for(payload), HUMAN).id
            first.commit()

        other_person = ActorContext("someone-else", ActorRole.HUMAN)
        replayed = confirm_staged_intake(waiting, staged_id, command_for(payload), other_person)

        assert replayed.id == registered
        assert held.state == "registered"


@pytest.mark.parametrize("actor", [SYSTEM, ActorContext("worker", ActorRole.WORKER)])
def test_no_machine_may_confirm_even_naming_a_change_record(
    migrated_engine: Engine, actor: ActorContext
) -> None:
    # `register_package_intake` would admit SYSTEM with a change record (ADR-0027). The confirm
    # must not: a staged row is moved on only by a person.
    payload = staged_payload(change_record_id=7)
    staged_id = _stage(migrated_engine, payload)

    with Session(migrated_engine) as session, pytest.raises(DomainError) as refused:
        confirm_staged_intake(session, staged_id, command_for(payload), actor)

    assert refused.value.code == "human_actor_required"


def test_a_command_under_another_key_is_refused(migrated_engine: Engine) -> None:
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)
    other = command_for({**payload, "idempotency_key": "somewhere-else"})

    with Session(migrated_engine) as session, pytest.raises(DomainError) as refused:
        confirm_staged_intake(session, staged_id, other, HUMAN)

    assert refused.value.code == "idempotency_conflict"


def test_a_withdrawn_row_cannot_be_confirmed(migrated_engine: Engine) -> None:
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)
    with Session(migrated_engine) as session:
        row = session.get(StagedPackageIntake, staged_id)
        assert row is not None
        row.state = "withdrawn"
        session.commit()

    with Session(migrated_engine) as session, pytest.raises(DomainError) as refused:
        confirm_staged_intake(session, staged_id, command_for(payload), HUMAN)

    assert refused.value.code == "staged_intake_not_confirmable"


def test_an_unknown_row_is_not_found(migrated_session: Session) -> None:
    for read in (
        lambda: staged_intake(migrated_session, uuid.uuid4()),
        lambda: confirm_staged_intake(
            migrated_session, uuid.uuid4(), command_for(staged_payload()), HUMAN
        ),
    ):
        with pytest.raises(DomainError) as refused:
            read()
        assert refused.value.code == "staged_intake_not_found"


def test_a_registration_the_service_refuses_leaves_the_row_staged(
    migrated_engine: Engine,
) -> None:
    # Staging validates the model only; the service's own checks run at the confirm.
    payload = staged_payload(status_at_intake="draft")
    staged_id = _stage(migrated_engine, payload)

    with Session(migrated_engine) as session, pytest.raises(DomainError) as refused:
        confirm_staged_intake(session, staged_id, command_for(payload), HUMAN)

    assert refused.value.code == "package_intake_status_invalid"
    with Session(migrated_engine) as session:
        staged = session.get(StagedPackageIntake, staged_id)
        assert staged is not None and staged.state == "staged"
