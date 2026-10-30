"""The executor's Coolify writes: one approved resource's variable (an application or a service),
and its restart.

ADR-0055 amendment 1 measured the token the executor holds: `read`, `write` and `deploy`, without
`read:sensitive`, so it writes a variable it can never read back. Coolify tokens name actions, not
resources, so the token itself could change any application on the instance. This client is the
bound: it is built for the one resource uuid an approved destination names, and its `permits`
allows exactly that resource's env PATCH and restart, so a bug elsewhere can't reach another.

Each variable is stored twice, once per `is_preview` value, and a PATCH changes one (amendment 1),
so a write sends both (a service's variable has one row). A refused write stops: the
delete-and-recreate fallback people use when a PATCH answers 500 could leave the variable
missing, and an orchestrator without its credentials setting doesn't boot. The next attempt
re-derives what to write from what the service loaded.

The executor reaches production Coolify over the tailnet, which encrypts it; its public address
answers plain HTTP (amendment 9).
"""

from __future__ import annotations

import ipaddress
from typing import Final, Literal
from urllib.parse import urlsplit

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem
from rotation_worker.contract import ContractError
from rotation_worker.values import Value

USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 60.0
# The preview row first: if the production row's write then fails, nothing the running service
# would load on its next restart has changed.
PREVIEW_ROWS: Final = (True, False)
# Tailscale's address range. WireGuard encrypts everything sent to it, so plain HTTP is allowed
# there and nowhere else: production Coolify's API has no TLS (ADR-0055 amendment 9).
TAILNET: Final = ipaddress.ip_network("100.64.0.0/10")


def url_problem(base_url: str) -> str | None:
    """Why the executor may not send its Coolify token to `base_url`, or None if it may."""
    parsed = urlsplit(base_url.strip())
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            return "must be https, or http to a tailnet address"
        return None if address in TAILNET else "must be https, or http to a tailnet address"
    return base_url_problem(base_url)


class CoolifyClient:
    def __init__(
        self,
        base_url: str,
        token: Value,
        resource_type: Literal["application", "service"],
        uuid: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if resource_type not in ("application", "service"):
            raise ContractError("a Coolify resource is an application or a service")
        problem = url_problem(base_url)
        if problem is not None:
            raise ContractError(f"the Coolify URL {problem}")
        self._service = resource_type == "service"
        if self._service:
            self._envs = f"/api/v1/services/{uuid}/envs"
            self._restart = f"/api/v1/services/{uuid}/restart"
        else:
            self._envs = f"/api/v1/applications/{uuid}/envs"
            self._restart = f"/api/v1/applications/{uuid}/restart"
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent=USER_AGENT,
                headers={"Authorization": f"Bearer {token.reveal()}"},
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                permits=self.permits,
                refuse=lambda method, path: ContractError(f"Coolify may not {method} {path}"),
            )
        except TransportFailure as failure:
            raise ContractError(f"the Coolify URL is unusable: {failure.error_type}") from None

    def permits(self, method: str, path: str) -> bool:
        return (method, path) in {("PATCH", self._envs), ("POST", self._restart)}

    def set_variable(self, key: str, value: str) -> None:
        """Write `value` to `key`: both rows of an application's, the one row of a service's.

        The value never appears in an error."""
        if self._service:
            self._send("PATCH", self._envs, json={"key": key, "value": value})
            return
        for preview in PREVIEW_ROWS:
            body = {"key": key, "value": value, "is_preview": preview}
            self._send("PATCH", self._envs, json=body)

    def restart(self) -> None:
        self._send("POST", self._restart)

    def _send(self, method: str, path: str, **kwargs: object) -> None:
        try:
            response = self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise ContractError(f"Coolify {method} got no answer: {failure.error_type}") from None
        if not 200 <= response.status_code < 300:
            # The body may echo the request, so only the status is kept.
            raise ContractError(f"Coolify {method} answered HTTP {response.status_code}")
