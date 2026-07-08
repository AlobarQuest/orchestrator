import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    Adjudication,
    ContextSnapshot,
    Event,
    EventPublication,
    Evidence,
    WorkPackage,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.event_publications import (
    deterministic_factory_event_id,
    export_event_publications,
    list_event_publications,
    map_source_fact,
    queue_event_publications,
    retry_event_publication,
)
from orchestrator.services.evidence import append_evidence
from orchestrator.services.lifecycle import ActorContext


def registered_worker() -> ActorContext:
    return ActorContext("factory-runner", ActorRole.WORKER)


def protocol_fixture_unit(session: Session) -> WorkUnit:
    package = WorkPackage(package_id="fixture-pkg", source_repository="owner/repo")
    revision = WorkPackageRevision(
        work_package=package,
        revision=1,
        content_hash="sha256:fixture",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=datetime(2026, 7, 6, tzinfo=UTC),
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={},
        authority_fingerprint="authority",
        registry_version=1,
        registered_by="human-1",
        intake_source="protocol_fixture",
    )
    session.add(revision)
    session.flush()
    unit = WorkUnit(
        unit_key="fixture-unit",
        work_package_revision_id=revision.id,
        title="Fixture unit",
        outcome="Fixture only",
        state="ready",
        decomposition_approved_by="human-1",
        decomposition_approved_at=datetime(2026, 7, 6, tzinfo=UTC),
        required_capability="repository_write",
        authority={
            "capabilities": {"repository_write": "allowed"},
            "budgets": {},
            "unknown_fields": [],
        },
        authority_fingerprint="authority",
    )
    session.add(unit)
    session.commit()
    return unit


def append_registered_evidence(session: Session, unit) -> Event:
    grant = claim_unit(session, unit.id, registered_worker(), "ws34-claim")
    assert isinstance(grant, LeaseGrant)
    evidence = append_evidence(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=grant.attempt,
        actor=registered_worker(),
        lease_token=grant.lease_token,
        evidence_type="test",
        stable_ref="artifact://ws34-result",
        payload={"exit_code": 0},
        source_revision="abc123",
        idempotency_key="ws34-evidence",
    )
    assert not isinstance(evidence, Exception)
    event = session.get(Event, evidence.event_id)
    assert event is not None
    return event


def test_maps_evidence_event_to_valid_factory_event(migrated_session: Session, ready_unit) -> None:
    event = append_registered_evidence(migrated_session, ready_unit)

    result = map_source_fact(migrated_session, "event", event.id)

    assert result.status == "pending"
    assert result.reason is None
    assert result.factory_event is not None
    factory_event = result.factory_event
    assert factory_event["schema"] == "factory-event/v1"
    assert factory_event["source"] == {
        "system": "orchestrator",
        "ref": f"orchestrator:event:{event.id}",
    }
    assert factory_event["actor"] == "factory-runner"
    assert factory_event["action"] == "orchestrator.evidence_recorded"
    assert factory_event["work_package"] == "pkg-1"
    assert factory_event["input_revision"] == "revision:1@sha256:sha256:one"
    assert factory_event["target"] == f"evidence:{event.subject_id}"
    assert factory_event["result"] == "success"
    assert factory_event["correlation_id"] == str(event.correlation_id)


def test_deterministic_event_id_is_stable_for_source_fact() -> None:
    source_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    assert deterministic_factory_event_id("evidence", source_id) == deterministic_factory_event_id(
        "evidence",
        source_id,
    )
    assert deterministic_factory_event_id("evidence", source_id) != deterministic_factory_event_id(
        "event",
        source_id,
    )


def test_maps_evidence_row_to_valid_factory_event(
    migrated_session: Session,
    ready_unit,
) -> None:
    event = append_registered_evidence(migrated_session, ready_unit)
    evidence = migrated_session.get(Evidence, event.subject_id)
    assert evidence is not None

    result = map_source_fact(migrated_session, "evidence", evidence.id)

    assert result.status == "pending"
    assert result.factory_event is not None
    assert result.factory_event["source"] == {
        "system": "orchestrator",
        "ref": f"orchestrator:evidence:{evidence.id}",
    }
    assert result.factory_event["action"] == "orchestrator.evidence_recorded"
    assert result.factory_event["target"] == f"evidence:{evidence.id}"


def test_maps_adjudication_and_context_rows_to_valid_factory_events(
    migrated_session: Session,
    ready_unit,
) -> None:
    failed_evidence = Evidence(
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://failed",
        source_revision="abc123",
        recorded_by="factory-runner",
        event_id=uuid.uuid4(),
        idempotency_key="ws34-failed-evidence",
    )
    migrated_session.add(failed_evidence)
    migrated_session.flush()
    adjudication = Adjudication(
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="waived",
        decided_by="devon",
        rationale="accepted",
        failed_evidence_id=failed_evidence.id,
        risk="bounded",
        follow_up="monitor",
        event_id=uuid.uuid4(),
    )
    snapshot = ContextSnapshot(
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        attempt=1,
        actor_id="factory-runner",
        actor_role="worker",
        context={"standing": "ok"},
        context_fingerprint="fingerprint",
        classification="accepted",
        decision="accepted",
        event_id=uuid.uuid4(),
        idempotency_key="ws34-context-direct",
    )
    migrated_session.add_all([adjudication, snapshot])
    migrated_session.commit()

    adjudication_result = map_source_fact(migrated_session, "adjudication", adjudication.id)
    context_result = map_source_fact(migrated_session, "context_snapshot", snapshot.id)

    assert adjudication_result.status == "pending"
    assert adjudication_result.factory_event is not None
    assert adjudication_result.factory_event["actor"] == "devon"
    assert adjudication_result.factory_event["action"] == "orchestrator.waiver_recorded"
    assert adjudication_result.factory_event["result"] == "success"
    assert context_result.status == "pending"
    assert context_result.factory_event is not None
    assert context_result.factory_event["actor"] == "factory-runner"
    assert context_result.factory_event["action"] == "orchestrator.context_preflight_recorded"


def test_unknown_current_actor_is_rejected_not_mapped(
    migrated_session: Session, ready_unit
) -> None:
    event = Event(
        actor_id="not-registered",
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=ready_unit.id,
        from_state="ready",
        to_state="submitted",
        payload={"version": ready_unit.version},
        correlation_id=uuid.uuid4(),
        idempotency_key="ws34-unknown-current",
    )
    migrated_session.add(event)
    migrated_session.commit()

    result = map_source_fact(migrated_session, "event", event.id)

    assert result.status == "rejected"
    assert result.factory_event is None
    assert result.reason is not None
    assert "unregistered actor" in result.reason


def test_unknown_protocol_fixture_actor_maps_to_unknown(migrated_session: Session) -> None:
    ready_unit = protocol_fixture_unit(migrated_session)
    event = Event(
        actor_id="legacy-worker",
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=ready_unit.id,
        from_state="ready",
        to_state="submitted",
        payload={"version": ready_unit.version},
        correlation_id=uuid.uuid4(),
        idempotency_key="ws34-unknown-fixture",
    )
    migrated_session.add(event)
    migrated_session.commit()

    result = map_source_fact(migrated_session, "event", event.id)

    assert result.status == "pending"
    assert result.factory_event is not None
    assert result.factory_event["actor"] == "unknown"
    assert result.factory_event["evidence"][0]["record"]["raw_actor_id"] == "legacy-worker"


def test_queue_event_publication_is_idempotent(
    migrated_session: Session,
    ready_unit,
) -> None:
    event = append_registered_evidence(migrated_session, ready_unit)

    first = queue_event_publications(migrated_session, source_kind="event", source_id=event.id)
    second = queue_event_publications(migrated_session, source_kind="event", source_id=event.id)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id
    assert first[0].status == "pending"
    assert first[0].event_id == deterministic_factory_event_id("event", event.id)
    assert len(list_event_publications(migrated_session)) == 1


def test_queue_records_skipped_and_rejected_source_facts(
    migrated_session: Session,
    ready_unit,
) -> None:
    skipped = Event(
        actor_id="factory-runner",
        action="internal.noop",
        subject_type="work_unit",
        subject_id=ready_unit.id,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key="ws34-skipped",
    )
    rejected = Event(
        actor_id="not-registered",
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=ready_unit.id,
        from_state="ready",
        to_state="submitted",
        payload={"version": ready_unit.version},
        correlation_id=uuid.uuid4(),
        idempotency_key="ws34-rejected",
    )
    migrated_session.add_all([skipped, rejected])
    migrated_session.commit()

    rows = (
        *queue_event_publications(migrated_session, source_kind="event", source_id=skipped.id),
        *queue_event_publications(migrated_session, source_kind="event", source_id=rejected.id),
    )

    assert [row.status for row in rows] == ["skipped", "rejected"]
    assert rows[0].skip_reason == "unmapped_local_action:internal.noop"
    assert rows[1].skip_reason is not None
    assert "unregistered actor" in rows[1].skip_reason
    assert rows[0].factory_event is None
    assert rows[1].factory_event is None


def test_export_writes_deterministic_snapshot_and_marks_rows_exported(
    migrated_session: Session,
    ready_unit,
    tmp_path: Path,
) -> None:
    event = append_registered_evidence(migrated_session, ready_unit)
    queued = queue_event_publications(migrated_session, source_kind="event", source_id=event.id)
    output_path = tmp_path / "factory-events.jsonl"

    exported = export_event_publications(migrated_session, output_path)
    first_content = output_path.read_text(encoding="utf-8")
    exported_again = export_event_publications(migrated_session, output_path)
    second_content = output_path.read_text(encoding="utf-8")

    assert [row.id for row in exported] == [queued[0].id]
    assert [row.id for row in exported_again] == [queued[0].id]
    assert first_content == second_content
    records = [json.loads(line) for line in first_content.splitlines()]
    assert [record["event_id"] for record in records] == [queued[0].event_id]
    row = migrated_session.get(EventPublication, queued[0].id)
    assert row is not None
    assert row.status == "exported"
    assert row.export_ref == str(output_path)
    assert row.attempt_count == 2


def test_failed_export_does_not_mutate_lifecycle_or_publication(
    migrated_session: Session,
    ready_unit,
    tmp_path: Path,
) -> None:
    event = append_registered_evidence(migrated_session, ready_unit)
    queued = queue_event_publications(migrated_session, source_kind="event", source_id=event.id)[0]
    original_state = ready_unit.state
    missing_parent = tmp_path / "missing" / "factory-events.jsonl"

    with pytest.raises(OSError):
        export_event_publications(migrated_session, missing_parent)

    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    row = migrated_session.get(EventPublication, queued.id)
    assert unit is not None
    assert row is not None
    assert unit.state == original_state
    assert row.status == "pending"
    assert row.export_ref is None
    assert row.attempt_count == 0


def test_retry_recomputes_failed_publication_without_lifecycle_mutation(
    migrated_session: Session,
    ready_unit,
) -> None:
    event = append_registered_evidence(migrated_session, ready_unit)
    publication = queue_event_publications(
        migrated_session,
        source_kind="event",
        source_id=event.id,
    )[0]
    publication.status = "failed"
    publication.factory_event = None
    publication.last_error = "temporary"
    migrated_session.commit()

    retried = retry_event_publication(migrated_session, publication.id)

    assert retried.id == publication.id
    assert retried.status == "pending"
    assert retried.factory_event is not None
    assert retried.last_error is None
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert unit is not None
    assert unit.state == ready_unit.state
