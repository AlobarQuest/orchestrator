import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BUNDLE_SCHEMA = "orchestrator-actor-bundle/v1"
ACTOR_FIELDS = frozenset({"agent_id", "version", "status", "runtime", "authority_profile"})
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
IDENTIFIER_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")


class RegistryValidationError(ValueError):
    """The registry cannot safely resolve the requested identity."""


@dataclass(frozen=True)
class RegistryActor:
    agent_id: str
    version: int
    status: str
    runtime: str
    authority_profile: str


class RegistryAdapter:
    def __init__(self, bundle: Mapping[str, Any]) -> None:
        self.source_revision, self._actors = _validate_bundle(bundle)

    @classmethod
    def from_path(cls, path: Path) -> "RegistryAdapter":
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RegistryValidationError("registry bundle is unreadable") from error
        if not isinstance(value, Mapping):
            raise RegistryValidationError("registry bundle must be an object")
        return cls(value)

    def resolve(self, agent_id: str) -> RegistryActor:
        actor = self._actors.get(agent_id)
        if actor is None:
            raise RegistryValidationError("unknown registry identity")
        if actor.status != "active":
            raise RegistryValidationError("registry identity is not active")
        return actor


def _validate_bundle(
    bundle: Mapping[str, Any],
) -> tuple[str, dict[str, RegistryActor]]:
    if set(bundle) != {"schema", "source_revision", "actors"}:
        raise RegistryValidationError("registry bundle has unknown or missing fields")
    if bundle["schema"] != BUNDLE_SCHEMA:
        raise RegistryValidationError("unsupported registry bundle schema")
    revision = bundle["source_revision"]
    if not isinstance(revision, str) or REVISION_PATTERN.fullmatch(revision) is None:
        raise RegistryValidationError("invalid registry source revision")
    actor_values = bundle["actors"]
    if not isinstance(actor_values, list):
        raise RegistryValidationError("registry actors must be a list")

    actors: dict[str, RegistryActor] = {}
    for value in actor_values:
        actor = validate_registry_actor(value)
        if actor.agent_id in actors:
            raise RegistryValidationError("duplicate registry identity")
        actors[actor.agent_id] = actor
    return revision, actors


def validate_registry_actor(value: object) -> RegistryActor:
    if not isinstance(value, Mapping) or set(value) != ACTOR_FIELDS:
        raise RegistryValidationError("registry identity has unknown or missing fields")
    agent_id = _required_identifier(value["agent_id"], "agent ID")
    authority_profile = _required_identifier(value["authority_profile"], "authority profile")
    runtime = _required_identifier(value["runtime"], "runtime")
    status = value["status"]
    if status not in {"active", "reserved", "retired"}:
        raise RegistryValidationError("invalid registry identity status")
    version = value["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise RegistryValidationError("invalid registry identity version")
    return RegistryActor(
        agent_id=agent_id,
        version=version,
        status=status,
        runtime=runtime,
        authority_profile=authority_profile,
    )


def _required_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise RegistryValidationError(f"invalid {name}")
    return value
