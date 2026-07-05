from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_session
from orchestrator.main import app, create_app


def test_liveness_does_not_require_database() -> None:
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_never_resolves_database_dependency() -> None:
    application = create_app()

    def fail_if_called() -> Iterator[Session]:
        raise AssertionError("liveness touched the database")
        yield

    application.dependency_overrides[get_session] = fail_if_called

    assert TestClient(application).get("/health/live").status_code == 200


def test_readiness_resolves_alembic_config_independent_of_cwd(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = Mock(spec=Session)
    connection = Mock()
    session.connection.return_value = connection
    application = create_app()
    application.dependency_overrides[get_session] = lambda: session
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "orchestrator.api.health.MigrationContext.configure",
        lambda _connection: Mock(get_current_revision=lambda: "head"),
    )
    observed: dict[str, str] = {}

    def script_from_config(config):
        observed["config_file"] = config.config_file_name
        return Mock(get_heads=lambda: ["head"])

    monkeypatch.setattr("orchestrator.api.health.ScriptDirectory.from_config", script_from_config)

    response = TestClient(application).get("/health/ready")

    assert response.status_code == 200
    assert Path(observed["config_file"]).is_absolute()


def test_readiness_sanitizes_database_failure() -> None:
    session = Mock(spec=Session)
    session.execute.side_effect = OperationalError("SELECT 1", {}, Exception("secret host"))
    application = create_app()
    application.dependency_overrides[get_session] = lambda: session

    response = TestClient(application).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "database"}
    assert "secret host" not in response.text


def test_readiness_rejects_drift_and_multiple_heads(monkeypatch) -> None:
    session = Mock(spec=Session)
    session.connection.return_value = Mock()
    application = create_app()
    application.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(
        "orchestrator.api.health.MigrationContext.configure",
        lambda _connection: Mock(get_current_revision=lambda: "database-head"),
    )
    monkeypatch.setattr(
        "orchestrator.api.health.ScriptDirectory.from_config",
        lambda _config: Mock(get_heads=lambda: ["head-a", "head-b"]),
    )

    response = TestClient(application).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "migration_drift"}


def test_readiness_sanitizes_alembic_configuration_failure(monkeypatch) -> None:
    session = Mock(spec=Session)
    session.connection.return_value = Mock()
    application = create_app()
    application.dependency_overrides[get_session] = lambda: session

    def fail_config(_config):
        raise RuntimeError("private filesystem detail")

    monkeypatch.setattr(
        "orchestrator.api.health.MigrationContext.configure",
        lambda _connection: Mock(get_current_revision=lambda: "head"),
    )
    monkeypatch.setattr("orchestrator.api.health.ScriptDirectory.from_config", fail_config)

    response = TestClient(application).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "configuration"}
    assert "private filesystem detail" not in response.text
