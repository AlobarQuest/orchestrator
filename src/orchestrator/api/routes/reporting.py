import re
import uuid
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from orchestrator.api.dependencies import ActorDep, SessionDep, SettingsDep
from orchestrator.api.routes.common import ERROR_RESPONSES
from orchestrator.api.schemas.reporting import (
    DeadLetterEntryResponse,
    SloReportResponse,
    StatusLedgerRowResponse,
)
from orchestrator.errors import DomainError
from orchestrator.services.lifecycle.lifecycle import require_operator_actor
from orchestrator.services.reporting.dead_letter import dead_letter
from orchestrator.services.reporting.evidence_pack import (
    EvidencePackResponse,
    evidence_pack_projection,
    evidence_pack_response,
    render_evidence_pack_markdown,
)
from orchestrator.services.reporting.slo_report import SloReportFilters, slo_report
from orchestrator.services.reporting.status_ledger import StatusLedgerFilters, status_ledger
from orchestrator.services.reporting.traceability import (
    TraceabilityAnchor,
    TraceabilityResponse,
    traceability_response,
)

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.get("/work-units/{unit_id}/evidence-pack", response_model=EvidencePackResponse)
def evidence_pack_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    """WS-P2.5 Increment 1: the structured JSON twin of the `/review` evidence-pack page.

    Authentication-only, deliberately no role gate -- the runner's WORKER credential must be able
    to read its own unit's evidentiary record.
    """
    return evidence_pack_response(evidence_pack_projection(session, unit_id))


@router.get(
    "/work-units/{unit_id}/evidence-pack/markdown",
    response_class=PlainTextResponse,
    include_in_schema=True,
)
def evidence_pack_markdown_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> PlainTextResponse:
    """WS-P2.5 Increment 1: a server-rendered markdown view of the same structured pack.

    Auth-only, no role gate -- identical access as the JSON twin. Rendered purely from
    `EvidencePackResponse`, never from the ORM projection or a template engine, so JSON and
    markdown are always two views of the one structured source.
    """
    pack = evidence_pack_response(evidence_pack_projection(session, unit_id))
    return PlainTextResponse(render_evidence_pack_markdown(pack), media_type="text/markdown")


@router.get("/dead-letter", response_model=list[DeadLetterEntryResponse])
def dead_letter_route(
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    """Read-only: terminal failures AND stalled approval gates made visible.

    The stalled-approval threshold takes no parameter and has no off switch (WS-P2.15).
    """
    require_operator_actor(actor)
    return dead_letter(
        session,
        failure_signature_threshold=settings.dispatch_failure_signature_threshold,
        stalled_approval_seconds=settings.dead_letter_stalled_approval_seconds,
        stalled_verification_seconds=settings.dead_letter_stalled_verification_seconds,
    )


@router.get("/status-ledger", response_model=list[StatusLedgerRowResponse])
def status_ledger_route(
    _actor: ActorDep,
    session: SessionDep,
    actor_id: str | None = None,
    work_unit_id: UUID | None = None,
    state: str | None = None,
    include_inactive: bool = False,
) -> object:
    return status_ledger(
        session,
        StatusLedgerFilters(
            actor_id=actor_id,
            work_unit_id=work_unit_id,
            state=state,
            include_inactive=include_inactive,
        ),
    )


@router.get("/slo-report", response_model=SloReportResponse)
def slo_report_route(
    _actor: ActorDep,
    session: SessionDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> object:
    return slo_report(session, SloReportFilters(since=since, until=until))


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@router.get("/traceability", response_model=TraceabilityResponse)
def traceability_route(
    _actor: ActorDep,
    session: SessionDep,
    work_unit_id: str | None = None,
    revision_id: str | None = None,
    artifact_digest: str | None = None,
    commit: str | None = None,
    pr_number: int | None = None,
    source_repository: str | None = None,
    environment: str | None = None,
    observation_id: str | None = None,
) -> object:
    """WS-P2.6: the intent -> unit -> PR -> commit -> artifact -> deployment -> observation chain.

    Authentication-only, no role gate, matching the other read surfaces this composes
    (evidence-pack, release-artifacts, deployment-observations). Read-only: it writes nothing.

    `observation_id` (ADR-0026 amendment 1) is the anchor that asks the question from the other
    end: what work did this signal cause? It is a query parameter on the existing route rather
    than a route of its own, so the exactly-one-anchor rule covers it without anything moving.
    """
    anchor = _parse_traceability_anchor(
        work_unit_id=work_unit_id,
        revision_id=revision_id,
        artifact_digest=artifact_digest,
        commit=commit,
        pr_number=pr_number,
        source_repository=source_repository,
        environment=environment,
        observation_id=observation_id,
    )
    return traceability_response(session, anchor)


def _parse_traceability_anchor(
    *,
    work_unit_id: str | None,
    revision_id: str | None,
    artifact_digest: str | None,
    commit: str | None,
    pr_number: int | None,
    source_repository: str | None,
    environment: str | None,
    observation_id: str | None,
) -> TraceabilityAnchor:
    active = [
        (kind, value)
        for kind, value in (
            ("work_unit", work_unit_id),
            ("revision", revision_id),
            ("artifact_digest", artifact_digest),
            ("commit", commit),
            ("pr", pr_number),
            ("environment", environment),
            ("observation", observation_id),
        )
        if value is not None
    ]
    if not active:
        raise DomainError("traceability_anchor_required", "provide exactly one anchor", None)
    if len(active) > 1:
        raise DomainError("traceability_anchor_ambiguous", "provide exactly one anchor", None)
    if source_repository is not None and pr_number is None:
        raise DomainError(
            "traceability_anchor_invalid", "source_repository requires pr_number", None
        )
    kind, value = active[0]
    return _ANCHOR_BUILDERS[kind](value, source_repository)


def _build_work_unit_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    return TraceabilityAnchor(kind="work_unit", work_unit_id=_parse_uuid(value, "work_unit_id"))


def _build_revision_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    return TraceabilityAnchor(kind="revision", revision_id=_parse_uuid(value, "revision_id"))


def _build_observation_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    # `_parse_uuid` rather than a bare `uuid.UUID`: an unwrapped parse raises `ValueError`, and
    # only `DomainError` and `APIAuthenticationError` have registered handlers, so a mistyped
    # anchor would reach the caller as an unhandled HTTP 500 instead of a named refusal.
    return TraceabilityAnchor(
        kind="observation", observation_id=_parse_uuid(value, "observation_id")
    )


def _build_commit_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    if _COMMIT_RE.fullmatch(value) is None:
        raise DomainError("invalid_commit", "commit must be a 40-char hex sha", None)
    return TraceabilityAnchor(kind="commit", commit=value)


def _build_pr_anchor(value: object, source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, int)
    if value <= 0:
        raise DomainError("invalid_pr_number", "pr_number must be positive", None)
    return TraceabilityAnchor(kind="pr", pr_number=value, source_repository=source_repository)


def _build_artifact_digest_anchor(
    value: object, _source_repository: str | None
) -> TraceabilityAnchor:
    assert isinstance(value, str)
    return TraceabilityAnchor(kind="artifact_digest", artifact_digest=value)


def _build_environment_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    return TraceabilityAnchor(kind="environment", environment=value)


_ANCHOR_BUILDERS = {
    "work_unit": _build_work_unit_anchor,
    "revision": _build_revision_anchor,
    "artifact_digest": _build_artifact_digest_anchor,
    "commit": _build_commit_anchor,
    "pr": _build_pr_anchor,
    "environment": _build_environment_anchor,
    "observation": _build_observation_anchor,
}


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError, TypeError:
        raise DomainError(f"invalid_{field}", f"{field} must be a UUID", None) from None
