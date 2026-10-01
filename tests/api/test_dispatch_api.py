from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.api.routes import common as common_routes
from orchestrator.api.routes import execution as execution_routes
from orchestrator.config import Settings, get_settings
from orchestrator.main import create_app
from orchestrator.services.github_app import GitHubAppTokenError, reset_token_providers
from tests._support.seeding import mount_seeding_routes
from tests._support.seeding import register_ready_unit as seed_ready_unit
from tests.api.test_lifecycle_api import AUTHORITY as BASE_AUTHORITY
from tests.api.test_lifecycle_api import SYSTEM
from tests.services.estate_doubles import FakeEstateLandingSource, inert_source
from tests.services.target_doubles import FakeFactoryTargetSource, declared_source

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


def FakeEstateLandingSourceFactory(**_: object) -> FakeEstateLandingSource:
    return inert_source()


def FakeFactoryTargetSourceFactory(*_: object, **__: object) -> FakeFactoryTargetSource:
    return declared_source()


@pytest.fixture
def dispatch_client(
    auth_config: AuthConfig,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    FakeGitHubActionsDispatcher.calls = []
    CAPTURED_TOKEN_PROVIDERS.clear()
    reset_token_providers()
    monkeypatch.setattr(execution_routes, "GitHubActionsDispatcher", FakeGitHubActionsDispatcher)
    # WS-P2.28: admission asks the estate whether landing on the target repository changes
    # anything already serving. Configured AND faked here for the same reason the workflow
    # client is: an unconfigured source refuses, which is the point of it.
    monkeypatch.setattr(common_routes, "HttpEstateLandingSource", FakeEstateLandingSourceFactory)
    # ADR-0015: admission reads the target repository's own declaration from GitHub.
    monkeypatch.setattr(
        execution_routes, "GitHubFactoryTargetSource", FakeFactoryTargetSourceFactory
    )
    app = create_app(auth_config)
    mount_seeding_routes(app)

    def database_session() -> Iterator[Session]:
        with Session(migrated_engine) as session:
            yield session

    def runtime_settings() -> Settings:
        return Settings(
            database_url="postgresql+psycopg://postgres:postgres@127.0.0.1/orchestrator_test",
            dispatch_enabled=True,
            dispatch_allowed_change_classes=frozenset({"repo.edit"}),
            dispatch_enabled_capabilities=frozenset({"repo.edit"}),
            github_app_id="123456",
            github_app_installation_id="78901234",
            github_app_private_key_b64=SecretStr("cGVt"),
            app_brain_url="https://app-brain.example",
            app_brain_read_key=SecretStr("read-only-value"),
        )

    app.dependency_overrides[get_session] = database_session
    app.dependency_overrides[get_settings] = runtime_settings
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client


def register_ready_unit(db_client: TestClient, *, key: str = "dispatch-api") -> str:
    return seed_ready_unit(
        db_client,
        key,
        authority=AUTHORITY,
        unit_key=key,
        title="Dispatch API",
        outcome="Dispatch API works",
        source_repository="AlobarQuest/orchestrator",
        enforcement_snapshot={"reach": ["source_repository"]},
        approved_at=datetime(2026, 7, 8, tzinfo=UTC),
    )


def test_dispatch_api_declares_route(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/work-units/{unit_id}/dispatch" in document["paths"]
    assert "DispatchCommandModel" in document["components"]["schemas"]
    assert "DispatchResponse" in document["components"]["schemas"]


def test_dispatch_api_fails_closed_when_switched_off(db_client: TestClient) -> None:
    """ADR-0046: dispatch defaults ON, and `ORCHESTRATOR_DISPATCH_ENABLED=false` is the off
    switch. Switched off, the route refuses by name before anything else is consulted."""
    switched_off = Settings.model_validate({"dispatch_enabled": False})
    cast(FastAPI, db_client.app).dependency_overrides[get_settings] = lambda: switched_off
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


# ---------------------------------------------------------------------------------------------
# ADR-0032: a supervised run may start outside the hours policy declares.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [{}, {"reason": None}, {"reason": ""}, {"reason": "   "}],
    ids=["absent", "null", "empty", "whitespace"],
)
def test_an_override_with_no_stated_reason_is_refused_by_name(
    dispatch_client: TestClient, override: dict[str, object]
) -> None:
    """A named `DomainError`, never a 422 naming a field location and never a 500.

    The `reason` field is deliberately unconstrained in the request model, so all four of these
    shapes reach the type that carries the override and are refused by it. A constrained field
    would answer three of them with a validation error listing a location, which tells an
    operator less about what to do next -- and would leave the fourth, an override object with
    nothing in it, indistinguishable from having sent no override at all.
    """
    unit_id = register_ready_unit(dispatch_client, key=f"reasonless-{len(override)}-{override}")

    response = dispatch_client.post(
        f"/api/v1/work-units/{unit_id}/dispatch",
        headers=SYSTEM,
        json={
            "idempotency_key": f"reasonless-{unit_id}",
            "expected_version": 2,
            "runner_attempt": 1,
            "change_window_override": override,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "change_window_override_reason_required"
    assert FakeGitHubActionsDispatcher.calls == []


def test_a_reasonless_override_is_refused_before_an_idempotent_replay_can_answer_it(
    dispatch_client: TestClient,
) -> None:
    """The refusal happens in the route, before either service is entered.

    A guard placed inside the service would sit BELOW the idempotency lookup, so a malformed
    request reusing a spent key would be answered with the earlier record at HTTP 200 -- shaped
    exactly like a successful run.
    """
    unit_id = register_ready_unit(dispatch_client, key="reasonless-after-success")
    body = {
        "idempotency_key": "reasonless-after-success",
        "expected_version": 2,
        "runner_attempt": 1,
    }
    first = dispatch_client.post(
        f"/api/v1/work-units/{unit_id}/dispatch", headers=SYSTEM, json=body
    )
    assert first.status_code == 200

    replay = dispatch_client.post(
        f"/api/v1/work-units/{unit_id}/dispatch",
        headers=SYSTEM,
        json={**body, "change_window_override": {"reason": ""}},
    )

    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "change_window_override_reason_required"


def test_the_served_request_models_carry_the_override(client: TestClient) -> None:
    """A field the deployed image does not declare makes the override silently unsendable while
    every other post-release check passes, so the served document is what is asserted."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert "change_window_override" in schemas["DispatchCommandModel"]["properties"]
    assert "change_window_override" in schemas["PrMergeCommandModel"]["properties"]
    assert "reason" in schemas["ChangeWindowOverrideModel"]["properties"]


def test_a_reasoned_override_reaches_the_record_through_the_route(
    dispatch_client: TestClient,
) -> None:
    """End to end over HTTP, so the wire shape is proven rather than the service's."""
    unit_id = register_ready_unit(dispatch_client, key="reasoned-over-http")

    response = dispatch_client.post(
        f"/api/v1/work-units/{unit_id}/dispatch",
        headers=SYSTEM,
        json={
            "idempotency_key": "reasoned-over-http",
            "expected_version": 2,
            "runner_attempt": 1,
            "change_window_override": {"reason": "supervised build session"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "dispatched"
    record_id = response.json()["id"]
    listed = dispatch_client.get(
        f"/api/v1/work-units/{unit_id}/evidence-pack", headers=SYSTEM
    ).json()
    started = [event for event in listed["events"] if event["action"] == "dispatch.dispatched"]
    assert record_id
    assert [event["change_window_override"]["reason"] for event in started] == [
        "supervised build session"
    ]
