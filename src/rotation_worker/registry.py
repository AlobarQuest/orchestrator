"""The credential registry and the package that carries a credential's approved destinations.

The registry is the `.cred-consumers.toml` files infraops' security drift reads, each in the
repository that owns its credentials (`REGISTRY_REPOSITORIES`). A credential id is unique across
all of them. Both are read at `main` of their repositories through `RepoMirror`, never from a
working copy, so the executor and the operator's comparison see the same thing.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import yaml

from rotation_worker.destinations import (
    DestinationError,
    credential_destinations,
    parse,
    render,
)
from rotation_worker.git_mirror import RepoMirror

REGISTRY_REPOSITORIES: Final = (
    "AlobarQuest/infraops-mcp-server",
    "AlobarQuest/orchestrator",
    "AlobarQuest/change-manager",
    "AlobarQuest/factory-runner",
)
REGISTRY_PATH: Final = ".cred-consumers.toml"
PACKAGES_REPOSITORY: Final = "AlobarQuest/intent-packages"


class RegistryError(RuntimeError):
    """The registry or the package can't give a single, well-formed answer."""


@dataclass(frozen=True)
class RegistryEntry:
    repository: str
    commit: str
    credential: Mapping[str, Any]

    @property
    def destinations(self) -> list[str]:
        return credential_destinations(self.credential)

    @property
    def rotated_by_sds(self) -> bool:
        return self.credential.get("rotated_by_sds") is True

    @property
    def keeper(self) -> str:
        """The keeper's destination string, which every rotation step reads the value from.

        A BWS secret (`bws_uuid`), or for a machine token, which has no BWS copy of itself, one
        of its Keychain items (`keeper_service` with `keeper_account`, ADR-0055 amendment 12).
        Exactly one of the two.
        """
        uuid = self.credential.get("bws_uuid")
        service = self.credential.get("keeper_service")
        account = self.credential.get("keeper_account")
        if uuid is not None and service is None and account is None and isinstance(uuid, str):
            return render({"kind": "bws-secret", "uuid": uuid})
        if uuid is None and isinstance(service, str) and isinstance(account, str):
            return render({"kind": "keychain", "service": service, "account": account})
        raise RegistryError(
            "a credential's keeper is bws_uuid, or keeper_service with keeper_account"
        )


@dataclass(frozen=True)
class PackageDestinations:
    commit: str
    path: str
    destinations: list[str]


_STANDING_PACKAGE_ID: Final = re.compile(r"^rotation-([a-z0-9][a-z0-9-]*)$")


def package_path(credential_id: str) -> str:
    return f"packages/rotation-{credential_id}/package.yaml"


def standing_credential(package_id: str) -> str | None:
    """The credential whose standing package `package_id` names, or None if it names none."""
    named = _STANDING_PACKAGE_ID.fullmatch(package_id)
    return named.group(1) if named else None


def lineage_path(credential_id: str) -> str:
    return f"packages/rotation-{credential_id}/lineage.yaml"


def find_credential(mirror: RepoMirror, credential_id: str) -> RegistryEntry:
    """The one registry entry with this id, read at each registry repository's `main`."""
    found = [entry for entry in _entries(mirror) if entry.credential.get("id") == credential_id]
    if len(found) != 1:
        raise RegistryError(f"{credential_id}: {len(found)} registry entries, expected exactly 1")
    return found[0]


def provider_entries(mirror: RepoMirror, provider: str) -> list[RegistryEntry]:
    """Every registry entry whose credential `provider` issues, read at each repository's `main`."""
    return [entry for entry in _entries(mirror) if entry.credential.get("provider") == provider]


def _entries(mirror: RepoMirror) -> list[RegistryEntry]:
    found: list[RegistryEntry] = []
    for repository in REGISTRY_REPOSITORIES:
        commit = mirror.sync(repository)
        try:
            document = tomllib.loads(mirror.show(repository, commit, REGISTRY_PATH).decode())
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise RegistryError(f"{repository}: {REGISTRY_PATH} doesn't parse") from error
        credentials = document.get("credential", [])
        if not isinstance(credentials, list) or not all(isinstance(c, dict) for c in credentials):
            raise RegistryError(
                f"{repository}: {REGISTRY_PATH} credentials aren't an array of tables"
            )
        found.extend(RegistryEntry(repository, commit, credential) for credential in credentials)
    return found


def latest_package_destinations(mirror: RepoMirror, credential_id: str) -> PackageDestinations:
    """`profile_fields.destinations` of the standing package at intent-packages' `main`."""
    commit = mirror.sync(PACKAGES_REPOSITORY)
    path = package_path(credential_id)
    fields = load_package(mirror.show(PACKAGES_REPOSITORY, commit, path)).get("profile_fields")
    return PackageDestinations(commit, path, standing_destinations(fields, credential_id, path))


def standing_destinations(fields: object, credential_id: str, where: str) -> list[str]:
    """A standing package's destination list, each string checked against the format."""
    if not isinstance(fields, dict) or fields.get("credential_id") != credential_id:
        raise RegistryError(f"{where}: profile_fields.credential_id isn't {credential_id!r}")
    if fields.get("standing") is not True:
        raise RegistryError(f"{where}: profile_fields.standing isn't true")
    destinations = fields.get("destinations")
    if destinations is None:
        raise RegistryError(f"{where}: profile_fields has no destinations yet")
    if not isinstance(destinations, list) or not all(isinstance(d, str) for d in destinations):
        raise RegistryError(f"{where}: profile_fields.destinations isn't a list of strings")
    for destination in destinations:
        try:
            parse(destination)
        except DestinationError as error:
            raise RegistryError(f"{where}: {error}") from error
    return destinations


def load_package(text: bytes) -> dict[str, Any]:
    try:
        package = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise RegistryError("package.yaml doesn't parse") from error
    if not isinstance(package, dict):
        raise RegistryError("package.yaml isn't a mapping")
    return package
