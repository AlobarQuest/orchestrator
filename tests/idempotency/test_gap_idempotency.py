"""AC-007: the paths that had NO duplicate-delivery test at all.

The coverage matrix exposed these three. AC-007 requires a regression test on every ingress
"added where one was missing" -- this is that. Each drives the real API, twice, with the same key.
"""

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Dependency, KnowledgePromotionProposal
from tests.api.test_knowledge_promotions_api import _record_observation, proposal_body
from tests.api.test_lifecycle_api import HUMAN, SYSTEM
from tests.api.test_status_ledger_api import _register_ready_unit


def _count(engine: Engine, model: type) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _dependency_body(key: str) -> dict[str, object]:
    return {
        "idempotency_key": key,
        "expected_version": 2,
        "kind": "external_system",
        "required_state_or_condition": "approved",
        "external_ref": "CAB-1",
    }


def test_a_duplicate_dependency_registration_writes_one_row(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _register_ready_unit(db_client, "idem-dependency")
    body = _dependency_body("idem-dep-1")

    first = db_client.post(f"/api/v1/work-units/{unit_id}/dependencies", headers=SYSTEM, json=body)
    second = db_client.post(f"/api/v1/work-units/{unit_id}/dependencies", headers=SYSTEM, json=body)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert _count(migrated_engine, Dependency) == 1


def test_a_duplicate_dependency_resolution_replays(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _register_ready_unit(db_client, "idem-dep-resolve")
    created = db_client.post(
        f"/api/v1/work-units/{unit_id}/dependencies",
        headers=SYSTEM,
        json=_dependency_body("idem-dep-2"),
    )
    assert created.status_code == 200
    dependency_id = created.json()["id"]
    body = {
        "idempotency_key": "idem-dep-resolve-1",
        "expected_version": 2,
        "status": "satisfied",
        "detail": {"note": "CAB approved"},
    }

    first = db_client.post(
        f"/api/v1/dependencies/{dependency_id}/resolve", headers=SYSTEM, json=body
    )
    second = db_client.post(
        f"/api/v1/dependencies/{dependency_id}/resolve", headers=SYSTEM, json=body
    )

    assert first.status_code == second.status_code == 200, (first.json(), second.json())
    assert first.json() == second.json()
    assert _count(migrated_engine, Dependency) == 1


def test_a_duplicate_knowledge_promotion_writes_one_row(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    body = proposal_body(_record_observation(db_client), key="idem-kp-1")

    first = db_client.post("/api/v1/knowledge-promotion-proposals", headers=HUMAN, json=body)
    second = db_client.post("/api/v1/knowledge-promotion-proposals", headers=HUMAN, json=body)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert _count(migrated_engine, KnowledgePromotionProposal) == 1
