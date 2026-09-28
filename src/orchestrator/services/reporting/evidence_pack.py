"""Per-unit evidence-pack projection (WS-P2.5).

Assembles the full evidentiary record for a single work unit -- authority, revision,
dependencies, claims, evidence, adjudications, approvals, and events -- into a single
read-only dict. Originally private to the ``/review`` GUI module; moved here so other callers
can share the identical assembly and query logic.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.runner_authority import runner_authority_violation
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
from orchestrator.services.lifecycle.lifecycle import (
    VerifierDecidedCompletion,
    verifier_decided_completion,
)


class EvidencePackWorkUnitResponse(BaseModel):
    """WS-P2.5: the subset of a work unit the evidence pack keys everything else against."""

    id: uuid.UUID
    title: str
    state: str
    authority_fingerprint: str


class EvidencePackProvenanceResponse(BaseModel):
    """The canonical package-revision facts a reviewer checks first: what was actually built."""

    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    registered_by: str


class EvidencePackAuthorityViolationResponse(BaseModel):
    code: str
    message: str
    remediation: str | None = None


class EvidencePackAuthorityResponse(BaseModel):
    authority_fingerprint: str
    envelope: dict[str, Any]
    authority_violation: EvidencePackAuthorityViolationResponse | None = None


class EvidencePackDependencyResponse(BaseModel):
    kind: str
    required_state_or_condition: str
    status: str


class EvidencePackClaimResponse(BaseModel):
    attempt: int
    claimed_by: str
    lease_expires_at: datetime
    terminal_reason: str | None = None


class EvidencePackEvidenceResponse(BaseModel):
    """One AC-keyed evidence record. `supersedes` chains to a prior entry's `id`."""

    id: uuid.UUID
    ac_id: str
    current: bool
    evidence_type: str
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    supersedes: uuid.UUID | None = None


class EvidencePackAdjudicationResponse(BaseModel):
    """One AC-keyed adjudication. Waiver fields are populated only when `outcome == "waived"`."""

    id: uuid.UUID
    ac_id: str
    outcome: str
    current: bool
    decided_by: str
    # WS-P3.7. The KIND of actor that decided, as a stored fact. NULL on every row written before
    # the column existed, and NULL means *unknown* -- a consumer must never read it as "not human".
    decided_by_role: str | None = None
    # The evidence the decision was recorded against. `failed_evidence_id` below is the waiver
    # field and answers a different question; only it was projected before.
    evidence_id: uuid.UUID | None = None
    rationale: str
    risk: str | None = None
    follow_up: str | None = None
    scope: str | None = None
    expires_at: datetime | None = None
    failed_evidence_id: uuid.UUID | None = None


class EvidencePackCriterionRefusalResponse(BaseModel):
    """One reason the unit does not qualify. `ac_id` is null when the reason is unit-wide."""

    ac_id: str | None = None
    code: str


class EvidencePackVerifierDecidedResponse(BaseModel):
    """Whether every required acceptance criterion of this unit reached a current terminal
    adjudication that the verifier recorded from its own evaluation of evidence.

    Computed once, in `services/lifecycle.py`, and served here so an off-process consumer can read
    the answer without parsing `/history` for an opaque event payload. Fails closed in every
    direction: an unrecorded decider kind, a criterion with no single current adjudication, a
    waiver, or a revision that declares no usable criteria all make `satisfied` false and name
    themselves in `refusals`.
    """

    satisfied: bool
    # ADR-0020's sentence, as its two clauses. `decided_by_verifier` is "with no human
    # adjudication"; `evidence_observed` is "from observed evidence". Served separately because a
    # criterion can fail either one alone, and an off-process consumer that can only read the AND
    # cannot tell which -- which is the whole reason Increment 1 made the condition readable.
    decided_by_verifier: bool
    evidence_observed: bool
    refusals: list[EvidencePackCriterionRefusalResponse]


class EvidencePackApprovalResponse(BaseModel):
    subject_type: str
    decision: str
    approved_by: str
    reason: str


class EvidencePackEventResponse(BaseModel):
    """One event, projected. A key this model does not declare is silently dropped, so a payload
    field a reader needs has to be named here as well as written there."""

    occurred_at: datetime
    action: str
    actor_id: str
    from_state: str | None = None
    to_state: str | None = None
    reason: str | None = None
    # ADR-0032, on the two acts that can carry one. Full fidelity in this JSON, which is
    # authenticated; the markdown renderer relays onto a possibly-public pull request comment and
    # deliberately does not interpolate the operator's words.
    change_window_override: dict[str, Any] | None = None


class EvidencePackResponse(BaseModel):
    """A single work unit's full evidentiary record, structured for programmatic consumption.

    Mirrors the field set of the `/review` evidence-pack HTML page (`templates/evidence_pack.html`)
    exactly, but as JSON any authenticated caller can read -- including the runner's WORKER
    credential, which has no role gate on this route. Field names are chosen so a per-release pack
    (WS-P2.5 Increment 2) can nest a `list[EvidencePackResponse]` without renaming anything here.
    """

    work_unit: EvidencePackWorkUnitResponse
    provenance: EvidencePackProvenanceResponse
    authority: EvidencePackAuthorityResponse
    dependencies: list[EvidencePackDependencyResponse]
    claims: list[EvidencePackClaimResponse]
    evidence: list[EvidencePackEvidenceResponse]
    adjudications: list[EvidencePackAdjudicationResponse]
    verifier_decided_completion: EvidencePackVerifierDecidedResponse
    approvals: list[EvidencePackApprovalResponse]
    events: list[EvidencePackEventResponse]


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
    }


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

    The projection is deliberately GUI-shaped (ORM rows, `set[uuid.UUID]` membership tests) since it
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
