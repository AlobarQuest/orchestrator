from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.api.schemas import (
    ClaimCommand,
    CommandBase,
    EvidenceCommand,
    RenewCommand,
    RevisionRegistration,
    UnitRegistration,
)
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import WorkUnitState
from orchestrator.services.claims import claim_unit, renew_claim
from orchestrator.services.evidence import append_evidence, list_evidence
from orchestrator.services.lifecycle import (
    ActorContext,
    TransitionCommand,
    transition_unit,
    unit_history,
)
from orchestrator.services.packages import (
    evaluate_readiness,
    register_approved_unit,
    register_revision,
)

SessionDep = Annotated[Session, Depends(get_session)]
ActorDep = Annotated[ActorContext, Depends(get_actor)]

router = APIRouter(prefix="/api/v1")

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


@router.post("/revisions")
def create_revision(
    body: RevisionRegistration,
    session: SessionDep,
    actor: ActorDep,
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


@router.post("/revisions/{revision_id}/work-units")
def create_unit(
    revision_id: UUID,
    body: UnitRegistration,
    session: SessionDep,
    actor: ActorDep,
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


@router.get("/work-units/{unit_id}/readiness")
def readiness(
    unit_id: UUID,
    session: SessionDep,
    _actor: ActorDep,
) -> dict[str, object]:
    result = evaluate_readiness(session, unit_id)
    return {
        "status": result.status,
        "reasons": [
            {"code": reason.code, "subject_id": reason.subject_id, "detail": reason.detail}
            for reason in result.reasons
        ],
    }


@router.post("/work-units/{unit_id}/claim")
def claim(
    unit_id: UUID,
    body: ClaimCommand,
    session: SessionDep,
    actor: ActorDep,
) -> object:
    return _raise_error(claim_unit(session, unit_id, actor, body.idempotency_key))


@router.post("/work-units/{unit_id}/renew")
def renew(
    unit_id: UUID,
    body: RenewCommand,
    session: SessionDep,
    actor: ActorDep,
) -> object:
    return _raise_error(renew_claim(session, unit_id, actor, body.attempt, body.lease_token))


@router.post("/work-units/{unit_id}/commands/{command}")
def command(
    unit_id: UUID,
    command: str,
    body: CommandBase,
    session: SessionDep,
    actor: ActorDep,
) -> object:
    target = COMMAND_TARGETS.get(command)
    if target is None:
        raise DomainError("command_not_found", "unknown lifecycle command", None)
    return transition_unit(
        session,
        TransitionCommand(unit_id, target, actor, body.expected_version, body.idempotency_key),
    )


@router.post("/work-units/{unit_id}/evidence")
def evidence(
    unit_id: UUID,
    body: EvidenceCommand,
    session: SessionDep,
    actor: ActorDep,
) -> object:
    return _raise_error(
        append_evidence(
            session,
            work_unit_id=unit_id,
            actor=actor,
            **body.model_dump(),
        )
    )


@router.get("/work-units/{unit_id}/evidence")
def evidence_list(
    unit_id: UUID,
    session: SessionDep,
    _actor: ActorDep,
) -> object:
    return list_evidence(session, unit_id)


@router.get("/work-units/{unit_id}/history")
def history(
    unit_id: UUID,
    session: SessionDep,
    _actor: ActorDep,
) -> object:
    return unit_history(session, unit_id)
