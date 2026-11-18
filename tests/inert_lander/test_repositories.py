"""`inert-landing repositories`: the repositories the lane's GitHub read token must name.

The lane's container mints its read token for exactly what this prints (ADR-0059 decision 2), so
it must name every repository a pass lists pull requests in, and must print nothing when it can't
derive the list.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from bump_proposer.landing_policy import LandingPolicy, LandingPolicyError
from inert_lander import cli
from inert_lander.cli import EXIT_OK, EXIT_UNUSABLE
from tests.inert_lander.test_inert_lander import REPOSITORY, FakeGitHub, _rule


def _policy(monkeypatch: pytest.MonkeyPatch, rule: LandingPolicy) -> list[tuple[Any, Any]]:
    calls: list[tuple[Any, Any]] = []

    def read(token: str, **kwargs: Any) -> LandingPolicy:
        calls.append((token, kwargs))
        return rule

    monkeypatch.setenv("INERT_LANDING_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setattr(cli, "read_landing_policy", read)
    return calls


def test_it_names_the_inert_population_sorted_once_each(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _policy(
        monkeypatch, _rule("AlobarQuest/zeta", "alobarquest/ALPHA", "AlobarQuest/alpha")
    )
    assert cli.repositories() == EXIT_OK
    assert capsys.readouterr().out.splitlines() == ["alobarquest/alpha", "alobarquest/zeta"]
    # The READ credential the pass reads the policy with, at production's URL by default.
    assert calls == [("cm", {"base_url": "https://change-mgr.alobar.net"})]


def test_it_reads_the_policy_at_the_configured_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _policy(monkeypatch, _rule())
    monkeypatch.setenv("INERT_LANDING_CHANGE_MANAGER_URL", "https://cm.example")
    assert cli.repositories() == EXIT_OK
    assert calls == [("cm", {"base_url": "https://cm.example"})]


def test_the_deploying_population_is_not_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The deploying block is the bump proposer's to read; this lane never lists its pull
    requests, so its token must not reach them."""
    rule = _rule()
    assert rule.deploying
    _policy(monkeypatch, rule)
    assert cli.repositories() == EXIT_OK
    printed = set(capsys.readouterr().out.splitlines())
    assert printed == set(rule.inert)
    assert not printed & rule.deploying


def test_every_repository_the_pass_lists_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Held against the pass itself: each repository `_subjects` enumerates is printed."""
    rule = _rule(REPOSITORY, "AlobarQuest/other")
    _policy(monkeypatch, rule)
    assert cli.repositories() == EXIT_OK
    printed = capsys.readouterr().out.splitlines()
    github = FakeGitHub()
    cli._subjects(github, rule)
    assert sorted(github.enumerated) == printed


def test_an_unreadable_policy_prints_nothing_and_is_unusable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_: Any, **__: Any) -> LandingPolicy:
        raise LandingPolicyError("change-manager answered 403 for /api/landing-policy")

    monkeypatch.setenv("INERT_LANDING_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setattr(cli, "read_landing_policy", refuse)
    assert cli.repositories() == EXIT_UNUSABLE
    out = capsys.readouterr()
    assert out.out == ""
    assert "[UNUSABLE] the landing policy could not be read: change-manager answered 403" in out.err


def test_a_missing_credential_prints_nothing_and_is_unusable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("INERT_LANDING_CHANGE_MANAGER_TOKEN", raising=False)
    monkeypatch.setattr(cli, "read_landing_policy", pytest.fail)
    assert cli.repositories() == EXIT_UNUSABLE
    out = capsys.readouterr()
    assert out.out == ""
    assert "INERT_LANDING_CHANGE_MANAGER_TOKEN is unset" in out.err


def test_main_runs_the_subcommand_and_refuses_extra_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _policy(monkeypatch, _rule())
    monkeypatch.setattr(sys, "argv", ["inert-landing", "repositories"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == EXIT_OK
    assert capsys.readouterr().out == f"{REPOSITORY.lower()}\n"

    monkeypatch.setattr(sys, "argv", ["inert-landing", "repositories", "--submit"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == EXIT_UNUSABLE
    out = capsys.readouterr()
    assert out.out == ""
    assert "usage: inert-landing repositories" in out.err


def test_main_still_runs_the_pass_with_its_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str] | None] = []
    monkeypatch.setattr(cli, "run", lambda argv=None: seen.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", ["inert-landing", "--submit"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 0
    assert seen == [["--submit"]]
