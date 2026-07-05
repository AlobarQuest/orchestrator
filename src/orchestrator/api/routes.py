from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.api.schemas import (
    AdjudicationCommand,
    AdjudicationResponse,
    ApprovalCommand,
    ApprovalResponse,
    ClaimCommand,
    DependencyCommand,
    DependencyResolutionCommand,
    DependencyResponse,
    ErrorResponse,
    EventResponse,
    EvidenceCommand,
    EvidenceResponse,
    LeaseResponse,
    LifecycleCommand,
    ReadinessResponse,
    RenewCommand,
    RetryCommand,
    RevisionRegistration,
    RevisionResponse,
    TransitionResponse,
    UnitRegistration,
    UnitResponse,
)
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import WorkUnitState
from orchestrator.services.claims import authorize_retry, claim_unit, renew_claim
from orchestrator.services.evidence import append_evidence, list_evidence, record_adjudication
from orchestrator.services.lifecycle import (
    ActorContext,
    TransitionCommand,
    transition_unit,
    unit_history,
)
from orchestrator.services.packages import (
    DependencySpec,
    evaluate_readiness,
    record_approval,
    register_approved_unit,
    register_dependency_command,
    register_revision,
    resolve_dependency_command,
)

SessionDep = Annotated[Session, Depends(get_session)]
ActorDep = Annotated[ActorContext, Depends(get_actor)]

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required or rejected"},
    403: {"model": ErrorResponse, "description": "Authenticated actor is forbidden"},
    409: {"model": ErrorResponse, "description": "Domain conflict"},
}

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
    "complete": WorkUnitState.COMPLETED,
    "fail": WorkUnitState.FAILED,
    "retry": WorkUnitState.READY,
    "cancel": WorkUnitState.CANCELLED,
}


def _raise_error(value: object) -> object:
    if isinstance(value, DomainError):
        raise value
    return value


@router.post("/revisions", response_model=RevisionResponse, status_code=201)
def create_revision(
    body: RevisionRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    revision = register_revision(
        session,
        **body.model_dump(exclude={"authority"}),
        authority=normalize_authority(body.authority),
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    session.commit()
    return {"id": revision.id, "revision": revision.revision}


@router.post(
    "/revisions/{revision_id}/work-units",
    response_model=UnitResponse,
    status_code=201,
)
def create_unit(
    revision_id: UUID,
    body: UnitRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    unit = register_approved_unit(
        session,
        revision_id=revision_id,
        **body.model_dump(exclude={"authority"}),
        authority=normalize_authority(body.authority),
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    session.commit()
    return {"id": unit.id, "state": unit.state, "version": unit.version}


@router.get("/work-units/{unit_id}/readiness", response_model=ReadinessResponse)
def readiness(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    result = evaluate_readiness(session, unit_id)
    return {
        "status": result.status,
        "reasons": [
            {"code": reason.code, "subject_id": reason.subject_id, "detail": reason.detail}
            for reason in result.reasons
        ],
    }


@router.post("/work-units/{unit_id}/claim", response_model=LeaseResponse)
def claim(
    unit_id: UUID,
    body: ClaimCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        claim_unit(
            session,
            unit_id,
            actor,
            body.idempotency_key,
            expected_version=body.expected_version,
        )
    )


@router.post("/work-units/{unit_id}/renew", response_model=LeaseResponse)
def renew(
    unit_id: UUID,
    body: RenewCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
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
            unit_id,
            target,
            actor,
            body.expected_version,
            body.idempotency_key,
            body.attempt,
            body.lease_token,
        ),
    )


@router.post("/work-units/{unit_id}/approvals", response_model=ApprovalResponse)
def approval(
    unit_id: UUID,
    body: ApprovalCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_approval(
        session,
        unit_id=unit_id,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        **body.model_dump(),
    )
    session.commit()
    return result


@router.post("/work-units/{unit_id}/adjudications", response_model=AdjudicationResponse)
def adjudication(
    unit_id: UUID,
    body: AdjudicationCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        record_adjudication(
            session,
            work_unit_id=unit_id,
            actor=actor,
            **body.model_dump(),
        )
    )


@router.post("/work-units/{unit_id}/retry-authorization", response_model=ApprovalResponse)
def retry_authorization(
    unit_id: UUID,
    body: RetryCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(authorize_retry(session, unit_id, actor, **body.model_dump()))


@router.post("/work-units/{unit_id}/dependencies", response_model=DependencyResponse)
def dependency(
    unit_id: UUID,
    body: DependencyCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    values = body.model_dump(exclude={"idempotency_key", "expected_version"})
    result = register_dependency_command(
        session,
        work_unit_id=unit_id,
        spec=DependencySpec(**values),
        actor_id=actor.actor_id,
        actor_role=actor.role,
        expected_version=body.expected_version,
        idempotency_key=body.idempotency_key,
    )
    session.commit()
    return result


@router.post("/dependencies/{dependency_id}/resolve", response_model=DependencyResponse)
def dependency_resolution(
    dependency_id: UUID,
    body: DependencyResolutionCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = resolve_dependency_command(
        session,
        dependency_id=dependency_id,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        **body.model_dump(),
    )
    session.commit()
    return result


@router.post("/work-units/{unit_id}/evidence", response_model=EvidenceResponse)
def evidence(
    unit_id: UUID,
    body: EvidenceCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        append_evidence(
            session,
            work_unit_id=unit_id,
            actor=actor,
            **body.model_dump(),
        )
    )


@router.get("/work-units/{unit_id}/evidence", response_model=list[EvidenceResponse])
def evidence_list(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return list_evidence(session, unit_id)


@router.get("/work-units/{unit_id}/history", response_model=list[EventResponse])
def history(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return unit_history(session, unit_id)
