"""The unit-caused lane's HTTP client. A different credential and a different surface, on purpose.

The staleness sweep beside this one records observations under the OBSERVER credential and reads
nothing. This lane binds a release artifact, which `record_release_artifact` admits only for the
SYSTEM actor, and it must read first to know which units to ask about. Two lanes, two credentials,
two confined surfaces -- kept in separate modules so neither can quietly acquire the other's reach.

THREE PATHS AND NO MORE, enforced here in code:

* `GET /api/v1/machine-activation-candidates` -- which completed units this repository could bind
  an artifact for, and what a binding would have to carry.
* `POST /api/v1/work-units/{id}/release-artifacts` -- the binding itself.
* `POST /api/v1/release-artifacts/{id}/deployment-observations` -- the activation check that
  follows it, which is the sixth traceability hop.

Both POST paths are matched against anchored patterns with the id's shape spelled out, so a
prefix, a trailing slash, or `…/{id}/anything-else` does not match. That is the same bound the
landing ledger keeps on its own reads and for the same reason: a surface stated in a docstring is
a wish, and a surface stated in a matcher is a property.

THE ACTIVATION CHECK NEEDS NO NEW CREDENTIAL, which is the answer to the obvious question about
where it should live. `record_deployment_observation` admits only the SYSTEM actor, and this lane
already holds SYSTEM because binding does. The sibling staleness sweep holds OBSERVER, whose write
allowlist is `{/api/v1/observations}` alone -- and that narrowness is what the estate's negative
tests certify, so it stays exactly as it is.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

import httpx

CANDIDATES_ENDPOINT = "/api/v1/machine-activation-candidates"
WORK_UNIT_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_BIND = re.compile(rf"^/api/v1/work-units/{WORK_UNIT_ID}/release-artifacts$")
_OBSERVE = re.compile(rf"^/api/v1/release-artifacts/{WORK_UNIT_ID}/deployment-observations$")

# See the sibling client: a DNS label over 63 octets and an empty one both CONSTRUCT fine and
# raise `UnicodeError` at REQUEST time from IDNA encoding, so the guard has to be explicit.
MAX_DNS_LABEL = 63

TIMEOUT_SECONDS = 30.0


class BindingCallError(RuntimeError):
    """The orchestrator could not be asked, or answered in a way this lane cannot interpret."""


class ForbiddenEndpointError(BindingCallError):
    """This lane tried to reach a path it is not allowed to reach."""


class UnusableEndpointError(RuntimeError):
    """The orchestrator URL cannot be used at all -- the operator's typo, not a bad checkout."""


def is_allowed_read(path: str) -> bool:
    return path.split("?", 1)[0] == CANDIDATES_ENDPOINT


def is_allowed_write(path: str) -> bool:
    return _BIND.match(path) is not None or _OBSERVE.match(path) is not None


def bind_path(work_unit_id: str) -> str:
    return f"/api/v1/work-units/{work_unit_id}/release-artifacts"


def observe_path(binding_id: str) -> str:
    return f"/api/v1/release-artifacts/{binding_id}/deployment-observations"


def _validate_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnusableEndpointError("the orchestrator URL must be https with a host")
    for label in parsed.hostname.split("."):
        if not label or len(label) > MAX_DNS_LABEL:
            raise UnusableEndpointError("the orchestrator URL has a malformed host")


def open_binding_client(
    *,
    base_url: str,
    credential_key_id: str,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> BindingClient:
    """Construct the client, translating an unusable base URL into this module's own error.

    Both halves of the malformed-URL guard live here -- construction and request -- so the CLI
    imports no HTTP client and this program keeps a bounded entry in the outbound allowlist.
    """
    _validate_base_url(base_url)
    try:
        return BindingClient(
            base_url=base_url,
            credential_key_id=credential_key_id,
            token=token,
            transport=transport,
        )
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise UnusableEndpointError(
            f"the orchestrator URL is not usable: {type(error).__name__}"
        ) from error


class BindingClient:
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
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BindingClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def candidates(self, repository: str) -> list[dict[str, Any]]:
        body = self._request("GET", CANDIDATES_ENDPOINT, params={"repository": repository})
        if not isinstance(body, list):
            raise BindingCallError("the orchestrator did not answer with a list of candidates")
        return [row for row in body if isinstance(row, dict)]

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._request("POST", bind_path(work_unit_id), json=payload)
        if not isinstance(body, dict):
            raise BindingCallError("the orchestrator did not answer with a binding")
        return body

    def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._request("POST", observe_path(binding_id), json=payload)
        if not isinstance(body, dict):
            raise BindingCallError("the orchestrator did not answer with an observation")
        return body

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """The ONE way anything leaves this process, guard first and per method."""
        allowed = is_allowed_read(path) if method == "GET" else is_allowed_write(path)
        if not allowed:
            raise ForbiddenEndpointError(f"this lane may not {method} {path}")
        try:
            response = self._client.request(method, path, **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # THREE exception families, and the third is the one a two-member tuple misses: IDNA
            # encoding of a malformed host raises `UnicodeError`, a `ValueError` and neither of
            # the other two.
            raise BindingCallError(
                f"orchestrator is unreachable for {method} {path}: {type(error).__name__}"
            ) from error
        if response.status_code >= 400:
            # The status only. A rejection body echoes the command back, and a diagnostic that
            # prints what it was given is how a value that should not be in a transcript gets
            # into one.
            raise BindingCallError(f"orchestrator rejected {method} {path}: {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise BindingCallError(
                f"orchestrator answered {method} {path} with a non-JSON body"
            ) from error
