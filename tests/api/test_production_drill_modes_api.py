from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_session
from orchestrator.config import ProductionDrillMode
from orchestrator.main import create_app

RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
RESOURCE_ID = UUID("22222222-2222-2222-2222-222222222222")
INVALID_CREDENTIAL = {
    "Authorization": "Bearer invalid",
    "X-Credential-Key-Id": "invalid-key",
}


@dataclass(frozen=True)
class RouteOperation:
    name: str
    method: str
    path: str
    body: dict[str, Any] | None
    available_modes: frozenset[ProductionDrillMode]


@dataclass(frozen=True)
class ModeClient:
    mode: ProductionDrillMode
    client: TestClient


SCHEMA_MODES = frozenset({ProductionDrillMode.STANDBY, ProductionDrillMode.ENABLED})
ENABLED_MODE = frozenset({ProductionDrillMode.ENABLED})
OPERATIONS = (
    RouteOperation(
        "runtime observation",
        "POST",
        "/api/v1/runtime-observations",
        {
            "idempotency_key": "mode-runtime-observation",
            "expected_version": 0,
            "container_id": "container",
            "configured_image_ref": "ghcr.io/example/orchestrator:production",
            "observed_image_digest": "ghcr.io/example/orchestrator@sha256:" + "a" * 64,
            "openapi_sha256": "sha256:" + "b" * 64,
            "observed_at": datetime(2026, 7, 13, tzinfo=UTC).isoformat(),
        },
        ENABLED_MODE,
    ),
    RouteOperation(
        "start",
        "POST",
        "/api/v1/production-drills",
        {
            "idempotency_key": "mode-start",
            "expected_version": 0,
            "revision_id": str(RESOURCE_ID),
            "runtime_observation_id": str(RESOURCE_ID),
        },
        ENABLED_MODE,
    ),
    RouteOperation("get run", "GET", f"/api/v1/production-drills/{RUN_ID}", None, SCHEMA_MODES),
    RouteOperation(
        "get state",
        "GET",
        f"/api/v1/production-drills/{RUN_ID}/state",
        None,
        SCHEMA_MODES,
    ),
    RouteOperation(
        "scenario",
        "POST",
        f"/api/v1/production-drills/{RUN_ID}/scenarios/crash_recovery",
        {"idempotency_key": "mode-scenario", "expected_version": 0},
        ENABLED_MODE,
    ),
    RouteOperation(
        "fail",
        "POST",
        f"/api/v1/production-drills/{RUN_ID}/fail",
        {
            "idempotency_key": "mode-fail",
            "expected_version": 0,
            "failure_code": "crash_recovery_failed",
            "diagnostic_ref": "drill://redacted/mode-fail",
        },
        ENABLED_MODE,
    ),
    RouteOperation(
        "human close",
        "POST",
        f"/api/v1/production-drills/{RUN_ID}/close",
        {
            "idempotency_key": "mode-close",
            "expected_version": 0,
            "closure_reason": "mode test",
        },
        SCHEMA_MODES,
    ),
)


@pytest.fixture(params=list(ProductionDrillMode), ids=lambda mode: mode.value)
def mode_client(request: pytest.FixtureRequest) -> Iterator[ModeClient]:
    mode = ProductionDrillMode(request.param)
    application = create_app(production_drill_mode=mode)

    def fail_if_entered() -> Iterator[Session]:
        raise AssertionError("route availability did not precede session access")
        yield

    application.dependency_overrides[get_session] = fail_if_entered
    with TestClient(application, raise_server_exceptions=False) as client:
        yield ModeClient(mode, client)


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda operation: operation.name)
def test_production_drill_route_availability_precedes_authentication_and_session(
    mode_client: ModeClient,
    operation: RouteOperation,
) -> None:
    response = mode_client.client.request(
        operation.method,
        operation.path,
        headers=INVALID_CREDENTIAL,
        json=operation.body,
    )

    if mode_client.mode in operation.available_modes:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
    else:
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "production_drill_unavailable"
