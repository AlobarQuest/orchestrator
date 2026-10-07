"""The confined orchestrator surface for this producer. ONE path, and no reads at all.

`POST /api/v1/observations` is the OBSERVER role's entire write surface (WS-P3.6 Increment 1), and
the bound is asserted here as well so a mistake in this program fails before a request leaves it.

**THIS PRODUCER COULD PREVIOUSLY REACH THE ORCHESTRATOR NOT AT ALL**, and that was a property its
isolation test asserted in those words. G1+G2 narrows the property rather than dropping it: what
made it worth having is that the orchestrator learns about this work through the carry, from a
record a person APPROVED, so a producer able to register an intake would be the machine approving
its own proposal. Filing an observation is the opposite act -- it states a fact and decides
nothing, and the credential it uses cannot transition a unit, land a pull request or create work.

**THE CREDENTIAL IS THE SHARED OBSERVER BEARER, NOT A FOURTH NAME FOR IT.** `orchestrator-observer`
is the one credential every observe-and-report producer holds by design, and the three lanes that
already hold it read it from the same environment variables this module's caller reads. A
lane-prefixed spelling would be a second name for one credential, which is the estate's own
N-copies defect in a different vocabulary.

The transport and its guards are the estate's shared confined client (ADR-0050); what is this
program's own is the one path it may reach, its exception classes and their wording.
"""

from __future__ import annotations

import re
from typing import Any, Final

import httpx

from estate_clients.confined import (
    ConfinedClient,
    TransportFailure,
    base_url_problem,
    error_code,
)

# NO DEFAULT BASE URL, unlike this program's change-manager client one module over. The two
# sibling observer lanes require the URL from the environment and so does this: the host would
# otherwise be a second copy of a value the launcher already supplies, and naming it here would
# spend a clause of this program's own isolation guard, which asserts that the orchestrator's
# production host appears nowhere in this package.
OBSERVATIONS_ENDPOINT: Final = "/api/v1/observations"
USER_AGENT: Final = "bump-proposer/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0


# A `DomainError` reaches the wire NESTED -- `{"error": {"code": ...}}` -- and `code` is a closed
# snake_case vocabulary. That is what makes it safe to print where the BODY is not: a rejection
# body echoes the command back. Matched rather than trusted, so a server that answered something
# else cannot put arbitrary text in this lane's log.
_SAFE_CODE: Final = re.compile(r"[a-z0-9_]{1,64}")


class ObservationError(Exception):
    """The orchestrator could not be asked, or refused in a way this pass cannot interpret."""


class ObservationWriteError(ObservationError):
    """One observation could not be filed. A finding about one bump, not a broken tool."""


class ForbiddenEndpointError(ObservationWriteError):
    """This program tried to reach a path it is not allowed to reach."""


class ObservationCredentialError(ObservationError):
    """The orchestrator refused this producer's IDENTITY, so no cause can be filed at all.

    Deliberately NOT an `ObservationWriteError`, for the reason `UnusableEndpointError` is not
    one: a refused credential is not one bad bump, it is every bump. Caught per pull request it
    reports each one `unobserved` and exits with this lane's FINDING code -- which
    `sds-deadman.sh` pings as SUCCESS, because a declared finding code means the pass ran and
    reported. A revoked bearer would then propose nothing for as long as it stayed revoked while
    the dead-man check read `up`, which is the permanently-quiet twin of a permanently-red
    control. The sibling change-manager bearer never had that hazard, and not by design: its
    first call happens BEFORE the loop, so it raises where nothing can absorb it.
    """


class UnusableEndpointError(ObservationError):
    """The orchestrator URL cannot be used at all -- an operator's typo, not one bad bump.

    Deliberately NOT an `ObservationWriteError`, and here that is behaviour rather than taxonomy:
    everything that raises it is raised by `open_client`, which the pass calls once before the
    loop, so it stops the whole pass as an unusable input instead of costing one pull request its
    row and letting every later one fail the same way.
    """


def is_allowed_write(path: str) -> bool:
    return path == OBSERVATIONS_ENDPOINT


def _permits(method: str, path: str) -> bool:
    return method == "POST" and is_allowed_write(path)


def _refuse(method: str, path: str) -> ForbiddenEndpointError:
    return ForbiddenEndpointError(f"the producer may not {method} {path}")


def _named_code(response: httpx.Response) -> str:
    """The orchestrator's own error code when it sent one, and nothing else.

    Without it an undeployed vocabulary (`observation_invalid`), a fact that moved under a frozen
    row (`observation_conflict`) and a stale version all print as one number -- and the three want
    different acts from whoever reads the line.
    """
    code = error_code(response)
    return f" ({code})" if _SAFE_CODE.fullmatch(code) else ""


def _validate_base_url(base_url: str) -> None:
    problem = base_url_problem(base_url)
    if problem is not None:
        raise UnusableEndpointError(f"the orchestrator URL {problem}")


def open_client(
    *,
    base_url: str,
    credential_key_id: str,
    token: str,
    transport: httpx.BaseTransport | None = None,
    user_agent: str = USER_AGENT,
) -> OrchestratorClient:
    """Construct the client, translating an unusable base URL into this module's own error.

    Both halves of the guard live HERE, beside each other, which also keeps this program's entry
    in the repository's outbound allowlist at exactly one new file.
    """
    _validate_base_url(base_url)
    try:
        return OrchestratorClient(
            base_url=base_url,
            credential_key_id=credential_key_id,
            token=token,
            transport=transport,
            user_agent=user_agent,
        )
    except TransportFailure as failure:
        raise UnusableEndpointError(
            f"the orchestrator URL is not usable: {failure.error_type}"
        ) from None


class OrchestratorClient:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        # Overridden by the rotation proposer (ADR-0054), which reuses this one-route client
        # under its own name rather than a copy of it.
        self._client = ConfinedClient(
            base_url=base_url,
            user_agent=user_agent,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": credential_key_id,
                "Accept": "application/json",
            },
            permits=_permits,
            refuse=_refuse,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrchestratorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def record_observation(self, payload: dict[str, Any]) -> str:
        """File one observation and answer its id. A replay answers the id it answered before.

        THE ID IS READ HERE RATHER THAN BY THE CALLER, because a FastAPI `response_model` DROPS
        every key it does not declare -- so a field that stopped being served arrives as absence
        rather than as an error. Absence read as "no cause" would let this producer propose a
        record naming nothing, which is the dangling cause the contract's ordering exists to
        prevent. It is the answer, so it is refused rather than defaulted.
        """
        body = self._post(OBSERVATIONS_ENDPOINT, payload)
        identifier = body.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ObservationWriteError(
                "the orchestrator did not say which observation it recorded"
            )
        return identifier

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """The ONE way anything leaves this process; the confined client asks `_permits` first."""
        try:
            response = self._client.request("POST", path, json=payload)
        except TransportFailure as failure:
            raise ObservationWriteError(
                f"the orchestrator is unreachable for POST {path}: {failure.error_type}"
            ) from None
        if response.status_code in (401, 403):
            # BEFORE the generic branch, because this one must not be absorbed per bump. See
            # `ObservationCredentialError`: a refused identity is every bump, not one.
            raise ObservationCredentialError(
                f"the orchestrator refused this producer's credential on POST {path}: "
                f"{response.status_code}{_named_code(response)}"
            )
        if not 200 <= response.status_code < 300:
            # ANY non-2xx, not `>= 400`, so a redirect is named as one: this route is not behind
            # the forward-auth chain today, but a proxy is a thing that gets reconfigured, and a
            # `>= 400` check would hand a redirect body to `response.json()` and report a routing
            # refusal as a response-encoding fault. THE STATUS ONLY -- a rejection body echoes the
            # command back, and printing what it was given is how a value that should not be in a
            # log gets into one.
            raise ObservationWriteError(
                f"the orchestrator rejected POST {path}: "
                f"{response.status_code}{_named_code(response)}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise ObservationWriteError(
                f"the orchestrator answered POST {path} with a non-JSON body"
            ) from error
        if not isinstance(body, dict):
            raise ObservationWriteError(f"the answer to POST {path} was not an object")
        return body
