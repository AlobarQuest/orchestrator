"""The read the machine-local binding lane asks, over the wire (ADR-0030).

Served through the API rather than only at the service, because that is where the producer reads
it: a response model DROPS every key it does not declare, so a field the service returns and the
model omits reaches the machine as absence. The end-to-end assertion here is the one that would
catch that; the service-level tests cannot.
"""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import UnitPrBinding, WorkUnit
from tests.api.test_lifecycle_api import HUMAN, SYSTEM

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}

REPOSITORY = "AlobarQuest/infraops-mcp-server"
HEAD_SHA = "fcc4f8811b51ea74293b79e16ddabc4250d00b41"
MERGE_COMMIT = "ac01f838fdc96e2ce3916f5a2601d3e9c232c064"
PR_NUMBER = 81
PACKAGE_HASH = "sha256:machine-activation-api"
CANDIDATES = "/api/v1/machine-activation-candidates"
AUTHORITY = {
    "capabilities": {"repo.edit": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 120},
    "constraints": {"target_repository": REPOSITORY},
}


def completed_unit(db_client: TestClient, migrated_engine: Engine, *, key: str = "activation-api"):
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-revision",
            "expected_version": 0,
            "package_id": f"{key}-package",
            "source_repository": REPOSITORY,
            "revision": 1,
            "content_hash": PACKAGE_HASH,
            "source_path": "intent.md",
            "source_commit": HEAD_SHA,
            "approved_by": "devon",
            "approved_at": datetime(2026, 8, 19, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {"acceptance_criteria": ["AC-001"]},
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
            "title": "Update eslint",
            "outcome": "The bump lands",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 8, 19, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    with Session(migrated_engine) as session:
        stored = session.get(WorkUnit, unit_id)
        assert stored is not None
        stored.state = WorkUnitState.COMPLETED
        session.add(UnitPrBinding(work_unit_id=stored.id, pr_number=PR_NUMBER, head_sha=HEAD_SHA))
        session.commit()
    return revision.json()["id"], unit_id


def record_landing(db_client: TestClient) -> None:
    reference = f"landing:{REPOSITORY}@{MERGE_COMMIT}"
    response = db_client.post(
        "/api/v1/observations",
        headers=OBSERVER,
        json={
            "idempotency_key": f"{reference}:1",
            "expected_version": 0,
            "source_system": "github",
            "source_reference": reference,
            "source_url": f"https://github.com/{REPOSITORY}/commit/{MERGE_COMMIT}",
            "trust_classification": "delivery_system",
            "subject_type": "repo",
            "subject_reference": REPOSITORY,
            "environment": None,
            "observation_type": "landing",
            "status": "observed",
            "severity": "info",
            "observed_at": datetime(2026, 8, 19, 21, 34, 18, tzinfo=UTC).isoformat(),
            "summary": f"{MERGE_COMMIT[:12]} landed on main of {REPOSITORY}",
            "facts": {
                "what_changed": {
                    "repository": REPOSITORY,
                    "base_ref": "main",
                    "commit": MERGE_COMMIT,
                    "head_commit": HEAD_SHA,
                    "pull_request": PR_NUMBER,
                    "title": "feat: implement SDS unit",
                    "files_changed": 2,
                    "files": ["package.json"],
                },
            },
            "payload_digest": None,
        },
    )
    assert response.status_code == 201, response.text


def test_the_candidates_route_declares_its_schema(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert CANDIDATES in document["paths"]
    assert "MachineActivationCandidateResponse" in document["components"]["schemas"]


def test_the_served_body_carries_every_field_the_producer_reads(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The end-to-end assertion. A response model drops what it does not declare, in silence."""
    _, unit_id = completed_unit(db_client, migrated_engine)
    record_landing(db_client)

    response = db_client.get(CANDIDATES, params={"repository": REPOSITORY}, headers=SYSTEM)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0]) == {
        "work_unit_id",
        "work_package_revision_id",
        "package_revision_hash",
        "unit_key",
        "source_repository",
        "pr_number",
        "source_commit",
        "merge_commit",
        "binding_id",
    }
    assert body[0]["work_unit_id"] == unit_id
    assert body[0]["merge_commit"] == MERGE_COMMIT
    assert body[0]["source_commit"] == HEAD_SHA
    assert body[0]["binding_id"] is None


def test_a_repository_with_no_confirmed_landings_answers_with_an_empty_list(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """200 and empty, never 404. A 404 would make the ordinary case look like a typo."""
    completed_unit(db_client, migrated_engine)

    response = db_client.get(CANDIDATES, params={"repository": REPOSITORY}, headers=SYSTEM)

    assert response.status_code == 200
    assert response.json() == []


def test_the_route_requires_a_repository(db_client: TestClient) -> None:
    assert db_client.get(CANDIDATES, headers=SYSTEM).status_code == 422


def test_the_route_requires_authentication(db_client: TestClient) -> None:
    assert db_client.get(CANDIDATES, params={"repository": REPOSITORY}).status_code == 401
