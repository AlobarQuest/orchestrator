"""The three BWS values `fork-sync onboard` reads, fetched in-process and never printed.

All three are readable by the broad machine account, whose token is the login Keychain item
`Claude` / `BWS_ACCESS_TOKEN_VPS_BACKUP` (the item `scripts/run-inert-landing.sh` reads as its
broad identity); the narrow `sds-operator` account reads none of them. `BWS_ACCESS_TOKEN_BROAD`
overrides the Keychain, as it does for that launcher.

The token reaches `bws` only through its environment and each value comes back on stdout, so
neither is ever in a process argument list. `FORCE_COLOR` and `CLICOLOR_FORCE` are dropped and
`--color no` passed, or `bws` wraps its JSON in escapes. A failure says which read failed and
never what `bws` printed.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from typing import Final

KEYCHAIN_SERVICE: Final = "Claude"
KEYCHAIN_ACCOUNT: Final = "BWS_ACCESS_TOKEN_VPS_BACKUP"
APP_PRIVATE_KEY_UUID: Final = "998b817e-6164-4b9f-9c52-b4e800e8270d"
"""`fork-secure-upstream-sync-app-private-key-b64` (Ops / Platform): base64 of the App's PEM."""
ANTHROPIC_API_KEY_UUID: Final = "b74bf8b3-938b-45c0-bc25-b415013cb563"
"""`anthropic-api-key` (Ops / Platform), the key every fork's review holds a copy of."""
CHANGE_MANAGER_READ_UUID: Final = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
"""`change-manager/M2M_TOKEN_READ` (Rotation / Keepers), the bearer the landing policy is read
with, as the inert landing lane reads it."""

SecretReader = Callable[[str], str]
"""A BWS secret's value by UUID; raises `Unavailable`."""


class Unavailable(RuntimeError):
    """A value could not be read. The message names the read, never a value."""


def _identity() -> str:
    override = os.environ.get("BWS_ACCESS_TOKEN_BROAD", "").strip()
    if override:
        return override
    keychain = ["-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"]
    found = subprocess.run(
        ["/usr/bin/security", "find-generic-password", *keychain], capture_output=True, text=True
    )
    if found.returncode != 0 or not found.stdout.strip():
        raise Unavailable(
            f"no BWS identity: the login Keychain has no item {KEYCHAIN_SERVICE} / "
            f"{KEYCHAIN_ACCOUNT} (the broad machine account's token), and "
            "BWS_ACCESS_TOKEN_BROAD is unset"
        )
    return found.stdout.strip()


def _bws() -> str:
    # launchd's PATH and a bare shell's differ; the CLI is installed in /usr/local/bin here.
    return shutil.which("bws") or "/usr/local/bin/bws"


def reader() -> SecretReader:
    """Reads through the broad identity, looked up on the first read and each value once."""
    identity: list[str] = []
    values: dict[str, str] = {}

    def read(uuid: str) -> str:
        if uuid not in values:
            if not identity:
                identity.append(_identity())
            env = {
                name: value
                for name, value in os.environ.items()
                if name not in ("FORCE_COLOR", "CLICOLOR_FORCE", "BWS_ACCESS_TOKEN")
            }
            got = subprocess.run(
                [_bws(), "secret", "get", uuid, "--output", "json", "--color", "no"],
                capture_output=True,
                text=True,
                env={**env, "BWS_ACCESS_TOKEN": identity[0]},
            )
            try:
                value = json.loads(got.stdout)["value"] if got.returncode == 0 else None
            except ValueError, KeyError, TypeError:
                value = None
            if not isinstance(value, str) or not value:
                raise Unavailable(
                    f"BWS secret {uuid} could not be read (bws exit {got.returncode})"
                )
            values[uuid] = value
        return values[uuid]

    return read


def pem(stored: str) -> str:
    """The App's PEM from its BWS keeper, which holds base64 of it (each Actions copy holds the
    decoded PEM: factory-runner's `.cred-consumers.toml`)."""
    try:
        decoded = base64.b64decode(stored.strip(), validate=True).decode("ascii")
    except binascii.Error, UnicodeDecodeError:
        raise Unavailable(f"BWS secret {APP_PRIVATE_KEY_UUID} is not base64 of a PEM") from None
    if not decoded.startswith("-----BEGIN ") or "PRIVATE KEY-----" not in decoded:
        raise Unavailable(f"BWS secret {APP_PRIVATE_KEY_UUID} does not decode to a private key")
    return decoded
