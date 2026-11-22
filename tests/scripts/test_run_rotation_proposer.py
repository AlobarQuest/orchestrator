"""`scripts/run-rotation-proposer.sh` runs only in the lane's container and refuses elsewhere."""

from __future__ import annotations

import subprocess
from pathlib import Path

LAUNCHER = Path("scripts/run-rotation-proposer.sh")


def test_outside_the_container_it_refuses_and_reads_nothing(tmp_path: Path) -> None:
    """No `BWS_ACCESS_TOKEN` and no program on PATH: it exits 1 with the FATAL line, so no secret
    was read and no program ran (a missing program would have said so instead)."""
    result = subprocess.run(
        ["/bin/bash", str(LAUNCHER.resolve()), "--submit"],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert (
        "the rotation-proposer lane runs only in its container, through lane-run" in result.stderr
    )
    assert "no rotation-proposer" not in result.stderr


def test_the_launcher_reads_the_bridge_token_only_on_submit() -> None:
    """The container's fetch, with the lane's token (`test_run_rotation_proposer_lane.py` runs it),
    sits inside the `--submit` gate."""
    lines = LAUNCHER.read_text().splitlines()
    gate = 'if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then'
    fetch = [i for i, line in enumerate(lines) if 'sds_bws_value "$SDS_BRIDGE_ALERT_UUID"' in line]
    assert len(fetch) == 1
    at = fetch[0]
    block = max(i for i, line in enumerate(lines[:at]) if line.strip() == gate)
    indent = lines[block][: len(lines[block]) - len(lines[block].lstrip())]
    assert lines.index(f"{indent}fi", block) > at
    assert lines[at].strip() == 'SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID")"'


def test_the_launcher_keeps_the_finding_exit_code_and_the_manifest_uuid() -> None:
    import tomllib

    text = LAUNCHER.read_text()
    assert "sds_deadman_arm sds-rotation-proposer --finding 3" in text
    listed = {
        entry["uuid"]
        for entry in tomllib.loads(Path(".bws-secrets.toml").read_text())["secret"]
        if entry["name"] == "ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE"
    }
    assert (
        f'SDS_BRIDGE_ALERT_UUID="${{ROTATION_PROPOSER_ALERT_BWS_UUID:-{next(iter(listed))}}}"'
        in text
    )
