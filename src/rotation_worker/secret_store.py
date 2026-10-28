"""Where values are kept: BWS for the VM instance, the operator machine's Keychain for the other.

BWS is reached through its SDK in-process, never the `bws` CLI.

ADR-0055 decision 3: values never enter a process argument list, and `bws secret edit` takes the
value as an argument. So the executor talks to BWS through `bitwarden-sdk` (one added dependency,
accepted for R1), logged in with the root token and no state file, so every session is a fresh
login (amendment 1: a cached session can outlive a revoked token).

The SDK is imported only when a `BwsStore` is built: it is an optional extra
(`rotation-worker`), so the orchestrator image and CI install it only where they need it.

The Keychain instance (ADR-0055 amendment 12) rotates BWS machine tokens, which live in the
operator machine's login Keychain, and keeps its own state there too. `/usr/bin/security` takes a
value only as an argument, so a value is always written through `security -i`, which reads the
command from stdin, and read back from stdout: no value is ever in a process argument list.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import UUID

from rotation_worker.destinations import parse, render
from rotation_worker.values import Value, scrub

if TYPE_CHECKING:
    from bitwarden_sdk import BitwardenClient

# BWS answers a burst with 429 ("Slow down! ... Try again in 1m") and sometimes a healthy request
# with a 500. Either one failing a step would spend an attempt, and after a host without an
# overlap it leaves every reader refused until someone undoes it. So a 429 or 5xx is retried, for
# about three minutes in all, well inside a rotation unit's lease. Any other refusal is final.
TRANSIENT = re.compile(r"\[(?:429|5\d\d) ")
RETRY_DELAYS: tuple[float, ...] = (5.0, 20.0, 60.0, 90.0)


class StoreError(RuntimeError):
    """BWS refused or could not be reached. The message never carries a value."""


@dataclass(frozen=True)
class StoredSecret:
    id: str
    key: str
    project_id: str | None


class SecretStore(Protocol):
    # The destination kind this store reads back: a consumer of that kind is verified by reading
    # it, and is written by distribute (`contract.py`).
    read_back_kind: str

    def locate(self, consumer: Mapping[str, Any]) -> str | None:
        """The secret id of a parsed destination this store reads back, or `None`."""
        ...

    def read(self, secret_id: str) -> Value: ...

    def read_key(self, secret_id: str) -> Value:
        """A multi-line private key, which a console paste stores with a trailing newline."""
        ...

    def revised(self, secret_id: str) -> datetime:
        """When the secret last changed: for a keeper, when the store step wrote it."""
        ...

    def find(self, project_id: str, key: str) -> StoredSecret | None: ...

    def create(self, project_id: str, key: str, value: Value) -> StoredSecret: ...

    def write(self, secret_id: str, value: Value) -> None: ...

    def delete(self, secret_id: str) -> None: ...


class BwsStore:
    """One BWS identity's view: the root's, of one organization, or the Keychain instance's
    inbox identity's, which names no organization and only reads and writes one secret by id.
    Every call is checked for success."""

    _sleep: Callable[[float], None] = staticmethod(time.sleep)
    read_back_kind = "bws-secret"

    def __init__(self, organization_id: str | None, access_token: Value) -> None:
        from bitwarden_sdk import BitwardenClient

        self._organization_id = organization_id
        # The SDK's defaults are bitwarden.com's API and identity servers.
        self._client: BitwardenClient = BitwardenClient()
        # No state file: every session is a fresh login.
        self._call(lambda: self._client.auth().login_access_token(access_token.reveal()))

    def locate(self, consumer: Mapping[str, Any]) -> str | None:
        return str(consumer["uuid"]) if consumer.get("kind") == self.read_back_kind else None

    def read(self, secret_id: str) -> Value:
        secret = self._call(lambda: self._client.secrets().get(secret_id))
        return Value(secret.value)

    def read_key(self, secret_id: str) -> Value:
        secret = self._call(lambda: self._client.secrets().get(secret_id))
        return Value(str(secret.value).strip())

    def revised(self, secret_id: str) -> datetime:
        revised = self._call(lambda: self._client.secrets().get(secret_id)).revision_date
        if not isinstance(revised, datetime) or revised.utcoffset() is None:
            raise StoreError("BWS gave a revision date without a zone")
        return revised

    def find(self, project_id: str, key: str) -> StoredSecret | None:
        organization = self._organization()
        listed = self._call(lambda: self._client.secrets().list(organization))
        matches = [
            entry
            for entry in listed.data
            if entry.key == key and project_id in {str(p) for p in entry.project_ids}
        ]
        if len(matches) > 1:
            raise StoreError(f"{len(matches)} secrets named {key!r} in one project")
        if not matches:
            return None
        return StoredSecret(str(matches[0].id), key, project_id)

    def create(self, project_id: str, key: str, value: Value) -> StoredSecret:
        # A 5xx can come after BWS created the secret, so a retry looks for it first: creating it
        # twice would leave two secrets with one name, which `find` refuses from then on.
        organization = UUID(self._organization())
        created = self._call(
            lambda: self._client.secrets().create(
                organization, key, value.reveal(), None, [UUID(project_id)]
            ),
            recover=lambda: self.find(project_id, key),
        )
        return StoredSecret(str(created.id), key, project_id)

    def write(self, secret_id: str, value: Value) -> None:
        # `update` replaces every field, so the key, note, project and organization are read
        # back first.
        current = self._call(lambda: self._client.secrets().get(secret_id))
        projects = [current.project_id] if current.project_id is not None else None
        self._call(
            lambda: self._client.secrets().update(
                str(current.organization_id),
                secret_id,
                current.key,
                value.reveal(),
                current.note,
                projects,
            )
        )

    def delete(self, secret_id: str) -> None:
        # As with `create`: a 5xx can come after BWS deleted it, and deleting it again is refused.
        self._call(
            lambda: self._client.secrets().delete([secret_id]),
            recover=lambda: None if self._listed(secret_id) else secret_id,
        )

    def _listed(self, secret_id: str) -> bool:
        organization = self._organization()
        listed = self._call(lambda: self._client.secrets().list(organization))
        return any(str(entry.id) == secret_id for entry in listed.data)

    def _organization(self) -> str:
        if self._organization_id is None:
            raise StoreError("this BWS identity names no organization to list or create in")
        return self._organization_id

    def _call(self, request: Any, *, recover: Callable[[], Any] | None = None) -> Any:
        """`request`'s data, retrying a transient refusal. Before each retry, `recover` may
        return what the failed request already did."""
        for delay in RETRY_DELAYS:
            try:
                return self._once(request)
            except StoreError as error:
                if TRANSIENT.search(str(error)) is None:
                    raise
            self._sleep(delay)
            if recover is not None and (found := recover()) is not None:
                return found
        return self._once(request)

    def _once(self, request: Any) -> Any:
        try:
            response = request()
        except Exception as error:
            # The SDK raises a bare Exception carrying the server's message.
            raise StoreError(f"BWS refused: {scrub(str(error))}") from None
        if not getattr(response, "success", False) or response.data is None:
            message = getattr(response, "error_message", None) or "no data"
            raise StoreError(f"BWS refused: {scrub(str(message))}")
        return response.data


SECURITY: Final = "/usr/bin/security"
SECURITY_TIMEOUT_SECONDS: Final = 30.0
# `security` exits 44 when no item matches.
NOT_FOUND: Final = 44
# The characters a value may hold to be written inside `security -i`'s double quotes. A BWS
# access token and a urlsafe bearer use only these; anything else is refused, never escaped.
WRITABLE: Final = re.compile(r"^[A-Za-z0-9._:+/=-]+$")
MODIFIED: Final = re.compile(r'"mdat"<timedate>=0x[0-9A-F]+\s+"(\d{14})Z')


class KeychainStore:
    """The operator machine's login Keychain, through `/usr/bin/security` (amendment 12).

    A secret id is the item's destination string, `keychain service="…" account="…"`, so the
    keeper and every consumer are named exactly as the registry and the package name them. A
    "project" is a Keychain service and a key is an account. Items are updated in place and
    never re-created, so a reader never finds one missing, and an update never restates the
    item's access list: macOS asks a person to allow that (measured 2026-10-22), and nobody is
    watching the executor.
    """

    read_back_kind = "keychain"

    def __init__(self, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self._run = run

    def locate(self, consumer: Mapping[str, Any]) -> str | None:
        if consumer.get("kind") != self.read_back_kind:
            return None
        return self._id(str(consumer["service"]), str(consumer["account"]))

    def read(self, secret_id: str) -> Value:
        service, account = self._item(secret_id)
        found = self._security(["find-generic-password", "-s", service, "-a", account, "-w"])
        if found.returncode != 0:
            raise StoreError(f"the Keychain has no readable item {secret_id} ({found.returncode})")
        # `-w` ends the value with one newline.
        return Value(found.stdout.removesuffix("\n"))

    def read_key(self, secret_id: str) -> Value:
        return self.read(secret_id)

    def revised(self, secret_id: str) -> datetime:
        service, account = self._item(secret_id)
        found = self._security(["find-generic-password", "-s", service, "-a", account])
        stamp = MODIFIED.search(found.stdout) if found.returncode == 0 else None
        if stamp is None:
            raise StoreError(f"the Keychain gave no modification date for {secret_id}")
        return datetime.strptime(stamp.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)

    def find(self, project_id: str, key: str) -> StoredSecret | None:
        found = self._security(["find-generic-password", "-s", project_id, "-a", key])
        if found.returncode == NOT_FOUND:
            return None
        if found.returncode != 0:
            raise StoreError(f"the Keychain couldn't be searched ({found.returncode})")
        return StoredSecret(self._id(project_id, key), key, project_id)

    def create(self, project_id: str, key: str, value: Value) -> StoredSecret:
        if self.find(project_id, key) is not None:
            raise StoreError(f"{self._id(project_id, key)} already exists")
        # Created with the one application it trusts, which is how every item here was made.
        self._write(project_id, key, value, f"-T {SECURITY}")
        return StoredSecret(self._id(project_id, key), key, project_id)

    def write(self, secret_id: str, value: Value) -> None:
        service, account = self._item(secret_id)
        if self.find(service, account) is None:
            raise StoreError(f"the Keychain has no item {secret_id} to update")
        self._write(service, account, value, "-U")

    def delete(self, secret_id: str) -> None:
        service, account = self._item(secret_id)
        deleted = self._security(["delete-generic-password", "-s", service, "-a", account])
        if deleted.returncode not in (0, NOT_FOUND):
            raise StoreError(f"the Keychain didn't delete {secret_id} ({deleted.returncode})")

    def _write(self, service: str, account: str, value: Value, option: str) -> None:
        for name in (service, account):
            if not WRITABLE.fullmatch(name):
                raise StoreError("a Keychain service or account holds a character it may not")
        if not WRITABLE.fullmatch(value.reveal()):
            raise StoreError("the value holds a character a Keychain write may not carry")
        command = (
            f'add-generic-password {option} -s "{service}" -a "{account}" -w "{value.reveal()}"\n'
        )
        written = self._security(["-i"], stdin=command)
        if written.returncode != 0:
            # `security -i` exits non-zero on a refused command (measured 2026-10-22). Its output
            # is never shown: it may echo the command, value included.
            raise StoreError(f"the Keychain refused a write to {self._id(service, account)}")

    def _security(
        self, arguments: list[str], stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            return self._run(
                [SECURITY, *arguments],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=SECURITY_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # A prompt waiting for a person, most likely: nobody is there to answer it.
            raise StoreError("the Keychain didn't answer: it may be asking a person") from None

    @staticmethod
    def _id(service: str, account: str) -> str:
        return render({"kind": "keychain", "service": service, "account": account})

    @staticmethod
    def _item(secret_id: str) -> tuple[str, str]:
        consumer = parse(secret_id)
        if consumer["kind"] != "keychain":
            raise StoreError(f"{secret_id} isn't a Keychain item")
        return str(consumer["service"]), str(consumer["account"])
