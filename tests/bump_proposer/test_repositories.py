"""`bump-proposer repositories`: the repositories the lane's GitHub read token must name.

The lane's container mints its read token for exactly what this prints (ADR-0059 decision 2), so
it must name every repository a pass reads, the packages repository its clone reads included, and
must print nothing when it can't derive the list.
"""

from __future__ import annotations

import pytest

from bump_proposer import cli
from bump_proposer.cli import EXIT_OK, EXIT_UNUSABLE
from sds_members.client import MembersUnreadable
from tests.bump_proposer.test_pass import _member


def _members(monkeypatch: pytest.MonkeyPatch, *members) -> None:
    monkeypatch.setattr(cli, "read_members_from_environment", lambda **_: list(members))


def test_it_names_the_packages_repository_and_each_dependency_updates_grant(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _members(
        monkeypatch,
        _member("AlobarQuest/crm"),
        _member("AlobarQuest/agent-sites"),
        _member("AlobarQuest/withdrawn", dependency_updates="withdrawn"),
        _member("AlobarQuest/retired", state="retired"),
    )
    assert cli.repositories() == EXIT_OK
    assert capsys.readouterr().out.splitlines() == [
        "AlobarQuest/agent-sites",
        "AlobarQuest/crm",
        "AlobarQuest/intent-packages",
    ]


def test_with_no_grant_it_still_names_the_packages_repository(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The clone needs it whatever the record grants; the pass reports an empty lane itself."""
    _members(monkeypatch)
    assert cli.repositories() == EXIT_OK
    assert capsys.readouterr().out == "AlobarQuest/intent-packages\n"


def test_a_repository_named_twice_in_different_case_is_printed_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _members(monkeypatch, _member("alobarquest/INTENT-packages"), _member("AlobarQuest/crm"))
    assert cli.repositories() == EXIT_OK
    assert capsys.readouterr().out.splitlines() == [
        "AlobarQuest/crm",
        "AlobarQuest/intent-packages",
    ]


def test_every_repository_the_pass_reads_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pass's scope is a subset of the grants (`_lane`), so the mint never misses one."""
    members = [_member("AlobarQuest/crm"), _member("AlobarQuest/agent-sites")]
    _members(monkeypatch, *members)
    cli.repositories()
    named = {line.casefold() for line in capsys.readouterr().out.splitlines()}
    granted = cli.granted_repositories(members, "dependency_updates")
    assert {name.casefold() for name in granted} <= named


def test_an_unreadable_member_record_is_unusable_with_nothing_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreadable(**_):
        raise MembersUnreadable("SDS_MEMBERS_OBSERVER_TOKEN is not set")

    monkeypatch.setattr(cli, "read_members_from_environment", unreadable)
    assert cli.repositories() == EXIT_UNUSABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SDS_MEMBERS_OBSERVER_TOKEN is not set" in captured.err


def _entrypoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["bump-proposer", *argv])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    captured = capsys.readouterr()
    return int(stopped.value.code or 0), captured.out, captured.err


def test_the_console_script_dispatches_the_subcommand(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _members(monkeypatch, _member("AlobarQuest/crm"))
    code, out, _ = _entrypoint(monkeypatch, capsys, "repositories")
    assert code == EXIT_OK
    assert out.splitlines() == ["AlobarQuest/crm", "AlobarQuest/intent-packages"]


def test_an_extra_argument_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _members(monkeypatch, _member("AlobarQuest/crm"))
    code, out, err = _entrypoint(monkeypatch, capsys, "repositories", "--submit")
    assert code == EXIT_UNUSABLE
    assert out == ""
    assert "usage: bump-proposer repositories" in err


def test_a_pass_is_still_a_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Anything but `repositories` first goes to `run`, with the arguments it was given."""
    seen: list[list[str] | None] = []
    monkeypatch.setattr(cli, "run", lambda argv=None: seen.append(argv) or EXIT_OK)
    code, _, _ = _entrypoint(monkeypatch, capsys, "--submit", "--repository", "AlobarQuest/crm")
    assert code == EXIT_OK
    assert seen == [["--submit", "--repository", "AlobarQuest/crm"]]
