"""Staged intakes: SYSTEM stages, a HUMAN confirms, and the confirm is the ordinary registration."""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from orchestrator.api.routes.intake import package_intake_command
from orchestrator.api.schemas.intake import PackageIntakeRegistration
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import Event, StagedPackageIntake, WorkPackageRevision
from orchestrator.services.intake.package_intake import (
    PackageIntakeCommand,
    register_package_intake,
)
from orchestrator.services.intake.staged_intake import (
    confirm_staged_intake,
    stage_package_intake,
    staged_intake,
    withdraw_staged_intake,
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
        staged = stage_package_intake(session, payload, command_for(payload), SYSTEM)
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
        payload = staged_payload()
        stage_package_intake(migrated_session, payload, command_for(payload), actor)

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
            staged = stage_package_intake(session, payload, command_for(payload), SYSTEM)
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
        withdraw_staged_intake(session, staged_id, "superseded by revision 2", HUMAN)
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


# --- staging refuses what the confirm would refuse, with the same code -----------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"status_at_intake": "draft"}, "package_intake_status_invalid"),
        ({"verification_mode": "trust_me"}, "package_intake_verification_invalid"),
        ({"expected_version": 1}, "version_conflict"),
        (
            {
                "acceptance_criteria": [
                    {
                        "ac_id": "AC-001",
                        "condition": "c",
                        "evidence_type": "vibes",
                        "evidence": "e",
                        "approver": "policy",
                    }
                ]
            },
            "unknown_evidence_type",
        ),
        (
            {"enforcement_snapshot": {"outcome": "o", "reach": ["the_moon"]}},
            "reach_invalid",
        ),
        (
            {"originating_observation_id": str(uuid.uuid4())},
            "intake_originating_observation_unknown",
        ),
    ],
)
def test_staging_refuses_with_the_code_the_confirm_would_have(
    migrated_engine: Engine, overrides: dict[str, Any], code: str
) -> None:
    payload = staged_payload(**overrides)
    with Session(migrated_engine) as session, pytest.raises(DomainError) as at_confirm:
        register_package_intake(session, command_for(payload), HUMAN)

    with pytest.raises(DomainError) as at_stage:
        _stage(migrated_engine, payload)

    assert at_stage.value.code == at_confirm.value.code == code
    with Session(migrated_engine) as session:
        assert session.scalars(select(StagedPackageIntake)).all() == []


def _register(engine: Engine, payload: dict[str, Any]) -> uuid.UUID:
    with Session(engine) as session:
        revision = register_package_intake(session, command_for(payload), HUMAN)
        session.commit()
        return revision.id


def test_staging_refuses_a_key_an_intake_already_used(migrated_engine: Engine) -> None:
    payload = staged_payload()
    _register(migrated_engine, {**payload, "content_hash": "sha256:other", "revision": 2})

    with pytest.raises(DomainError) as refused:
        _stage(migrated_engine, payload)

    assert refused.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    "registered",
    [
        {"content_hash": "sha256:different"},
        {},  # the identical revision: confirming it would register nothing new
    ],
)
def test_staging_refuses_a_package_revision_already_registered(
    migrated_engine: Engine, registered: dict[str, Any]
) -> None:
    payload = staged_payload()
    _register(migrated_engine, {**payload, "idempotency_key": "elsewhere", **registered})

    with pytest.raises(DomainError) as refused:
        _stage(migrated_engine, payload)

    assert refused.value.code == "package_intake_conflict"


def test_staging_refuses_a_package_registered_from_another_repository(
    migrated_engine: Engine,
) -> None:
    payload = staged_payload()
    _register(
        migrated_engine,
        {
            **payload,
            "idempotency_key": "elsewhere",
            "revision": 2,
            "content_hash": "sha256:two",
            "source_repository": "someone/else",
        },
    )

    with pytest.raises(DomainError) as refused:
        _stage(migrated_engine, payload)

    assert refused.value.code == "package_intake_conflict"


def test_a_row_that_went_stale_is_refused_at_confirm_and_can_be_withdrawn(
    migrated_engine: Engine,
) -> None:
    # Staging passed; then the same package revision was registered with other content. The
    # confirm re-runs everything and refuses, and the row stays staged until a person withdraws it.
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)
    _register(
        migrated_engine, {**payload, "idempotency_key": "elsewhere", "content_hash": "sha256:x"}
    )

    with Session(migrated_engine) as session, pytest.raises(DomainError) as refused:
        confirm_staged_intake(session, staged_id, command_for(payload), HUMAN)
    assert refused.value.code == "package_intake_conflict"

    with Session(migrated_engine) as session:
        withdraw_staged_intake(session, staged_id, "registered by hand with a fix", HUMAN)
        session.commit()
    with Session(migrated_engine) as session:
        staged = session.get(StagedPackageIntake, staged_id)
        assert staged is not None and staged.state == "withdrawn"


# --- withdraw --------------------------------------------------------------------------------


def test_withdrawing_stamps_who_when_and_why(migrated_engine: Engine) -> None:
    staged_id = _stage(migrated_engine, staged_payload())

    with Session(migrated_engine) as session:
        withdraw_staged_intake(session, staged_id, "  stale bump  ", HUMAN)
        session.commit()

    with Session(migrated_engine) as session:
        staged = session.get(StagedPackageIntake, staged_id)
        assert staged is not None
        assert staged.state == "withdrawn"
        assert staged.withdrawn_by == "devon"
        assert staged.withdrawn_at is not None
        assert staged.withdrawal_reason == "stale bump"
        assert staged.registered_revision_id is None


def test_a_second_withdraw_changes_nothing(migrated_engine: Engine) -> None:
    staged_id = _stage(migrated_engine, staged_payload())
    with Session(migrated_engine) as session:
        withdraw_staged_intake(session, staged_id, "first", HUMAN)
        session.commit()

    with Session(migrated_engine) as session:
        again = withdraw_staged_intake(
            session, staged_id, "second", ActorContext("someone-else", ActorRole.HUMAN)
        )
        session.commit()
        assert (again.withdrawn_by, again.withdrawal_reason) == ("devon", "first")


@pytest.mark.parametrize("actor", [SYSTEM, ActorContext("worker", ActorRole.WORKER)])
def test_no_machine_may_withdraw(migrated_engine: Engine, actor: ActorContext) -> None:
    staged_id = _stage(migrated_engine, staged_payload())

    with Session(migrated_engine) as session, pytest.raises(DomainError) as refused:
        withdraw_staged_intake(session, staged_id, "reason", actor)

    assert refused.value.code == "human_actor_required"


def test_a_blank_reason_is_refused(migrated_engine: Engine) -> None:
    staged_id = _stage(migrated_engine, staged_payload())

    with Session(migrated_engine) as session, pytest.raises(DomainError) as refused:
        withdraw_staged_intake(session, staged_id, "   ", HUMAN)

    assert refused.value.code == "staged_intake_withdrawal_reason_required"


def test_a_registered_row_cannot_be_withdrawn(migrated_engine: Engine) -> None:
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)
    with Session(migrated_engine) as session:
        confirm_staged_intake(session, staged_id, command_for(payload), HUMAN)
        session.commit()

    with Session(migrated_engine) as session, pytest.raises(DomainError) as refused:
        withdraw_staged_intake(session, staged_id, "too late", HUMAN)

    assert refused.value.code == "staged_intake_not_withdrawable"


def test_withdrawing_an_unknown_row_is_not_found(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as refused:
        withdraw_staged_intake(migrated_session, uuid.uuid4(), "reason", HUMAN)

    assert refused.value.code == "staged_intake_not_found"


def _lock_waiters(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock'"
            )
        ).scalar_one()


def test_a_confirm_racing_a_withdraw_ends_in_one_outcome_and_a_clean_409(
    migrated_engine: Engine,
) -> None:
    """A press and a withdraw arrive together. Each must wait on the row lock, so the second to
    get it reads what the first did and is refused by name -- never both acting on a `staged`
    row they each read, which would combine a registration and a withdrawal in one row and
    reach the wire as an IntegrityError (a bare 500).

    A third session holds the row lock until both are provably waiting on a lock in Postgres,
    so the race is real rather than a hope about thread scheduling."""
    payload = staged_payload()
    staged_id = _stage(migrated_engine, payload)
    outcomes: dict[str, object] = {}

    def act(name: str) -> None:
        with Session(migrated_engine) as session:
            try:
                if name == "confirm":
                    confirm_staged_intake(session, staged_id, command_for(payload), HUMAN)
                else:
                    withdraw_staged_intake(session, staged_id, "racing the confirm", HUMAN)
                session.commit()
                outcomes[name] = "done"
            except Exception as error:  # the assertion below names what arrived
                session.rollback()
                outcomes[name] = error

    with Session(migrated_engine) as holder:
        holder.scalar(
            select(StagedPackageIntake).where(StagedPackageIntake.id == staged_id).with_for_update()
        )
        threads = [threading.Thread(target=act, args=(name,)) for name in ("confirm", "withdraw")]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 10
        while _lock_waiters(migrated_engine) < 2:
            assert time.monotonic() < deadline, "both requests should be waiting on a lock"
            time.sleep(0.02)
        holder.rollback()
    for thread in threads:
        thread.join(timeout=10)

    done = [name for name, outcome in outcomes.items() if outcome == "done"]
    refused = [outcome for outcome in outcomes.values() if outcome != "done"]
    assert len(done) == 1 and len(refused) == 1, outcomes
    assert isinstance(refused[0], DomainError), repr(refused[0])
    assert (
        refused[0].code
        == {
            "confirm": "staged_intake_not_withdrawable",
            "withdraw": "staged_intake_not_confirmable",
        }[done[0]]
    )
    with Session(migrated_engine) as session:
        row = session.get(StagedPackageIntake, staged_id)
        assert row is not None
        assert row.state == {"confirm": "registered", "withdraw": "withdrawn"}[done[0]]
        revisions = session.scalar(select(func.count()).select_from(WorkPackageRevision))
        assert revisions == (1 if done[0] == "confirm" else 0)
