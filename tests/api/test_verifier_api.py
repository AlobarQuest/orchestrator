import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import PackageAcceptanceCriterion, WorkUnit
from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, VERIFIER, WORKER


def test_verify_api_declares_route(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/work-units/{unit_id}/verify" in document["paths"]
    assert "VerifyCommandModel" in document["components"]["schemas"]
    assert "VerifyResponse" in document["components"]["schemas"]


def test_worker_cannot_call_verify_api(db_client: TestClient) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "verify-api-revision",
            "expected_version": 0,
            "package_id": "verify-api",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": "sha256:verify-api",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
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
            "idempotency_key": "verify-api-unit",
            "expected_version": 0,
            "unit_key": "verify-api-unit",
            "title": "Verify API unit",
            "outcome": "verified",
            "required_capability": "repository_write",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201

    response = db_client.post(
        f"/api/v1/work-units/{unit.json()['id']}/verify",
        headers=WORKER,
        json={"idempotency_key": "verify-api-worker", "expected_version": 1},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_verifier_can_call_verify_api(db_client: TestClient, migrated_engine: Engine) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "verify-api-judgment-revision",
            "expected_version": 0,
            "package_id": "verify-api-judgment",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": "sha256:verify-api-judgment",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
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
            "idempotency_key": "verify-api-judgment-unit",
            "expected_version": 0,
            "unit_key": "verify-api-judgment-unit",
            "title": "Verify API judgment unit",
            "outcome": "verified",
            "required_capability": "repository_write",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    revision_id = revision.json()["id"]
    with Session(migrated_engine) as session:
        stored_unit = session.get(WorkUnit, unit_id)
        assert stored_unit is not None
        stored_unit.state = WorkUnitState.SUBMITTED
        session.add(
            PackageAcceptanceCriterion(
                work_package_revision_id=revision_id,
                ac_id="ac-1",
                condition="manual review complete",
                evidence_type="human.review",
                evidence="review evidence",
                approver="verifier",
            )
        )
        session.commit()

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/verify",
        headers=VERIFIER,
        json={"idempotency_key": "verify-api-judgment", "expected_version": 1},
    )

    assert response.status_code == 200
    assert response.json()["unit_id"] == unit_id
    assert response.json()["result"] == "awaiting_review"
    assert response.json()["state"] == "awaiting_review"
