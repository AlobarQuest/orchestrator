"""lane-run: start one lane's launcher in its container with a fixed environment (ADR-0059).

Every container run of a launcher goes through this program: the scheduler's crontab line and a
one-off `docker exec` alike. It starts the launcher with exactly the variables below and no other
(decision 1, "`lane-run` passes the launcher a fixed, tracked list of variables, and one secret"):

- `PATH`, set here to the image's directories, and `HOME`, a new empty directory for this run, so
  `bws` holds no cached session (workspace invariant #23). The directory is removed afterwards.
- `BWS_ACCESS_TOKEN`, copied from the container's environment: the run's only secret.
- `SDS_SECRETS_SOCKET`, set to `BROKER_SOCKET` when the lane's secrets-broker socket is mounted
  there (ADR-0055 amendment 30), and never copied: a value in the container's environment is
  ignored. `scripts/sds-bws.sh` then reads each one-argument fetch through that socket.
- `TZ` and `SDS_DEADMAN_PING_URL_UUID` (a BWS UUID, not a secret).
- Each lane state variable in `STATE_VARIABLES`, only when the container's environment sets it.
- The factory-events chain's three non-secret settings in `CHAIN_SETTINGS` (ADR-0055 amendment 26
  item 3), which only the work carrier's and the bump driver's composes set. Its read password is
  a secret, so it isn't here: the launcher reads it from BWS with the token.
- infraops' two directory settings in `INFRAOPS_SETTINGS`, which only the rotation proposer's
  compose sets: where `cred-findings` finds `cred-consumers.list` and `cred-rotation-state.json`.
  The launcher refuses a pass with either unset, so it never falls back to `HOME`.

Each is copied only when the container's environment sets it. `LANE_SCHEDULE_ENABLED` is never
passed: the entrypoint reads it at container start, and no launcher reads it. An increment that
gives a launcher a new non-secret variable adds it here in the same change. No secret is ever
added: a launcher reads every secret from BWS through the token.

Usage:
    lane-run <lane> [launcher arguments...]   runs scripts/run-<lane>.sh beside this file
    lane-run --read <uuid>...                  proves the lane's BWS identity (decision 2)

`--read` starts no launcher. When the broker's socket is mounted it asks the socket for each UUID
and needs no token; otherwise, under the same environment, it runs one
`bws secret get <uuid> --color no` per UUID. Either way it discards the value and prints
`<uuid> ok` or `<uuid> refused`, never a value. The token never appears in a process argument: it
reaches each child through its environment alone. A mounted socket is never bypassed for `bws`,
so a broken broker shows as `refused`.

Exit codes: a launcher run exits with the launcher's own code. `--read` exits 0 when every read
is `ok` and 1 when any is `refused`. A usage error, an unknown lane, or, with no socket, a missing
token or a missing `bws` for `--read`, exits 2.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

LAUNCHER_DIR = Path(__file__).resolve().parent
LANE_PATH = "/app/.venv/bin:/usr/local/bin:/usr/bin:/bin"
TOKEN = "BWS_ACCESS_TOKEN"
COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")
# Where a broker lane's compose mounts its secrets-broker socket, and the variable that tells the
# launcher so (ADR-0055 amendment 30). The client waits longer than the broker's 15 s fetch plus a
# wait behind its 8 concurrent fetches.
BROKER_SOCKET = Path("/run/sds-broker/broker.sock")
SOCKET_MARKER = "SDS_SECRETS_SOCKET"
BROKER_TIMEOUT_S = 60.0
# The ten state variables of the eleven stateful lanes (decision 1, "Lane state"); both landers
# share `LANDER_STATE_DIR`.
STATE_VARIABLES = (
    "BUMP_DRIVER_STATE_DIR",
    "BUMP_PROPOSER_STATE_DIR",
    "LANDER_STATE_DIR",
    "EXECUTION_WATCH_STATE_DIR",
    "PIN_WATCHER_STATE_DIR",
    "REVISION_WATCHER_STATE_DIR",
    "ROTATION_DECOMPOSER_STATE_DIR",
    "ROTATION_PROPOSER_STATE_DIR",
    "VERIFIER_STATE_DIR",
    "WORK_WATCHER_STATE_DIR",
)
# The chain's host, port and database (never its password), each a plain value in the work
# carrier's and the bump driver's composes; each launcher passes them to its chain reads alone.
CHAIN_SETTINGS = ("FACTORY_EVENTS_DB_HOST", "FACTORY_EVENTS_DB_PORT", "FACTORY_EVENTS_DB_NAME")
# infraops' list and state directories (`src/security-drift/paths.ts`), each a plain value in the
# rotation proposer's compose; the launcher hands them to `cred-findings` through the program.
INFRAOPS_SETTINGS = ("INFRADRIFT_CONFIG_DIR", "SECURITY_DRIFT_STATE_DIR")
LANE = re.compile(r"^[a-z][a-z0-9-]*$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2
USAGE = "usage: lane-run <lane> [launcher arguments...] | lane-run --read <uuid>..."


def lane_environment(
    source: Mapping[str, str], home: str, broker: Path = BROKER_SOCKET
) -> dict[str, str]:
    """The launcher's whole environment: the fixed `PATH`, `home`, each listed name `source`
    sets, and the marker when `broker` is a socket. Nothing else in `source` is copied."""
    environment = {"PATH": LANE_PATH, "HOME": home}
    for name in (*COPIED, *STATE_VARIABLES, *CHAIN_SETTINGS, *INFRAOPS_SETTINGS):
        if name in source:
            environment[name] = source[name]
    if broker.is_socket():
        environment[SOCKET_MARKER] = str(broker)
    return environment


def _fail(message: str) -> int:
    print(f"lane-run: {message}", file=sys.stderr)
    return EXIT_USAGE


def run_launcher(
    lane: str, arguments: Sequence[str], source: Mapping[str, str], broker: Path = BROKER_SOCKET
) -> int:
    launcher = LAUNCHER_DIR / f"run-{lane}.sh"
    if not LANE.match(lane) or not launcher.is_file():
        return _fail(f"no launcher for lane {lane!r} at {launcher}")
    with tempfile.TemporaryDirectory(prefix="lane-run-") as home:
        completed = subprocess.run(
            [str(launcher), *arguments], env=lane_environment(source, home, broker), check=False
        )
    return completed.returncode


def read_from_socket(path: Path, uuid: str) -> str | None:
    """`uuid`'s value from the broker at `path`, or None for any failure: no connection, no reply,
    an error reply, or a reply for another UUID. What the broker sent is never surfaced."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(BROKER_TIMEOUT_S)
            connection.connect(str(path))
            connection.sendall(f"{uuid}\n".encode())
            reply = b""
            while chunk := connection.recv(65536):
                reply += chunk
        answer = json.loads(reply)
    except OSError, ValueError:
        return None
    if not isinstance(answer, dict) or answer.get("id") != uuid:
        return None
    value = answer.get("value")
    return value if isinstance(value, str) else None


def _read_through_broker(uuids: Sequence[str], broker: Path) -> int:
    refused = False
    for uuid in uuids:
        if read_from_socket(broker, uuid) is None:
            refused = True
            print(f"{uuid} refused")
            print(f"lane-run: no value from the secrets broker for {uuid}", file=sys.stderr)
        else:
            print(f"{uuid} ok")
    return EXIT_REFUSED if refused else EXIT_OK


def read_secrets(
    uuids: Sequence[str], source: Mapping[str, str], broker: Path = BROKER_SOCKET
) -> int:
    if not uuids:
        return _fail(USAGE)
    malformed = [uuid for uuid in uuids if not UUID.match(uuid)]
    if malformed:
        return _fail(f"not a BWS secret UUID: {', '.join(malformed)}")
    if broker.is_socket():
        return _read_through_broker(uuids, broker)
    if not source.get(TOKEN):
        return _fail(f"{TOKEN} is not set in this container; nothing can be read")
    # Found on the container's own PATH, the image's `ENV PATH`, which holds every directory of
    # `LANE_PATH`; a test puts a stand-in `bws` there.
    bws = shutil.which("bws", path=source.get("PATH"))
    if bws is None:
        return _fail("bws is not on PATH")
    refused = False
    with tempfile.TemporaryDirectory(prefix="lane-run-") as home:
        environment = lane_environment(source, home)
        for uuid in uuids:
            # What bws prints is discarded, not swallowed: on success it is the secret's value,
            # and the proof may report only whether the read succeeded. A failure is reported by
            # its exit status, on stderr, beside the UUID's `refused` line.
            completed = subprocess.run(
                [bws, "secret", "get", uuid, "--color", "no"],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if completed.returncode == 0:
                print(f"{uuid} ok")
            else:
                refused = True
                print(f"{uuid} refused")
                status = completed.returncode
                print(f"lane-run: bws exited {status} reading {uuid}", file=sys.stderr)
    return EXIT_REFUSED if refused else EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    source: Mapping[str, str] | None = None,
    broker: Path = BROKER_SOCKET,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    environment = os.environ if source is None else source
    if not arguments or arguments[0] in ("-h", "--help"):
        return _fail(USAGE)
    if arguments[0] == "--read":
        return read_secrets(arguments[1:], environment, broker)
    return run_launcher(arguments[0], arguments[1:], environment, broker)


if __name__ == "__main__":
    raise SystemExit(main())
