import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, cast

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Adjudication, Evidence, PackageAcceptanceCriterion
from orchestrator.services.evidence import current_adjudication, record_adjudication
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.verifier_evaluators import human_may_adjudicate
from tests.services.test_dependencies import register_unit


def record(session: Session, command: dict[str, Any]) -> Adjudication | DomainError:
    return record_adjudication(session, **cast(Any, command))


def add_criterion(session: Session, unit, ac_id: str, evidence_type: str) -> None:
    session.add(
        PackageAcceptanceCriterion(
            work_package_revision_id=unit.work_package_revision_id,
            ac_id=ac_id,
            condition="condition",
            evidence_type=evidence_type,
            evidence="evidence",
            approver="human-1",
        )
    )
    session.flush()


def add_evidence(session: Session, unit, ac_id: str, evidence_type: str, payload: dict) -> None:
    session.add(
        Evidence(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id=ac_id,
            attempt=1,
            evidence_type=evidence_type,
            payload=payload,
            source_revision="abc1234",
            recorded_by="worker-1",
            event_id=uuid.uuid4(),
            idempotency_key=f"evidence-{unit.id}-{ac_id}",
        )
    )
    session.flush()


def test_a_human_may_not_decide_a_criterion_the_machine_owns() -> None:
    # FAIL-OPEN CONTROL (the mirror of R1). `automated_test` floors to deterministic_permitted
    # after Increment 1, so readable evidence resolves it. A human must not pre-empt that -- not
    # even in awaiting_review, where clause (b) would otherwise open the door.
    readable = Evidence(evidence_type="pytest", payload={"status": "pass"})

    assert human_may_adjudicate("automated_test", readable, WorkUnitState.AWAITING_REVIEW) is False


def test_a_human_may_decide_a_human_floored_criterion_in_any_state() -> None:
    assert human_may_adjudicate("human_review", None, WorkUnitState.SUBMITTED) is True
    # The floor outranks the evidence: readable deterministic evidence does not demote a
    # human-floored criterion. This is the R1 fail-open, restated at the authorization layer.
    readable = Evidence(evidence_type="pytest", payload={"status": "pass"})
    assert human_may_adjudicate("human_review", readable, WorkUnitState.SUBMITTED) is True


def test_a_human_may_decide_a_deterministic_criterion_only_once_the_verifier_has_asked() -> None:
    # Clause (b). Before the verifier routes to human review, evidence may still arrive -- this is
    # the automated_check-before-CI window, closed by STATE rather than by type.
    assert human_may_adjudicate("automated_check", None, WorkUnitState.VERIFYING) is False
    assert human_may_adjudicate("automated_check", None, WorkUnitState.AWAITING_REVIEW) is True


def test_a_criterion_that_does_not_exist_is_decidable_by_nobody() -> None:
    # `_criterion_evidence_type` returns None when no criterion row backs the ac_id. Flooring an
    # absent type to `human` (which `floor_for` does, by design) must NOT grant authority here --
    # the fail-closed direction for an unknown TYPE is the opposite of the one for an absent
    # CRITERION.
    assert human_may_adjudicate(None, None, WorkUnitState.AWAITING_REVIEW) is False


def test_human_may_not_pre_empt_the_verifier_on_a_resolved_automated_test(
    migrated_session: Session, ready_unit
) -> None:
    # THE FAIL-OPEN THIS TASK CLOSES, end to end. Increment 1 made `automated_test`
    # deterministically evaluable but left `_authorize_outcome` keyed on JUDGMENT_TYPES, which
    # still contains it -- so a human could record `passed` over a verifier result they never saw.
    # The unit is parked in AWAITING_REVIEW deliberately: clause (b) is then the ONLY thing that
    # could admit this, and it must not, because the evidence resolves. A test that left the unit
    # in READY would pass for the wrong reason.
    ready_unit.state = WorkUnitState.AWAITING_REVIEW
    add_criterion(migrated_session, ready_unit, "ac-1", "automated_test")
    add_evidence(migrated_session, ready_unit, "ac-1", "pytest", {"status": "pass"})

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="the tests look green to me",
        idempotency_key="human-pass-resolved-automated-test",
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_human_may_decide_an_automated_test_the_verifier_could_not_resolve(
    migrated_session: Session, ready_unit
) -> None:
    # Clause (b), end to end -- the companion that proves the refusal above is about the RESOLVED
    # evidence and not about the type. Same criterion type, same unit state, no readable evidence:
    # the verifier deferred, so the human is the only actor who can settle it.
    ready_unit.state = WorkUnitState.AWAITING_REVIEW
    add_criterion(migrated_session, ready_unit, "ac-1", "automated_test")

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="verified by hand; the suite never reported",
        idempotency_key="human-pass-unresolved-automated-test",
    )

    assert isinstance(result, Adjudication)
    assert result.outcome == "passed"


def test_a_recorded_adjudication_survives_the_session(
    migrated_session: Session, migrated_engine: Engine, ready_unit
) -> None:
    # WS-P2.1's defect shape: a service that flushes but never commits returns the right object and
    # writes nothing. This is the guard for the non-committing-core refactor -- it must keep being
    # true that the SINGLE-criterion entry point still owns and closes its transaction.
    #
    # The re-read is deliberately in a SECOND session. `expire_all()` alone would prove nothing
    # here: a flushed-but-uncommitted row is visible to its own transaction, so re-reading through
    # `migrated_session` would pass under exactly the defect being guarded against.
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="verified",
        idempotency_key="adjudication-survives-the-session",
    )

    assert isinstance(result, Adjudication)
    recorded_id, recorded_outcome = result.id, result.outcome

    migrated_session.expire_all()
    with Session(migrated_engine) as reader:
        reread = reader.get(Adjudication, recorded_id)
        assert reread is not None
        assert reread.outcome == recorded_outcome


def test_human_may_pass_a_judgment_type_ac(migrated_session: Session, ready_unit) -> None:
    add_criterion(migrated_session, ready_unit, "ac-1", "human.review")
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="reviewed and met",
        idempotency_key="human-pass-1",
    )
    assert isinstance(result, Adjudication)
    assert result.outcome == "passed"
    assert result.decided_by == "human-1"


def test_human_may_not_pass_a_deterministic_ac(migrated_session: Session, ready_unit) -> None:
    add_criterion(migrated_session, ready_unit, "ac-1", "test")
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="looks green to me",
        idempotency_key="human-pass-det",
    )
    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_a_human_may_record_failed_on_a_judgment_criterion(
    migrated_session: Session, ready_unit
) -> None:
    # Spec AC-006, and the supersession of `test_human_may_not_record_failed`, which pinned the
    # rule this increment exists to remove. Before now the human vocabulary was passed /
    # not_applicable / waived -- the gate could say yes, doesn't apply, or nothing. It could not
    # say no. `waived` is not the missing "no": it means "this failed and I accept it anyway", and
    # demands a failed evidence id, a risk class, a follow-up and a future expiry.
    add_criterion(migrated_session, ready_unit, "ac-1", "human.review")

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="failed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="not met",
        idempotency_key="human-failed-1",
    )

    assert isinstance(result, Adjudication)
    assert result.outcome == "failed"
    assert result.decided_by == "human-1"


def test_a_human_may_not_record_failed_on_a_machine_owned_criterion(
    migrated_session: Session, ready_unit
) -> None:
    # Spec AC-007. `failed` inherits the same predicate as `passed` -- it is not a wider door.
    ready_unit.state = WorkUnitState.AWAITING_REVIEW
    add_criterion(migrated_session, ready_unit, "ac-1", "automated_test")
    add_evidence(migrated_session, ready_unit, "ac-1", "pytest", {"status": "pass"})

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="failed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="I disagree with the suite",
        idempotency_key="human-failed-machine-owned",
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_human_may_not_pass_a_criterion_less_ac(migrated_session: Session, ready_unit) -> None:
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="looks fine",
        idempotency_key="human-pass-no-criterion",
    )
    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


@pytest.mark.parametrize("outcome", ["passed", "failed", "not_applicable"])
def test_verifier_records_each_non_waiver_outcome(
    migrated_session: Session, ready_unit, outcome: str
) -> None:
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome=outcome,
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="verified",
        idempotency_key=f"adjudication-{outcome}",
    )

    assert isinstance(result, Adjudication)
    assert result.outcome == outcome
    assert result.decided_by == "verifier-1"


def test_non_waiver_risk_outside_vocabulary_is_a_clean_error(
    migrated_session: Session, ready_unit
) -> None:
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="verified",
        idempotency_key="non-waiver-bad-risk",
        risk="catastrophic",
    )

    assert isinstance(result, DomainError)
    assert result.code == "adjudication_invalid"


def test_worker_cannot_record_adjudication(migrated_session: Session, ready_unit) -> None:
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("worker-1", ActorRole.WORKER),
        rationale="looks good",
        idempotency_key="adjudication-1",
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_correction_supersedes_current_terminal_and_query_returns_it(
    migrated_session: Session, ready_unit
) -> None:
    common = {
        "work_package_revision_id": ready_unit.work_package_revision_id,
        "work_unit_id": ready_unit.id,
        "ac_id": "ac-1",
        "actor": ActorContext("verifier-1", ActorRole.VERIFIER),
        "rationale": "verified",
    }
    first = record_adjudication(
        migrated_session, outcome="failed", idempotency_key="adjudication-1", **common
    )
    second = record_adjudication(
        migrated_session, outcome="passed", idempotency_key="adjudication-2", **common
    )

    assert isinstance(first, Adjudication)
    assert isinstance(second, Adjudication)
    assert second.supersedes_adjudication_id == first.id
    current = current_adjudication(
        migrated_session,
        ready_unit.work_package_revision_id,
        ready_unit.id,
        "ac-1",
    )
    assert current is not None
    assert current.id == second.id


def test_adjudication_idempotency_is_exact(migrated_session: Session, ready_unit) -> None:
    command: dict[str, Any] = {
        "work_package_revision_id": ready_unit.work_package_revision_id,
        "work_unit_id": ready_unit.id,
        "ac_id": "ac-1",
        "outcome": "passed",
        "actor": ActorContext("verifier-1", ActorRole.VERIFIER),
        "rationale": "verified",
        "idempotency_key": "adjudication-1",
    }
    first = record(migrated_session, command)
    replay = record(migrated_session, command)
    changed = record(migrated_session, command | {"outcome": "failed"})

    assert isinstance(first, Adjudication)
    assert isinstance(replay, Adjudication)
    assert replay.id == first.id
    assert isinstance(changed, DomainError)
    assert changed.code == "idempotency_conflict"


def test_adjudication_replay_precedes_current_version_validation(
    migrated_session: Session, ready_unit
) -> None:
    command: dict[str, Any] = {
        "work_package_revision_id": ready_unit.work_package_revision_id,
        "work_unit_id": ready_unit.id,
        "ac_id": "ac-1",
        "outcome": "passed",
        "actor": ActorContext("verifier-1", ActorRole.VERIFIER),
        "rationale": "verified",
        "idempotency_key": "adjudication-versioned",
        "expected_version": ready_unit.version,
    }
    first = record(migrated_session, command)
    assert isinstance(first, Adjudication)
    ready_unit.version += 1
    migrated_session.commit()

    replay = record(migrated_session, command)
    changed_actor = record(
        migrated_session,
        command | {"actor": ActorContext("verifier-2", ActorRole.VERIFIER)},
    )

    assert isinstance(replay, Adjudication)
    assert replay.id == first.id
    assert isinstance(changed_actor, DomainError)
    assert changed_actor.code == "idempotency_conflict"


def test_concurrent_identical_adjudications_converge(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as setup:
        unit = register_unit(setup, "concurrent-adjudication")
        setup.commit()
        command: dict[str, Any] = {
            "work_package_revision_id": unit.work_package_revision_id,
            "work_unit_id": unit.id,
            "ac_id": "ac-1",
            "outcome": "passed",
            "actor": ActorContext("verifier-1", ActorRole.VERIFIER),
            "rationale": "verified",
            "idempotency_key": "concurrent-adjudication-1",
        }

    start = Barrier(2)

    def decide() -> tuple[str, object]:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            start.wait(timeout=5)
            result = record(session, command)
            if isinstance(result, Adjudication):
                return ("adjudication", result.id)
            return ("error", result.code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(decide) for _index in range(2))
        results = tuple(future.result(timeout=10) for future in futures)

    assert all(kind == "adjudication" for kind, _value in results)
    assert len({value for _kind, value in results}) == 1
