from __future__ import annotations

from pathlib import Path

import pytest

from tests.standing_identity.fixtures import write_package


@pytest.fixture(autouse=True)
def _acting_passes_may_send_alerts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An acting pass needs the sender token and a state directory (bridge Part 3).

    Nothing is sent: a pass these tests run raises no alert, so the client is built and never
    asked to send. `test_alerts.py` injects its own sender wherever an alert is raised.
    """
    monkeypatch.setenv("SDS_BRIDGE_ALERT_TOKEN", "not-a-real-token")
    monkeypatch.setenv("WORK_WATCHER_STATE_DIR", str(tmp_path / "alert-state"))


@pytest.fixture(autouse=True)
def packages_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The watcher reads a record's standing package from an intent-packages checkout.

    Without this the default is the developer's real `~/Projects/intent-packages`, so a test
    passes on a machine that has the package and records an extra "unreadable" finding on one
    that does not (CI). The checkout is a temp directory holding the package the shared
    `record()` helper names; a test wanting another package writes it here.
    """
    root = tmp_path / "intent-packages"
    write_package(
        root,
        "infraops-mcp-server-npm-eslint",
        repository="AlobarQuest/infraops-mcp-server",
        dependency="eslint",
    )
    monkeypatch.setenv("WORK_WATCHER_PACKAGES_CHECKOUT", str(root))
    return root
