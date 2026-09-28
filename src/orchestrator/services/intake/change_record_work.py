"""What work a change record caused, and whether it is done (ADR-0029).

A `work` change record is a human's decision that a bump should be built. The orchestrator holds
everything after that decision, keyed by revision id -- and until this module nothing could get
BACK: no listing route, no lookup by `change_record_id`, so the only read was
`GET /api/v1/package-intakes/{revision_id}` for a revision id the asker already had.

**THE RULE LIVES HERE BECAUSE THE UNITS DO.** The producer that retires the record could reduce
unit states itself, and that is exactly what this module exists to prevent: a reduction computed
in the producer is a reduction each future producer implements again, and they will not agree.
There is one today; the entry gates exist because there will be more.

**THE STATES TRAVEL WITH THE VERDICT, and that is not redundancy.** They are the evidence for it.
A caller that disagrees with the boolean can see which unit is holding the record open, so a wrong
answer is diagnosable rather than merely wrong.

**ABSENCE IS AN ORDINARY ANSWER, NEVER A 404.** A record with no revision is the common case -- it
is every record a person has approved and the carry has not reached yet -- and a record whose
revision has no units is the equally ordinary state between intake and a human approving a
breakdown. Both answer with `all_units_completed: false` and an empty list. Only the reader can
tell those two apart, which is why the revisions are reported rather than counted.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import WorkPackageRevision, WorkUnit

# The one state that means the work a record asked for was built.
#
# NARROW ON PURPOSE, and the neighbouring states are the reason. `failed` is not terminal for this
# question -- a failed unit may be granted another attempt -- so treating "no longer in flight" as
# done would report a record finished whose work is still live. `cancelled` is a human's decision
# and the matching record decision stays a human's. Anything wider here would be a machine
# deciding something nobody asked it to.
COMPLETED = "completed"


class UnitCompletion:
    """One unit of a revision the record caused, projected onto what the question needs."""

    __slots__ = ("unit_id", "unit_key", "revision_id", "state")

    def __init__(
        self,
        *,
        unit_id: uuid.UUID,
        unit_key: str,
        revision_id: uuid.UUID,
        state: str,
    ) -> None:
        self.unit_id = unit_id
        self.unit_key = unit_key
        self.revision_id = revision_id
        self.state = state


class ChangeRecordWork:
    """The answer: which revisions a record caused, their units, and whether all are done."""

    __slots__ = ("change_record_id", "revision_ids", "units", "all_units_completed")

    def __init__(
        self,
        *,
        change_record_id: int,
        revision_ids: tuple[uuid.UUID, ...],
        units: tuple[UnitCompletion, ...],
        all_units_completed: bool,
    ) -> None:
        self.change_record_id = change_record_id
        self.revision_ids = revision_ids
        self.units = units
        self.all_units_completed = all_units_completed


def work_for_change_record(session: Session, change_record_id: int) -> ChangeRecordWork:
    """Every unit of every revision this record caused, and the one derived verdict.

    THE VERDICT IS `units AND every state is completed`, and both halves are load-bearing. The
    emptiness clause is what stops a record with no work reading as a record whose work is done --
    `all()` over an empty sequence is True, which would retire every record the carry has not
    reached yet, i.e. exactly the population this whole lane exists to serve.

    MORE THAN ONE REVISION IS POSSIBLE and is handled by requiring all of them. Nothing constrains
    `change_record_id` to one revision: a package can be revised, and each revision carries the
    originating reference forward explicitly. Requiring every unit across every matching revision
    is the fail-closed reading -- a superseded revision holding a cancelled unit keeps the record
    open for a person, which is the direction this lane already reserves for a human.
    """
    revision_ids = tuple(
        session.scalars(
            select(WorkPackageRevision.id)
            .where(WorkPackageRevision.change_record_id == change_record_id)
            .order_by(WorkPackageRevision.id)
        )
    )
    if not revision_ids:
        return ChangeRecordWork(
            change_record_id=change_record_id,
            revision_ids=(),
            units=(),
            all_units_completed=False,
        )

    rows = session.scalars(
        select(WorkUnit)
        .where(WorkUnit.work_package_revision_id.in_(revision_ids))
        .order_by(WorkUnit.work_package_revision_id, WorkUnit.unit_key)
    )
    units = tuple(
        UnitCompletion(
            unit_id=row.id,
            unit_key=row.unit_key,
            revision_id=row.work_package_revision_id,
            state=row.state,
        )
        for row in rows
    )
    return ChangeRecordWork(
        change_record_id=change_record_id,
        revision_ids=revision_ids,
        units=units,
        all_units_completed=bool(units) and all(unit.state == COMPLETED for unit in units),
    )
