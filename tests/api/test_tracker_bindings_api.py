"""POST /work-units/{unit_id}/tracker-binding + GET /tracker-bindings (WS-P2.7).

Mirrors the pr-binding route's auth shape: SYSTEM-only write, auth-only read, expected_version
must be 0. The binding is a projection record only -- it never touches the unit's lifecycle
state, so no approval/ready dance is needed to exercise it; a bare draft unit is enough.
"""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, SYSTEM, WORKER


def _make_work_unit(db_client: TestClient, suffix: str = "") -> str:
    key_suffix = f"-{suffix}" if suffix else ""
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"tracker-binding-revision{key_suffix}",
            "expected_version": 0,
            "package_id": f"tracker-binding{key_suffix}",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": f"sha256:tracker-binding{key_suffix}",
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
            "idempotency_key": f"tracker-binding-unit{key_suffix}",
            "expected_version": 0,
            "unit_key": f"tracker-binding-unit{key_suffix}",
            "title": f"Tracker binding unit{key_suffix}",
            "outcome": "Unit is projectable onto a tracker",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    return unit.json()["id"]


def _binding_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "tracker_system": "todoist",
        "external_item_id": "task-1",
        "external_url": None,
        "projected_state": "ready",
        "idempotency_key": "tracker-binding-k1",
        "expected_version": 0,
    }
    body.update(overrides)
    return body


def test_system_can_upsert_and_anyone_authed_can_list(db_client: TestClient) -> None:
    unit_id = _make_work_unit(db_client, "list")

    resp = db_client.post(
        f"/api/v1/work-units/{unit_id}/tracker-binding",
        headers=SYSTEM,
        json=_binding_body(),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["external_item_id"] == "task-1"
    assert resp.json()["work_unit_id"] == unit_id

    listing = db_client.get("/api/v1/tracker-bindings", headers=SYSTEM)
    assert listing.status_code == 200
    assert any(row["work_unit_id"] == unit_id for row in listing.json())


def test_worker_can_read_tracker_bindings(db_client: TestClient) -> None:
    unit_id = _make_work_unit(db_client, "worker-read")

    resp = db_client.post(
        f"/api/v1/work-units/{unit_id}/tracker-binding",
        headers=SYSTEM,
        json=_binding_body(idempotency_key="tracker-binding-worker-read"),
    )
    assert resp.status_code == 200, resp.text

    listing = db_client.get("/api/v1/tracker-bindings", headers=WORKER)
    assert listing.status_code == 200
    assert any(row["work_unit_id"] == unit_id for row in listing.json())


def test_unauthenticated_post_is_401(db_client: TestClient) -> None:
    unit_id = _make_work_unit(db_client, "unauth")

    resp = db_client.post(
        f"/api/v1/work-units/{unit_id}/tracker-binding",
        json=_binding_body(),
    )

    assert resp.status_code == 401


def test_worker_post_is_403(db_client: TestClient) -> None:
    unit_id = _make_work_unit(db_client, "worker")

    resp = db_client.post(
        f"/api/v1/work-units/{unit_id}/tracker-binding",
        headers=WORKER,
        json=_binding_body(),
    )

    assert resp.status_code == 403


def test_nonzero_expected_version_is_409(db_client: TestClient) -> None:
    unit_id = _make_work_unit(db_client, "conflict")

    resp = db_client.post(
        f"/api/v1/work-units/{unit_id}/tracker-binding",
        headers=SYSTEM,
        json=_binding_body(expected_version=3),
    )

    assert resp.status_code == 409
