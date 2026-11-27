#!/usr/bin/env python3
"""List what a BWS access token can see, as ids and counts only (ADR-0055 amendment 34 item 5).

The rotation executor's bind compares a pasted lane token's live reach with its registry row's
declared reach (`_REACH` in `src/rotation_worker/adapters/bws_machine_token.py`). Whether the SDK's
secret listing for a token with a project grant includes that project's secrets is unmeasured, and
it decides how the broker token's bind must read. This script makes the same three SDK calls as
`_REACH` (log in, list projects, list secrets) and prints one JSON line on stdout: the project ids
and secret ids the token sees, their counts, and, for each `--expect` id, whether it was listed.

It never prints a secret's value, key or name, the token, or any digest of the token. The token is
read from `BWS_REACH_PROBE_TOKEN` only, never the ambient `BWS_ACCESS_TOKEN`, so a session's own
identity can't be measured by mistake. The SDK runs with a new client and an empty, throwaway
`HOME`, as the executor's bind does, so no cached session answers for it.

Usage (needs the `rotation-worker` extra: `uv sync --frozen --extra rotation-worker`):

    BWS_REACH_PROBE_TOKEN="$(security find-generic-password -s Claude -a <item> -w)" \\
      .venv/bin/python -I scripts/bws_reach_probe.py --organization <org uuid> \\
      --expect <project uuid> --expect <uuid of a secret in that project>

Exit codes: 0 listed, 2 usage (no token, bad argument), 3 the login or a listing failed (the error's
class is named, never its text).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from typing import Any

TOKEN_VARIABLE = "BWS_REACH_PROBE_TOKEN"
LISTED, USAGE, FAILED = 0, 2, 3


def _sdk_client() -> Any:
    """A new SDK client. Imported here: the SDK is the `rotation-worker` extra, which CI's
    `uv sync --frozen` doesn't install."""
    from bitwarden_sdk import BitwardenClient

    return BitwardenClient()


def _uuid(text: str) -> str:
    try:
        return str(uuid.UUID(text))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a UUID: {text!r}") from None


def _arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List what a BWS access token can see.")
    parser.add_argument("--organization", required=True, type=_uuid, help="BWS organization id")
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        type=_uuid,
        help="a project or secret id to report as listed or not; repeatable",
    )
    return parser.parse_args(argv)


def _emit(line: dict[str, Any]) -> None:
    print(json.dumps(line, sort_keys=True))


def measure(
    token: str, organization: str, expect: Sequence[str], client_factory: Callable[[], Any]
) -> dict[str, Any]:
    """What `token` sees: sorted ids and counts, and whether each expected id was listed."""
    client = client_factory()
    client.auth().login_access_token(token)
    projects = sorted(str(p.id) for p in client.projects().list(organization).data.data)
    secrets = sorted(str(s.id) for s in client.secrets().list(organization).data.data)
    seen = set(projects) | set(secrets)
    return {
        "event": "bws_reach_probe.listed",
        "projects": {"count": len(projects), "ids": projects},
        "secrets": {"count": len(secrets), "ids": secrets},
        "expected": {identifier: identifier in seen for identifier in expect},
    }


def main(argv: Sequence[str] | None = None, client_factory: Callable[[], Any] = _sdk_client) -> int:
    try:
        arguments = _arguments(sys.argv[1:] if argv is None else argv)
    except SystemExit as error:  # argparse has already printed its usage line to stderr.
        return USAGE if error.code else LISTED
    token = os.environ.get(TOKEN_VARIABLE, "")
    if not token:
        _emit({"event": "bws_reach_probe.refused", "error": f"{TOKEN_VARIABLE} is unset or empty"})
        return USAGE
    previous = os.environ.get("HOME")
    with tempfile.TemporaryDirectory(prefix="bws-reach-probe-") as home:
        os.environ["HOME"] = home
        try:
            line = measure(token, arguments.organization, arguments.expect, client_factory)
        except Exception as error:  # The SDK's errors carry its text; only the class is printed.
            _emit({"event": "bws_reach_probe.failed", "error": type(error).__name__})
            return FAILED
        finally:
            if previous is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous
    _emit(line)
    return LISTED


if __name__ == "__main__":
    sys.exit(main())
