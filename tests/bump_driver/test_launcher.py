"""The launcher: an outcome alarm, on the hour's fiftieth minute, one variable per secret."""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

LAUNCHER = Path("scripts/run-bump-driver.sh")
PLIST = Path("scripts/com.devon.bump-driver.plist")


def _text() -> str:
    return LAUNCHER.read_text()


def test_it_is_an_outcome_alarm_and_never_arms_the_switch() -> None:
    text = _text()
    assert 'sds_outcome_begin "$CHECK_NAME" bump-driver "$_acting"' in text
    assert 'sds_outcome_end "$rc"' in text
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
