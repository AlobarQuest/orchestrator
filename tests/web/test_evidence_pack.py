import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Adjudication, Evidence, WorkUnit
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


def test_evidence_pack_labels_supersession_and_named_waiver_facts(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        old = Evidence(
            work_package_revision_id=review_unit.work_package_revision_id,
            work_unit_id=review_unit.id,
            ac_id="ac-1",
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://old",
            source_revision="abc123",
            recorded_by="worker",
            event_id=uuid.uuid4(),
            idempotency_key="web-evidence-old",
        )
        session.add(old)
        session.flush()
        session.add(
            Evidence(
                work_package_revision_id=review_unit.work_package_revision_id,
                work_unit_id=review_unit.id,
                ac_id="ac-1",
                attempt=1,
                evidence_type="test",
                stable_ref="artifact://current",
                source_revision="abc123",
                recorded_by="worker",
                event_id=uuid.uuid4(),
                idempotency_key="web-evidence-current",
                supersedes_evidence_id=old.id,
            )
        )
        prior = Adjudication(
            work_package_revision_id=review_unit.work_package_revision_id,
            work_unit_id=review_unit.id,
            ac_id="ac-1",
            outcome="failed",
            decided_by="verifier",
            rationale="failed",
            event_id=uuid.uuid4(),
        )
        session.add(prior)
        session.flush()
        session.add(
            Adjudication(
                work_package_revision_id=review_unit.work_package_revision_id,
                work_unit_id=review_unit.id,
                ac_id="ac-1",
                outcome="waived",
                decided_by="devon",
                rationale="accepted",
                failed_evidence_id=old.id,
                risk="bounded",
                follow_up="monitor",
                scope="ac-1",
                event_id=uuid.uuid4(),
                supersedes_adjudication_id=prior.id,
            )
        )
        session.commit()
        failed_id = str(old.id)

    page = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)

    assert page.status_code == 200
    assert "artifact://old" in page.text and "superseded" in page.text
    assert "artifact://current" in page.text and "current" in page.text
    assert failed_id in page.text
    assert "Risk: bounded" in page.text
    assert "Follow-up: monitor" in page.text
    assert "Scope: ac-1" in page.text
