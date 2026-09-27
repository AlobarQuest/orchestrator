"""The whole work pipeline, any status: the staleness report's listing (ruling B1, 2026-09-27).

**A SUBCLASS OF THE CARRY'S SOURCE, NOT A NEW METHOD ON IT.** The carry's client is asserted to
have exactly one public method, because the carry must be unable to decide the work it carries.
This listing is the watcher's question, so it lives here, and it inherits the carry's guarded
`_get` -- one allowlisted path (`/api/items`), one client construction, one error shape.

**THE STATUS IS NOT SENT.** change-manager applies `status` as a SQL filter, which makes a pending
record indistinguishable from one that does not exist; every status is projected and the caller
branches on it. The source IS sent and re-checked on every row, for the same reason the carry's
parse re-checks it: FastAPI ignores an unknown query parameter silently.

Every key read here is already one of `work_carrier.change_manager.RECORD_FIELDS`, so the
cross-repo field check vets this parse without a second declaration.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from work_carrier.change_manager import (
    WORK_SOURCE,
    ChangeManagerError,
    HttpWorkRecordSource,
)

_ITEMS = "/api/items"


class PipelineRecord(NamedTuple):
    change_record_id: int
    status: str
    package_id: str
    package_revision: int
    reasoning: str


class PipelineListing(HttpWorkRecordSource):
    def work_pipeline(self) -> tuple[PipelineRecord, ...]:
        body = self._get(_ITEMS, {"source": WORK_SOURCE})
        if not isinstance(body, list):
            raise ChangeManagerError("change-manager did not answer the listing with a list")
        return tuple(_pipeline_record(row) for row in body if isinstance(row, dict))


def _pipeline_record(row: dict[str, Any]) -> PipelineRecord:
    """One pipeline row, or a refusal."""
    if row.get("source") != WORK_SOURCE:
        raise ChangeManagerError(
            f"change-manager served a '{row.get('source')}' record to a query for '{WORK_SOURCE}'"
        )
    change_record_id = row.get("id")
    status = row.get("status")
    package_id = row.get("package_id")
    package_revision = row.get("package_revision")
    if not isinstance(change_record_id, int) or isinstance(change_record_id, bool):
        raise ChangeManagerError("a change record carries no usable id")
    if not isinstance(status, str) or not status:
        raise ChangeManagerError(f"change record {change_record_id} carries no status")
    if not isinstance(package_id, str) or not package_id:
        raise ChangeManagerError(f"change record {change_record_id} names no package")
    if not isinstance(package_revision, int) or isinstance(package_revision, bool):
        raise ChangeManagerError(f"change record {change_record_id} names no package revision")
    return PipelineRecord(
        change_record_id=change_record_id,
        status=status,
        package_id=package_id,
        package_revision=package_revision,
        reasoning=str(row.get("reasoning") or ""),
    )
