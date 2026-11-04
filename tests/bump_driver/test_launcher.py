"""The launcher: an outcome alarm, on the hour's fiftieth minute, one variable per secret."""

from __future__ import annotations

import ast
import plistlib
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bump_driver import cli

LAUNCHER = Path("scripts/run-bump-driver.sh")
PLIST = Path("scripts/com.devon.bump-driver.plist")


def _text() -> str:
    return LAUNCHER.read_text()


def test_it_is_an_outcome_alarm_and_never_arms_the_switch() -> None:
    text = _text()
    assert 'sds_outcome_begin "$CHECK_NAME" bump-driver "$_acting"' in text
    assert re.search(r'^sds_outcome_end "\$rc" --told 2$', text, re.MULTILINE)
    assert "sds-bump-driver" in text
    assert "sds_deadman" + "_arm" not in text


def test_it_runs_hourly_at_fifty_minutes_past() -> None:
    template = PLIST.read_text().replace("__REPO_ROOT__", "/r").replace("__HOME__", "/h")
    plist = plistlib.loads(template.encode())
    assert plist["StartCalendarInterval"] == {"Minute": 50}
    assert "StartInterval" not in plist


def test_each_identity_reads_its_own_secrets_and_none_is_ambient() -> None:
    text = _text()
    fetches = dict(re.findall(r'sds_bws_value "\$(\w+)" "\$(\w+)"', text))
    assert fetches == {
        "ORCHESTRATOR_SYSTEM_UUID": "NARROW_IDENTITY",
        "SDS_BRIDGE_ALERT_UUID": "NARROW_IDENTITY",
        "CHANGE_MANAGER_READ_UUID": "BROAD_IDENTITY",
        "CODE_BRAIN_KEY_UUID": "BROAD_IDENTITY",
        "INFRA_BRAIN_KEY_UUID": "BROAD_IDENTITY",
    }
    assert "export BWS_ACCESS_TOKEN" not in text
    assert "unset NARROW_IDENTITY BROAD_IDENTITY" in text


def test_it_gives_the_generator_what_a_conformance_claim_needs() -> None:
    text = _text()
    assert 'PATH="$REPO_ROOT/.venv/bin:$HOME/Projects/intent-packages/.venv/bin:$PATH"' in text
    assert (
        'FACTORY_PYTHONPATH="$HOME/Projects/project-standards/src:'
        '$HOME/Projects/security-standards/src"'
    ) in text


def _outcome_calls() -> list[ast.Call]:
    tree = ast.parse(Path(cli.__file__).read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Outcome"
    ]


def _result(call: ast.Call) -> str | None:
    """The result word an `Outcome(...)` call names, or None when it isn't a literal or constant."""
    node = call.args[1] if len(call.args) > 1 else None
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name) and isinstance(getattr(cli, node.id, None), str):
        return str(getattr(cli, node.id))
    return None


def _keyed(call: ast.Call) -> bool:
    key = call.args[4] if len(call.args) > 4 else None
    key = key or next((k.value for k in call.keywords if k.arg == "key"), None)
    return key is not None and not (isinstance(key, ast.Constant) and key.value is None)


def test_every_outcome_that_exits_two_carries_the_key_of_its_alert() -> None:
    """The launcher pings success on exit 2 (`--told 2`), which is honest only while every
    finding is a keyed alert. A new finding result, or an `Outcome` built without its key, fails."""
    calls = _outcome_calls()
    assert calls
    unreadable = [c.lineno for c in calls if _result(c) is None]
    assert unreadable == [], f"Outcome results the test cannot resolve, lines {unreadable}"
    unkeyed = [c.lineno for c in calls if _result(c) in cli.FINDINGS and not _keyed(c)]
    assert unkeyed == [], f"finding outcomes with no alert key, lines {unkeyed}"
    assert {_result(c) for c in calls} >= cli.FINDINGS


def test_a_failed_alert_sync_exits_three_not_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    finding = cli.Outcome("u", "refused", "x", "o/r", "k")
    stub: Any = SimpleNamespace(close=lambda: None, fresh=None)
    for name in ("ChangeManagerReader", "Orchestrator", "GitHub", "Mirrors", "Generator"):
        monkeypatch.setattr(cli, name, lambda *a, **k: stub)
    monkeypatch.setattr(cli, "read_members", lambda **k: {})
    monkeypatch.setattr(cli, "granted_repositories", lambda *a: [])
    monkeypatch.setattr(cli, "factory_binary", lambda home: "factory")
    monkeypatch.setattr(cli, "_generator_environment", lambda *a: {})
    monkeypatch.setattr(cli, "run_pass", lambda *a, **k: [finding])
    stub.work_records = lambda: []
    synced = iter([None, "alert-hub said no"])
    monkeypatch.setattr(cli, "sync_alerts", lambda *a, **k: next(synced))
    environ = {
        "ORCHESTRATOR_API_TOKEN": "x",
        "ORCHESTRATOR_API_CREDENTIAL_KEY_ID": "x",
        "CHANGE_MANAGER_TOKEN": "x",
        "BUMP_DRIVER_GITHUB_TOKEN": "x",
        cli.TOKEN_ENV: "x",
    }
    args = (environ, tmp_path, tmp_path)
    assert cli._pass(*args, dry_run=False) == cli.FOUND
    assert cli._pass(*args, dry_run=False) == cli.INCOMPLETE == 3
