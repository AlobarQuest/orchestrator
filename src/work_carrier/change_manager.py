"""The confined change-manager surface for the carry. ONE path, and it is a READ.

The carry lists approved work proposals. It can reach nothing else, and unlike every other
client this repository holds against this service it has NO write path at all -- which is what
makes "an item the carry cannot prepare is left exactly as it was" a structural property rather
than a promise. There is no code here that could write, so no branch that could be reached
wrongly, and no ordering in which a partial failure leaves the record changed.

That is unchanged by ADR-0027, which gave the carry a write to the ORCHESTRATOR and none here.
The asymmetry is the point: a carry that could approve the proposal it is carrying would be a
system asking itself for permission, and the human decision this whole lane exists to serve is
the one recorded in this service.

That bound is asserted here as intent, not as the control. change-manager's own scope table is
the control, and `read` is the scope this program's credential should hold. This is what makes a
mistake in this program fail before a request leaves it, and what keeps the bound true in a
development deployment where the narrow secrets are unset.

**IT MUST NAME THE PIPELINE, and the reason is the whole safety argument one repository over.**
`GET /api/items` WITHHOLDS a proposed source from any caller that does not name one, because the
04:00 change-window executor calls it without a source filter and hands what comes back to an
LLM agent holding production Coolify tools. So the same one-line asymmetry that keeps work
proposals away from that agent is what this program has to opt into, and a sweep that forgot the
parameter would read an empty list and report a clean pass having carried nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx

DEFAULT_BASE_URL: Final = "https://change-mgr.alobar.net"
USER_AGENT: Final = "work-carrier/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0

# What change-manager calls the pipeline these records arrive on (`app/sources.py::WORK_SOURCE`).
WORK_SOURCE: Final = "work"
# The status a human moved them to. Read as a QUERY here rather than filtered from the rows,
# which is the opposite of what `services/change_record.py` does and correct for the opposite
# reason: that module has to tell "pending" from "absent", while this one is a queue drain and a
# record that is not approved is simply not this program's business yet.
APPROVED: Final = "approved"

_ITEMS: Final = "/api/items"


class ChangeManagerError(Exception):
    """change-manager could not be asked, or answered in a way this pass cannot interpret."""


class ForbiddenEndpointError(ChangeManagerError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed(path: str) -> bool:
    return path == _ITEMS


@dataclass(frozen=True)
class WorkRecord:
    """One approved work proposal, projected onto what the carry needs.

    The locator is three separate REQUIRED fields rather than one parsed string, because
    change-manager stores them that way for the reason ADR-0019 gives about acceptance criteria:
    the ingress refuses a record that lacks them, and a refusal needs a field to be absent from.
    A row here that lacks one is a record from a source this program should not have been served.
    """

    change_record_id: int
    package_id: str
    package_revision: int
    package_source_repository: str
    reasoning: str
    decided_by: str | None


class WorkRecordSource(Protocol):
    def approved_work(self) -> tuple[WorkRecord, ...]: ...


class HttpWorkRecordSource:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._client = client

    def approved_work(self) -> tuple[WorkRecord, ...]:
        body = self._get(_ITEMS, {"status": APPROVED, "source": WORK_SOURCE})
        if not isinstance(body, list):
            raise ChangeManagerError("change-manager did not answer the listing with a list")
        return tuple(_record(row) for row in body if isinstance(row, dict))

    def _get(self, path: str, params: dict[str, str]) -> Any:
        if not is_allowed(path):
            raise ForbiddenEndpointError(f"{path} is not a path this program may reach")
        try:
            client = self._client or httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {self._token}"},
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # `httpx` raises at the CONSTRUCTOR for some malformed URLs and at request time for
            # others, and the third family is a `ValueError`: IDNA encoding of a malformed host
            # raises `UnicodeError`, which is neither an `HTTPError` nor an `InvalidURL`. A
            # doubled dot or an over-long DNS label in an environment variable is an ordinary
            # typo and must be a finding, not a traceback.
            raise ChangeManagerError(f"change-manager base URL is unusable: {error}") from error
        try:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise ChangeManagerError(f"change-manager could not be read: {error}") from error
        finally:
            if self._client is None:
                client.close()


def _record(row: dict[str, Any]) -> WorkRecord:
    """One row, or a refusal. Every field the carry uses is required and typed.

    A row missing a locator field is not a record this program can act on, and guessing at one
    would produce an intake naming a package nobody chose. `source` is re-checked even though it
    was a query parameter, because FastAPI ignores an unknown query parameter silently -- a
    renamed parameter would hand this program another pipeline's records with no error anywhere.
    """
    if row.get("source") != WORK_SOURCE:
        raise ChangeManagerError(
            f"change-manager served a '{row.get('source')}' record to a query for '{WORK_SOURCE}'"
        )
    if row.get("status") != APPROVED:
        raise ChangeManagerError(
            f"change-manager served a '{row.get('status')}' record to a query for '{APPROVED}'"
        )
    change_record_id = row.get("id")
    package_id = row.get("package_id")
    package_revision = row.get("package_revision")
    repository = row.get("package_source_repository")
    if not isinstance(change_record_id, int) or isinstance(change_record_id, bool):
        raise ChangeManagerError("a change record carries no usable id")
    if not isinstance(package_id, str) or not package_id:
        raise ChangeManagerError(f"change record {change_record_id} names no package")
    if not isinstance(package_revision, int) or isinstance(package_revision, bool):
        raise ChangeManagerError(f"change record {change_record_id} names no package revision")
    if not isinstance(repository, str) or not repository:
        raise ChangeManagerError(f"change record {change_record_id} names no package repository")
    return WorkRecord(
        change_record_id=change_record_id,
        package_id=package_id,
        package_revision=package_revision,
        package_source_repository=repository,
        reasoning=str(row.get("reasoning") or ""),
        decided_by=row.get("decided_by") if isinstance(row.get("decided_by"), str) else None,
    )
