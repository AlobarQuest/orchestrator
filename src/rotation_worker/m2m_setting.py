"""The orchestrator's credentials setting, rewritten for one entry (ADR-0055 decision 7 and
amendment 9).

`ORCHESTRATOR_M2M_CREDENTIALS` is one JSON object of every machine identity: a key id mapped to
`{agent_id, token_hash}` and, during a rotation's overlap, `previous: {token_hash, until}`. The
executor's Coolify token can't read the stored value, so it starts from what the running
orchestrator loaded (its identities route) and changes only the entry its approved
destination names. Every other entry is written back as served, so a hand edit since the last
rotation stands.

A setting the orchestrator refuses at boot takes production down, and the executor can't import the
orchestrator's parser, so `build` refuses everything that parser refuses before a byte is written:
a hash that isn't 64 lowercase hex characters, a `previous` hash equal to any entry's current hash,
and an `until` that isn't UTC or is more than eight days ahead. Key ids and agent ids are copied
from what was served, so `build` can't change them. `tests/api/test_m2m_identities_api.py` loads
what it builds through the orchestrator's own parser.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from rotation_worker.contract import ContractError
from rotation_worker.values import Value

HASH: Final = re.compile(r"^[0-9a-f]{64}$")
# Decision 7: the overlap's backstop is seven days, and boot refuses one more than eight ahead.
OVERLAP: Final = timedelta(days=7)
MAX_OVERLAP: Final = timedelta(days=8)


class Change(StrEnum):
    # Accept the new value beside the old one.
    HOST = "host"
    # Stop accepting the old value.
    RETIRE = "retire"


@dataclass(frozen=True)
class Rewrite:
    """The setting to write, or None when the served one already says what the change asks."""

    text: str | None
    # sha256 prefix of the text, for evidence: the text itself holds 64-character hashes.
    fingerprint: str | None


def bearer_hash(value: Value) -> str:
    return hashlib.sha256(value.reveal().encode()).hexdigest()


def build(
    served: Mapping[str, Any],
    entry: str,
    change: Change,
    new_hash: str,
    old_hash: str,
    now: datetime,
) -> Rewrite:
    """The setting with `entry` changed. Refuses a served setting this rotation didn't leave."""
    identities = {key: dict(value) for key, value in served.items()}
    target = identities.get(entry)
    if target is None:
        raise ContractError("the orchestrator has no entry with the approved key id")
    previous = target.get("previous")
    previous_hash = previous.get("token_hash") if isinstance(previous, Mapping) else None
    if change is Change.HOST:
        if target.get("token_hash") == new_hash and previous_hash == old_hash:
            return Rewrite(None, None)
        if target.get("token_hash") != old_hash:
            raise ContractError("the entry holds neither the old hash nor the new one")
        target["token_hash"] = new_hash
        target["previous"] = {"token_hash": old_hash, "until": (now + OVERLAP).isoformat()}
        # An expired overlap authenticates nothing; rotating is a good time to drop it.
        for key, identity in identities.items():
            if key != entry and _expired(identity, now):
                del identity["previous"]
    else:
        if target.get("token_hash") != new_hash:
            raise ContractError("the entry doesn't hold the new hash")
        if previous is None:
            return Rewrite(None, None)
        if previous_hash != old_hash:
            raise ContractError("the entry's previous hash isn't the old value's")
        del target["previous"]
    _refuse_what_boot_refuses(identities, now)
    text = json.dumps(identities, separators=(",", ":"))
    return Rewrite(text, hashlib.sha256(text.encode()).hexdigest()[:8])


def _expired(identity: Mapping[str, Any], now: datetime) -> bool:
    previous = identity.get("previous")
    return isinstance(previous, Mapping) and _until(previous) <= now


def _until(previous: Mapping[str, Any]) -> datetime:
    try:
        until = datetime.fromisoformat(str(previous.get("until")))
    except ValueError as error:
        raise ContractError("a previous hash's until isn't an instant") from error
    if until.utcoffset() != timedelta(0):
        raise ContractError("a previous hash's until isn't UTC")
    return until


def _refuse_what_boot_refuses(identities: Mapping[str, dict[str, Any]], now: datetime) -> None:
    current: set[str] = set()
    for key, identity in identities.items():
        if set(identity) - {"previous"} != {"agent_id", "token_hash"}:
            raise ContractError(f"entry {key} isn't an agent id and a hash")
        if not isinstance(identity["token_hash"], str) or not HASH.fullmatch(
            identity["token_hash"]
        ):
            raise ContractError(f"entry {key}'s hash isn't a sha256")
        current.add(identity["token_hash"])
    for key, identity in identities.items():
        _refuse_a_bad_previous(key, identity.get("previous"), current, now)


def _refuse_a_bad_previous(key: str, previous: object, current: set[str], now: datetime) -> None:
    if previous is None:
        return
    if not isinstance(previous, Mapping) or set(previous) != {"token_hash", "until"}:
        raise ContractError(f"entry {key}'s previous isn't a hash and an until")
    if not HASH.fullmatch(str(previous["token_hash"])) or previous["token_hash"] in current:
        raise ContractError(f"entry {key}'s previous hash is malformed or still current")
    if _until(previous) > now + MAX_OVERLAP:
        raise ContractError(f"entry {key}'s until is more than eight days ahead")
