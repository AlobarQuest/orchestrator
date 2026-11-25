"""`scripts/run-execution-watch.sh`: which credentials each invocation reads, when it arms the
dead-man switch, and how it maps the program's exit code.

Nothing here touches the real BWS or Healthchecks: the launcher is copied with its helpers into a
throwaway tree holding a stub `execution-watch`, and a stub `bws` is first on PATH. The launcher
runs only in the lane's container, so every pass but the refusal is given the lane's token, or the
secrets broker's socket as `SDS_SECRETS_SOCKET` (a stand-in broker, `conftest.py`'s).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy
from scripts.secrets_broker import load_allowlist

LAUNCHER = Path("scripts/run-execution-watch.sh")
HELPERS = ("sds-install.sh", "sds-deadman.sh", "sds-bws.sh")

PING_URL = "https://hc.example/ping/execution-watch"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
PING_URL_KEEPER = "97802aac-5a7f-45e8-8185-b4fc00cd318e"
COMPOSE = Path("lanes/execution-watch/compose.yaml")
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
# A ping's URL arrives on stdin as curl's config; anything else fails.
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
        'echo "socket=${SDS_SECRETS_SOCKET:+set}" >> "$STUB_LOG"\n'
        f"exit {exit_code}\n"
    )


def _run(
    tmp_path: Path,
    *args: str,
    program_exit: int = 0,
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
    stubbed = [("bws", _CONTAINER_BWS), ("curl", _CURL), ("env", _ENV)]
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
    }
    if container is not None:
        # What `lane-run` hands the launcher in the lane's container: the lane's token.
        env.update(container, BWS_LOG=str(tmp_path / "bws.log"), PING_LOG=str(tmp_path / "pings"))
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / LAUNCHER.name), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text().splitlines() if log.exists() else []


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


def test_a_bare_run_reads_only_and_does_not_ping(tmp_path: Path) -> None:
    result, log = _run(tmp_path, container=_container())
    assert result.returncode == 0
    assert log == ["args=", "observer=set", "alert=", "lane_token=", "socket="]
    assert "not arming" in result.stdout


def test_a_seed_run_needs_no_alert_token_and_does_not_ping(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--seed", container=_container())
    assert result.returncode == 0
    assert log == ["args=--seed", "observer=set", "alert=", "lane_token=", "socket="]
    assert "not arming" in result.stdout


def test_a_submit_run_reads_both_and_arms_the_switch(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--submit", container=_container())
    assert result.returncode == 0
    assert log == ["args=--submit", "observer=set", "alert=set", "lane_token=", "socket="]
    assert "not arming" not in result.stdout
    assert "[deadman]" in result.stdout


@pytest.mark.parametrize(("program_exit", "expected"), [(0, 0), (1, 1), (2, 2), (3, 1), (127, 1)])
def test_exit_codes_pass_through_or_become_one(
    tmp_path: Path, program_exit: int, expected: int
) -> None:
    result, _ = _run(tmp_path, program_exit=program_exit, container=_container())
    assert result.returncode == expected


def test_the_finding_code_is_the_unreadable_code() -> None:
    text = LAUNCHER.read_text()
    assert "sds_deadman_arm sds-execution-watch --finding 2" in text
    assert "export BWS_ACCESS_TOKEN" not in text


def test_outside_the_container_it_refuses_and_runs_nothing(tmp_path: Path) -> None:
    """The stubs log to the container's paths and the ping-URL id is set, so a read or a ping
    would leave a trace; the lane's token is empty."""
    outside = {**_container(), "BWS_ACCESS_TOKEN": ""}
    result, log = _run(tmp_path, "--submit", container=outside)
    assert result.returncode == 1
    assert "the execution-watch lane runs only in its container, through lane-run" in result.stderr
    assert log == []
    assert not (tmp_path / "bws.log").exists()
    assert not (tmp_path / "env.log").exists()
    assert _pings(tmp_path) == []


def test_in_the_container_every_read_uses_the_lanes_token_from_the_environment(
    tmp_path: Path,
) -> None:
    result, log = _run(tmp_path, "--submit", container=_container())
    assert result.returncode == 0
    # The program inherits neither the lane's token nor any other identity.
    assert log == ["args=--submit", "observer=set", "alert=set", "lane_token=", "socket="]
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
    result, log = _run(tmp_path, "--submit", container=_container(refuses))
    assert result.returncode == 1
    assert log == []  # the program never ran
    assert _pings(tmp_path) == []
    assert "could not be read — alerting disabled this run" in result.stdout


def test_a_failure_after_the_start_ping_ends_in_fail(tmp_path: Path) -> None:
    """A token that loses its read between the ping-URL read and the observer read: `/start` was
    sent, and the exit handler sends `/fail`, so `started` is cleared."""
    result, log = _run(tmp_path, "--submit", container=_container(OBSERVER_UUID))
    assert result.returncode == 1
    assert log == []
    assert _pings(tmp_path) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


# ---- the container on the secrets broker (ADR-0055 amendment 30) ------------------------------


def _socket(socket_path: Path) -> dict[str, str]:
    """The container as `lane-run` starts it on the broker: no token, and the socket's marker."""
    return {"SDS_SECRETS_SOCKET": str(socket_path), "SDS_DEADMAN_PING_URL_UUID": PING_URL_UUID}


def _served() -> dict[str, str | None]:
    return {OBSERVER_UUID: "observer", ALERT_UUID: "alert", PING_URL_UUID: PING_URL}


def test_on_the_socket_a_submit_pass_reads_every_secret_through_it(
    tmp_path: Path, broker
) -> None:
    """`bws` never runs, and the program gets neither the lane's token nor the socket's marker."""
    result, log = _run(tmp_path, "--submit", container=_socket(broker(_served())))
    assert result.returncode == 0, result.stderr
    assert log == ["args=--submit", "observer=set", "alert=set", "lane_token=", "socket="]
    assert not (tmp_path / "bws.log").exists()
    assert _pings(tmp_path) == [f"{PING_URL}/start", PING_URL]


def test_on_the_socket_a_bare_pass_asks_no_alert_token(tmp_path: Path, broker) -> None:
    served = _served()
    del served[ALERT_UUID]
    result, log = _run(tmp_path, container=_socket(broker(served)))
    assert result.returncode == 0, result.stderr
    assert log == ["args=", "observer=set", "alert=", "lane_token=", "socket="]


@pytest.mark.parametrize(
    ("refused", "reason"),
    [(OBSERVER_UUID, "bws_failed"), (ALERT_UUID, "not_allowed")],
    ids=["bearer-read-failed", "alert-not-on-the-allowlist"],
)
def test_a_secret_the_socket_refuses_fails_the_pass(
    tmp_path: Path, broker, refused: str, reason: str
) -> None:
    served = _served()
    if reason == "bws_failed":
        served[refused] = None
    else:
        del served[refused]
    result, log = _run(tmp_path, "--submit", container=_socket(broker(served)))
    assert result.returncode == 1
    assert f"the secrets broker gave no value for {refused}: {reason}" in result.stderr
    assert "(the lane's socket or token)" in result.stderr
    assert log == []
    assert not (tmp_path / "bws.log").exists()
    assert _pings(tmp_path) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_a_dead_socket_fails_the_pass_and_never_falls_back_to_bws(
    tmp_path: Path, dead_socket: Path
) -> None:
    result, log = _run(tmp_path, "--submit", container=_socket(dead_socket))
    assert result.returncode == 1
    assert f"the secrets broker gave no value for {OBSERVER_UUID}: no_reply" in result.stderr
    assert log == []
    assert not (tmp_path / "bws.log").exists()


def test_a_socket_variable_naming_no_socket_refuses_and_runs_nothing(tmp_path: Path) -> None:
    """`SDS_SECRETS_SOCKET` alone isn't the container: it must name a socket."""
    not_a_socket = tmp_path / "broker.sock"
    not_a_socket.write_text("")
    result, log = _run(tmp_path, "--submit", container=_socket(not_a_socket))
    assert result.returncode == 1
    assert "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN" in result.stderr
    assert log == []
    assert _pings(tmp_path) == []


def test_the_compose_is_a_broker_lane_and_maps_no_token() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "execution-watch")
    allowlist = load_allowlist(Path(deploy.BROKER_ALLOWLIST))
    assert deploy.broker_lane(service, "execution-watch", allowlist)
    assert deploy.add_refusals(service, "execution-watch", allowlist) == []
    assert deploy.compose_broker_refusals(COMPOSE.read_text(), "execution-watch") == []
    assert allowlist["execution-watch"] == {OBSERVER_UUID, ALERT_UUID, PING_URL_KEEPER}
    assert "BWS_ACCESS_TOKEN" not in service["environment"]
    assert service["volumes"] == [
        "sds-lanes-execution-watch-state:/var/lib/sds/execution-watch",
        "/data/sds/broker/execution-watch:/run/sds-broker:ro",
    ]
    assert set(yaml.safe_load(COMPOSE.read_text())) == {"services", "volumes"}
