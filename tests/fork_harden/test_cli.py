"""`fork-harden run`: dormant when no fork has a rule, and its exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fork_harden import cli, git, rules
from fork_harden.harden import Outcome, Status
from fork_harden.rules import Fork

A = Fork("AlobarQuest/a", ())
B = Fork("AlobarQuest/b", ())


def test_a_pass_with_no_fork_reads_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def no_read(*_: object) -> None:
        raise AssertionError("a pass with no fork must not read")

    monkeypatch.setattr(git, "open_cache", no_read)
    lines, code = cli.run_pass((), tmp_path, push=True)
    assert code == 0
    assert lines == ["no fork has a hardening rule; nothing read"]


def _fake(statuses: dict[str, Status | None]):
    def fake_harden(repo: git.Repo, fork: Fork, *, push: bool) -> Outcome:
        status = statuses[fork.repository]
        if status is None:
            raise git.GitError("unreachable")
        return Outcome(fork, status)

    return fake_harden


@pytest.mark.parametrize(
    ("statuses", "code"),
    [
        ({"AlobarQuest/a": "advanced", "AlobarQuest/b": "current"}, 0),
        ({"AlobarQuest/a": "would_advance", "AlobarQuest/b": "current"}, 0),
        ({"AlobarQuest/a": None, "AlobarQuest/b": "current"}, cli.EXIT_UNREADABLE),
        ({"AlobarQuest/a": "held", "AlobarQuest/b": "current"}, cli.EXIT_HELD),
        ({"AlobarQuest/a": None, "AlobarQuest/b": "held"}, cli.EXIT_HELD),
    ],
)
def test_held_outranks_unreadable_and_every_fork_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    statuses: dict[str, Status | None],
    code: int,
) -> None:
    monkeypatch.setattr(cli, "harden", _fake(statuses))
    lines, got = cli.run_pass((A, B), tmp_path, push=True)
    assert got == code
    assert [line.split(":")[0] for line in lines] == ["AlobarQuest/a", "AlobarQuest/b"]


def test_the_command_exits_with_the_pass_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "FORKS", (A,))
    monkeypatch.setattr(cli, "harden", _fake({"AlobarQuest/a": "held"}))
    result = CliRunner().invoke(
        cli.app, ["run", "--push", "--granted", "AlobarQuest/a", "--cache", str(tmp_path)]
    )
    assert result.exit_code == cli.EXIT_HELD
    assert "AlobarQuest/a: held" in result.output


def test_an_unknown_fork_is_refused(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app, ["run", "--fork", "x/y", "--granted", "x/y", "--cache", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "x/y has no hardening rule" in result.output


def test_every_fork_with_rules_is_the_fork_not_upstream() -> None:
    assert {fork.repository.split("/")[0] for fork in rules.FORKS} == {"AlobarQuest"}


def test_a_path_that_is_not_utf8_is_printed_escaped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    name = b"src/caf\xe9.sh".decode("utf-8", "surrogateescape")

    def held(repo: git.Repo, fork: Fork, *, push: bool) -> Outcome:
        return Outcome(fork, "held", reasons=[f"{name} is not UTF-8 text"])

    monkeypatch.setattr(cli, "FORKS", (A,))
    monkeypatch.setattr(cli, "harden", held)
    result = CliRunner().invoke(
        cli.app, ["run", "--granted", "AlobarQuest/a", "--cache", str(tmp_path)]
    )
    assert result.exit_code == cli.EXIT_HELD
    assert "src/caf\\udce9.sh is not UTF-8 text" in result.output


def _hardened_repositories(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def fake_harden(repo: git.Repo, fork: Fork, *, push: bool) -> Outcome:
        seen.append(fork.repository)
        return Outcome(fork, "current")

    monkeypatch.setattr(cli, "FORKS", (A, B))
    monkeypatch.setattr(cli, "harden", fake_harden)
    monkeypatch.setattr(git, "open_cache", lambda *_: None)
    return seen


def test_only_granted_forks_are_hardened(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = _hardened_repositories(monkeypatch)
    result = CliRunner().invoke(
        cli.app, ["run", "--granted", "alobarquest/A", "--cache", str(tmp_path)]
    )
    assert seen == ["AlobarQuest/a"]
    assert result.exit_code == cli.EXIT_UNREADABLE


def test_an_ungranted_fork_is_skipped_and_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _hardened_repositories(monkeypatch)
    result = CliRunner().invoke(
        cli.app, ["run", "--granted", "AlobarQuest/a", "--cache", str(tmp_path)]
    )
    assert seen == ["AlobarQuest/a"]
    assert result.exit_code == cli.EXIT_UNREADABLE
    assert "fork-harden: AlobarQuest/b is not granted security patches; skipped" in result.output


def test_nothing_granted_hardens_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = _hardened_repositories(monkeypatch)
    result = CliRunner().invoke(cli.app, ["run", "--cache", str(tmp_path)])
    assert seen == []
    assert result.exit_code == cli.EXIT_UNREADABLE


def test_a_granted_name_without_rules_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _hardened_repositories(monkeypatch)
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--granted", "AlobarQuest/a",
            "--granted", "AlobarQuest/b",
            "--granted", "AlobarQuest/rtk",
            "--cache", str(tmp_path),
        ],
    )  # fmt: skip
    assert seen == ["AlobarQuest/a", "AlobarQuest/b"]
    assert result.exit_code == 0
    assert "skipped" not in result.output
