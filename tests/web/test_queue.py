from fastapi.testclient import TestClient

from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN, WORKER


def test_queue_is_human_authenticated_and_grouped(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    denied = db_client.get("/review", headers=WORKER)
    page = db_client.get("/review", headers=HUMAN)

    assert denied.status_code == 403
    assert page.status_code == 200
    assert "Awaiting review" in page.text
    assert review_unit.title in page.text
    assert "readiness" in page.text.lower()
    assert "dependency_pending" in page.text
    assert "/review/units/" in page.text
