"""`scripts/run-tool-installer.sh`: the alert token is fetched on the acting pass only.

The launcher is copied with its helpers into a throwaway tree holding a stub `tool-installer`; a
stub `bws` is first on PATH and every identity arrives as an environment variable, so the real
Keychain, BWS and Healthchecks are never reached.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path("scripts/run-tool-installer.sh")
HELPERS = ("sds-install.sh", "sds-deadman.sh", "sds-bws.sh", "sds-token.sh")

_BWS = '#!/bin/sh\necho \'{"value": "secret"}\'\n'
_BWS_UNREADABLE = "#!/bin/sh\nexit 1\n"


def _program(exit_code: int) -> str:
    return (
        "#!/bin/sh\n"
        'echo "args=$*" >> "$STUB_LOG"\n'
        'echo "alert=${SDS_BRIDGE_ALERT_TOKEN:+set}" >> "$STUB_LOG"\n'
        f"exit {exit_code}\n"
    )


def _run(
    tmp_path: Path, *args: str, program_exit: int = 0, bws: str = _BWS
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    program = root / ".venv" / "bin" / "tool-installer"
    program.write_text(_program(program_exit))
    program.chmod(0o755)
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        python.symlink_to(sys.executable)
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    (stubs / "bws").write_text(bws)
    (stubs / "bws").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    log = tmp_path / "stub.log"
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "STUB_LOG": str(log),
        "BWS_ACCESS_TOKEN": "narrow",
        "BWS_ACCESS_TOKEN_BROAD": "broad",
        "TOOL_INSTALLER_GITHUB_TOKEN": "gh",
    }
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / LAUNCHER.name), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text().splitlines() if log.exists() else []


def test_the_acting_pass_fetches_the_alert_token(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--install")
    assert result.returncode == 0
    assert log == ["args=--install", "alert=set"]


@pytest.mark.parametrize("args", [(), ("--dry-run",), ("--install", "--dry-run")])
def test_a_pass_that_does_not_act_fetches_no_alert_token(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    result, log = _run(tmp_path, *args)
    assert result.returncode == 0
    assert log[-1] == "alert="


def test_an_acting_pass_that_cannot_read_the_token_exits_1_without_running(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--install", bws=_BWS_UNREADABLE)
    assert result.returncode == 1
    assert log == []
    assert "alert sender token" in result.stderr


@pytest.mark.parametrize(("program_exit", "expected"), [(0, 0), (1, 1), (2, 2), (3, 3), (127, 1)])
def test_exit_codes_pass_through_or_become_one(
    tmp_path: Path, program_exit: int, expected: int
) -> None:
    result, _ = _run(tmp_path, "--install", program_exit=program_exit)
    assert result.returncode == expected


def test_the_narrow_token_is_never_exported_for_the_alert_fetch() -> None:
    assert "export BWS_ACCESS_TOKEN" not in LAUNCHER.read_text()
