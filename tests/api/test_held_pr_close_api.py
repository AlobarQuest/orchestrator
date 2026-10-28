"""POST /api/v1/held-pr-close -- the route for bridge layer Part 4's close act.

No network is reached: the client configures no App credentials, so the re-composed admission
carries `landing_app_credentials_missing` and the act refuses before anything is sent. What is
under test is the wiring -- both lanes reach their service, the key is the pull request's, and
the refusal arrives as the act's own code.
"""

import pytest
from fastapi.testclient import TestClient

from orchestrator.services.landing.held_close import HELD_CLOSE_LANE_OFF
from tests.api.test_lifecycle_api import SYSTEM

ROUTE = "/api/v1/held-pr-close"
HEAD = "9f7f6ea6b3adde1cfc712f737647bc308cadb59a"


def _body(lane: str, **overrides: object) -> dict[str, object]:
    return {
        "lane": lane,
        "repository": "AlobarQuest/orchestrator",
        "pull_request_number": 3,
        "idempotency_key": "held-pr-close:alobarquest/orchestrator#3",
        "expected_head_sha": HEAD,
        **overrides,
    }


@pytest.mark.parametrize("lane", ["inert", "estate"])
def test_each_lane_reaches_its_act_and_refuses_with_no_credential(
    db_client: TestClient, lane: str
) -> None:
    response = db_client.post(ROUTE, json=_body(lane), headers=SYSTEM)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == HELD_CLOSE_LANE_OFF


def test_a_key_other_than_the_pull_requests_own_is_refused(db_client: TestClient) -> None:
    response = db_client.post(ROUTE, json=_body("inert", idempotency_key="other"), headers=SYSTEM)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "idempotency_conflict"


def test_a_lane_nobody_named_is_unusable_input(db_client: TestClient) -> None:
    response = db_client.post(ROUTE, json=_body("factory"), headers=SYSTEM)

    assert response.status_code == 422
