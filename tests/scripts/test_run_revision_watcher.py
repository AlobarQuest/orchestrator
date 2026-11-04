"""`scripts/run-revision-watcher.sh`: the alert token is fetched on the acting pass only.

Copied with its helpers into a throwaway tree holding a stub `revision-watcher`; a stub `bws` is
first on PATH, so no real credential is read.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path("scripts/run-revision-watcher.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh", "sds-token.sh")
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"

_BWS = '#!/bin/sh\nprintf \'%s\\n\' "$3" >> "$BWS_LOG"\necho \'{"value": "secret"}\'\n'
_PYTHON = '#!/bin/bash\nif [ "$1" = "-c" ]; then exec "$REAL_PYTHON" "$@"; fi\n'
_PROGRAM = (
    "#!/bin/sh\n"
    'echo "args=$* alert=${SDS_BRIDGE_ALERT_TOKEN:+set}" >> "$STUB_LOG"\n'
    "exit ${PROGRAM_EXIT}\n"
)


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def _run(
    tmp_path: Path, *args: str, program_exit: int = 0, bws: str = _BWS
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    program = root / ".venv" / "bin" / "revision-watcher"
    program.write_text(_PROGRAM.replace("${PROGRAM_EXIT}", str(program_exit)))
    program.chmod(0o755)
    python = root / ".venv" / "bin" / "python"
    python.write_text(_PYTHON)
    python.chmod(0o755)
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    (stubs / "bws").write_text(bws)
    (stubs / "bws").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    log, bws_log = tmp_path / "stub.log", tmp_path / "bws.log"
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "STUB_LOG": str(log),
        "BWS_LOG": str(bws_log),
        "BWS_ACCESS_TOKEN": "narrow",
        "BWS_ACCESS_TOKEN_BROAD": "broad",
        "REVISION_WATCHER_GITHUB_TOKEN": "x",
        "REAL_PYTHON": sys.executable,
    }
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / LAUNCHER.name), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, _lines(log), _lines(bws_log)


def test_the_acting_pass_fetches_the_alert_token(tmp_path: Path) -> None:
    result, log, bws_log = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert ALERT_UUID in bws_log
    assert log == ["args= alert=set"]


def test_a_dry_run_fetches_no_alert_token(tmp_path: Path) -> None:
    result, log, bws_log = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert ALERT_UUID not in bws_log
    assert log == ["args=--dry-run alert="]


def test_an_acting_pass_that_cannot_read_the_token_exits_1_without_running(
    tmp_path: Path,
) -> None:
    bws = (
        '#!/bin/sh\nif [ "$3" = "' + ALERT_UUID + '" ]; then exit 1; fi\n'
        'echo \'{"value": "secret"}\'\n'
    )
    result, log, _ = _run(tmp_path, bws=bws)
    assert result.returncode == 1
    assert log == []
    assert "alert sender token" in result.stderr


@pytest.mark.parametrize(("program_exit", "expected"), [(0, 0), (2, 2), (3, 3), (1, 1), (127, 1)])
def test_exit_codes_pass_through(tmp_path: Path, program_exit: int, expected: int) -> None:
    result, _, _ = _run(tmp_path, program_exit=program_exit)
    assert result.returncode == expected
