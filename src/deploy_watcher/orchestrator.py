"""The confined orchestrator surface. One write, one read, checked before the transport.

ADR-0022. Until this increment the watcher did not speak to the orchestrator at all, and saying so
was part of its isolation test. What changed is that a rollout the watcher observes may belong to a
WORK UNIT, and the orchestrator's traceability chain filters its observation hop on
`subject_type="work_unit"` -- so the watcher, which is already there and already knows which
landing it is looking at, is the one producer positioned to fill it honestly.

THE WRITE IS THE OBSERVER ROLE'S WHOLE WRITE SURFACE, and the bound is repeated here in code for
the reason both sibling adapters repeat it: a second write becomes structurally unreachable rather
than merely unwritten. This program cannot dispatch, cannot adjudicate, cannot record evidence, and
cannot move a unit's state -- not because it does not try, but because a path that is not one of the
two below never leaves the process.

THE READ IS THE BINDING, and it is why there is a read at all. A landing commit's `SDS-Unit:`
trailer is written by factory-runner, which is the party whose compliance the observation would be
about; recording an observation against whatever unit a commit names would let a commit choose its
own subject. `…/history` carries the orchestrator's own `pr_merge` record of its own act, naming
the repository, the pull request and the commit -- so the unit is confirmed against the durable
record rather than against the claim. Reads are unconfined server-side for the OBSERVER role, so
this bound is the client's own and is the only one.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://sds.alobar.net"
USER_AGENT = "deploy-watcher/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 30.0

# The credential key id the orchestrator resolves the bearer against. A constant rather than a
# setting: an operator who could change it could only ever make the call unauthenticated.
OBSERVER_KEY_ID = "orchestrator-observer"

# A work unit id as the orchestrator stringifies it, which is how the trailer is written and how
# the API path spells it.
WORK_UNIT_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_WORK_UNIT_ID = re.compile(f"^{WORK_UNIT_ID}$")

OBSERVATIONS_ENDPOINT = "/api/v1/observations"

# Anchored, with the id shape spelled out, so the bound stays a bound: `…/{id}/anything-else` does
# not match, and neither does a prefix, a trailing slash, or a traversal.
_UNIT_HISTORY = re.compile(rf"^/api/v1/work-units/{WORK_UNIT_ID}/history$")


class OrchestratorError(Exception):
    """The orchestrator could not be asked, or refused in a way the pass cannot interpret."""


class ForbiddenEndpointError(OrchestratorError):
    """This program tried to reach a path it is not allowed to reach."""


def is_work_unit_id(value: object) -> bool:
    return isinstance(value, str) and _WORK_UNIT_ID.match(value) is not None


def is_allowed_write(path: str) -> bool:
    return path == OBSERVATIONS_ENDPOINT


def is_allowed_read(path: str) -> bool:
    return _UNIT_HISTORY.match(path) is not None


def history_path(work_unit_id: str) -> str:
    return f"/api/v1/work-units/{work_unit_id}/history"


class OrchestratorClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        credential_key_id: str = OBSERVER_KEY_ID,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # CONSTRUCTION IS GUARDED AS WELL AS THE REQUEST, because httpx refuses a malformed URL in
        # two different places: a control character is refused by `urlparse` here and now, while a
        # doubled dot or an over-long DNS label survives to IDNA encoding at request time. Both are
        # ordinary environment-variable typos, and a guard on one half is not a guard -- this
        # estate has written that down and then shipped the escape anyway.
        try:
            self._client = httpx.Client(
                base_url=base_url.rstrip("/"),
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Credential-Key-Id": credential_key_id,
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise OrchestratorError(
                f"the orchestrator address is unusable: {type(error).__name__}"
            ) from error

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrchestratorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def unit_history(self, work_unit_id: str) -> list[dict[str, Any]] | None:
        """One unit's events, or None when the ORCHESTRATOR says it holds no such unit.

        The 404 is a REAL ANSWER and the distinction is load-bearing: a landing claiming a unit
        that does not exist is a fact about the landing, while an unreachable orchestrator -- or a
        route the deployed image does not serve, which this estate has shipped -- is a measurement
        that did not happen. The distinguishing byte is in the BODY, read off production rather
        than off the handler: `main.py`'s handler is keyed on `code.endswith("_not_found")` but
        what reaches the wire is `{"error": {"code": "work_unit_not_found", …}}`. FastAPI's own
        404 is `{"detail": "Not Found"}` and a proxy's is not JSON at all, and neither matches.
        """
        path = history_path(work_unit_id)
        response = self._request("GET", path)
        if response.status_code == 404:
            if _is_domain_absence(response):
                return None
            raise OrchestratorError(f"the orchestrator has no route at {path}: 404")
        if response.status_code >= 400:
            raise OrchestratorError(f"the orchestrator rejected GET {path}: {response.status_code}")
        body = _json(response, f"GET {path}")
        if not isinstance(body, list):
            raise OrchestratorError(f"GET {path} answered with something that is not a list")
        return [event for event in body if isinstance(event, dict)]

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one observation. Every failure is this client's own error, never a raw status."""
        response = self._request("POST", OBSERVATIONS_ENDPOINT, json=payload)
        if response.status_code >= 400:
            # The status only. A rejection body echoes the command back, and a diagnostic that
            # prints what it was given is how a value that should not be in a transcript gets
            # into one.
            raise OrchestratorError(
                f"the orchestrator rejected POST {OBSERVATIONS_ENDPOINT}: {response.status_code}"
            )
        body = _json(response, f"POST {OBSERVATIONS_ENDPOINT}")
        if not isinstance(body, dict):
            raise OrchestratorError(
                f"POST {OBSERVATIONS_ENDPOINT} answered with something that is not an object"
            )
        return body

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Every request this client makes, and the ONE place the surface is enforced.

        The allowlists were checked at each call site first, and adversarial review showed both
        checks were tautologies — `is_allowed_write(OBSERVATIONS_ENDPOINT)` is a constant compared
        against itself, and `is_allowed_read(history_path(id))` cannot fail because `history_path`
        builds the only shape the pattern accepts. A guard that cannot fire is not a guard, and it
        reads as one. Here it is real: a method or a path this program acquires later inherits the
        refusal without anybody remembering, which is the property `change_manager.py` has because
        its paths interpolate an id.
        """
        allowed = is_allowed_read(path) if method == "GET" else is_allowed_write(path)
        if not allowed:
            raise ForbiddenEndpointError(f"the watcher may not {method} {path}")
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            # The exception TYPE only, and `ValueError` is caught alongside `HTTPError` because
            # httpx raises three unrelated families for a malformed URL: IDNA encoding of a
            # malformed host raises `UnicodeError`, which is a `ValueError` and is neither an
            # `HTTPError` nor an `InvalidURL`. A doubled dot or an over-long DNS label in a base
            # URL is an ordinary environment-variable typo, and this estate has already shipped
            # that escape twice.
            raise OrchestratorError(
                f"the orchestrator is unreachable for {method} {path}: {type(error).__name__}"
            ) from error
        except (httpx.InvalidURL, ValueError) as error:
            raise OrchestratorError(
                f"the orchestrator address is unusable for {method} {path}: {type(error).__name__}"
            ) from error


def _json(response: httpx.Response, what: str) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise OrchestratorError(f"{what} answered with something that is not JSON") from error


def _is_domain_absence(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    error = body.get("error") if isinstance(body, dict) else None
    return isinstance(error, dict) and str(error.get("code", "")).endswith("_not_found")
