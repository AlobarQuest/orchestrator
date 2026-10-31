"""A finished pass pings alert-hub; a crashed one stays silent, which is the signal."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rotation_worker import cli
from rotation_worker.notify import Notifier, heartbeat_key
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_notify import Hub

CONFIG: Any = SimpleNamespace(source_revision="0123456789ab")


@pytest.fixture
def pass_with(monkeypatch: pytest.MonkeyPatch) -> Any:
    def arrange(notifier: Notifier | None, passes: Any = lambda _s: 2) -> None:
        setup = SimpleNamespace(notifier=notifier, store=MemoryStore())
        monkeypatch.setattr(cli, "build_setup", lambda _c: setup)
        monkeypatch.setattr(cli, "run_pass", passes)

    return arrange


def test_a_finished_pass_sends_one_heartbeat(pass_with: Any, tmp_path: Path) -> None:
    hub = Hub()
    pass_with(hub.notifier(tmp_path))
    assert cli.run(CONFIG) == 0
    assert [(m["key"], m["status"]) for m in hub.sent] == [
        (heartbeat_key("bws-secret"), "resolved")
    ]
    assert not (tmp_path / "announced-acts.json").exists()


def test_a_crashed_pass_sends_none(pass_with: Any, tmp_path: Path) -> None:
    hub = Hub()

    def crash(_setup: object) -> int:
        raise RuntimeError("the ledger is unreadable")

    pass_with(hub.notifier(tmp_path), crash)
    with pytest.raises(RuntimeError):
        cli.run(CONFIG)
    assert hub.sent == []


def test_a_heartbeat_failure_still_returns_0(pass_with: Any, tmp_path: Path) -> None:
    pass_with(Hub(500, 500, 500, 500).notifier(tmp_path))
    assert cli.run(CONFIG) == 0


def test_an_unreadable_token_still_returns_0(pass_with: Any, tmp_path: Path) -> None:
    def unreadable() -> Value:
        raise StoreError("the store is down")

    pass_with(Notifier(unreadable, tmp_path))
    assert cli.run(CONFIG) == 0


def test_no_notifier_means_no_heartbeat(pass_with: Any) -> None:
    pass_with(None)
    assert cli.run(CONFIG) == 0
