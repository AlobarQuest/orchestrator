import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_actor, get_session
from orchestrator.api.routes import SettingsDep, package_intake_command
from orchestrator.api.schemas import PackageIntakeRegistration
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.runner_authority import runner_command_authority_violation
from orchestrator.kernel.states import WAIVER_RISK_CLASSES, ActorRole, WorkUnitState
from orchestrator.kernel.transitions import TransitionGuards, authorize_transition
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
from orchestrator.services.decision_facts import (
    decision_facts_for_revision,
    decision_facts_for_unit,
)
from orchestrator.services.decomposition import (
    approve_decomposition_proposal,
    reject_decomposition_proposal,
    require_decomposition_revision,
)
from orchestrator.services.evidence import (
    AdjudicationDecision,
    record_adjudications,
)
from orchestrator.services.evidence_pack import evidence_pack_projection
from orchestrator.services.graduation_ledger import graduation_ledger
from orchestrator.services.lifecycle import (
    ActorContext,
    TransitionCommand,
    transition_unit,
)
from orchestrator.services.package_intake import register_package_intake
from orchestrator.services.packages import record_approval
from orchestrator.services.pending_decisions import (
    SETTLED_STATES,
    adjudicable_criteria,
    grouped_pending_decisions,
)
from orchestrator.services.reconciliation import (
    ResolutionCommand,
    open_conditions,
    record_resolution,
)
from orchestrator.services.release_evidence_pack import release_evidence_pack_response

router = APIRouter(prefix="/review", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
SessionDep = Annotated[Session, Depends(get_session)]
ActorDep = Annotated[ActorContext, Depends(get_actor)]
CSRF_COOKIE = "orchestrator_review_session"
CSRF_TTL_SECONDS = 900
# Every other form binds its CSRF token to the UUID of the thing being acted on. A new intake has
# no subject yet -- the revision it creates does not exist until the POST succeeds -- so the token
# binds to this sentinel instead. It still binds actor, session, action and idempotency key, which
# is what stops a token minted for one form being replayed at another.
_NEW_INTAKE_SUBJECT = uuid.UUID(int=0)


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
def queue(
    request: Request, actor: ActorDep, session: SessionDep, settings: SettingsDep
) -> HTMLResponse:
    """Every decision waiting on a person, grouped by what the decision IS.

    Grouping by lifecycle state meant you had to know which states imply a gate before you could
    find your own work -- and five of the eight kinds are not work-unit states at all.

    This is also where the WS-P2.19 stall report is read, and it is the whole of its wiring. A
    stalled unit is a decision waiting on a person, so it belongs on the list a person already
    opens rather than behind an operator query nobody runs; and being derived on read, it appears
    and disappears with the fact itself, with no pass to schedule and nothing to switch off.
    """
    _human(actor)
    return _render(
        request,
        "queue.html",
        {
            "groups": grouped_pending_decisions(
                session,
                execution_stall_grace_seconds=settings.execution_stall_grace_seconds,
            )
        },
    )


def _adjudicatable_criteria(
    session: Session, unit: WorkUnit, revision: WorkPackageRevision
) -> tuple[dict[str, Any], ...]:
    # The rule for WHICH criteria a person may be shown, and which of those they may decide, is
    # shared with the queue: `human_may_adjudicate` is the same predicate the service authorizes
    # on, so the form cannot offer an outcome that would be refused -- nor withhold one that
    # would be accepted. Two copies of that rule would diverge silently.
    #
    # Each criterion carries its own current evidence so the template can render it INSIDE that
    # criterion's fieldset. The page's evidence section is the full, supersession-aware audit
    # view; this is the one row the decision is actually about, next to the control that decides
    # it. It is the same row the predicate above was computed from, not a second lookup.
    #
    # `condition` is the standard the criterion states and `expected_evidence` is the artifact its
    # author asked for. The judgment being made is "does what arrived satisfy what was required",
    # so all three belong in one place. Until now the standard appeared only on the queue: a
    # reviewer read it, clicked through, and decided without it -- WS-P2.17's founding complaint,
    # one level up. `approver` is deliberately NOT carried: it is an identity, it decides nothing
    # about whether the evidence is sufficient, and the person reading this page is the approver.
    return tuple(
        {
            "ac_id": criterion.ac_id,
            "evidence_type": criterion.evidence_type,
            "condition": criterion.condition,
            "expected_evidence": criterion.evidence,
            "human_may_decide": may_decide,
            "evidence": evidence,
        }
        for criterion, may_decide, evidence in adjudicable_criteria(session, unit, revision)
    )


# The outcomes the review form offers, each with the state it moves the unit to. One definition,
# read by the form (which renders the options) and by the route (which resolves the submitted
# value): two copies would let the form offer an outcome the route cannot name.
REVIEW_OUTCOMES: tuple[tuple[str, str, WorkUnitState], ...] = (
    ("completed", "Complete", WorkUnitState.COMPLETED),
    ("revision_required", "Request revision", WorkUnitState.REVISION_REQUIRED),
)
# The page models EDGE LEGALITY -- which is state-keyed and free -- and never guard satisfaction,
# which costs queries. So it asks the kernel with every guard already met: "if the paperwork were
# in order, could a person make this move at all?"
_GUARDS_MET = TransitionGuards(True, True, True)


def _a_human_could_move(state: WorkUnitState, target: WorkUnitState) -> bool:
    try:
        authorize_transition(state, target, ActorRole.HUMAN, _GUARDS_MET)
    except DomainError:
        return False
    return True


def _available_actions(unit: WorkUnit, authority_violation: object | None) -> dict[str, Any]:
    """Which action forms this unit's page may render, each from the rule its service enforces.

    Increment 2 established that the adjudication dropdown must offer exactly what the service
    accepts; this is the same principle over the rest of the page. A form the service would refuse
    wastes a click and reads as a broken control -- a cancelled unit offered five of them. A form
    the service WOULD accept but the page hides is worse, because it removes the operator's only
    route, so every condition below is a precondition somebody else already enforces:

    * **approval** -- an action approval satisfies exactly one guard, on the one human edge into
      READY, and it is bound to `str(unit.version)` so any move invalidates it. Offered where that
      edge exists, which is where `status_ledger` already reports the approval as pending.
    * **authority approval** -- refused outright while the envelope violates its own contract
      (`record_approval` re-raises the violation). Otherwise offered until the unit is settled:
      the approval's only consumer admits work, and `SETTLED_STATES` is the queue's own statement
      that a completed or cancelled unit is finished with people.
    * **review outcomes** -- each outcome offered iff a human could make that move. From SUBMITTED
      only completion is a human's; requesting a revision there belongs to the verifier.
    * **cancel** -- the human edges into CANCELLED.
    * **retry** -- `_require_retry_allowed`: FAILED, with the attempt budget spent. A failed unit
      with attempts left goes back through a requeue, which only SYSTEM may perform, so naming a
      retry there sends a person to a form that refuses them.

    The guard `authorize_transition` applies to a COMPLETED target is NOT modelled here: whether
    every criterion is satisfied needs the criteria and the adjudications, so the review form is
    offered on edge legality alone and the service still refuses an unfinished completion.
    """
    state = WorkUnitState(unit.state)
    review_outcomes = tuple(
        (value, label)
        for value, label, target in REVIEW_OUTCOMES
        if _a_human_could_move(state, target)
    )
    actions: dict[str, Any] = {
        "approval": _a_human_could_move(state, WorkUnitState.READY),
        "authority_approval": authority_violation is None and state not in SETTLED_STATES,
        "review_outcomes": review_outcomes,
        "cancel": _a_human_could_move(state, WorkUnitState.CANCELLED),
        "retry": state is WorkUnitState.FAILED and unit.attempt_count >= unit.max_attempts,
    }
    return actions


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
        "decision_facts": decision_facts_for_revision(revision),
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
    available = _available_actions(context["unit"], context["authority_violation"])
    context["available_actions"] = available
    # A token is minted for the forms the page will actually render, and for no others: an
    # unusable token is a control the page is pretending to have.
    #
    # "adjudication" joins the standard action tokens because the unit's criteria are now decided
    # by ONE form: one CSRF token, one idempotency key and one expected_version for the whole
    # submission, instead of a set keyed per acceptance criterion.
    actions = ["adjudication"] + [
        action
        for action in ("approval", "authority_approval", "cancel", "retry")
        if available[action]
    ]
    if available["review_outcomes"]:
        actions.append("review")
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
    context["adjudicatable_criteria"] = _adjudicatable_criteria(
        session, context["unit"], context["revision"]
    )
    context["waiver_risk_classes"] = WAIVER_RISK_CLASSES
    # An empty "Human actions" heading reads as a bug rather than as an answer, and a settled unit
    # legitimately has none. Computed here, over every control the section can carry.
    context["no_action_available"] = not (
        any(available.values()) or context["adjudicatable_criteria"] or context["open_conditions"]
    )
    context["decision_facts"] = decision_facts_for_unit(context["unit"], context["revision"])
    # Graduation evidence, computed only when the authority-approval control is actually rendered.
    # It is decision support for one decision -- "have I cleared this envelope shape before, and
    # how did it go?" -- so it belongs beside that form and nowhere else. On a unit with no such
    # control there is no decision to support, and the counts would read as a verdict on settled
    # work instead.
    context["graduation_ledger"] = (
        graduation_ledger(session, context["unit"]) if available["authority_approval"] else None
    )
    return _render(request, "unit.html", context)


@router.get("/intakes/new", response_class=HTMLResponse)
def new_intake(request: Request, actor: ActorDep) -> HTMLResponse:
    """The human surface for package intake (ADR-0006).

    Registered BEFORE `/intakes/{revision_id}` on purpose: FastAPI matches routes in declaration
    order, and `new` is a valid-looking path segment that the parameterised route would otherwise
    claim first (failing on UUID parsing instead of rendering this page).
    """
    _human(actor)
    idempotency_key = str(uuid.uuid4())
    return _render(
        request,
        "intake_new.html",
        {
            "idempotency_key": idempotency_key,
            "csrf_token": _issue_token(
                request, actor, _NEW_INTAKE_SUBJECT, "package_intake", idempotency_key
            ),
        },
    )


@router.post("/intakes")
def create_intake(
    request: Request,
    actor: ActorDep,
    session: SessionDep,
    payload: Annotated[str, Form(min_length=1)],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Register a package intake from the `emit-intake-payload` JSON pasted into the form.

    This is the only human-reachable intake path in production: the `/api/v1/package-intakes`
    route requires a HUMAN actor but sits on an M2M-only router, and no HUMAN credential exists
    (ADR-0006). It relaxes NOTHING -- the pasted JSON goes through the same
    `PackageIntakeRegistration` validation and the same `register_package_intake` service as the
    API route, so the `caller_attested_cli_verified` requirement, the `expected_version: 0`
    requirement and the approved-package requirement all still hold.
    """
    _human(actor)
    _require_form(
        request,
        actor,
        _NEW_INTAKE_SUBJECT,
        "package_intake",
        csrf_token,
        idempotency_key,
        confirm,
    )
    registration = _parse_intake_payload(payload, idempotency_key)
    revision = register_package_intake(session, package_intake_command(registration), actor)
    session.commit()
    return RedirectResponse(f"/review/intakes/{revision.id}", status_code=303)


def _parse_intake_payload(payload: str, idempotency_key: str) -> PackageIntakeRegistration:
    """Parse and validate the pasted JSON, as a DomainError on every failure path.

    Only `DomainError` and `APIAuthenticationError` have registered exception handlers, so a bare
    `json.JSONDecodeError` or a Pydantic `ValidationError` raised from here would surface as an
    unhandled HTTP 500 rather than a message the human can act on. The idempotency key is taken
    from the CSRF-bound form field, not from the payload: it is what the form already committed
    to, and letting the pasted text override it would let one token be replayed under a new key.

    Every OTHER field is passed through untouched. In particular `expected_version` is not
    defaulted here: `emit-intake-payload` always emits it, and supplying it on the browser path
    would make this form accept a payload the API route rejects -- which is exactly the
    "a new set of rules" that ADR-0006 says the form must not become.
    """
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as error:
        raise DomainError(
            "intake_payload_not_json",
            f"the intake payload is not valid JSON: {error}",
            "paste the exact output of `orchestrator emit-intake-payload`",
        ) from error
    if not isinstance(document, dict):
        raise DomainError(
            "intake_payload_not_an_object",
            "the intake payload must be a JSON object",
            "paste the exact output of `orchestrator emit-intake-payload`",
        )
    document["idempotency_key"] = idempotency_key
    try:
        return PackageIntakeRegistration.model_validate(document)
    except ValidationError as error:
        raise DomainError(
            "intake_payload_invalid",
            f"the intake payload failed validation: {error.error_count()} problem(s); "
            + "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors()[:5]
            ),
            "regenerate the payload with `orchestrator emit-intake-payload`",
        ) from error


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
    violation = runner_command_authority_violation(normalize_authority(unit.authority))
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


def _aligned(values: list[str] | None, count: int, field: str) -> tuple[str | None, ...]:
    """One criterion's worth of an optional field, positionally matched to the submitted ac_ids.

    The form renders a fieldset per criterion and every control inside it always submits, so the
    repeated fields arrive in document order and line up by index. A submission where they do NOT
    line up is refused rather than realigned: guessing which decision an unmatched rationale or
    waiver field belongs to would mis-attribute an outcome, which is the class of bug this
    increment exists to close.
    """
    if not values:
        return (None,) * count
    if len(values) != count:
        raise DomainError(
            "adjudication_invalid",
            f"submitted {field} values do not line up with the submitted acceptance criteria",
            None,
        )
    return tuple(_blank_to_none(value) for value in values)


def _submitted_decisions(
    ac_ids: list[str],
    outcomes: list[str],
    rationales: list[str],
    failed_evidence_ids: list[str] | None,
    risks: list[str] | None,
    follow_ups: list[str] | None,
    expiries: list[str] | None,
) -> tuple[AdjudicationDecision, ...]:
    count = len(ac_ids)
    if count == 0 or len(outcomes) != count or len(rationales) != count:
        raise DomainError(
            "adjudication_invalid",
            "each submitted acceptance criterion needs exactly one outcome and rationale",
            None,
        )
    failed = _aligned(failed_evidence_ids, count, "failed_evidence_id")
    risk_classes = _aligned(risks, count, "risk")
    follow_up_texts = _aligned(follow_ups, count, "follow_up")
    expires = _aligned(expiries, count, "expires_at")
    return tuple(
        AdjudicationDecision(
            ac_id=ac_ids[index],
            outcome=outcomes[index],
            rationale=rationales[index],
            failed_evidence_id=_parse_optional_uuid(failed[index]),
            risk=risk_classes[index],
            follow_up=follow_up_texts[index],
            expires_at=_parse_optional_datetime(expires[index]),
        )
        for index in range(count)
        # A criterion the reviewer left blank is not a decision (AC-012). It is dropped here rather
        # than defaulted, so answering two of three records exactly two.
        if _blank_to_none(outcomes[index]) is not None
    )


@router.post("/units/{unit_id}/adjudication")
def adjudicate(
    request: Request,
    unit_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    expected_version: Annotated[int, Form()],
    ac_id: Annotated[list[str], Form()],
    outcome: Annotated[list[str], Form()],
    rationale: Annotated[list[str], Form()],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
    failed_evidence_id: Annotated[list[str] | None, Form()] = None,
    risk: Annotated[list[str] | None, Form()] = None,
    follow_up: Annotated[list[str] | None, Form()] = None,
    expires_at: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, unit_id, "adjudication", csrf_token, idempotency_key, confirm)
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    result = record_adjudications(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit_id,
        decisions=_submitted_decisions(
            ac_id, outcome, rationale, failed_evidence_id, risk, follow_up, expires_at
        ),
        actor=actor,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
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
    target = next(
        (state for value, _, state in REVIEW_OUTCOMES if value == outcome),
        None,
    )
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
