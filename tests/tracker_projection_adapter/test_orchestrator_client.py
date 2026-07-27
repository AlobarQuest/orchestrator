import httpx
import pytest

from tracker_projection_adapter.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    _is_allowed_write,
)


def _client(seen):
    def handler(request):
        seen.append(f"{request.method} {request.url.path}?{request.url.query.decode()}")
        if request.url.path == "/api/v1/status-ledger":
            return httpx.Response(
                200,
                json=[
                    {"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "ready"}
                ],
            )
        if request.url.path == "/api/v1/tracker-bindings":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/tracker-binding"):
            return httpx.Response(200, json={"work_unit_id": "u1"})
        return httpx.Response(404)

    return OrchestratorClient(
        base_url="https://sds.invalid",
        credential_key_id="orchestrator-system",
        token="fixture-token",
        transport=httpx.MockTransport(handler),
    )


def test_status_ledger_requests_include_inactive():
    seen = []
    rows = _client(seen).status_ledger()
    assert rows[0]["unit_key"] == "K-1"
    assert any("include_inactive=true" in s for s in seen)


def test_upsert_hits_the_allowed_write_path():
    seen = []
    _client(seen).upsert_tracker_binding(
        work_unit_id="123e4567-e89b-12d3-a456-426614174000",
        tracker_system="todoist",
        external_item_id="task-1",
        external_url=None,
        projected_state="ready",
        idempotency_key="k1",
    )
    assert any(
        "POST /api/v1/work-units/123e4567-e89b-12d3-a456-426614174000/tracker-binding" in s
        for s in seen
    )


def test_write_to_a_transition_path_is_forbidden():
    client = _client([])
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/123e4567-e89b-12d3-a456-426614174000/commands/ready", {})
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/observations", {})


def test_report_tracker_reconciliation_posts_to_the_allowed_endpoint():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "conditions_recorded": 0,
                "skipped_correlations": 0,
                "suppressed_duplicates": 0,
            },
        )

    client = OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-system",
        token="t",
        transport=httpx.MockTransport(handler),
    )
    client.report_tracker_reconciliation(
        observed_states=[
            {"tracker_system": "todoist", "external_item_id": "tid-1", "observed_completed": True}
        ],
        idempotency_key="k",
    )
    assert seen == [("POST", "/api/v1/reconciliation/tracker-detect")]


def test_write_surface_allows_only_the_two_report_only_endpoints():
    assert _is_allowed_write(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/tracker-binding"
    )
    assert _is_allowed_write("/api/v1/reconciliation/tracker-detect")
    for forbidden in (
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready",
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/evidence",
        "/api/v1/observations",
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/adjudications",
    ):
        assert not _is_allowed_write(forbidden)
