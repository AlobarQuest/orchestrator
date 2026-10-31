"""An in-memory `SecretStore`, so the contract is tested against the protocol, not BWS."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from rotation_worker.secret_store import StoredSecret, StoreError
from rotation_worker.values import Value


class MemoryStore:
    """BWS's shape: secrets by UUID, and `bws-secret` destinations read back."""

    read_back_kind = "bws-secret"

    def __init__(self) -> None:
        self.secrets: dict[str, tuple[str, str, Value]] = {}
        self.deleted: list[str] = []
        self.revisions: dict[str, datetime] = {}

    def put(self, project_id: str, key: str, value: Value) -> str:
        secret_id = str(uuid.uuid4())
        self.secrets[secret_id] = (project_id, key, value)
        self.revisions[secret_id] = datetime.now(UTC)
        return secret_id

    def locate(self, consumer: Mapping[str, Any]) -> str | None:
        return str(consumer["uuid"]) if consumer.get("kind") == self.read_back_kind else None

    def read(self, secret_id: str) -> Value:
        if secret_id not in self.secrets:
            raise StoreError("no such secret")
        return self.secrets[secret_id][2]

    def read_key(self, secret_id: str) -> Value:
        return self.read(secret_id)

    def revised(self, secret_id: str) -> datetime:
        if secret_id not in self.secrets:
            raise StoreError("no such secret")
        return self.revisions[secret_id]

    def find(self, project_id: str, key: str) -> StoredSecret | None:
        found = [
            StoredSecret(secret_id, key, project_id)
            for secret_id, (project, name, _) in self.secrets.items()
            if project == project_id and name == key
        ]
        return found[0] if found else None

    def create(self, project_id: str, key: str, value: Value) -> StoredSecret:
        return StoredSecret(self.put(project_id, key, value), key, project_id)

    def write(self, secret_id: str, value: Value) -> None:
        project, key, _ = self.secrets[secret_id]
        self.secrets[secret_id] = (project, key, value)
        self.revisions[secret_id] = datetime.now(UTC)

    def delete(self, secret_id: str) -> None:
        del self.secrets[secret_id]
        self.deleted.append(secret_id)


def member_record(*granted: str, retired: Iterable[str] = ()) -> dict[str, Any]:
    """The member record as `GET /api/v1/members` serves it: each repository granted
    `credential_rotation`, on a live member unless it is also named in `retired`."""
    return {
        "members": [
            {
                "repository": repository,
                "state": "retired" if repository in retired else "member",
                "grants": {"credential_rotation": {"state": "granted"}},
            }
            for repository in granted
        ]
    }
