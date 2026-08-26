"""Per-unit evidence-pack projection (WS-P2.5).

Assembles the full evidentiary record for a single work unit -- authority, revision,
dependencies, claims, evidence, adjudications, approvals, events, and event publications --
into a single read-only dict. Originally private to the ``/review`` GUI module; moved here so
other callers can share the identical assembly and query logic.
"""

import uuid
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from orchestrator.api.schemas import (
    EvidencePackAdjudicationResponse,
    EvidencePackApprovalResponse,
    EvidencePackAuthorityResponse,
    EvidencePackAuthorityViolationResponse,
    EvidencePackClaimResponse,
    EvidencePackCriterionRefusalResponse,
    EvidencePackDependencyResponse,
    EvidencePackEventPublicationResponse,
    EvidencePackEventResponse,
    EvidencePackEvidenceResponse,
    EvidencePackProvenanceResponse,
    EvidencePackResponse,
    EvidencePackVerifierDecidedResponse,
    EvidencePackWorkUnitResponse,
)
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.runner_authority import runner_authority_violation
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Claim,
    Dependency,
    Event,
    EventPublication,
    Evidence,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle import (
    VerifierDecidedCompletion,
    verifier_decided_completion,
)


def evidence_pack_projection(session: Session, unit_id: uuid.UUID) -> dict[str, Any]:
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
    events = tuple(
        session.scalars(
            select(Event).where(Event.subject_id == unit.id).order_by(Event.occurred_at, Event.id)
        )
    )
    authority = normalize_authority(unit.authority).normalized()
    violation = runner_authority_violation(normalize_authority(unit.authority), unit.authority)
    return {
        "unit": unit,
        "authority": authority,
        "authority_violation": (
            {
                "code": violation.code,
                "message": violation.message,
                "remediation": violation.remediation,
            }
            if violation is not None
            else None
        ),
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
        # The one derived answer on the pack: computed here so the JSON route, the markdown twin
        # and the `/review` page all read the same result rather than three restatements of it.
        "verifier_decided_completion": verifier_decided_completion(session, revision, unit),
        "approvals": tuple(
            session.scalars(
                select(Approval).where(Approval.subject_id == unit.id).order_by(Approval.created_at)
            )
        ),
        "events": events,
        "event_publications": _event_publication_projection(
            session,
            evidence=evidence,
            adjudications=adjudications,
            events=events,
        ),
    }


def _event_publication_projection(
    session: Session,
    *,
    evidence: tuple[Evidence, ...],
    adjudications: tuple[Adjudication, ...],
    events: tuple[Event, ...],
) -> tuple[dict[str, Any], ...]:
    source_ids: dict[str, set[uuid.UUID]] = {
        "evidence": {row.id for row in evidence},
        "adjudication": {row.id for row in adjudications},
        "event": {row.id for row in events},
    }
    clauses = [
        and_(EventPublication.source_kind == kind, EventPublication.source_id.in_(ids))
        for kind, ids in source_ids.items()
        if ids
    ]
    if not clauses:
        return ()
    rows = tuple(
        session.scalars(
            select(EventPublication)
            .where(or_(*clauses))
            .order_by(
                EventPublication.source_kind,
                EventPublication.source_id,
                EventPublication.created_at,
                EventPublication.event_id,
            )
        )
    )
    return tuple(
        {
            "row": row,
            "source_ref": f"orchestrator:{row.source_kind}:{row.source_id}",
        }
        for row in rows
    )


def _verifier_decided_response(
    answer: VerifierDecidedCompletion,
) -> EvidencePackVerifierDecidedResponse:
    return EvidencePackVerifierDecidedResponse(
        satisfied=answer.satisfied,
        decided_by_verifier=answer.decided_by_verifier,
        evidence_observed=answer.evidence_observed,
        refusals=[
            EvidencePackCriterionRefusalResponse(ac_id=refusal.ac_id, code=refusal.code)
            for refusal in answer.refusals
        ],
    )


def evidence_pack_response(projection: dict[str, Any]) -> EvidencePackResponse:
    """Serialize `evidence_pack_projection`'s ORM/set-bearing dict into a JSON-safe response.

    The projection is deliberately GUI-shaped (ORM rows, `set[UUID]` membership tests) since it
    was originally private to the `/review` template. This is the one place that maps it to plain,
    JSON-serializable types -- callers must never return the projection dict directly from a JSON
    route.
    """
    unit: WorkUnit = projection["unit"]
    revision: WorkPackageRevision = projection["revision"]
    current_evidence_ids: set[uuid.UUID] = projection["current_evidence_ids"]
    current_adjudication_ids: set[uuid.UUID] = projection["current_adjudication_ids"]
    violation = projection["authority_violation"]

    return EvidencePackResponse(
        work_unit=EvidencePackWorkUnitResponse(
            id=unit.id,
            title=unit.title,
            state=unit.state,
            authority_fingerprint=unit.authority_fingerprint,
        ),
        provenance=EvidencePackProvenanceResponse(
            revision=revision.revision,
            content_hash=revision.content_hash,
            source_path=revision.source_path,
            source_commit=revision.source_commit,
            registered_by=revision.registered_by,
        ),
        authority=EvidencePackAuthorityResponse(
            authority_fingerprint=unit.authority_fingerprint,
            envelope=projection["authority"],
            authority_violation=(
                EvidencePackAuthorityViolationResponse(**violation)
                if violation is not None
                else None
            ),
        ),
        dependencies=[
            EvidencePackDependencyResponse(
                kind=row.kind,
                required_state_or_condition=row.required_state_or_condition,
                status=row.status,
            )
            for row in projection["dependencies"]
        ],
        claims=[
            EvidencePackClaimResponse(
                attempt=row.attempt,
                claimed_by=row.claimed_by,
                lease_expires_at=row.lease_expires_at,
                terminal_reason=row.terminal_reason,
            )
            for row in projection["claims"]
        ],
        evidence=[
            EvidencePackEvidenceResponse(
                id=row.id,
                ac_id=row.ac_id,
                current=row.id in current_evidence_ids,
                evidence_type=row.evidence_type,
                stable_ref=row.stable_ref,
                payload=row.payload,
                supersedes=row.supersedes_evidence_id,
            )
            for row in projection["evidence"]
        ],
        adjudications=[
            EvidencePackAdjudicationResponse(
                id=row.id,
                ac_id=row.ac_id,
                outcome=row.outcome,
                current=row.id in current_adjudication_ids,
                decided_by=row.decided_by,
                decided_by_role=row.decided_by_role,
                evidence_id=row.evidence_id,
                rationale=row.rationale,
                risk=row.risk,
                follow_up=row.follow_up,
                scope=row.scope,
                expires_at=row.expires_at,
                failed_evidence_id=row.failed_evidence_id,
            )
            for row in projection["adjudications"]
        ],
        verifier_decided_completion=_verifier_decided_response(
            projection["verifier_decided_completion"]
        ),
        approvals=[
            EvidencePackApprovalResponse(
                subject_type=row.subject_type,
                decision=row.decision,
                approved_by=row.approved_by,
                reason=row.reason,
            )
            for row in projection["approvals"]
        ],
        event_publications=[
            EvidencePackEventPublicationResponse(
                source_ref=item["source_ref"],
                status=item["row"].status,
                event_id=item["row"].event_id,
                export_ref=item["row"].export_ref,
                last_error=item["row"].last_error,
            )
            for item in projection["event_publications"]
        ],
        events=[
            EvidencePackEventResponse(
                occurred_at=row.occurred_at,
                action=row.action,
                actor_id=row.actor_id,
                from_state=row.from_state,
                to_state=row.to_state,
                reason=row.payload.get("reason") if row.payload else None,
                change_window_override=(
                    row.payload.get("change_window_override") if row.payload else None
                ),
            )
            for row in projection["events"]
        ],
    )


def _md_cell(value: object) -> str:
    """Make a value safe to place inside a markdown table cell.

    A literal `|` shifts every downstream column and a bare newline breaks out of the table
    row entirely -- both are realistic in free-text values (exception messages, JSON payloads)
    that this module puts straight into `| ... |` rows. Escape backslashes first so the pipe
    escape introduced below is never itself re-escaped, then escape `|`, then collapse CR/LF to
    a single space. `None` renders as an empty cell.
    """
    if value is None:
        return ""
    text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def render_evidence_pack_markdown(pack: EvidencePackResponse) -> str:
    """Render an `EvidencePackResponse` as the same 8 sections the `/review` GUI shows.

    Pure `EvidencePackResponse -> str`: the structured pack is the one source that feeds both
    the JSON route and this markdown view -- this function never touches the ORM projection or a
    template engine.
    """
    lines: list[str] = [
        f"# Evidence Pack: {pack.work_unit.title}",
        "",
        f"Unit `{pack.work_unit.id}` -- state `{pack.work_unit.state}`",
        "",
        "> Approver identities and adjudication rationale are omitted here; the full "
        "record is in the orchestrator.",
        "",
        *_render_provenance_section(pack),
        *_render_authority_section(pack),
        *_render_dependencies_and_claims_section(pack),
        *_render_evidence_section(pack),
        *_render_adjudications_section(pack),
        *_render_approvals_section(pack),
        *_render_event_publications_section(pack),
        *_render_event_history_section(pack),
    ]
    return "\n".join(lines) + "\n"


def _render_provenance_section(pack: EvidencePackResponse) -> list[str]:
    provenance = pack.provenance
    return [
        "## Canonical provenance",
        f"- Package revision: {provenance.revision}",
        f"- Content hash: {provenance.content_hash}",
        f"- Source path: {provenance.source_path}",
        f"- Source commit: {provenance.source_commit}",
        f"- Registered by: {provenance.registered_by}",
        "",
    ]


def _render_authority_section(pack: EvidencePackResponse) -> list[str]:
    authority = pack.authority
    lines = [
        "## Authority",
        f"- Fingerprint: `{authority.authority_fingerprint}`",
        f"- Envelope: `{authority.envelope}`",
    ]
    if authority.authority_violation is not None:
        violation = authority.authority_violation
        remediation = f" Remediation: {violation.remediation}." if violation.remediation else ""
        lines.append(f"- Violation: {violation.code} -- {violation.message}.{remediation}")
    lines.append("")
    return lines


def _render_dependencies_and_claims_section(pack: EvidencePackResponse) -> list[str]:
    lines = ["## Dependencies and claims"]
    if pack.dependencies or pack.claims:
        for dependency in pack.dependencies:
            lines.append(f"- {dependency.kind}: {dependency.status}")
        for claim in pack.claims:
            lines.append(
                f"- Attempt {claim.attempt} claimed by {claim.claimed_by}, "
                f"expires {claim.lease_expires_at.isoformat()}, "
                f"reason {claim.terminal_reason or 'active'}"
            )
    else:
        lines.append("- None recorded")
    lines.append("")
    return lines


def _render_evidence_section(pack: EvidencePackResponse) -> list[str]:
    lines = [
        "## AC-keyed evidence, including supersession",
        "| AC | Status | Type | Reference or payload | Supersedes |",
        "| --- | --- | --- | --- | --- |",
    ]
    if pack.evidence:
        for row in pack.evidence:
            status = "current" if row.current else "superseded"
            reference = row.stable_ref if row.stable_ref is not None else row.payload
            lines.append(
                f"| {_md_cell(row.ac_id)} | {_md_cell(status)} | {_md_cell(row.evidence_type)} | "
                f"{_md_cell(reference)} | {_md_cell(row.supersedes) or 'Root'} |"
            )
    else:
        lines.append("| -- | -- | -- | No evidence recorded. | -- |")
    lines.append("")
    return lines


def _render_adjudications_section(pack: EvidencePackResponse) -> list[str]:
    """Omits `decided_by` (approver identity) and `rationale` (free-text reasoning) -- this
    markdown is relayed into a PR comment on the target repo, which may be public. The full
    fields remain on the JSON route.

    Two WS-P3.7 fields, decided rather than inherited:

    * `decided_by_role` IS rendered. A role is a kind, not an identity -- "verifier" and "human"
      name no person -- and it is the one fact a reader of a public pull request most needs in
      order to know whether anyone actually looked at this. `unrecorded` is printed for NULL so
      the historical rows read as unknown rather than silently as machine-decided.
    * `evidence_id` is NOT rendered. It is an internal row identifier that means nothing outside
      the orchestrator, so it would be noise on the comment and a database handle on a possibly
      public page; the JSON route carries it for the consumers that can resolve it.
    """
    lines = ["## Adjudications and waiver facts"]
    if pack.adjudications:
        for row in pack.adjudications:
            status = "current" if row.current else "superseded"
            decider = row.decided_by_role or "unrecorded"
            entry = f"- {row.ac_id}: {row.outcome} ({status}), decided by the {decider} role"
            if row.outcome == "waived":
                expires = row.expires_at.isoformat() if row.expires_at else "never"
                entry += (
                    f" Failed evidence: {row.failed_evidence_id}. Risk: {row.risk}. "
                    f"Follow-up: {row.follow_up}. Scope: {row.scope or 'full'}. "
                    f"Expires: {expires}."
                )
            lines.append(entry)
    else:
        lines.append("- No adjudications recorded.")
    lines.append("")
    lines.extend(_render_verifier_decided_lines(pack))
    return lines


def _render_verifier_decided_lines(pack: EvidencePackResponse) -> list[str]:
    """The one derived answer, rendered because it is the headline of the section above and
    carries no identity: refusal codes name criteria, never people."""
    answer = pack.verifier_decided_completion
    # Both clauses, separately labelled. The headline used to read only the decider clause while
    # rendering `satisfied`, which since Increment 4b is the AND of two — so a unit whose criteria
    # WERE all verifier-decided, off evidence the worker attested to, rendered "no" under a
    # sentence about who decided. This markdown is relayed onto a possibly-public pull request,
    # which makes it the surface most likely to be quoted out of its context.
    lines = [
        "Every required criterion decided by the verifier from its own evaluation: "
        + ("yes" if answer.decided_by_verifier else "no"),
        "Every required criterion resolved from evidence the orchestrator observed: "
        + ("yes" if answer.evidence_observed else "no"),
    ]
    for refusal in answer.refusals:
        subject = refusal.ac_id or "unit"
        lines.append(f"- {subject}: {refusal.code}")
    lines.append("")
    return lines


def _render_approvals_section(pack: EvidencePackResponse) -> list[str]:
    """Omits `approved_by` (approver identity) and `reason` (free-text) -- same PR-comment
    exposure as the adjudications section above."""
    lines = ["## Approvals"]
    if pack.approvals:
        for row in pack.approvals:
            lines.append(f"- {row.subject_type} {row.decision}")
    else:
        lines.append("- No approvals recorded.")
    lines.append("")
    return lines


def _render_event_publications_section(pack: EvidencePackResponse) -> list[str]:
    lines = [
        "## Event publications",
        "| Source | Status | Event ID | Export | Last error |",
        "| --- | --- | --- | --- | --- |",
    ]
    if pack.event_publications:
        for row in pack.event_publications:
            lines.append(
                f"| {_md_cell(row.source_ref)} | {_md_cell(row.status)} | "
                f"{_md_cell(row.event_id)} | {_md_cell(row.export_ref) or 'Not exported'} | "
                f"{_md_cell(row.last_error) or 'None'} |"
            )
    else:
        lines.append("| -- | -- | -- | No event publications recorded. | -- |")
    lines.append("")
    return lines


def _render_event_history_section(pack: EvidencePackResponse) -> list[str]:
    """Omits `actor_id` (identity) -- the payload `reason` (an operational code, e.g.
    `budget_exceeded`) is kept.

    An override of the change window (ADR-0032) is reported as having happened and NOT quoted:
    the reason is an operator's own words, this rendering is relayed onto a pull request comment
    that may be public, and free text is what every other section here redacts by hand. The JSON
    is authenticated and carries the whole record, which is where a reader goes for the words.
    """
    lines = ["## Event history"]
    if pack.events:
        for index, row in enumerate(pack.events, start=1):
            reason = f" -- Reason: {row.reason}" if row.reason else ""
            override = _override_note(row.change_window_override)
            lines.append(
                f"{index}. {row.occurred_at.isoformat()} -- {row.action} -- "
                f"{row.from_state or 'none'} to {row.to_state or 'none'}{reason}{override}"
            )
    else:
        lines.append("1. No events recorded.")
    return lines


def _override_note(override: dict[str, Any] | None) -> str:
    """Whether a change-window override was carried, and whether it changed the answer. No words.

    Carried-and-unused is reported rather than hidden: an act inside the declared hours needed no
    override, and saying so is what stops a reader inferring one from the other.
    """
    if not override:
        return ""
    return (
        " -- Change window overridden"
        if override.get("applied")
        else " -- Change window override carried, not applied"
    )
