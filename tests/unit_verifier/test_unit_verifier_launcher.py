"""The unit verifier's launcher, LaunchAgent plist and installer."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import tomllib
from pathlib import Path

NEW = Path("scripts/run-unit-verifier.sh")
LABEL = "com.devon.unit-verifier"


def test_it_arms_the_new_check_and_keeps_the_identity_and_the_program() -> None:
    text = NEW.read_text()
    assert 'sds_deadman_arm sds-unit-verifier --finding 2 "$@"' in text
    assert "sds-rotation-verifier" not in text
    assert 'ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-verifier"' in text
    assert '"$REPO_ROOT/.venv/bin/unit-verifier" "$@"' in text


def test_the_plist_runs_the_new_launcher_on_the_old_cadence() -> None:
    plist = plistlib.loads(Path(f"scripts/{LABEL}.plist").read_bytes())
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"][1] == "__REPO_ROOT__/scripts/run-unit-verifier.sh"
    assert plist["StartInterval"] == 600
    assert plist["StandardOutPath"] == "__HOME__/Library/Logs/unit-verifier.log"


def test_the_installer_installs_the_new_label() -> None:
    text = Path("scripts/install-unit-verifier-launchd.sh").read_text()
    assert f'LABEL="{LABEL}"' in text
    assert 'TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"' in text


def test_the_launcher_is_a_registered_reader_of_the_bootstrap_token() -> None:
    registry = tomllib.loads(Path(".cred-consumers.toml").read_text())
    [credential] = [
        row for row in registry["credential"] if row.get("keeper_account") == "BWS_ACCESS_TOKEN_SDS"
    ]
    [reader] = [row for row in credential["consumer"] if row.get("ref") == str(NEW)]
    assert reader["kind"] == "runtime-fetch"
    assert reader["healthchecks_check"] == "sds-unit-verifier"


def test_the_installer_retires_the_old_lane_as_it_installs(tmp_path: Path) -> None:
    """Both lanes running would overlap passes, which trips "chain head changed" on `/verify`."""
    root = tmp_path / "main"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "unit-verifier").write_text("#!/bin/sh\n")
    (root / ".venv" / "bin" / "unit-verifier").chmod(0o755)
    for name in ("install-unit-verifier-launchd.sh", "sds-install.sh", f"{LABEL}.plist"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "launchctl.log"
    (stubs / "launchctl").write_text('#!/bin/sh\necho "launchctl $*" >> "$LAUNCHCTL_LOG"\n')
    (stubs / "launchctl").chmod(0o755)
    agents = tmp_path / "home" / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    retired = agents / "com.devon.rotation-verifier.plist"
    retired.write_text("<plist/>")

    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / "install-unit-verifier-launchd.sh")],
        env={
            "PATH": f"{stubs}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "LAUNCHCTL_LOG": str(log),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    old_gone = next(i for i, c in enumerate(calls) if "bootout" in c and "rotation-verifier" in c)
    started = next(i for i, c in enumerate(calls) if "bootstrap" in c)
    assert old_gone < started
    # launchd reloads whatever is in this directory at the next login.
    assert not retired.exists()
    assert (agents / f"{LABEL}.plist").exists()
