import json
from pathlib import Path

import pytest
import yaml

from orchestrator.identity.registry import RegistryAdapter
from orchestrator.main import load_auth_config
from tests.architecture.test_interpreter_agreement import pinned_version

RUNTIME_STAGE = f"FROM python:{pinned_version()}-slim AS runtime"


def test_container_is_non_root_and_health_checked() -> None:
    """The interpreter version is DERIVED from `.python-version` rather than repeated here.

    It used to be a literal, and the reasoning for that is worth keeping: the literal is what an
    update bot's proposal collides with, so a language-version replacement cannot reach
    production without a person editing it -- the same construct as `factory-runner`'s action
    pins, doing the same job. Editing it is how the review is recorded. That property is
    unchanged; the literal it collides with now lives in `.python-version`, one file that
    everything reads, and `test_interpreter_agreement.py` is what reds when the two part company.

    Moved 3.12 -> 3.14 on 2026-09-02 after measuring: the whole locked dependency set installs on
    3.14 on macOS/arm64 AND on linux/amd64 (the runtime platform), and the full suite returns
    4752 passed / 2 skipped on 3.14 -- identical to 3.12.

    THIS DOCSTRING USED TO CARRY A GUARD THAT WENT MISSING FOR A DAY, AND THE SHAPE OF THAT GAP
    IS WORTH KEEPING. It read: "`requires-python` stays `>=3.12` ... pyright still checks against
    that floor, so using a 3.13+ only feature is still an error here." On 2026-09-09 pyright was
    moved off the floor and onto `.python-version`, because checking a version nothing executes is
    the wrong grounds to accept or reject code on -- and the floor stayed `>=3.12` for the rest of
    that day, so for those hours pyright targeted the pin, ruff derived its target from the floor,
    and NOTHING checked the floor at all. That hole is closed: the floor is now the pin, held to
    equality by `test_interpreter_agreement.py`'s
    `test_the_requires_python_floor_is_the_pinned_version`, and both tools target one number.
    Do not reopen it by putting pyright back on the floor -- the
    pinned interpreter is what runs, and the floor is now checked on its own terms.
    """
    dockerfile = Path("Dockerfile").read_text()

    assert RUNTIME_STAGE in dockerfile
    assert "USER orchestrator" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "/health/live" in dockerfile
    assert "alembic upgrade" not in dockerfile


def test_the_revision_arg_is_declared_in_the_RUNTIME_stage() -> None:
    """An ARG is scoped to the stage that declares it. Declared on the builder instead, the
    runtime ENV would expand to empty and every image ever built would report `null` while the
    build command looked entirely correct -- a wrong answer that costs a release to notice."""
    dockerfile = Path("Dockerfile").read_text()
    runtime = dockerfile.split(RUNTIME_STAGE, 1)[1]

    assert "ARG ORCHESTRATOR_REVISION" in runtime
    assert "ENV ORCHESTRATOR_REVISION=${ORCHESTRATOR_REVISION}" in runtime


def test_the_release_workflow_passes_the_revision_it_labels_the_image_with() -> None:
    """The workflow already stamps this commit as an OCI label. Serving a DIFFERENT value than
    the label would be worse than serving none, so both read the one output."""
    workflow = Path(".github/workflows/release-image.yml").read_text()

    assert '--build-arg ORCHESTRATOR_REVISION="${{ steps.tag.outputs.revision }}"' in workflow
    assert 'org.opencontainers.image.revision="${{ steps.tag.outputs.revision }}"' in workflow


def test_runtime_image_copies_only_declared_application_artifacts() -> None:
    dockerfile = Path("Dockerfile").read_text()
    runtime = dockerfile.split(RUNTIME_STAGE, 1)[1]

    assert "COPY . ." not in runtime
    assert "/app/.venv" in runtime
    assert "/app/src" in runtime
    assert "/app/migrations" in runtime
    assert "/app/registry-bundle.json" in runtime


def test_runtime_image_carries_pinned_factory_event_helpers() -> None:
    dockerfile = Path("Dockerfile").read_text()
    runtime = dockerfile.split(RUNTIME_STAGE, 1)[1]

    assert "SECURITY_STANDARDS_DIR=/app/security-standards" in runtime
    assert "/agents /app/security-standards/registry/agents" in runtime
    assert "/src /app/security-standards/src" in runtime
    assert "/schema /app/security-standards/schema" in runtime


def test_compose_uses_postgres_16_and_explicit_web_startup() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = compose["services"]

    assert services["orchestrator-postgres"]["image"] == "postgres:16-alpine"
    assert "alembic" not in " ".join(services["orchestrator"]["command"])
    assert "/health/live" in services["orchestrator"]["healthcheck"]["test"][-1]


def test_dockerignore_excludes_credentials_and_local_state() -> None:
    ignored = set(Path(".dockerignore").read_text().splitlines())

    assert {".git", ".env", "*.env", ".venv", "__pycache__", ".pytest_cache"} <= ignored


def test_runtime_auth_loads_embedded_registry_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = Path("tests/fixtures/registry-bundle.json").resolve()
    monkeypatch.setenv("ORCHESTRATOR_REGISTRY_BUNDLE", str(bundle))
    monkeypatch.setenv(
        "ORCHESTRATOR_M2M_CREDENTIALS",
        json.dumps({"worker-key": {"agent_id": "worker", "token_hash": "a" * 64}}),
    )
    monkeypatch.setenv("ORCHESTRATOR_TRUSTED_PROXY_IPS", '["127.0.0.1"]')
    monkeypatch.setenv("ORCHESTRATOR_PROXY_MARKER", "trusted-marker")
    monkeypatch.setenv("ORCHESTRATOR_EMAIL_TO_ACTOR", '{"devon@example.invalid":"devon"}')
    monkeypatch.setenv("ORCHESTRATOR_CSRF_SECRET", "x" * 32)

    config = load_auth_config()

    assert config is not None
    assert config.registry.source_revision == "0123456789abcdef0123456789abcdef01234567"
    assert config.m2m_credentials["worker-key"].agent_id == "worker"
    monkeypatch.delenv("ORCHESTRATOR_CSRF_SECRET")
    with pytest.raises(RuntimeError, match="runtime authentication configuration"):
        load_auth_config()


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"worker-key": {"agent_id": "worker", "token_hash": "A" * 64}},
        {"worker-key": {"agent_id": "worker", "token_hash": "a" * 63}},
        {"worker-key": {"agent_id": "unknown", "token_hash": "a" * 64}},
        {"worker-key": {"agent_id": "retired", "token_hash": "a" * 64}},
    ],
)
def test_runtime_auth_rejects_invalid_credentials_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    credentials: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    bundle = {
        "schema": "orchestrator-actor-bundle/v1",
        "source_revision": "0123456789abcdef0123456789abcdef01234567",
        "actors": [
            {
                "agent_id": "worker",
                "version": 1,
                "status": "active",
                "runtime": "runner",
                "authority_profile": "agent-queue-v1",
            },
            {
                "agent_id": "retired",
                "version": 1,
                "status": "retired",
                "runtime": "runner",
                "authority_profile": "agent-queue-v1",
            },
        ],
    }
    bundle_path = Path("tests/fixtures/runtime-invalid-bundle.json")
    monkeypatch.setattr(
        "orchestrator.main.RegistryAdapter.from_path",
        lambda _path: RegistryAdapter(bundle),
    )
    monkeypatch.setenv("ORCHESTRATOR_REGISTRY_BUNDLE", str(bundle_path))
    monkeypatch.setenv("ORCHESTRATOR_M2M_CREDENTIALS", json.dumps(credentials))
    monkeypatch.setenv("ORCHESTRATOR_TRUSTED_PROXY_IPS", '["127.0.0.1"]')
    monkeypatch.setenv("ORCHESTRATOR_PROXY_MARKER", "trusted-marker")
    monkeypatch.setenv("ORCHESTRATOR_EMAIL_TO_ACTOR", "{}")
    monkeypatch.setenv("ORCHESTRATOR_CSRF_SECRET", "x" * 32)

    with pytest.raises(RuntimeError, match="runtime authentication configuration"):
        load_auth_config()
    assert "worker-key" not in caplog.text
    assert "a" * 63 not in caplog.text


def test_runtime_auth_rejects_role_for_unknown_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = Path("tests/fixtures/registry-bundle.json").resolve()
    monkeypatch.setenv("ORCHESTRATOR_REGISTRY_BUNDLE", str(bundle))
    monkeypatch.setenv(
        "ORCHESTRATOR_M2M_CREDENTIALS",
        json.dumps({"worker-key": {"agent_id": "worker", "token_hash": "a" * 64}}),
    )
    monkeypatch.setenv("ORCHESTRATOR_M2M_ROLES", '{"missing-key":"system"}')
    monkeypatch.setenv("ORCHESTRATOR_TRUSTED_PROXY_IPS", '["127.0.0.1"]')
    monkeypatch.setenv("ORCHESTRATOR_PROXY_MARKER", "trusted-marker")
    monkeypatch.setenv("ORCHESTRATOR_EMAIL_TO_ACTOR", '{"devon@example.invalid":"devon"}')
    monkeypatch.setenv("ORCHESTRATOR_CSRF_SECRET", "x" * 32)

    with pytest.raises(RuntimeError, match="runtime authentication configuration"):
        load_auth_config()


@pytest.mark.parametrize("actor_id", ["missing", "worker"])
def test_runtime_auth_rejects_invalid_human_actor_mapping(
    monkeypatch: pytest.MonkeyPatch,
    actor_id: str,
) -> None:
    bundle = Path("tests/fixtures/registry-bundle.json").resolve()
    monkeypatch.setenv("ORCHESTRATOR_REGISTRY_BUNDLE", str(bundle))
    monkeypatch.setenv(
        "ORCHESTRATOR_M2M_CREDENTIALS",
        json.dumps({"worker-key": {"agent_id": "worker", "token_hash": "a" * 64}}),
    )
    monkeypatch.setenv("ORCHESTRATOR_TRUSTED_PROXY_IPS", '["127.0.0.1"]')
    monkeypatch.setenv("ORCHESTRATOR_PROXY_MARKER", "trusted-marker")
    monkeypatch.setenv(
        "ORCHESTRATOR_EMAIL_TO_ACTOR",
        json.dumps({"devon@example.invalid": actor_id}),
    )
    monkeypatch.setenv("ORCHESTRATOR_CSRF_SECRET", "x" * 32)

    with pytest.raises(RuntimeError, match="runtime authentication configuration"):
        load_auth_config()
