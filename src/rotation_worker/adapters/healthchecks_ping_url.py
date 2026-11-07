"""The Healthchecks ping-URL adapter: a lane's dead-man check's ping URL (ADR-0055 amendment 19).

A person copies the URL from the check's page on the self-hosted Healthchecks and pastes it into
the VM's inbox (ADR-0059 Q10). The executor writes it into its keeper, the lane's ping-URL secret
in `Rotation / Keepers`, which the lane's account reads by a direct grant. That secret is the only
destination: nothing else holds a copy.

* **Issue only.** A ping URL can't be rotated, only replaced by recreating its check, which starts
  a new history (infraops's class landmine), so the type has an issue plan and no rotation plan.
* **Bind and verify by shape** (item 5): the keeper holds the inbox's empty marker, and the value
  is exactly the self-hosted instance's ping base followed by one UUID. Nothing probes it, because
  a ping marks the check up, and the VM holds no Healthchecks key that identifies a check (D-28).
  The lane's check going overdue after its first scheduled pass shows a wrong URL.
* **The UUID is the secret.** No message names the value or any part of it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from rotation_worker.contract import EMPTY, ContractError
from rotation_worker.destinations import DestinationError, credential_destinations, parse
from rotation_worker.plans import derive_issue
from rotation_worker.readers import HEALTHCHECKS_URL
from rotation_worker.types import TYPES
from rotation_worker.values import Value

NAME: Final = "healthchecks-ping-url"
# Its own provider name: every registry row naming it is a ping URL's.
PROVIDER: Final = NAME
PING_BASE: Final = f"{HEALTHCHECKS_URL}/ping/"
SHAPE: Final = re.compile(
    re.escape(PING_BASE) + r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
PLAN: Final = derive_issue(TYPES[NAME].capabilities, (), "", "bws-secret")


def check_row(credential: Mapping[str, Any]) -> None:
    """Refuse, before a claim, a row naming any destination beyond its provider and keeper."""
    try:
        kinds = sorted(parse(d)["kind"] for d in credential_destinations(credential))
    except DestinationError as error:
        raise ContractError(f"a ping-URL row's consumers are malformed: {error}") from None
    if kinds != ["bws-secret", "provider"]:
        raise ContractError("a ping-URL row's one destination is its keeper")


class HealthchecksPingUrlProvider:
    name = PROVIDER
    probe_target = "none: a ping URL is proved by its shape alone"
    revoke_probe_target = probe_target
    dead_statuses: frozenset[int] = frozenset()
    hosts_destinations = False

    def shape_problem(self, new: Value) -> str | None:
        """Why `new` isn't a ping URL of the self-hosted instance, or `None`."""
        if SHAPE.fullmatch(new.reveal()) is None:
            return "it isn't the self-hosted Healthchecks ping base followed by one UUID"
        return None

    def bind_refusal(self, new: Value, old: Value) -> str | None:
        """Why a pasted value isn't a ping URL to issue (item 5), or `None`."""
        if old.reveal() != EMPTY:
            return "the keeper doesn't hold only the marker: a ping URL is issued, never rotated"
        return self.shape_problem(new)

    def mint(self) -> Value:
        raise ContractError("a person copies a ping URL from its check's page")

    def known_bad(self) -> Value:
        raise ContractError("a ping URL is never probed, so it has no known-bad control")

    def probe(self, value: Value) -> int | None:
        raise ContractError("a ping URL is never probed: a ping marks its check up")

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("a ping URL has no hosted service to host it")

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        raise ContractError("a ping URL has no hosted consumer")

    def revoke(self, value: Value) -> str:
        raise ContractError("a ping URL is never revoked; its check is recreated instead")
