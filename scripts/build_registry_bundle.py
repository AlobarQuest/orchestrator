#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from orchestrator.identity.registry import (
    BUNDLE_SCHEMA,
    REVISION_PATTERN,
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
    repository, actor_tree = _validate_checkout(registry_dir, source_revision)
    actors: list[dict[str, Any]] = []
    seen: set[str] = set()
    entries = _actor_entries(repository, source_revision, actor_tree)
    if not entries:
        raise RegistryValidationError("registry contains no identities")
    for name, content in entries:
        source = _load_identity(name, content)
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


def artifact_digest(artifact_dir: Path) -> str:
    revision, entries = _artifact_content(artifact_dir)
    return _content_digest(revision, entries)


def _content_digest(revision: bytes, entries: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for name, content in [("SOURCE_REVISION", revision), *entries]:
        encoded_name = name.encode()
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def build_bundle_from_artifact(
    artifact_dir: Path,
    source_revision: str,
    artifact_sha256: str,
) -> dict[str, Any]:
    revision_content, raw_entries = _artifact_content(artifact_dir)
    actual_digest = _content_digest(revision_content, raw_entries)
    if (
        len(artifact_sha256) != 64
        or any(character not in "0123456789abcdef" for character in artifact_sha256)
        or not hmac.compare_digest(actual_digest, artifact_sha256)
    ):
        raise RegistryValidationError("registry artifact digest does not match")
    try:
        recorded_revision = revision_content.decode("utf-8").strip()
        entries = [(name, content.decode("utf-8")) for name, content in raw_entries]
    except UnicodeDecodeError as error:
        raise RegistryValidationError("registry artifact contains invalid text") from error
    if REVISION_PATTERN.fullmatch(source_revision) is None or recorded_revision != source_revision:
        raise RegistryValidationError("registry artifact does not match source revision")
    return _bundle_from_entries(entries, source_revision)


def _artifact_content(artifact_dir: Path) -> tuple[bytes, list[tuple[str, bytes]]]:
    revision_path = artifact_dir / "SOURCE_REVISION"
    agent_dir = artifact_dir / "agents"
    if artifact_dir.is_symlink() or revision_path.is_symlink() or agent_dir.is_symlink():
        raise RegistryValidationError("registry artifact must not contain a symlink")
    try:
        revision_content = revision_path.read_bytes()
    except OSError as error:
        raise RegistryValidationError("registry artifact has no source revision") from error
    entries: list[tuple[str, bytes]] = []
    try:
        paths = sorted(agent_dir.iterdir())
    except OSError as error:
        raise RegistryValidationError("registry contains no identities") from error
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.suffix != ".yaml":
            raise RegistryValidationError(f"invalid registry identity artifact: {path.name}")
        entries.append((f"agents/{path.name}", path.read_bytes()))
    return revision_content, entries


def _bundle_from_entries(entries: list[tuple[str, str]], source_revision: str) -> dict[str, Any]:
    if not entries:
        raise RegistryValidationError("registry contains no identities")
    actors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, content in entries:
        source = _load_identity(name, content)
        actor_value = {
            field: source[field]
            for field in ("agent_id", "version", "status", "runtime", "authority_profile")
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


def _validate_checkout(registry_dir: Path, source_revision: str) -> tuple[Path, str]:
    if registry_dir.is_symlink() or (registry_dir / "agents").is_symlink():
        raise RegistryValidationError("registry path must not contain a symlink")
    try:
        head = _git(registry_dir, "rev-parse", "HEAD").strip()
        dirty = _git(registry_dir, "status", "--porcelain")
        repository = Path(_git(registry_dir, "rev-parse", "--show-toplevel").strip())
        relative_registry = registry_dir.absolute().relative_to(repository.absolute())
    except (OSError, subprocess.CalledProcessError) as error:
        raise RegistryValidationError("registry directory is not a git checkout") from error
    except ValueError as error:
        raise RegistryValidationError("registry directory is outside its git checkout") from error
    if head != source_revision:
        raise RegistryValidationError("registry checkout does not match source revision")
    if dirty:
        raise RegistryValidationError("registry checkout is dirty")
    registry_prefix = "" if str(relative_registry) == "." else f"{relative_registry.as_posix()}/"
    return repository, f"{registry_prefix}agents"


def _git(registry_dir: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(registry_dir), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    )


def _actor_entries(repository: Path, revision: str, actor_tree: str) -> list[tuple[str, str]]:
    try:
        tree = subprocess.check_output(
            ["git", "-C", str(repository), "ls-tree", "-z", f"{revision}:{actor_tree}"],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RegistryValidationError("registry contains no identities") from error

    entries: list[tuple[str, str]] = []
    for raw_entry in tree.split(b"\0"):
        if not raw_entry:
            continue
        try:
            metadata, raw_name = raw_entry.split(b"\t", 1)
            mode, object_type, _object_id = metadata.split(b" ", 2)
            name = raw_name.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise RegistryValidationError("invalid registry tree entry") from error
        if mode not in {b"100644", b"100755"} or object_type != b"blob":
            raise RegistryValidationError(f"registry identity is not a regular file: {name}")
        if not name.endswith(".yaml") or "/" in name:
            raise RegistryValidationError(f"invalid registry identity filename: {name}")
        try:
            content = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(repository),
                    "cat-file",
                    "blob",
                    f"{revision}:{actor_tree}/{name}",
                ],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8")
        except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as error:
            raise RegistryValidationError(f"invalid registry identity blob: {name}") from error
        entries.append((name, content))
    return entries


def _load_identity(name: str, content: str) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise RegistryValidationError(f"invalid registry identity: {name}") from error
    if not isinstance(value, Mapping) or set(value) != SOURCE_FIELDS:
        message = f"registry identity has unknown or missing fields: {name}"
        raise RegistryValidationError(message)
    if value["schema"] != "agent-identity/v1":
        raise RegistryValidationError(f"unsupported registry identity schema: {name}")
    if not isinstance(value["capabilities"], list) or not isinstance(value["prohibited"], list):
        raise RegistryValidationError(f"invalid registry identity lists: {name}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--registry-dir", type=Path)
    source.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.artifact_dir is not None:
        if arguments.artifact_sha256 is None:
            parser.error("--artifact-sha256 is required with --artifact-dir")
        bundle = build_bundle_from_artifact(
            arguments.artifact_dir,
            arguments.source_revision,
            arguments.artifact_sha256,
        )
    else:
        bundle = build_bundle(arguments.registry_dir, arguments.source_revision)
    write_bundle(arguments.output, bundle)


if __name__ == "__main__":
    main()
