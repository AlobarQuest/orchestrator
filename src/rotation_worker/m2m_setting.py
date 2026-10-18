"""The orchestrator's credentials setting, rewritten for one entry (ADR-0055 decision 7 and
amendment 9).

`ORCHESTRATOR_M2M_CREDENTIALS` is one JSON object of every machine identity: a key id mapped to
`{agent_id, token_hash}` and, during a rotation's overlap, `previous: {token_hash, until}`. The
executor's Coolify token can't read the stored value, so it starts from what the running
orchestrator loaded (its identities route) and changes only the entry its approved
destination names. Every other entry is written back as served, so a hand edit the orchestrator
has loaded stands; one it hasn't loaded yet is overwritten.

A setting the orchestrator refuses at boot takes production down, and the executor can't import the
orchestrator's parser, so `build` refuses everything that parser refuses before a byte is written:
a hash that isn't 64 lowercase hex characters, a `previous` hash equal to any entry's current hash,
and an `until` that isn't UTC or is more than eight days ahead. Key ids and agent ids are copied
from what was served, so `build` can't change them. `tests/api/test_m2m_identities_api.py` loads
what it builds through the orchestrator's own parser.

`Change.REPLACE` hosts with no overlap: the entry's new hash and no `previous`, so the old value
stops authenticating as this key id at the restart. It exists for one case, an old hash another
entry still holds as current (the observer and drift-reporter bearers, ADR-0055 amendment 9),
where boot refuses the overlap. `build` refuses it whenever an overlap is possible, so an approved
declaration can't become a way to skip one.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
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
    # Accept the new value alone, where the old is another entry's current hash.
    REPLACE = "replace"


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
    if entry not in identities:
        raise ContractError("the orchestrator has no entry with the approved key id")
    if not CHANGES[change](identities, entry, new_hash, old_hash, now):
        return Rewrite(None, None)
    _refuse_what_boot_refuses(identities, now)
    text = json.dumps(identities, separators=(",", ":"))
    return Rewrite(text, hashlib.sha256(text.encode()).hexdigest()[:8])


# Each change edits `identities` in place, and returns False when the served setting already says
# what it asks, so there is nothing to write.
type _Edit = Callable[[dict[str, dict[str, Any]], str, str, str, datetime], bool]


def _host(
    identities: dict[str, dict[str, Any]], entry: str, new_hash: str, old_hash: str, now: datetime
) -> bool:
    target = identities[entry]
    if target.get("token_hash") == new_hash and _previous_hash(target) == old_hash:
        return False
    if target.get("token_hash") != old_hash:
        raise ContractError("the entry holds neither the old hash nor the new one")
    target["token_hash"] = new_hash
    target["previous"] = {"token_hash": old_hash, "until": (now + OVERLAP).isoformat()}
    _drop_expired_elsewhere(identities, entry, now)
    return True


def _replace(
    identities: dict[str, dict[str, Any]], entry: str, new_hash: str, old_hash: str, now: datetime
) -> bool:
    target = identities[entry]
    # Checked first: a retry that writes nothing can't skip an overlap.
    if target.get("token_hash") == new_hash and "previous" not in target:
        return False
    if not any(
        identity.get("token_hash") == old_hash
        for key, identity in identities.items()
        if key != entry
    ):
        raise ContractError("no other entry holds the old hash, so the host must overlap")
    if target.get("token_hash") != old_hash:
        raise ContractError("the entry holds neither the old hash nor the new one")
    if "previous" in target and not _expired(target, now):
        # Writing no previous would end that overlap unrecorded: never guess.
        raise ContractError("the entry still accepts an earlier value as previous")
    target["token_hash"] = new_hash
    target.pop("previous", None)
    _drop_expired_elsewhere(identities, entry, now)
    return True


def _retire(
    identities: dict[str, dict[str, Any]], entry: str, new_hash: str, old_hash: str, now: datetime
) -> bool:
    target = identities[entry]
    if target.get("token_hash") != new_hash:
        raise ContractError("the entry doesn't hold the new hash")
    if target.get("previous") is None:
        return False
    if _previous_hash(target) != old_hash:
        raise ContractError("the entry's previous hash isn't the old value's")
    del target["previous"]
    return True


CHANGES: Final[dict[Change, _Edit]] = {
    Change.HOST: _host,
    Change.REPLACE: _replace,
    Change.RETIRE: _retire,
}


def _previous_hash(identity: Mapping[str, Any]) -> object:
    previous = identity.get("previous")
    return previous.get("token_hash") if isinstance(previous, Mapping) else None


def _drop_expired_elsewhere(
    identities: dict[str, dict[str, Any]], entry: str, now: datetime
) -> None:
    # An expired overlap authenticates nothing; rotating is a good time to drop it.
    for key, identity in identities.items():
        if key != entry and _expired(identity, now):
            del identity["previous"]


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
