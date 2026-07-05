import shutil
from pathlib import Path

import pytest
import yaml

from orchestrator.identity.registry import RegistryValidationError
from scripts.build_registry_bundle import artifact_digest, build_bundle_from_artifact


def test_docker_build_requires_exact_registry_revision() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "ARG SECURITY_STANDARDS_REVISION" in dockerfile
    assert "--source-revision ${SECURITY_STANDARDS_REVISION}" in dockerfile
    assert "--artifact-dir /registry" in dockerfile
    assert "ARG REGISTRY_ARTIFACT_SHA256" in dockerfile
    assert "--artifact-sha256 ${REGISTRY_ARTIFACT_SHA256}" in dockerfile
    assert "COPY --from=registry" in dockerfile


def test_quality_build_uses_fixture_registry_with_pinned_revision() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/quality.yml").read_text())
    steps = workflow["jobs"]["quality"]["steps"]
    build = next(step for step in steps if step.get("name") == "Build fixture-registry image")

    assert "SECURITY_STANDARDS_REVISION=" in build["run"]
    assert "REGISTRY_ARTIFACT_SHA256=" in build["run"]
    assert "--build-context registry=tests/fixtures/security-standards" in build["run"]


def test_fixture_registry_revision_is_exact_and_non_secret() -> None:
    fixture = Path("tests/fixtures/security-standards")
    revision = (fixture / "SOURCE_REVISION").read_text().strip()
    contents = "\n".join(path.read_text() for path in (fixture / "agents").glob("*.yaml"))

    assert len(revision) == 40
    assert all(character in "0123456789abcdef" for character in revision)
    assert "credential" not in contents.lower()
    assert "token" not in contents.lower()


def test_fixture_artifact_build_records_exact_revision() -> None:
    fixture = Path("tests/fixtures/security-standards")
    revision = (fixture / "SOURCE_REVISION").read_text().strip()

    digest = artifact_digest(fixture)
    bundle = build_bundle_from_artifact(fixture, revision, digest)

    assert bundle["source_revision"] == revision
    assert [actor["agent_id"] for actor in bundle["actors"]] == ["devon", "worker"]
    with pytest.raises(RegistryValidationError, match="source revision"):
        build_bundle_from_artifact(fixture, "f" * 40, digest)


def test_artifact_digest_rejects_modified_content_with_unchanged_revision(
    tmp_path: Path,
) -> None:
    source = Path("tests/fixtures/security-standards")
    artifact = tmp_path / "registry"
    shutil.copytree(source, artifact)
    expected_digest = artifact_digest(artifact)
    worker = artifact / "agents" / "worker.yaml"
    worker.write_text(worker.read_text().replace("version: 1", "version: 2"))

    with pytest.raises(RegistryValidationError, match="digest"):
        build_bundle_from_artifact(
            artifact,
            (artifact / "SOURCE_REVISION").read_text().strip(),
            expected_digest,
        )


def test_artifact_rejects_source_revision_symlink(tmp_path: Path) -> None:
    source = Path("tests/fixtures/security-standards")
    artifact = tmp_path / "registry"
    shutil.copytree(source, artifact)
    revision = artifact / "SOURCE_REVISION"
    target = tmp_path / "revision"
    target.write_text(revision.read_text())
    revision.unlink()
    revision.symlink_to(target)

    with pytest.raises(RegistryValidationError, match="symlink"):
        artifact_digest(artifact)
