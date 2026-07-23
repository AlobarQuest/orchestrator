import uuid

from orchestrator.persistence.models import Event


def test_event_improvisation_defaults_false(migrated_session):
    event = Event(
        actor_id="worker-1",
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=uuid.uuid4(),
        from_state="claimed",
        to_state="executing",
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key="evt-improv-default",
    )
    migrated_session.add(event)
    migrated_session.flush()
    migrated_session.refresh(event)
    assert event.improvisation is False
