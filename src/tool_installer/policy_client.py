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
from urllib.parse import urlsplit

import httpx

FACTORY_POLICY_ENDPOINT = "/api/v1/factory-policy"

# RFC 1035: a DNS label is at most 63 octets and may not be empty. Checked here rather than left
# to `httpx` because a doubled dot and an over-long label both CONSTRUCT cleanly and raise
# `UnicodeError` from IDNA encoding at REQUEST time.
MAX_DNS_LABEL = 63


class PolicyReadError(RuntimeError):
    """The policy could not be read. Always a refusal; never a fallback to assumed hours."""


class ForbiddenEndpointError(PolicyReadError):
    """A read outside this client's single route."""


class UnusableEndpointError(RuntimeError):
    """The orchestrator URL cannot be used at all -- the operator's typo, not a bad answer."""


def _validate_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnusableEndpointError("the orchestrator URL must be https with a host")
    for label in parsed.hostname.split("."):
        if not label or len(label) > MAX_DNS_LABEL:
            raise UnusableEndpointError("the orchestrator URL has a malformed host")


def is_allowed_read(path: str) -> bool:
    return path == FACTORY_POLICY_ENDPOINT


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
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise UnusableEndpointError(
            f"the orchestrator URL is not usable: {type(error).__name__}"
        ) from error


class PolicyClient:
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

    def __enter__(self) -> PolicyClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def factory_policy(self) -> dict[str, Any]:
        return self.get(FACTORY_POLICY_ENDPOINT)

    def get(self, path: str) -> dict[str, Any]:
        """The whole surface, and the only method there is -- read-only by construction."""
        if not is_allowed_read(path):
            raise ForbiddenEndpointError(f"the installer may not read {path}")
        try:
            response = self._client.request("GET", path)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # THREE exception families, and the third is the one a two-member tuple misses: IDNA
            # encoding of a malformed HOST raises `UnicodeError`, a `ValueError` and neither of
            # the other two. An escape here is a traceback instead of a named refusal.
            raise PolicyReadError(
                f"orchestrator is unreachable for GET {path}: {type(error).__name__}"
            ) from error
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
