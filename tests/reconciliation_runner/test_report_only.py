"""AC-009: report-only is a property of WHICH ENDPOINT the runner may call."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from reconciliation_runner.cli import run_pass
from reconciliation_runner.client import (
    ALLOWED_WRITE_ENDPOINTS,
    ForbiddenEndpointError,
    ReconciliationClient,
)

HEAD = "a" * 40
UNIT = "3f6b6c68-0000-4000-8000-000000000001"
BINDING = "7c1c0f4a-0000-4000-8000-000000000002"
DIGEST = "sha256:" + "b" * 64

IN_FLIGHT = {
    "units": [
        {
            "work_unit_id": UNIT,
            "unit_key": "impl-1",
            "state": "submitted",
            "version": 4,
            "attempt_count": 1,
            "work_package_revision_id": UNIT,
            "pr_number": 41,
            "head_sha": HEAD,
            "verification_read_head_sha": HEAD,
        }
    ],
    "release_bindings": [
        {
            "binding_id": BINDING,
            "work_unit_id": UNIT,
            "work_unit_state": "completed",
            "source_repository": "AlobarQuest/orchestrator",
            "artifact_digest": DIGEST,
            "has_post_deploy_unit": False,
            "post_deploy_unit_state": None,
            "post_deploy_unit_created_at": None,
        }
    ],
}

REALITY = {
    "pull_requests": [
        {
            "work_unit_id": UNIT,
            "pr_number": 41,
            "head_sha": HEAD,
            "state": "closed",
            "merged": True,
            "updated_at": datetime(2026, 7, 10, 11, 0, tzinfo=UTC).isoformat(),
        }
    ],
    "check_runs": [
        {
            "work_unit_id": UNIT,
            "pr_number": 41,
            "check_name": "Quality",
            "head_sha": HEAD,
            "conclusion": "success",
            "completed_at": datetime(2026, 7, 10, 10, 0, tzinfo=UTC).isoformat(),
        }
    ],
    "deployments": [
        {
            "binding_id": BINDING,
            "artifact_digest": DIGEST,
            "deploy_status": "succeeded",
            "environment": "production",
            "completed_at": datetime(2026, 7, 10, 10, 30, tzinfo=UTC).isoformat(),
        }
    ],
}


def _client(seen: list[str]) -> ReconciliationClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v1/in-flight-units":
            return httpx.Response(200, json=IN_FLIGHT)
        if request.url.path == "/api/v1/observations":
            return httpx.Response(201, json={"id": UNIT})
        if request.url.path == "/api/v1/reconciliation/detect":
            return httpx.Response(
                200,
                json={
                    "conditions_recorded": 2,
                    "skipped_correlations": 0,
                    "suppressed_duplicates": 0,
                },
            )
        return httpx.Response(404)

    return ReconciliationClient(
        base_url="https://sds.invalid",
        credential_key_id="orchestrator-system",
        token="fixture-token",
        transport=httpx.MockTransport(handler),
    )


def test_a_full_pass_touches_exactly_the_two_allowed_write_endpoints(tmp_path: Path) -> None:
    reality = tmp_path / "reality.json"
    reality.write_text(json.dumps(REALITY))
    seen: list[str] = []

    summary = run_pass(_client(seen), reality, pass_id="pass-1")

    writes = {entry for entry in seen if entry.startswith("POST")}
    assert writes == {
        "POST /api/v1/observations",
        "POST /api/v1/reconciliation/detect",
    }
    # ...and that IS the allowlist, not a coincidence of this fixture.
    assert {f"POST {p}" for p in ALLOWED_WRITE_ENDPOINTS} == writes
    assert summary["observations_pushed"] == 3
    assert summary["conditions_recorded"] == 2


def test_the_deployment_endpoint_is_structurally_unreachable() -> None:
    """It mints a WorkUnit plus five Evidence rows per push, and its environment field admits
    arbitrary strings -- a SYSTEM-credentialed runner calling it could create unbounded post-deploy
    units. It would be setting canonical lifecycle state while calling itself a reporter."""
    client = _client([])

    with pytest.raises(ForbiddenEndpointError):
        client.post(f"/api/v1/release-artifacts/{BINDING}/deployment-observations", {})


def test_no_lifecycle_mutation_is_reachable() -> None:
    client = _client([])

    for path in (
        f"/api/v1/work-units/{UNIT}/commands/submit",
        f"/api/v1/work-units/{UNIT}/commands/complete",
        f"/api/v1/work-units/{UNIT}/evidence",
        f"/api/v1/work-units/{UNIT}/adjudications",
        f"/api/v1/work-units/{UNIT}/claim",
    ):
        with pytest.raises(ForbiddenEndpointError):
            client.post(path, {})
