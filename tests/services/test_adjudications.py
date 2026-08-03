import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, cast

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Evidence,
    PackageAcceptanceCriterion,
    WorkUnit,
)
from orchestrator.services.evidence import (
    AdjudicationDecision,
    current_adjudication,
    current_evidence,
    record_adjudication,
    record_adjudications,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.verifier_evaluators import human_may_adjudicate
from tests.services.test_dependencies import register_unit


def record(session: Session, command: dict[str, Any]) -> Adjudication | DomainError:
    return record_adjudication(session, **cast(Any, command))


# Every VERIFIER adjudication in production comes from `verify_work_unit`, which derives the
# outcome from the evidence chain and sets this (WS-P2.32). Tests below that use a verifier actor
# INCIDENTALLY -- to reach persistence, idempotency, supersession or concurrency behaviour that has
# nothing to do with who decided -- go through the same door production does. The direct-POST door
# has its own tests, and they assert it is shut.
FROM_EVALUATION: dict[str, Any] = {"from_verifier_evaluation": True}


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


def register_multi_criterion_unit(session: Session, key: str, *ac_ids: str) -> WorkUnit:
    """A unit whose revision DECLARES several acceptance criteria, parked in `awaiting_review`.

    `work_package_revisions` is append-only at the database, so the declared list cannot be widened
    after registration -- a submission covering several criteria needs the list up front, or every
    criterion but `ac-1` is `evidence_subject_invalid`.
    """
    unit = register_unit(session, key, acceptance_criteria=ac_ids)
    unit.state = WorkUnitState.AWAITING_REVIEW
    session.commit()
    return unit


def adjudications_for(session: Session, unit, ac_id: str) -> tuple[Adjudication, ...]:
    return tuple(
        session.scalars(
            select(Adjudication).where(
                Adjudication.work_unit_id == unit.id, Adjudication.ac_id == ac_id
            )
        )
    )


def test_two_criteria_are_adjudicated_in_one_submission(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    # AC-010. The bug: each criterion had its own form and its own expected_version fixed at page
    # render, so submitting one staleness-broke the next. This recorded a wrong outcome on
    # WS-P2.13 AC-002.
    unit = register_multi_criterion_unit(migrated_session, "two-criteria", "ac-1", "ac-2")
    add_criterion(migrated_session, unit, "ac-1", "human.review")
    add_criterion(migrated_session, unit, "ac-2", "human.review")
    migrated_session.commit()
    version_before = unit.version

    result = record_adjudications(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        decisions=(
            AdjudicationDecision(ac_id="ac-1", outcome="passed", rationale="reviewed and met"),
            AdjudicationDecision(
                ac_id="ac-2", outcome="not_applicable", rationale="out of scope here"
            ),
        ),
        idempotency_key="submission-two-criteria",
        expected_version=version_before,
    )

    assert not isinstance(result, DomainError)
    assert tuple(row.ac_id for row in result) == ("ac-1", "ac-2")

    migrated_session.expire_all()
    with Session(migrated_engine) as reader:
        assert len(adjudications_for(reader, unit, "ac-1")) == 1
        assert len(adjudications_for(reader, unit, "ac-2")) == 1
        first = current_adjudication(reader, unit.work_package_revision_id, unit.id, "ac-1")
        second = current_adjudication(reader, unit.work_package_revision_id, unit.id, "ac-2")
        assert first is not None and first.outcome == "passed"
        assert second is not None and second.outcome == "not_applicable"
        # One act, one timestamp -- both criteria share the transaction's clock.
        assert first.decided_at == second.decided_at
        # THE VERSION POST-CONDITION. Recording adjudications does not touch `work_units.version`
        # at all -- not once per submission, not once per criterion. One `expected_version` is
        # therefore checked once and stays valid for every criterion of the submission.
        reread_unit = reader.get(WorkUnit, unit.id)
        assert reread_unit is not None and reread_unit.version == version_before


def test_a_refused_criterion_writes_nothing_at_all(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    # AC-011. All-or-nothing, proved where it can actually break: the refusal is raised by the
    # SECOND criterion, after the first has already been added and flushed. A per-criterion commit
    # would leave `ac-1` behind -- a worse bug than the one this increment fixes.
    unit = register_multi_criterion_unit(migrated_session, "refused-criterion", "ac-1", "ac-2")
    add_criterion(migrated_session, unit, "ac-1", "human.review")
    add_criterion(migrated_session, unit, "ac-2", "automated_test")
    add_evidence(migrated_session, unit, "ac-2", "pytest", {"status": "pass"})
    migrated_session.commit()
    version_before = unit.version

    result = record_adjudications(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        decisions=(
            AdjudicationDecision(ac_id="ac-1", outcome="passed", rationale="reviewed and met"),
            AdjudicationDecision(ac_id="ac-2", outcome="passed", rationale="looks green to me"),
        ),
        idempotency_key="submission-refused-criterion",
        expected_version=version_before,
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"

    migrated_session.expire_all()
    with Session(migrated_engine) as reader:
        assert adjudications_for(reader, unit, "ac-1") == ()
        assert adjudications_for(reader, unit, "ac-2") == ()
        reread_unit = reader.get(WorkUnit, unit.id)
        assert reread_unit is not None and reread_unit.version == version_before


def test_a_stale_submission_is_refused_as_a_whole(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    # AC-011's other half: the unit changed since the page rendered. One expected_version guards
    # the whole submission, so a stale one refuses every criterion, not the tail of them.
    unit = register_multi_criterion_unit(migrated_session, "stale-submission", "ac-1", "ac-2")
    add_criterion(migrated_session, unit, "ac-1", "human.review")
    add_criterion(migrated_session, unit, "ac-2", "human.review")
    migrated_session.commit()

    result = record_adjudications(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        decisions=(
            AdjudicationDecision(ac_id="ac-1", outcome="passed", rationale="reviewed and met"),
            AdjudicationDecision(ac_id="ac-2", outcome="passed", rationale="reviewed and met"),
        ),
        idempotency_key="submission-stale",
        expected_version=unit.version - 1,
    )

    assert isinstance(result, DomainError)
    assert result.code == "version_conflict"

    migrated_session.expire_all()
    with Session(migrated_engine) as reader:
        assert adjudications_for(reader, unit, "ac-1") == ()
        assert adjudications_for(reader, unit, "ac-2") == ()


def test_a_replayed_submission_returns_the_same_rows(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    # One key for the submission. Replay is all-or-nothing too: the same submission returns the
    # same rows and writes nothing new.
    unit = register_multi_criterion_unit(migrated_session, "replayed", "ac-1", "ac-2")
    add_criterion(migrated_session, unit, "ac-1", "human.review")
    add_criterion(migrated_session, unit, "ac-2", "human.review")
    migrated_session.commit()
    submission: dict[str, Any] = {
        "work_package_revision_id": unit.work_package_revision_id,
        "work_unit_id": unit.id,
        "actor": ActorContext("human-1", ActorRole.HUMAN),
        "decisions": (
            AdjudicationDecision(ac_id="ac-1", outcome="passed", rationale="reviewed and met"),
            AdjudicationDecision(ac_id="ac-2", outcome="passed", rationale="reviewed and met"),
        ),
        "idempotency_key": "submission-replayed",
    }

    first = record_adjudications(migrated_session, **cast(Any, submission))
    replay = record_adjudications(migrated_session, **cast(Any, submission))

    assert not isinstance(first, DomainError)
    assert not isinstance(replay, DomainError)
    assert tuple(row.id for row in replay) == tuple(row.id for row in first)

    migrated_session.expire_all()
    with Session(migrated_engine) as reader:
        assert len(adjudications_for(reader, unit, "ac-1")) == 1
        assert len(adjudications_for(reader, unit, "ac-2")) == 1


def test_a_key_reused_for_a_different_submission_is_a_conflict(migrated_session: Session) -> None:
    # What replay compares is the WHOLE submission, not one criterion's decision. Changing any
    # criterion's outcome -- or dropping a criterion from the submission -- is a different act, and
    # a key that already stands for a different act may not silently succeed on a subset.
    unit = register_multi_criterion_unit(migrated_session, "reused-key", "ac-1", "ac-2")
    add_criterion(migrated_session, unit, "ac-1", "human.review")
    add_criterion(migrated_session, unit, "ac-2", "human.review")
    migrated_session.commit()
    submission: dict[str, Any] = {
        "work_package_revision_id": unit.work_package_revision_id,
        "work_unit_id": unit.id,
        "actor": ActorContext("human-1", ActorRole.HUMAN),
        "decisions": (
            AdjudicationDecision(ac_id="ac-1", outcome="passed", rationale="reviewed and met"),
            AdjudicationDecision(ac_id="ac-2", outcome="passed", rationale="reviewed and met"),
        ),
        "idempotency_key": "submission-reused-key",
    }
    first = record_adjudications(migrated_session, **cast(Any, submission))
    assert not isinstance(first, DomainError)

    changed_outcome = record_adjudications(
        migrated_session,
        **cast(
            Any,
            submission
            | {
                "decisions": (
                    submission["decisions"][0],
                    AdjudicationDecision(ac_id="ac-2", outcome="failed", rationale="not met"),
                )
            },
        ),
    )
    dropped_criterion = record_adjudications(
        migrated_session,
        **cast(Any, submission | {"decisions": (submission["decisions"][0],)}),
    )

    assert isinstance(changed_outcome, DomainError)
    assert changed_outcome.code == "idempotency_conflict"
    assert isinstance(dropped_criterion, DomainError)
    assert dropped_criterion.code == "idempotency_conflict"


def test_an_empty_submission_is_refused(migrated_session: Session, ready_unit) -> None:
    # AC-012's service-side floor: a form where the reviewer answered nothing is not a submission
    # that records nothing, it is not a submission.
    result = record_adjudications(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        decisions=(),
        idempotency_key="submission-empty",
    )

    assert isinstance(result, DomainError)
    assert result.code == "adjudication_invalid"


def test_a_criterion_may_not_appear_twice_in_one_submission(
    migrated_session: Session, ready_unit
) -> None:
    # Two decisions for one criterion would chain into a supersession within a single act, which
    # is not something the form can mean. Fail closed rather than pick one.
    add_criterion(migrated_session, ready_unit, "ac-1", "human.review")

    result = record_adjudications(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        decisions=(
            AdjudicationDecision(ac_id="ac-1", outcome="passed", rationale="reviewed and met"),
            AdjudicationDecision(ac_id="ac-1", outcome="failed", rationale="on reflection, no"),
        ),
        idempotency_key="submission-duplicate-ac",
    )

    assert isinstance(result, DomainError)
    assert result.code == "adjudication_invalid"


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
        **FROM_EVALUATION,
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
def test_verifier_records_each_non_waiver_outcome_from_its_own_evaluation(
    migrated_session: Session, ready_unit, outcome: str
) -> None:
    # The verifier's vocabulary is unchanged. What changed (WS-P2.32) is the ROUTE: this is what
    # `verify_work_unit` does after `evaluate_criterion` has read the evidence chain.
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome=outcome,
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="verified",
        idempotency_key=f"adjudication-{outcome}",
        **FROM_EVALUATION,
    )

    assert isinstance(result, Adjudication)
    assert result.outcome == outcome
    assert result.decided_by == "verifier-1"


@pytest.mark.parametrize("outcome", ["passed", "failed", "not_applicable"])
def test_verifier_may_not_adjudicate_outside_its_own_evaluation(
    migrated_session: Session, migrated_engine: Engine, ready_unit, outcome: str
) -> None:
    # THE BYPASS, refused. Until WS-P2.32 this call succeeded, and a `passed` on each required
    # criterion drove the unit to COMPLETED with no evidence anywhere in the system -- past the
    # named-check observer, past unanimity, past `failed_closed` on divergence. `_completion_
    # satisfied` reads adjudications and structurally cannot read evidence, so nothing downstream
    # could notice; the reconciliation lane detects reality CHANGING, never reality having been
    # MISREPORTED, and its `_detect_check` needs a prior OBSERVED success it would never have.
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome=outcome,
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="I read the PR and it looks right to me",
        idempotency_key=f"bypass-{outcome}",
    )

    assert isinstance(result, DomainError)
    assert result.code == "verifier_evaluation_required"
    # Refused, not merely un-returned. A DIFFERENT session is the only reader that cannot see an
    # uncommitted row.
    with Session(migrated_engine) as reader:
        assert adjudications_for(reader, ready_unit, "ac-1") == ()


def test_a_verifier_evidence_reference_does_not_buy_the_bypass(
    migrated_session: Session, ready_unit
) -> None:
    # THE DISCRIMINATING CONTROL, and the reason this is not "require an evidence_id". A non-null
    # reference proves a row exists, not that it SUPPORTS the outcome -- it can point at the
    # worker's own artifact. Here the reference is real, subject-valid and the current chain head,
    # and the answer is still no: what the verifier lacks is not a citation, it is an evaluation.
    add_criterion(migrated_session, ready_unit, "ac-1", "automated_check")
    add_evidence(migrated_session, ready_unit, "ac-1", "runner.pr.opened", {"pr_number": 7})
    migrated_session.commit()
    evidence = current_evidence(
        migrated_session, ready_unit.work_package_revision_id, ready_unit.id, "ac-1"
    )
    assert evidence is not None

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="the PR is open and CI is green on my screen",
        idempotency_key="bypass-with-evidence-reference",
        evidence_id=evidence.id,
    )

    assert isinstance(result, DomainError)
    assert result.code == "verifier_evaluation_required"


def test_the_refusal_names_the_route_and_not_the_role(
    migrated_session: Session, ready_unit
) -> None:
    # A bare `role_forbidden` would say the verifier may not decide this, which is false and would
    # send an operator looking for a different credential. The role is right; the door is wrong,
    # and the recovery hint says which one to use. Same shape as `post_deploy_verifier_required`.
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="looks right",
        idempotency_key="bypass-names-the-route",
    )

    assert isinstance(result, DomainError)
    assert result.recovery == "verify"


def test_a_verifier_still_may_not_waive_even_from_its_own_evaluation(
    migrated_session: Session, ready_unit
) -> None:
    # The waiver branch is checked FIRST and is unchanged: waiving is a human act about accepting
    # a known failure, and no evaluation makes it a machine's to perform. Without this, a reader
    # could reasonably think the new flag re-opened the whole vocabulary.
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="waived",
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="accepting the failure",
        idempotency_key="verifier-waiver",
        **FROM_EVALUATION,
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


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
        **FROM_EVALUATION,
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
        **FROM_EVALUATION,
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
        **FROM_EVALUATION,
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
        **FROM_EVALUATION,
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
            **FROM_EVALUATION,
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
