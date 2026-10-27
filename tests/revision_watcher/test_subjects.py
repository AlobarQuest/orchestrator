"""The watcher's subjects are the member record's apps, and an unreadable record is incomplete."""

from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

from revision_watcher import cli
from revision_watcher.subjects import Subject, load_subjects
from sds_members.client import SERVICES, MembersUnreadable


def _member(repository: str, state: str, *apps: tuple[str, str, str]) -> dict:
    return {
        "repository": repository,
        "owner": "devon",
        "state": state,
        "working_copy": None,
        "machine_local": False,
        "apps": [{"name": n, "health_url": u, "branch": b} for n, u, b in apps],
        "grants": {
            s: {"state": "granted", "seeded": False, "set_by": "x", "set_at": None, "reason": "r"}
            for s in SERVICES
        },
    }


def _record(*members: dict) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json={"members": list(members)}))


def _load(transport: httpx.MockTransport) -> tuple[Subject, ...]:
    return load_subjects(token="t", credential_key_id="orchestrator-observer", transport=transport)


def test_subjects_come_from_member_apps() -> None:
    subjects = _load(
        _record(
            _member(
                "A/brain",
                "member",
                ("one", "https://one.example/api/health", "main"),
                ("two", "https://two.example/api/health", "preview"),
            ),
            _member("A/solo", "onboarding", ("solo", "https://solo.example/h", "main")),
        )
    )

    assert subjects == (
        Subject("one", "https://one.example/api/health", "A/brain", "main"),
        Subject("two", "https://two.example/api/health", "A/brain", "preview"),
        Subject("solo", "https://solo.example/h", "A/solo", "main"),
    )


def test_retired_member_apps_are_not_subjects() -> None:
    subjects = _load(
        _record(
            _member("A/gone", "retired", ("gone", "https://gone.example/h", "main")),
            _member("A/live", "member", ("live", "https://live.example/h", "main")),
        )
    )

    assert [s.name for s in subjects] == ["live"]


def test_an_unreadable_record_raises_unchanged() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))

    with pytest.raises(MembersUnreadable):
        _load(transport)


ENV = {
    "REVISION_WATCHER_GITHUB_TOKEN": "x",
    "ORCHESTRATOR_API_URL": "https://sds.example",
    "ORCHESTRATOR_API_CREDENTIAL_KEY_ID": "orchestrator-observer",
    "ORCHESTRATOR_API_TOKEN": "y",
}


def _unmeasured(**_: object) -> None:
    raise AssertionError("nothing may be measured without a subject list")


@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]])
def test_unreadable_record_exits_3(monkeypatch, dry_run) -> None:
    def unreadable(**_: object):
        raise MembersUnreadable("the member record is unreachable: ConnectError")

    monkeypatch.setattr(cli, "load_subjects", unreadable)
    monkeypatch.setattr(cli, "_measure", _unmeasured)

    result = CliRunner(env=ENV).invoke(cli.app, dry_run)

    assert result.exit_code == cli.EXIT_INCOMPLETE
    assert "the member record is unreachable" in result.output


def test_a_record_with_no_apps_exits_3(monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_subjects", lambda **_: ())
    monkeypatch.setattr(cli, "_measure", _unmeasured)

    result = CliRunner(env=ENV).invoke(cli.app, [])

    assert result.exit_code == cli.EXIT_INCOMPLETE
    assert "no subjects" in result.output
