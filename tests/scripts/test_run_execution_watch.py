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
PING_URL = "https://hc.example/ping/execution-watch"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
# The container's `bws`: `$BWS_REFUSES` lists the UUIDs the lane's token can no longer read (all
# of them, for a revoked token); the ping-URL secret holds `PING_URL`. Each call logs its argv and
# whether the token arrived in its environment.
_CONTAINER_BWS = f"""#!/bin/sh
echo "$* token=${{BWS_ACCESS_TOKEN:+set}}" >> "$BWS_LOG"
case " $BWS_REFUSES " in *" $3 "*) exit 1 ;; esac
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
echo '{{"value": "secret-for-'"$3"'"}}'
"""
# `env` as the helpers call it (`env -u FORCE_COLOR ... bws ...`), logging its arguments: in the
# container the lane's token must never be one of them.
_ENV = """#!/bin/sh
echo "$*" >> "$ENV_LOG"
exec /usr/bin/env "$@"
"""
# A ping's URL arrives on stdin as curl's config; anything else (the Mac's listing read) fails.
_CURL = """#!/bin/sh
for argument in "$@"; do [ "$argument" = "-" ] && { cat >> "$PING_LOG"; exit 0; }; done
exit 1
"""


def _program(exit_code: int) -> str:
    return (
        "#!/bin/sh\n"
        'echo "args=$*" >> "$STUB_LOG"\n'
        'echo "observer=${ORCHESTRATOR_API_TOKEN:+set}" >> "$STUB_LOG"\n'
        'echo "alert=${SDS_BRIDGE_ALERT_TOKEN:+set}" >> "$STUB_LOG"\n'
        'echo "lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$STUB_LOG"\n'
        f"exit {exit_code}\n"
    )


def _run(
    tmp_path: Path,
    *args: str,
    program_exit: int = 0,
    identity: str | None = "narrow",
    container: dict[str, str] | None = None,
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
    stubbed = [("bws", _CONTAINER_BWS if container else _BWS), ("curl", _CURL), ("env", _ENV)]
    for name, body in stubbed:
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    log = tmp_path / "stub.log"
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "STUB_LOG": str(log),
        "ENV_LOG": str(tmp_path / "env.log"),
        # Stops the dead-man resolution reading the Keychain; the stub key it then gets is junk.
        "BWS_ACCESS_TOKEN_BROAD": "broad",
    }
    if identity is not None:
        env["BWS_ACCESS_TOKEN_SDS"] = identity
    if container is not None:
        # What `lane-run` hands the launcher in the lane's container: the lane's token, and no
        # Keychain identity of either kind.
        env.pop("BWS_ACCESS_TOKEN_BROAD")
        env.update(container, BWS_LOG=str(tmp_path / "bws.log"), PING_LOG=str(tmp_path / "pings"))
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
    assert log == ["args=", "observer=set", "alert=", "lane_token="]
    assert "not arming" in result.stdout


def test_a_seed_run_needs_no_alert_token_and_does_not_ping(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--seed")
    assert result.returncode == 0
    assert log == ["args=--seed", "observer=set", "alert=", "lane_token="]
    assert "not arming" in result.stdout


def test_a_submit_run_reads_both_and_arms_the_switch(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--submit")
    assert result.returncode == 0
    assert log == ["args=--submit", "observer=set", "alert=set", "lane_token="]
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


# ---- in the lane's container (ADR-0059) -----------------------------------------------------


def _container(refuses: str = "") -> dict[str, str]:
    return {
        "BWS_ACCESS_TOKEN": "a-lane-token",
        "SDS_DEADMAN_PING_URL_UUID": PING_URL_UUID,
        "BWS_REFUSES": refuses,
    }


def _pings(tmp_path: Path) -> list[str]:
    log = tmp_path / "pings"
    lines = log.read_text().splitlines() if log.exists() else []
    return [line.removeprefix('url = "').removesuffix('"') for line in lines]


def test_in_the_container_every_read_uses_the_lanes_token_from_the_environment(
    tmp_path: Path,
) -> None:
    result, log = _run(tmp_path, "--submit", identity=None, container=_container())
    assert result.returncode == 0
    # The program inherits neither the lane's token nor any other identity.
    assert log == ["args=--submit", "observer=set", "alert=set", "lane_token="]
    reads = (tmp_path / "bws.log").read_text().splitlines()
    assert [line.split()[2] for line in reads] == [
        PING_URL_UUID,
        OBSERVER_UUID,
        "b415d03b-f92d-46b1-8a9d-b4f10103df49",
    ]
    assert all(line.endswith("token=set") for line in reads)
    env_calls = (tmp_path / "env.log").read_text()
    assert env_calls.count("bws secret get") == 3
    assert "a-lane-token" not in "\n".join(reads) + env_calls + result.stdout + result.stderr
    assert _pings(tmp_path) == [f"{PING_URL}/start", PING_URL]
    assert PING_URL not in result.stdout + result.stderr


def test_a_pass_on_a_revoked_token_sends_no_start(tmp_path: Path) -> None:
    """ADR-0059 Amendment 2, "A pass on a dead token": every read refuses, the ping-URL read
    first, so the pass pings nothing and the check is never left `started`. It fails its pass,
    and the check goes down after its grace."""
    refuses = f"{PING_URL_UUID} {OBSERVER_UUID} b415d03b-f92d-46b1-8a9d-b4f10103df49"
    result, log = _run(tmp_path, "--submit", identity=None, container=_container(refuses))
    assert result.returncode == 1
    assert log == []  # the program never ran
    assert _pings(tmp_path) == []
    assert "could not be read — alerting disabled this run" in result.stdout


def test_a_failure_after_the_start_ping_ends_in_fail(tmp_path: Path) -> None:
    """A token that loses its read between the ping-URL read and the observer read: `/start` was
    sent, and the exit handler sends `/fail`, so `started` is cleared."""
    result, log = _run(tmp_path, "--submit", identity=None, container=_container(OBSERVER_UUID))
    assert result.returncode == 1
    assert log == []
    assert _pings(tmp_path) == [f"{PING_URL}/start", f"{PING_URL}/fail"]
