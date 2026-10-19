"""The two estate reads `fork-sync check` makes: change-manager's inert landing block and App
Brain's landing determination. Both are optional -- without a credential the step is reported
as not checked, never as passing.

The policy is read whole rather than through `bump_proposer.landing_policy`, because the fork's
sync author must appear in `non_ecosystem_authors` (ADR-0041) and that reader does not keep it.
"""

from __future__ import annotations

from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from revision_watcher.estate import LandingReader

CHANGE_MANAGER_URL: Final = "https://change-mgr.alobar.net"
_POLICY_PATH: Final = "/api/landing-policy"


class EstateReadError(RuntimeError):
    pass


def read_inert_landing(
    token: str, *, base_url: str = CHANGE_MANAGER_URL, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """The policy's `inert_landing` block. One GET, one path."""
    try:
        client = ConfinedClient(
            base_url=base_url,
            user_agent="fork-sync/1 (+AlobarQuest/orchestrator)",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30.0,
            transport=transport,
            permits=lambda method, path: method == "GET" and path == _POLICY_PATH,
            refuse=lambda method, path: EstateReadError(f"may not {method} {path}"),
        )
    except TransportFailure as failure:
        raise EstateReadError(f"change-manager URL unusable: {failure.error_type}") from None
    try:
        response = client.request("GET", _POLICY_PATH)
        document = response.json() if response.status_code == 200 else None
    except (TransportFailure, ValueError) as error:
        raise EstateReadError(f"change-manager unreadable: {type(error).__name__}") from None
    finally:
        client.close()
    if not isinstance(document, dict) or not isinstance(document.get("inert_landing"), dict):
        raise EstateReadError(f"change-manager answered {response.status_code} with no inert block")
    return document["inert_landing"]


def app_brain_landing(repository: str, *, base_url: str, key: str) -> str:
    """`inert`, `redeploys` or `unknown` (which includes "could not ask")."""
    with LandingReader(base_url=base_url, key=key) as reader:
        return reader.landing(repository)
