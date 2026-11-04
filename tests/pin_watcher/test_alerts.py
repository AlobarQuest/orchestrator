"""Part 1a: exit 2 reaches a person as one keyed alert per repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from pin_watcher import alerts
from tests.pin_watcher.conftest import RECOMMENDED, Estate, Hub, behind, identical
from tests.pin_watcher.test_cli import _run as _invoke


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIN_WATCHER_GITHUB_TOKEN", "gh")
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://sds.example.net")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-observer")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "bearer")


PIN = "b" * 40
DATE = "2026-08-20T09:00:00Z"
KEY = alerts.repository_key("o/b")


def _run(monkeypatch, estate: Estate, orchestrator, *args: str):
    """One pass in its own patch context, so a test can run two passes against two estates."""
    with monkeypatch.context() as patch:
        return _invoke(patch, estate, orchestrator, *args)


def _drifted() -> Estate:
    return Estate(
        callers={"o/a": RECOMMENDED, "o/b": PIN},
        comparisons={RECOMMENDED: identical(), PIN: behind(23)},
        dates={PIN: DATE},
    )


def _current() -> Estate:
    return Estate(
        callers={"o/a": RECOMMENDED, "o/b": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
    )


def test_a_drifted_caller_fires_then_resolves(monkeypatch, orchestrator, hub: Hub) -> None:
    assert _run(monkeypatch, _drifted(), orchestrator).exit_code == 2
    assert hub.keys("firing") == [KEY]
    assert hub.remembered() == {KEY}

    hub.sent.clear()
    assert _run(monkeypatch, _current(), orchestrator).exit_code == 0
    assert hub.keys("resolved") == [KEY]
    assert hub.remembered() == set()


def test_a_standing_finding_is_told_once(monkeypatch, orchestrator, hub: Hub) -> None:
    for _ in range(2):
        _run(monkeypatch, _drifted(), orchestrator)
    assert len(hub.sent) == 1


def test_an_unreadable_repository_leaves_its_remembered_key_alone(
    monkeypatch, orchestrator, hub: Hub
) -> None:
    _run(monkeypatch, _drifted(), orchestrator)
    hub.sent.clear()
    estate = Estate(
        callers={"o/a": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
        unreadable={"o/b"},
    )
    assert _run(monkeypatch, estate, orchestrator).exit_code == 3
    assert hub.sent == []
    assert hub.remembered() == {KEY}


def test_a_failed_send_is_incomplete_after_the_rows_were_filed(
    monkeypatch, orchestrator, written, hub: Hub
) -> None:
    hub.status = 500
    assert _run(monkeypatch, _drifted(), orchestrator).exit_code == 3
    assert len(written) == 2


def test_an_unusable_state_directory_is_incomplete(
    monkeypatch, orchestrator, written, hub: Hub, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv(alerts.STATE_DIR_ENV, str(blocker / "state"))
    assert _run(monkeypatch, _drifted(), orchestrator).exit_code == 3
    assert len(written) == 2
    assert hub.sent == []


def test_a_dry_run_sends_nothing_and_needs_no_token(monkeypatch, orchestrator, hub: Hub) -> None:
    monkeypatch.delenv(alerts.TOKEN_ENV)
    assert _run(monkeypatch, _drifted(), orchestrator, "--dry-run").exit_code == 2
    assert hub.sent == []


def test_an_acting_pass_without_the_alert_token_is_the_tool_failing(
    monkeypatch, orchestrator, written
) -> None:
    monkeypatch.delenv(alerts.TOKEN_ENV)
    assert _run(monkeypatch, _drifted(), orchestrator).exit_code == 1
    assert written == []
