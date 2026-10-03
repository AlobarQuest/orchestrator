"""Whether a run may have started for a unit, read from its dispatch records.

A run may have started for any record whose trigger call was made: a `dispatched` record, and any
record carrying a `failure_signature`, which only a raised trigger call writes (status `failed`, or
`blocked` once the circuit breaker opens), since a failed call can still have reached GitHub. A
record refused at admission carries no signature and ran nothing. Superseding a decomposition
(ADR-0052) refuses a unit for which a run may have started, since that run may be about to
claim it. This module reads records; it never starts a run.
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import DispatchRecord


def a_run_may_have_started(session: Session, unit_id: uuid.UUID) -> bool:
    record = session.scalar(
        select(DispatchRecord.id)
        .where(
            DispatchRecord.work_unit_id == unit_id,
            or_(
                DispatchRecord.status == "dispatched",
                DispatchRecord.failure_signature.is_not(None),
            ),
        )
        .limit(1)
    )
    return record is not None
