"""The estate reads `fork-sync` makes: change-manager's landing policy and its served revision,
App Brain's landing determination, and whether the sync App is installed on a fork. Each is one
GET on one path through the confined transport. `check` makes the policy and App Brain reads only
when given a credential -- without one the step is reported as not checked, never as passing.

The policy is read whole rather than through `bump_proposer.landing_policy`, because the fork's
sync author must appear in `non_ecosystem_authors` (ADR-0041) and that reader does not keep it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from revision_watcher.estate import LandingReader

CHANGE_MANAGER_URL: Final = "https://change-mgr.alobar.net"
GITHUB_API: Final = "https://api.github.com"
SYNC_APP_ID: Final = "4094707"
"""`fork-secure-upstream-sync` (ADR-0056 decision 2): an identifier, not a credential."""
USER_AGENT: Final = "fork-sync/1 (+AlobarQuest/orchestrator)"
_POLICY_PATH: Final = "/api/landing-policy"
_HEALTH_PATH: Final = "/api/health"
_REPOSITORY: Final = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
_JWT_LIFETIME: Final = timedelta(minutes=9)


class EstateReadError(RuntimeError):
    pass


def _get(
    base_url: str,
    path: str,
    headers: dict[str, str],
    transport: httpx.BaseTransport | None,
    service: str,
) -> tuple[int, Any]:
    """One GET of one path: the status, and the JSON body (`None` when it isn't JSON)."""
    try:
        client = ConfinedClient(
            base_url=base_url,
            user_agent=USER_AGENT,
            headers={"Accept": "application/json", **headers},
            timeout=30.0,
            transport=transport,
            permits=lambda method, asked: method == "GET" and asked == path,
            refuse=lambda method, asked: EstateReadError(f"may not {method} {asked}"),
        )
    except TransportFailure as failure:
        raise EstateReadError(f"{service} URL unusable: {failure.error_type}") from None
    try:
        response = client.request("GET", path)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None
    except TransportFailure as error:
        raise EstateReadError(f"{service} unreadable: {error.error_type}") from None
    finally:
        client.close()


def read_landing_policy(
    token: str, *, base_url: str = CHANGE_MANAGER_URL, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """The whole policy document: its `version` and its `inert_landing` block among the rest."""
    headers = {"Authorization": f"Bearer {token}"}
    status, document = _get(base_url, _POLICY_PATH, headers, transport, "change-manager")
    if (
        status != 200
        or not isinstance(document, dict)
        or not isinstance(document.get("inert_landing"), dict)
        or not isinstance(document.get("version"), int)
    ):
        raise EstateReadError(f"change-manager answered {status} with no versioned inert block")
    return document


def read_inert_landing(
    token: str, *, base_url: str = CHANGE_MANAGER_URL, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """The policy's `inert_landing` block."""
    return read_landing_policy(token, base_url=base_url, transport=transport)["inert_landing"]


def change_manager_revision(
    *, base_url: str = CHANGE_MANAGER_URL, transport: httpx.BaseTransport | None = None
) -> str:
    """The commit change-manager's running container was built from (`unknown` outside an image).
    The health route needs no credential."""
    status, document = _get(base_url, _HEALTH_PATH, {}, transport, "change-manager")
    revision = document.get("revision") if isinstance(document, dict) else None
    if status != 200 or not isinstance(revision, str):
        raise EstateReadError(f"change-manager's health answered {status} with no revision")
    return revision


def app_installed(
    fork: str,
    private_key: str,
    *,
    app_id: str = SYNC_APP_ID,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    """Whether the sync App's installation covers `fork`. GitHub answers this only to the App
    itself, so it is asked with a JWT signed by the App's key, which never leaves this process;
    the JWT can do nothing but ask for installation tokens, and none is asked for."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    if not _REPOSITORY.fullmatch(fork):
        raise EstateReadError(f"{fork!r} isn't an owner/name repository")
    try:
        key = load_pem_private_key(private_key.encode(), password=None)
    except ValueError:
        raise EstateReadError("the sync App's key isn't a PEM private key") from None
    if not isinstance(key, RSAPrivateKey):
        raise EstateReadError("the sync App's key isn't an RSA private key")
    now = clock()
    claims = {
        # A minute back, for the clocks GitHub and this machine disagree by.
        "iat": int((now - timedelta(minutes=1)).timestamp()),
        "exp": int((now + _JWT_LIFETIME).timestamp()),
        "iss": app_id,
    }
    headers = {
        "Authorization": f"Bearer {jwt.encode(claims, key, algorithm='RS256')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    status, _ = _get(GITHUB_API, f"/repos/{fork}/installation", headers, transport, "GitHub")
    if status in (200, 404):
        return status == 200
    # The body may echo the request, so only the status is kept.
    raise EstateReadError(f"GitHub answered the installation read with HTTP {status}")


def app_brain_landing(repository: str, *, base_url: str, key: str) -> str:
    """`inert`, `redeploys` or `unknown` (which includes "could not ask")."""
    with LandingReader(base_url=base_url, key=key) as reader:
        return reader.landing(repository)
