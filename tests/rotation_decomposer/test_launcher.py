"""The launcher: an outcome alarm that reads the sender token only for an acting pass."""

from __future__ import annotations

import tomllib
from pathlib import Path

LAUNCHER = Path("scripts/run-rotation-decomposer.sh")


def _lines() -> list[str]:
    return LAUNCHER.read_text().splitlines()


def test_it_stays_an_outcome_alarm_and_never_arms_the_switch() -> None:
    text = LAUNCHER.read_text()
    assert 'sds_outcome_begin "$CHECK_NAME" rotation-decomposer "$_acting"' in text
    assert 'sds_outcome_end "$rc"' in text
    assert "sds_deadman" + "_arm" not in text


def test_the_sender_token_is_read_only_by_an_acting_pass_with_the_narrow_identity() -> None:
    lines = _lines()
    fetch = [i for i, line in enumerate(lines) if '"$SDS_BRIDGE_ALERT_UUID"' in line]
    assert len(fetch) == 1
    assert lines[fetch[0]].endswith('"$NARROW_IDENTITY")"')
    guard = max(i for i in range(fetch[0]) if lines[i] == 'if [ "$_acting" -eq 1 ]; then')
    assert lines.index("fi", guard) > fetch[0]
    assert fetch[0] < lines.index("unset NARROW_IDENTITY BROAD_IDENTITY")
    assert "  export SDS_BRIDGE_ALERT_TOKEN" in lines
    # Failure goes through the outcome alarm, as every other credential does.
    assert "sds_outcome_fail" in lines[fetch[0] + 1]


def test_the_sender_token_uuid_is_the_one_the_manifest_lists() -> None:
    manifest = tomllib.loads(Path(".bws-secrets.toml").read_text())
    listed = {
        entry["uuid"]
        for entry in manifest["secret"]
        if entry["name"] == "ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE"
    }
    assert f'SDS_BRIDGE_ALERT_UUID="{next(iter(listed))}"' in LAUNCHER.read_text()
