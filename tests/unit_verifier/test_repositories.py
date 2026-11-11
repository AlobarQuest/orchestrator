"""`unit-verifier repositories`: the repositories the lane's container mints its GitHub token for,
read from the member record with the lane's VERIFIER bearer (ADR-0059 increment 3).

Run through `main`, the entry point the launcher calls, with the orchestrator faked by a transport.
"""

from __future__ import annotations

import functools
from typing import Any

import httpx
import pytest

from sds_members.client import SERVICES
from sds_members.populations import service_for_change_class
from unit_verifier import cli
from unit_verifier.client import VerifierClient


def _member(repository: str, state: str = "member", **grants: str) -> dict[str, Any]:
    return {
        "repository": repository,
        "owner": "Owner",
        "state": state,
        "working_copy": None,
        "machine_local": False,
        "apps": [],
        "holds": [],
        "grants": {service: {"state": grants.get(service, "absent")} for service in SERVICES},
    }


def _main(
    monkeypatch: pytest.MonkeyPatch, members: httpx.Response, *argv: str
) -> tuple[int, list[str]]:
    requests: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.method} {request.url.path}")
        assert request.headers["Authorization"] == "Bearer verifier-token"
        assert request.headers["X-Credential-Key-Id"] == "orchestrator-verifier"
        return members

    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "verifier-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-verifier")
    monkeypatch.setattr(
        cli,
        "VerifierClient",
        functools.partial(VerifierClient, transport=httpx.MockTransport(answer)),
    )
    return cli.main(["repositories", *argv]), requests


def _record(*members: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"members": list(members)})


def test_it_prints_every_repository_granted_bumps_or_app_changes_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _record(
        _member("Owner/bumps", dependency_updates="granted"),
        _member("Owner/apps", app_changes="granted"),
        _member("Owner/both", dependency_updates="granted", app_changes="granted"),
        # Another spelling of a granted repository: the mint refuses a name twice.
        _member("owner/BUMPS", app_changes="granted"),
        _member("Owner/rollouts-only", rollouts="granted"),
        _member("Owner/withdrawn", app_changes="withdrawn"),
        _member("Owner/retired", "retired", dependency_updates="granted"),
    )
    rc, requests = _main(monkeypatch, record)
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert out.out.splitlines() == ["Owner/bumps", "Owner/both", "Owner/apps"]
    assert requests == ["GET /api/v1/members"]


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(503),
        httpx.Response(200, json={"members": []}),
        httpx.Response(200, json={"members": [{"repository": "Owner/torn"}]}),
        httpx.Response(200, text="not json"),
    ],
    ids=["unreachable", "empty", "malformed", "non-json"],
)
def test_an_unreadable_or_empty_record_prints_nothing_and_exits_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: httpx.Response
) -> None:
    rc, _ = _main(monkeypatch, answer)
    out = capsys.readouterr()
    assert rc == 3
    assert out.out == ""
    assert out.err.startswith("unit-verifier repositories: ")


def test_a_record_granting_neither_service_says_so_rather_than_printing_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _ = _main(monkeypatch, _record(_member("Owner/rollouts", rollouts="granted")))
    out = capsys.readouterr()
    assert rc == 3
    assert out.out == ""
    assert "no repository is granted" in out.err


def test_without_the_bearer_it_is_the_tool_failing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    assert cli.main(["repositories"]) == 1
    assert capsys.readouterr().out == ""


def test_the_services_are_the_ones_a_check_filing_units_grant_is_read_from() -> None:
    """`check_filing_repositories` names the two services `_grant_hold` and landing map a bump and
    an app change to; this holds its literals to `service_for_change_class`."""
    assert {
        service_for_change_class(cli.BUMP_CHANGE_CLASS),
        service_for_change_class("app-change"),
    } == {"dependency_updates", "app_changes"}


def test_a_pass_still_takes_no_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Mac's plist and the crontab run `unit-verifier` bare; `--dry-run` still parses."""
    seen: list[bool] = []
    monkeypatch.setattr(cli, "run", lambda client, *, dry_run, **_: seen.append(dry_run) or 0)
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "verifier-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-verifier")
    assert cli.main([]) == 0
    assert cli.main(["--dry-run"]) == 0
    assert seen == [False, True]
