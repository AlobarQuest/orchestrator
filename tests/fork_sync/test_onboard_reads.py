"""The reads `fork-sync onboard` makes outside `gh`: the App installation, change-manager's policy
and revision (each one GET through the confined transport, answered here by a mock transport),
and BWS (through a replaced `subprocess.run`), shown never putting a token or value on argv."""

from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fork_sync import bws, estate

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


def _answering(status: int, body: Any, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle)


@pytest.mark.parametrize(("status", "installed"), [(200, True), (404, False)])
def test_the_installation_is_asked_as_the_app(status: int, installed: bool) -> None:
    seen: list[httpx.Request] = []
    transport = _answering(status, {"id": 1}, seen)
    answer = estate.app_installed("AlobarQuest/rea", PEM, transport=transport, clock=lambda: NOW)
    assert answer is installed
    [request] = seen
    assert request.method == "GET" and request.url.path == "/repos/AlobarQuest/rea/installation"
    scheme, token = request.headers["Authorization"].split(" ")
    claims = jwt.decode(
        token, KEY.public_key(), algorithms=["RS256"], options={"verify_exp": False}
    )
    assert scheme == "Bearer" and claims["iss"] == estate.SYNC_APP_ID
    assert request.headers["User-Agent"].startswith("fork-sync/")


def test_an_installation_read_that_fails_is_not_an_answer() -> None:
    with pytest.raises(estate.EstateReadError, match="HTTP 500"):
        estate.app_installed("AlobarQuest/rea", PEM, transport=_answering(500, {}, []))
    with pytest.raises(estate.EstateReadError, match="isn't a PEM"):
        estate.app_installed("AlobarQuest/rea", "not a key", transport=_answering(200, {}, []))


def test_the_policy_is_read_whole_and_the_revision_without_a_credential() -> None:
    seen: list[httpx.Request] = []
    document = {"version": 12, "inert_landing": {"repositories": []}}
    assert estate.read_landing_policy("t", transport=_answering(200, document, seen)) == document
    assert estate.read_inert_landing("t", transport=_answering(200, document, [])) == {
        "repositories": []
    }
    assert seen[0].headers["Authorization"] == "Bearer t"
    seen.clear()
    revision = estate.change_manager_revision(
        transport=_answering(200, {"status": "ok", "revision": "abc"}, seen)
    )
    assert revision == "abc" and "Authorization" not in seen[0].headers
    assert seen[0].url.path == "/api/health"
    with pytest.raises(estate.EstateReadError):
        estate.read_landing_policy("t", transport=_answering(200, {"inert_landing": {}}, []))
    with pytest.raises(estate.EstateReadError):
        estate.change_manager_revision(transport=_answering(502, None, []))


class FakeRun:
    """`subprocess.run` for the Keychain and `bws`: records argv and env, answers by program."""

    def __init__(self, keychain: str = "identity-token\n", bws_out: str = "") -> None:
        self.keychain = keychain
        self.bws_out = bws_out
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kwargs.get("env")))
        out = self.keychain if argv[0] == "/usr/bin/security" else self.bws_out
        return subprocess.CompletedProcess(argv, 0 if out else 44, out, "")


def test_bws_gets_the_token_by_env_and_never_by_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(bws_out=json.dumps({"value": "the-value"}))
    monkeypatch.setattr(bws.subprocess, "run", fake)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("BWS_ACCESS_TOKEN_BROAD", raising=False)
    read = bws.reader()
    assert read(bws.ANTHROPIC_API_KEY_UUID) == "the-value"
    assert read(bws.ANTHROPIC_API_KEY_UUID) == "the-value"  # read once
    keychain, get = fake.calls
    assert keychain[0][-3:-1] == ["-a", bws.KEYCHAIN_ACCOUNT]
    assert (
        " ".join(get[0][1:]) == f"secret get {bws.ANTHROPIC_API_KEY_UUID} --output json --color no"
    )
    assert get[1] is not None and get[1]["BWS_ACCESS_TOKEN"] == "identity-token"
    assert "FORCE_COLOR" not in get[1]
    assert all("identity-token" not in argv and "the-value" not in argv for argv, _ in fake.calls)


def test_a_missing_identity_or_value_says_which_and_shows_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BWS_ACCESS_TOKEN_BROAD", raising=False)
    monkeypatch.setattr(bws.subprocess, "run", FakeRun(keychain=""))
    with pytest.raises(bws.Unavailable, match="BWS_ACCESS_TOKEN_VPS_BACKUP"):
        bws.reader()(bws.APP_PRIVATE_KEY_UUID)
    monkeypatch.setattr(bws.subprocess, "run", FakeRun(bws_out='{"error": "secret-ish"}'))
    with pytest.raises(bws.Unavailable) as failure:
        bws.reader()(bws.APP_PRIVATE_KEY_UUID)
    assert "secret-ish" not in str(failure.value)


def test_the_app_key_is_decoded_from_its_keeper() -> None:
    assert bws.pem(base64.b64encode(PEM.encode()).decode() + "\n") == PEM
    with pytest.raises(bws.Unavailable):
        bws.pem(base64.b64encode(b"hello").decode())
    with pytest.raises(bws.Unavailable):
        bws.pem("not base64!")
