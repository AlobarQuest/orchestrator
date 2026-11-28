"""A person's mint, taken from the inbox rather than clicked (ADR-0055 amendment 15, increment 3c).

Where a type's provider can't mint by machine, a person creates the new value in the provider's
console and pastes it into one BWS secret, `rotation-inbox`, the same for every credential and
every rotation. Each instance reads its own (the Keychain instance's sits in a project only its
narrow inbox identity reaches, so no BWS consumer token is ever where the VM can read it).

The unit after the person's mint waits, unclaimed, until a value is staged for its rotation. Each
pass that finds a value in the inbox binds it to that rotation, writing the staging secret and
then emptying the inbox, only when all of these hold:

* no other rotation waits on a person's mint, across the estate (`waiting_mints`), since nothing in
  most pasted values says which credential it was made for. A type whose value names its own
  credential (`NamesItsCredential`, ADR-0055 amendment 35 item 6 B: a GitHub App key answers
  `GET /app` with its App's id) binds while others wait, when the value names this rotation's
  declared identity and no waiting rotation declares the same one; a waiting rotation whose
  credential can't be told refuses it;
* it isn't the value it replaces, and the provider accepts it beside a refused known-bad control;
* it is for the same account as the value it replaces (`same_identity`), which must still answer:
  after an early revoke or an expiry, the value is staged by hand. A type that binds by what the
  value is instead (`BindsByItself`, ADR-0055 amendment 19 items 4 and 5) asks that in place of
  this check and the one before it: a lane token by its declared reach. A ping URL is never
  pasted: the executor reads it from the Healthchecks listing (amendment 23);
* it is no other keeper's value of the same type: two BWS machine tokens can reach exactly the
  same projects, so only this tells one pasted for the other.

A value that doesn't bind stays where it is and fails nothing; the person is told only once it
has failed on a later pass too (`Refusals`), so a pass that caught a paste half-done says nothing.
A staging secret written by hand (`rotation-worker stage`) serves as well as one bound here.
One that holds only the empty marker, or nothing, isn't staged (`pasted`): Bitwarden saves a new
secret before its value is pasted, so a pass can find it in that state. A value bound from the
inbox then goes into that secret, never beside it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from rotation_worker.adapters import ADAPTERS
from rotation_worker.checks import UNIT_KEY
from rotation_worker.contract import EMPTY, ContractError, Provider, Rotation, StepKind, accepts
from rotation_worker.envelope import ADAPTER_KEY, is_rotation_unit
from rotation_worker.envelope import constraints as envelope_constraints
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.plans import person_acts_beside
from rotation_worker.secret_store import SecretStore, read_as
from rotation_worker.values import Value, scrub

LOG = logging.getLogger("rotation_worker")
INBOX_KEY: Final = "rotation-inbox"
# Where each instance's person pastes, by the kind of store the instance keeps.
INBOX_PROJECTS: Final = {"bws-secret": "Rotation / Inbox", "keychain": "Rotation / Inbox (Mac)"}
STATE_FILE: Final = "inbox-refused.json"


@runtime_checkable
class ConfirmsIdentity(Protocol):
    """A provider that can tell whether two values are for the same account and reach."""

    def same_identity(self, new: Value, old: Value) -> bool: ...


def pasted(store: SecretStore, secret_id: str) -> Value | None:
    """The value a person pasted into a secret, or `None` while it holds the empty marker or no
    value at all. A console paste can end in a newline."""
    try:
        value = store.read_key(secret_id)
    except ValueError:
        # Blank, or padded where the store doesn't strip: the person cleared it or is mid-paste.
        return None
    return None if value.reveal() == EMPTY else value


@runtime_checkable
class BindsByItself(Protocol):
    """A provider whose pasted value binds by what it is, not by matching the value it replaces,
    which may be the empty marker or, after a leak, refused (ADR-0055 amendment 19)."""

    def bind_refusal(self, new: Value, old: Value) -> str | None:
        """Why `new` isn't this credential's value, or `None`. Never probes in a way that acts."""
        ...


@runtime_checkable
class NamesItsCredential(Protocol):
    """A provider whose value says which credential it is for, so the inbox can bind it while other
    rotations wait on a mint (ADR-0055 amendment 35 item 6 B). Every other check still applies."""

    @property
    def declared(self) -> str:
        """The identity this rotation's registry row declares."""
        ...

    def declared_by(self, credential: Mapping[str, Any]) -> str | None:
        """The identity another registry row declares, or `None` for a row of no such type.
        Raises `ContractError` for a row of its type whose identity can't be read."""
        ...

    def identity_of(self, new: Value) -> str | None:
        """The identity `new` answers for, or `None` when it answers for none."""
        ...


@dataclass(frozen=True)
class WaitingMint:
    """Another rotation's unit that waits on a person's mint."""

    unit_key: str
    revision_id: str


class Inbox:
    """The one BWS secret a person pastes a new value into, reached through `open_store` only
    when a unit waits on a mint: on the Keychain instance that is a BWS login of its own."""

    def __init__(self, open_store: Callable[[], SecretStore], secret_id: str) -> None:
        self._open_store = open_store
        self._store: SecretStore | None = None
        self._secret_id = secret_id

    def read(self) -> Value | None:
        """The pasted value, or `None` for an empty inbox."""
        return pasted(self._opened(), self._secret_id)

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


def waiting_mints(client: OrchestratorClient, revision_id: str) -> list[WaitingMint]:
    """Each listed unit of another rotation that waits on a person's mint, by unit key: never
    claimed, and straight after one. Both instances' units count, and so do units whose
    decomposition holds the mint as a human act."""
    found = []
    for row in client.active_units():
        unit = client.unit(str(row["unit_id"]))
        other_revision = str(unit.get("work_package_revision_id"))
        if not is_rotation_unit(unit) or other_revision == revision_id:
            continue
        constraints = envelope_constraints(unit)
        waiting = int(unit.get("attempt_count") or 0) == 0 and person_mints_before(
            str(constraints.get(UNIT_KEY)), str(constraints.get(ADAPTER_KEY))
        )
        if waiting:
            found.append(WaitingMint(str(row["unit_key"]), other_revision))
    return sorted(found, key=lambda mint: mint.unit_key)


def bind(
    new: Value,
    *,
    rotation: Rotation,
    store: SecretStore,
    provider: Provider,
    others_waiting: Sequence[str],
    other_keepers: Callable[[], Iterable[tuple[str, Value]]],
    staging_id: str | None = None,
    others_declare: Callable[[], Mapping[str, str | None]] | None = None,
) -> str | None:
    """Stage `new` for `rotation`. Returns why it wasn't, or `None` once the staging secret holds
    it; emptying the inbox is the caller's. `staging_id` names a staging secret that held no value
    when the caller looked: `new` is written into it. `others_declare` gives, by unit key, the
    identity each waiting rotation's row declares (`None` for a row of another type), for a
    provider that `NamesItsCredential`; a waiting rotation it omits can't be told, and refuses."""
    if others_waiting:
        refusal = _waiting_refusal(new, provider, others_waiting, others_declare)
        if refusal is not None:
            return refusal
    old = read_as(store, rotation.credential_class, rotation.keeper_id)
    if new.same_as(old):
        return "it is the value the keeper already holds"
    refusal = _identity_refusal(new, old, provider)
    if refusal is not None:
        return refusal
    for credential, value in other_keepers():
        if new.same_as(value):
            return f"it is {credential}'s value"
    return _stage(new, rotation, store, staging_id)


def _identity_refusal(new: Value, old: Value, provider: Provider) -> str | None:
    """Why `new` isn't the value that replaces `old`, by what the provider can tell, or `None`."""
    if isinstance(provider, BindsByItself):
        return provider.bind_refusal(new, old)
    if not accepts(provider, new):
        return "the provider doesn't accept it beside a refused known-bad value"
    if not isinstance(provider, ConfirmsIdentity):
        return "this type can't tell whose a value is, so nothing is taken from the inbox"
    if not provider.same_identity(new, old):
        return (
            "it isn't for the same account as the value it replaces, or that value no longer "
            "answers"
        )
    return None


def _waiting_refusal(
    new: Value,
    provider: Provider,
    others_waiting: Sequence[str],
    others_declare: Callable[[], Mapping[str, str | None]] | None,
) -> str | None:
    """Why other rotations waiting on a mint keep `new` from binding, or `None` when it names this
    rotation and no waiting one declares the same identity."""
    unknown = (
        "another rotation waits on a person's mint too, so the inbox can't tell which "
        f"credential the value is for: {', '.join(others_waiting)}"
    )
    if not isinstance(provider, NamesItsCredential) or others_declare is None:
        return unknown
    if provider.identity_of(new) != provider.declared:
        return f"{unknown}; it doesn't name this rotation's own"
    declared = others_declare()
    untold = sorted(key for key in others_waiting if key not in declared)
    if untold:
        return f"{unknown}; what {', '.join(untold)} waits for can't be told"
    clashing = sorted(key for key in others_waiting if declared.get(key) == provider.declared)
    if clashing:
        return (
            f"another waiting rotation declares the same identity ({provider.declared}), so the "
            f"inbox can't tell which it is for: {', '.join(clashing)}"
        )
    return None


def _stage(
    new: Value, rotation: Rotation, store: SecretStore, staging_id: str | None
) -> str | None:
    if staging_id is None:
        store.create(rotation.work_project_id, rotation.staging_key, new)
        return None
    if pasted(store, staging_id) is not None:
        # Asked again after the checks, which take seconds, to keep a value pasted meanwhile;
        # one pasted between this read and the write is still overwritten.
        return "a value was staged for this rotation while the inbox's was checked"
    store.write(staging_id, new)
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
