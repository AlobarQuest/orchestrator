"""Whether a run may have started for a unit, read from its dispatch records.

A record in `dispatched` or `failed` may have started a run: a failed trigger call can still have
reached GitHub. `skipped` and `blocked` records ran nothing. Superseding a decomposition
(ADR-0052) refuses a unit for which a run may have started, since that run may be about to
claim it. This module reads records; it never starts a run.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import DispatchRecord

_MAY_HAVE_STARTED = ("dispatched", "failed")  # not-a-vocabulary: subset of DISPATCH_RECORD_STATUSES


def a_run_may_have_started(session: Session, unit_id: uuid.UUID) -> bool:
    record = session.scalar(
        select(DispatchRecord.id)
        .where(
            DispatchRecord.work_unit_id == unit_id,
            DispatchRecord.status.in_(_MAY_HAVE_STARTED),
        )
        .limit(1)
    )
    return record is not None
