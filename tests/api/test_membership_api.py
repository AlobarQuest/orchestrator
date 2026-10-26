"""`POST /api/v1/member-proposals`: SYSTEM stages a membership proposal for a person to confirm."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.schemas.membership import MemberProposalResponse
from orchestrator.persistence.models import SdsMember, SdsMemberProposal
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, VERIFIER, WORKER

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}

BODY = {
    "summary": "enroll brain",
    "changes": [
        {"repository": "AlobarQuest/brain", "reason": "r", "state": "onboarding", "owner": "devon"}
    ],
}


def _post(client: TestClient, headers: dict[str, str], key: str | None = None) -> object:
    headers = {**headers, "Idempotency-Key": key or f"m-{uuid.uuid4().hex}"}
    return client.post("/api/v1/member-proposals", headers=headers, json=BODY)


def test_the_response_model_declares_exactly_the_pinned_fields() -> None:
    assert set(MemberProposalResponse.model_fields) == {
        "id",
        "state",
        "review_path",
        "staged_by",
        "staged_at",
    }


def test_system_stages_through_the_api(db_client: TestClient, migrated_engine: Engine) -> None:
    response = _post(db_client, SYSTEM)

    assert response.status_code == 201, response.text  # type: ignore[attr-defined]
    served = response.json()  # type: ignore[attr-defined]
    assert served["state"] == "staged"
    assert served["review_path"] == f"/review/member-proposals/{served['id']}"
    with Session(migrated_engine) as session:
        assert session.get(SdsMemberProposal, uuid.UUID(served["id"])) is not None
        assert session.scalar(select(func.count()).select_from(SdsMember)) == 0


def test_observer_cannot_stage(db_client: TestClient) -> None:
    for headers in (OBSERVER, WORKER, VERIFIER, HUMAN):
        response = _post(db_client, headers)
        assert response.status_code == 403  # type: ignore[attr-defined]
        assert response.json()["error"]["code"] == "role_forbidden"  # type: ignore[attr-defined]


def test_a_missing_idempotency_key_is_refused(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/member-proposals", headers=SYSTEM, json=BODY)
    assert response.status_code == 422 or response.status_code == 400


def test_a_refused_body_surfaces_its_code(db_client: TestClient) -> None:
    bad = {"summary": "s", "changes": [{"repository": "nope", "reason": "r"}]}
    response = db_client.post(
        "/api/v1/member-proposals", headers={**SYSTEM, "Idempotency-Key": "bad"}, json=bad
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "member_repository_invalid"
