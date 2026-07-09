import json
import subprocess
from pathlib import Path

import pytest

from orchestrator.identity.registry import RegistryAdapter, RegistryValidationError
from scripts.build_registry_bundle import (
    artifact_digest,
    build_bundle,
    build_bundle_from_artifact,
    write_bundle,
)

REVISION = "0123456789abcdef0123456789abcdef01234567"


def actor(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "agent_id": "worker",
        "version": 1,
        "status": "active",
        "runtime": "runner",
        "authority_profile": "agent-queue-v1",
    }
    value.update(overrides)
    return value


def bundle(*actors: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "orchestrator-actor-bundle/v1",
        "source_revision": REVISION,
        "actors": list(actors),
    }


def write_identity(registry_dir: Path, name: str, content: str) -> None:
    agents = registry_dir / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / name).write_text(content)


def commit_registry(registry_dir: Path) -> str:
    subprocess.run(["git", "init", "-q", registry_dir], check=True)
    subprocess.run(["git", "-C", registry_dir, "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            registry_dir,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", registry_dir, "rev-parse", "HEAD"], text=True
    ).strip()


def test_bundle_generation_is_sorted_versioned_and_credential_free(tmp_path: Path) -> None:
    registry_dir = tmp_path / "registry"
    write_identity(
        registry_dir,
        "zeta.yaml",
        """schema: agent-identity/v1
agent_id: zeta
version: 2
status: active
runtime: runner
operator: devon
environment: mini
description: fixture
authority_profile: agent-queue-v1
capabilities: []
prohibited: []
""",
    )
    write_identity(
        registry_dir,
        "devon.yaml",
        """schema: agent-identity/v1
agent_id: devon
version: 1
status: active
runtime: human
operator: devon
environment: any
description: fixture
authority_profile: human-operator-v1
capabilities: []
prohibited: []
""",
    )
    revision = commit_registry(registry_dir)

    generated = build_bundle(registry_dir, revision)

    assert generated == {
        "schema": "orchestrator-actor-bundle/v1",
        "source_revision": revision,
        "actors": [
            {
                "agent_id": "devon",
                "version": 1,
                "status": "active",
                "runtime": "human",
                "authority_profile": "human-operator-v1",
            },
            {
                "agent_id": "zeta",
                "version": 2,
                "status": "active",
                "runtime": "runner",
                "authority_profile": "agent-queue-v1",
            },
        ],
    }
    assert "credential" not in json.dumps(generated).lower()


def test_bundle_output_is_deterministic(tmp_path: Path) -> None:
    output = tmp_path / "bundle.json"
    value = bundle(actor())

    write_bundle(output, value)
    first = output.read_bytes()
    write_bundle(output, value)

    assert output.read_bytes() == first
    assert first.endswith(b"\n")


@pytest.mark.parametrize(
    "change",
    [
        "credential: fixture-secret\n",
        "unknown_key: value\n",
    ],
)
def test_generator_rejects_secret_or_unknown_identity_fields(tmp_path: Path, change: str) -> None:
    registry_dir = tmp_path / "registry"
    write_identity(
        registry_dir,
        "worker.yaml",
        f"""schema: agent-identity/v1
agent_id: worker
version: 1
status: active
runtime: runner
operator: devon
environment: mini
description: fixture
authority_profile: agent-queue-v1
capabilities: []
prohibited: []
{change}""",
    )
    revision = commit_registry(registry_dir)

    with pytest.raises(RegistryValidationError):
        build_bundle(registry_dir, revision)


def test_generator_rejects_duplicate_ids(tmp_path: Path) -> None:
    registry_dir = tmp_path / "registry"
    identity = """schema: agent-identity/v1
agent_id: worker
version: 1
status: active
runtime: runner
operator: devon
environment: mini
description: fixture
authority_profile: agent-queue-v1
capabilities: []
prohibited: []
"""
    write_identity(registry_dir, "one.yaml", identity)
    write_identity(registry_dir, "two.yaml", identity)
    revision = commit_registry(registry_dir)

    with pytest.raises(RegistryValidationError, match="duplicate"):
        build_bundle(registry_dir, revision)


def test_generator_rejects_dirty_or_non_exact_registry(tmp_path: Path) -> None:
    registry_dir = tmp_path / "registry"
    write_identity(
        registry_dir,
        "worker.yaml",
        """schema: agent-identity/v1
agent_id: worker
version: 1
status: active
runtime: runner
operator: devon
environment: mini
description: fixture
authority_profile: agent-queue-v1
capabilities: []
prohibited: []
""",
    )
    revision = commit_registry(registry_dir)

    with pytest.raises(RegistryValidationError, match="revision"):
        build_bundle(registry_dir, "f" * 40)

    (registry_dir / "agents" / "worker.yaml").write_text("dirty")
    with pytest.raises(RegistryValidationError, match="dirty"):
        build_bundle(registry_dir, revision)


def test_generator_ignores_untracked_ignored_yaml(tmp_path: Path) -> None:
    registry_dir = tmp_path / "registry"
    write_identity(
        registry_dir,
        "worker.yaml",
        """schema: agent-identity/v1
agent_id: worker
version: 1
status: active
runtime: runner
operator: devon
environment: mini
description: fixture
authority_profile: agent-queue-v1
capabilities: []
prohibited: []
""",
    )
    (registry_dir / ".gitignore").write_text("agents/ignored.yaml\n")
    revision = commit_registry(registry_dir)
    write_identity(registry_dir, "ignored.yaml", "host-controlled")

    generated = build_bundle(registry_dir, revision)

    assert [value["agent_id"] for value in generated["actors"]] == ["worker"]


def test_registry_artifact_digest_covers_runtime_helpers(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    fixture = Path("tests/fixtures/security-standards")
    subprocess.run(["cp", "-R", str(fixture), str(artifact)], check=True)
    revision = (artifact / "SOURCE_REVISION").read_text().strip()

    first_digest = artifact_digest(artifact)
    generated = build_bundle_from_artifact(artifact, revision, first_digest)
    (artifact / "src" / "factory_events" / "envelope.py").write_text("changed\n")

    assert [value["agent_id"] for value in generated["actors"]] == ["devon", "worker"]
    assert artifact_digest(artifact) != first_digest
    with pytest.raises(RegistryValidationError, match="digest"):
        build_bundle_from_artifact(artifact, revision, first_digest)


def test_generator_rejects_empty_registry(tmp_path: Path) -> None:
    registry_dir = tmp_path / "registry"
    (registry_dir / "agents").mkdir(parents=True)
    (registry_dir / "README.md").write_text("fixture")
    revision = commit_registry(registry_dir)

    with pytest.raises(RegistryValidationError, match="no identities"):
        build_bundle(registry_dir, revision)


def test_generator_rejects_symlinked_identity(tmp_path: Path) -> None:
    registry_dir = tmp_path / "registry"
    external_identity = tmp_path / "identity.yaml"
    external_identity.write_text("host-controlled")
    agents = registry_dir / "agents"
    agents.mkdir(parents=True)
    (agents / "worker.yaml").symlink_to(external_identity)
    revision = commit_registry(registry_dir)

    with pytest.raises(RegistryValidationError, match="regular file"):
        build_bundle(registry_dir, revision)


def test_generator_rejects_symlinked_agents_directory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    registry_dir = repository / "registry"
    external_agents = tmp_path / "agents"
    write_identity(
        tmp_path,
        "worker.yaml",
        """schema: agent-identity/v1
agent_id: worker
version: 1
status: active
runtime: runner
operator: devon
environment: mini
description: fixture
authority_profile: agent-queue-v1
capabilities: []
prohibited: []
""",
    )
    registry_dir.mkdir(parents=True)
    (registry_dir / "agents").symlink_to(external_agents)
    revision = commit_registry(repository)

    with pytest.raises(RegistryValidationError, match="symlink"):
        build_bundle(registry_dir, revision)


@pytest.mark.parametrize(
    "status",
    ["retired", "reserved"],
)
def test_registry_fails_closed_for_inactive_identities(status: str) -> None:
    adapter = RegistryAdapter(bundle(actor(status=status)))

    with pytest.raises(RegistryValidationError):
        adapter.resolve("worker")


@pytest.mark.parametrize(
    "invalid_bundle",
    [
        bundle(actor(extra="value")),
        bundle(actor(version=True)),
        bundle(actor(), actor()),
    ],
)
def test_registry_fails_closed_for_unusable_or_invalid_identities(
    invalid_bundle: dict[str, object],
) -> None:
    with pytest.raises(RegistryValidationError):
        RegistryAdapter(invalid_bundle)


def test_registry_rejects_unknown_identity() -> None:
    adapter = RegistryAdapter(bundle(actor()))

    with pytest.raises(RegistryValidationError):
        adapter.resolve("missing")
