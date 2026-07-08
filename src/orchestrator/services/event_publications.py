from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    Adjudication,
    ContextSnapshot,
    DeploymentObservation,
    Event,
    EventPublication,
    Evidence,
    Observation,
    ReleaseArtifactBinding,
    WorkPackage,
    WorkPackageRevision,
    WorkUnit,
)

SOURCE_SYSTEM = "orchestrator"
MAPPING_VERSION = "ws34.v1"


@dataclass(frozen=True)
class MappingResult:
    status: str
    event_id: str
    factory_event: dict[str, Any] | None
    reason: str | None = None


@dataclass(frozen=True)
class EventPublicationFilters:
    source_kind: str | None = None
    source_id: uuid.UUID | None = None
    status: str | None = None


@dataclass(frozen=True)
class _SourceContext:
    work_package: str | None
    input_revision: str | None
    target: str | None
    fixture_or_historical: bool


def deterministic_factory_event_id(source_kind: str, source_id: uuid.UUID) -> str:
    return _factory_events_envelope().deterministic_event_id(
        SOURCE_SYSTEM,
        f"{MAPPING_VERSION}:{source_kind}:{source_id}",
    )


def map_source_fact(session: Session, source_kind: str, source_id: uuid.UUID) -> MappingResult:
    event_id = deterministic_factory_event_id(source_kind, source_id)
    if source_kind == "evidence":
        return _map_evidence_fact(session, source_id, event_id)
    if source_kind == "adjudication":
        return _map_adjudication_fact(session, source_id, event_id)
    if source_kind == "context_snapshot":
        return _map_context_snapshot_fact(session, source_id, event_id)
    if source_kind != "event":
        return MappingResult("skipped", event_id, None, f"unsupported_source_kind:{source_kind}")
    return _map_event_fact(session, source_id, event_id)


def _map_event_fact(session: Session, source_id: uuid.UUID, event_id: str) -> MappingResult:
    event = session.get(Event, source_id)
    if event is None:
        return MappingResult("rejected", event_id, None, "source event not found")
    action = _factory_action(event)
    if action is None:
        return MappingResult("skipped", event_id, None, f"unmapped_local_action:{event.action}")

    source_context = _source_context_for_event(session, event)
    actor = _mapped_actor(event.actor_id, source_context.fixture_or_historical)
    if actor is None:
        return MappingResult(
            "rejected",
            event_id,
            None,
            f"unregistered actor {event.actor_id!r}",
        )

    envelope = _factory_events_envelope()
    factory_event = envelope.make_event(
        actor=actor,
        action=action,
        result=_factory_result(event),
        source={"system": SOURCE_SYSTEM, "ref": _source_ref("event", source_id)},
        timestamp=_format_timestamp(event.occurred_at),
        target=source_context.target,
        work_package=source_context.work_package,
        input_revision=source_context.input_revision,
        evidence=[_mapping_evidence(event)],
        authority_grant=_authority_grant(event),
        correlation_id=str(event.correlation_id),
        event_id=event_id,
    )
    return MappingResult("pending", event_id, factory_event)


def _map_evidence_fact(session: Session, source_id: uuid.UUID, event_id: str) -> MappingResult:
    evidence = session.get(Evidence, source_id)
    if evidence is None:
        return MappingResult("rejected", event_id, None, "source evidence not found")
    source_context = _source_context_for_revision(
        session,
        evidence.work_package_revision_id,
        target=f"evidence:{evidence.id}",
    )
    actor = _mapped_actor(evidence.recorded_by, source_context.fixture_or_historical)
    if actor is None:
        return MappingResult(
            "rejected",
            event_id,
            None,
            f"unregistered actor {evidence.recorded_by!r}",
        )
    factory_event = _factory_events_envelope().make_event(
        actor=actor,
        action="orchestrator.evidence_recorded",
        result="success",
        source={"system": SOURCE_SYSTEM, "ref": _source_ref("evidence", source_id)},
        timestamp=_format_timestamp(evidence.recorded_at),
        target=source_context.target,
        work_package=source_context.work_package,
        input_revision=source_context.input_revision,
        evidence=[_evidence_row_record(evidence)],
        correlation_id=str(evidence.event_id),
        event_id=event_id,
    )
    return MappingResult("pending", event_id, factory_event)


def _map_adjudication_fact(session: Session, source_id: uuid.UUID, event_id: str) -> MappingResult:
    adjudication = session.get(Adjudication, source_id)
    if adjudication is None:
        return MappingResult("rejected", event_id, None, "source adjudication not found")
    source_context = _source_context_for_revision(
        session,
        adjudication.work_package_revision_id,
        target=f"adjudication:{adjudication.id}",
    )
    actor = _mapped_actor(adjudication.decided_by, source_context.fixture_or_historical)
    if actor is None:
        return MappingResult(
            "rejected",
            event_id,
            None,
            f"unregistered actor {adjudication.decided_by!r}",
        )
    action = (
        "orchestrator.waiver_recorded"
        if adjudication.outcome == "waived"
        else "orchestrator.adjudication_recorded"
    )
    factory_event = _factory_events_envelope().make_event(
        actor=actor,
        action=action,
        result="failure" if adjudication.outcome == "failed" else "success",
        source={"system": SOURCE_SYSTEM, "ref": _source_ref("adjudication", source_id)},
        timestamp=_format_timestamp(adjudication.decided_at),
        target=source_context.target,
        work_package=source_context.work_package,
        input_revision=source_context.input_revision,
        evidence=[_adjudication_row_record(adjudication)],
        correlation_id=str(adjudication.event_id),
        event_id=event_id,
    )
    return MappingResult("pending", event_id, factory_event)


def _map_context_snapshot_fact(
    session: Session,
    source_id: uuid.UUID,
    event_id: str,
) -> MappingResult:
    snapshot = session.get(ContextSnapshot, source_id)
    if snapshot is None:
        return MappingResult("rejected", event_id, None, "source context snapshot not found")
    source_context = _source_context_for_revision(
        session,
        snapshot.work_package_revision_id,
        target=f"context_snapshot:{snapshot.id}",
    )
    actor = _mapped_actor(snapshot.actor_id, source_context.fixture_or_historical)
    if actor is None:
        return MappingResult(
            "rejected",
            event_id,
            None,
            f"unregistered actor {snapshot.actor_id!r}",
        )
    action = (
        "orchestrator.context_update_accepted"
        if snapshot.classification == "same_scope"
        else "orchestrator.context_preflight_recorded"
    )
    factory_event = _factory_events_envelope().make_event(
        actor=actor,
        action=action,
        result=_context_result(snapshot),
        source={"system": SOURCE_SYSTEM, "ref": _source_ref("context_snapshot", source_id)},
        timestamp=_format_timestamp(snapshot.created_at),
        target=source_context.target,
        work_package=source_context.work_package,
        input_revision=source_context.input_revision,
        evidence=[_context_snapshot_row_record(snapshot)],
        correlation_id=str(snapshot.event_id),
        event_id=event_id,
    )
    return MappingResult("pending", event_id, factory_event)


def queue_event_publications(
    session: Session,
    *,
    source_kind: str | None = None,
    source_id: uuid.UUID | None = None,
) -> tuple[EventPublication, ...]:
    source_facts = _queue_source_facts(session, source_kind=source_kind, source_id=source_id)
    rows = tuple(_queue_one(session, kind, fact_id) for kind, fact_id in source_facts)
    session.commit()
    return rows


def list_event_publications(
    session: Session,
    filters: EventPublicationFilters | None = None,
) -> tuple[EventPublication, ...]:
    stmt = select(EventPublication)
    if filters is not None:
        if filters.source_kind is not None:
            stmt = stmt.where(EventPublication.source_kind == filters.source_kind)
        if filters.source_id is not None:
            stmt = stmt.where(EventPublication.source_id == filters.source_id)
        if filters.status is not None:
            stmt = stmt.where(EventPublication.status == filters.status)
    stmt = stmt.order_by(EventPublication.created_at, EventPublication.event_id)
    return tuple(session.scalars(stmt))


def retry_event_publication(session: Session, publication_id: uuid.UUID) -> EventPublication:
    publication = session.get(EventPublication, publication_id)
    if publication is None:
        raise DomainError("event_publication_not_found", "event publication does not exist", None)
    result = map_source_fact(session, publication.source_kind, publication.source_id)
    _apply_mapping_result(publication, result)
    publication.updated_at = _now()
    session.commit()
    return publication


def export_event_publications(session: Session, output_path: Path) -> tuple[EventPublication, ...]:
    rows = tuple(
        session.scalars(
            select(EventPublication)
            .where(
                EventPublication.status.in_(("pending", "failed", "exported")),
                EventPublication.factory_event.is_not(None),
            )
            .order_by(EventPublication.created_at, EventPublication.event_id)
        )
    )
    content = "".join(
        json.dumps(row.factory_event, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
    )
    temp_path = output_path.with_name(f"{output_path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(output_path)

    now = _now()
    for row in rows:
        row.status = "exported"
        row.export_ref = str(output_path)
        row.attempt_count += 1
        row.last_attempted_at = now
        row.updated_at = now
        row.last_error = None
    session.commit()
    return rows


def _queue_source_facts(
    session: Session,
    *,
    source_kind: str | None,
    source_id: uuid.UUID | None,
) -> tuple[tuple[str, uuid.UUID], ...]:
    if source_kind is not None and source_id is not None:
        return ((source_kind, source_id),)
    if source_kind not in {None, "event"}:
        return ()
    stmt = select(Event.id).order_by(Event.occurred_at, Event.id)
    if source_id is not None:
        stmt = stmt.where(Event.id == source_id)
    return tuple(("event", event_id) for event_id in session.scalars(stmt))


def _queue_one(session: Session, source_kind: str, source_id: uuid.UUID) -> EventPublication:
    existing = session.scalar(
        select(EventPublication).where(
            EventPublication.source_kind == source_kind,
            EventPublication.source_id == source_id,
            EventPublication.mapping_version == MAPPING_VERSION,
        )
    )
    if existing is not None:
        return existing

    result = map_source_fact(session, source_kind, source_id)
    publication = EventPublication(
        source_kind=source_kind,
        source_id=source_id,
        source_action=_source_action(session, source_kind, source_id),
        event_id=result.event_id,
        mapping_version=MAPPING_VERSION,
        status=result.status,
        skip_reason=result.reason,
        factory_event=result.factory_event,
    )
    session.add(publication)
    session.flush()
    return publication


def _apply_mapping_result(publication: EventPublication, result: MappingResult) -> None:
    publication.event_id = result.event_id
    publication.status = result.status
    publication.skip_reason = result.reason
    publication.factory_event = result.factory_event
    publication.last_error = None


def _source_action(session: Session, source_kind: str, source_id: uuid.UUID) -> str | None:
    if source_kind != "event":
        return None
    event = session.get(Event, source_id)
    return event.action if event is not None else None


def _factory_action(event: Event) -> str | None:
    if event.action == "evidence.recorded":
        return "orchestrator.evidence_recorded"
    if event.action == "release_artifact.bound":
        return "orchestrator.release_artifact_bound"
    if event.action == "deployment.observed":
        return "orchestrator.deployment_observed"
    if event.action == "post_deploy_verification.created":
        return "orchestrator.post_deploy_verification_created"
    if event.action == "observation.recorded":
        return "orchestrator.observation_recorded"
    if event.action == "work_unit.transitioned" and event.to_state == "submitted":
        return "orchestrator.work_unit_submitted"
    return None


def _factory_result(event: Event) -> str:
    if event.to_state == "failed":
        return "failure"
    return "success"


def _source_context_for_event(session: Session, event: Event) -> _SourceContext:
    target: str | None = f"{event.subject_type}:{event.subject_id}"
    revision = _revision_for_event_subject(session, event)
    fixture_or_historical = _is_historical_event(event)

    if revision is None:
        return _SourceContext(None, None, target, fixture_or_historical)
    package = session.get(WorkPackage, revision.work_package_id)
    return _SourceContext(
        work_package=package.package_id if package is not None else None,
        input_revision=f"revision:{revision.revision}@sha256:{revision.content_hash}",
        target=target,
        fixture_or_historical=fixture_or_historical or revision.intake_source == "protocol_fixture",
    )


def _revision_for_event_subject(session: Session, event: Event) -> WorkPackageRevision | None:
    if event.subject_type == "work_package_revision":
        return session.get(WorkPackageRevision, event.subject_id)
    revision_id = _revision_id_for_event_subject(session, event)
    if revision_id is not None:
        return session.get(WorkPackageRevision, revision_id)
    return None


def _revision_id_for_event_subject(session: Session, event: Event) -> uuid.UUID | None:
    if event.subject_type == "work_unit":
        row = session.get(WorkUnit, event.subject_id)
    elif event.subject_type == "evidence":
        row = session.get(Evidence, event.subject_id)
    elif event.subject_type == "adjudication":
        row = session.get(Adjudication, event.subject_id)
    elif event.subject_type == "context_snapshot":
        row = session.get(ContextSnapshot, event.subject_id)
    elif event.subject_type == "release_artifact_binding":
        row = session.get(ReleaseArtifactBinding, event.subject_id)
    elif event.subject_type == "deployment_observation":
        row = session.get(DeploymentObservation, event.subject_id)
    elif event.subject_type == "observation":
        row = session.get(Observation, event.subject_id)
    else:
        row = None
    return getattr(row, "work_package_revision_id", None)


def _source_context_for_revision(
    session: Session,
    revision_id: uuid.UUID,
    *,
    target: str,
) -> _SourceContext:
    revision = session.get(WorkPackageRevision, revision_id)
    if revision is None:
        return _SourceContext(None, None, target, False)
    package = session.get(WorkPackage, revision.work_package_id)
    return _SourceContext(
        work_package=package.package_id if package is not None else None,
        input_revision=f"revision:{revision.revision}@sha256:{revision.content_hash}",
        target=target,
        fixture_or_historical=revision.intake_source == "protocol_fixture",
    )


def _mapped_actor(actor_id: str, fixture_or_historical: bool) -> str | None:
    if actor_id in _registered_actor_ids():
        return actor_id
    if actor_id == "system":
        return "unknown"
    if fixture_or_historical:
        return "unknown"
    return None


def _registered_actor_ids() -> frozenset[str]:
    _ensure_security_standards_on_path()
    registry = import_module("agent_registry.registry")
    return registry.registered_ids()


def _factory_events_envelope() -> Any:
    _ensure_security_standards_on_path()
    return import_module("factory_events.envelope")


def _ensure_security_standards_on_path() -> None:
    src = _security_standards_src()
    if src is not None and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _security_standards_src() -> Path | None:
    env_dir = os.environ.get("SECURITY_STANDARDS_DIR")
    if env_dir:
        candidate = Path(env_dir) / "src"
        return candidate if candidate.is_dir() else None
    candidate = Path(__file__).resolve().parents[3].parent / "security-standards" / "src"
    return candidate if candidate.is_dir() else None


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _source_ref(source_kind: str, source_id: uuid.UUID) -> str:
    return f"{SOURCE_SYSTEM}:{source_kind}:{source_id}"


def _mapping_evidence(event: Event) -> dict[str, Any]:
    record: dict[str, Any] = {
        "source_kind": "event",
        "source_id": str(event.id),
        "local_action": event.action,
        "subject_type": event.subject_type,
        "subject_id": str(event.subject_id),
        "from_state": event.from_state,
        "to_state": event.to_state,
        "raw_actor_id": event.actor_id,
    }
    if event.action == "observation.recorded":
        command = event.payload.get("command") if isinstance(event.payload, dict) else None
        if isinstance(command, dict):
            for key in (
                "source_system",
                "source_reference",
                "trust_classification",
                "subject_type",
                "subject_reference",
                "environment",
                "observation_type",
                "status",
                "severity",
                "observed_at",
                "normalized_fact_hash",
                "payload_digest",
            ):
                record[key] = command.get(key)
    return {
        "kind": "orchestrator_mapping_source",
        "record": record,
    }


def _evidence_row_record(evidence: Evidence) -> dict[str, Any]:
    return {
        "kind": "orchestrator_evidence",
        "record": {
            "source_kind": "evidence",
            "source_id": str(evidence.id),
            "ac_id": evidence.ac_id,
            "attempt": evidence.attempt,
            "evidence_type": evidence.evidence_type,
            "stable_ref": evidence.stable_ref,
            "payload": evidence.payload,
            "source_revision": evidence.source_revision,
            "raw_actor_id": evidence.recorded_by,
            "context_snapshot_id": (
                str(evidence.context_snapshot_id)
                if evidence.context_snapshot_id is not None
                else None
            ),
        },
    }


def _adjudication_row_record(adjudication: Adjudication) -> dict[str, Any]:
    return {
        "kind": "orchestrator_adjudication",
        "record": {
            "source_kind": "adjudication",
            "source_id": str(adjudication.id),
            "ac_id": adjudication.ac_id,
            "outcome": adjudication.outcome,
            "rationale": adjudication.rationale,
            "evidence_id": str(adjudication.evidence_id) if adjudication.evidence_id else None,
            "failed_evidence_id": (
                str(adjudication.failed_evidence_id) if adjudication.failed_evidence_id else None
            ),
            "risk": adjudication.risk,
            "follow_up": adjudication.follow_up,
            "scope": adjudication.scope,
            "raw_actor_id": adjudication.decided_by,
        },
    }


def _context_snapshot_row_record(snapshot: ContextSnapshot) -> dict[str, Any]:
    return {
        "kind": "orchestrator_context_snapshot",
        "record": {
            "source_kind": "context_snapshot",
            "source_id": str(snapshot.id),
            "attempt": snapshot.attempt,
            "actor_role": snapshot.actor_role,
            "context_fingerprint": snapshot.context_fingerprint,
            "classification": snapshot.classification,
            "decision": snapshot.decision,
            "approval_id": str(snapshot.approval_id) if snapshot.approval_id else None,
            "raw_actor_id": snapshot.actor_id,
        },
    }


def _context_result(snapshot: ContextSnapshot) -> str:
    if snapshot.decision == "accepted":
        return "success"
    if snapshot.decision == "rejected":
        return "failure"
    return "unknown"


def _authority_grant(event: Event) -> dict[str, Any] | None:
    authority = event.payload.get("authority_grant") if isinstance(event.payload, dict) else None
    return authority if isinstance(authority, dict) else None


def _is_historical_event(event: Event) -> bool:
    if not isinstance(event.payload, dict):
        return False
    return event.payload.get("historical_replay") is True


def _now() -> datetime:
    return datetime.now(UTC)
