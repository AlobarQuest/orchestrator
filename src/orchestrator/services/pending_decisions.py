"""What needs a human right now, across every gate kind (WS-P2.17, spec 5.5).

The review queue used to group work units by lifecycle state and list readiness facts, so finding
your own work meant already knowing which states imply a gate -- and five of the eight kinds below
are not work-unit states at all.

`kernel/transitions.py::DESIGNED_HUMAN_GATES` names the three *transition* gates and is the source
for exactly one kind here. It is not the whole set, and this module does not pretend otherwise:
a package registered but never broken up, a proposed breakdown, a unit whose authority envelope
nobody has approved, an undecided acceptance criterion and an open reconciliation condition are
all decisions waiting on a person, and none of them is an edge.

Every entry names **the decision required**, not the state the subject is in, and the whole list is
DERIVED on read -- nothing is ticked off, so an entry disappears exactly when its decision is
recorded.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.kernel.transitions import DESIGNED_HUMAN_GATES
from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposal,
    Evidence,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.authority_gate import human_authority_gate
from orchestrator.services.evidence import current_adjudication, current_evidence
from orchestrator.services.execution_stall import stalled_executions
from orchestrator.services.lifecycle import POST_DEPLOY_AC_IDS
from orchestrator.services.reconciliation import open_conditions
from orchestrator.services.verifier_criteria import load_required_criteria
from orchestrator.services.verifier_evaluators import human_may_adjudicate

# A unit in one of these states is finished with people. FAILED is not one of them: it is stopped
# and nothing automatic will move it, which is precisely a decision waiting on someone.
SETTLED_STATES = frozenset({WorkUnitState.COMPLETED, WorkUnitState.CANCELLED})

# Ordered so the entries that block everything downstream come first.
KIND_ORDER = (
    "package_breakdown",
    "decomposition_proposal",
    "authority_approval",
    "unit_transition",
    "stalled_execution",
    "failed_disposition",
    "criterion_adjudication",
    "reconciliation_condition",
)
KIND_LABELS: dict[str, str] = {
    "package_breakdown": "Packages with no breakdown in progress",
    "decomposition_proposal": "Proposed breakdowns awaiting your decision",
    "authority_approval": "Authority envelopes awaiting your approval",
    "unit_transition": "Work units stopped at a gate",
    "stalled_execution": "Work units whose worker went quiet",
    "failed_disposition": "Failed work units awaiting a disposition",
    "criterion_adjudication": "Acceptance criteria awaiting your judgment",
    "reconciliation_condition": "Divergences between reality and stored state",
}


def pending_decisions(
    session: Session, *, execution_stall_grace_seconds: int
) -> tuple[dict[str, Any], ...]:
    """Every decision waiting on a human, ordered by kind then by subject.

    The grace is required rather than defaulted, so no caller can arrive at a stall report by
    accident and none can leave the threshold behind: a second default here would be a second
    place for the configured one to disagree with.
    """
    entries: list[dict[str, Any]] = [
        *_package_breakdowns(session),
        *_proposed_breakdowns(session),
        *_unit_decisions(session),
        *_stalled_units(session, execution_stall_grace_seconds),
        *_open_divergences(session),
    ]
    entries.sort(key=lambda entry: (KIND_ORDER.index(entry["kind"]), entry["subject"]))
    return tuple(entries)


def grouped_pending_decisions(
    session: Session, *, execution_stall_grace_seconds: int
) -> tuple[dict[str, Any], ...]:
    """`pending_decisions` folded into its kinds, keeping `KIND_ORDER`. Empty kinds are dropped."""
    entries = pending_decisions(
        session, execution_stall_grace_seconds=execution_stall_grace_seconds
    )
    groups = []
    for kind in KIND_ORDER:
        members = [entry for entry in entries if entry["kind"] == kind]
        if members:
            # Keyed `entries`, never `items`: in Jinja `group.items` resolves the dict METHOD
            # before the key, so the template would render a bound method and 500 on |length.
            groups.append({"kind": kind, "label": KIND_LABELS[kind], "entries": tuple(members)})
    return tuple(groups)


def adjudicable_criteria(
    session: Session, unit: WorkUnit, revision: WorkPackageRevision
) -> tuple[tuple[PackageAcceptanceCriterion, bool, Evidence | None], ...]:
    """The criteria a human may be shown for this unit, each with whether they may decide it.

    The single source for that rule: the `/review` form renders these, and the queue counts the
    undecided ones among them. Two copies of "which criteria are a person's to decide" is the
    WS-P2.16 vocabulary-divergence shape, in a place where the divergence would be silent.

    The criterion's current evidence is returned with it because the answer already depends on
    that row -- the form renders it beside the decision, and reading it a second time there would
    be both a repeated query and a second chance to disagree about which row is current.

    The criterion ids the verifier generates for its own post-release checks are excluded on the
    id alone -- they are verifier-owned, and public adjudication of them is refused.
    """
    try:
        criteria = load_required_criteria(session, unit, revision)
    except DomainError:
        return ()
    rows = []
    for criterion in criteria:
        if criterion.ac_id in POST_DEPLOY_AC_IDS:
            continue
        evidence = current_evidence(session, revision.id, unit.id, criterion.ac_id)
        may_decide = human_may_adjudicate(criterion.evidence_type, evidence, unit.state)
        rows.append((criterion, may_decide, evidence))
    return tuple(rows)


def _entry(kind: str, subject: str, decision: str, detail: str, href: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "subject": subject,
        "decision": decision,
        "detail": detail,
        "href": href,
    }


def _package_breakdowns(session: Session) -> list[dict[str, Any]]:
    """Packages registered through the CLI intake with nothing in flight.

    There is no server-side record of an intake "awaiting registration" -- `emit-intake-payload`
    writes to the operator's terminal, and the first row that exists is the registered revision.
    So the pending decision a registered package can carry is the next one: have it broken into
    work units, or decide it goes no further.

    `manual_ws31` (the superseded bootstrap path) and `protocol_fixture` revisions are excluded:
    a fixture can never produce work units at all, so nothing about it is a person's to decide.
    """
    active_approved = select(ApprovedDecomposition.work_package_revision_id).where(
        ApprovedDecomposition.superseded_at.is_(None)
    )
    undecided_proposals = select(DecompositionProposal.work_package_revision_id).where(
        DecompositionProposal.state == "proposed"
    )
    revisions = session.scalars(
        select(WorkPackageRevision)
        .where(
            WorkPackageRevision.intake_source == "package_cli",
            WorkPackageRevision.id.not_in(active_approved),
            WorkPackageRevision.id.not_in(undecided_proposals),
        )
        .order_by(WorkPackageRevision.registered_at, WorkPackageRevision.id)
    )
    return [
        _entry(
            "package_breakdown",
            f"{revision.work_package.package_id} revision {revision.revision}",
            "Have this package broken into work units, or decide it goes no further",
            "It is registered and approved, and no breakdown of it is in progress.",
            f"/review/intakes/{revision.id}",
        )
        for revision in revisions
    ]


def _proposed_breakdowns(session: Session) -> list[dict[str, Any]]:
    proposals = session.scalars(
        select(DecompositionProposal)
        .where(DecompositionProposal.state == "proposed")
        .order_by(DecompositionProposal.proposed_at, DecompositionProposal.id)
    )
    return [
        _entry(
            "decomposition_proposal",
            f"Proposal {proposal.proposal_number}",
            "Approve, reject, or send back this proposed breakdown",
            proposal.rationale,
            f"/review/decomposition-proposals/{proposal.id}",
        )
        for proposal in proposals
    ]


def _gate_targets() -> dict[str, tuple[str, ...]]:
    """The states a designed human gate can move a unit to, keyed by the state it waits in."""
    targets: dict[str, set[str]] = {}
    for source, target in DESIGNED_HUMAN_GATES:
        targets.setdefault(str(source), set()).add(str(target))
    return {source: tuple(sorted(values)) for source, values in targets.items()}


def _unit_decisions(session: Session) -> list[dict[str, Any]]:
    gates = _gate_targets()
    entries: list[dict[str, Any]] = []
    units = session.scalars(
        select(WorkUnit)
        .where(WorkUnit.state.not_in(tuple(str(state) for state in SETTLED_STATES)))
        .order_by(WorkUnit.created_at, WorkUnit.id)
    )
    for unit in units:
        href = f"/review/units/{unit.id}"
        if unit.authority_approval_id is None and _authority_gate_refusals(session, unit):
            entries.append(
                _entry(
                    "authority_approval",
                    unit.title,
                    "Approve this unit's authority envelope, or leave it unable to proceed",
                    "No approval is bound to this unit's current authority fingerprint.",
                    href,
                )
            )
        targets = gates.get(unit.state)
        if targets is not None:
            outcomes = " or ".join(target.replace("_", " ") for target in targets)
            entries.append(
                _entry(
                    "unit_transition",
                    unit.title,
                    f"Decide whether this unit becomes {outcomes}",
                    f"It has been waiting in {unit.state.replace('_', ' ')}.",
                    href,
                )
            )
        if unit.state == str(WorkUnitState.FAILED):
            entries.append(_failed_disposition(unit, href))
        entries.extend(_undecided_criteria(session, unit, href))
    return entries


def _authority_gate_refusals(session: Session, unit: WorkUnit) -> tuple[str, ...]:
    """Why policy still wants a person on this unit's envelope; empty means it does not (ADR-0011).

    This is the surface the whole increment is for: R2 is *"a dependency bump becomes something he
    does not approve"*, and an entry that keeps appearing for work policy already recognises is the
    ceremony R7 named. A revision that cannot be loaded leaves the entry standing, which is both
    the fail-toward-asking rule and the behaviour this queue has always had.
    """
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        return ("revision_not_found",)
    return human_authority_gate(unit, revision).refusals


def _failed_disposition(unit: WorkUnit, href: str) -> dict[str, Any]:
    """The disposition a failed unit needs, named for what this person can actually do.

    `authorize_retry` is the only route back for a unit whose attempt budget is spent, and it
    refuses `attempts_not_exhausted` for one that still has attempts left -- where the way back is
    a requeue, which only SYSTEM may perform. Naming a retry in that case would send a person to a
    form that refuses them, so the two cases read differently. Cancellation is always theirs.
    """
    attempts = f"It failed after {unit.attempt_count} of {unit.max_attempts} attempts."
    if unit.attempt_count >= unit.max_attempts:
        return _entry(
            "failed_disposition",
            unit.title,
            "Authorize a retry with a raised attempt limit, or cancel this unit",
            f"{attempts} Its attempt budget is spent, so running it again needs a raised limit.",
            href,
        )
    return _entry(
        "failed_disposition",
        unit.title,
        "Cancel this unit, or have the system requeue it",
        f"{attempts} It has attempts left, so a requeue needs no raised limit.",
        href,
    )


def _undecided_criteria(session: Session, unit: WorkUnit, href: str) -> list[dict[str, Any]]:
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        return []
    entries = []
    for criterion, may_decide, _evidence in adjudicable_criteria(session, unit, revision):
        if not may_decide:
            continue
        if current_adjudication(session, revision.id, unit.id, criterion.ac_id) is not None:
            continue
        entries.append(
            _entry(
                "criterion_adjudication",
                f"{unit.title} · {criterion.ac_id}",
                "Judge this acceptance criterion against its evidence",
                criterion.condition,
                href,
            )
        )
    return entries


def _stalled_units(session: Session, grace_seconds: int) -> list[dict[str, Any]]:
    """A unit that was claimed, started, and then went quiet (WS-P2.19).

    The decision names only what this person can actually do. Cancelling is theirs, from the
    control on the unit page; recovering the expired claim is the system's, on request. Requeue is
    deliberately not named -- it targets failed and blocked units, so offering it here would send
    someone to an action that refuses them, which is the mistake `_failed_disposition` already
    avoids in the other direction.

    The detail says outright that the orchestrator cannot tell a dead worker from a live one,
    because a reader who assumed it could would over-read the entry. What it CAN say is the part
    that matters for the decision: that attempt is finished either way.
    """
    return [
        _entry(
            "stalled_execution",
            stalled.title,
            "Cancel this unit, or have the system recover its expired claim",
            (
                f"Attempt {stalled.attempt} has held it in {stalled.state} since its hold ended "
                f"at {stalled.hold_ended_at.isoformat()}, {stalled.stalled_seconds}s ago. That "
                "attempt can no longer report anything — a lapsed claim refuses both a renewal "
                "and an evidence write — so this entry reads the same whether its worker died or "
                "is still running."
            ),
            f"/review/units/{stalled.work_unit_id}",
        )
        for stalled in stalled_executions(session, grace_seconds=grace_seconds)
    ]


def _open_divergences(session: Session) -> list[dict[str, Any]]:
    entries = []
    for condition in open_conditions(session):
        unit = session.get(WorkUnit, condition.work_unit_id)
        title = unit.title if unit is not None else str(condition.work_unit_id)
        entries.append(
            _entry(
                "reconciliation_condition",
                f"{title} · {condition.condition_type}",
                "Accept, correct, or dismiss this divergence",
                condition.detail,
                _condition_href(condition.work_unit_id),
            )
        )
    return entries


def _condition_href(work_unit_id: uuid.UUID) -> str:
    """Conditions are resolved from the unit page, where the per-condition form lives."""
    return f"/review/units/{work_unit_id}"
