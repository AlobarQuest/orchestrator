"""`scripts/bws_reach_probe.py`: what a BWS token sees, printed as ids and counts only.

The SDK is replaced by a fake that records its calls; every value the fake could leak (the token,
a secret's key and value) is random per run and must appear nowhere in the output.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from scripts import bws_reach_probe
from scripts.bws_reach_probe import FAILED, LISTED, TOKEN_VARIABLE, USAGE

ORGANIZATION = str(uuid.uuid4())


@dataclass
class _Item:
    id: uuid.UUID
    key: str = field(default_factory=lambda: secrets.token_hex(8))
    value: str = field(default_factory=lambda: secrets.token_hex(16))


@dataclass
class _Listing:
    data: Any


@dataclass
class _Response:
    data: _Listing


class _FakeClient:
    def __init__(self, projects: list[_Item], listed: list[_Item], fail: bool = False) -> None:
        self._projects, self._secrets, self._fail = projects, listed, fail
        self.logins: list[str] = []
        self.organizations: list[str] = []
        self.home: str | None = None

    def auth(self) -> _FakeClient:
        return self

    def login_access_token(self, token: str) -> None:
        self.home = os.environ.get("HOME")
        self.logins.append(token)
        if self._fail:
            raise RuntimeError(f"login refused for {token}")

    def projects(self) -> _FakeClient:
        self._listing = self._projects
        return self

    def secrets(self) -> _FakeClient:
        self._listing = self._secrets
        return self

    def list(self, organization: str) -> _Response:
        self.organizations.append(organization)
        return _Response(_Listing(self._listing))


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    value = secrets.token_urlsafe(24)
    monkeypatch.setenv(TOKEN_VARIABLE, value)
    return value


def _run(capsys: pytest.CaptureFixture[str], client: _FakeClient, *argv: str) -> tuple[int, str]:
    code = bws_reach_probe.main(["--organization", ORGANIZATION, *argv], lambda: client)
    return code, capsys.readouterr().out


def test_it_prints_sorted_ids_counts_and_the_expected_ids(
    capsys: pytest.CaptureFixture[str], token: str
) -> None:
    project = _Item(uuid.uuid4())
    listed = [_Item(uuid.uuid4()) for _ in range(3)]
    absent = str(uuid.uuid4())
    client = _FakeClient([project], listed)
    code, out = _run(
        capsys,
        client,
        "--expect",
        str(project.id),
        "--expect",
        str(listed[0].id),
        "--expect",
        absent,
    )
    assert code == LISTED
    line = json.loads(out)
    assert line["projects"] == {"count": 1, "ids": [str(project.id)]}
    assert line["secrets"] == {"count": 3, "ids": sorted(str(s.id) for s in listed)}
    assert line["expected"] == {
        "projects": {str(project.id): True, str(listed[0].id): False, absent: False},
        "secrets": {str(project.id): False, str(listed[0].id): True, absent: False},
    }
    assert client.logins == [token]
    assert client.organizations == [ORGANIZATION, ORGANIZATION]


def test_no_token_key_or_value_reaches_the_output(
    capsys: pytest.CaptureFixture[str], token: str
) -> None:
    listed = [_Item(uuid.uuid4()) for _ in range(2)]
    _, out = _run(capsys, _FakeClient([], listed))
    for leak in [token, *(s.key for s in listed), *(s.value for s in listed)]:
        assert leak not in out


def test_the_sdk_runs_under_a_throwaway_home_which_is_restored(
    capsys: pytest.CaptureFixture[str], token: str
) -> None:
    before = os.environ.get("HOME")
    client = _FakeClient([], [])
    _run(capsys, client)
    assert client.home is not None and client.home != before
    assert "bws-reach-probe-" in client.home
    assert os.environ.get("HOME") == before


def test_a_failed_login_names_only_the_error_class(
    capsys: pytest.CaptureFixture[str], token: str
) -> None:
    code, out = _run(capsys, _FakeClient([], [], fail=True))
    assert code == FAILED
    assert json.loads(out) == {"event": "bws_reach_probe.failed", "error": "RuntimeError"}
    assert token not in out


@pytest.mark.parametrize("value", [None, ""])
def test_no_token_is_refused_before_the_sdk(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(TOKEN_VARIABLE, value)
    monkeypatch.setenv("BWS_ACCESS_TOKEN", secrets.token_urlsafe(24))
    client = _FakeClient([], [])
    code, out = _run(capsys, client)
    assert code == USAGE
    assert json.loads(out)["event"] == "bws_reach_probe.refused"
    assert client.logins == []


def test_a_malformed_id_is_a_usage_error(capsys: pytest.CaptureFixture[str], token: str) -> None:
    client = _FakeClient([], [])
    code, _ = _run(capsys, client, "--expect", "not-a-uuid")
    assert code == USAGE
    assert client.logins == []


def test_a_missing_sdk_is_a_usage_error_and_home_is_restored(
    capsys: pytest.CaptureFixture[str], token: str
) -> None:
    before = os.environ.get("HOME")

    def missing() -> Any:
        raise ModuleNotFoundError("No module named 'bitwarden_sdk'")

    code = bws_reach_probe.main(["--organization", ORGANIZATION], missing)
    line = json.loads(capsys.readouterr().out)
    assert code == USAGE
    assert line == {"event": "bws_reach_probe.refused", "error": bws_reach_probe.NO_SDK}
    assert os.environ.get("HOME") == before
