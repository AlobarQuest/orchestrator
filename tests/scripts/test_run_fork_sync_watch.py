"""`scripts/run-fork-sync-watch.sh` pings an OUTCOME: success only on exit 0, `/fail` with the
report as its body on anything else -- unlike the dead-man switch, which pings success on a lane's
finding code.

The real launcher runs from a throwaway tree with the real `sds-deadman.sh` (for its lookup of the
ping URL by name), a fake `fork-sync` that prints a report and exits as told, and `curl` and `bws`
stubs on PATH, so nothing is read from BWS or the Keychain and nothing is sent anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path("scripts/run-fork-sync-watch.sh")
PING_URL = "https://hc.example/ping/abcdef"

_CURL = """#!/usr/bin/env bash
config=0
for argument in "$@"; do
    case "$argument" in
        *healthcheck.alobar.net/api/v3/checks/*) printf '%s' "$LISTING"; exit 0 ;;
        --config) config=1 ;;
    esac
done
[ "$config" -eq 1 ] || exit 1
url="$(sed -n 's/^url = "\\(.*\\)"$/\\1/p')"
body=""
previous=""
for argument in "$@"; do
    [ "$previous" = "--data-binary" ] && body="$(cat "${argument#@}")"
    previous="$argument"
done
printf '%s\\t%s\\n' "$url" "$(printf '%s' "$body" | tr '\\n' '|')" >> "$PING_LOG"
"""
_BWS = '#!/bin/sh\nprintf \'{"value": "not-a-real-key"}\'\n'
_TOOL = """#!/bin/sh
echo "stuck             AlobarQuest/rea not_landing -- #7"
echo "fork-sync watch: 1 fork(s), 1 stuck, 9 GitHub API call(s)"
exit "$FAKE_RC"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(tmp_path: Path, rc: int, check: str = "sds-fork-sync-watch") -> tuple[int, list, str]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    shutil.copy(Path("scripts/sds-deadman.sh"), root / "scripts" / "sds-deadman.sh")
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    _executable(venv / "fork-sync", _TOOL)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "curl", _CURL)
    _executable(stubs / "bws", _BWS)
    home = tmp_path / "home"
    home.mkdir()
    ping_log = tmp_path / "pings.txt"
    ping_log.write_text("")
    env = dict(os.environ)
    env.update(
        PATH=f"{stubs}{os.pathsep}{env['PATH']}",
        HOME=str(home),
        BWS_ACCESS_TOKEN_BROAD="not-a-real-identity",
        LISTING=json.dumps({"checks": [{"name": check, "ping_url": PING_URL}]}),
        PING_LOG=str(ping_log),
        FAKE_RC=str(rc),
    )
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / LAUNCHER.name)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    pings = [tuple(line.split("\t")) for line in ping_log.read_text().splitlines()]
    return result.returncode, pings, result.stdout + result.stderr


def test_a_clean_pass_pings_start_then_success(tmp_path: Path) -> None:
    rc, pings, output = _run(tmp_path, 0)
    assert rc == 0
    assert pings == [(f"{PING_URL}/start", ""), (PING_URL, "")]
    assert "not_landing" in output


@pytest.mark.parametrize("rc", [3, 2, 1, 127])
def test_every_other_exit_pings_fail_with_the_report_as_its_body(tmp_path: Path, rc: int) -> None:
    """3 (stuck) above all: the dead-man switch would report it healthy; this check must page."""
    code, pings, _ = _run(tmp_path, rc)
    assert code == rc
    assert pings[0] == (f"{PING_URL}/start", "")
    assert pings[1][0] == f"{PING_URL}/fail"
    assert "stuck             AlobarQuest/rea not_landing" in pings[1][1]
    assert len(pings) == 2


def test_an_unresolved_check_never_gates_the_pass(tmp_path: Path) -> None:
    rc, pings, output = _run(tmp_path, 3, check="some-other-check")
    assert rc == 3
    assert pings == []
    assert "no check named 'sds-fork-sync-watch'" in output


def test_the_launcher_does_not_arm_the_dead_man_switch() -> None:
    """Arming it maps a finding code to a success ping, the inversion this check exists to avoid;
    and `test_sds_deadman.py` selects launchers by that function's name in their text."""
    assert "sds_deadman_arm" not in LAUNCHER.read_text()


# ---- the installer's Healthchecks check -------------------------------------------------------

INSTALLER = Path("scripts/install-fork-sync-watch-launchd.sh")
_INSTALL_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CURL_ARGS"
cat >> "$CURL_STDIN"
printf '%s' "$HC_STATUS"
"""


@pytest.mark.parametrize(("status", "said"), [("201", "created"), ("200", "already exists")])
def test_the_installer_creates_the_check_without_exposing_the_key(
    tmp_path: Path, status: str, said: str
) -> None:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (INSTALLER.name, "sds-install.sh", "sds-bws.sh", "com.devon.fork-sync-watch.plist"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    _executable(venv / "fork-sync", "#!/bin/sh\n")
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "curl", _INSTALL_CURL)
    _executable(stubs / "bws", _BWS)
    _executable(stubs / "launchctl", "#!/bin/sh\n")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN_BROAD": "not-a-real-identity",
        "CURL_ARGS": str(tmp_path / "args"),
        "CURL_STDIN": str(tmp_path / "stdin"),
        "HC_STATUS": status,
    }
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / INSTALLER.name)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"Healthchecks check sds-fork-sync-watch {said}" in result.stdout
    args = (tmp_path / "args").read_text()
    assert "not-a-real-key" not in args + result.stdout + result.stderr
    assert (tmp_path / "stdin").read_text() == 'header = "X-Api-Key: not-a-real-key"\n'
    payload = json.loads(args.split("--data-binary ", 1)[1].strip())
    assert payload["name"] == "sds-fork-sync-watch"
    assert payload["unique"] == ["name"] and payload["channels"] == "*"
    assert (payload["schedule"], payload["tz"]) == ("55 * * * *", "America/New_York")
    assert "-o /dev/null" in args
    plist = (home / "Library/LaunchAgents/com.devon.fork-sync-watch.plist").read_text()
    assert "<integer>55</integer>" in plist and "__REPO_ROOT__" not in plist
