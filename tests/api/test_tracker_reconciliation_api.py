"""POST /api/v1/reconciliation/tracker-detect (WS-P2.7 Inc-2): inbound tracker reconciliation.

Mirrors /reconciliation/detect's auth shape (SYSTEM-only, report-only) and reuses
ReconciliationDetectResponse -- same counters shape, no new response model.
"""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, SYSTEM, WORKER


def _make_bound_unit(db_client: TestClient, suffix: str) -> str:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"tracker-detect-revision-{suffix}",
            "expected_version": 0,
            "package_id": f"tracker-detect-{suffix}",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": f"sha256:tracker-detect-{suffix}",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {"acceptance_criteria": ["ac-1"]},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": f"tracker-detect-unit-{suffix}",
            "expected_version": 0,
            "unit_key": f"tracker-detect-unit-{suffix}",
            "title": f"Tracker detect unit {suffix}",
            "outcome": "Unit is projectable onto a tracker",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    binding = db_client.post(
        f"/api/v1/work-units/{unit_id}/tracker-binding",
        headers=SYSTEM,
        json={
            "tracker_system": "todoist",
            "external_item_id": f"tid-{suffix}",
            "external_url": None,
            "projected_state": "ready",
            "idempotency_key": f"tracker-detect-binding-{suffix}",
            "expected_version": 0,
        },
    )
    assert binding.status_code == 200, binding.text
    return unit_id


def _body(items: list[dict[str, object]], key: str = "k1") -> dict[str, object]:
    return {"observed_states": items, "idempotency_key": key, "expected_version": 0}


def test_system_detect_returns_counters(db_client: TestClient) -> None:
    _make_bound_unit(db_client, "sys")

    response = db_client.post(
        "/api/v1/reconciliation/tracker-detect",
        headers=SYSTEM,
        json=_body(
            [
                {
                    "tracker_system": "todoist",
                    "external_item_id": "tid-sys",
                    "observed_completed": True,
                }
            ]
        ),
    )

    assert response.status_code == 200
    assert response.json() == {
        "conditions_recorded": 1,
        "skipped_correlations": 0,
        "suppressed_duplicates": 0,
    }


def test_worker_is_forbidden(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/reconciliation/tracker-detect",
        headers=WORKER,
        json=_body([]),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_human_is_forbidden(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/reconciliation/tracker-detect",
        headers=HUMAN,
        json=_body([]),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_nonzero_expected_version_rejected(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/reconciliation/tracker-detect",
        headers=SYSTEM,
        json={"observed_states": [], "idempotency_key": "k", "expected_version": 1},
    )

    assert response.status_code == 409
