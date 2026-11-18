"""`estate-landing repositories`: the repositories the lane's GitHub read token must name.

The lane's container mints its read token for exactly what this prints (ADR-0059 decision 2), so
it must name every repository a pass reads GitHub for, and must print nothing when it can't derive
the list.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from bump_proposer.landing_policy import LandingPolicy, LandingPolicyError
from estate_lander import cli
from estate_lander.cli import EXIT_OK, EXIT_UNUSABLE


def _rule(*deploying: str) -> LandingPolicy:
    """The declaration, as `bump_proposer.landing_policy` parses it: the real dataclass."""
    return LandingPolicy(
        version=14,
        inert=frozenset({"alobarquest/agent-sites"}),
        permitted_authors=frozenset({"dependabot[bot]"}),
        inert_excluded_ecosystems=frozenset({"docker"}),
        deploying=frozenset(name.lower() for name in deploying),
        deploying_excluded_ecosystems=frozenset({"github_actions"}),
    )


def _policy(monkeypatch: pytest.MonkeyPatch, rule: LandingPolicy) -> list[tuple[Any, Any]]:
    calls: list[tuple[Any, Any]] = []

    def read(token: str, **kwargs: Any) -> LandingPolicy:
        calls.append((token, kwargs))
        return rule

    monkeypatch.setenv("ESTATE_LANDING_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.delenv("ESTATE_LANDING_CHANGE_MANAGER_URL", raising=False)
    monkeypatch.setattr(cli, "read_landing_policy", read)
    return calls


def test_it_names_the_deploying_population_sorted_once_each(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _policy(
        monkeypatch, _rule("AlobarQuest/change-manager", "alobarquest/BRAIN", "AlobarQuest/brain")
    )
    assert cli.repositories() == EXIT_OK
    assert capsys.readouterr().out.splitlines() == [
        "alobarquest/brain",
        "alobarquest/change-manager",
    ]
    # The READ credential the pass reads its records with, at production's URL by default.
    assert calls == [("cm", {"base_url": "https://change-mgr.alobar.net"})]


def test_it_reads_the_policy_at_the_configured_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _policy(monkeypatch, _rule("alobarquest/brain"))
    monkeypatch.setenv("ESTATE_LANDING_CHANGE_MANAGER_URL", "https://cm.example")
    assert cli.repositories() == EXIT_OK
    assert calls == [("cm", {"base_url": "https://cm.example"})]


def test_the_inert_population_is_not_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The inert block is the inert lander's; this lane's landings are never there."""
    rule = _rule("alobarquest/brain")
    _policy(monkeypatch, rule)
    assert cli.repositories() == EXIT_OK
    printed = set(capsys.readouterr().out.splitlines())
    assert printed == set(rule.deploying)
    assert not printed & rule.inert


def test_a_policy_with_no_deploying_population_prints_nothing_and_is_unusable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parse reads an absent population as empty; that is not a list of none."""
    _policy(monkeypatch, _rule())
    assert cli.repositories() == EXIT_UNUSABLE
    out = capsys.readouterr()
    assert out.out == ""
    assert "[UNUSABLE] the landing policy declares no deploying population" in out.err


def test_an_unreadable_policy_prints_nothing_and_is_unusable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_: Any, **__: Any) -> LandingPolicy:
        raise LandingPolicyError("change-manager answered 403 for /api/landing-policy")

    monkeypatch.setenv("ESTATE_LANDING_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setattr(cli, "read_landing_policy", refuse)
    assert cli.repositories() == EXIT_UNUSABLE
    out = capsys.readouterr()
    assert out.out == ""
    assert "[UNUSABLE] the landing policy could not be read: change-manager answered 403" in out.err


def test_a_missing_credential_prints_nothing_and_is_unusable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ESTATE_LANDING_CHANGE_MANAGER_TOKEN", raising=False)
    monkeypatch.setattr(cli, "read_landing_policy", pytest.fail)
    assert cli.repositories() == EXIT_UNUSABLE
    out = capsys.readouterr()
    assert out.out == ""
    assert "ESTATE_LANDING_CHANGE_MANAGER_TOKEN is unset" in out.err


def test_main_runs_the_subcommand_and_refuses_extra_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _policy(monkeypatch, _rule("alobarquest/brain"))
    monkeypatch.setattr(sys, "argv", ["estate-landing", "repositories"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == EXIT_OK
    assert capsys.readouterr().out == "alobarquest/brain\n"

    monkeypatch.setattr(sys, "argv", ["estate-landing", "repositories", "--submit"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == EXIT_UNUSABLE
    out = capsys.readouterr()
    assert out.out == ""
    assert "usage: estate-landing repositories" in out.err


@pytest.mark.parametrize("argv", [[], ["--submit"]], ids=["bare", "submit"])
def test_main_still_runs_the_pass_with_its_arguments(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    seen: list[list[str] | None] = []
    monkeypatch.setattr(cli, "run", lambda argv=None: seen.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", ["estate-landing", *argv])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 0
    assert seen == [argv]
