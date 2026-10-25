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
    result = CliRunner().invoke(cli.app, ["run", "--push", "--cache", str(tmp_path)])
    assert result.exit_code == cli.EXIT_HELD
    assert "AlobarQuest/a: held" in result.output


def test_an_unknown_fork_is_refused(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.app, ["run", "--fork", "x/y", "--cache", str(tmp_path)])
    assert result.exit_code == 1
    assert "x/y has no hardening rule" in result.output


def test_every_fork_with_rules_is_the_fork_not_upstream() -> None:
    assert {fork.repository.split("/")[0] for fork in rules.FORKS} == {"AlobarQuest"}
