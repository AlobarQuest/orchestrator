"""Part 1a: exit 2 reaches a person as one keyed alert per application."""

from __future__ import annotations

from pathlib import Path

from revision_watcher import alerts, cli
from revision_watcher.census import BEHIND, CURRENT, DIVERGED, UNREADABLE, Pass
from tests.revision_watcher.conftest import OLD, Hub
from tests.revision_watcher.test_cli import SUBJECT, _drive, _reading

KEY = alerts.application_key(SUBJECT.name)


def test_a_behind_application_fires_then_resolves(monkeypatch, hub: Hub) -> None:
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(BEHIND, served=OLD)]))
    assert result.exit_code == cli.EXIT_FOUND
    assert hub.keys("firing") == [KEY]
    assert hub.remembered() == {KEY}

    hub.sent.clear()
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(CURRENT)]))
    assert result.exit_code == cli.EXIT_CLEAN
    assert hub.keys("resolved") == [KEY]
    assert hub.remembered() == set()


def test_a_diverged_application_fires(monkeypatch, hub: Hub) -> None:
    _drive(monkeypatch, Pass(readings=[_reading(DIVERGED, served=OLD)]))
    assert hub.keys("firing") == [KEY]


def test_a_standing_finding_is_told_once(monkeypatch, hub: Hub) -> None:
    for _ in range(2):
        _drive(monkeypatch, Pass(readings=[_reading(BEHIND, served=OLD)]))
    assert hub.keys("firing") == [KEY]
    assert len(hub.sent) == 1


def test_an_unreadable_application_leaves_its_remembered_key_alone(monkeypatch, hub: Hub) -> None:
    _drive(monkeypatch, Pass(readings=[_reading(BEHIND, served=OLD)]))
    hub.sent.clear()
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(UNREADABLE, served=None)]))
    assert result.exit_code == cli.EXIT_INCOMPLETE
    assert hub.sent == []
    assert hub.remembered() == {KEY}


def test_an_undeclared_application_raises_nothing(monkeypatch, hub: Hub) -> None:
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(CURRENT)], undeclared=["newcomer"]))
    assert result.exit_code == cli.EXIT_CLEAN
    assert hub.sent == []


def test_a_failed_send_is_incomplete_after_the_work_was_done(monkeypatch, hub: Hub) -> None:
    hub.status = 500
    result, recorder = _drive(monkeypatch, Pass(readings=[_reading(BEHIND, served=OLD)]))
    assert result.exit_code == cli.EXIT_INCOMPLETE
    assert len(recorder.rows) == 1, "the pass filed its rows before the send failed"


def test_an_unusable_state_directory_is_incomplete(monkeypatch, hub: Hub, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv(alerts.STATE_DIR_ENV, str(blocker / "state"))
    result, recorder = _drive(monkeypatch, Pass(readings=[_reading(BEHIND, served=OLD)]))
    assert result.exit_code == cli.EXIT_INCOMPLETE
    assert len(recorder.rows) == 1
    assert hub.sent == []


def test_a_dry_run_sends_nothing_and_needs_no_token(monkeypatch, hub: Hub) -> None:
    monkeypatch.delenv(alerts.TOKEN_ENV)
    result, _ = _drive(
        monkeypatch, Pass(readings=[_reading(BEHIND, served=OLD)]), argv=["--dry-run"]
    )
    assert result.exit_code == cli.EXIT_FOUND
    assert hub.sent == []


def test_an_acting_pass_without_the_alert_token_is_the_tool_failing(monkeypatch, hub: Hub) -> None:
    monkeypatch.delenv(alerts.TOKEN_ENV)
    result, recorder = _drive(monkeypatch, Pass(readings=[_reading(BEHIND, served=OLD)]))
    assert result.exit_code == cli.EXIT_TOOL_FAILED
    assert recorder.rows == []
