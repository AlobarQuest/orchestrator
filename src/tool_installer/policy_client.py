"""The SYSTEM-credentialled READ of the deployed factory policy. One route, no writes at all.

**A SEPARATE MODULE FROM `orchestrator_client.py`, AND THE SPLIT IS THE PROPERTY RATHER THAN AN
EXCEPTION TO IT.** This holds a SYSTEM bearer and may only GET; that one holds an OBSERVER bearer
and may only POST. A single module serving both would put two credentials behind one guard, and
the SYSTEM bearer is the one that can drive a work unit's lifecycle -- so the module that carries
it must be the one that cannot reach anything but a policy read. `activation_sweep` splits its
two surfaces for exactly this reason and is the precedent.

**WHY SYSTEM RATHER THAN THE OBSERVER BEARER THE WRITE ALREADY NEEDS.** Measured against
production 2026-09-06: `GET /api/v1/factory-policy` answers **403** to `orchestrator-observer` and
**200** to `orchestrator-system`. So the second credential is forced by the endpoint, not chosen
for convenience -- worth recording, because "reads are unconfined for the observer role" is true
of the observer confinement and is not true of this route.

The base-URL guard is the pin watcher's, copied with it: `httpx` refuses some malformed URLs at
the CONSTRUCTOR and others at REQUEST time, so a guard on one half is not a guard, and the two
halves would otherwise carry different exit codes.
"""

from __future__ import annotations

from typing import Any

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem

FACTORY_POLICY_ENDPOINT = "/api/v1/factory-policy"


class PolicyReadError(RuntimeError):
    """The policy could not be read. Always a refusal; never a fallback to assumed hours."""


class ForbiddenEndpointError(PolicyReadError):
    """A read outside this client's single route."""


class UnusableEndpointError(RuntimeError):
    """The orchestrator URL cannot be used at all -- the operator's typo, not a bad answer."""


def _validate_base_url(base_url: str) -> None:
    problem = base_url_problem(base_url)
    if problem is not None:
        raise UnusableEndpointError(f"the orchestrator URL {problem}")


def is_allowed_read(path: str) -> bool:
    return path == FACTORY_POLICY_ENDPOINT


def _permits(method: str, path: str) -> bool:
    return method == "GET" and is_allowed_read(path)


def _refuse(_method: str, path: str) -> ForbiddenEndpointError:
    return ForbiddenEndpointError(f"the installer may not read {path}")


def open_policy_client(
    *,
    base_url: str,
    credential_key_id: str,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> PolicyClient:
    _validate_base_url(base_url)
    try:
        return PolicyClient(
            base_url=base_url,
            credential_key_id=credential_key_id,
            token=token,
            transport=transport,
        )
    except TransportFailure as failure:
        raise UnusableEndpointError(
            f"the orchestrator URL is not usable: {failure.error_type}"
        ) from None


class PolicyClient:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = ConfinedClient(
            base_url=base_url,
            user_agent="tool-installer/1 (+AlobarQuest/orchestrator)",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": credential_key_id,
            },
            timeout=30.0,
            transport=transport,
            permits=_permits,
            refuse=_refuse,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PolicyClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def factory_policy(self) -> dict[str, Any]:
        return self.get(FACTORY_POLICY_ENDPOINT)

    def get(self, path: str) -> dict[str, Any]:
        """The whole surface, and the only method there is -- read-only by construction."""
        try:
            response = self._client.request("GET", path)
        except TransportFailure as failure:
            raise PolicyReadError(
                f"orchestrator is unreachable for GET {path}: {failure.error_type}"
            ) from None
        if response.status_code >= 400:
            # The status only. A rejection body echoes the request back, and a diagnostic that
            # prints what it was given is how a value that should not be in a log gets into one.
            raise PolicyReadError(f"orchestrator rejected GET {path}: {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise PolicyReadError(f"orchestrator answered GET {path} with a non-JSON body") from (
                error
            )
        if not isinstance(body, dict):
            raise PolicyReadError(f"orchestrator answered GET {path} with a non-object body")
        return body
