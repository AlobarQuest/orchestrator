import json
from pathlib import Path

import pytest
import yaml

from orchestrator.config import ProductionDrillMode
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.main import create_app, load_auth_config
from orchestrator.services.production_drill_compatibility import (
    DRILL_REVISION,
    PRE_DRILL_REVISION,
    production_drill_enabled,
    production_drill_schema_active,
)


def _configure_runtime_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = Path("tests/fixtures/registry-bundle.json").resolve()
    monkeypatch.setenv("ORCHESTRATOR_REGISTRY_BUNDLE", str(bundle))
    monkeypatch.setenv(
        "ORCHESTRATOR_M2M_CREDENTIALS",
        json.dumps(
            {
                "worker-key": {"agent_id": "worker", "token_hash": "a" * 64},
                "observer-key": {
                    "agent_id": "runtime-observer",
                    "token_hash": "b" * 64,
                },
            }
        ),
    )
    monkeypatch.setenv("ORCHESTRATOR_TRUSTED_PROXY_IPS", '["127.0.0.1"]')
    monkeypatch.setenv("ORCHESTRATOR_PROXY_MARKER", "trusted-marker")
    monkeypatch.setenv("ORCHESTRATOR_EMAIL_TO_ACTOR", '{"devon@example.invalid":"devon"}')
    monkeypatch.setenv(
        "ORCHESTRATOR_M2M_ROLES",
        '{"worker-key":"system","observer-key":"system"}',
    )
    monkeypatch.setenv("ORCHESTRATOR_CSRF_SECRET", "x" * 32)


def _set_drill_credential_ids(
    monkeypatch: pytest.MonkeyPatch,
    production_drill_id: str | None,
    runtime_observer_id: str | None,
) -> None:
    pairs = {
        "ORCHESTRATOR_PRODUCTION_DRILL_CREDENTIAL_KEY_ID": production_drill_id,
        "ORCHESTRATOR_RUNTIME_OBSERVER_CREDENTIAL_KEY_ID": runtime_observer_id,
    }
    for name, value in pairs.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_container_is_python_312_non_root_and_health_checked() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "python:3.12-slim" in dockerfile
    assert "USER orchestrator" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "/health/live" in dockerfile
    assert "alembic upgrade" not in dockerfile


def test_runtime_image_copies_only_declared_application_artifacts() -> None:
    dockerfile = Path("Dockerfile").read_text()
    runtime = dockerfile.split("FROM python:3.12-slim AS runtime", 1)[1]

    assert "COPY . ." not in runtime
    assert "/app/.venv" in runtime
    assert "/app/src" in runtime
    assert "/app/migrations" in runtime
    assert "/app/registry-bundle.json" in runtime


def test_runtime_image_carries_pinned_factory_event_helpers() -> None:
    dockerfile = Path("Dockerfile").read_text()
    runtime = dockerfile.split("FROM python:3.12-slim AS runtime", 1)[1]

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


def test_pre_drill_authenticated_environment_loads_in_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime_auth(monkeypatch)
    _set_drill_credential_ids(monkeypatch, None, None)

    config = load_auth_config(ProductionDrillMode.OFF)

    assert config is not None
    assert config.registry.source_revision == "0123456789abcdef0123456789abcdef01234567"
    assert config.m2m_credentials["worker-key"].agent_id == "worker"
    assert config.production_drill_credential_key_id is None
    assert config.runtime_observer_credential_key_id is None


def test_runtime_auth_loads_in_standby_without_drill_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime_auth(monkeypatch)
    _set_drill_credential_ids(monkeypatch, None, None)

    config = load_auth_config(ProductionDrillMode.STANDBY)

    assert config is not None
    assert config.production_drill_credential_key_id is None
    assert config.runtime_observer_credential_key_id is None


@pytest.mark.parametrize("mode", list(ProductionDrillMode))
@pytest.mark.parametrize(
    ("production_drill_id", "runtime_observer_id"),
    [("worker-key", None), (None, "observer-key")],
)
def test_runtime_auth_rejects_partial_drill_credential_pair(
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
    production_drill_id: str | None,
    runtime_observer_id: str | None,
) -> None:
    _configure_runtime_auth(monkeypatch)
    _set_drill_credential_ids(monkeypatch, production_drill_id, runtime_observer_id)

    with pytest.raises(RuntimeError, match="runtime authentication configuration"):
        load_auth_config(mode)


def test_enabled_runtime_auth_requires_drill_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime_auth(monkeypatch)
    _set_drill_credential_ids(monkeypatch, None, None)

    with pytest.raises(RuntimeError, match="runtime authentication configuration"):
        load_auth_config(ProductionDrillMode.ENABLED)


@pytest.mark.parametrize("mode", list(ProductionDrillMode))
def test_runtime_auth_loads_distinct_system_drill_credentials_in_every_mode(
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
) -> None:
    _configure_runtime_auth(monkeypatch)
    _set_drill_credential_ids(monkeypatch, "worker-key", "observer-key")

    config = load_auth_config(mode)

    assert config is not None
    assert config.production_drill_credential_key_id == "worker-key"
    assert config.runtime_observer_credential_key_id == "observer-key"


@pytest.mark.parametrize(
    ("production_drill_id", "runtime_observer_id", "roles"),
    [
        (
            "worker-key",
            "worker-key",
            '{"worker-key":"system","observer-key":"system"}',
        ),
        (
            "worker-key",
            "observer-key",
            '{"worker-key":"worker","observer-key":"system"}',
        ),
    ],
)
def test_runtime_auth_rejects_invalid_drill_credential_mapping_without_logging_ids(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    production_drill_id: str,
    runtime_observer_id: str,
    roles: str,
) -> None:
    _configure_runtime_auth(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_M2M_ROLES", roles)
    _set_drill_credential_ids(monkeypatch, production_drill_id, runtime_observer_id)

    with pytest.raises(RuntimeError, match="runtime authentication configuration"):
        load_auth_config(ProductionDrillMode.ENABLED)
    assert production_drill_id not in caplog.text
    assert runtime_observer_id not in caplog.text


@pytest.mark.parametrize("mode", [ProductionDrillMode.OFF, ProductionDrillMode.STANDBY])
def test_no_auth_development_mode_is_preserved_before_enabled(
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
) -> None:
    monkeypatch.delenv("ORCHESTRATOR_REGISTRY_BUNDLE", raising=False)

    assert load_auth_config(mode) is None


def test_enabled_rejects_missing_registry_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_REGISTRY_BUNDLE", raising=False)

    with pytest.raises(RuntimeError, match="runtime authentication configuration"):
        load_auth_config(ProductionDrillMode.ENABLED)


def test_create_app_stores_explicit_production_drill_mode() -> None:
    application = create_app(production_drill_mode=ProductionDrillMode.STANDBY)

    assert application.state.production_drill_mode is ProductionDrillMode.STANDBY


@pytest.mark.parametrize(
    ("mode", "schema_active", "enabled"),
    [
        (ProductionDrillMode.OFF, False, False),
        (ProductionDrillMode.STANDBY, True, False),
        (ProductionDrillMode.ENABLED, True, True),
    ],
)
def test_production_drill_compatibility_predicates(
    mode: ProductionDrillMode,
    schema_active: bool,
    enabled: bool,
) -> None:
    assert production_drill_schema_active(mode) is schema_active
    assert production_drill_enabled(mode) is enabled
    assert PRE_DRILL_REVISION == "0014_wsp21_recovery_controls"
    assert DRILL_REVISION == "0017_runtime_observations"


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
