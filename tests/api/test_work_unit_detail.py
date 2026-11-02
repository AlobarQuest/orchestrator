"""What `GET /work-units/{id}` serves the verifier lane: the dispatch, the pull request, the head.

Read over the wire, because a response model drops every key it does not declare.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import DispatchRecord, UnitPrBinding
from tests._support.seeding import register_completed_unit

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}
REPOSITORY = "AlobarQuest/sds-canary"
HEAD = "fcc4f8811b51ea74293b79e16ddabc4250d00b41"
AUTHORITY = {
    "capabilities": {"repo.edit": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 120},
    "constraints": {"target_repository": REPOSITORY},
}


def _unit(db_client: TestClient, engine: Engine, key: str) -> tuple[str, str]:
    revision_id, unit_id = register_completed_unit(
        db_client,
        engine,
        key,
        authority=AUTHORITY,
        unit_key=key,
        title="Bump",
        outcome="The bump lands",
        source_repository=REPOSITORY,
        content_hash=f"sha256:{key}",
        source_commit=HEAD,
        enforcement_snapshot={"acceptance_criteria": ["AC-001"]},
    )
    return revision_id, unit_id


def _dispatch(session: Session, revision_id: str, unit_id: str, attempt: int, status: str):
    row = DispatchRecord(
        work_unit_id=uuid.UUID(unit_id),
        work_package_revision_id=uuid.UUID(revision_id),
        runner_attempt=attempt,
        status=status,
        idempotency_key=f"d-{unit_id}-{attempt}",
        target_repository=REPOSITORY,
        workflow_id="w.yml",
        workflow_ref="main",
        payload={},
    )
    session.add(row)
    session.commit()
    return row.id


def _get(db_client: TestClient, unit_id: str) -> dict:
    response = db_client.get(f"/api/v1/work-units/{unit_id}", headers=OBSERVER)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_unit_never_dispatched_or_bound_serves_nulls(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    _, unit_id = _unit(db_client, migrated_engine, "detail-bare")
    body = _get(db_client, unit_id)
    assert body["latest_dispatch_id"] is None
    assert body["pr_number"] is None
    assert body["armed_head_sha"] is None
    assert body["automated_check_ac_ids"] == []
    assert body["named_check_filed"] is False


def test_the_newest_dispatch_that_ran_wins(db_client: TestClient, migrated_engine: Engine) -> None:
    revision_id, unit_id = _unit(db_client, migrated_engine, "detail-dispatches")
    with Session(migrated_engine) as session:
        _dispatch(session, revision_id, unit_id, 1, "dispatched")
        newest = _dispatch(session, revision_id, unit_id, 2, "dispatched")
        _dispatch(session, revision_id, unit_id, 3, "skipped")
    assert _get(db_client, unit_id)["latest_dispatch_id"] == str(newest)


def test_the_bound_pull_request_and_armed_head_are_served(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    _, unit_id = _unit(db_client, migrated_engine, "detail-binding")
    with Session(migrated_engine) as session:
        session.add(
            UnitPrBinding(
                work_unit_id=uuid.UUID(unit_id),
                pr_number=7,
                head_sha="b" * 40,
                verification_read_head_sha=HEAD,
                verification_read_attempt=1,
            )
        )
        session.commit()
    body = _get(db_client, unit_id)
    assert body["pr_number"] == 7
    assert body["armed_head_sha"] == HEAD
