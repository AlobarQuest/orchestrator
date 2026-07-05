import re

from fastapi.testclient import TestClient

from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN


def test_detail_has_human_actions_but_no_worker_or_creation_controls(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    assert "Review outcome" in page.text
    assert "Cancel work unit" in page.text
    assert "Authorize retry" in page.text
    assert "Claim work" not in page.text
    assert "Create work unit" not in page.text
    assert "<label" in page.text


def test_review_form_uses_post_redirect_get(db_client: TestClient, review_unit: WorkUnit) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token is not None

    response = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={
            "csrf_token": token.group(1),
            "expected_version": str(review_unit.version),
            "outcome": "revision_required",
            "reason": "Needs another pass",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/review/units/{review_unit.id}"
