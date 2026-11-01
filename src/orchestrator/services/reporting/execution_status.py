"""The execution-status read: one row per subject still in play, for the execution alert lane.

Derived live from the source tables; it writes nothing. A subject is a work unit, or something that
waits on a person before a unit exists: a staged intake, an undecomposed package, or an undecided
proposal. Units in `completed` or `cancelled` have reached the end of their story and have no row.
Every fact is read through the predicate that already owns it: the rotation test from
`rotation_claims`, the change class as dispatch reads it, the grant from the member record (read
once per call), the stall from `execution_stall`, and the open breaker from the dead-letter view.

`waiting` and `overdue` are served here because the threshold is this service's setting. A row is
`waiting` from its first read, so the lane can baseline it before it is `overdue`.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from orchestrator.clock import TransactionClock
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    ApprovedDecomposition,
    Claim,
    DecompositionProposal,
    Dependency,
    DispatchRecord,
    Event,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.execution.target_grant import (
    MemberRecordGrants,
    change_class_of,
    service_for_change_class,
)
from orchestrator.services.intake.bump_policy import DEPENDENCY_UPDATE
from orchestrator.services.intake.intake_reads import policy_refusals
from orchestrator.services.lifecycle.execution_stall import stalled_executions
from orchestrator.services.lifecycle.rotation_claims import is_rotation_revision, is_rotation_unit
from orchestrator.services.membership.members import list_members
from orchestrator.services.reporting.admission_queries import (
    proposed_breakdowns,
    staged_intakes,
    undecomposed_revisions,
)
from orchestrator.services.reporting.dead_letter import (
    RESOLVED_UNIT_STATES,
    _open_circuit_breakers,
)

SUBJECT_UNIT = "unit"
SUBJECT_STAGED_INTAKE = "staged-intake"
SUBJECT_PACKAGE = "package"
SUBJECT_PROPOSAL = "proposal"
_SENT_BACK = "revision_required"
# The unit states that wait on a person at an admission gate.
_GATE_STATES = frozenset(  # not-a-vocabulary: kernel enum members
    {WorkUnitState.DRAFT, WorkUnitState.AWAITING_APPROVAL, WorkUnitState.AWAITING_REVIEW}
)


@dataclass(frozen=True)
class ExecutionStatusRow:
    """`is_rotation` stays false on a row that isn't a unit, and `rotation_package` says the same
    of a proposal: the lane's early return for a rotation unit must not swallow a proposal's
    `policy_not_applied`, which no other lane reports. The other unit flags are empty there."""

    subject_kind: str
    subject_id: uuid.UUID
    state: str
    entered_at: datetime
    waiting: bool
    overdue: bool
    unit_id: uuid.UUID | None = None
    change_class: str | None = None
    is_rotation: bool = False
    standing: bool | None = None
    target_repository: str | None = None
    granted: bool | None = None
    latest_dispatch_status: str | None = None
    latest_dispatch_at: datetime | None = None
    has_claim: bool | None = None
    lease_lapsed: bool | None = None
    breaker_open: bool | None = None
    policy_not_applied: str | None = None
    proposed_by: str | None = None
    rotation_package: bool = False


def _is_standing(revision: WorkPackageRevision) -> bool:
    snapshot = revision.enforcement_snapshot
    fields = snapshot.get("profile_fields") if isinstance(snapshot, dict) else None
    return isinstance(fields, dict) and fields.get("standing") is True


def _claimed_since(newest_claim: datetime | None, record: DispatchRecord | None) -> bool:
    """A claim answers the latest dispatch only if it came after it; an older one is another run's.

    Compared by time: `runner_attempt` and a claim's attempt are independent counters.
    """
    if newest_claim is None:
        return False
    return record is None or newest_claim >= record.created_at


def _entered_at(session: Session, units: Sequence[WorkUnit]) -> dict[uuid.UUID, datetime]:
    """When each unit last moved into its current state. A same-state event isn't a move: a claim
    renewal, a dependency and an approval all write `to_state` as the state the unit is in."""
    entered: dict[uuid.UUID, datetime] = {}
    states = {unit.id: unit.state for unit in units}
    for subject_id, to_state, occurred_at in session.execute(
        select(Event.subject_id, Event.to_state, func.max(Event.occurred_at))
        .where(
            Event.subject_type == "work_unit",
            Event.subject_id.in_(list(states)),
            Event.from_state.is_distinct_from(Event.to_state),
        )
        .group_by(Event.subject_id, Event.to_state)
    ):
        if to_state == states[subject_id]:
            entered[subject_id] = occurred_at
    return entered


def _unit_rows(
    session: Session,
    units: Sequence[WorkUnit],
    grants: MemberRecordGrants,
    *,
    cutoff: datetime,
    stall_grace_seconds: int,
    failure_signature_threshold: int,
) -> list[ExecutionStatusRow]:
    revisions = {
        revision.id: revision
        for revision in session.scalars(
            select(WorkPackageRevision).where(
                WorkPackageRevision.id.in_({unit.work_package_revision_id for unit in units})
            )
        )
    }
    latest = {
        record.work_unit_id: record
        for record in session.scalars(
            select(DispatchRecord)
            .where(
                DispatchRecord.work_unit_id.in_([unit.id for unit in units]),
                DispatchRecord.status != "skipped",
            )
            .distinct(DispatchRecord.work_unit_id)
            .order_by(
                DispatchRecord.work_unit_id,
                DispatchRecord.created_at.desc(),
                DispatchRecord.runner_attempt.desc(),
            )
        )
    }
    newest_claim = {
        work_unit_id: acquired_at
        for work_unit_id, acquired_at in session.execute(
            select(Claim.work_unit_id, func.max(Claim.acquired_at))
            .where(Claim.work_unit_id.in_([unit.id for unit in units]))
            .group_by(Claim.work_unit_id)
        )
    }
    lapsed = {
        stalled.work_unit_id
        for stalled in stalled_executions(session, grace_seconds=stall_grace_seconds)
    }
    breakers = {
        entry.work_unit_id
        for entry in _open_circuit_breakers(session, failure_signature_threshold, grants)
    }
    entered = _entered_at(session, units)
    unit_ids = [unit.id for unit in units]
    blocked_by_dependency = set(
        session.scalars(
            select(Dependency.work_unit_id).where(
                Dependency.work_unit_id.in_(unit_ids),
                Dependency.status == "pending",
                Dependency.kind == "work_unit",
            )
        )
    )
    freed_at = {
        work_unit_id: resolved_at
        for work_unit_id, resolved_at in session.execute(
            select(Dependency.work_unit_id, func.max(Dependency.resolved_at))
            .where(Dependency.work_unit_id.in_(unit_ids), Dependency.resolved_at.is_not(None))
            .group_by(Dependency.work_unit_id)
        )
    }
    rows: list[ExecutionStatusRow] = []
    for unit in units:
        revision = revisions[unit.work_package_revision_id]
        envelope = normalize_authority(unit.authority)
        change_class = change_class_of(envelope, unit.required_capability) or None
        repository = envelope.target_repository or None
        record = latest.get(unit.id)
        entered_at = entered.get(unit.id, unit.created_at)
        if unit.state == WorkUnitState.DRAFT and unit.id in freed_at:
            entered_at = max(entered_at, freed_at[unit.id])
        waiting = unit.state in _GATE_STATES and not (
            unit.state == WorkUnitState.DRAFT and unit.id in blocked_by_dependency
        )
        rows.append(
            ExecutionStatusRow(
                subject_kind=SUBJECT_UNIT,
                subject_id=unit.id,
                unit_id=unit.id,
                state=unit.state,
                entered_at=entered_at,
                waiting=waiting,
                overdue=waiting and entered_at <= cutoff,
                change_class=change_class,
                is_rotation=is_rotation_unit(unit, revision),
                standing=_is_standing(revision),
                target_repository=repository,
                granted=bool(
                    repository
                    and change_class
                    and grants.is_granted(repository, service_for_change_class(change_class))
                ),
                latest_dispatch_status=record.status if record else None,
                latest_dispatch_at=record.created_at if record else None,
                has_claim=_claimed_since(newest_claim.get(unit.id), record),
                lease_lapsed=unit.id in lapsed,
                breaker_open=unit.id in breakers,
            )
        )
    return rows


def _sent_back(session: Session) -> Sequence[DecompositionProposal]:
    """Each proposal a person sent back and nothing has answered: the newest of its revision, on a
    revision with no active approval."""
    active_approved = select(ApprovedDecomposition.work_package_revision_id).where(
        ApprovedDecomposition.superseded_at.is_(None)
    )
    later = aliased(DecompositionProposal)
    return session.scalars(
        select(DecompositionProposal)
        .where(
            DecompositionProposal.state == _SENT_BACK,
            DecompositionProposal.work_package_revision_id.not_in(active_approved),
            ~select(later.id)
            .where(
                later.work_package_revision_id == DecompositionProposal.work_package_revision_id,
                later.proposal_number > DecompositionProposal.proposal_number,
            )
            .exists(),
        )
        .order_by(DecompositionProposal.decided_at, DecompositionProposal.id)
    ).all()


def _proposal_row(
    proposal: DecompositionProposal,
    revision: WorkPackageRevision,
    grants: MemberRecordGrants,
    refusal: dict[str, object] | None,
    cutoff: datetime,
) -> ExecutionStatusRow:
    """A sent-back proposal's clock runs from the decision. The bump fields are what
    `execution-watch` reads to leave a proposal the bump lane raises to that lane; an event is
    immutable, so a refusal counts only while the proposal it describes is undecided."""
    entered_at = proposal.decided_at or proposal.proposed_at
    snapshot = revision.enforcement_snapshot
    fields = snapshot.get("profile_fields") if isinstance(snapshot, dict) else None
    target = fields.get("target_repo") if isinstance(fields, dict) else None
    repository = target if isinstance(target, str) and target else None
    bump = revision.profile == DEPENDENCY_UPDATE
    return ExecutionStatusRow(
        subject_kind=SUBJECT_PROPOSAL,
        subject_id=proposal.id,
        state=proposal.state,
        entered_at=entered_at,
        waiting=True,
        overdue=entered_at <= cutoff,
        change_class=DEPENDENCY_UPDATE if bump else None,
        standing=_is_standing(revision),
        target_repository=repository if bump else None,
        granted=bool(
            bump
            and repository
            and grants.is_granted(repository, service_for_change_class(DEPENDENCY_UPDATE))
        ),
        policy_not_applied=(
            str(refusal["reason"]) if refusal and proposal.state != _SENT_BACK else None
        ),
        proposed_by=proposal.proposed_by,
        rotation_package=is_rotation_revision(revision),
    )


def _proposal_rows(
    session: Session,
    proposals: Sequence[DecompositionProposal],
    grants: MemberRecordGrants,
    cutoff: datetime,
) -> list[ExecutionStatusRow]:
    revisions = {
        revision.id: revision
        for revision in session.scalars(
            select(WorkPackageRevision).where(
                WorkPackageRevision.id.in_(
                    {proposal.work_package_revision_id for proposal in proposals}
                )
            )
        )
    }
    refusals = policy_refusals(session, [proposal.id for proposal in proposals])
    return [
        _proposal_row(
            proposal,
            revisions[proposal.work_package_revision_id],
            grants,
            refusals.get(proposal.id),
            cutoff,
        )
        for proposal in proposals
    ]


def _package_rows(
    session: Session, sent_back: Iterable[DecompositionProposal], cutoff: datetime
) -> list[ExecutionStatusRow]:
    """Undecomposed packages, past what only this lane excludes: a revision with a rejected
    proposal is declined work, and a sent-back one is already the proposal's row. A revision whose
    approval was superseded waits from that decision, not from its registration."""
    settled = set(
        session.scalars(
            select(DecompositionProposal.work_package_revision_id).where(
                DecompositionProposal.state == "rejected"
            )
        )
    ) | {proposal.work_package_revision_id for proposal in sent_back}
    superseded = {
        revision_id: decided_at
        for revision_id, decided_at in session.execute(
            select(
                ApprovedDecomposition.work_package_revision_id,
                func.max(ApprovedDecomposition.superseded_at),
            )
            .where(ApprovedDecomposition.superseded_at.is_not(None))
            .group_by(ApprovedDecomposition.work_package_revision_id)
        )
    }
    rows: list[ExecutionStatusRow] = []
    for revision in undecomposed_revisions(session):
        if revision.id in settled:
            continue
        decided = superseded.get(revision.id)
        entered_at = max(revision.registered_at, decided) if decided else revision.registered_at
        rows.append(
            ExecutionStatusRow(
                subject_kind=SUBJECT_PACKAGE,
                subject_id=revision.id,
                state="registered",
                entered_at=entered_at,
                waiting=True,
                overdue=entered_at <= cutoff,
            )
        )
    return rows


def execution_status(
    session: Session,
    *,
    stall_grace_seconds: int,
    failure_signature_threshold: int,
    stalled_approval_seconds: int,
) -> list[ExecutionStatusRow]:
    cutoff = TransactionClock().now(session) - timedelta(seconds=stalled_approval_seconds)
    grants = MemberRecordGrants(session, list_members(session))
    units = session.scalars(
        select(WorkUnit).where(WorkUnit.state.not_in(RESOLVED_UNIT_STATES)).order_by(WorkUnit.id)
    ).all()
    sent_back = _sent_back(session)
    return [
        *_unit_rows(
            session,
            units,
            grants,
            cutoff=cutoff,
            stall_grace_seconds=stall_grace_seconds,
            failure_signature_threshold=failure_signature_threshold,
        ),
        *(
            ExecutionStatusRow(
                subject_kind=SUBJECT_STAGED_INTAKE,
                subject_id=staged.id,
                state=staged.state,
                entered_at=staged.staged_at,
                waiting=True,
                overdue=staged.staged_at <= cutoff,
            )
            for staged in staged_intakes(session)
        ),
        *_package_rows(session, sent_back, cutoff),
        *_proposal_rows(session, (*proposed_breakdowns(session), *sent_back), grants, cutoff),
    ]
