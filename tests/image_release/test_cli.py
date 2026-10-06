"""`image-release bind` through its real entry point: its tokens, its refusals, its exit codes.

Exercised through the Typer app rather than only through `release_pass`, because the options, the
credential checks and the exit code are not reachable from the function beneath -- and a person
running the deploy reads nothing but the output and the code.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from image_release.cli import (
    EXIT_CONDITION,
    EXIT_INCOMPLETE,
    EXIT_OK,
    EXIT_USAGE,
    SYSTEM_TOKEN_VARIABLE,
    VERIFIER_TOKEN_VARIABLE,
    app,
)
from tests.image_release.conftest import (
    DIGEST,
    OTHER_DIGEST,
    FakeProduction,
    FakeSystem,
    FakeVerifier,
    History,
    candidate_row,
)

runner = CliRunner()


class Installed:
    def __init__(self, system: FakeSystem, production: FakeProduction) -> None:
        self.system = system
        self.production = production
        self.verifier = FakeVerifier()
        self.opened: dict[str, dict[str, Any]] = {}
        self.closed: list[str] = []


def _install(
    monkeypatch: pytest.MonkeyPatch, system: FakeSystem, production: FakeProduction
) -> Installed:
    installed = Installed(system, production)

    def factory(name: str, fake: Any) -> Any:
        class Opened:
            def __init__(self, **kwargs: Any) -> None:
                installed.opened[name] = kwargs

            def __getattr__(self, attribute: str) -> Any:
                return getattr(fake, attribute)

            def close(self) -> None:
                installed.closed.append(name)

        return Opened

    monkeypatch.setattr("image_release.cli.SystemClient", factory("system", system))
    monkeypatch.setattr("image_release.cli.AnonymousClient", factory("anonymous", production))
    monkeypatch.setattr("image_release.cli.VerifierClient", factory("verifier", installed.verifier))
    monkeypatch.setenv(SYSTEM_TOKEN_VARIABLE, "system-bearer-stand-in")
    monkeypatch.setenv(VERIFIER_TOKEN_VARIABLE, "verifier-bearer-stand-in")
    return installed


def _invoke(history: History, *extra: str, observed: str = DIGEST, built: str = "") -> Any:
    return runner.invoke(
        app,
        [
            "bind",
            "--repository",
            "AlobarQuest/orchestrator",
            "--checkout",
            str(history.path),
            "--built-commit",
            built or history.built,
            "--digest",
            DIGEST,
            "--observed-digest",
            observed,
            "--image-name",
            "orchestrator",
            "--tag",
            "readable-amd64",
            "--workflow-run-url",
            "https://github.com/AlobarQuest/orchestrator/actions/runs/1",
            "--base-url",
            "https://sds.example.invalid",
            "--deployer",
            "hq-session",
            *extra,
        ],
    )


def test_a_full_pass_exits_zero_and_verifies_as_the_verifier(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _install(
        monkeypatch, FakeSystem([candidate_row(history.merge)]), FakeProduction(history.built)
    )

    result = _invoke(history)

    assert result.exit_code == EXIT_OK, result.output
    summary = json.loads(result.output)
    assert summary["units"][0]["verification"]["outcome"] == "completed"
    assert len(installed.verifier.verified) == 1
    # Each identity's bearer goes to its own client and nowhere else.
    assert installed.opened["system"]["token"] == "system-bearer-stand-in"
    assert installed.opened["system"]["credential_key_id"] == "orchestrator-system"
    assert installed.opened["verifier"]["token"] == "verifier-bearer-stand-in"
    assert installed.opened["verifier"]["credential_key_id"] == "orchestrator-verifier"
    assert "token" not in installed.opened["anonymous"]
    assert sorted(installed.closed) == ["anonymous", "system", "verifier"]


def test_a_digest_mismatch_refuses_before_anything_is_read(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _install(
        monkeypatch, FakeSystem([candidate_row(history.merge)]), FakeProduction(history.built)
    )

    result = _invoke(history, observed=OTHER_DIGEST)

    assert result.exit_code == EXIT_CONDITION
    assert installed.opened == {}
    assert installed.system.bound == []


def test_a_served_revision_mismatch_exits_two(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, FakeSystem([candidate_row(history.merge)]), FakeProduction(history.later))
    assert _invoke(history).exit_code == EXIT_CONDITION


def test_an_unreadable_checkout_exits_three(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, FakeSystem([candidate_row(history.merge)]), FakeProduction("d" * 40))
    assert _invoke(history, built="d" * 40).exit_code == EXIT_INCOMPLETE


@pytest.mark.parametrize("missing", [SYSTEM_TOKEN_VARIABLE, VERIFIER_TOKEN_VARIABLE])
def test_each_token_is_required_for_a_real_run(
    history: History, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    installed = _install(
        monkeypatch, FakeSystem([candidate_row(history.merge)]), FakeProduction(history.built)
    )
    monkeypatch.delenv(missing)

    result = _invoke(history)

    assert result.exit_code == EXIT_USAGE
    assert missing in result.output
    assert installed.opened == {}


def test_a_dry_run_needs_no_verifier_token_and_writes_nothing(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _install(
        monkeypatch, FakeSystem([candidate_row(history.merge)]), FakeProduction(history.built)
    )
    monkeypatch.delenv(VERIFIER_TOKEN_VARIABLE)

    result = _invoke(history, "--dry-run")

    assert result.exit_code == EXIT_OK, result.output
    assert "verifier" not in installed.opened
    assert installed.system.bound == [] and installed.system.observed == []
    assert json.loads(result.output)["units"][0]["binding"]["dry_run"] is True


def test_a_dry_run_still_needs_the_system_token(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, FakeSystem([]), FakeProduction(history.built))
    monkeypatch.delenv(SYSTEM_TOKEN_VARIABLE)
    assert _invoke(history, "--dry-run").exit_code == EXIT_USAGE


@pytest.mark.parametrize(
    "arguments",
    [("--built-commit", "abc123"), ("--digest", "sha256:short")],
    ids=["short-sha", "short-digest"],
)
def test_a_malformed_identity_is_a_usage_error(
    history: History, monkeypatch: pytest.MonkeyPatch, arguments: tuple[str, str]
) -> None:
    installed = _install(monkeypatch, FakeSystem([]), FakeProduction(history.built))
    # Typer takes the last occurrence of a repeated option.
    result = _invoke(history, *arguments)
    assert result.exit_code == EXIT_USAGE
    assert installed.opened == {}


def test_an_unusable_base_url_is_a_usage_error(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SYSTEM_TOKEN_VARIABLE, "s")
    monkeypatch.setenv(VERIFIER_TOKEN_VARIABLE, "v")
    result = _invoke(history, "--base-url", "http://insecure")
    assert result.exit_code == EXIT_USAGE
    assert "--base-url" in result.output
