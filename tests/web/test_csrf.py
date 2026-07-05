from fastapi.testclient import TestClient

from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN


def test_human_mutation_rejects_missing_csrf_and_confirmation(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    response = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=HUMAN,
        data={
            "expected_version": str(review_unit.version),
            "reason": "No longer required",
        },
    )

    assert response.status_code == 403
