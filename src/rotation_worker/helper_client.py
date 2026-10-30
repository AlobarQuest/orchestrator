"""The executor's SSH client to the VPS Postgres helper (`deploy/vps/sds-pg-rotate`).

The helper runs as root behind an SSH forced command: one JSON line in, one JSON line out. A
password travels only on stdin, never in argv, a log or an error. The private key is written to a
0600 file for the one call and removed in a `finally`; the host key is pinned from configuration,
so a host that isn't the VPS is refused before anything is sent. No ssh stderr text reaches an
error message, only the exit code, because ssh may echo what it was given.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from rotation_worker.contract import ContractError
from rotation_worker.values import Value

SSH: Final = "/usr/bin/ssh"
TIMEOUT_SECONDS: Final = 120
REFUSED_EXIT: Final = 2
REASON: Final = re.compile(r"^[a-z_]{1,64}$")
FINGERPRINT_KEY: Final = re.compile(r"^[A-Za-z0-9_.-]+/[A-Z_][A-Z0-9_]*$")

Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class HelperClient:
    def __init__(
        self,
        host: str,
        known_host: str,
        key: Value,
        state_dir: Path,
        run: Runner = subprocess.run,
    ) -> None:
        self._host = host
        self._known_host = known_host
        self._key = key
        self._state_dir = state_dir
        self._run = run

    def set(self, target: str, value: Value) -> bool:
        """Set the target role's password; whether it changed anything."""
        answer = self._call({"verb": "set", "target": target, "password": value.reveal()})
        changed = answer.get("changed")
        if not isinstance(changed, bool):
            raise ContractError("the helper's set answer has no changed flag")
        return changed

    def probe(self, target: str, value: Value) -> int | None:
        """The HTTP-style status the target's login gives `value`: 200, 401, or None if unknown."""
        answer = self._call({"verb": "probe", "target": target, "password": value.reveal()})
        status = answer.get("status")
        if status is not None and (isinstance(status, bool) or status not in (200, 401)):
            raise ContractError("the helper's probe answer has an unknown status")
        return status

    def loaded(self, target: str) -> dict[str, str]:
        """What each container of the target loaded, as `<container>/<env>` to a fingerprint."""
        answer = self._call({"verb": "loaded", "target": target})
        prints = answer.get("fingerprints")
        if not isinstance(prints, dict) or not all(
            isinstance(name, str) and FINGERPRINT_KEY.fullmatch(name) and isinstance(print_, str)
            for name, print_ in prints.items()
        ):
            raise ContractError("the helper's loaded answer is malformed")
        return dict(prints)

    def _call(self, request: dict[str, str]) -> dict[str, Any]:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="pg-helper-", dir=self._state_dir))
        try:
            key_file, known_hosts = scratch / "key", scratch / "known_hosts"
            _write_private(key_file, self._key.reveal().rstrip("\n") + "\n")
            _write_private(known_hosts, self._known_host.rstrip("\n") + "\n")
            argv = ssh_argv(self._host, key_file, known_hosts)
            try:
                result = self._run(
                    argv,
                    input=json.dumps(request).encode(),
                    capture_output=True,
                    timeout=TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise ContractError("the helper didn't answer in time") from None
            except OSError as error:
                raise ContractError(f"ssh couldn't start: {type(error).__name__}") from None
        finally:
            _remove(scratch)
        return _answer(request["verb"], result)


def ssh_argv(host: str, key_file: Path, known_hosts: Path) -> list[str]:
    return [
        SSH,
        "-i",
        str(key_file),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        host,
    ]


def _remove(scratch: Path) -> None:
    for name in ("key", "known_hosts"):
        (scratch / name).unlink(missing_ok=True)
    scratch.rmdir()


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)


def _answer(verb: str, result: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    try:
        body = json.loads(result.stdout.decode().strip().splitlines()[-1])
    except ValueError, IndexError:
        body = None
    if not isinstance(body, dict):
        # ssh's own failures (a host-key mismatch, a refused login) exit 255 with no JSON.
        raise ContractError(f"the helper's {verb} gave no answer (exit {result.returncode})")
    refused = body.get("refused")
    if refused is not None or result.returncode == REFUSED_EXIT:
        reason = refused if isinstance(refused, str) and REASON.fullmatch(refused) else "unknown"
        raise ContractError(f"the helper refused {verb}: {reason}")
    if result.returncode != 0:
        raise ContractError(f"the helper's {verb} failed (exit {result.returncode})")
    return body
