"""The ledger's HTTP client for the orchestrator. Its write surface is enforced HERE, in code.

ONE endpoint: `POST /api/v1/observations`. That is the OBSERVER role's entire write surface
(WS-P3.6 Increment 1), and putting the same bound in the client means a second write is
structurally unreachable rather than merely unwritten -- the shape both sibling adapters use.

RECORDING still reads nothing from the orchestrator, and that has not changed: what it records
comes from GitHub, and re-running is made safe by the observation's own idempotency rather than
by first asking what is already there.

AUDITING does read, and this contract changed in WS-P3.6 Increment 3 rather than drifting. The
permissive-drift detector re-evaluates what the LEDGER recorded -- if it re-derived the landings
from GitHub instead it would be a second recorder, not an audit of the first. The read surface is
bounded the same way the write surface is, to the paths it needs; OBSERVER reads are deliberately
unconfined server-side, so this bound is the client's own and is the only one.

**WS-P3.7 Increment 5 WIDENED THE READ SURFACE FROM ONE PATH TO THREE, deliberately.** ADR-0020
lets the factory land its own pull request, and a landing recorded on that basis says a work unit's
criteria were met. Read from GitHub alone that is the RUNNER'S OWN ASSERTION re-recorded -- the
pull request body and the commit trailers are both written by the thing whose compliance is in
question -- which is the opposite of what a ledger is for. So the audit asks the orchestrator, and
the two paths it needs are:

* `…/evidence-pack` -- the unit's state, its authority fingerprint, and the orchestrator's own
  composed answer to "was every required criterion decided by the verifier, from evidence the
  orchestrator observed"; and
* `…/history` -- the unit's events, whose `pr_merge` payload names the repository, the pull
  request, the head and the merge commit. That is the BINDING: it is what makes a landing this
  unit's landing rather than any completed unit the commit chose to name.

**Two paths, not the one the increment's handoff proposed, and the reason is measured rather than
preferred.** The evidence pack's event projection carries `action` and `actor_id` and drops the
`payload`, so it cannot name the pull request; the history carries the payload but no adjudication
is subject to a unit, so it cannot answer who decided the criteria. Each path answers exactly half,
and a basis checked on half is a basis that admits a landing claiming a unit it never touched.
`…/pr-merge-admission` would answer both in one call and is deliberately NOT used: it evaluates
whether the landing may happen NOW, and re-asking it later manufactures findings out of ordinary
change -- a superseded approval, a re-classified repository. The durable record does not drift.

Both additions are GET, both are authentication-only server-side, and neither can change anything.
**The WRITE surface is untouched: one endpoint, still.**
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from landing_ledger.model import WORK_UNIT_ID

OBSERVATIONS_ENDPOINT = "/api/v1/observations"

# The two per-unit reads the ADR-0020 audit needs. A pattern rather than a literal because the
# path carries a unit id -- anchored, and with the id shape spelled out, so that the bound stays a
# bound: `…/{id}/anything-else` does not match, and neither does a prefix or a trailing slash.
_UNIT_READS = re.compile(rf"^/api/v1/work-units/{WORK_UNIT_ID}/(evidence-pack|history)$")


class LedgerWriteError(RuntimeError):
    pass


class ForbiddenEndpointError(LedgerWriteError):
    """The ledger attempted a write outside its recording-only surface."""


class ForbiddenReadError(LedgerWriteError):
    """The ledger attempted a read outside the one path its audit needs."""


def _raise_missing_route(path: str) -> Any:
    raise LedgerWriteError(f"orchestrator has no route at {path}: 404")


def is_allowed_write(path: str) -> bool:
    return path == OBSERVATIONS_ENDPOINT


def is_allowed_read(path: str) -> bool:
    return path == OBSERVATIONS_ENDPOINT or _UNIT_READS.match(path) is not None


def evidence_pack_path(work_unit_id: str) -> str:
    return f"/api/v1/work-units/{work_unit_id}/evidence-pack"


def history_path(work_unit_id: str) -> str:
    return f"/api/v1/work-units/{work_unit_id}/history"


class OrchestratorClient:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": credential_key_id,
            },
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrchestratorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(OBSERVATIONS_ENDPOINT, payload)

    def read_landings(self, repository: str) -> list[dict[str, Any]]:
        return self.get(
            OBSERVATIONS_ENDPOINT,
            observation_type="landing",
            subject_type="repo",
            subject_reference=repository,
        )

    def read_evidence_pack(self, work_unit_id: str) -> dict[str, Any] | None:
        """One unit's evidentiary record, or None when the orchestrator has no such unit.

        The 404 is a REAL ANSWER, not an error, and the caller depends on the distinction: a
        landing claiming a unit that does not exist is a finding, while an unreachable
        orchestrator is a measurement that did not happen. Mirrors `GitHubReader`, which returns
        None on 404 for the same reason.
        """
        body = self._read(evidence_pack_path(work_unit_id))
        if body is None:
            return None
        if not isinstance(body, dict):
            raise LedgerWriteError("orchestrator answered the evidence pack with a non-object body")
        return dict(body)

    def read_unit_history(self, work_unit_id: str) -> list[dict[str, Any]] | None:
        body = self._read(history_path(work_unit_id))
        if body is None:
            return None
        if not isinstance(body, list):
            raise LedgerWriteError("orchestrator answered the unit history with a non-list body")
        return list(body)

    def get(self, path: str, **params: str) -> list[dict[str, Any]]:
        body = self._read(path, **params)
        if not isinstance(body, list):
            raise LedgerWriteError(f"orchestrator answered GET {path} with a non-list body")
        return list(body)

    @staticmethod
    def _is_domain_absence(response: httpx.Response) -> bool:
        """Did the ORCHESTRATOR say this subject does not exist, or did something else say 404?

        The shape is NESTED and was read off production rather than off the handler, which is what
        this repository's own rules require and what caught it: `main.py`'s handler is keyed on
        `error.code.endswith("_not_found")`, but what reaches the wire is
        `{"error": {"code": "work_unit_not_found", …}}`. FastAPI's own 404 is `{"detail": "Not
        Found"}`, and a proxy's is not JSON at all -- neither matches, which is the point.
        """
        try:
            body = response.json()
        except ValueError:
            return False
        error = body.get("error") if isinstance(body, dict) else None
        return isinstance(error, dict) and str(error.get("code", "")).endswith("_not_found")

    def _read(self, path: str, **params: str) -> Any:
        if not is_allowed_read(path):
            raise ForbiddenReadError(f"the ledger may not read {path}")
        try:
            response = self._client.request("GET", path, params=params or None)
        except httpx.HTTPError as error:
            raise LedgerWriteError(
                f"orchestrator is unreachable for GET {path}: {type(error).__name__}"
            ) from error
        if response.status_code == 404:
            # A 404 is an ANSWER only when the ORCHESTRATOR says so. It answers a missing unit with
            # its own `DomainError` body carrying `code: "work_unit_not_found"` (`main.py`), while
            # a wrong base URL, a proxy, or a route absent from the deployed image answers the
            # framework's bare `{"detail": "Not Found"}` -- and this estate has shipped a release
            # whose routes production did not serve. Reading every 404 as "no such unit" would turn
            # a misconfiguration into a finding accusing the orchestrator of losing units it holds,
            # for every factory landing at once. The distinguishing byte is in the body, so read it.
            return None if self._is_domain_absence(response) else _raise_missing_route(path)
        if response.status_code >= 400:
            raise LedgerWriteError(f"orchestrator rejected GET {path}: {response.status_code}")
        return response.json()

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not is_allowed_write(path):
            raise ForbiddenEndpointError(f"the ledger may not write to {path}")
        try:
            response = self._client.request("POST", path, json=payload)
        except httpx.HTTPError as error:
            # An unreachable orchestrator raises before any status code exists. Same reasoning as
            # the reader's: it must become this client's own error, or one landing's write takes
            # the whole pass down.
            raise LedgerWriteError(
                f"orchestrator is unreachable for POST {path}: {type(error).__name__}"
            ) from error
        if response.status_code >= 400:
            # The status only. A rejection body echoes the command back, and a diagnostic that
            # prints what it was given is how a value that should not be in a transcript gets
            # into one.
            raise LedgerWriteError(f"orchestrator rejected POST {path}: {response.status_code}")
        return dict(response.json())
