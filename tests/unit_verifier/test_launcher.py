"""The launcher: a dead-man lane whose alert token can never stop a verification."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

LAUNCHER = Path("scripts/run-unit-verifier.sh")
_BWS = """#!/bin/sh
case " $* " in
    *" ${BWS_FAIL_UUID:-none} "*) exit 1 ;;
esac
printf '{"value": "not-a-real-key"}'
"""
# Records the NAME of each variable set, never a value, and whether the alert token is empty.
_PROGRAM = """#!/bin/sh
env | sed 's/=.*//' > "$ENV_LOG"
[ -n "${SDS_BRIDGE_ALERT_TOKEN:-}" ] && echo ALERT_TOKEN_PRESENT >> "$ENV_LOG"
exit "${PROGRAM_RC:-0}"
"""


def _uuid(name: str) -> str:
    manifest = tomllib.loads(Path(".bws-secrets.toml").read_text())
    return next(entry["uuid"] for entry in manifest["secret"] if entry["name"] == name)


ALERT_UUID = _uuid("ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE")


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(tmp_path: Path, **env_overrides: str) -> tuple[int, str, set[str]]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (LAUNCHER.name, "sds-deadman.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    _executable(venv / "unit-verifier", _PROGRAM)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "bws", _BWS)
    _executable(stubs / "gh", "#!/bin/sh\necho not-a-real-gh-token\n")
    _executable(stubs / "curl", "#!/bin/sh\nexit 1\n")
    home = tmp_path / "home"
    home.mkdir()
    env_log = tmp_path / "env.txt"
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": "not-a-real-identity",
        "ENV_LOG": str(env_log),
        **env_overrides,
    }
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / LAUNCHER.name)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    names = set(env_log.read_text().split()) if env_log.exists() else set()
    return result.returncode, result.stdout + result.stderr, names


def test_the_lane_gets_its_alert_token(tmp_path: Path) -> None:
    rc, output, names = _run(tmp_path)
    assert rc == 0, output
    assert "ALERT_TOKEN_PRESENT" in names


def test_a_failed_alert_token_fetch_still_runs_the_pass(tmp_path: Path) -> None:
    rc, output, names = _run(tmp_path, BWS_FAIL_UUID=ALERT_UUID)
    assert rc == 0, output
    assert "ORCHESTRATOR_API_TOKEN" in names
    assert "ALERT_TOKEN_PRESENT" not in names
    assert "alert" in output.lower()


def test_a_failed_verifier_bearer_fetch_still_stops_the_lane(tmp_path: Path) -> None:
    rc, _, names = _run(tmp_path, BWS_FAIL_UUID=_uuid("orchestrator-verifier-bearer"))
    assert rc == 1
    assert not names


@pytest.mark.parametrize("code", ["2", "3"])
def test_the_lane_s_codes_pass_through(tmp_path: Path, code: str) -> None:
    rc, _, _ = _run(tmp_path, PROGRAM_RC=code)
    assert rc == int(code)


def test_finding_code_and_header_agree() -> None:
    text = LAUNCHER.read_text()
    assert 'sds_deadman_arm sds-unit-verifier --finding 2 "$@"' in text
    header = text[: text.index("set -uo pipefail")]
    assert "#   2  something was found: a unit went to a person or an alert names it." in header
    assert "#   3  the queue could not be read, or alert-hub could not be told." in header
