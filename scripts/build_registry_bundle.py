#!/usr/bin/env python3
import argparse
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from orchestrator.identity.registry import (
    BUNDLE_SCHEMA,
    RegistryValidationError,
    validate_registry_actor,
)

SOURCE_FIELDS = frozenset(
    {
        "schema",
        "agent_id",
        "version",
        "status",
        "runtime",
        "operator",
        "environment",
        "description",
        "authority_profile",
        "capabilities",
        "prohibited",
    }
)


def build_bundle(registry_dir: Path, source_revision: str) -> dict[str, Any]:
    _validate_checkout(registry_dir, source_revision)
    actors: list[dict[str, Any]] = []
    seen: set[str] = set()
    paths = sorted((registry_dir / "agents").glob("*.yaml"))
    if not paths:
        raise RegistryValidationError("registry contains no identities")
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RegistryValidationError(f"registry identity is not a regular file: {path.name}")
        source = _load_identity(path)
        actor_value = {
            name: source[name]
            for name in ("agent_id", "version", "status", "runtime", "authority_profile")
        }
        actor = validate_registry_actor(actor_value)
        if actor.agent_id in seen:
            raise RegistryValidationError("duplicate registry identity")
        seen.add(actor.agent_id)
        actors.append(actor_value)
    actors.sort(key=lambda value: value["agent_id"])
    return {
        "schema": BUNDLE_SCHEMA,
        "source_revision": source_revision,
        "actors": actors,
    }


def write_bundle(output: Path, bundle: Mapping[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, indent=2, sort_keys=False) + "\n")


def _validate_checkout(registry_dir: Path, source_revision: str) -> None:
    try:
        head = _git(registry_dir, "rev-parse", "HEAD").strip()
        dirty = _git(registry_dir, "status", "--porcelain")
    except (OSError, subprocess.CalledProcessError) as error:
        raise RegistryValidationError("registry directory is not a git checkout") from error
    if head != source_revision:
        raise RegistryValidationError("registry checkout does not match source revision")
    if dirty:
        raise RegistryValidationError("registry checkout is dirty")


def _git(registry_dir: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(registry_dir), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    )


def _load_identity(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise RegistryValidationError(f"invalid registry identity: {path.name}") from error
    if not isinstance(value, Mapping) or set(value) != SOURCE_FIELDS:
        message = f"registry identity has unknown or missing fields: {path.name}"
        raise RegistryValidationError(message)
    if value["schema"] != "agent-identity/v1":
        raise RegistryValidationError(f"unsupported registry identity schema: {path.name}")
    if not isinstance(value["capabilities"], list) or not isinstance(value["prohibited"], list):
        raise RegistryValidationError(f"invalid registry identity lists: {path.name}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-dir", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    bundle = build_bundle(arguments.registry_dir, arguments.source_revision)
    write_bundle(arguments.output, bundle)


if __name__ == "__main__":
    main()
