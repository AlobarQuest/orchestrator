"""WS-P3.7 Increment 1: the condition ADR-0020's safety case rests on, as a readable fact.

> the factory may act only when every acceptance criterion was resolved deterministically
> from observed evidence, with no human adjudication.

Before this increment that condition could not be read from the database or from any API: the
deciding actor's KIND survived only inside an event payload, one join away, behind an opaque
`dict[str, Any]` no schema documents. `adjudications.decided_by_role` makes it a stored fact, and
`verifier_decided_completion` is the one place the question is answered.

Most of what follows is a direction the answer must fail CLOSED in. The affirmative case is one
test and the refusals are six, which is the right ratio for a predicate whose job is to refuse.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.evidence_types import VERIFIER_NAMED_CHECK_EVIDENCE_TYPE
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Evidence,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.evidence import record_adjudication
from orchestrator.services.lifecycle import ActorContext, verifier_decided_completion
from tests.services.test_adjudications import FROM_EVALUATION, add_criterion, add_evidence
from tests.services.test_dependencies import register_unit

VERIFIER = ActorContext("orchestrator-verifier", ActorRole.VERIFIER)
HUMAN = ActorContext("devon", ActorRole.HUMAN)


def _unit(session: Session, key: str, *ac_ids: str) -> WorkUnit:
    unit = register_unit(session, key, acceptance_criteria=ac_ids)
    unit.state = WorkUnitState.AWAITING_REVIEW
    for ac_id in ac_ids:
        add_criterion(session, unit, ac_id, "human.review")
    session.commit()
    return unit


def _revision(session: Session, unit: WorkUnit) -> WorkPackageRevision:
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    return revision


def _decide(
    session: Session,
    unit: WorkUnit,
    ac_id: str,
    *,
    actor: ActorContext,
    outcome: str = "passed",
    key: str | None = None,
    **extra: Any,
) -> Adjudication:
    result = record_adjudication(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        outcome=outcome,
        actor=actor,
        rationale="decided",
        idempotency_key=key or f"{unit.unit_key}-{ac_id}-{outcome}",
        **extra,
    )
    assert not isinstance(result, DomainError), result
    return result


def _insert_raw_adjudication(
    session: Session, unit: WorkUnit, ac_id: str, decided_by_role: str | None
) -> Adjudication:
    """A row written straight at the table, bypassing the service.

    `adjudications` carries the append-only trigger, so a stored `decided_by_role` can never be
    edited afterwards -- which is what makes it a record of the decision rather than a mutable
    label, and why the tests that need an arbitrary value must INSERT it rather than UPDATE it.
    `decided_by_role=None` is not a synthetic input either: it is exactly the shape of every row
    written before migration 0024.
    """
    row = Adjudication(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        outcome="passed",
        decided_by="whoever",
        decided_by_role=decided_by_role,
        rationale="recorded before the column existed",
        event_id=uuid.uuid4(),
    )
    session.add(row)
    session.commit()
    return row


def _observed(session: Session, unit: WorkUnit, ac_id: str) -> Evidence:
    """Evidence the orchestrator OBSERVED for itself, returned so an adjudication can cite it.

    ADR-0020's condition has two clauses and most of this file is about the second one -- *who*
    decided. Supplying observed evidence is what keeps those tests measuring that clause rather
    than tripping over the first, which has its own section at the end.
    """
    row = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        attempt=1,
        evidence_type=VERIFIER_NAMED_CHECK_EVIDENCE_TYPE,
        payload={"status": "pass"},
        source_revision="abc1234",
        recorded_by="orchestrator-verifier",
        event_id=uuid.uuid4(),
        idempotency_key=f"observed-{unit.id}-{ac_id}",
    )
    session.add(row)
    session.flush()
    return row


def _decide_observed(session: Session, unit: WorkUnit, ac_id: str, **kwargs: Any) -> Adjudication:
    return _decide(session, unit, ac_id, evidence_id=_observed(session, unit, ac_id).id, **kwargs)


def _answer(session: Session, unit: WorkUnit):
    return verifier_decided_completion(session, _revision(session, unit), unit)


def _refusals(session: Session, unit: WorkUnit) -> list[tuple[str | None, str]]:
    """Refusals of the DECIDER clause only.

    The evidence clause raises its own code on the same criteria, and these tests predate it and
    are not about it -- filtering keeps each assertion measuring one clause, which is the point of
    the two being separate fields.
    """
    return [
        (refusal.ac_id, refusal.code)
        for refusal in _answer(session, unit).refusals
        if refusal.code != "criterion_evidence_not_observed"
    ]


# ---------------------------------------------------------------------------------------------
# The one affirmative case.
# ---------------------------------------------------------------------------------------------


def test_every_criterion_decided_by_the_verifier_satisfies_the_condition(
    migrated_session: Session,
) -> None:
    unit = _unit(migrated_session, "verifier-decided-all", "ac-1", "ac-2")
    _decide_observed(migrated_session, unit, "ac-1", actor=VERIFIER, **FROM_EVALUATION)
    _decide_observed(migrated_session, unit, "ac-2", actor=VERIFIER, **FROM_EVALUATION)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is True
    assert answer.decided_by_verifier is True
    assert answer.evidence_observed is True
    assert answer.refusals == ()


# ---------------------------------------------------------------------------------------------
# The directions it fails closed in.
# ---------------------------------------------------------------------------------------------


def test_a_human_decision_on_any_criterion_disqualifies_the_unit(
    migrated_session: Session,
) -> None:
    """The point of the condition: if a human had to decide, a human is already in the loop."""
    unit = _unit(migrated_session, "one-human-decision", "ac-1", "ac-2")
    _decide_observed(migrated_session, unit, "ac-1", actor=VERIFIER, **FROM_EVALUATION)
    _decide(migrated_session, unit, "ac-2", actor=HUMAN)

    answer = _answer(migrated_session, unit)

    assert answer.decided_by_verifier is False
    assert _refusals(migrated_session, unit) == [("ac-2", "decider_was_not_the_verifier")]


def test_an_undecided_criterion_disqualifies_the_unit(migrated_session: Session) -> None:
    unit = _unit(migrated_session, "one-undecided", "ac-1", "ac-2")
    _decide_observed(migrated_session, unit, "ac-1", actor=VERIFIER, **FROM_EVALUATION)

    answer = _answer(migrated_session, unit)

    assert answer.decided_by_verifier is False
    assert _refusals(migrated_session, unit) == [("ac-2", "no_current_adjudication")]


def test_a_waiver_disqualifies_the_unit_for_two_independent_reasons(
    migrated_session: Session,
) -> None:
    """A waiver settles FAILED evidence and only a HUMAN may record one, so it is disqualified by
    its OUTCOME as well as by its decider. Two reasons, deliberately: the second is a schema column
    that can be NULL, and a condition this load-bearing should not rest on it alone."""
    unit = _unit(migrated_session, "waived-criterion", "ac-1")
    add_evidence(migrated_session, unit, "ac-1", "test", {"status": "fail"})
    evidence = migrated_session.scalar(select(Evidence).where(Evidence.work_unit_id == unit.id))
    assert evidence is not None

    _decide(
        migrated_session,
        unit,
        "ac-1",
        actor=HUMAN,
        outcome="waived",
        failed_evidence_id=evidence.id,
        risk="medium",
        follow_up="revisit next release",
    )

    assert _answer(migrated_session, unit).satisfied is False
    assert {code for _, code in _refusals(migrated_session, unit)} == {
        "criterion_waived",
        "outcome_does_not_settle_criterion",
        "decider_was_not_the_verifier",
    }


def test_an_unrecorded_decider_kind_refuses_rather_than_reading_as_machine_decided(
    migrated_session: Session,
) -> None:
    """Historical rows carry NULL, and NULL is *unknown*. Reading it as "not a human" would make
    every adjudication written before this column qualify -- the exact boundary ADR-0014 forbids,
    since the population on its clean side is not clean."""
    unit = _unit(migrated_session, "null-decider", "ac-1")
    _insert_raw_adjudication(migrated_session, unit, "ac-1", None)

    answer = _answer(migrated_session, unit)

    assert answer.decided_by_verifier is False
    assert _refusals(migrated_session, unit) == [("ac-1", "decider_kind_unrecorded")]


def test_a_human_decision_on_a_non_required_criterion_still_disqualifies_the_unit(
    migrated_session: Session,
) -> None:
    """A per-criterion scan cannot see this. `_validated_subject` admits any `ac_id` the REVISION
    declares, which for a decomposed unit is a superset of the ones mapped to this unit -- so a
    human can decide something here that `required_ac_ids` never iterates. ADR-0020's condition is
    "with no human adjudication", not "none among the criteria that happened to be required".

    The row is inserted directly, which is what makes the state reachable in a test: reproducing
    it through the service needs an approved decomposition whose mapping makes this unit's
    required set a strict subset of the revision's declared one. The state under test is simply
    "an adjudication exists on this unit for an `ac_id` that is not required", and that is exactly
    what the insert produces.
    """
    unit = _unit(migrated_session, "outside-decision", "ac-1")
    _decide_observed(migrated_session, unit, "ac-1", actor=VERIFIER, **FROM_EVALUATION)
    _insert_raw_adjudication(migrated_session, unit, "ac-2", "human")

    answer = _answer(migrated_session, unit)

    assert answer.decided_by_verifier is False
    assert [(r.ac_id, r.code) for r in answer.refusals] == [
        ("ac-2", "decision_outside_required_criteria")
    ]


def test_a_revision_declaring_no_usable_criteria_refuses_instead_of_raising(
    migrated_session: Session,
) -> None:
    """The answer is served from a read surface that must keep answering for every unit that
    exists. `load_required_criteria` RAISES for this shape; `required_ac_ids` returns None, and
    None is a refusal here -- not a 500 on the evidence pack of a WS-3.1 bootstrap unit."""
    unit = _unit(migrated_session, "no-criteria")

    answer = _answer(migrated_session, unit)

    assert answer.decided_by_verifier is False
    assert [(r.ac_id, r.code) for r in answer.refusals] == [(None, "required_criteria_undeclared")]


def test_the_answer_reads_the_current_adjudication_not_a_superseded_one(
    migrated_session: Session,
) -> None:
    """A verifier decision that a human later overrode must not still qualify the unit."""
    unit = _unit(migrated_session, "superseded-decision", "ac-1")
    _decide_observed(migrated_session, unit, "ac-1", actor=VERIFIER, key="first", **FROM_EVALUATION)
    _decide(migrated_session, unit, "ac-1", actor=HUMAN, key="second")

    answer = _answer(migrated_session, unit)

    assert answer.decided_by_verifier is False
    assert _refusals(migrated_session, unit) == [("ac-1", "decider_was_not_the_verifier")]


# ---------------------------------------------------------------------------------------------
# The column itself: a fact recorded at decision time, durable, and closed to invented values.
# ---------------------------------------------------------------------------------------------


def test_the_deciding_role_is_persisted_and_readable_from_another_session(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Re-read through a DIFFERENT session, which is the only reader that cannot see an
    uncommitted write. `expire_all()` plus a re-read inside the same open transaction cannot
    distinguish a flushed row from a committed one."""
    unit = _unit(migrated_session, "role-persisted", "ac-1")
    row_id = _decide(migrated_session, unit, "ac-1", actor=HUMAN).id

    with Session(migrated_engine) as reader:
        stored = reader.get(Adjudication, row_id)
        assert stored is not None
        assert stored.decided_by_role == "human"


def test_a_verifier_role_can_only_come_from_the_verifier_own_evaluation(
    migrated_session: Session,
) -> None:
    """The implication `decided_by_role == "verifier"` leans on: a VERIFIER adjudication that did
    not come from `verify_work_unit` is refused outright (WS-P2.32), so no such row exists to carry
    the role. That refusal is CODE, not schema, and this asserts it still holds -- if it ever
    stopped, the column would begin attesting something it does not know."""
    unit = _unit(migrated_session, "verifier-direct-post", "ac-1")

    refused = record_adjudication(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=VERIFIER,
        rationale="I read the pull request and it looks right to me",
        idempotency_key="verifier-direct-post",
    )

    assert isinstance(refused, DomainError)
    assert refused.code == "verifier_evaluation_required"
    assert (
        migrated_session.scalar(select(Adjudication.id).where(Adjudication.work_unit_id == unit.id))
        is None
    )


@pytest.mark.parametrize("role", sorted(member.value for member in ActorRole))
def test_the_column_admits_every_actor_role(migrated_session: Session, role: str) -> None:
    """The model's CHECK is derived from `ActorRole` and migration 0024 freezes a copy of it. A
    member the migration's copy missed would fail here rather than at the first write against the
    real schema -- and the migration is what production runs."""
    unit = _unit(migrated_session, f"role-check-{role}", "ac-1")

    row = _insert_raw_adjudication(migrated_session, unit, "ac-1", role)

    assert row.decided_by_role == role


def test_the_column_refuses_a_value_that_is_not_an_actor_role(migrated_session: Session) -> None:
    unit = _unit(migrated_session, "role-check-invented", "ac-1")

    with pytest.raises(IntegrityError, match="ck_adjudications_decided_by_role"):
        _insert_raw_adjudication(migrated_session, unit, "ac-1", "not-a-role")
    migrated_session.rollback()


# ---------------------------------------------------------------------------------------------
# The OTHER clause: what the decision rested on. Increment 4b, on Devon's ruling that requiring
# observed evidence "is not an extra term ... you are finishing the sentence, not raising the bar."
# ---------------------------------------------------------------------------------------------


def _attested(session: Session, unit: WorkUnit, ac_id: str) -> Evidence:
    """Evidence the WORKER recorded about its own run. Deterministic to the evaluator, attested."""
    row = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        attempt=1,
        evidence_type="pytest",
        payload={"status": "pass"},
        source_revision="abc1234",
        recorded_by="claude-code-runner",
        event_id=uuid.uuid4(),
        idempotency_key=f"attested-{unit.id}-{ac_id}",
    )
    session.add(row)
    session.flush()
    return row


def test_evidence_the_worker_attested_to_does_not_satisfy_the_observed_clause(
    migrated_session: Session,
) -> None:
    """The hole this closes, stated as the estate found it: the evaluator dispatches on the
    ARRIVING row's type, so a `pytest` row the worker wrote saying it passed resolves
    deterministically and the verifier records `passed`. The runner said its tests passed and
    nobody checked. Verifier-decided is TRUE here; observed is not."""
    unit = _unit(migrated_session, "attested-not-observed", "ac-1")
    _decide(
        migrated_session,
        unit,
        "ac-1",
        actor=VERIFIER,
        evidence_id=_attested(migrated_session, unit, "ac-1").id,
        **FROM_EVALUATION,
    )

    answer = _answer(migrated_session, unit)

    assert answer.decided_by_verifier is True
    assert answer.evidence_observed is False
    assert answer.satisfied is False
    assert [(r.ac_id, r.code) for r in answer.refusals] == [
        ("ac-1", "criterion_evidence_not_observed")
    ]


def test_an_adjudication_citing_no_evidence_does_not_satisfy_the_observed_clause(
    migrated_session: Session,
) -> None:
    unit = _unit(migrated_session, "cites-nothing", "ac-1")
    _decide(migrated_session, unit, "ac-1", actor=VERIFIER, **FROM_EVALUATION)

    answer = _answer(migrated_session, unit)

    assert answer.evidence_observed is False
    assert answer.satisfied is False


def test_evidence_on_a_DIFFERENT_unit_does_not_satisfy_the_observed_clause(
    migrated_session: Session,
) -> None:
    """The observed set is scoped to THIS unit. Resolving it by id alone, across units, would let
    a neighbour's observed check vouch for a criterion nobody looked at."""
    unit = _unit(migrated_session, "own-unit", "ac-1")
    # A different criteria list, so `register_test_revision` mints a genuinely different
    # revision -- it is idempotent on the default list, and two units sharing one revision
    # collide on UNIQUE(revision, ac_id).
    other = _unit(migrated_session, "neighbour-unit", "ac-1", "ac-9")
    borrowed = _observed(migrated_session, other, "ac-1")
    migrated_session.add(
        Adjudication(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            outcome="passed",
            decided_by="orchestrator-verifier",
            decided_by_role="verifier",
            rationale="the named check was observed to conclude success",
            evidence_id=borrowed.id,
            event_id=uuid.uuid4(),
        )
    )
    migrated_session.commit()

    assert _answer(migrated_session, unit).evidence_observed is False
