"""lane-run: start one lane's launcher in its container with a fixed environment (ADR-0059).

Every container run of a launcher goes through this program: the scheduler's crontab line and a
one-off `docker exec` alike. It starts the launcher with exactly the variables below and no other
(decision 1, "`lane-run` passes the launcher a fixed, tracked list of variables, and one secret"):

- `PATH`, set here to the image's directories, and `HOME`, a new empty directory for this run, so
  `bws` holds no cached session (workspace invariant #23). The directory is removed afterwards.
- `BWS_ACCESS_TOKEN`, copied from the container's environment: the run's only secret.
- `TZ` and `SDS_DEADMAN_PING_URL_UUID` (a BWS UUID, not a secret).
- Each lane state variable in `STATE_VARIABLES`, only when the container's environment sets it.

Each is copied only when the container's environment sets it. `LANE_SCHEDULE_ENABLED` is never
passed: the entrypoint reads it at container start, and no launcher reads it. An increment that
gives a launcher a new non-secret variable adds it here in the same change. No secret is ever
added: a launcher reads every secret from BWS through the token.

Usage:
    lane-run <lane> [launcher arguments...]   runs scripts/run-<lane>.sh beside this file
    lane-run --read <uuid>...                  proves the lane's BWS identity (decision 2)

`--read` starts no launcher. Under the same environment it runs one
`bws secret get <uuid> --color no` per UUID, discards what bws prints, and prints `<uuid> ok` or
`<uuid> refused`, never a value. The token never appears in a process argument: it reaches each
child through its environment alone.

Exit codes: a launcher run exits with the launcher's own code. `--read` exits 0 when every read
is `ok` and 1 when any is `refused`. A usage error, an unknown lane, a missing token for `--read`
or a missing `bws` exits 2.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

LAUNCHER_DIR = Path(__file__).resolve().parent
LANE_PATH = "/app/.venv/bin:/usr/local/bin:/usr/bin:/bin"
TOKEN = "BWS_ACCESS_TOKEN"
COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")
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
LANE = re.compile(r"^[a-z][a-z0-9-]*$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2
USAGE = "usage: lane-run <lane> [launcher arguments...] | lane-run --read <uuid>..."


def lane_environment(source: Mapping[str, str], home: str) -> dict[str, str]:
    """The launcher's whole environment: the fixed `PATH`, `home`, and each listed name `source`
    sets. Nothing else in `source` is copied."""
    environment = {"PATH": LANE_PATH, "HOME": home}
    for name in (*COPIED, *STATE_VARIABLES):
        if name in source:
            environment[name] = source[name]
    return environment


def _fail(message: str) -> int:
    print(f"lane-run: {message}", file=sys.stderr)
    return EXIT_USAGE


def run_launcher(lane: str, arguments: Sequence[str], source: Mapping[str, str]) -> int:
    launcher = LAUNCHER_DIR / f"run-{lane}.sh"
    if not LANE.match(lane) or not launcher.is_file():
        return _fail(f"no launcher for lane {lane!r} at {launcher}")
    with tempfile.TemporaryDirectory(prefix="lane-run-") as home:
        completed = subprocess.run(
            [str(launcher), *arguments], env=lane_environment(source, home), check=False
        )
    return completed.returncode


def read_secrets(uuids: Sequence[str], source: Mapping[str, str]) -> int:
    if not uuids:
        return _fail(USAGE)
    malformed = [uuid for uuid in uuids if not UUID.match(uuid)]
    if malformed:
        return _fail(f"not a BWS secret UUID: {', '.join(malformed)}")
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


def main(argv: Sequence[str] | None = None, source: Mapping[str, str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    environment = os.environ if source is None else source
    if not arguments or arguments[0] in ("-h", "--help"):
        return _fail(USAGE)
    if arguments[0] == "--read":
        return read_secrets(arguments[1:], environment)
    return run_launcher(arguments[0], arguments[1:], environment)


if __name__ == "__main__":
    raise SystemExit(main())
