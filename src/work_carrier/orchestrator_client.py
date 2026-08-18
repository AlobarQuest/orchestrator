"""The confined orchestrator surface for the carry. ONE path, and it is the only write it has.

The carry registers the package intake an approved change record asks for, and it can reach
nothing else -- not a breakdown, not an approval, not a work unit. The orchestrator's own
registrar guard is the control; this is the statement of intent that makes a mistake in this
program fail before a request leaves it.

**THE CARRY DECIDES NOTHING BY WRITING.** Every rule about what may be registered is evaluated
inside the orchestrator, in the transaction that records it: that the package is approved, that
its hash matches its lineage approval, that the actor may register at all, and -- ADR-0027 --
that a machine-registered intake names the change record that caused it. This program relays a
payload it did not compose and a decision it did not make, which is the whole reason it is
allowed to run unattended.

**IT DOES NOT VERIFY THE CHANGE RECORD, AND NEITHER DOES THE ORCHESTRATOR.** The reference
travels on trust: asking change-manager inside the registration transaction would make a foreign
service's outage a refusal to record work a person had already approved. What stands behind the
reference is that this program reads only APPROVED records -- `change_manager.approved_work`
names the status as a query and re-checks it on every row -- so the check lives here, before the
call, and nowhere else. A reader must not take the orchestrator's guard for validation of it.
"""

from __future__ import annotations

from typing import Any, Final

import httpx

DEFAULT_BASE_URL: Final = "https://sds.alobar.net"
USER_AGENT: Final = "work-carrier/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 60.0

_INTAKES: Final = "/api/v1/package-intakes"


class OrchestratorError(Exception):
    """The orchestrator could not be asked, or refused in a way this pass cannot interpret."""


class ForbiddenEndpointError(OrchestratorError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed_write(path: str) -> bool:
    return path == _INTAKES


def _detail(response: httpx.Response) -> str:
    """The orchestrator's own explanation, bounded. Never the whole body, never headers.

    A `DomainError` reaches the wire NESTED under `error`; FastAPI's own validation failures
    arrive as `detail`. A reader written from one shape matches neither the other nor a proxy's
    HTML, and this estate has already recorded that trap.
    """
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:400]
        if payload.get("detail"):
            return str(payload["detail"])[:400]
    return f"HTTP {response.status_code}"


class OrchestratorClient:
    def __init__(
        self,
        token: str,
        key_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            self._client = httpx.Client(
                base_url=base_url.rstrip("/"),
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Credential-Key-Id": key_id,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        except (httpx.InvalidURL, ValueError) as error:
            # CONSTRUCTION raises for some malformed URLs and request time for others: a control
            # character is refused here by `urlparse`, while a doubled dot or an over-long DNS
            # label survives until IDNA encoding at `request`. Guarding one half leaves an
            # environment-variable typo crashing the pass with a traceback instead of reporting.
            raise OrchestratorError(
                f"the orchestrator base URL is unusable: {type(error).__name__}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrchestratorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def register_intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Register the prepared intake. The payload is the emitter's, byte for byte.

        Nothing here edits it. `orchestrator emit-intake-payload` produced it -- the same command
        whose output a human pastes into the form -- so there is no second composition to
        diverge from the one this estate documents, including the idempotency key, which is
        derived from the record so a second pass over an unchanged queue is a replay.
        """
        if not is_allowed_write(_INTAKES):  # pragma: no cover - the allowlist is a constant
            raise ForbiddenEndpointError(f"the carry may not POST {_INTAKES}")
        return self._post(_INTAKES, payload)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """The ONE way anything leaves this process, guard first."""
        if not is_allowed_write(path):
            raise ForbiddenEndpointError(f"the carry may not POST {path}")
        try:
            response = self._client.post(path, json=payload)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The exception TYPE only. An httpx error carries the request, and a diagnostic that
            # prints what it was given is how a bearer token reaches a transcript.
            raise OrchestratorError(
                f"the orchestrator is unreachable for POST {path}: {type(error).__name__}"
            ) from None
        if response.status_code >= 400:
            hint = " -- the credential is not the system one" if response.status_code == 403 else ""
            raise OrchestratorError(
                f"the orchestrator answered {response.status_code} for {path}{hint}: "
                f"{_detail(response)}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise OrchestratorError("the intake response was not JSON") from error
        if not isinstance(body, dict):
            raise OrchestratorError("the intake response was not an object")
        return body
