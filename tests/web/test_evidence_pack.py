from fastapi.testclient import TestClient

from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN


def test_evidence_pack_is_read_only_and_shows_canonical_provenance(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)

    assert page.status_code == 200
    assert "Evidence Pack" in page.text
    assert "Source commit" in page.text
    assert "Authority" in page.text
    assert "Event history" in page.text
    assert "<form" not in page.text


def test_evidence_pack_has_no_post_route(db_client: TestClient, review_unit: WorkUnit) -> None:
    response = db_client.post(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)

    assert response.status_code == 405
