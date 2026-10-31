"""Keyed alerts: a proposal waiting 48 hours fires, a decided one resolves, an unread pass holds."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from rotation_decomposer import cli
from rotation_decomposer.alerts import decomposition_key, sync_alerts
from rotation_decomposer.outcome import Outcome
from tests.rotation_decomposer.test_pass import RECORD, FakeOrchestrator

NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
SUBJECT = "record 139 (rotation-example rev 2)"
WAITING = Outcome(SUBJECT, "awaiting-approval", "a proposal waits for a person on /review")


class Hub:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        return httpx.Response(202, json={})


@pytest.fixture
def hub() -> Hub:
    return Hub()


def _sync(hub: Hub, tmp_path: Path, outcomes: list[Outcome], hours: float = 0) -> str | None:
    return sync_alerts(
        outcomes,
        token="t",
        state_dir=tmp_path,
        now=NOW + timedelta(hours=hours),
        transport=httpx.MockTransport(hub),
    )


def _sent(hub: Hub) -> list[tuple[str, str]]:
    return [(row["key"], row["status"]) for row in hub.sent]


def test_a_proposal_waiting_48_hours_alerts(hub: Hub, tmp_path: Path) -> None:
    _sync(hub, tmp_path, [WAITING])
    _sync(hub, tmp_path, [WAITING], 47.9)
    assert hub.sent == []
    _sync(hub, tmp_path, [WAITING], 48)
    _sync(hub, tmp_path, [WAITING], 72)
    assert _sent(hub) == [(decomposition_key(SUBJECT), "firing")]
    assert "48 hours" in hub.sent[0]["message"]


def test_a_decided_proposal_resolves(hub: Hub, tmp_path: Path) -> None:
    key = decomposition_key(SUBJECT)
    _sync(hub, tmp_path, [WAITING])
    _sync(hub, tmp_path, [WAITING], 49)
    _sync(hub, tmp_path, [Outcome(SUBJECT, "approved", "x")], 50)
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]


def test_a_proposal_that_is_not_waiting_never_alerts(hub: Hub, tmp_path: Path) -> None:
    for result in ("approved", "proposed", "declined", "refused", "superseded", "would-propose"):
        _sync(hub, tmp_path, [Outcome(SUBJECT, result, "x")])
        _sync(hub, tmp_path, [Outcome(SUBJECT, result, "x")], 100)
    assert hub.sent == []


def test_an_unread_pass_holds_every_key(hub: Hub, tmp_path: Path) -> None:
    key = decomposition_key(SUBJECT)
    _sync(hub, tmp_path, [WAITING])
    _sync(hub, tmp_path, [WAITING], 49)
    unread = Outcome("record 140 (rotation-other rev 1)", "unread", "503")
    assert _sync(hub, tmp_path, [unread], 50) is None
    assert _sent(hub) == [(key, "firing")]
    # The clock was held too: the wait is still 49 hours old, not restarted.
    _sync(hub, tmp_path, [WAITING], 51)
    assert _sent(hub) == [(key, "firing")]
    _sync(hub, tmp_path, [], 52)
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]


def test_an_unusable_state_directory_is_named(hub: Hub, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert _sync(hub, blocker / "sub", [WAITING]) is not None
    assert hub.sent == []


def _main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcomes: list[Outcome]) -> list[Any]:
    """Run `cli.main` over a pass that returns `outcomes`, recording what it would sync."""
    synced: list[Any] = []

    class Reader:
        def __init__(self, **_: Any) -> None: ...
        def work_records(self) -> list[dict[str, Any]]:
            return [RECORD]

        def close(self) -> None: ...

    class Orchestrator(FakeOrchestrator):
        def __init__(self, **_: Any) -> None:
            super().__init__()

        def close(self) -> None: ...

    monkeypatch.setenv("CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "o")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-system")
    monkeypatch.setenv("ROTATION_DECOMPOSER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "ChangeManagerReader", Reader)
    monkeypatch.setattr(cli, "Orchestrator", Orchestrator)
    monkeypatch.setattr(cli, "default_mirror_root", lambda program: tmp_path / program)
    monkeypatch.setattr(cli, "run_pass", lambda *args, **kwargs: outcomes)
    monkeypatch.setattr(
        cli, "sync_alerts", lambda *a, token, state_dir, now: synced.append((a, token, state_dir))
    )
    return synced


def test_a_dry_run_sends_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    synced = _main(monkeypatch, tmp_path, [WAITING])
    monkeypatch.delenv("SDS_BRIDGE_ALERT_TOKEN", raising=False)
    assert cli.main(["--dry-run"]) == 0
    assert synced == []


def test_a_writing_pass_syncs_with_the_sender_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    synced = _main(monkeypatch, tmp_path, [WAITING])
    monkeypatch.setenv("SDS_BRIDGE_ALERT_TOKEN", "sender")
    assert cli.main([]) == 0
    assert synced == [(([WAITING],), "sender", tmp_path / "state")]


def test_a_writing_pass_without_the_sender_token_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    synced = _main(monkeypatch, tmp_path, [WAITING])
    monkeypatch.delenv("SDS_BRIDGE_ALERT_TOKEN", raising=False)
    assert cli.main([]) == 1
    assert synced == []


def test_a_sync_failure_keeps_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _main(monkeypatch, tmp_path, [Outcome(SUBJECT, "declined", "x")])
    monkeypatch.setenv("SDS_BRIDGE_ALERT_TOKEN", "sender")
    monkeypatch.setattr(cli, "sync_alerts", lambda *a, **k: "alert-hub said no")
    assert cli.main([]) == cli.FOUND
    assert "alert-hub said no" in capsys.readouterr().err
