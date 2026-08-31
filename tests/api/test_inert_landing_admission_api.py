"""GET /api/v1/inert-pr-merge-admission -- the served surface of the composed answer.
ADR-0038 part 2.

**A `response_model` drops what it does not declare, silently and with no error**, so a field
added to the service alone passes every service-level assertion and reaches no caller. This estate
has shipped that defect twice: once on the runner brief, and once on this answer's own sibling,
where the enumerating agent read `branch_update_qualifies`, got nothing, and skipped every record
for two days while reporting zero. The set-equality assertion below is what makes the next addition
fall through visibly rather than quietly.

No network is reached. The client configures no App credentials, so the gateway raises before it
can send anything and every remote term refuses -- which is the answer under test, since these
fields must be served on a REFUSING answer as much as on a permitting one.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import SYSTEM

ROUTE = "/api/v1/inert-pr-merge-admission"


def _admission(db_client: TestClient) -> dict[str, Any]:
    response = db_client.get(
        ROUTE,
        params={"repository": "alobarquest/orchestrator", "pr_number": 3},
        headers=SYSTEM,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_body_carries_every_field_of_the_composed_answer(db_client: TestClient) -> None:
    """SET EQUALITY over the served keys rather than a membership check for one field: the
    silent-drop hole belongs to the model, not to any particular field."""
    from orchestrator.services.inert_landing_admission import InertLandingAdmission

    assert set(_admission(db_client)) == set(InertLandingAdmission.__dataclass_fields__)


def test_the_body_carries_the_branch_update_permission_a_freshening_pass_acts_on(
    db_client: TestClient,
) -> None:
    """The half of the answer that is not "may this land". A caller cannot compose it for itself,
    so undeclared it would read as absent and the freshening pass would skip every subject."""
    body = _admission(db_client)

    assert "branch_update_qualifies" in body
    assert body["branch_update_qualifies"] is False


def test_an_unconfigured_deployment_refuses_by_name_rather_than_erroring(
    db_client: TestClient,
) -> None:
    """Every refusal is a named string on a 200, never an exception: only `DomainError` and
    `APIAuthenticationError` have handlers, so anything else here would be a bare 500 from a gate
    that has stopped deciding."""
    body = _admission(db_client)

    assert body["satisfied"] is False
    assert "landing_not_enabled" in body["refusals"]
    assert "landing_app_credentials_missing" in body["refusals"]
    # The policy and the estate are both unconfigured in this client, and each says so in its own
    # words -- three causes, three different people.
    assert "inert_landing_policy_source_unconfigured" in body["refusals"]
    assert "landing_estate_source_unconfigured" in body["refusals"]


def test_the_route_reports_the_repository_it_was_asked_about_folded(
    db_client: TestClient,
) -> None:
    response = db_client.get(
        ROUTE,
        params={"repository": "AlobarQuest/Orchestrator", "pr_number": 3},
        headers=SYSTEM,
    )

    assert response.status_code == 200, response.text
    assert response.json()["repository"] == "alobarquest/orchestrator"


def test_a_repository_that_is_not_owner_slash_name_is_refused_by_the_schema(
    db_client: TestClient,
) -> None:
    """Bounded in SHAPE because it is interpolated into GitHub API paths called with the App
    installation token."""
    response = db_client.get(
        ROUTE, params={"repository": "../../etc", "pr_number": 3}, headers=SYSTEM
    )

    assert response.status_code == 422
