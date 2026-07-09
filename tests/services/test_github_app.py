import base64
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from orchestrator.config import Settings
from orchestrator.services import github_app
from orchestrator.services.github_app import (
    REFRESH_MARGIN_SECONDS,
    GitHubAppCredentials,
    GitHubAppTokenError,
    GitHubAppTokenProvider,
    github_app_credentials,
    reset_token_providers,
    token_provider_for,
)

MINTED_TOKEN = "ghs_mintedinstallationtoken"
APP_ID = "123456"
INSTALLATION_ID = "78901234"


@pytest.fixture(scope="module")
def private_key_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def public_key_pem(private_key_pem: bytes) -> bytes:
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture
def credentials(private_key_pem: bytes) -> GitHubAppCredentials:
    return _credentials(base64.b64encode(private_key_pem).decode())


def _credentials(private_key_b64: str) -> GitHubAppCredentials:
    return GitHubAppCredentials(
        app_id=APP_ID,
        installation_id=INSTALLATION_ID,
        private_key_b64=SecretStr(private_key_b64),
    )


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeGitHub:
    """Records mint calls so caching can be asserted by call count."""

    def __init__(self, *responses: FakeResponse, delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.delay = delay
        self._lock = threading.Lock()

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        with self._lock:
            self.calls.append({"url": url, **kwargs})
            response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if self.delay:
            time.sleep(self.delay)
        return response


def mint_response(*, expires_in: int, token: str = MINTED_TOKEN) -> FakeResponse:
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    return FakeResponse(201, {"token": token, "expires_at": expires_at.isoformat()})


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    return install(monkeypatch, FakeGitHub(mint_response(expires_in=3600)))


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeGitHub) -> FakeGitHub:
    monkeypatch.setattr(github_app.httpx, "post", fake.post)
    return fake


# --- the "configured" helper: one definition of configured, shared by gate and minter ---


@pytest.mark.parametrize(
    "absent",
    ["github_app_id", "github_app_installation_id", "github_app_private_key_b64"],
)
def test_credentials_are_absent_when_any_field_is_missing(absent: str) -> None:
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://localhost/x",
        "github_app_id": APP_ID,
        "github_app_installation_id": INSTALLATION_ID,
        "github_app_private_key_b64": "cGVt",
    }
    values[absent] = None

    assert github_app_credentials(Settings(**values)) is None


def test_credentials_are_present_when_all_three_fields_are_set() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://localhost/x",
        github_app_id=APP_ID,
        github_app_installation_id=INSTALLATION_ID,
        github_app_private_key_b64=SecretStr("cGVt"),
    )

    resolved = github_app_credentials(settings)

    assert resolved is not None
    assert resolved.app_id == APP_ID
    assert resolved.installation_id == INSTALLATION_ID


def test_credentials_repr_masks_the_private_key() -> None:
    assert "c3VwZXI=" not in repr(_credentials("c3VwZXI="))


def test_settings_repr_masks_the_private_key() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://localhost/x",
        github_app_private_key_b64=SecretStr("c3VwZXItc2VjcmV0LXBlbQ=="),
    )

    assert "c3VwZXItc2VjcmV0LXBlbQ==" not in repr(settings)
    assert "c3VwZXItc2VjcmV0LXBlbQ==" not in str(settings.model_dump())


# --- caching ---


def test_token_is_minted_once_and_reused_before_expiry(
    credentials: GitHubAppCredentials, github: FakeGitHub
) -> None:
    provider = GitHubAppTokenProvider(credentials)

    assert provider() == MINTED_TOKEN
    assert provider() == MINTED_TOKEN
    assert len(github.calls) == 1


def test_token_is_refreshed_once_inside_the_refresh_margin(
    credentials: GitHubAppCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Expiry inside the 300s margin means the cached token is already too old to reuse.
    github = install(monkeypatch, FakeGitHub(mint_response(expires_in=REFRESH_MARGIN_SECONDS - 30)))
    provider = GitHubAppTokenProvider(credentials)

    provider()
    provider()

    assert len(github.calls) == 2


def test_missing_expires_at_is_treated_as_already_expired(
    credentials: GitHubAppCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = install(monkeypatch, FakeGitHub(FakeResponse(201, {"token": MINTED_TOKEN})))
    provider = GitHubAppTokenProvider(credentials)

    assert provider() == MINTED_TOKEN
    provider()

    assert len(github.calls) == 2


def test_concurrent_callers_mint_a_single_token(
    credentials: GitHubAppCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = install(monkeypatch, FakeGitHub(mint_response(expires_in=3600), delay=0.05))
    provider = GitHubAppTokenProvider(credentials)
    tokens: list[str] = []
    barrier = threading.Barrier(8)

    def call() -> None:
        barrier.wait()
        tokens.append(provider())

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert tokens == [MINTED_TOKEN] * 8
    assert len(github.calls) == 1


# --- the JWT assertion ---


def test_assertion_is_rs256_and_within_githubs_ten_minute_limit(
    credentials: GitHubAppCredentials, public_key_pem: bytes, github: FakeGitHub
) -> None:
    provider = GitHubAppTokenProvider(credentials)
    provider()

    authorization = github.calls[0]["headers"]["Authorization"]
    assertion = authorization.removeprefix("Bearer ")
    header = jwt.get_unverified_header(assertion)
    claims = jwt.decode(
        assertion, public_key_pem, algorithms=["RS256"], options={"verify_aud": False}
    )

    now = int(time.time())
    assert header["alg"] == "RS256"
    assert claims["iss"] == APP_ID
    assert claims["iat"] <= now
    assert claims["exp"] - now <= 600


def test_mint_call_targets_the_installation_and_sends_github_api_headers(
    credentials: GitHubAppCredentials, github: FakeGitHub
) -> None:
    provider = GitHubAppTokenProvider(credentials)
    provider()

    call = github.calls[0]
    headers: Mapping[str, str] = call["headers"]
    assert call["url"].endswith(f"/app/installations/{INSTALLATION_ID}/access_tokens")
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert call["timeout"] > 0


# --- fail-closed, with no key material anywhere ---


def test_absent_credentials_fail_closed_with_a_reason_code() -> None:
    provider = GitHubAppTokenProvider(None)

    with pytest.raises(GitHubAppTokenError) as excinfo:
        provider()

    assert excinfo.value.code == "app_credentials_missing"


def test_a_private_key_that_is_not_base64_fails_closed() -> None:
    provider = GitHubAppTokenProvider(_credentials("not base64 at all !!!"))

    with pytest.raises(GitHubAppTokenError) as excinfo:
        provider()

    assert excinfo.value.code == "private_key_not_base64"


def test_a_malformed_private_key_fails_closed_without_leaking_key_material() -> None:
    secret = "-----BEGIN PRIVATE KEY-----\nnot-a-real-key-but-still-secret\n"
    provider = GitHubAppTokenProvider(_credentials(base64.b64encode(secret.encode()).decode()))

    with pytest.raises(GitHubAppTokenError) as excinfo:
        provider()

    error = excinfo.value
    assert error.code == "private_key_invalid"
    rendered = f"{error!r} {error} {[str(context) for context in _causes(error)]}"
    assert "not-a-real-key-but-still-secret" not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered


def test_a_failed_mint_carries_only_the_status_code_never_the_body(
    credentials: GitHubAppCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"token": "ghs_leaked_token_in_an_error_body"}
    install(monkeypatch, FakeGitHub(FakeResponse(401, body)))
    provider = GitHubAppTokenProvider(credentials)

    with pytest.raises(GitHubAppTokenError) as excinfo:
        provider()

    error = excinfo.value
    assert error.code == "mint_status:401"
    assert "ghs_leaked_token_in_an_error_body" not in f"{error!r} {error}"


def test_a_mint_transport_error_fails_closed(
    credentials: GitHubAppCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(url: str, **kwargs: Any) -> FakeResponse:
        raise github_app.httpx.ConnectError("connection refused")

    monkeypatch.setattr(github_app.httpx, "post", explode)
    provider = GitHubAppTokenProvider(credentials)

    with pytest.raises(GitHubAppTokenError) as excinfo:
        provider()

    assert excinfo.value.code == "mint_request_error:ConnectError"


def test_an_unparseable_mint_body_fails_closed(
    credentials: GitHubAppCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeGitHub(FakeResponse(201, {"expires_at": "2026-01-01T00:00:00Z"})))
    provider = GitHubAppTokenProvider(credentials)

    with pytest.raises(GitHubAppTokenError) as excinfo:
        provider()

    assert excinfo.value.code == "mint_response_invalid"


def test_provider_construction_never_touches_credentials() -> None:
    """The kill-switch proof dispatches with dispatch disabled and no App credentials.

    `dispatch_route` builds the dispatcher (and therefore the provider) unconditionally,
    before `_blocked_reason` runs, so construction must not validate or decode anything.
    """
    GitHubAppTokenProvider(None)
    GitHubAppTokenProvider(_credentials("not base64 at all !!!"))


def _causes(error: BaseException) -> list[BaseException]:
    causes: list[BaseException] = []
    current = error.__cause__ or error.__context__
    while current is not None:
        causes.append(current)
        current = current.__cause__ or current.__context__
    return causes


# --- WS-6.4.0c review fixes ---


def test_a_wrapped_base64_private_key_is_accepted(
    private_key_pem: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openssl base64` wraps at 64 columns; a correctly-encoded PEM has newlines in it."""
    wrapped = base64.encodebytes(private_key_pem).decode()
    assert "\n" in wrapped.strip()
    github = install(monkeypatch, FakeGitHub(mint_response(expires_in=3600)))

    assert GitHubAppTokenProvider(_credentials(wrapped))() == MINTED_TOKEN
    assert len(github.calls) == 1


def test_a_201_with_an_unparseable_body_fails_closed(
    credentials: GitHubAppCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must not escape as a JSONDecodeError: that rolls back with no dispatch record."""
    install(monkeypatch, FakeGitHub(FakeResponse(201, ValueError("not json"))))
    provider = GitHubAppTokenProvider(credentials)

    with pytest.raises(GitHubAppTokenError) as excinfo:
        provider()

    assert excinfo.value.code == "mint_response_invalid"


def test_the_same_credentials_share_one_provider_and_therefore_one_token_cache() -> None:
    reset_token_providers()
    credentials = _credentials("cGVt")

    assert token_provider_for(credentials) is token_provider_for(_credentials("cGVt"))


def test_different_credentials_get_different_providers() -> None:
    reset_token_providers()

    first = token_provider_for(_credentials("cGVt"))
    second = token_provider_for(_credentials("b3RoZXI="))
    absent = token_provider_for(None)

    assert first is not second
    assert absent is not first


def test_resetting_providers_drops_the_cache() -> None:
    reset_token_providers()
    before = token_provider_for(None)
    reset_token_providers()

    assert token_provider_for(None) is not before
