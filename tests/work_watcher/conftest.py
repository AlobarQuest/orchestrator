from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _acting_passes_may_send_alerts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An acting pass needs the sender token and a state directory (bridge Part 3).

    Nothing is sent: a pass these tests run raises no alert, so the client is built and never
    asked to send. `test_alerts.py` injects its own sender wherever an alert is raised.
    """
    monkeypatch.setenv("SDS_BRIDGE_ALERT_TOKEN", "not-a-real-token")
    monkeypatch.setenv("WORK_WATCHER_STATE_DIR", str(tmp_path / "alert-state"))
