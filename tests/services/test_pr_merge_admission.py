"""WS-P3.7 Increment 4a: the composed, report-only answer to whether the factory may land a
unit's pull request.

The shape of this file is the shape of the answer. **One affirmative test, and one per refusal**,
because a predicate whose job is to refuse is only as good as the directions it refuses in -- and
because `satisfied` is a positive conjunction rather than an empty objection list, each term has
to be shown to be able to say no on its own, with everything else saying yes.

Nothing here acts. There is no credential, no client, and no mutation anywhere in the module under
test; these tests would still pass with the network unplugged, which is the property that lets the
answer be read against real completed units before anything obeys it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import orchestrator.services.pr_merge_admission as admission_module
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Event,
    PackageAcceptanceCriterion,
    ReconciliationCondition,
    ReconciliationResolution,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import record_approval, register_approved_unit
from orchestrator.services.pr_bindings import record_verification_read_head, upsert_pr_binding
from orchestrator.services.pr_merge_admission import (
    MERGE_CAPABILITY,
    admission_for,
    pr_merge_admission,
)
from tests.services.estate_doubles import (
    LANDING_REDEPLOYS,
    LANDING_UNKNOWN,
    SOURCE_UNCONFIGURED,
    SOURCE_UNREADABLE,
    EstateAnswer,
    FakeEstateLandingSource,
    inert_source,
)
from tests.services.test_adjudications import FROM_EVALUATION, add_criterion
from tests.services.test_package_registration import NOW, register_test_revision
from tests.services.test_verifier_decided_completion import HUMAN, VERIFIER, _decide

TARGET = "owner/repo"
HEAD = "0123456789abcdef0123456789abcdef01234567"
SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)


def _envelope(*, merge_level: str = "allowed", target: str | None = TARGET) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        capabilities={"repo.edit": "allowed", MERGE_CAPABILITY: merge_level},
        budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=60),
        constraints={"target_repository": target} if target is not None else {},
    )


def _unit(session: Session, key: str, *, envelope: AuthorityEnvelope | None = None) -> WorkUnit:
    revision = register_test_revision(session, acceptance_criteria=("ac-1",))
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=envelope or _envelope(),
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    # `register_test_revision` is idempotent on the default criteria list -- fixed package id,
    # fixed content hash -- so a second unit here shares the FIRST unit's revision, and declaring
    # the criterion again violates UNIQUE(revision, ac_id). Declare it once per revision.
    already_declared = session.scalar(
        select(PackageAcceptanceCriterion.id).where(
            PackageAcceptanceCriterion.work_package_revision_id == revision.id,
            PackageAcceptanceCriterion.ac_id == "ac-1",
        )
    )
    if already_declared is None:
        add_criterion(session, unit, "ac-1", "automated_check")
    session.commit()
    return unit


def _revision(session: Session, unit: WorkUnit) -> WorkPackageRevision:
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    return revision


def _verifier_decided(session: Session, unit: WorkUnit) -> None:
    _decide(session, unit, "ac-1", actor=VERIFIER, **FROM_EVALUATION)


def _approve_authority(session: Session, unit: WorkUnit) -> None:
    record_approval(
        session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id="devon",
        actor_role=ActorRole.HUMAN,
        reason="the envelope is what I am approving",
        idempotency_key=f"authority-{unit.unit_key}",
        expected_version=unit.version,
    )
    session.commit()


def _bind_and_arm(session: Session, unit: WorkUnit, *, head: str = HEAD) -> None:
    upsert_pr_binding(
        session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        pr_number=7,
        head_sha=head,
        attempt=unit.attempt_count,
    )
    record_verification_read_head(
        session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        head_sha=head,
        attempt=unit.attempt_count,
    )
    session.commit()


def _complete(session: Session, unit: WorkUnit) -> None:
    unit.state = WorkUnitState.COMPLETED.value
    session.commit()


def _ready_unit(session: Session, key: str, **envelope_kwargs) -> WorkUnit:
    """Every term satisfied. Each test below removes exactly one."""
    unit = _unit(session, key, envelope=_envelope(**envelope_kwargs))
    _verifier_decided(session, unit)
    _approve_authority(session, unit)
    _bind_and_arm(session, unit)
    _complete(session, unit)
    return unit


def _answer(session: Session, unit: WorkUnit, source=None):
    return admission_for(session, unit, _revision(session, unit), source or inert_source())


def _open_condition(session: Session, unit: WorkUnit) -> ReconciliationCondition:
    """A divergence the reconciliation lane observed and nobody has decided yet."""
    event = Event(
        actor_id="drift-reconciler",
        action="reconciliation.required",
        subject_type="work_unit",
        subject_id=unit.id,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    session.add(event)
    session.flush()
    condition = ReconciliationCondition(
        work_unit_id=unit.id,
        condition_type="external_merge_alarm",
        observation_kind="github_pr",
        detected_at=NOW,
        detail="the pull request was observed already landed",
        stored_state="open",
        observed_state="closed",
        lineage_hash=f"sha256:{unit.unit_key}",
        normalized_divergence_hash=f"sha256:{unit.unit_key}-divergence",
        event_id=event.id,
        idempotency_key=str(uuid.uuid4()),
    )
    session.add(condition)
    session.commit()
    return condition


def _resolve_condition(session: Session, condition: ReconciliationCondition) -> None:
    event = Event(
        actor_id="devon",
        action="reconciliation.resolved",
        subject_type="reconciliation_condition",
        subject_id=condition.id,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    session.add(event)
    session.flush()
    session.add(
        ReconciliationResolution(
            condition_id=condition.id,
            resolved_by="devon",
            decision="dismissed",
            rationale="I looked; it was a false alarm",
            event_id=event.id,
            idempotency_key=str(uuid.uuid4()),
        )
    )
    session.commit()


# ---------------------------------------------------------------------------------------------
# The one affirmative case.
# ---------------------------------------------------------------------------------------------


def test_a_unit_meeting_every_term_is_admitted(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "merge-admissible")

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is True
    assert answer.refusals == ()
    assert answer.target_repository == TARGET
    assert answer.pr_number == 7
    assert answer.verified_head_sha == HEAD


def test_the_answer_reports_the_head_that_was_adjudicated_not_the_latest(
    migrated_session: Session,
) -> None:
    """`verified_head_sha` is the ARMED head. An act must name it, and naming the latest head
    instead would land whatever has been pushed since the criteria were decided."""
    unit = _ready_unit(migrated_session, "armed-head-reported")
    upsert_pr_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        pr_number=7,
        head_sha="ffffffffffffffffffffffffffffffffffffffff",
        attempt=unit.attempt_count,
    )

    assert _answer(migrated_session, unit).verified_head_sha == HEAD


# ---------------------------------------------------------------------------------------------
# One refusal per term. Everything else says yes in every one of these.
# ---------------------------------------------------------------------------------------------


def test_an_unfinished_unit_is_refused(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "not-completed")
    unit.state = WorkUnitState.AWAITING_REVIEW.value
    migrated_session.commit()

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("work_unit_not_completed",)


def test_a_criterion_a_human_decided_refuses_the_whole_unit(migrated_session: Session) -> None:
    """ADR-0020's safety condition, called rather than re-derived. If a human had to decide, a
    human is already in the loop and the landing is theirs to make."""
    unit = _unit(migrated_session, "human-decided")
    # The state a human actually adjudicates from: a deterministic-floored criterion is decidable
    # by a person only while the unit is awaiting review (WS-P2.17 Inc 2, clause (b)).
    unit.state = WorkUnitState.AWAITING_REVIEW.value
    migrated_session.commit()
    _decide(migrated_session, unit, "ac-1", actor=HUMAN)
    _approve_authority(migrated_session, unit)
    _bind_and_arm(migrated_session, unit)
    _complete(migrated_session, unit)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("criteria_not_verifier_decided",)


def test_an_unresolved_reconciliation_condition_refuses(migrated_session: Session) -> None:
    """The only observations of GitHub this repository holds about a unit. A pull request already
    landed elsewhere, a head observed to move, a check that flipped red afterwards -- each is a
    recorded fact and a pending human decision, and the factory must not land past one."""
    unit = _ready_unit(migrated_session, "open-condition")
    _open_condition(migrated_session, unit)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("open_reconciliation_condition",)


def test_a_resolved_reconciliation_condition_does_not_refuse(migrated_session: Session) -> None:
    """Open means *no resolution row* -- the estate's own set difference, with no status column to
    drift. A condition somebody has decided is not a pending decision, and a term that kept
    refusing after the decision would make every unit that ever diverged permanently unlandable."""
    unit = _ready_unit(migrated_session, "resolved-condition")
    condition = _open_condition(migrated_session, unit)
    _resolve_condition(migrated_session, condition)

    assert _answer(migrated_session, unit).satisfied is True


def test_a_condition_on_another_unit_does_not_refuse_this_one(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "condition-elsewhere")
    other = _ready_unit(migrated_session, "the-other-unit")
    _open_condition(migrated_session, other)

    assert _answer(migrated_session, unit).satisfied is True


def test_a_revision_that_is_not_the_units_own_is_refused_rather_than_answered(
    migrated_session: Session,
) -> None:
    """`required_ac_ids` derives the required criteria from the revision it is HANDED, so a caller
    passing a foreign one asks about a different set and can convert a refusal into an affirmative
    answer. The holder split exists to invite exactly such a caller in the acting increment."""
    unit = _ready_unit(migrated_session, "own-revision")
    # A genuinely different revision: `register_test_revision` keys its package id on the declared
    # criteria, so a different list is a different package rather than a replay of the same one.
    foreign = register_test_revision(migrated_session, acceptance_criteria=("ac-1", "ac-2"))
    assert foreign.id != unit.work_package_revision_id

    with pytest.raises(DomainError) as error:
        admission_for(migrated_session, unit, foreign, inert_source())

    assert error.value.code == "revision_not_for_unit"


def test_an_envelope_that_does_not_grant_landing_is_refused(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "capability-prohibited", merge_level="prohibited")

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("merge_capability_not_authorized",)


def test_an_envelope_that_never_mentions_the_capability_is_refused(
    migrated_session: Session,
) -> None:
    """`level_for` answers `prohibited` for a name it does not carry, so silence refuses. This is
    the shape EVERY unit in production has, and it is why the answer should be no for all of
    them."""
    envelope = AuthorityEnvelope(
        capabilities={"repo.edit": "allowed"},
        budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=60),
        constraints={"target_repository": TARGET},
    )
    unit = _unit(migrated_session, "capability-absent", envelope=envelope)
    _verifier_decided(migrated_session, unit)
    _approve_authority(migrated_session, unit)
    _bind_and_arm(migrated_session, unit)
    _complete(migrated_session, unit)

    assert _answer(migrated_session, unit).refusals == ("merge_capability_not_authorized",)


def test_a_unit_with_no_human_authority_approval_is_refused(migrated_session: Session) -> None:
    """The gate that remains. There is no policy-pattern substitute here, deliberately: an
    envelope granting this capability matches no known-good pattern, so the human gate applies by
    totality and this term asks for the approval unconditionally."""
    unit = _unit(migrated_session, "no-authority-approval")
    _verifier_decided(migrated_session, unit)
    _bind_and_arm(migrated_session, unit)
    _complete(migrated_session, unit)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("authority_approval_not_bound",)


def test_an_approval_bound_to_a_different_fingerprint_is_refused(
    migrated_session: Session,
) -> None:
    """`exact_authority_approval` binds the approval to the unit's CURRENT fingerprint, so an
    approval that no longer describes the envelope is not an approval of it."""
    unit = _ready_unit(migrated_session, "stale-fingerprint")
    unit.authority_fingerprint = "sha256:something-else"
    migrated_session.commit()

    assert _answer(migrated_session, unit).refusals == ("authority_approval_not_bound",)


def test_a_unit_with_no_pull_request_is_refused(migrated_session: Session) -> None:
    unit = _unit(migrated_session, "no-binding")
    _verifier_decided(migrated_session, unit)
    _approve_authority(migrated_session, unit)
    _complete(migrated_session, unit)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("pr_binding_missing",)
    assert answer.pr_number is None
    assert answer.verified_head_sha is None


def test_a_pull_request_whose_head_was_never_adjudicated_is_refused(
    migrated_session: Session,
) -> None:
    unit = _unit(migrated_session, "unarmed-head")
    _verifier_decided(migrated_session, unit)
    _approve_authority(migrated_session, unit)
    upsert_pr_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        pr_number=7,
        head_sha=HEAD,
        attempt=unit.attempt_count,
    )
    _complete(migrated_session, unit)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("verification_head_unarmed",)


def test_a_head_armed_for_an_earlier_attempt_is_refused(migrated_session: Session) -> None:
    """The armed head is write-once per CYCLE. A value carried over from an earlier attempt
    describes a tree an earlier adjudication was about, not this one."""
    unit = _ready_unit(migrated_session, "stale-attempt")
    unit.attempt_count = unit.attempt_count + 1
    migrated_session.commit()

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("verification_head_not_current_attempt",)


def test_a_head_that_moved_after_adjudication_is_refused(migrated_session: Session) -> None:
    """The sharpest of the three: the tree that would land is not the tree the criteria were
    decided about."""
    unit = _ready_unit(migrated_session, "moved-head")
    upsert_pr_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        pr_number=7,
        head_sha="ffffffffffffffffffffffffffffffffffffffff",
        attempt=unit.attempt_count,
    )

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("pr_head_moved_since_verification",)


def test_an_envelope_naming_no_repository_is_refused_without_asking_the_estate(
    migrated_session: Session,
) -> None:
    """Asking about the empty string would report a missing assessment in place of a missing
    constraint, and would pay for a network round-trip to do it."""
    unit = _ready_unit(migrated_session, "no-target", target=None)
    source = inert_source()

    answer = _answer(migrated_session, unit, source)

    assert answer.satisfied is False
    assert answer.refusals == ("merge_target_repository_missing",)
    assert source.asked == []


def test_a_repository_that_changes_something_already_serving_is_refused(
    migrated_session: Session,
) -> None:
    """ADR-0019's boundary, read from the estate rather than from a list of repository names."""
    unit = _ready_unit(migrated_session, "estate-redeploys")
    source = FakeEstateLandingSource(default=EstateAnswer(LANDING_REDEPLOYS))

    answer = _answer(migrated_session, unit, source)

    assert answer.satisfied is False
    assert answer.refusals == ("merge_target_repository_redeploys",)
    assert source.asked == [TARGET]


def test_an_unassessed_repository_is_refused(migrated_session: Session) -> None:
    """The estate saying it has not looked is not permission."""
    unit = _ready_unit(migrated_session, "estate-unknown")
    source = FakeEstateLandingSource(default=EstateAnswer(LANDING_UNKNOWN, "not_assessed"))

    assert _answer(migrated_session, unit, source).refusals == ("merge_target_estate_unknown",)


def test_an_unrecognised_estate_answer_is_refused(migrated_session: Session) -> None:
    """Structural rather than enumerated: a fourth value shipped on the authoring side arrives
    here as no answer, never as the nearest recognisable thing."""
    unit = _ready_unit(migrated_session, "estate-unrecognised")
    source = FakeEstateLandingSource(default=EstateAnswer(None, SOURCE_UNREADABLE))

    assert _answer(migrated_session, unit, source).refusals == (
        "merge_target_estate_source_unreadable",
    )


def test_an_unconfigured_estate_source_is_refused_distinctly(migrated_session: Session) -> None:
    """A missing setting on this deployment and a service refusing send different people
    somewhere different, so they are two codes rather than one."""
    unit = _ready_unit(migrated_session, "estate-unconfigured")
    source = FakeEstateLandingSource(default=EstateAnswer(None, SOURCE_UNCONFIGURED))

    assert _answer(migrated_session, unit, source).refusals == (
        "merge_target_estate_source_unconfigured",
    )


# ---------------------------------------------------------------------------------------------
# Properties of the composed answer itself.
# ---------------------------------------------------------------------------------------------


def test_every_unmet_term_is_reported_not_only_the_first(migrated_session: Session) -> None:
    """Admission reports one reason because a blocked unit needs one thing done next. This is
    asked about a unit that has already finished, where the useful answer is the whole list."""
    unit = _unit(migrated_session, "everything-wrong", envelope=_envelope(merge_level="prohibited"))

    answer = _answer(
        migrated_session, unit, FakeEstateLandingSource(default=EstateAnswer(LANDING_REDEPLOYS))
    )

    assert answer.satisfied is False
    assert set(answer.refusals) == {
        "work_unit_not_completed",
        "criteria_not_verifier_decided",
        "merge_capability_not_authorized",
        "authority_approval_not_bound",
        "pr_binding_missing",
        "merge_target_repository_redeploys",
    }


def test_the_answer_reads_one_normalized_authority_snapshot(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The envelope is what a human's authority approval attests, so it is read exactly once --
    the property admission pins for the same reason. Asserted rather than asserted-in-prose,
    because a second normalization is a second reading of the thing under approval."""
    unit = _ready_unit(migrated_session, "one-authority-snapshot")
    original = admission_module.normalize_authority
    calls: list[object] = []

    def track(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(admission_module, "normalize_authority", track)

    assert _answer(migrated_session, unit).satisfied is True
    assert len(calls) == 1


def test_the_loader_refuses_a_unit_that_does_not_exist(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        pr_merge_admission(migrated_session, uuid.uuid4(), inert_source())

    assert error.value.code == "work_unit_not_found"
