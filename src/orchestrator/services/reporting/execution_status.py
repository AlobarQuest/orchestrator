"""The execution-status read: one row per unit still in play, for the execution alert lane.

Derived live from the source tables; it writes nothing. Units in `completed` or `cancelled` have
reached the end of their story and have no row. Every fact is read through the predicate that
already owns it: the rotation test from `rotation_claims`, the change class as dispatch reads it,
the grant from the member record (read once per call), the stall from `execution_stall`, and the
open breaker from the dead-letter view.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import Claim, DispatchRecord, WorkPackageRevision, WorkUnit
from orchestrator.services.execution.dispatch import _change_class
from orchestrator.services.execution.target_grant import (
    MemberRecordGrants,
    service_for_change_class,
)
from orchestrator.services.lifecycle.execution_stall import stalled_executions
from orchestrator.services.lifecycle.rotation_claims import is_rotation_unit
from orchestrator.services.membership.members import list_members
from orchestrator.services.reporting.dead_letter import (
    RESOLVED_UNIT_STATES,
    _open_circuit_breakers,
)


@dataclass(frozen=True)
class ExecutionStatusRow:
    unit_id: uuid.UUID
    state: str
    change_class: str | None
    is_rotation: bool
    standing: bool
    target_repository: str | None
    granted: bool
    latest_dispatch_status: str | None
    latest_dispatch_at: datetime | None
    has_claim: bool
    lease_lapsed: bool
    breaker_open: bool


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


def execution_status(
    session: Session, *, stall_grace_seconds: int, failure_signature_threshold: int
) -> list[ExecutionStatusRow]:
    units = session.scalars(
        select(WorkUnit).where(WorkUnit.state.not_in(RESOLVED_UNIT_STATES)).order_by(WorkUnit.id)
    ).all()
    if not units:
        return []
    revisions = {
        revision.id: revision
        for revision in session.scalars(
            select(WorkPackageRevision).where(
                WorkPackageRevision.id.in_({unit.work_package_revision_id for unit in units})
            )
        )
    }
    grants = MemberRecordGrants(session, list_members(session))
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
        entry.work_unit_id for entry in _open_circuit_breakers(session, failure_signature_threshold)
    }
    rows: list[ExecutionStatusRow] = []
    for unit in units:
        revision = revisions[unit.work_package_revision_id]
        envelope = normalize_authority(unit.authority)
        change_class = _change_class(envelope, unit.required_capability) or None
        repository = envelope.target_repository or None
        record = latest.get(unit.id)
        rows.append(
            ExecutionStatusRow(
                unit_id=unit.id,
                state=unit.state,
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
