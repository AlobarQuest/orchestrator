import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_actor, get_session
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
CSRF_COOKIE = "orchestrator_review_session"
CSRF_TTL_SECONDS = 900


def _human(actor: ActorContext) -> None:
    if actor.role is not ActorRole.HUMAN:
        raise DomainError("human_actor_required", "human review requires a human actor", None)


def _session_id(request: Request) -> str:
    existing = getattr(request.state, "review_session_id", None)
    if isinstance(existing, str):
        return existing
    session_id = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    request.state.review_session_id = session_id
    return session_id


def _render(request: Request, template: str, context: dict[str, Any]) -> HTMLResponse:
    session_id = _session_id(request)
    response = templates.TemplateResponse(
        request=request,
        name=template,
        context=context,
    )
    response.set_cookie(CSRF_COOKIE, session_id, httponly=True, samesite="strict", secure=True)
    return response


def _csrf_secret(request: Request) -> bytes:
    config = getattr(request.app.state, "auth_config", None)
    if not isinstance(config, AuthConfig) or config.csrf_secret is None:
        raise DomainError("csrf_unavailable", "CSRF signing is not configured", None)
    return config.csrf_secret


def _issue_token(
    request: Request,
    actor: ActorContext,
    unit_id: uuid.UUID,
    action: str,
    idempotency_key: str,
) -> str:
    payload = {
        "action": action,
        "actor": actor.actor_id,
        "exp": int(time.time()) + CSRF_TTL_SECONDS,
        "idempotency_key": idempotency_key,
        "session": _session_id(request),
        "unit": str(unit_id),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    signature = hmac.new(_csrf_secret(request), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _require_form(
    request: Request,
    actor: ActorContext,
    unit_id: uuid.UUID,
    action: str,
    csrf_token: str,
    idempotency_key: str,
    confirm: str | None,
) -> None:
    try:
        encoded, signature = csrf_token.rsplit(".", 1)
        expected = hmac.new(_csrf_secret(request), encoded.encode(), hashlib.sha256).hexdigest()
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode())
    except (ValueError, TypeError, json.JSONDecodeError):
        payload, expected, signature = {}, "", ""
    valid = (
        hmac.compare_digest(expected, signature)
        and payload.get("actor") == actor.actor_id
        and payload.get("unit") == str(unit_id)
        and payload.get("action") == action
        and payload.get("session") == request.cookies.get(CSRF_COOKIE)
        and payload.get("idempotency_key") == idempotency_key
        and isinstance(payload.get("exp"), int)
        and payload["exp"] >= int(time.time())
        and confirm == "yes"
    )
    if not valid:
        raise DomainError("csrf_rejected", "valid CSRF and explicit confirmation required", None)


@router.get("", response_class=HTMLResponse)
def queue(request: Request, actor: ActorDep, session: SessionDep) -> HTMLResponse:
    _human(actor)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    units = tuple(session.scalars(select(WorkUnit).order_by(WorkUnit.state, WorkUnit.created_at)))
    for unit in units:
        readiness = evaluate_readiness(session, unit.id, for_update=False)
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
    evidence = tuple(
        session.scalars(
            select(Evidence).where(Evidence.work_unit_id == unit.id).order_by(Evidence.recorded_at)
        )
    )
    adjudications = tuple(
        session.scalars(
            select(Adjudication)
            .where(Adjudication.work_unit_id == unit.id)
            .order_by(Adjudication.decided_at)
        )
    )
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
        "evidence": evidence,
        "current_evidence_ids": {row.id for row in evidence}
        - {row.supersedes_evidence_id for row in evidence if row.supersedes_evidence_id},
        "adjudications": adjudications,
        "current_adjudication_ids": {row.id for row in adjudications}
        - {
            row.supersedes_adjudication_id
            for row in adjudications
            if row.supersedes_adjudication_id
        },
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
    context = _projection(session, unit_id)
    keys = {action: str(uuid.uuid4()) for action in ("approval", "review", "cancel", "retry")}
    context["idempotency_keys"] = keys
    context["csrf_tokens"] = {
        action: _issue_token(request, actor, unit_id, action, key) for action, key in keys.items()
    }
    return _render(request, "unit.html", context)


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
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, unit_id, "approval", csrf_token, idempotency_key, confirm)
    record_approval(
        session,
        unit_id=unit_id,
        subject_type="action",
        actor_id=actor.actor_id,
        actor_role=actor.role,
        reason=reason,
        idempotency_key=idempotency_key,
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
    idempotency_key: str,
    reason: str,
) -> None:
    transition_unit(
        session,
        TransitionCommand(unit_id, target, actor, expected_version, idempotency_key, reason=reason),
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
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, unit_id, "review", csrf_token, idempotency_key, confirm)
    targets = {
        "completed": WorkUnitState.COMPLETED,
        "revision_required": WorkUnitState.REVISION_REQUIRED,
    }
    target = targets.get(outcome)
    if target is None:
        raise DomainError("review_outcome_invalid", "invalid review outcome", None)
    _human_transition(session, unit_id, actor, target, expected_version, idempotency_key, reason)
    return _redirect(unit_id)


@router.post("/units/{unit_id}/cancel")
def cancel(
    request: Request,
    unit_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    expected_version: Annotated[int, Form()],
    reason: Annotated[str, Form(min_length=1)],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, unit_id, "cancel", csrf_token, idempotency_key, confirm)
    _human_transition(
        session,
        unit_id,
        actor,
        WorkUnitState.CANCELLED,
        expected_version,
        idempotency_key,
        reason,
    )
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
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, unit_id, "retry", csrf_token, idempotency_key, confirm)
    result = authorize_retry(
        session,
        unit_id,
        actor,
        new_max_attempts=new_max_attempts,
        reason=reason,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )
    if isinstance(result, DomainError):
        raise result
    return _redirect(unit_id)
