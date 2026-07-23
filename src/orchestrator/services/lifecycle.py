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
from orchestrator.kernel.leases import hash_lease_token
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.kernel.transitions import TransitionGuards, authorize_transition
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    ApprovedDecomposition,
    Claim,
    DecompositionProposalAcMapping,
    DeploymentObservation,
    Event,
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


def _packagerequired_ac_ids(enforcement_snapshot: dict[str, object]) -> tuple[str, ...] | None:
    value = enforcement_snapshot.get("acceptance_criteria")
    if not isinstance(value, list) or not value:
        return None
    ac_ids = tuple(item for item in value if isinstance(item, str) and item.strip())
    if len(ac_ids) != len(value) or len(set(ac_ids)) != len(ac_ids):
        return None
    return ac_ids


def _current_terminal_is_satisfied(
    adjudications: tuple[Adjudication, ...], occurred_at: datetime
) -> bool:
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
            return False
        superseded_ids.add(previous_id)

    current = tuple(row for row in adjudications if row.id not in superseded_ids)
    if len(current) != 1 or not _is_single_chain(current[0], by_id):
        return False
    terminal = current[0]
    if terminal.outcome in {"passed", "not_applicable"}:
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
