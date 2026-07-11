"""WS-P2.1 Task 9: the recover-evidence API surface (AC-004)."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Claim, Evidence, WorkUnit
from tests.api.test_lifecycle_api import SYSTEM, WORKER
from tests.api.test_status_ledger_api import _register_ready_unit


def _expired_claim(db_client: TestClient, migrated_engine: Engine, unit_id: str) -> int:
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": f"rc-claim-{unit_id}", "expected_version": 2},
    )
    assert claim.status_code == 200
    attempt = claim.json()["attempt"]
    with Session(migrated_engine) as session:
        row = session.scalar(select(Claim).where(Claim.work_unit_id == uuid.UUID(unit_id)))
        assert row is not None
        row.lease_expires_at = row.acquired_at  # the lease lapsed before submit
        session.commit()
    return attempt


def _body(unit_id: str, migrated_engine: Engine, key: str) -> dict[str, object]:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        revision_id = str(unit.work_package_revision_id)
        # Optimistic concurrency: the operator supplies the version they actually observed.
        version = unit.version
    return {
        "idempotency_key": key,
        "expected_version": version,
        "work_package_revision_id": revision_id,
        "ac_id": "ac-1",
        "evidence_type": "test",
        "stable_ref": "artifact://recovered",
        "payload": {"exit_code": 0},
        "source_revision": "abc123",
    }


def test_the_expired_worker_cannot_recover_its_own_evidence(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _register_ready_unit(db_client, "recover-role")
    attempt = _expired_claim(db_client, migrated_engine, unit_id)

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
        headers=WORKER,
        json=_body(unit_id, migrated_engine, "rc-role"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_recovery_attaches_the_evidence_to_the_prior_attempt(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _register_ready_unit(db_client, "recover-ok")
    attempt = _expired_claim(db_client, migrated_engine, unit_id)

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
        headers=SYSTEM,
        json=_body(unit_id, migrated_engine, "rc-ok"),
    )

    assert response.status_code == 201, response.json()
    assert response.json()["attempt"] == attempt
    with Session(migrated_engine) as session:
        rows = list(
            session.scalars(select(Evidence).where(Evidence.work_unit_id == uuid.UUID(unit_id)))
        )
        assert len(rows) == 1
        # Exactly one head: the chain was never forked.
        assert sum(1 for row in rows if row.supersedes_evidence_id is None) == 1
        # No new attempt was minted -- the budget is intact.
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        assert unit.attempt_count == 1
        assert unit.state == "failed"
