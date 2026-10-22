"""The BWS machine-token adapter: a Bitwarden access token, created and revoked by a person.

ADR-0055 amendment 12. Bitwarden offers no machine path to create or revoke an access token, so
mint and stage are one human act (the token is created in the web app and staged into the
Keychain with `rotation-worker stage`) and retire-old another. Everything between runs on the
Keychain instance of the executor, on the machine whose Keychain holds the token:

* **Probe** is a fresh login (amendment 1): a new SDK client in a child process with an empty,
  throwaway `HOME` and only `PATH`, the token on its stdin. A login answers 200; Bitwarden's
  `[400 Bad Request] {"error":"invalid_client"}` answers 400, for a revoked token and a malformed
  one alike; anything else is indeterminate. The known-bad control is a well-formed random token.
  Measured 2026-10-22: a random token and a just-revoked one both answered `invalid_client`, and
  a live one logged in, while a client that logged in before the revoke kept reading.
* **The switch** is store then distribute: the keeper Keychain item is quarantined and updated,
  then every other Keychain item that held the old value is updated in place. Each is then read
  back by verify-consumers, and a scheduled reader is verified by its run since the switch.
* **The `verify` unit gates the person's revoke**, as the Bitbucket plan's does, and proves the
  old value live just before it.
* **Confirm-dead waits out the exposure window.** A session that logged in before the revoke
  keeps reading, so `confirm` is claimed no earlier than `REVOKE_SETTLES` after the person
  recorded the revoke.
* **Switches wait out the night.** The scheduled lanes read these tokens between 02:00 and
  07:35 New York time, so the `store` unit isn't claimed then.
"""

from __future__ import annotations

import base64
import secrets
import string
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from rotation_worker.contract import (
    Actor,
    ContractError,
    HumanAct,
    Plan,
    Step,
    StepKind,
    UnitPlan,
    validate_plan,
)
from rotation_worker.values import Value

NAME: Final = "bws-machine-token"
PROVIDER: Final = "bitwarden-sm"
LOGIN_TIMEOUT_SECONDS: Final = 60.0
# Longer than a session's lifetime after a revoke: amendment 1's window.
REVOKE_SETTLES: Final = timedelta(minutes=75)
QUIET_ZONE: Final = ZoneInfo("America/New_York")
QUIET_START: Final = time(2, 0)
QUIET_END: Final = time(7, 35)

M, H = Actor.MACHINE, Actor.HUMAN
PLAN: Final = Plan(
    (
        HumanAct("mint", (Step(StepKind.MINT, H), Step(StepKind.STAGE, H))),
        UnitPlan(
            "store",
            (
                Step(StepKind.VERIFY_NEW, M, "AC-001"),
                Step(StepKind.STORE, M, "AC-002"),
                Step(StepKind.DISTRIBUTE, M, "AC-003"),
            ),
        ),
        UnitPlan("verify", (Step(StepKind.VERIFY_CONSUMERS, M, "AC-004"),)),
        HumanAct("revoke", (Step(StepKind.RETIRE_OLD, H),)),
        UnitPlan("confirm", (Step(StepKind.CONFIRM_DEAD, M, "AC-005"),)),
    )
)
validate_plan(PLAN)

# Run in the child: logs in once and says how it went, never echoing the token.
_LOGIN: Final = """
import sys
from bitwarden_sdk import BitwardenClient
token = sys.stdin.read()
try:
    BitwardenClient().auth().login_access_token(token)
except Exception as error:
    message = str(error)
    print("refused" if "invalid_client" in message and "400" in message else "other")
else:
    print("live")
"""
_ANSWERS: Final = {"live": 200, "refused": 400}


def wait_reason(unit: UnitPlan, history: Sequence[Mapping[str, Any]], now: datetime) -> str | None:
    """Why this unit can't be claimed yet, or `None`. Asked before every claim."""
    if unit.name == "store":
        local = now.astimezone(QUIET_ZONE).time()
        if QUIET_START <= local < QUIET_END:
            return "the scheduled lanes read these tokens until 07:35 New York time"
    if unit.name == "confirm":
        revoked = _revoked_at(history)
        if revoked is None:
            return "no revoke is recorded yet"
        if now < revoked + REVOKE_SETTLES:
            return f"a session from before the revoke may read until {revoked + REVOKE_SETTLES}"
    return None


def _revoked_at(history: Sequence[Mapping[str, Any]]) -> datetime | None:
    # The confirm unit's one human act is the revoke; a completed unit it waited on resolves
    # with a `reason`, never a `command`.
    found = [
        datetime.fromisoformat(str(event["occurred_at"]))
        for event in history
        if event.get("action") == "dependency.resolved"
        and ((event.get("payload") or {}).get("command") or {}).get("status") == "satisfied"
    ]
    return max(found) if found else None


class BwsMachineTokenProvider:
    name = PROVIDER
    probe_target = "a fresh Bitwarden login, new client and empty HOME"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({400})
    hosts_destinations = False

    def __init__(self, interpreter: str = sys.executable) -> None:
        self._interpreter = interpreter

    def mint(self) -> Value:
        raise ContractError("a person creates a BWS access token, in the web app")

    def known_bad(self) -> Value:
        letters = string.ascii_letters + string.digits
        secret = "".join(secrets.choice(letters) for _ in range(30))
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        return Value(f"0.{uuid.uuid4()}.{secret}:{key}")

    def probe(self, value: Value) -> int | None:
        with tempfile.TemporaryDirectory(prefix="rotation-login-") as home:
            try:
                answer = subprocess.run(
                    [self._interpreter, "-I", "-c", _LOGIN],
                    input=value.reveal(),
                    capture_output=True,
                    text=True,
                    timeout=LOGIN_TIMEOUT_SECONDS,
                    env={"PATH": "/usr/bin:/bin", "HOME": home},
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return None
        return _ANSWERS.get(answer.stdout.strip())

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("a BWS access token has no hosted service to host it")

    def revoke(self, value: Value) -> str:
        raise ContractError("a person revokes a BWS access token, in the web app")
