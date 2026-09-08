from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from orchestrator.api import health
from orchestrator.api.dependencies import get_session
from orchestrator.main import app, create_app


def test_liveness_does_not_require_database() -> None:
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "revision": None}


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
        lambda _connection: Mock(get_current_heads=lambda: ("head",)),
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
        lambda _connection: Mock(get_current_heads=lambda: ("database-head",)),
    )
    monkeypatch.setattr(
        "orchestrator.api.health.ScriptDirectory.from_config",
        lambda _config: Mock(get_heads=lambda: ["head-a", "head-b"]),
    )

    response = TestClient(application).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "migration_drift"}


@pytest.mark.parametrize("database_heads", [(), ("extra-database-head",)])
def test_readiness_rejects_real_database_zero_or_multiple_heads(
    migrated_session: Session,
    database_heads: tuple[str, ...],
) -> None:
    migrated_session.execute(text("DELETE FROM alembic_version"))
    for head in database_heads:
        migrated_session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": head},
        )
    if database_heads:
        script_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        assert script_head is not None
        migrated_session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": script_head},
        )
    application = create_app()
    application.dependency_overrides[get_session] = lambda: migrated_session

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
        lambda _connection: Mock(get_current_heads=lambda: ("head",)),
    )
    monkeypatch.setattr("orchestrator.api.health.ScriptDirectory.from_config", fail_config)

    response = TestClient(application).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "configuration"}
    assert "private filesystem detail" not in response.text


# ---------------------------------------------------------------------------------------------
# What build is this? The image carries the answer as an OCI label and the tag carries a short
# form, and reading either needs a shell on the host -- so until this field existed nothing off
# the machine could ask what production was serving, and this is the one application in the
# estate whose swap is performed by hand.
# ---------------------------------------------------------------------------------------------


def test_liveness_states_the_build_it_is_serving(monkeypatch) -> None:
    monkeypatch.setattr(health, "REVISION", "de1bb53b20f287b0738c1b2c96d93b934a2ebfac")

    body = TestClient(app).get("/health/live").json()

    assert body["revision"] == "de1bb53b20f287b0738c1b2c96d93b934a2ebfac"
    assert body["status"] == "ok"


def test_an_unstamped_build_SAYS_SO_rather_than_omitting_the_key(monkeypatch) -> None:
    """Every image built before this field existed has no honest answer, and so does any process
    started outside an image. `null` says that; an absent key says the same thing to a reader who
    is looking and something else entirely to one calling `.get()`, which is how a build that
    cannot state its revision comes to be read as one that matches."""
    monkeypatch.setattr(health, "REVISION", None)

    body = TestClient(app).get("/health/live").json()

    assert "revision" in body
    assert body["revision"] is None


def test_the_served_schema_admits_a_null_revision(monkeypatch) -> None:
    """A response model that declared `dict[str, str]` would DROP the key rather than serve
    `null` -- this repository's own recorded invariant, in the direction where the consumer reads
    a key that is not there and continues."""
    schema = app.openapi()["paths"]["/health/live"]["get"]["responses"]["200"]
    types = schema["content"]["application/json"]["schema"]["additionalProperties"]["anyOf"]

    assert {"type": "null"} in types


def test_the_revision_is_read_once_rather_than_per_request(monkeypatch) -> None:
    """A process cannot change which build it is. Reading the environment per request would say
    otherwise to anyone who looked, and would let a variable set after start-up change the
    answer -- so the value is bound at import and the environment is not consulted again."""
    monkeypatch.setattr(health, "REVISION", "aaaaaaaa")
    monkeypatch.setenv("ORCHESTRATOR_REVISION", "bbbbbbbb")

    assert TestClient(app).get("/health/live").json()["revision"] == "aaaaaaaa"
