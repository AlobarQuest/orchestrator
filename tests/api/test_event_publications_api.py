import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Event, EventPublication
from tests.api.test_lifecycle_api import HUMAN
from tests.api.test_status_ledger_api import _register_ready_unit


def _event_for_ready_unit(db_client: TestClient, engine: Engine) -> Event:
    unit_id = _register_ready_unit(db_client, "event-publications")
    with Session(engine) as session:
        event = Event(
            actor_id="factory-runner",
            action="work_unit.transitioned",
            subject_type="work_unit",
            subject_id=uuid.UUID(unit_id),
            from_state="ready",
            to_state="submitted",
            payload={"version": 2},
            correlation_id=uuid.uuid4(),
            idempotency_key="event-publications-source",
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event


def test_event_publication_api_lists_and_queues_idempotently(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    event = _event_for_ready_unit(db_client, migrated_engine)

    first = db_client.post(
        "/api/v1/event-publications/queue",
        headers=HUMAN,
        json={
            "idempotency_key": "queue-event-publications",
            "expected_version": 0,
            "source_kind": "event",
            "source_id": str(event.id),
        },
    )
    second = db_client.post(
        "/api/v1/event-publications/queue",
        headers=HUMAN,
        json={
            "idempotency_key": "queue-event-publications-replay",
            "expected_version": 0,
            "source_kind": "event",
            "source_id": str(event.id),
        },
    )
    listed = db_client.get("/api/v1/event-publications", headers=HUMAN)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [first.json()[0]["id"]]
    row = first.json()[0]
    assert row["source_system"] == "orchestrator"
    assert row["source_kind"] == "event"
    assert row["source_id"] == str(event.id)
    assert row["source_action"] == "work_unit.transitioned"
    assert row["status"] == "pending"
    assert row["event_id"].startswith("evt-")


def test_event_publication_api_exports_snapshot(
    db_client: TestClient,
    migrated_engine: Engine,
    tmp_path: Path,
) -> None:
    event = _event_for_ready_unit(db_client, migrated_engine)
    queued = db_client.post(
        "/api/v1/event-publications/queue",
        headers=HUMAN,
        json={
            "idempotency_key": "queue-export-publications",
            "expected_version": 0,
            "source_kind": "event",
            "source_id": str(event.id),
        },
    )
    assert queued.status_code == 200
    output_path = tmp_path / "factory-events.jsonl"

    exported = db_client.post(
        "/api/v1/event-publications/export",
        headers=HUMAN,
        json={
            "idempotency_key": "export-event-publications",
            "expected_version": 0,
            "output_path": str(output_path),
        },
    )

    assert exported.status_code == 200
    assert output_path.read_text(encoding="utf-8")
    assert exported.json()[0]["status"] == "exported"
    assert exported.json()[0]["export_ref"] == str(output_path)


def test_event_publication_api_retries_without_lifecycle_mutation(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    event = _event_for_ready_unit(db_client, migrated_engine)
    queued = db_client.post(
        "/api/v1/event-publications/queue",
        headers=HUMAN,
        json={
            "idempotency_key": "queue-retry-publications",
            "expected_version": 0,
            "source_kind": "event",
            "source_id": str(event.id),
        },
    )
    assert queued.status_code == 200
    publication_id = queued.json()[0]["id"]
    with Session(migrated_engine) as session:
        publication = session.get(EventPublication, uuid.UUID(publication_id))
        assert publication is not None
        publication.status = "failed"
        publication.factory_event = None
        publication.last_error = "temporary"
        session.commit()

    retried = db_client.post(
        f"/api/v1/event-publications/{publication_id}/retry",
        headers=HUMAN,
        json={"idempotency_key": "retry-event-publication", "expected_version": 0},
    )

    assert retried.status_code == 200
    assert retried.json()["id"] == publication_id
    assert retried.json()["status"] == "pending"
    with Session(migrated_engine) as session:
        event_after = session.get(Event, event.id)
        assert event_after is not None
        assert event_after.to_state == "submitted"
