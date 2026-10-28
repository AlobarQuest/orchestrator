"""`scripts/run-change-proposer.sh` hands the broad BWS identity to one call and exports nothing.

The propose keeper is readable only by the broad machine account, and the launcher used to export
that account's token as `BWS_ACCESS_TOKEN`, so every process it started ran as the broad account.
Here the real launcher runs from a throwaway tree under only PATH and HOME (launchd's environment),
with `bws`, `gh`, `curl` and the tool stubbed: no credential is read and nothing leaves the machine.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

LAUNCHER = Path("scripts/run-change-proposer.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh")
PROPOSE_UUID = "acccb346-4baa-43ec-a1d4-b4a400c048ee"

_BWS = """#!/bin/sh
echo "token=${BWS_ACCESS_TOKEN-<unset>} args=$*" >> "$STUB_LOG"
printf '%s\\n' '{"id": "x", "key": "k", "value": "stub-secret-value"}'
"""
_GH = "#!/bin/sh\necho gh-stub-token\n"
_CURL = "#!/bin/sh\nexit 0\n"
_TOOL = """#!/bin/sh
cm="${CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN-<unset>}"
echo "bws=${BWS_ACCESS_TOKEN-<unset>} cm=$cm" > "$CHILD_LOG"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    _executable(venv / "change-proposer", _TOOL)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "bws", _BWS)
    _executable(stubs / "gh", _GH)
    _executable(stubs / "curl", _CURL)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN_BROAD": "broad-stub",
        "BWS_ACCESS_TOKEN_SDS": "narrow-stub",
        "STUB_LOG": str(tmp_path / "bws.log"),
        "CHILD_LOG": str(tmp_path / "child.log"),
    }
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / LAUNCHER.name), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    bws_log = tmp_path / "bws.log"
    calls = bws_log.read_text().splitlines() if bws_log.exists() else []
    child = (tmp_path / "child.log").read_text().strip()
    return result, calls, child


def test_a_submitting_run_reads_the_keeper_as_the_broad_account_and_exports_no_identity(
    tmp_path: Path,
) -> None:
    result, calls, child = _run(tmp_path, "--submit")
    assert result.returncode == 0, result.stderr
    fetch = [call for call in calls if PROPOSE_UUID in call]
    assert len(fetch) == 1 and fetch[0].startswith("token=broad-stub ")
    assert child == "bws=<unset> cm=stub-secret-value"


def test_a_dry_run_fetches_no_credential(tmp_path: Path) -> None:
    result, calls, child = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not [call for call in calls if PROPOSE_UUID in call]
    assert child == "bws=<unset> cm=<unset>"
