"""The confined orchestrator surface for the carry. TWO paths: one read, then the one write.

The carry asks what an approved change record has already caused, and registers the package
intake it asks for when the answer is nothing. It can reach nothing else -- not a breakdown, not
an approval, not a work unit. The orchestrator's own registrar guard is the control; this is the
statement of intent that makes a mistake in this program fail before a request leaves it.

**THE READ CAME SECOND AND IT IS WHY THE FIRST LINE MOVED.** Until 2026-08-21 this module was
write-only and said so. A read that widens the surface a module asserts about ITSELF is a
different thing from reaching into one another program owns -- so the claim moves with the code
rather than being softened into prose, because this repository has twice been bitten by a
docstring asserting a shape one increment had already falsified.

**THE READ IS NOT A SECOND VERDICT.** `carried_revisions` relays the revision ids the
orchestrator serves and tests whether there are any; it does not reduce unit states, decide
completeness, or interpret them. Its sibling in `work_watcher` deliberately relays
`all_units_completed` rather than computing it, because which states count as done is a
judgment; whether a record has caused a revision at all is the presence of rows, and there is
nothing for a second implementation to disagree with.

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

import re
from typing import Any, Final

import httpx

DEFAULT_BASE_URL: Final = "https://sds.alobar.net"
USER_AGENT: Final = "work-carrier/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 60.0

_INTAKES: Final = "/api/v1/package-intakes"
# ADR-0029's route, read here for a different question than the watcher asks of it. Anchored and
# bounded the same way, so a record id that is not one cannot compose a path to somewhere else.
_WORK: Final = re.compile(r"^/api/v1/change-records/[0-9]{1,9}/work$")


class OrchestratorError(Exception):
    """The orchestrator could not be asked, or refused in a way this pass cannot interpret."""


class IntakeRefused(OrchestratorError):
    """The orchestrator refused. A fact about the subject, not a broken tool.

    It CARRIES THE REFUSAL CODE as well as the message, because not every refusal means the same
    thing to a reader and the message is prose that will be reworded. A `DomainError` reaches the
    wire nested under `error`, so classifying refusals apart needs the code read from there.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class ForbiddenEndpointError(OrchestratorError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed_write(path: str) -> bool:
    return path == _INTAKES


def is_allowed_read(path: str) -> bool:
    return _WORK.match(path) is not None


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


def _hint(response: httpx.Response) -> str:
    """A pointer at the likeliest misconfiguration, keyed on the REFUSAL rather than the status.

    A role refusal from this route is `intake_registrar_invalid`, and it is a 403 -- but the
    adjacent refusal for a machine that named no change record is a 409, and so is a redirect's
    neighbour. Keying on the code rather than on 403 keeps the hint attached to the case it was
    written for even when the status for that case moves.
    """
    if _error_code(response) in {"intake_registrar_invalid", "role_forbidden"}:
        return " -- the credential is not the system one"
    if 300 <= response.status_code < 400:
        return " -- a redirect, so the request did not reach the app: check the proxy routing"
    return ""


def _error_code(response: httpx.Response) -> str:
    """The refusal's own code, read from where a `DomainError` actually puts it: NESTED."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return ""


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

    def carried_revisions(self, change_record_id: int) -> tuple[str, ...]:
        """The package revisions this change record has already caused, relayed unchanged.

        EMPTY MEANS NOT YET CARRIED, and it is an ordinary answer rather than an absence: a
        record a person approved this morning, and a record id the orchestrator has never seen,
        both answer 200 with an empty list. So there is no not-found case to tell apart here,
        which is why that route was built to answer the way it does.

        **A `revision_ids` THAT IS NOT A LIST IS A FINDING, NEVER "NOT CARRIED".** A FastAPI
        `response_model` DROPS every key it does not declare, so a field that stopped being
        served arrives as absence rather than as an error -- and absence read as "not carried"
        would put this lane silently back to registering over its own work every morning, which
        is the defect this read exists to end. The watcher is deliberately lenient on the same
        key because there it is decoration for a reader; here it IS the answer.
        """
        body = self._get(f"/api/v1/change-records/{change_record_id}/work")
        revisions = body.get("revision_ids")
        if not isinstance(revisions, list):
            raise OrchestratorError(
                f"the orchestrator did not say what change record {change_record_id} has "
                "already caused"
            )
        return tuple(str(revision) for revision in revisions)

    def _get(self, path: str) -> dict[str, Any]:
        """The ONE way a question leaves this process, guard first.

        The write's twin, and the guards are separate rather than one path check: a read
        allowlist that admitted the write route, or the reverse, would be a surface nobody
        decided to open. Neither predicate can satisfy the other.
        """
        if not is_allowed_read(path):
            raise ForbiddenEndpointError(f"the carry may not GET {path}")
        try:
            response = self._client.get(path)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The exception TYPE only, for the reason the write path gives: an httpx error
            # carries the request, and a diagnostic that prints what it was given is how a
            # bearer token reaches a transcript.
            raise OrchestratorError(
                f"the orchestrator is unreachable for GET {path}: {type(error).__name__}"
            ) from None
        if not 200 <= response.status_code < 300:
            # ANY non-2xx, so a redirect is named as one. This route is not behind the
            # forward-auth chain the intake route sits behind, but a proxy is a thing that gets
            # reconfigured, and `>= 400` would hand a redirect body to `response.json()` and
            # report a routing refusal as a response-encoding fault.
            raise OrchestratorError(
                f"the orchestrator answered {response.status_code} for GET {path}{_hint(response)}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise OrchestratorError(f"the answer to GET {path} was not JSON") from error
        if not isinstance(body, dict):
            raise OrchestratorError(f"the answer to GET {path} was not an object")
        return body

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
        if not 200 <= response.status_code < 300:
            # ANY non-2xx, not `>= 400`, and the difference is the production case rather than a
            # pedantic one. `POST /api/v1/package-intakes` sits behind an Alobar ID forward-auth
            # router at the proxy, so a machine bearer arriving there draws a **302** to
            # id.alobar.net -- measured, not inferred. httpx does not follow redirects, so a
            # `>= 400` check waves that through to `response.json()`, which dies on the redirect
            # body and reports "the intake response was not JSON": a routing refusal disguised
            # every morning as a response-encoding fault. Naming the status is what makes the
            # answer point at the proxy.
            raise IntakeRefused(
                f"the orchestrator answered {response.status_code} for {path}{_hint(response)}: "
                f"{_detail(response)}",
                _error_code(response),
            )
        try:
            body = response.json()
        except ValueError as error:
            raise OrchestratorError("the intake response was not JSON") from error
        if not isinstance(body, dict):
            raise OrchestratorError("the intake response was not an object")
        return body
