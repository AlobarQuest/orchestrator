import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import orchestrator.api.routes as routes
from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.config import Settings, get_settings
from orchestrator.main import create_app
from orchestrator.services.github_app import GitHubAppTokenError, reset_token_providers
from tests.api.test_lifecycle_api import AUTHORITY as BASE_AUTHORITY
from tests.api.test_lifecycle_api import HUMAN, SYSTEM

TARGET_REPOSITORY = "AlobarQuest/orchestrator"
# Dispatch routes per-unit, so a dispatchable unit must declare its target repository.
AUTHORITY = {
    **BASE_AUTHORITY,
    "constraints": {"target_repository": TARGET_REPOSITORY},
    # Conformance is attested per unit, against that unit's own target repository.
    "conformance": {
        "status": "green",
        "accepted_standards": [],
        "standards_touched": ["project-standards"],
    },
}


# The provider the route handed the dispatcher, captured for the credential-agreement test.
CAPTURED_TOKEN_PROVIDERS: list[Callable[[], str]] = []


class FakeGitHubActionsDispatcher:
    calls: list[dict[str, object]] = []

    def __init__(self, token_provider: Callable[[], str]) -> None:
        CAPTURED_TOKEN_PROVIDERS.append(token_provider)

    def dispatch_workflow(self, **kwargs: object) -> dict[str, str]:
        self.calls.append(kwargs)
        return {
            "workflow_run_id": "api-run",
            "workflow_run_url": "https://github.invalid/api-run",
        }


@pytest.fixture
def dispatch_client(
    auth_config: AuthConfig,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    FakeGitHubActionsDispatcher.calls = []
    CAPTURED_TOKEN_PROVIDERS.clear()
    reset_token_providers()
    monkeypatch.setattr(routes, "GitHubActionsDispatcher", FakeGitHubActionsDispatcher)
    app = create_app(auth_config)

    def database_session() -> Iterator[Session]:
        with Session(migrated_engine) as session:
            yield session

    def runtime_settings() -> Settings:
        return Settings(
            database_url="postgresql+psycopg://postgres:postgres@127.0.0.1/orchestrator_test",
            dispatch_enabled=True,
            dispatch_allowed_change_classes=frozenset({"repo.edit"}),
            dispatch_enabled_capabilities=frozenset({"repo.edit"}),
            dispatch_allowed_target_repositories=frozenset({TARGET_REPOSITORY}),
            github_app_id="123456",
            github_app_installation_id="78901234",
            github_app_private_key_b64=SecretStr("cGVt"),
        )

    app.dependency_overrides[get_session] = database_session
    app.dependency_overrides[get_settings] = runtime_settings
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client


def register_ready_unit(db_client: TestClient, *, key: str = "dispatch-api") -> str:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-revision",
            "expected_version": 0,
            "package_id": f"{key}-package",
            "source_repository": "AlobarQuest/orchestrator",
            "revision": 1,
            "content_hash": f"sha256:{key}",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-unit",
            "expected_version": 0,
            "unit_key": key,
            "title": "Dispatch API",
            "outcome": "Dispatch API works",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    approved = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert approved.status_code == 200
    ready = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready",
        headers=SYSTEM,
        json={"idempotency_key": f"{key}-ready", "expected_version": 1},
    )
    assert ready.status_code == 200
    return str(unit_id)


def test_dispatch_api_declares_route(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/work-units/{unit_id}/dispatch" in document["paths"]
    assert "DispatchCommandModel" in document["components"]["schemas"]
    assert "DispatchResponse" in document["components"]["schemas"]


def test_dispatch_api_fails_closed_by_default(db_client: TestClient) -> None:
    unit_id = register_ready_unit(db_client, key="dispatch-api-disabled")

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/dispatch",
        headers=SYSTEM,
        json={
            "idempotency_key": "dispatch-api-disabled",
            "expected_version": 2,
            "runner_attempt": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert response.json()["reason_code"] == "dispatch_disabled"


def test_dispatch_api_calls_configured_workflow(dispatch_client: TestClient) -> None:
    unit_id = register_ready_unit(dispatch_client, key="dispatch-api-enabled")

    response = dispatch_client.post(
        f"/api/v1/work-units/{unit_id}/dispatch",
        headers=SYSTEM,
        json={
            "idempotency_key": "dispatch-api-enabled",
            "expected_version": 2,
            "runner_attempt": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "dispatched"
    assert response.json()["github_run_id"] == "api-run"
    assert response.json()["target_repository"] == TARGET_REPOSITORY
    assert FakeGitHubActionsDispatcher.calls[0]["repository"] == TARGET_REPOSITORY
    assert FakeGitHubActionsDispatcher.calls[0]["inputs"] == {"work_unit_id": unit_id}


def test_dispatch_api_mints_with_the_credentials_the_admission_gate_attested(
    dispatch_client: TestClient,
) -> None:
    """The gate reads the injected settings; the minter must read the very same ones.

    A provider built from process settings instead would carry no credentials at all, and
    would fail `app_credentials_missing` while the gate had just attested `configured`.
    """
    unit_id = register_ready_unit(dispatch_client, key="dispatch-api-credentials")
    response = dispatch_client.post(
        f"/api/v1/work-units/{unit_id}/dispatch",
        headers=SYSTEM,
        json={
            "idempotency_key": "dispatch-api-credentials",
            "expected_version": 2,
            "runner_attempt": 1,
        },
    )
    assert response.status_code == 200

    assert len(CAPTURED_TOKEN_PROVIDERS) == 1
    with pytest.raises(GitHubAppTokenError) as excinfo:
        CAPTURED_TOKEN_PROVIDERS[0]()

    # `cGVt` decodes to b"pem", which is not a usable key — but it IS the injected one.
    assert excinfo.value.code == "private_key_invalid"
