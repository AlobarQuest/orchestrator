"""A person's mint, taken from the inbox rather than clicked (ADR-0055 amendment 15, increment 3c).

Where a type's provider can't mint by machine, a person creates the new value in the provider's
console and pastes it into one BWS secret, `rotation-inbox`, the same for every credential and
every rotation. Each instance reads its own (the Keychain instance's sits in a project only its
narrow inbox identity reaches, so no BWS consumer token is ever where the VM can read it).

The unit after the person's mint waits, unclaimed, until a value is staged for its rotation. Each
pass that finds a value in the inbox binds it to that rotation, writing the staging secret and
then emptying the inbox, only when all of these hold:

* no other rotation waits on a person's mint, across the estate (`other_mints`), since nothing in
  a pasted value says which credential it was made for;
* it isn't the value it replaces, and the provider accepts it beside a refused known-bad control;
* it is for the same account as the value it replaces (`same_identity`), which must still answer:
  after an early revoke or an expiry, the value is staged by hand;
* it is no other keeper's value of the same type: two BWS machine tokens can reach exactly the
  same projects, so only this tells one pasted for the other.

A value that doesn't bind stays where it is and fails nothing; the person is told only once it
has failed on a later pass too (`Refusals`), so a pass that caught a paste half-done says nothing.
A staging secret written by hand (`rotation-worker stage`) serves as well as one bound here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from rotation_worker.adapters import ADAPTERS
from rotation_worker.checks import UNIT_KEY
from rotation_worker.contract import ContractError, Provider, Rotation, StepKind, accepts
from rotation_worker.envelope import ADAPTER_KEY, is_rotation_unit
from rotation_worker.envelope import constraints as envelope_constraints
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.plans import person_acts_beside
from rotation_worker.secret_store import SecretStore
from rotation_worker.values import Value, scrub

LOG = logging.getLogger("rotation_worker")
INBOX_KEY: Final = "rotation-inbox"
# What an empty inbox holds: BWS keeps no secret without a value.
EMPTY: Final = "empty"
# Where each instance's person pastes, by the kind of store the instance keeps.
INBOX_PROJECTS: Final = {"bws-secret": "Rotation / Inbox", "keychain": "Rotation / Inbox (Mac)"}
STATE_FILE: Final = "inbox-refused.json"


@runtime_checkable
class ConfirmsIdentity(Protocol):
    """A provider that can tell whether two values are for the same account and reach."""

    def same_identity(self, new: Value, old: Value) -> bool: ...


class Inbox:
    """The one BWS secret a person pastes a new value into, reached through `open_store` only
    when a unit waits on a mint: on the Keychain instance that is a BWS login of its own."""

    def __init__(self, open_store: Callable[[], SecretStore], secret_id: str) -> None:
        self._open_store = open_store
        self._store: SecretStore | None = None
        self._secret_id = secret_id

    def read(self) -> Value | None:
        """The pasted value, or `None` for an empty inbox. A console paste can end in a newline."""
        store = self._opened()
        try:
            value = store.read_key(self._secret_id)
        except ValueError:
            # A blank value: the person cleared it.
            return None
        return None if value.reveal() == EMPTY else value

    def empty(self) -> None:
        self._opened().write(self._secret_id, Value(EMPTY))

    def _opened(self) -> SecretStore:
        if self._store is None:
            self._store = self._open_store()
        return self._store


def person_mints_before(unit_name: str, adapter_name: str) -> bool:
    """Whether a unit of this adapter comes straight after a person's mint. Unknown either way
    counts as one, since it errs toward binding nothing."""
    adapter = ADAPTERS.get(adapter_name)
    if adapter is None:
        return True
    try:
        unit = adapter.plan.unit(unit_name)
    except ContractError:
        return True
    # A type's derived plan has the same units, in the same order, as `Adapter.plan`.
    return person_acts_beside(adapter.plan, unit, -1, StepKind.MINT)


def other_mints(client: OrchestratorClient, revision_id: str) -> list[str]:
    """The key of each listed unit of another rotation that waits on a person's mint: never
    claimed, and straight after one. Both instances' units count, and so do units whose
    decomposition holds the mint as a human act."""
    found = []
    for row in client.active_units():
        unit = client.unit(str(row["unit_id"]))
        if not is_rotation_unit(unit) or str(unit.get("work_package_revision_id")) == revision_id:
            continue
        constraints = envelope_constraints(unit)
        waiting = int(unit.get("attempt_count") or 0) == 0 and person_mints_before(
            str(constraints.get(UNIT_KEY)), str(constraints.get(ADAPTER_KEY))
        )
        if waiting:
            found.append(str(row["unit_key"]))
    return sorted(found)


def bind(
    new: Value,
    *,
    rotation: Rotation,
    store: SecretStore,
    provider: Provider,
    others_waiting: Sequence[str],
    other_keepers: Callable[[], Iterable[tuple[str, Value]]],
) -> str | None:
    """Stage `new` for `rotation`. Returns why it wasn't, or `None` once the staging secret holds
    it; emptying the inbox is the caller's."""
    if others_waiting:
        return (
            "another rotation waits on a person's mint too, so the inbox can't tell which "
            f"credential the value is for: {', '.join(others_waiting)}"
        )
    old = store.read(rotation.keeper_id)
    if new.same_as(old):
        return "it is the value the keeper already holds"
    if not accepts(provider, new):
        return "the provider doesn't accept it beside a refused known-bad value"
    if not isinstance(provider, ConfirmsIdentity):
        return "this type can't tell whose a value is, so nothing is taken from the inbox"
    if not provider.same_identity(new, old):
        return (
            "it isn't for the same account as the value it replaces, or that value no longer "
            "answers"
        )
    for credential, value in other_keepers():
        if new.same_as(value):
            return f"it is {credential}'s value"
    store.create(rotation.work_project_id, rotation.staging_key, new)
    return None


class Refusals:
    """The inbox value that last failed to bind, by fingerprint, so a person hears of a refusal
    only on a later pass's failure too. Losing the record only delays the alert by a pass."""

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / STATE_FILE

    def again(self, value: Value) -> bool:
        """Whether `value` failed on an earlier pass too; records that it failed on this one."""
        prefix = value.fingerprint.sha256_prefix
        try:
            before = json.loads(self._path.read_text()).get("refused")
        except OSError, ValueError, AttributeError:
            before = None
        if before == prefix:
            return True
        staged = self._path.with_suffix(".tmp")
        try:
            staged.write_text(json.dumps({"refused": prefix}))
            staged.replace(self._path)
        except OSError as error:
            LOG.warning("inbox_refusal_unrecorded", extra={"reason": scrub(str(error))})
        return False
