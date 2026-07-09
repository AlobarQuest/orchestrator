"""Mint GitHub App installation access tokens for workflow dispatch.

Dispatch cannot hold a static bearer token. An App's installation access token expires
after an hour, and an App JWT — the thing you can mint offline from the private key —
cannot call the workflow-dispatch endpoint at all. Only an installation token can. So the
dispatcher is handed a callable that mints, caches, and refreshes, rather than a string.

Nothing here may log, format, or re-raise key material. `GitHubAppTokenError` carries a
reason code and nothing else, so a traceback that escapes to an event payload or an HTTP
response cannot carry the PEM or the minted token.
"""

import base64
import binascii
import hashlib
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from pydantic import SecretStr

from orchestrator.config import Settings

GITHUB_API_URL = "https://api.github.com"

# GitHub rejects an assertion whose `exp` is more than 10 minutes out, and one whose `iat`
# is in the future. The 60s backdate absorbs clock skew in both directions.
CLOCK_SKEW_SECONDS = 60
ASSERTION_LIFETIME_SECONDS = 540

# Refresh this far before the installation token actually expires, so a token minted for a
# dispatch is never on the edge of expiring while GitHub processes it.
REFRESH_MARGIN_SECONDS = 300

MINT_TIMEOUT_SECONDS = 10.0


class GitHubAppTokenError(Exception):
    """A minting failure, identified by a reason code that never carries key material."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GitHubAppCredentials:
    app_id: str
    installation_id: str
    private_key_b64: SecretStr = field(repr=False)


def github_app_credentials(settings: Settings) -> GitHubAppCredentials | None:
    """The single definition of "App credentials are configured".

    The dispatch gate and the minter both read it, so a partially configured trio fails
    closed at the gate (`github_app_credentials_missing`) rather than at mint time.
    """
    private_key_b64 = settings.github_app_private_key_b64
    if not settings.github_app_id or not settings.github_app_installation_id:
        return None
    if private_key_b64 is None or not private_key_b64.get_secret_value():
        return None
    return GitHubAppCredentials(
        app_id=settings.github_app_id,
        installation_id=settings.github_app_installation_id,
        private_key_b64=private_key_b64,
    )


class GitHubAppTokenProvider:
    """Mints and caches one installation token per process.

    Construction is deliberately lazy and validates nothing: `dispatch_route` builds the
    dispatcher before the dispatch gate runs, so a disabled or unconfigured orchestrator
    must be able to construct a provider it will never call.
    """

    def __init__(
        self,
        credentials: GitHubAppCredentials | None,
        *,
        timeout: float = MINT_TIMEOUT_SECONDS,
    ) -> None:
        self._credentials = credentials
        self._timeout = timeout
        self._lock = threading.Lock()
        self._cached: tuple[str, datetime] | None = None

    def __call__(self) -> str:
        # Check the cache before taking the lock: a caller holding a still-valid token must
        # not queue behind an in-flight mint, which blocks on the network for up to
        # `timeout`. Rebinding one tuple keeps token and expiry consistent for that
        # lock-free reader.
        fresh = _fresh_token(self._cached)
        if fresh is not None:
            return fresh
        # Single-flight: sync FastAPI routes run in a threadpool, so concurrent dispatches
        # share this provider and would otherwise mint a token each.
        with self._lock:
            fresh = _fresh_token(self._cached)
            if fresh is not None:
                return fresh
            token, expires_at = self._mint()
            self._cached = (token, expires_at)
            return token

    def _mint(self) -> tuple[str, datetime]:
        credentials = self._credentials
        if credentials is None:
            raise GitHubAppTokenError("app_credentials_missing")
        url = f"{GITHUB_API_URL}/app/installations/{credentials.installation_id}/access_tokens"
        try:
            response = httpx.post(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {_assertion(credentials)}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=self._timeout,
            )
        except httpx.RequestError as error:
            raise GitHubAppTokenError(f"mint_request_error:{error.__class__.__name__}") from error
        if response.status_code != 201:
            # The status code only: a success-shaped body carries the token itself, and an
            # error body is unaudited.
            raise GitHubAppTokenError(f"mint_status:{response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            # A 201 carrying an unparseable body must still fail closed into a dispatch
            # record, not escape as a JSONDecodeError that rolls the transaction back.
            raise GitHubAppTokenError("mint_response_invalid") from error
        return _parse_mint_response(payload)


def _fresh_token(cached: tuple[str, datetime] | None) -> str | None:
    if cached is None:
        return None
    token, expires_at = cached
    if datetime.now(UTC) < expires_at - timedelta(seconds=REFRESH_MARGIN_SECONDS):
        return token
    return None


def _assertion(credentials: GitHubAppCredentials) -> str:
    # `openssl base64` wraps at 64 columns and GNU `base64` at 76, so a correctly-encoded
    # PEM routinely arrives with embedded newlines. Strip whitespace rather than reject it,
    # but keep `validate=True` so genuinely non-base64 input still fails closed.
    encoded = "".join(credentials.private_key_b64.get_secret_value().split())
    try:
        private_key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise GitHubAppTokenError("private_key_not_base64") from error
    issued_at = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": credentials.app_id,
        "iat": issued_at - CLOCK_SKEW_SECONDS,
        "exp": issued_at + ASSERTION_LIFETIME_SECONDS,
    }
    try:
        return jwt.encode(claims, private_key, algorithm="RS256")
    except (ValueError, TypeError, jwt.PyJWTError) as error:
        raise GitHubAppTokenError("private_key_invalid") from error


def _parse_mint_response(payload: Any) -> tuple[str, datetime]:
    if not isinstance(payload, dict):
        raise GitHubAppTokenError("mint_response_invalid")
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise GitHubAppTokenError("mint_response_invalid")
    return token, _parse_expires_at(payload.get("expires_at"))


def _parse_expires_at(value: Any) -> datetime:
    """An absent or unparseable expiry means "already expired" — never "cache forever"."""
    if not isinstance(value, str):
        return datetime.now(UTC)
    try:
        expires_at = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    return expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)


_PROVIDERS: dict[str, GitHubAppTokenProvider] = {}
_PROVIDERS_LOCK = threading.Lock()


def token_provider_for(credentials: GitHubAppCredentials | None) -> GitHubAppTokenProvider:
    """The one provider for these credentials, so its minted token outlives a request.

    Keyed on the credentials the caller resolved rather than read from process settings:
    the dispatch gate and the minter must agree about which credentials are in play, and a
    settings override that reaches only one of them would make the gate's verdict a lie.
    """
    key = _credentials_key(credentials)
    with _PROVIDERS_LOCK:
        provider = _PROVIDERS.get(key)
        if provider is None:
            provider = GitHubAppTokenProvider(credentials)
            _PROVIDERS[key] = provider
        return provider


def reset_token_providers() -> None:
    """Drop cached providers. For tests: the cache is process-lifetime by design."""
    with _PROVIDERS_LOCK:
        _PROVIDERS.clear()


def _credentials_key(credentials: GitHubAppCredentials | None) -> str:
    """Identify a credential set without holding its secret as a dict key."""
    if credentials is None:
        return "absent"
    material = ":".join(
        (
            credentials.app_id,
            credentials.installation_id,
            credentials.private_key_b64.get_secret_value(),
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()
