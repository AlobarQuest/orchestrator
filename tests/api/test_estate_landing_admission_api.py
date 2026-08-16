"""GET /api/v1/estate-pr-merge-admission -- the served surface of the composed answer.

**The shape pin lives beside the predicate it protects**, in the branch-update tests, because that
is where somebody adding a field to the answer is working. What it cannot show is the
one thing that matters to the caller: that the key is actually IN THE BODY. A `response_model`
drops what it does not declare silently, and this estate has shipped that defect once already --
so the model-versus-dataclass assertion and this request are two different claims, and only this
one is measured at the surface the reporting agent reads.

No network is reached. The client configures no App credentials, so the gateway raises before it
can send anything and every remote term refuses -- which is the answer under test here, since the
field must be served on a REFUSING answer as much as on a permitting one.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import SYSTEM


def _admission(db_client: TestClient) -> dict[str, Any]:
    response = db_client.get(
        "/api/v1/estate-pr-merge-admission",
        params={"repository": "alobarquest/brain", "pr_number": 31},
        headers=SYSTEM,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_body_carries_the_base_comparison_the_reporting_agent_classifies_on(
    db_client: TestClient,
) -> None:
    """ADR-0024. The agent cannot observe this for itself -- it reads no repository -- so an
    undeclared field would leave it falling back to its fail-toward-a-finding default forever,
    with every term on this side computing correctly and nothing on the wire."""
    body = _admission(db_client)

    assert "rollout_base_matches_pin" in body
    assert body["rollout_base_matches_pin"] is False


def test_the_body_carries_every_field_of_the_composed_answer(db_client: TestClient) -> None:
    """Stated as SET EQUALITY over the served keys rather than as a membership check for this
    increment's one field: the silent-drop hole belongs to the model, not to the field, and a
    membership check would not see the next addition fall through it."""
    from orchestrator.services.estate_landing_admission import EstateLandingAdmission

    assert set(_admission(db_client)) == set(EstateLandingAdmission.__dataclass_fields__)
