"""`scripts/run-execution-watch.sh`: which credentials each invocation reads, when it arms the
dead-man switch, and how it maps the program's exit code.

Nothing here touches the real Keychain, BWS or Healthchecks: the launcher is copied with its
helpers into a throwaway tree holding a stub `execution-watch`, a stub `bws` is first on PATH, and
the identities arrive as environment variables so `/usr/bin/security` is never asked.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path("scripts/run-execution-watch.sh")
PLIST = Path("scripts/com.devon.execution-watch.plist")
HELPERS = ("sds-install.sh", "sds-deadman.sh", "sds-bws.sh")

_BWS = '#!/bin/sh\necho \'{"value": "secret-for-\'"$2"\'"}\'\n'


def _program(exit_code: int) -> str:
    return (
        "#!/bin/sh\n"
        'echo "args=$*" >> "$STUB_LOG"\n'
        'echo "observer=${ORCHESTRATOR_API_TOKEN:+set}" >> "$STUB_LOG"\n'
        'echo "alert=${SDS_BRIDGE_ALERT_TOKEN:+set}" >> "$STUB_LOG"\n'
        f"exit {exit_code}\n"
    )


def _run(
    tmp_path: Path, *args: str, program_exit: int = 0, identity: str | None = "narrow"
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    program = root / ".venv" / "bin" / "execution-watch"
    program.write_text(_program(program_exit))
    program.chmod(0o755)
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        python.symlink_to(sys.executable)
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    (stubs / "bws").write_text(_BWS)
    (stubs / "bws").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    log = tmp_path / "stub.log"
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "STUB_LOG": str(log),
        # Stops the dead-man resolution reading the Keychain; the stub key it then gets is junk.
        "BWS_ACCESS_TOKEN_BROAD": "broad",
    }
    if identity is not None:
        env["BWS_ACCESS_TOKEN_SDS"] = identity
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / LAUNCHER.name), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text().splitlines() if log.exists() else []


def test_a_bare_run_reads_only_and_does_not_ping(tmp_path: Path) -> None:
    result, log = _run(tmp_path)
    assert result.returncode == 0
    assert log == ["args=", "observer=set", "alert="]
    assert "not arming" in result.stdout


def test_a_seed_run_needs_no_alert_token_and_does_not_ping(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--seed")
    assert result.returncode == 0
    assert log == ["args=--seed", "observer=set", "alert="]
    assert "not arming" in result.stdout


def test_a_submit_run_reads_both_and_arms_the_switch(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--submit")
    assert result.returncode == 0
    assert log == ["args=--submit", "observer=set", "alert=set"]
    assert "not arming" not in result.stdout
    assert "[deadman]" in result.stdout


@pytest.mark.parametrize(("program_exit", "expected"), [(0, 0), (1, 1), (2, 2), (3, 1), (127, 1)])
def test_exit_codes_pass_through_or_become_one(
    tmp_path: Path, program_exit: int, expected: int
) -> None:
    result, _ = _run(tmp_path, program_exit=program_exit)
    assert result.returncode == expected


def test_the_finding_code_is_the_unreadable_code() -> None:
    text = LAUNCHER.read_text()
    assert "sds_deadman_arm sds-execution-watch --finding 2" in text
    assert "export BWS_ACCESS_TOKEN" not in text


def test_the_job_passes_submit_every_fifteen_minutes() -> None:
    rendered = PLIST.read_text().replace("__REPO_ROOT__", "/r").replace("__HOME__", "/h")
    plist = plistlib.loads(rendered.encode())
    assert plist["StartInterval"] == 900
    assert plist["ProgramArguments"][-1] == "--submit"
    assert "/opt/homebrew/bin" in plist["EnvironmentVariables"]["PATH"]
