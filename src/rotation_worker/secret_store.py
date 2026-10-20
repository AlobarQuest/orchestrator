"""Where values are kept: BWS, reached through its SDK in-process, never the `bws` CLI.

ADR-0055 decision 3: values never enter a process argument list, and `bws secret edit` takes the
value as an argument. So the executor talks to BWS through `bitwarden-sdk` (one added dependency,
accepted for R1), logged in with the root token and no state file, so every session is a fresh
login (amendment 1: a cached session can outlive a revoked token).

The SDK is imported only when a `BwsStore` is built: it is an optional extra
(`rotation-worker`), so the orchestrator image and CI install it only where they need it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

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
    """The root's view of BWS: one organization, every call checked for success."""

    _sleep: Callable[[float], None] = staticmethod(time.sleep)

    def __init__(self, organization_id: str, access_token: Value) -> None:
        from bitwarden_sdk import BitwardenClient

        self._organization_id = organization_id
        # The SDK's defaults are bitwarden.com's API and identity servers.
        self._client: BitwardenClient = BitwardenClient()
        # No state file: every session is a fresh login.
        self._call(lambda: self._client.auth().login_access_token(access_token.reveal()))

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
        listed = self._call(lambda: self._client.secrets().list(self._organization_id))
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
        created = self._call(
            lambda: self._client.secrets().create(
                UUID(self._organization_id), key, value.reveal(), None, [UUID(project_id)]
            ),
            recover=lambda: self.find(project_id, key),
        )
        return StoredSecret(str(created.id), key, project_id)

    def write(self, secret_id: str, value: Value) -> None:
        # `update` replaces every field, so the key, note and project are read back first.
        current = self._call(lambda: self._client.secrets().get(secret_id))
        projects = [current.project_id] if current.project_id is not None else None
        self._call(
            lambda: self._client.secrets().update(
                self._organization_id,
                secret_id,
                current.key,
                value.reveal(),
                current.note,
                projects,
            )
        )

    def delete(self, secret_id: str) -> None:
        self._call(lambda: self._client.secrets().delete([secret_id]))

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
