import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_actor, get_session
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.runner_authority import dependency_update_authority_violation
from orchestrator.kernel.states import WAIVER_RISK_CLASSES, ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    DecompositionProposal,
    DecompositionProposalAcMapping,
    DecompositionProposalDependency,
    DecompositionProposalRetainedAc,
    DecompositionProposalUnit,
    Event,
    PackageAcceptanceCriterion,
    ReconciliationCondition,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.claims import authorize_retry
from orchestrator.services.decomposition import (
    approve_decomposition_proposal,
    reject_decomposition_proposal,
    require_decomposition_revision,
)
from orchestrator.services.evidence import record_adjudication
from orchestrator.services.evidence_pack import evidence_pack_projection
from orchestrator.services.lifecycle import (
    POST_DEPLOY_AC_IDS,
    ActorContext,
    TransitionCommand,
    transition_unit,
)
from orchestrator.services.packages import evaluate_readiness, record_approval
from orchestrator.services.reconciliation import (
    ResolutionCommand,
    open_conditions,
    record_resolution,
)
from orchestrator.services.release_evidence_pack import release_evidence_pack_response
from orchestrator.services.verifier_criteria import load_required_criteria
from orchestrator.services.verifier_evaluators import JUDGMENT_TYPES

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
    candidate = request.cookies.get(CSRF_COOKIE)
    session_id = ""
    if candidate is not None:
        try:
            raw, signature = candidate.rsplit(".", 1)
            expected = hmac.new(_csrf_secret(request), raw.encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature):
                session_id = candidate
        except ValueError:
            pass
    if not session_id:
        raw = secrets.token_urlsafe(32)
        signature = hmac.new(_csrf_secret(request), raw.encode(), hashlib.sha256).hexdigest()
        session_id = f"{raw}.{signature}"
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
    subject_id: uuid.UUID,
    action: str,
    idempotency_key: str,
) -> str:
    payload = {
        "action": action,
        "actor": actor.actor_id,
        "exp": int(time.time()) + CSRF_TTL_SECONDS,
        "idempotency_key": idempotency_key,
        "session": _session_id(request),
        "subject": str(subject_id),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    signature = hmac.new(_csrf_secret(request), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _require_form(
    request: Request,
    actor: ActorContext,
    subject_id: uuid.UUID,
    action: str,
    csrf_token: str,
    idempotency_key: str,
    confirm: str | None,
) -> None:
    try:
        encoded, signature = csrf_token.rsplit(".", 1)
        expected = hmac.new(_csrf_secret(request), encoded.encode(), hashlib.sha256).hexdigest()
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode())
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        payload, expected, signature = {}, "", ""
    valid = (
        hmac.compare_digest(expected, signature)
        and payload.get("actor") == actor.actor_id
        and payload.get("subject") == str(subject_id)
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


def _adjudicatable_criteria(
    session: Session, unit: WorkUnit, revision: WorkPackageRevision
) -> tuple[dict[str, Any], ...]:
    try:
        criteria = load_required_criteria(session, unit, revision)
    except DomainError:
        return ()
    return tuple(
        {
            "ac_id": criterion.ac_id,
            "evidence_type": criterion.evidence_type,
            "is_judgment": criterion.evidence_type.strip().lower() in JUDGMENT_TYPES,
        }
        for criterion in criteria
        if criterion.ac_id not in POST_DEPLOY_AC_IDS
    )


def _package_intake_projection(session: Session, revision_id: uuid.UUID) -> dict[str, Any]:
    revision = session.get(WorkPackageRevision, revision_id)
    if revision is None or revision.intake_source != "package_cli":
        raise DomainError(
            "package_intake_not_found",
            "package intake does not exist for this revision",
            None,
        )
    acceptance_criteria = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.work_package_revision_id == revision.id)
            .order_by(PackageAcceptanceCriterion.ac_id, PackageAcceptanceCriterion.id)
        )
    )
    intake_event = session.scalar(
        select(Event)
        .where(
            Event.subject_type == "work_package_revision",
            Event.subject_id == revision.id,
            Event.action == "package_revision.intake_registered",
        )
        .order_by(Event.occurred_at, Event.id)
    )
    command = intake_event.payload.get("command", {}) if intake_event is not None else {}
    return {
        "revision": revision,
        "package": revision.work_package,
        "acceptance_criteria": acceptance_criteria,
        "authority": command.get("authority"),
    }


def _decomposition_proposal_projection(session: Session, proposal_id: uuid.UUID) -> dict[str, Any]:
    proposal = session.get(DecompositionProposal, proposal_id)
    if proposal is None:
        raise DomainError(
            "decomposition_proposal_not_found",
            "decomposition proposal does not exist",
            None,
        )
    revision = session.get(WorkPackageRevision, proposal.work_package_revision_id)
    assert revision is not None
    proposal_units = tuple(
        session.scalars(
            select(DecompositionProposalUnit)
            .where(DecompositionProposalUnit.proposal_id == proposal.id)
            .order_by(DecompositionProposalUnit.unit_key)
        )
    )
    units = tuple(
        {
            "unit_key": unit.unit_key,
            "title": unit.title,
            "outcome": unit.outcome,
            "required_capability": unit.required_capability,
            "max_attempts": unit.max_attempts,
            "authority_fingerprint": unit.authority_fingerprint,
            "authority": normalize_authority(unit.authority).normalized(),
        }
        for unit in proposal_units
    )
    dependencies = tuple(
        session.scalars(
            select(DecompositionProposalDependency)
            .where(DecompositionProposalDependency.proposal_id == proposal.id)
            .order_by(
                DecompositionProposalDependency.source_unit_key,
                DecompositionProposalDependency.target_unit_key,
                DecompositionProposalDependency.external_ref,
            )
        )
    )
    criteria = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.work_package_revision_id == revision.id)
            .order_by(PackageAcceptanceCriterion.ac_id, PackageAcceptanceCriterion.id)
        )
    )
    criteria_by_id = {criterion.id: criterion for criterion in criteria}
    mappings = tuple(
        session.scalars(
            select(DecompositionProposalAcMapping)
            .where(DecompositionProposalAcMapping.proposal_id == proposal.id)
            .order_by(
                DecompositionProposalAcMapping.unit_key,
                DecompositionProposalAcMapping.package_acceptance_criterion_id,
            )
        )
    )
    retained_acs = tuple(
        session.scalars(
            select(DecompositionProposalRetainedAc)
            .where(DecompositionProposalRetainedAc.proposal_id == proposal.id)
            .order_by(DecompositionProposalRetainedAc.package_acceptance_criterion_id)
        )
    )
    mapped_by_criterion = {mapping.package_acceptance_criterion_id: mapping for mapping in mappings}
    retained_by_criterion = {
        retained.package_acceptance_criterion_id: retained for retained in retained_acs
    }
    # Sum the proposed units' authority-envelope call ceilings, so a human approving the
    # split sees the projected total budget alongside the split itself, not just per unit.
    ceilings = [
        normalize_authority(unit.authority).budgets.max_llm_calls for unit in proposal_units
    ]
    projected_llm_calls = sum(c for c in ceilings if c is not None)
    units_without_ceiling = sum(1 for c in ceilings if c is None)
    created_work_unit_ids = (
        proposal.created_work_unit_ids if isinstance(proposal.created_work_unit_ids, dict) else {}
    )
    ac_coverage: list[dict[str, Any]] = []
    for criterion in criteria:
        mapping = mapped_by_criterion.get(criterion.id)
        retained = retained_by_criterion.get(criterion.id)
        if mapping is not None:
            ac_coverage.append(
                {
                    "criterion": criterion,
                    "disposition": "mapped_to_unit",
                    "unit_key": mapping.unit_key,
                    "rationale": None,
                }
            )
        elif retained is not None:
            ac_coverage.append(
                {
                    "criterion": criterion,
                    "disposition": "retained_package_level",
                    "unit_key": None,
                    "rationale": retained.rationale,
                }
            )
    return {
        "proposal": proposal,
        "revision": revision,
        "package": revision.work_package,
        "units": units,
        "dependencies": dependencies,
        "projected_llm_calls": projected_llm_calls,
        "units_without_ceiling": units_without_ceiling,
        "ac_coverage": ac_coverage,
        "retained_acs": tuple(
            {
                "criterion": criteria_by_id[retained.package_acceptance_criterion_id],
                "rationale": retained.rationale,
            }
            for retained in retained_acs
        ),
        "created_work_unit_ids": tuple(sorted(created_work_unit_ids.items())),
    }


@router.get("/units/{unit_id}", response_class=HTMLResponse)
def detail(
    request: Request, unit_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> HTMLResponse:
    _human(actor)
    context = evidence_pack_projection(session, unit_id)
    actions = ["approval", "review", "cancel", "retry"]
    if context["authority_violation"] is None:
        actions.append("authority_approval")
    keys = {action: str(uuid.uuid4()) for action in actions}
    context["idempotency_keys"] = keys
    context["csrf_tokens"] = {
        action: _issue_token(request, actor, unit_id, action, key) for action, key in keys.items()
    }
    # Open reconciliation conditions, each with its own CSRF token bound to that condition --
    # a recovery surface an operator cannot actually act on is not a recovery surface.
    conditions = open_conditions(session, unit_id)
    condition_keys = {str(row.id): str(uuid.uuid4()) for row in conditions}
    context["open_conditions"] = tuple(
        {
            "id": row.id,
            "condition_type": row.condition_type,
            "observation_kind": row.observation_kind,
            "detail": row.detail,
            "detected_at": row.detected_at,
            "stored_state": row.stored_state,
            "observed_state": row.observed_state,
        }
        for row in conditions
    )
    context["condition_idempotency_keys"] = condition_keys
    context["condition_csrf_tokens"] = {
        str(row.id): _issue_token(request, actor, row.id, "resolve", condition_keys[str(row.id)])
        for row in conditions
    }
    criteria = _adjudicatable_criteria(session, context["unit"], context["revision"])
    adjudication_keys = {row["ac_id"]: str(uuid.uuid4()) for row in criteria}
    context["adjudicatable_criteria"] = criteria
    context["adjudication_idempotency_keys"] = adjudication_keys
    context["adjudication_csrf_tokens"] = {
        row["ac_id"]: _issue_token(
            request, actor, unit_id, "adjudication", adjudication_keys[row["ac_id"]]
        )
        for row in criteria
    }
    context["waiver_risk_classes"] = WAIVER_RISK_CLASSES
    return _render(request, "unit.html", context)


@router.get("/intakes/{revision_id}", response_class=HTMLResponse)
def intake_detail(
    request: Request, revision_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> HTMLResponse:
    _human(actor)
    return _render(request, "intake.html", _package_intake_projection(session, revision_id))


@router.get("/decomposition-proposals/{proposal_id}", response_class=HTMLResponse)
def decomposition_proposal_detail(
    request: Request, proposal_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> HTMLResponse:
    _human(actor)
    context = _decomposition_proposal_projection(session, proposal_id)
    if context["proposal"].state == "proposed":
        keys = {action: str(uuid.uuid4()) for action in ("approve", "reject", "require_revision")}
        context["idempotency_keys"] = keys
        context["csrf_tokens"] = {
            action: _issue_token(request, actor, proposal_id, action, key)
            for action, key in keys.items()
        }
    return _render(request, "decomposition_proposal.html", context)


@router.get("/units/{unit_id}/evidence-pack", response_class=HTMLResponse)
def evidence_pack(
    request: Request, unit_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> HTMLResponse:
    _human(actor)
    return _render(request, "evidence_pack.html", evidence_pack_projection(session, unit_id))


@router.get("/revisions/{revision_id}/evidence-pack", response_class=HTMLResponse)
def release_evidence_pack(
    request: Request, revision_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> HTMLResponse:
    _human(actor)
    return _render(
        request,
        "release_evidence_pack.html",
        {"pack": release_evidence_pack_response(session, revision_id)},
    )


def _redirect(unit_id: uuid.UUID) -> RedirectResponse:
    return RedirectResponse(f"/review/units/{unit_id}", status_code=303)


def _proposal_redirect(proposal_id: uuid.UUID) -> RedirectResponse:
    return RedirectResponse(f"/review/decomposition-proposals/{proposal_id}", status_code=303)


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


@router.post("/units/{unit_id}/authority-approval")
def approve_authority(
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
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    violation = dependency_update_authority_violation(normalize_authority(unit.authority))
    if violation is not None:
        raise DomainError(violation.code, violation.message, violation.remediation)
    _require_form(
        request,
        actor,
        unit_id,
        "authority_approval",
        csrf_token,
        idempotency_key,
        confirm,
    )
    record_approval(
        session,
        unit_id=unit_id,
        subject_type="authority",
        actor_id=actor.actor_id,
        actor_role=actor.role,
        reason=reason,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )
    session.commit()
    return _redirect(unit_id)


def _blank_to_none(value: str | None) -> str | None:
    """A browser submits an unset optional field as the empty string, not absence.

    `record_adjudication`'s risk-class check (`risk is not None and risk not in
    WAIVER_RISK_CLASSES`) treats "" as a bogus risk class rather than "no risk given" -- so every
    optional adjudication field must be normalized to None before it reaches the service.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_optional_uuid(value: str | None) -> uuid.UUID | None:
    stripped = _blank_to_none(value)
    if stripped is None:
        return None
    try:
        return uuid.UUID(stripped)
    except ValueError as error:
        raise DomainError(
            "adjudication_invalid", "failed_evidence_id must be a valid UUID", None
        ) from error


def _parse_optional_datetime(value: str | None) -> datetime | None:
    stripped = _blank_to_none(value)
    if stripped is None:
        return None
    try:
        result = datetime.fromisoformat(stripped)
    except ValueError as error:
        raise DomainError(
            "adjudication_invalid", "expires_at must be an ISO 8601 timestamp", None
        ) from error
    if result.tzinfo is None:
        raise DomainError(
            "adjudication_invalid",
            "expires_at must include a timezone offset (e.g. 2027-06-01T00:00:00Z)",
            None,
        )
    return result


@router.post("/units/{unit_id}/adjudication")
def adjudicate(
    request: Request,
    unit_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    expected_version: Annotated[int, Form()],
    ac_id: Annotated[str, Form(min_length=1)],
    outcome: Annotated[str, Form()],
    rationale: Annotated[str, Form(min_length=1)],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
    failed_evidence_id: Annotated[str | None, Form()] = None,
    risk: Annotated[str | None, Form()] = None,
    follow_up: Annotated[str | None, Form()] = None,
    expires_at: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, unit_id, "adjudication", csrf_token, idempotency_key, confirm)
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    result = record_adjudication(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit_id,
        ac_id=ac_id,
        outcome=outcome,
        actor=actor,
        rationale=rationale,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        failed_evidence_id=_parse_optional_uuid(failed_evidence_id),
        risk=_blank_to_none(risk),
        follow_up=_blank_to_none(follow_up),
        expires_at=_parse_optional_datetime(expires_at),
    )
    if isinstance(result, DomainError):
        raise result
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


@router.post("/reconciliation/conditions/{condition_id}/resolution")
def resolve_reconciliation_condition(
    request: Request,
    condition_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    decision: Annotated[str, Form(min_length=1)],
    rationale: Annotated[str, Form(min_length=1)],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Close a reconciliation condition. HUMAN-only, and deliberately on /review.

    Detection never auto-resolves -- a resolution is an operator decision. It lives here rather
    than on /api because production strips the Authentik headers from /api, so a human actor
    cannot reach an /api route at all.
    """
    _human(actor)
    _require_form(request, actor, condition_id, "resolve", csrf_token, idempotency_key, confirm)
    condition = session.get(ReconciliationCondition, condition_id)
    if condition is None:
        raise DomainError("condition_not_found", "reconciliation condition does not exist", None)
    unit_id = condition.work_unit_id
    result = record_resolution(
        session,
        ResolutionCommand(
            actor=actor,
            condition_id=condition_id,
            decision=decision,
            rationale=rationale,
            idempotency_key=idempotency_key,
        ),
    )
    if isinstance(result, DomainError):
        raise result
    return _redirect(unit_id)


@router.post("/decomposition-proposals/{proposal_id}/approve")
def approve_decomposition_route(
    request: Request,
    proposal_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    reason: Annotated[str, Form(min_length=1)],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, proposal_id, "approve", csrf_token, idempotency_key, confirm)
    approve_decomposition_proposal(
        session,
        proposal_id,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    session.commit()
    return _proposal_redirect(proposal_id)


@router.post("/decomposition-proposals/{proposal_id}/reject")
def reject_decomposition_route(
    request: Request,
    proposal_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    reason: Annotated[str, Form(min_length=1)],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, proposal_id, "reject", csrf_token, idempotency_key, confirm)
    reject_decomposition_proposal(
        session,
        proposal_id,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    session.commit()
    return _proposal_redirect(proposal_id)


@router.post("/decomposition-proposals/{proposal_id}/require-revision")
def require_decomposition_revision_route(
    request: Request,
    proposal_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    reason: Annotated[str, Form(min_length=1)],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(
        request,
        actor,
        proposal_id,
        "require_revision",
        csrf_token,
        idempotency_key,
        confirm,
    )
    require_decomposition_revision(
        session,
        proposal_id,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    session.commit()
    return _proposal_redirect(proposal_id)
