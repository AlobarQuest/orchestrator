"""WS-P2.8 Task 6: the follow-up minting pass's production entry point."""

from typing import get_args

from fastapi.testclient import TestClient

from orchestrator.api.schemas import SkippedRevisionResponse
from orchestrator.services import follow_ups
from tests.api.test_lifecycle_api import SYSTEM, WORKER


def test_mint_requires_the_system_actor(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/follow-ups/mint",
        headers=WORKER,
        json={"idempotency_key": "mint-1", "expected_version": 0},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_mint_rejects_a_non_zero_expected_version(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/follow-ups/mint",
        headers=SYSTEM,
        json={"idempotency_key": "mint-2", "expected_version": 3},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"


def test_mint_on_an_empty_ledger_returns_counted_nothing(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/follow-ups/mint",
        headers=SYSTEM,
        json={"idempotency_key": "mint-3", "expected_version": 0},
    )

    assert response.status_code == 200
    assert response.json() == {"minted": [], "skipped": [], "considered": 0}


def test_the_response_vocabulary_matches_the_services_skip_reasons() -> None:
    """The Literal is a second copy by necessity. This is what keeps it a copy and not a fork."""
    declared = set(get_args(SkippedRevisionResponse.model_fields["reason"].annotation))
    service = {
        value
        for name, value in vars(follow_ups).items()
        if name.startswith("SKIP_") and isinstance(value, str)
    }

    assert declared == service
