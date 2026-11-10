"""The Healthchecks ping-URL adapter: a lane's dead-man check's ping URL (ADR-0055 amendments 19,
21 and 23).

The executor reads the URL from the Management key's check listing (`readers.PingUrls`), taking
the one check whose name is the registry row's `healthchecks_check`, before the unit is claimed
(`worker._read_mint_waits`), and writes it into its keeper, the lane's ping-URL secret in
`Rotation / Keepers`, which the lane's account reads by a direct grant. That secret is the only
destination: nothing else holds a copy. No person pastes a ping URL, and the inbox is never read
for one (amendment 23, under Devon's standing answer that a ping URL is configuration while the
estate is in development).

* **Issue only.** A ping URL can't be rotated, only replaced by recreating its check, which starts
  a new history (infraops's class landmine), so the type has an issue plan and no rotation plan.
  Its mint creates nothing (`Capabilities.creates`), so a package allows no `credential_create`.
* **Verify by shape, then by a read-only lookup** (ADR-0055 amendment 21, Devon's D-31): the
  keeper holds the inbox's empty marker, the value is exactly the self-hosted instance's ping base
  followed by one UUID, and the executor's read-only Healthchecks key finds the check by that
  UUID's `unique_key` (200) while a random UUID's `unique_key` finds none (404), the known-bad
  control. Nothing is ever sent to the ping URL, because a ping marks the check up. The lookup
  reaches only the UUID's first half, the part `unique_key` hashes; on this path nobody types the
  URL, since Healthchecks builds `ping_url` from its own `PING_ENDPOINT` and the check's UUID.
  Cut-over step 4 (the container's first scheduled ping landing on that check) is the only proof
  of the full URL.
* **No message names the value.** A log line names the URL by fingerprint only, and evidence names
  a check only by its `unique_key`'s first characters, which Healthchecks shows a read-only key in
  place of the UUID.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Final

from rotation_worker.contract import ContractError
from rotation_worker.destinations import DestinationError, credential_destinations, parse
from rotation_worker.plans import derive_issue
from rotation_worker.readers import (
    CHECKS,
    HEALTHCHECKS_URL,
    KEY_PREFIX,
    Healthchecks,
    PingUrls,
    ReadError,
)
from rotation_worker.registry import CHECK_FIELD
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
    """Refuse, before a claim, a row naming any destination beyond its provider and keeper, or no
    check to read the URL of."""
    try:
        kinds = sorted(parse(d)["kind"] for d in credential_destinations(credential))
    except DestinationError as error:
        raise ContractError(f"a ping-URL row's consumers are malformed: {error}") from None
    if kinds != ["bws-secret", "provider"]:
        raise ContractError("a ping-URL row's one destination is its keeper")
    check_name(credential)


def check_name(credential: Mapping[str, Any]) -> str:
    """The name of the check whose URL the row's keeper gets: its `healthchecks_check`."""
    name = credential.get(CHECK_FIELD)
    if not isinstance(name, str) or not name or name.strip() != name:
        raise ContractError(f"a ping-URL row names its check in {CHECK_FIELD}, a trimmed string")
    return name


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
        *,
        ping_urls: PingUrls | None = None,
        check: str | None = None,
    ) -> None:
        self._healthchecks = healthchecks
        self._random_uuid = random_uuid
        self._ping_urls = ping_urls
        self._check = check

    @property
    def mint_source(self) -> str:
        """What the mint reads, for a log line: the check, by its name."""
        return self._check or "no check"

    def read_mint(self) -> Value:
        """The check's ping URL, from this pass's listing, refused unless it is of the self-hosted
        shape. `ReadError` refuses the unit; `ReadUnavailable` and `StoreError` wait."""
        if self._ping_urls is None:
            raise ReadError("no Healthchecks Management key configured to read a ping URL with")
        if self._check is None:
            raise ReadError(f"the row names no check in {CHECK_FIELD}")
        url = self._ping_urls.ping_url(self._check)
        if self.shape_problem(url) is not None:
            raise ReadError(
                f"check {self._check!r}'s ping_url isn't the self-hosted ping base followed "
                "by one UUID"
            )
        return url

    def shape_problem(self, new: Value) -> str | None:
        """Why `new` isn't a ping URL of the self-hosted instance, or `None`."""
        if SHAPE.fullmatch(new.reveal()) is None:
            return "it isn't the self-hosted Healthchecks ping base followed by one UUID"
        return None

    def lookup_note(self, new: Value) -> str:
        """What `verify_new` files beside its probes: the check, by its `unique_key`'s start."""
        start = unique_key(new)[:KEY_PREFIX]
        return f"the read-only key looked up the check whose unique_key starts {start}"

    def mint(self) -> Value:
        """The URL `read_mint` found before the claim: the listing is read once per pass, so
        nothing is read again here."""
        return self.read_mint()

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
