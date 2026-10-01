from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from orchestrator.api.dependencies import ActorDep, SessionDep
from orchestrator.api.routes.common import (
    ERROR_RESPONSES,
    raise_error,
    require_zero_expected_version,
)
from orchestrator.api.schemas.common import ApprovalResponse, LifecycleCommand, TransitionResponse
from orchestrator.api.schemas.lifecycle import (
    ClaimCommand,
    ContextSnapshotResponse,
    CostActualsCommand,
    CostActualsResponse,
    EventResponse,
    LeaseResponse,
    PrBindingCommand,
    PrBindingResponse,
    PreflightCommandModel,
    ReclaimCommand,
    RenewCommand,
    RequeueCommand,
    RetryCommand,
    UnitResponse,
)
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import ContextSnapshot, WorkUnit
from orchestrator.services.lifecycle.claims import (
    authorize_retry,
    claim_unit,
    reclaim_expired_claim,
    renew_claim,
    requeue_unit,
)
from orchestrator.services.lifecycle.context import PreflightCommand, record_preflight
from orchestrator.services.lifecycle.cost_actuals import record_cost_actuals
from orchestrator.services.lifecycle.lifecycle import (
    TransitionCommand,
    transition_unit,
    unit_history,
)
from orchestrator.services.lifecycle.pr_bindings import arm_verification_head, upsert_pr_binding

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


COMMAND_TARGETS = {
    "ready": WorkUnitState.READY,
    "start": WorkUnitState.EXECUTING,
    "block": WorkUnitState.BLOCKED,
    "request-approval": WorkUnitState.AWAITING_APPROVAL,
    "approve": WorkUnitState.READY,
    "submit": WorkUnitState.SUBMITTED,
    "verify": WorkUnitState.VERIFYING,
    "review": WorkUnitState.AWAITING_REVIEW,
    "revision-required": WorkUnitState.REVISION_REQUIRED,
    "complete": WorkUnitState.COMPLETED,
    "fail": WorkUnitState.FAILED,
    "retry": WorkUnitState.READY,
    "cancel": WorkUnitState.CANCELLED,
}


@router.post("/work-units/{unit_id}/requeue", response_model=UnitResponse)
def requeue(
    unit_id: UUID,
    body: RequeueCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-006. SYSTEM recovery for the NOT-exhausted case; `retry` owns the exhausted one."""
    return raise_error(requeue_unit(session, unit_id, actor, **body.model_dump()))


@router.post("/work-units/{unit_id}/pr-binding", response_model=PrBindingResponse)
def pr_binding(
    unit_id: UUID,
    body: PrBindingCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """The worker reports the PR it opened, and re-reports the head after every push.

    This is the orchestrator's EXPECTATION of the pull request. It is deliberately written by our
    own side of the ledger and never derived from an observation: if observed reality wrote the
    expectation, an attacker's push would silently become the expected head and no divergence
    could ever fire.
    """
    require_zero_expected_version(body.expected_version, "pr binding")
    return raise_error(
        upsert_pr_binding(
            session,
            actor=actor,
            work_unit_id=unit_id,
            pr_number=body.pr_number,
            head_sha=body.head_sha,
            attempt=body.attempt,
            lease_token=body.lease_token,
        )
    )


@router.post("/work-units/{unit_id}/preflight", response_model=ContextSnapshotResponse)
def preflight(
    unit_id: UUID,
    body: PreflightCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_preflight(
        session,
        PreflightCommand(
            work_unit_id=unit_id,
            standing_context=body.standing_context,
            previous_context_snapshot_id=body.previous_context_snapshot_id,
            approval_id=body.approval_id,
            purpose=body.purpose,
            idempotency_key=body.idempotency_key,
            attempt=body.attempt,
            lease_token=body.lease_token,
            expected_version=body.expected_version,
        ),
        actor,
    )
    if isinstance(result, DomainError):
        raise result
    session.commit()
    return result


@router.get(
    "/work-units/{unit_id}/context-snapshots",
    response_model=list[ContextSnapshotResponse],
)
def context_snapshots(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    if session.get(WorkUnit, unit_id) is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return tuple(
        session.scalars(
            select(ContextSnapshot)
            .where(ContextSnapshot.work_unit_id == unit_id)
            .order_by(ContextSnapshot.created_at, ContextSnapshot.id)
        )
    )


@router.post("/work-units/{unit_id}/claim", response_model=LeaseResponse)
def claim(
    unit_id: UUID,
    body: ClaimCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return raise_error(
        claim_unit(
            session,
            unit_id,
            actor,
            body.idempotency_key,
            expected_version=body.expected_version,
            standing_context=body.standing_context,
        )
    )


@router.post("/work-units/{unit_id}/renew", response_model=LeaseResponse)
def renew(
    unit_id: UUID,
    body: RenewCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return raise_error(
        renew_claim(
            session,
            unit_id,
            actor,
            body.attempt,
            body.lease_token,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        )
    )


@router.post("/work-units/{unit_id}/reclaim-expired-claim", response_model=LeaseResponse)
def reclaim_expired(
    unit_id: UUID,
    body: ReclaimCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return raise_error(
        reclaim_expired_claim(
            session,
            unit_id,
            actor,
            ActorContext(body.next_owner_id, ActorRole.WORKER),
            body.idempotency_key,
            expected_version=body.expected_version,
            standing_context=body.standing_context,
        )
    )


@router.post(
    "/work-units/{unit_id}/commands/{command}",
    response_model=TransitionResponse,
)
def command(
    unit_id: UUID,
    command: str,
    body: LifecycleCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    target = COMMAND_TARGETS.get(command)
    if target is None:
        raise DomainError("command_not_found", "unknown lifecycle command", None)
    return transition_unit(
        session,
        TransitionCommand(
            unit_id=unit_id,
            target=target,
            actor=actor,
            expected_version=body.expected_version,
            idempotency_key=body.idempotency_key,
            attempt=body.attempt,
            lease_token=body.lease_token,
            reason=body.reason,
            standing_context=body.standing_context,
            context_snapshot_id=body.context_snapshot_id,
        ),
        # Submitting is the moment the worker hands a head over to be adjudicated, so it is the
        # moment the divergence alarm is armed -- in the SAME transaction, holding the unit row
        # lock. Arming later, at verify time, would re-read the head and silently adopt any push
        # that landed in between as the new expectation, which is exactly what must never happen.
        after=(
            (lambda db, unit: arm_verification_head(db, unit, actor=actor))
            if target is WorkUnitState.SUBMITTED
            else None
        ),
    )


@router.post("/work-units/{unit_id}/retry-authorization", response_model=ApprovalResponse)
def retry_authorization(
    unit_id: UUID,
    body: RetryCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return raise_error(authorize_retry(session, unit_id, actor, **body.model_dump()))


@router.post("/work-units/{unit_id}/cost-actuals", response_model=CostActualsResponse)
def cost_actuals(
    unit_id: UUID,
    body: CostActualsCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """The runner reports the actual LLM cost of one attempt (WS-P2.4 Increment 1).

    Claim-gated exactly like evidence/pr-binding: a worker must prove it holds this unit's live
    claim. Emitted before the terminal fail/submit transition so the lease is still valid.
    """
    require_zero_expected_version(body.expected_version, "cost actuals")
    event = record_cost_actuals(
        session,
        actor=actor,
        work_unit_id=unit_id,
        attempt=body.attempt,
        lease_token=body.lease_token,
        cost_known=body.cost_known,
        llm_calls=body.llm_calls,
        num_turns=body.num_turns,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cost_usd=body.cost_usd,
        idempotency_key=body.idempotency_key,
    )
    return CostActualsResponse(
        work_unit_id=unit_id,
        attempt=body.attempt,
        event_id=event.id,
        cost_known=body.cost_known,
    )


@router.get("/work-units/{unit_id}/history", response_model=list[EventResponse])
def history(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return unit_history(session, unit_id)
