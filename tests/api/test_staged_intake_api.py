"""`POST /api/v1/staged-intakes`: the CLI's way to put an intake in front of a person (ADR-0006
amendment 1). SYSTEM only, and it registers nothing."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.api.schemas.intake import StagedIntakeResponse
from orchestrator.persistence.models import StagedPackageIntake, WorkPackageRevision
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, VERIFIER, WORKER
from tests.api.test_package_intake_api import intake_payload

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}

# Pinned: a response_model drops any key it does not declare, so the fields the CLI will read
# (the follow-up PR deep-links `review_path`) are held here rather than trusted to the service.
STAGED_RESPONSE_FIELDS = {
    "id",
    "state",
    "idempotency_key",
    "package_id",
    "revision",
    "staged_by",
    "staged_at",
    "registered_revision_id",
    "review_path",
}


def _body(**overrides: object) -> dict[str, object]:
    return intake_payload(idempotency_key=f"staged-api-{uuid.uuid4().hex}", **overrides)


def test_the_response_model_declares_exactly_the_pinned_fields() -> None:
    assert set(StagedIntakeResponse.model_fields) == STAGED_RESPONSE_FIELDS


def test_the_system_actor_stages_an_intake_and_is_sent_to_the_review_page(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    body = _body()

    response = db_client.post("/api/v1/staged-intakes", headers=SYSTEM, json=body)

    assert response.status_code == 201, response.text
    served = response.json()
    assert set(served) == STAGED_RESPONSE_FIELDS
    assert served["state"] == "staged"
    assert served["idempotency_key"] == body["idempotency_key"]
    assert served["package_id"] == "pkg-ws32"
    assert served["registered_revision_id"] is None
    assert served["review_path"] == f"/review/staged-intakes/{served['id']}"
    # Committed, and read back on a fresh session; and nothing was registered.
    with Session(migrated_engine) as session:
        staged = session.get(StagedPackageIntake, uuid.UUID(served["id"]))
        assert staged is not None and staged.payload["package_id"] == "pkg-ws32"
        # As sent: no field the caller left out is filled with a default, so the confirm
        # rebuilds the model the caller sent rather than a fuller one.
        assert set(staged.payload) == set(body)
        assert session.scalars(select(WorkPackageRevision)).all() == []


def test_a_restaged_body_replays_the_same_row(db_client: TestClient) -> None:
    body = _body()

    first = db_client.post("/api/v1/staged-intakes", headers=SYSTEM, json=body).json()
    second = db_client.post("/api/v1/staged-intakes", headers=SYSTEM, json=body).json()

    assert first["id"] == second["id"]


def test_a_different_body_under_a_staged_key_is_a_conflict(db_client: TestClient) -> None:
    body = _body()
    db_client.post("/api/v1/staged-intakes", headers=SYSTEM, json=body)

    response = db_client.post(
        "/api/v1/staged-intakes", headers=SYSTEM, json={**body, "revision": 2}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.parametrize("headers", [HUMAN, WORKER, VERIFIER, OBSERVER])
def test_every_other_actor_is_refused(db_client: TestClient, headers: dict[str, str]) -> None:
    response = db_client.post("/api/v1/staged-intakes", headers=headers, json=_body())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_a_body_the_registration_model_refuses_is_refused_at_staging(
    db_client: TestClient,
) -> None:
    body = _body()
    del body["acceptance_criteria"]

    response = db_client.post("/api/v1/staged-intakes", headers=SYSTEM, json=body)

    assert response.status_code == 422
