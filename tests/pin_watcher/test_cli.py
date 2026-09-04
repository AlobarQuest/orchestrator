"""The four exit codes, which are the whole interface a scheduled run has."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from pin_watcher import cli as cli_module
from pin_watcher.cli import app
from tests.pin_watcher.conftest import RECOMMENDED, Estate, behind, identical

runner = CliRunner()


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIN_WATCHER_GITHUB_TOKEN", "gh")
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://sds.example.net")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-observer")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "bearer")


def _run(
    monkeypatch: pytest.MonkeyPatch,
    estate: Estate,
    orchestrator: httpx.MockTransport,
    *args: str,
):
    """Invoke the real command, with both transports substituted at their construction sites."""
    real_reader = cli_module.GitHubReader
    monkeypatch.setattr(
        cli_module,
        "GitHubReader",
        lambda **kw: real_reader(**{**kw, "transport": estate.transport()}),
    )
    real_open = cli_module.open_client
    monkeypatch.setattr(
        cli_module, "open_client", lambda **kw: real_open(**{**kw, "transport": orchestrator})
    )
    return runner.invoke(app, list(args))


def test_a_clean_estate_exits_zero(monkeypatch, orchestrator, written) -> None:
    estate = Estate(
        callers={"o/a": RECOMMENDED, "o/b": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
    )
    result = _run(monkeypatch, estate, orchestrator)
    assert result.exit_code == 0, result.output
    assert len(written) == 2, "a current caller is filed too, or silence means nothing"


def test_a_drifted_caller_exits_two(monkeypatch, orchestrator, written) -> None:
    pin = "b" * 40
    estate = Estate(
        callers={"o/a": RECOMMENDED, "o/b": pin},
        comparisons={RECOMMENDED: identical(), pin: behind(23)},
        dates={pin: "2026-08-20T09:00:00Z"},
    )
    result = _run(monkeypatch, estate, orchestrator)
    assert result.exit_code == 2, result.output
    assert "23 behind" in result.output


def test_an_unreadable_repository_exits_three_even_when_a_finding_was_also_found(
    monkeypatch, orchestrator, written
) -> None:
    """3 outranks 2: an incomplete pass cannot claim it found everything there was to find."""
    pin = "b" * 40
    estate = Estate(
        callers={"o/a": pin},
        comparisons={pin: behind(4)},
        dates={pin: "2026-08-29T09:00:00Z"},
        unreadable={"o/broken"},
    )
    result = _run(monkeypatch, estate, orchestrator)
    assert result.exit_code == 3, result.output


def test_an_unreadable_recommendation_exits_three_having_measured_nothing(
    monkeypatch, orchestrator, written
) -> None:
    estate = Estate(callers={"o/a": RECOMMENDED}, recommended="not-a-sha")
    result = _run(monkeypatch, estate, orchestrator)
    assert result.exit_code == 3
    assert written == []


def test_a_row_that_could_not_be_filed_exits_three_rather_than_zero(monkeypatch) -> None:
    """A finding nobody could file is a finding nobody has. That is incomplete, not clean."""
    refusing = httpx.MockTransport(lambda request: httpx.Response(503))
    estate = Estate(callers={"o/a": RECOMMENDED}, comparisons={RECOMMENDED: identical()})
    result = _run(monkeypatch, estate, refusing)
    assert result.exit_code == 3, result.output
    assert "row not filed" in result.output


def test_a_missing_github_credential_is_the_tool_failing_rather_than_a_finding(
    monkeypatch, orchestrator, written
) -> None:
    monkeypatch.delenv("PIN_WATCHER_GITHUB_TOKEN")
    estate = Estate(callers={}, comparisons={})
    assert _run(monkeypatch, estate, orchestrator).exit_code == 1


def test_a_missing_orchestrator_credential_is_the_tool_failing(
    monkeypatch, orchestrator, written
) -> None:
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN")
    estate = Estate(callers={"o/a": RECOMMENDED}, comparisons={RECOMMENDED: identical()})
    assert _run(monkeypatch, estate, orchestrator).exit_code == 1


@pytest.mark.parametrize("url", ["https://host..example", "http://sds.example.net", "https://"])
def test_an_unusable_orchestrator_url_is_the_tool_failing_for_every_caller_at_once(
    monkeypatch, orchestrator, written, url: str
) -> None:
    """Not an incomplete pass: one typo makes the tool unusable, it does not cost one caller."""
    monkeypatch.setenv("ORCHESTRATOR_API_URL", url)
    estate = Estate(callers={"o/a": RECOMMENDED}, comparisons={RECOMMENDED: identical()})
    assert _run(monkeypatch, estate, orchestrator).exit_code == 1


def test_a_dry_run_measures_and_files_nothing(monkeypatch, orchestrator, written) -> None:
    pin = "b" * 40
    estate = Estate(
        callers={"o/a": pin},
        comparisons={pin: behind(3)},
        dates={pin: "2026-08-29T09:00:00Z"},
    )
    result = _run(monkeypatch, estate, orchestrator, "--dry-run")
    assert result.exit_code == 2, "a dry run still reports what it found"
    assert written == []


def test_the_report_names_every_caller_and_marks_only_the_findings(
    monkeypatch, orchestrator, written
) -> None:
    pin = "b" * 40
    estate = Estate(
        callers={"o/clean": RECOMMENDED, "o/stale": pin},
        comparisons={RECOMMENDED: identical(), pin: behind(9)},
        dates={pin: "2026-08-25T09:00:00Z"},
    )
    output: Any = _run(monkeypatch, estate, orchestrator).output
    assert "-> o/stale" in output
    assert "-> o/clean" not in output and "o/clean" in output
