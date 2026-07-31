import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Adjudication, EventPublication, Evidence, WorkUnit
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


def test_evidence_pack_shows_read_only_event_publication_status(
    db_client: TestClient,
    migrated_engine: Engine,
    review_unit: WorkUnit,
) -> None:
    with Session(migrated_engine) as session:
        evidence = Evidence(
            work_package_revision_id=review_unit.work_package_revision_id,
            work_unit_id=review_unit.id,
            ac_id="ac-1",
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://published",
            source_revision="abc123",
            recorded_by="factory-runner",
            event_id=uuid.uuid4(),
            idempotency_key="web-publication-evidence",
        )
        session.add(evidence)
        session.flush()
        session.add(
            EventPublication(
                source_kind="evidence",
                source_id=evidence.id,
                source_action="evidence.recorded",
                event_id="evt-" + "a" * 64,
                mapping_version="ws34.v1",
                status="exported",
                factory_event={"schema": "factory-event/v1"},
                export_ref="/tmp/factory-events.jsonl",
            )
        )
        session.commit()
        source_ref = f"orchestrator:evidence:{evidence.id}"

    page = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)

    assert page.status_code == 200
    assert "Event publications" in page.text
    assert source_ref in page.text
    assert "exported" in page.text
    assert "evt-" + "a" * 64 in page.text
    assert "<form" not in page.text


def test_the_evidence_pack_renders_the_reference_and_the_payload(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    """AC-014. `stable_ref or payload` hid the content of every row that carried a reference --
    which is every row a runner writes."""
    with Session(migrated_engine) as session:
        session.add(
            Evidence(
                work_package_revision_id=review_unit.work_package_revision_id,
                work_unit_id=review_unit.id,
                ac_id="ac-1",
                attempt=1,
                evidence_type="test",
                stable_ref="artifact://both",
                payload={"detail": "collected 1795 items"},
                source_revision="abc123",
                recorded_by="worker",
                event_id=uuid.uuid4(),
                idempotency_key="web-evidence-both",
            )
        )
        session.commit()

    page = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)

    assert page.status_code == 200
    assert "artifact://both" in page.text
    assert "collected 1795 items" in page.text


def test_the_unit_page_renders_evidence_payload_content(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    """AC-015. The page CONTAINING the adjudication form rendered only `ac_id`, type and
    current/superseded -- so the only prose a reviewer read was the criterion's own condition
    text. "It had words that looked right" is what that produces."""
    with Session(migrated_engine) as session:
        session.add(
            Evidence(
                work_package_revision_id=review_unit.work_package_revision_id,
                work_unit_id=review_unit.id,
                ac_id="ac-1",
                attempt=1,
                evidence_type="test",
                stable_ref="artifact://unit-page",
                payload={"status": "pass", "detail": "836 passed, 0 failed"},
                source_revision="abc123",
                recorded_by="worker",
                event_id=uuid.uuid4(),
                idempotency_key="web-evidence-unit-page",
            )
        )
        session.commit()

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    assert "artifact://unit-page" in page.text
    assert "836 passed, 0 failed" in page.text


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
                risk="medium",
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
    assert "artifact://old</td><td>" in page.text
    assert "<td>superseded</td><td>test</td><td>artifact://old" in page.text
    assert "<td>current</td><td>test</td><td>artifact://current" in page.text
    assert "failed (superseded)" in page.text
    assert "waived (current)" in page.text
    assert failed_id in page.text
    assert "Risk: medium" in page.text
    assert "Follow-up: monitor" in page.text
    assert "Scope: ac-1" in page.text
