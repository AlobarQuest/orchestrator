"""secrets-broker: hand each lane only its allowlisted BWS secrets over its own Unix socket.

One service on `prod-vps` holds one BWS identity and serves every lane in the allowlist
(ADR-0055 amendment 30), so a lane cut over to it needs no BWS token of its own:

- The allowlist, `lanes/secrets-broker/allowlist.toml`, is baked into the image, so it changes only
  through a reviewed orchestrator PR and a release. The broker refuses to start if it is missing,
  unreadable, names a lane twice, or holds a malformed lane name or UUID.
- Each lane gets `<root>/<lane>/broker.sock`, mode 0600, in a directory of mode 0750. A lane's
  compose mounts only its own directory, so the socket a request arrives on is the lane's identity:
  the request carries no token, header or claim.
- One request per connection: a UUID and a newline, at most 64 bytes, read within 5 seconds. The
  reply is one JSON line in the shape `bws secret get --output json` prints,
  `{"id": ..., "value": ...}`, or `{"error": "<code>"}` with `not_allowed`, `bad_request` or
  `bws_failed`, and the connection closes.
- A UUID outside the lane's list is refused before `bws` runs. Each listed one is fetched fresh,
  with at most 8 fetches at once and 15 seconds each, under a new empty `HOME` and with
  `--color no` and no colour variables (workspace invariants 22 and 23). Nothing is cached or
  written to disk.
- An error carries no value and none of `bws`'s output: its stderr is discarded, and its stdout is
  only parsed. The broker logs one JSON line per request to stdout, `lane`, `uuid` and `outcome`,
  never a value.

Usage:
    secrets_broker.py --allowlist <path> --root <directory>

The image runs it as `/app/.venv/bin/python -I /app/scripts/secrets_broker.py` (standard library
only, like `lane_run.py`), and it serves until SIGTERM. Exit codes: 0 after SIGTERM, 2 for a usage
error, a refused allowlist, a missing `bws`, or an unset or empty `BWS_ACCESS_TOKEN`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any

LANE = re.compile(r"[a-z][a-z0-9-]*")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SOCKET_NAME = "broker.sock"
MAX_REQUEST = 64
READ_TIMEOUT_S = 5.0
FETCH_TIMEOUT_S = 15.0
MAX_FETCHES = 8
# What a closing connection reads of a client's leftover bytes, so a Linux peer isn't reset
# before it reads the reply.
DRAIN_BYTES, DRAIN_TIMEOUT_S = 4096, 1.0
TOKEN = "BWS_ACCESS_TOKEN"
EXIT_OK, EXIT_USAGE = 0, 2

Fetcher = Callable[[str], str | None]
_LOG = threading.Lock()


class AllowlistError(Exception):
    """The allowlist can't be used; the broker doesn't start."""


def load_allowlist(path: Path) -> dict[str, frozenset[str]]:
    """Each lane's readable UUIDs, from the file at `path`."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise AllowlistError(f"the allowlist {path} can't be read: {error}") from error
    return parse_allowlist(text, path)


def parse_allowlist(text: str, path: Path | str) -> dict[str, frozenset[str]]:
    """Each lane's readable UUIDs, from the allowlist's text (`path` names it in a refusal).
    `tomllib` itself refuses a lane named twice."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise AllowlistError(f"the allowlist {path} can't be read: {error}") from error
    lanes = document.get("lanes")
    if not isinstance(lanes, dict) or set(document) != {"lanes"}:
        raise AllowlistError(f"the allowlist {path} must hold exactly a [lanes] table")
    allowlist = {}
    for lane, entry in lanes.items():
        if not LANE.fullmatch(lane):
            raise AllowlistError(f"the allowlist names a malformed lane {lane!r}")
        read = entry.get("read") if isinstance(entry, dict) else None
        if not isinstance(read, list) or set(entry) != {"read"}:
            raise AllowlistError(f"lane {lane!r} must hold exactly a `read` list")
        for uuid in read:
            if not isinstance(uuid, str) or not UUID.fullmatch(uuid):
                raise AllowlistError(f"lane {lane!r} lists a malformed UUID {uuid!r}")
        allowlist[lane] = frozenset(read)
    return allowlist


def _fetch_environment(source: Mapping[str, str], home: str) -> dict[str, str]:
    """`bws`'s whole environment: `PATH`, the new `home` and the token. No colour variable is
    copied, nor anything else."""
    environment = {"HOME": home}
    for name in ("PATH", TOKEN):
        if name in source:
            environment[name] = source[name]
    return environment


def fetch(uuid: str, bws: str, timeout_s: float) -> str | None:
    """The secret's value, or None on any failure. Nothing `bws` prints is ever passed on."""
    with tempfile.TemporaryDirectory(prefix="secrets-broker-") as home:
        try:
            # Its own session, so a timeout kills every process `bws` started and none keeps
            # the pipe open.
            with subprocess.Popen(
                [bws, "secret", "get", uuid, "--output", "json", "--color", "no"],
                env=_fetch_environment(os.environ, home),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            ) as process:
                try:
                    output, _ = process.communicate(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    return None
            if process.returncode != 0:
                return None
            document = json.loads(output)
        except OSError, ValueError:
            return None
    value = document.get("value") if isinstance(document, dict) else None
    return value if isinstance(value, str) else None


def _log(lane: str, uuid: str | None, outcome: str) -> None:
    line = json.dumps({"lane": lane, "uuid": uuid, "outcome": outcome})
    with _LOG:
        print(line, flush=True)


def _error(lane: str, uuid: str | None, code: str) -> bytes:
    _log(lane, uuid, code)
    return (json.dumps({"error": code}) + "\n").encode()


def handle(lane: str, request: bytes, allowed: frozenset[str], fetcher: Fetcher) -> bytes:
    """The reply line to one request on `lane`'s socket."""
    text = request.decode("ascii", errors="replace")
    if len(request) > MAX_REQUEST or not text.endswith("\n") or not UUID.fullmatch(text[:-1]):
        return _error(lane, None, "bad_request")
    uuid = text[:-1]
    if uuid not in allowed:
        return _error(lane, uuid, "not_allowed")
    value = fetcher(uuid)
    if value is None:
        return _error(lane, uuid, "bws_failed")
    _log(lane, uuid, "ok")
    return (json.dumps({"id": uuid, "value": value}) + "\n").encode()


def _read_request(connection: socket.socket) -> bytes:
    """The request line, or b"" when the client is silent past `READ_TIMEOUT_S` in all. Reads at
    most one byte past the limit, so an oversized request is refused without reading the rest."""
    deadline = time.monotonic() + READ_TIMEOUT_S
    request = b""
    while b"\n" not in request and len(request) <= MAX_REQUEST:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return b""
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(MAX_REQUEST + 1 - len(request))
        except TimeoutError:
            return b""
        if not chunk:
            break
        request += chunk
    return request


def _close_gently(connection: socket.socket) -> None:
    """End the reply, then read a little of what the client left unread before closing."""
    try:
        connection.shutdown(socket.SHUT_WR)
        connection.settimeout(DRAIN_TIMEOUT_S)
        drained = 0
        while drained < DRAIN_BYTES and (chunk := connection.recv(DRAIN_BYTES)):
            drained += len(chunk)
    except OSError:
        pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path: Path, lane: str, allowed: frozenset[str], fetcher: Fetcher) -> None:
        self.lane, self.allowed, self.fetcher = lane, allowed, fetcher
        super().__init__(str(path), _Handler, bind_and_activate=False)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Print nothing: a traceback could carry what the request held."""


class _Handler(socketserver.BaseRequestHandler):
    server: _Server

    def handle(self) -> None:
        server = self.server
        try:
            request = _read_request(self.request)
            reply = handle(server.lane, request, server.allowed, server.fetcher)
            self.request.settimeout(READ_TIMEOUT_S)
            self.request.sendall(reply)
            _close_gently(self.request)
        except OSError:
            pass
        except Exception:
            # Logged without its text, which could carry what the request or `bws` held. The
            # client gets no reply, as when the broker is down.
            _log(server.lane, None, "internal")


def _bind(root: Path, lane: str, allowed: frozenset[str], fetcher: Fetcher) -> _Server:
    """The lane's server, bound and listening at `<root>/<lane>/broker.sock`, mode 0600 before it
    accepts anything. A stale socket from an earlier run is removed first."""
    directory = root / lane
    directory.mkdir(mode=0o750, exist_ok=True)
    directory.chmod(0o750)
    path = directory / SOCKET_NAME
    path.unlink(missing_ok=True)
    server = _Server(path, lane, allowed, fetcher)
    try:
        server.server_bind()
        path.chmod(0o600)
        server.server_activate()
    except OSError:
        server.server_close()
        raise
    return server


def serve(
    allowlist: dict[str, frozenset[str]],
    root: Path,
    fetcher: Fetcher,
    *,
    stop: threading.Event | None = None,
) -> None:
    """Serve each lane on its own socket until `stop` is set; `main` sets it on SIGTERM. An empty
    allowlist binds nothing and waits all the same."""
    stop = threading.Event() if stop is None else stop
    gate = threading.BoundedSemaphore(MAX_FETCHES)

    def limited(uuid: str) -> str | None:
        with gate:
            return fetcher(uuid)

    # Each server's loop starts as soon as it is bound, so every server listed here is running
    # and `shutdown` (which waits for the loop to stop) returns, even when a later bind fails.
    servers = []
    try:
        for lane, allowed in sorted(allowlist.items()):
            server = _bind(root, lane, allowed, limited)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        stop.wait()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def _refuse(message: str) -> int:
    """One JSON error line on stderr for a refusal to start."""
    print(json.dumps({"error": message}), file=sys.stderr)
    return EXIT_USAGE


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="secrets_broker.py")
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        allowlist = load_allowlist(arguments.allowlist)
    except AllowlistError as error:
        return _refuse(str(error))
    bws = shutil.which("bws")
    if bws is None:
        return _refuse("bws is not on PATH")
    # Without its token every fetch would fail; refuse to start, so the resource shows the cause.
    if not os.environ.get(TOKEN):
        return _refuse(f"{TOKEN} is not set")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
    serve(allowlist, arguments.root, partial(fetch, bws=bws, timeout_s=FETCH_TIMEOUT_S), stop=stop)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
