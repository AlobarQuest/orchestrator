import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import Clock, TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.context import context_fingerprint
from orchestrator.kernel.evidence_types import OBSERVED_EVIDENCE_TYPES
from orchestrator.kernel.leases import hash_lease_token
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.kernel.transitions import (
    DESIGNED_HUMAN_GATES,
    TransitionGuards,
    authorize_transition,
)
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    ApprovedDecomposition,
    Claim,
    DecompositionProposalAcMapping,
    DeploymentObservation,
    Event,
    Evidence,
    PackageAcceptanceCriterion,
    UnitPrBinding,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.claim_release import release_claim

# The single source of truth for the generated post-deploy AC ids: this module PRODUCES them
# (required_ac_ids for a generated post-deploy unit); `services.evidence` imports this same tuple
# to gate public adjudication. Do not keep a second copy -- a divergence would let a newly
# generated post-deploy AC be publicly adjudicated.
POST_DEPLOY_AC_IDS = (
    "post-deploy-artifact",
    "post-deploy-auth",
    "post-deploy-dispatch",
    "post-deploy-health",
    "post-deploy-routes",
)

# The capability a generated follow-up review unit carries -- the same string
# `services.follow_ups` mints units with and `is_generated_follow_up_unit` /
# `_is_generated_follow_up_subject` check alongside the derived unit id. Defined here rather than in
# `follow_ups` (which is where it originally lived) because `follow_ups` already imports one-way
# FROM this module (`ActorContext`); putting the constant in the module the others already depend
# on avoids a cycle without inventing a new shared module. `follow_ups.FOLLOW_UP_CAPABILITY`
# re-exports this value so its existing external imports are unaffected.
#
# It is NOT on its own a marker: it is authorable, so `is_generated_follow_up_unit` requires the
# derived id too. It is deliberately absent from ORCHESTRATOR_ONLY_CAPABILITIES, so unit ingress
# refuses it outright -- `_mint` constructs its unit directly and never consults that vocabulary.
FOLLOW_UP_CAPABILITY = "follow_up_review"

# The two outcomes that settle a criterion without reservation. `waived` deliberately is not one
# of them: a waiver settles a criterion whose evidence FAILED, and only a HUMAN may record one
# (`_authorize_outcome`). Kept here so the completion check and the verifier-decided check below
# agree on what "satisfying" means -- they differ only in who is allowed to have said it.
#
# not-a-vocabulary: internal policy subset of adjudication outcomes (which outcomes settle a
# criterion outright), not a set any producer outside this repository must agree with. The full
# outcome vocabulary is pinned by `ck_adjudications_outcome`; this is a judgment made over it, the
# same shape as `evidence.NON_WAIVER_OUTCOMES`.
SATISFYING_OUTCOMES = frozenset({"passed", "not_applicable"})

# The single source of truth for the generated follow-up review AC id. Same producer/consumer
# split as the tuple above: this module PRODUCES it (required_ac_ids for a review unit) and
# `services.evidence` imports it to decide subject validity. One copy only.
#
# It is deliberately NOT gated the way the ids above are. Those are verifier-owned and public
# adjudication must refuse them; this one is human-owned by design and public adjudication must
# ACCEPT it. Two rules pointing opposite ways, asserted in both directions in the tests.
FOLLOW_UP_AC_ID = "follow-up-review"

# The generated follow-up criterion's evidence type. `services.verifier_criteria` stamps this onto
# the transient criterion it constructs; `services.evidence` needs the identical value as the
# fallback for `_criterion_evidence_type` (the generated criterion is never persisted as a
# `PackageAcceptanceCriterion` row, so the normal DB lookup finds nothing). Naming it once here,
# rather than repeating the literal in both call sites, is the same discipline as the two tuples
# above -- `observation` is not new vocabulary (it is already in JUDGMENT_TYPES), only its
# ownership by this one AC id is.
FOLLOW_UP_EVIDENCE_TYPE = "observation"


def follow_up_unit_id(revision_id: uuid.UUID) -> uuid.UUID:
    """The id under which `services.follow_ups` mints a revision's follow-up review unit.

    Content-addressed, so a second minting pass cannot create a second row. This is the structural
    half of the idempotency story; the already-minted skip is the reporting half, and the unique
    constraint on `(work_package_revision_id, unit_key)` is the backstop if both are bypassed.

    Defined HERE rather than in `follow_ups` -- the same move already made for
    `FOLLOW_UP_CAPABILITY`, for the same reason. `follow_ups` imports one-way FROM this module, so
    putting the derivation in the module the identity predicates already live in gives them one
    definition to share without a cycle.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"sds:follow-up:{revision_id}")


@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    role: ActorRole


@dataclass(frozen=True)
class TransitionCommand:
    unit_id: uuid.UUID
    target: WorkUnitState
    actor: ActorContext
    expected_version: int
    idempotency_key: str
    attempt: int | None = None
    lease_token: str | None = None
    reason: str | None = None
    standing_context: dict[str, Any] | None = None
    context_snapshot_id: uuid.UUID | None = None


@dataclass(frozen=True)
class TransitionResult:
    unit_id: uuid.UUID
    state: WorkUnitState
    version: int
    event_id: uuid.UUID


def require_operator_actor(actor: ActorContext) -> None:
    """Read surfaces that enumerate failure signatures are operator-only.

    SYSTEM is the M2M lane and HUMAN is the review lane; a worker or verifier credential has no
    business enumerating another unit's failures.
    """
    if actor.role not in {ActorRole.SYSTEM, ActorRole.HUMAN}:
        raise DomainError("role_forbidden", "only an operator may read this surface", None)


def unit_history(session: Session, unit_id: uuid.UUID) -> tuple[Event, ...]:
    if session.get(WorkUnit, unit_id) is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return tuple(
        session.scalars(
            select(Event).where(Event.subject_id == unit_id).order_by(Event.occurred_at, Event.id)
        )
    )


def transition_unit(
    session: Session,
    command: TransitionCommand,
    *,
    clock: Clock | None = None,
    after: Callable[[Session, WorkUnit], None] | None = None,
) -> TransitionResult:
    """`after` runs inside the transition's transaction, holding the unit row lock, and only for
    a real transition -- an idempotent replay skips it, because its effect already happened.

    It exists so a caller can attach a side effect to a specific transition (the SUBMIT route
    arms the PR head) without this module having to know about that caller's concern. Inverting
    it -- importing the side effect here -- would make lifecycle depend on services that already
    depend on lifecycle.
    """
    try:
        result = _perform_transition(session, command, clock or TransactionClock(), after)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def _perform_transition(
    session: Session,
    command: TransitionCommand,
    clock: Clock,
    after: Callable[[Session, WorkUnit], None] | None = None,
) -> TransitionResult:
    unit = session.execute(
        select(WorkUnit).where(WorkUnit.id == command.unit_id).with_for_update()
    ).scalar_one_or_none()
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)

    existing = session.execute(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return _idempotent_result(existing, command)
    if unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )

    source = WorkUnitState(unit.state)
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    occurred_at = clock.now(session)
    try:
        authorize_transition(
            source,
            command.target,
            command.actor.role,
            _transition_guards(session, unit, revision, occurred_at),
        )
    except DomainError as error:
        error.current_state = unit.state
        error.current_version = unit.version
        if error.code == "invalid_transition" and source is WorkUnitState.EXECUTING:
            error.recovery = "submit"
        raise
    _apply_claim_transition_effects(session, unit, revision, command, occurred_at)

    next_version = unit.version + 1
    unit.state = command.target
    unit.version = next_version
    event = _transition_event(command, unit, source, revision.registry_version, occurred_at)
    session.add(event)
    session.flush()
    if after is not None:
        after(session, unit)
        session.flush()
    return TransitionResult(unit.id, command.target, next_version, event.id)


def _transition_event(
    command: TransitionCommand,
    unit: WorkUnit,
    source: WorkUnitState,
    registry_version: int,
    occurred_at: datetime,
) -> Event:
    improvisation = (
        command.actor.role is ActorRole.HUMAN
        and (source, command.target) not in DESIGNED_HUMAN_GATES
    )
    return Event(
        occurred_at=occurred_at,
        actor_id=command.actor.actor_id,
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=source,
        to_state=command.target,
        payload={
            "actor_role": command.actor.role,
            "command": _command_identity(command, source),
            "registry_version": registry_version,
            "reason": command.reason,
            "version": unit.version,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=command.idempotency_key,
        improvisation=improvisation,
    )


def _idempotent_result(event: Event, command: TransitionCommand) -> TransitionResult:
    if event.subject_type != "work_unit" or event.action != "work_unit.transitioned":
        raise _idempotency_conflict()
    try:
        source = WorkUnitState(event.from_state)
    except (TypeError, ValueError):
        raise _idempotency_conflict() from None
    expected = event.subject_id == command.unit_id and event.payload.get(
        "command"
    ) == _command_identity(command, source)
    if not expected:
        raise _idempotency_conflict()
    version = event.payload.get("version")
    if not isinstance(version, int):
        raise DomainError("event_invalid", "transition event has no valid version", None)
    return TransitionResult(command.unit_id, command.target, version, event.id)


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )


def _command_identity(command: TransitionCommand, source: WorkUnitState) -> dict[str, object]:
    return {
        "action": "work_unit.transitioned",
        "actor_id": command.actor.actor_id,
        "actor_role": command.actor.role,
        "context_fingerprint": (
            context_fingerprint(command.standing_context)
            if command.standing_context is not None
            else None
        ),
        "context_snapshot_id": (
            str(command.context_snapshot_id) if command.context_snapshot_id is not None else None
        ),
        "expected_version": command.expected_version,
        "from_state": source,
        "attempt": command.attempt,
        "lease_token_hash": (
            hash_lease_token(command.lease_token) if command.lease_token is not None else None
        ),
        "reason": command.reason,
        "target": command.target,
        "unit_id": str(command.unit_id),
    }


def _require_active_claim(
    session: Session,
    unit: WorkUnit,
    command: TransitionCommand,
    occurred_at: datetime,
) -> Claim:
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id)
        .order_by(Claim.attempt.desc())
        .limit(1)
        .with_for_update()
    )
    valid = (
        claim is not None
        and command.attempt is not None
        and command.lease_token is not None
        and claim.claimed_by == command.actor.actor_id
        and claim.attempt == command.attempt
        and secrets.compare_digest(claim.lease_token_hash, hash_lease_token(command.lease_token))
        and claim.released_at is None
        and claim.lease_expires_at > occurred_at
    )
    if not valid:
        raise DomainError(
            "active_claim_required",
            "worker mutation requires its active claim credentials",
            "claim",
            current_state=unit.state,
            current_version=unit.version,
        )
    assert claim is not None
    return claim


def _apply_claim_transition_effects(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    command: TransitionCommand,
    occurred_at: datetime,
) -> None:
    if command.actor.role is ActorRole.WORKER:
        claim = _require_active_claim(session, unit, command, occurred_at)
        if command.target is WorkUnitState.EXECUTING:
            claim.execution_context_snapshot_id = _execution_context_snapshot_id(
                session, unit, revision, claim, command
            )
        elif command.target is WorkUnitState.FAILED:
            release_claim(claim, terminal_reason="work_unit_failed", released_at=occurred_at)
        return

    if command.actor.role is ActorRole.HUMAN and command.target is WorkUnitState.CANCELLED:
        claim = _latest_unreleased_claim(session, unit)
        if claim is not None:
            release_claim(claim, terminal_reason="work_unit_cancelled", released_at=occurred_at)


def _latest_unreleased_claim(session: Session, unit: WorkUnit) -> Claim | None:
    return session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id, Claim.released_at.is_(None))
        .order_by(Claim.attempt.desc())
        .limit(1)
        .with_for_update()
    )


def _execution_context_snapshot_id(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    claim: Claim,
    command: TransitionCommand,
) -> uuid.UUID | None:
    # Empty means "none supplied", exactly as in the claim path: `runner_brief` serves `{}`
    # and a worker echoes it back into `start`. Treating `{}` as a supplied-but-incomplete
    # context rejected the value the orchestrator itself had served.
    if not command.standing_context:
        if _has_required_context(revision):
            raise DomainError("context_missing_required", "standing context is incomplete", None)
        return None

    from orchestrator.services.context import PreflightCommand, require_execution_context

    snapshot = require_execution_context(
        session,
        PreflightCommand(
            work_unit_id=unit.id,
            standing_context=command.standing_context,
            previous_context_snapshot_id=command.context_snapshot_id or claim.context_snapshot_id,
            approval_id=None,
            purpose="execution",
            idempotency_key=f"{command.idempotency_key}:execution-context",
            attempt=claim.attempt,
            lease_token=command.lease_token,
        ),
        command.actor,
    )
    return snapshot.id


def _has_required_context(revision: WorkPackageRevision) -> bool:
    required = revision.enforcement_snapshot.get("required_context")
    return isinstance(required, dict) and bool(required)


def _transition_guards(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    occurred_at: datetime,
) -> TransitionGuards:
    approval_recorded = (
        session.execute(
            select(Approval.id)
            .where(
                Approval.subject_type == "action",
                Approval.subject_id == unit.id,
                Approval.decision == "approved",
                Approval.subject_revision_or_fingerprint == str(unit.version),
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    adjudications = tuple(
        session.execute(
            select(Adjudication).where(
                Adjudication.work_unit_id == unit.id,
                Adjudication.work_package_revision_id == revision.id,
            )
        ).scalars()
    )
    return TransitionGuards(
        approval_recorded,
        _completion_satisfied(
            required_ac_ids(session, revision, unit),
            adjudications,
            occurred_at,
        ),
        _submission_binding_recorded(session, unit),
    )


def _submission_binding_recorded(session: Session, unit: WorkUnit) -> bool:
    """Whether EXECUTING -> SUBMITTED may proceed: a unit that may open a pull request must have
    recorded a binding for THIS attempt before submitting.

    Capability-keyed: a unit whose envelope does not allow ``github.pr.create`` never opens a PR,
    so it has nothing to bind and submits freely -- that is the load-bearing contract non-runner
    submitters (drills, SYSTEM, human) rely on. Attempt-scoped: a binding carried over from a
    previous attempt does NOT satisfy the guard. Attempt 1 opens PR #100 and submits; the unit
    goes REVISION_REQUIRED -> READY -> re-dispatch; attempt 2's binding POST fails and the runner
    submits anyway -- the old row is still present, but its ``binding_attempt`` is 1, so the guard
    refuses, and the divergence alarm cannot arm on attempt 1's stale head.
    """
    envelope = normalize_authority(unit.authority)
    if envelope.level_for("github.pr.create") != "allowed":
        return True
    binding = session.get(UnitPrBinding, unit.id)
    return binding is not None and binding.binding_attempt == unit.attempt_count


def _completion_satisfied(
    required_ac_ids: tuple[str, ...] | None,
    adjudications: tuple[Adjudication, ...],
    occurred_at: datetime,
) -> bool:
    if required_ac_ids is None:
        return False
    grouped = {ac_id: [] for ac_id in required_ac_ids}
    for adjudication in adjudications:
        if adjudication.ac_id not in grouped:
            continue
        grouped[adjudication.ac_id].append(adjudication)
    return all(
        _current_terminal_is_satisfied(tuple(grouped[ac_id]), occurred_at)
        for ac_id in required_ac_ids
    )


def required_ac_ids(
    session: Session,
    revision: WorkPackageRevision,
    unit: WorkUnit,
) -> tuple[str, ...] | None:
    if _is_generated_post_deploy_unit(session, revision, unit):
        return POST_DEPLOY_AC_IDS
    if is_generated_follow_up_unit(unit):
        return (FOLLOW_UP_AC_ID,)

    has_approved_decomposition = (
        session.execute(
            select(ApprovedDecomposition.id)
            .where(
                ApprovedDecomposition.work_package_revision_id == revision.id,
                ApprovedDecomposition.superseded_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    mapped_ac_ids = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion.ac_id)
            .join(
                DecompositionProposalAcMapping,
                DecompositionProposalAcMapping.package_acceptance_criterion_id
                == PackageAcceptanceCriterion.id,
            )
            .join(
                ApprovedDecomposition,
                ApprovedDecomposition.proposal_id == DecompositionProposalAcMapping.proposal_id,
            )
            .where(
                ApprovedDecomposition.work_package_revision_id == revision.id,
                ApprovedDecomposition.superseded_at.is_(None),
                PackageAcceptanceCriterion.work_package_revision_id == revision.id,
                DecompositionProposalAcMapping.unit_key == unit.unit_key,
            )
            .order_by(PackageAcceptanceCriterion.ac_id)
        )
    )
    if has_approved_decomposition:
        return mapped_ac_ids
    return _packagerequired_ac_ids(revision.enforcement_snapshot)


def _is_generated_post_deploy_unit(
    session: Session,
    revision: WorkPackageRevision,
    unit: WorkUnit,
) -> bool:
    observation = session.scalar(
        select(DeploymentObservation.id).where(
            DeploymentObservation.work_package_revision_id == revision.id,
            DeploymentObservation.post_deploy_work_unit_id == unit.id,
        )
    )
    return observation is not None


def is_generated_follow_up_unit(unit: WorkUnit) -> bool:
    """Is this unit one the follow-up minting pass created?

    Identity, not capability. `required_capability` is a field a unit AUTHOR supplies, and both
    ingress paths accept any capability the orchestrator recognises -- so a capability-only marker
    is forgeable, and forging it substitutes this module's single generated criterion for the
    package's real acceptance criteria. `follow_up_unit_id` is a `uuid5` over the revision id: only
    the minting pass produces it, and no ingress path lets an author choose it for a unit on that
    revision, because the row it names is the minted one.

    The capability is kept as a second clause because it is what the rest of the system reads (the
    envelope, the criterion generator, the queue). The id is what makes the marker unforgeable.
    """
    return unit.required_capability == FOLLOW_UP_CAPABILITY and unit.id == follow_up_unit_id(
        unit.work_package_revision_id
    )


def _packagerequired_ac_ids(enforcement_snapshot: dict[str, object]) -> tuple[str, ...] | None:
    value = enforcement_snapshot.get("acceptance_criteria")
    if not isinstance(value, list) or not value:
        return None
    ac_ids = tuple(item for item in value if isinstance(item, str) and item.strip())
    if len(ac_ids) != len(value) or len(set(ac_ids)) != len(ac_ids):
        return None
    return ac_ids


@dataclass(frozen=True)
class CriterionDecisionRefusal:
    """Why one criterion (or the unit as a whole, when `ac_id` is None) does not qualify."""

    ac_id: str | None
    code: str


@dataclass(frozen=True)
class VerifierDecidedCompletion:
    """ADR-0020's safety condition, as two clauses that are reported separately.

    The decision is one sentence -- *"resolved deterministically from OBSERVED evidence, with no
    human adjudication"* -- and it has two halves that fail for different reasons and are fixed by
    different people. `decided_by_verifier` is the second half; `evidence_observed` is the first.
    Both are positive conjunctions over the required criteria, computed independently, and
    `satisfied` is their AND rather than an emptiness test over `refusals`.

    Kept apart because a criterion can carry observed evidence and still have been decided by a
    human, and can be verifier-decided off evidence the worker attested to. Collapsing them would
    report one defect for two situations.
    """

    satisfied: bool
    decided_by_verifier: bool
    evidence_observed: bool
    refusals: tuple[CriterionDecisionRefusal, ...]


def verifier_decided_completion(
    session: Session,
    revision: WorkPackageRevision,
    unit: WorkUnit,
) -> VerifierDecidedCompletion:
    """Did EVERY required acceptance criterion reach a current terminal adjudication that the
    verifier recorded from its own evaluation of evidence?

    This is a strictly narrower question than `_completion_satisfied`, which asks only whether the
    criteria were settled acceptably and never asks who settled them, or how. Four things
    disqualify a criterion, and each is a separate refusal because each fails for its own reason:

    * there is no single valid current adjudication for it;
    * its outcome is not one that settles a criterion outright -- which excludes `waived`
      SPECIFICALLY and independently of the role column, since a waiver settles FAILED evidence
      and `_authorize_outcome` admits one only from a HUMAN;
    * the deciding actor's kind was not recorded, i.e. the row predates the column. NULL is
      *unknown*, and unknown refuses; it is never read as "not a human";
    * the deciding actor was not the verifier.

    A fifth disqualifies the UNIT rather than a criterion: a non-verifier adjudication recorded on
    this unit against an `ac_id` that is not one of its required ones. That is reachable -- for a
    unit born of a decomposition, `_validated_subject` admits any `ac_id` the REVISION declares,
    which is a superset of the ones mapped to this unit -- and a per-criterion scan cannot see it.
    ADR-0020's condition is "with no human adjudication", not "with no human adjudication among
    the criteria that happened to be required", so a human who decided anything at all here was in
    the loop and the answer is no.

    `role == verifier` carries its weight only because `_authorize_outcome` refuses a verifier
    adjudication that did not come from `verify_work_unit`'s own evaluation
    (`verifier_evaluation_required`, WS-P2.32). That implication lives in code rather than in the
    schema, so it is asserted by test rather than assumed here.

    Never raises. `required_ac_ids` returns None rather than refusing for a unit whose revision
    declares nothing usable, and None is simply a refusal here -- this answer is served from a read
    surface that must keep answering for every unit that exists, including the historical ones.
    """
    required = required_ac_ids(session, revision, unit)
    if not required:
        return VerifierDecidedCompletion(
            satisfied=False,
            decided_by_verifier=False,
            evidence_observed=False,
            refusals=(CriterionDecisionRefusal(None, "required_criteria_undeclared"),),
        )

    grouped: dict[str, list[Adjudication]] = {ac_id: [] for ac_id in required}
    outside: list[CriterionDecisionRefusal] = []
    rows = session.scalars(select(Adjudication).where(Adjudication.work_unit_id == unit.id))
    for adjudication in rows:
        if adjudication.ac_id in grouped:
            grouped[adjudication.ac_id].append(adjudication)
        elif adjudication.decided_by_role != ActorRole.VERIFIER.value:
            outside.append(
                CriterionDecisionRefusal(adjudication.ac_id, "decision_outside_required_criteria")
            )

    observed = _observed_evidence_ids(session, unit)
    verdicts = tuple(
        _criterion_decision_verdict(ac_id, tuple(grouped[ac_id]), observed) for ac_id in required
    )
    # Each clause is each criterion's own positive answer, never "no refusal was raised". An
    # answer whose affirmative case is an empty objection list is the fail-open shape this
    # repository keeps finding, and it is the reason these fields are computed separately.
    # Safe as an emptiness test only because `outside` is filled by the SAME pass that fills
    # `grouped`: a query returning nothing leaves every criterion refusing `no_current_adjudication`
    # rather than leaving this term quietly true.
    nobody_decided_anything_else = not outside
    decided_by_verifier = (
        all(verdict.decided_by_verifier for verdict in verdicts) and nobody_decided_anything_else
    )
    evidence_observed = all(verdict.evidence_observed for verdict in verdicts)
    return VerifierDecidedCompletion(
        satisfied=decided_by_verifier and evidence_observed,
        decided_by_verifier=decided_by_verifier,
        evidence_observed=evidence_observed,
        refusals=tuple(refusal for verdict in verdicts for refusal in verdict.refusals)
        + tuple(outside),
    )


def _observed_evidence_ids(session: Session, unit: WorkUnit) -> frozenset[uuid.UUID]:
    """Which of this unit's evidence rows the orchestrator OBSERVED, rather than was told about.

    Resolved by id rather than by re-reading each adjudication's row, so one query answers the
    whole unit. Membership is set algebra against the named producer set -- an evidence type this
    build does not recognise is simply not observed, which is the direction that refuses.
    """
    return frozenset(
        session.scalars(
            select(Evidence.id).where(
                Evidence.work_unit_id == unit.id,
                Evidence.evidence_type.in_(OBSERVED_EVIDENCE_TYPES),
            )
        )
    )


@dataclass(frozen=True)
class _CriterionVerdict:
    decided_by_verifier: bool
    evidence_observed: bool
    refusals: tuple[CriterionDecisionRefusal, ...]


def _criterion_decision_verdict(
    ac_id: str,
    adjudications: tuple[Adjudication, ...],
    observed_evidence_ids: frozenset[uuid.UUID],
) -> _CriterionVerdict:
    terminal = _current_terminal(adjudications)
    if terminal is None:
        return _CriterionVerdict(
            decided_by_verifier=False,
            evidence_observed=False,
            refusals=(CriterionDecisionRefusal(ac_id, "no_current_adjudication"),),
        )
    settles = terminal.outcome in SATISFYING_OUTCOMES
    # Asked separately from `settles`, which already excludes it, because a waiver is the one
    # outcome that is HUMAN by construction -- two reasons to refuse, the second of which does not
    # depend on a schema column that can be NULL.
    waived = terminal.outcome == "waived"
    verifier_decided = terminal.decided_by_role == ActorRole.VERIFIER.value
    # WHAT the decision rested on, as distinct from WHO made it. `verify_work_unit` cites the
    # evidence row it resolved from, so an adjudication naming no evidence rested on none this
    # side can point at, and one naming a row the worker recorded rested on an attestation.
    observed = terminal.evidence_id is not None and terminal.evidence_id in observed_evidence_ids
    refusals: list[CriterionDecisionRefusal] = []
    if waived:
        refusals.append(CriterionDecisionRefusal(ac_id, "criterion_waived"))
    if not settles:
        refusals.append(CriterionDecisionRefusal(ac_id, "outcome_does_not_settle_criterion"))
    if terminal.decided_by_role is None:
        refusals.append(CriterionDecisionRefusal(ac_id, "decider_kind_unrecorded"))
    elif not verifier_decided:
        refusals.append(CriterionDecisionRefusal(ac_id, "decider_was_not_the_verifier"))
    if not observed:
        refusals.append(CriterionDecisionRefusal(ac_id, "criterion_evidence_not_observed"))
    return _CriterionVerdict(
        decided_by_verifier=settles and not waived and verifier_decided,
        evidence_observed=observed,
        refusals=tuple(refusals),
    )


def _current_terminal(adjudications: tuple[Adjudication, ...]) -> Adjudication | None:
    """The one valid chain head for a criterion, or None if there isn't exactly one.

    Extracted so that the two questions asked of a criterion -- "is it satisfied?" and "who
    decided it?" -- resolve "which row is current" identically. A second notion of the current
    adjudication is the defect class `_record_one_adjudication` already warns about for evidence.
    """
    by_id = {adjudication.id: adjudication for adjudication in adjudications}
    superseded_ids: set[uuid.UUID] = set()
    for adjudication in adjudications:
        previous_id = adjudication.supersedes_adjudication_id
        if previous_id is None:
            continue
        previous = by_id.get(previous_id)
        if (
            previous is None
            or previous.ac_id != adjudication.ac_id
            or previous_id in superseded_ids
            or previous_id == adjudication.id
        ):
            return None
        superseded_ids.add(previous_id)

    current = tuple(row for row in adjudications if row.id not in superseded_ids)
    if len(current) != 1 or not _is_single_chain(current[0], by_id):
        return None
    return current[0]


def _current_terminal_is_satisfied(
    adjudications: tuple[Adjudication, ...], occurred_at: datetime
) -> bool:
    terminal = _current_terminal(adjudications)
    if terminal is None:
        return False
    if terminal.outcome in SATISFYING_OUTCOMES:
        return True
    return (
        terminal.outcome == "waived"
        and terminal.scope is None
        and (terminal.expires_at is None or terminal.expires_at > occurred_at)
    )


def _is_single_chain(current: Adjudication, by_id: dict[uuid.UUID, Adjudication]) -> bool:
    visited: set[uuid.UUID] = set()
    cursor: Adjudication | None = current
    while cursor is not None:
        if cursor.id in visited:
            return False
        visited.add(cursor.id)
        previous_id = cursor.supersedes_adjudication_id
        cursor = by_id.get(previous_id) if previous_id is not None else None
    return len(visited) == len(by_id)
