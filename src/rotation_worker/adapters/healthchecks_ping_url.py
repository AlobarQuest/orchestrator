"""The Healthchecks ping-URL adapter: a lane's dead-man check's ping URL (ADR-0055 amendment 19).

A person copies the URL from the check's page on the self-hosted Healthchecks and pastes it into
the VM's inbox (ADR-0059 Q10). The executor writes it into its keeper, the lane's ping-URL secret
in `Rotation / Keepers`, which the lane's account reads by a direct grant. That secret is the only
destination: nothing else holds a copy.

* **Issue only.** A ping URL can't be rotated, only replaced by recreating its check, which starts
  a new history (infraops's class landmine), so the type has an issue plan and no rotation plan.
* **Bind and verify by shape, then by a read-only lookup** (ADR-0055 amendment 21, Devon's
  D-31): the keeper holds the inbox's empty marker, the value is exactly the self-hosted
  instance's ping base followed by one UUID, and the executor's read-only Healthchecks key finds
  the check by that UUID's `unique_key` (200) while a random UUID's `unique_key` finds none
  (404), the known-bad control. Nothing is ever sent to the ping URL, because a ping marks the
  check up. A check outside the read-only key's project answers 404, so the bind refuses it.
  The lookup proves only that the check exists in that project and that the UUID matches it in
  its first half, the part `unique_key` hashes: a URL wrong only in its last 16 hex characters
  passes. Cut-over step 4 (the container's first scheduled ping landing on that check) is the
  only proof of the full URL.
* **The UUID is the secret.** No message names the value or any part of it. A log line or
  evidence names a check only by its `unique_key`'s first characters, which Healthchecks shows a
  read-only key in place of the UUID.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Final

from rotation_worker.contract import EMPTY, LIVE_STATUS, ContractError
from rotation_worker.destinations import DestinationError, credential_destinations, parse
from rotation_worker.plans import derive_issue
from rotation_worker.readers import CHECKS, HEALTHCHECKS_URL, KEY_PREFIX, Healthchecks
from rotation_worker.types import TYPES
from rotation_worker.values import Value

NAME: Final = "healthchecks-ping-url"
# Its own provider name: every registry row naming it is a ping URL's.
PROVIDER: Final = NAME
PING_BASE: Final = f"{HEALTHCHECKS_URL}/ping/"
SHAPE: Final = re.compile(
    re.escape(PING_BASE) + r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
# Healthchecks answers a read-only lookup of a check outside the key's project with 404.
NOT_FOUND: Final = 404
PLAN: Final = derive_issue(TYPES[NAME].capabilities, (), "", "bws-secret")


def check_row(credential: Mapping[str, Any]) -> None:
    """Refuse, before a claim, a row naming any destination beyond its provider and keeper."""
    try:
        kinds = sorted(parse(d)["kind"] for d in credential_destinations(credential))
    except DestinationError as error:
        raise ContractError(f"a ping-URL row's consumers are malformed: {error}") from None
    if kinds != ["bws-secret", "provider"]:
        raise ContractError("a ping-URL row's one destination is its keeper")


def unique_key(ping_url: Value) -> str:
    """The `unique_key` Healthchecks reads the URL's check by: the SHA-1 hex digest of the first 16
    characters of the check UUID's hex form, which has no hyphens (Healthchecks `v4.4`,
    `hc/api/models.py` lines 432-434, `Check.unique_key`: `self.code.hex[:16]`)."""
    text = ping_url.reveal()
    if SHAPE.fullmatch(text) is None:
        raise ContractError("only a self-hosted ping URL is looked up")
    code = uuid.UUID(text.removeprefix(PING_BASE))
    return hashlib.sha1(code.hex[:16].encode()).hexdigest()


class HealthchecksPingUrlProvider:
    name = PROVIDER
    probe_target = f"GET {HEALTHCHECKS_URL}{CHECKS}<unique_key>, with the read-only key"
    revoke_probe_target = "none: a ping URL is never revoked"
    dead_statuses: frozenset[int] = frozenset({NOT_FOUND})
    hosts_destinations = False

    def __init__(
        self,
        healthchecks: Healthchecks | None,
        random_uuid: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self._healthchecks = healthchecks
        self._random_uuid = random_uuid

    def shape_problem(self, new: Value) -> str | None:
        """Why `new` isn't a ping URL of the self-hosted instance, or `None`."""
        if SHAPE.fullmatch(new.reveal()) is None:
            return "it isn't the self-hosted Healthchecks ping base followed by one UUID"
        return None

    def bind_refusal(self, new: Value, old: Value) -> str | None:
        """Why a pasted value isn't a ping URL to issue (amendment 21), or `None`."""
        if old.reveal() != EMPTY:
            return "the keeper doesn't hold only the marker: a ping URL is issued, never rotated"
        problem = self.shape_problem(new)
        if problem is not None:
            return problem
        control = self.probe(self.known_bad())
        if control is None:
            return "Healthchecks gave no answer to the lookup, so it is asked again next pass"
        if control != NOT_FOUND:
            return f"the lookup of a random UUID's check answered {control}, not {NOT_FOUND}"
        status = self.probe(new)
        if status == NOT_FOUND:
            return (
                "the read-only key finds no such check: a URL wrong in its UUID's first half, "
                "or another project's check"
            )
        if status is None:
            return "Healthchecks gave no answer to the lookup, so it is asked again next pass"
        if status != LIVE_STATUS:
            return f"the lookup of its check answered {status}"
        return None

    def lookup_note(self, new: Value) -> str:
        """What `verify_new` files beside its probes: the check, by its `unique_key`'s start."""
        start = unique_key(new)[:KEY_PREFIX]
        return f"the read-only key looked up the check whose unique_key starts {start}"

    def mint(self) -> Value:
        raise ContractError("a person copies a ping URL from its check's page")

    def known_bad(self) -> Value:
        """A ping URL of a random UUID: no check in the project has its `unique_key`."""
        return Value(f"{PING_BASE}{self._random_uuid()}")

    def probe(self, value: Value) -> int | None:
        """The read-only lookup of the URL's check; nothing is sent to the URL itself."""
        if self._healthchecks is None:
            raise ContractError("a ping URL is looked up with the read-only Healthchecks key")
        return self._healthchecks.check_status(unique_key(value))

    def probe_before_revoke(self, value: Value) -> int | None:
        raise ContractError("a ping URL is never revoked; its check is recreated instead")

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("a ping URL has no hosted service to host it")

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        raise ContractError("a ping URL has no hosted consumer")

    def revoke(self, value: Value) -> str:
        raise ContractError("a ping URL is never revoked; its check is recreated instead")
