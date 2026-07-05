import secrets
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Claim,
    Dependency,
    Event,
    Evidence,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.claims import authorize_retry
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import evaluate_readiness, record_approval

router = APIRouter(prefix="/review", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
SessionDep = Annotated[Session, Depends(get_session)]
ActorDep = Annotated[ActorContext, Depends(get_actor)]
CSRF_COOKIE = "orchestrator_csrf"


def _human(actor: ActorContext) -> None:
    if actor.role is not ActorRole.HUMAN:
        raise DomainError("human_actor_required", "human review requires a human actor", None)


def _csrf(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)


def _render(request: Request, template: str, context: dict[str, Any]) -> HTMLResponse:
    token = _csrf(request)
    response = templates.TemplateResponse(
        request=request,
        name=template,
        context={**context, "csrf_token": token},
    )
    response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", secure=False)
    return response


def _require_form(request: Request, csrf_token: str, confirm: str | None) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    if cookie is None or not secrets.compare_digest(cookie, csrf_token) or confirm != "yes":
        raise DomainError("csrf_rejected", "valid CSRF and explicit confirmation required", None)


@router.get("", response_class=HTMLResponse)
def queue(request: Request, actor: ActorDep, session: SessionDep) -> HTMLResponse:
    _human(actor)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    units = tuple(session.scalars(select(WorkUnit).order_by(WorkUnit.state, WorkUnit.created_at)))
    for unit in units:
        readiness = evaluate_readiness(session, unit.id)
        grouped[unit.state].append(
            {
                "unit": unit,
                "readiness": readiness.status,
                "reasons": readiness.reasons,
            }
        )
    return _render(request, "queue.html", {"groups": dict(grouped)})


def _projection(session: Session, unit_id: uuid.UUID) -> dict[str, Any]:
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    return {
        "unit": unit,
        "revision": revision,
        "dependencies": tuple(
            session.scalars(select(Dependency).where(Dependency.work_unit_id == unit.id))
        ),
        "claims": tuple(
            session.scalars(
                select(Claim).where(Claim.work_unit_id == unit.id).order_by(Claim.attempt.desc())
            )
        ),
        "evidence": tuple(
            session.scalars(
                select(Evidence)
                .where(Evidence.work_unit_id == unit.id)
                .order_by(Evidence.recorded_at)
            )
        ),
        "adjudications": tuple(
            session.scalars(
                select(Adjudication)
                .where(Adjudication.work_unit_id == unit.id)
                .order_by(Adjudication.decided_at)
            )
        ),
        "approvals": tuple(
            session.scalars(
                select(Approval).where(Approval.subject_id == unit.id).order_by(Approval.created_at)
            )
        ),
        "events": tuple(
            session.scalars(
                select(Event)
                .where(Event.subject_id == unit.id)
                .order_by(Event.occurred_at, Event.id)
            )
        ),
    }


@router.get("/units/{unit_id}", response_class=HTMLResponse)
def detail(
    request: Request, unit_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> HTMLResponse:
    _human(actor)
    return _render(request, "unit.html", _projection(session, unit_id))


@router.get("/units/{unit_id}/evidence-pack", response_class=HTMLResponse)
def evidence_pack(
    request: Request, unit_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> HTMLResponse:
    _human(actor)
    return _render(request, "evidence_pack.html", _projection(session, unit_id))


def _redirect(unit_id: uuid.UUID) -> RedirectResponse:
    return RedirectResponse(f"/review/units/{unit_id}", status_code=303)


@router.post("/units/{unit_id}/approval")
def approve(
    request: Request,
    unit_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    expected_version: Annotated[int, Form()],
    reason: Annotated[str, Form(min_length=1)],
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, csrf_token, confirm)
    record_approval(
        session,
        unit_id=unit_id,
        subject_type="action",
        actor_id=actor.actor_id,
        actor_role=actor.role,
        reason=reason,
        idempotency_key=str(uuid.uuid4()),
        expected_version=expected_version,
    )
    session.commit()
    return _redirect(unit_id)


def _human_transition(
    session: Session,
    unit_id: uuid.UUID,
    actor: ActorContext,
    target: WorkUnitState,
    expected_version: int,
) -> None:
    transition_unit(
        session,
        TransitionCommand(unit_id, target, actor, expected_version, str(uuid.uuid4())),
    )


@router.post("/units/{unit_id}/review")
def review(
    request: Request,
    unit_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    expected_version: Annotated[int, Form()],
    outcome: Annotated[str, Form()],
    reason: Annotated[str, Form(min_length=1)],
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    del reason
    _human(actor)
    _require_form(request, csrf_token, confirm)
    targets = {
        "completed": WorkUnitState.COMPLETED,
        "revision_required": WorkUnitState.REVISION_REQUIRED,
    }
    target = targets.get(outcome)
    if target is None:
        raise DomainError("review_outcome_invalid", "invalid review outcome", None)
    _human_transition(session, unit_id, actor, target, expected_version)
    return _redirect(unit_id)


@router.post("/units/{unit_id}/cancel")
def cancel(
    request: Request,
    unit_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    expected_version: Annotated[int, Form()],
    reason: Annotated[str, Form(min_length=1)],
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    del reason
    _human(actor)
    _require_form(request, csrf_token, confirm)
    _human_transition(session, unit_id, actor, WorkUnitState.CANCELLED, expected_version)
    return _redirect(unit_id)


@router.post("/units/{unit_id}/retry")
def retry(
    request: Request,
    unit_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    expected_version: Annotated[int, Form()],
    new_max_attempts: Annotated[int, Form(gt=0)],
    reason: Annotated[str, Form(min_length=1)],
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, csrf_token, confirm)
    result = authorize_retry(
        session,
        unit_id,
        actor,
        new_max_attempts=new_max_attempts,
        reason=reason,
        idempotency_key=str(uuid.uuid4()),
        expected_version=expected_version,
    )
    if isinstance(result, DomainError):
        raise result
    return _redirect(unit_id)
