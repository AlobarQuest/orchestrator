import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    OBSERVATION_SEVERITIES,
    OBSERVATION_SOURCE_SYSTEMS,
    OBSERVATION_STATUSES,
    OBSERVATION_SUBJECT_TYPES,
    OBSERVATION_TRUST_CLASSIFICATIONS,
    OBSERVATION_TYPES,
    Event,
    Observation,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.production_drill_resources import (
    bind_created_drill_observation,
    require_production_drill_resource,
)
from orchestrator.services.release_artifacts import SHA256_DIGEST

IDEMPOTENCY_LOCK_NAMESPACE = 0x57533631
BWS_TOKEN_SHAPE = re.compile(r"\b0\.[0-9a-fA-F-]{36}\.[A-Za-z0-9_=-]{8,}")
ENVIRONMENT = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
SECRET_KEY_PARTS = (
    "api_key",
    "authorization",
    "bearer",
    "body",
    "credential",
    "instruction",
    "log",
    "password",
    "secret",
    "token",
)
MAX_FACT_BYTES = 4096
MAX_FACT_STRING = 512
MAX_SUMMARY = 512
MAX_REF = 512


@dataclass(frozen=True)
class ObservationCommand:
    actor: ActorContext
    source_system: str
    source_reference: str
    source_url: str | None
    trust_classification: str
    subject_type: str
    subject_reference: str
    environment: str | None
    observation_type: str
    status: str
    severity: str
    observed_at: datetime
    summary: str
    facts: dict[str, Any]
    payload_digest: str | None
    idempotency_key: str
    expected_version: int | None = None


@dataclass(frozen=True)
class ObservationFilters:
    source_system: str | None = None
    subject_type: str | None = None
    subject_reference: str | None = None
    observation_type: str | None = None
    environment: str | None = None
    observed_from: datetime | None = None
    observed_to: datetime | None = None


def record_observation(session: Session, command: ObservationCommand) -> Observation | DomainError:
    try:
        row, _ = _record_observation(session, command)
        if not session.info.get("production_drill_scenario_atomic"):
            session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "observation_conflict",
            "observation conflicts with an existing source reference",
            "verify normalized facts before retrying",
        )
    except Exception:
        session.rollback()
        raise


def record_production_drill_observation(
    session: Session, *, run_id: uuid.UUID, command: ObservationCommand
) -> Observation | DomainError:
    try:
        observation, created = _record_observation(session, command)
        if not created:
            require_production_drill_resource(session, run_id, "observation", observation.id)
        else:
            bind_created_drill_observation(session, run_id, observation)
        if not session.info.get("production_drill_scenario_atomic"):
            session.commit()
        return observation
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "observation_conflict",
            "observation conflicts with an existing source reference",
            "verify normalized facts before retrying",
        )
    except Exception:
        session.rollback()
        raise


def list_observations(
    session: Session,
    filters: ObservationFilters | None = None,
) -> tuple[Observation, ...]:
    stmt = select(Observation)
    if filters is not None:
        if filters.source_system is not None:
            stmt = stmt.where(Observation.source_system == filters.source_system)
        if filters.subject_type is not None:
            stmt = stmt.where(Observation.subject_type == filters.subject_type)
        if filters.subject_reference is not None:
            stmt = stmt.where(Observation.subject_reference == filters.subject_reference)
        if filters.observation_type is not None:
            stmt = stmt.where(Observation.observation_type == filters.observation_type)
        if filters.environment is not None:
            stmt = stmt.where(Observation.environment == filters.environment)
        if filters.observed_from is not None:
            stmt = stmt.where(Observation.observed_at >= filters.observed_from)
        if filters.observed_to is not None:
            stmt = stmt.where(Observation.observed_at <= filters.observed_to)
    stmt = stmt.order_by(Observation.observed_at, Observation.received_at, Observation.id)
    return tuple(session.scalars(stmt))


def canonical_fact_hash(command: ObservationCommand) -> str:
    payload = _fact_identity(_normalized_command(command))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _record_observation(session: Session, command: ObservationCommand) -> tuple[Observation, bool]:
    _authorize_actor(command.actor)
    command = _normalized_command(command)
    _validate_command(command)
    fact_hash = canonical_fact_hash(command)
    payload = _command_payload(command, fact_hash)
    _lock_idempotency_key(session, command.idempotency_key)

    existing = session.scalar(
        select(Observation).where(Observation.idempotency_key == command.idempotency_key)
    )
    if existing is not None:
        _validate_idempotent_replay(session, existing, payload)
        return existing, False

    event_with_key = session.scalar(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    )
    if event_with_key is not None:
        raise _idempotency_conflict()

    same_fact = session.scalar(
        select(Observation)
        .where(
            Observation.source_system == command.source_system,
            Observation.source_reference == command.source_reference,
            Observation.normalized_fact_hash == fact_hash,
        )
        .with_for_update()
    )
    if same_fact is not None:
        return same_fact, False

    same_source = session.scalar(
        select(Observation)
        .where(
            Observation.source_system == command.source_system,
            Observation.source_reference == command.source_reference,
        )
        .with_for_update()
    )
    if same_source is not None:
        raise DomainError(
            "observation_conflict",
            "source reference already has different normalized observation facts",
            "record an explicit supersession model before changing observations",
        )

    now = TransactionClock().now(session)
    observation_id = uuid.uuid4()
    event_id = uuid.uuid4()
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="observation.recorded",
            subject_type="observation",
            subject_id=observation_id,
            from_state=None,
            to_state=None,
            payload={"command": payload},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    row = Observation(
        id=observation_id,
        source_system=command.source_system,
        source_reference=command.source_reference,
        source_url=command.source_url,
        trust_classification=command.trust_classification,
        subject_type=command.subject_type,
        subject_reference=command.subject_reference,
        environment=command.environment,
        observation_type=command.observation_type,
        status=command.status,
        severity=command.severity,
        observed_at=command.observed_at,
        received_at=now,
        summary=command.summary,
        facts=command.facts,
        normalized_fact_hash=fact_hash,
        payload_digest=command.payload_digest,
        recorded_by=command.actor.actor_id,
        event_id=event_id,
        idempotency_key=command.idempotency_key,
    )
    session.add(row)
    session.flush()
    return row, True


def _authorize_actor(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may record observations",
            None,
        )


def _validate_command(command: ObservationCommand) -> None:
    if command.expected_version not in {None, 0}:
        raise DomainError(
            "version_conflict",
            "observation ingestion requires expected version 0",
            "reload",
            current_version=0,
        )
    if not command.idempotency_key.strip():
        raise DomainError("observation_invalid", "idempotency key is required", None)
    _validate_enum(command.source_system, OBSERVATION_SOURCE_SYSTEMS, "source_system")
    _validate_enum(
        command.trust_classification,
        OBSERVATION_TRUST_CLASSIFICATIONS,
        "trust_classification",
    )
    _validate_enum(command.subject_type, OBSERVATION_SUBJECT_TYPES, "subject_type")
    _validate_enum(command.observation_type, OBSERVATION_TYPES, "observation_type")
    _validate_enum(command.status, OBSERVATION_STATUSES, "status")
    _validate_enum(command.severity, OBSERVATION_SEVERITIES, "severity")
    if command.observed_at.tzinfo is None:
        raise DomainError("observation_invalid", "observed_at must include a timezone", None)
    for label, value in {
        "source_reference": command.source_reference,
        "subject_reference": command.subject_reference,
        "summary": command.summary,
    }.items():
        if not value.strip() or len(value) > (MAX_SUMMARY if label == "summary" else MAX_REF):
            raise DomainError("observation_invalid", f"{label} is invalid", None)
    if command.environment is not None and not ENVIRONMENT.fullmatch(command.environment):
        raise DomainError("observation_invalid", "environment is invalid", None)
    if command.payload_digest is not None and not SHA256_DIGEST.fullmatch(command.payload_digest):
        raise DomainError("observation_invalid", "payload_digest must be a sha256 digest", None)
    _validate_bounded_facts(command.facts)
    secret_path = _secret_metadata_path(_command_payload(command, canonical_fact_hash(command)))
    if secret_path is not None:
        raise DomainError(
            "observation_secret_rejected",
            f"observation contains secret-shaped content at {secret_path}",
            "store only bounded non-secret references and summaries",
        )


def _validate_enum(value: str, allowed: tuple[str, ...], label: str) -> None:
    if value not in allowed:
        raise DomainError("observation_invalid", f"{label} is unsupported", None)


def _validate_bounded_facts(facts: dict[str, Any]) -> None:
    if not isinstance(facts, dict) or not facts:
        raise DomainError("observation_invalid", "facts must be a non-empty object", None)
    _validate_fact_value(facts)
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_FACT_BYTES:
        raise DomainError("observation_invalid", "facts exceed bounded size", None)


def _validate_fact_value(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 64:
                raise DomainError("observation_invalid", "fact key is invalid", None)
            _validate_fact_value(child)
    elif isinstance(value, list):
        if len(value) > 30:
            raise DomainError("observation_invalid", "fact list is too large", None)
        for child in value:
            _validate_fact_value(child)
    elif isinstance(value, str):
        if len(value) > MAX_FACT_STRING:
            raise DomainError("observation_invalid", "fact string is too large", None)
    elif value is None or isinstance(value, bool | int | float):
        return
    else:
        raise DomainError("observation_invalid", "fact value is unsupported", None)


def _normalized_command(command: ObservationCommand) -> ObservationCommand:
    source_url = (
        _canonical_https_url(command.source_url, "source_url")
        if command.source_url is not None
        else None
    )
    environment = _optional_text(command.environment)
    return replace(
        command,
        source_system=command.source_system.strip(),
        source_reference=command.source_reference.strip(),
        source_url=source_url,
        trust_classification=command.trust_classification.strip(),
        subject_type=command.subject_type.strip(),
        subject_reference=command.subject_reference.strip(),
        environment=environment,
        observation_type=command.observation_type.strip(),
        status=command.status.strip(),
        severity=command.severity.strip(),
        summary=command.summary.strip(),
        facts=_normalize_json(command.facts),
        payload_digest=_optional_text(command.payload_digest),
        idempotency_key=command.idempotency_key.strip(),
    )


def _normalize_json(value: object) -> Any:
    if isinstance(value, dict):
        return {str(key).strip(): _normalize_json(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_json(child) for child in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _canonical_https_url(value: str, field: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise DomainError("observation_invalid", f"{field} must be a non-secret HTTPS URL", None)
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path or "", parsed.query, ""))


def _fact_identity(command: ObservationCommand) -> dict[str, Any]:
    return {
        "source_system": command.source_system,
        "source_reference": command.source_reference,
        "subject_type": command.subject_type,
        "subject_reference": command.subject_reference,
        "environment": command.environment,
        "observation_type": command.observation_type,
        "status": command.status,
        "severity": command.severity,
        "observed_at": command.observed_at.isoformat(),
        "summary": command.summary,
        "facts": command.facts,
        "payload_digest": command.payload_digest,
    }


def _command_payload(command: ObservationCommand, fact_hash: str) -> dict[str, object]:
    raw = asdict(command)
    actor = raw.pop("actor")
    raw["actor_id"] = actor["actor_id"]
    raw["actor_role"] = str(actor["role"])
    raw["observed_at"] = command.observed_at.isoformat()
    raw["normalized_fact_hash"] = fact_hash
    raw.pop("expected_version", None)
    return raw


def _secret_metadata_path(value: object, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if any(part in key_text.lower() for part in SECRET_KEY_PARTS):
                return child_path
            found = _secret_metadata_path(child, child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _secret_metadata_path(child, f"{path}[{index}]")
            if found is not None:
                return found
    elif isinstance(value, str):
        lowered = value.lower()
        if "authorization: bearer " in lowered or BWS_TOKEN_SHAPE.search(value):
            return path
    return None


def _validate_idempotent_replay(
    session: Session,
    row: Observation,
    payload: dict[str, object],
) -> None:
    event = session.get(Event, row.event_id)
    if (
        event is None
        or event.action != "observation.recorded"
        or event.subject_id != row.id
        or event.payload.get("command") != payload
    ):
        raise _idempotency_conflict()


def _lock_idempotency_key(session: Session, idempotency_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
        {
            "namespace": IDEMPOTENCY_LOCK_NAMESPACE,
            "idempotency_key": idempotency_key,
        },
    )


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different observation",
        "use a new idempotency key",
    )
