import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, RuntimeObservation
from orchestrator.services.lifecycle import ActorContext

IDEMPOTENCY_LOCK_NAMESPACE = 0x57533534
RUNTIME_OBSERVATION_TARGET = "https://sds.alobar.net"
COOLIFY_APPLICATION_ID = "eqj5l7k705fhi12x9i74fqf0"
CONTAINER_ID = re.compile(r"^[a-f0-9]{12,64}$")
REPO_DIGEST = re.compile(r"^ghcr\.io/alobarquest/orchestrator@sha256:[0-9a-f]{64}$")
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_REF = re.compile(r"^ghcr\.io/alobarquest/orchestrator:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class RuntimeObservationCommand:
    actor: ActorContext
    container_id: str
    configured_image_ref: str
    observed_image_digest: str
    openapi_sha256: str
    observed_at: datetime
    idempotency_key: str
    expected_version: int | None = None


def record_runtime_observation(
    session: Session,
    command: RuntimeObservationCommand,
) -> RuntimeObservation | DomainError:
    try:
        _authorize_observer(command.actor)
        _validate_command(command)
        payload = _command_payload(command)
        _lock_idempotency_key(session, command.idempotency_key)

        existing = session.scalar(
            select(RuntimeObservation).where(
                RuntimeObservation.idempotency_key == command.idempotency_key
            )
        )
        if existing is not None:
            _validate_idempotent_replay(existing, payload)
            return existing
        if session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key)):
            raise _idempotency_conflict()

        now = TransactionClock().now(session)
        observation_id = uuid.uuid4()
        event_id = uuid.uuid4()
        session.add(
            Event(
                id=event_id,
                occurred_at=now,
                actor_id=command.actor.actor_id,
                action="runtime.observed",
                subject_type="runtime_observation",
                subject_id=observation_id,
                from_state=None,
                to_state=None,
                payload={"command": payload},
                correlation_id=uuid.uuid4(),
                idempotency_key=command.idempotency_key,
            )
        )
        row = RuntimeObservation(
            id=observation_id,
            target=RUNTIME_OBSERVATION_TARGET,
            coolify_application_id=COOLIFY_APPLICATION_ID,
            container_id=command.container_id,
            configured_image_ref=command.configured_image_ref,
            observed_image_digest=command.observed_image_digest,
            openapi_sha256=command.openapi_sha256,
            observed_at=command.observed_at,
            observer_actor_id=command.actor.actor_id,
            observer_credential_key_id=command.actor.credential_key_id,
            recorded_at=now,
            event_id=event_id,
            idempotency_key=command.idempotency_key,
        )
        session.add(row)
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "runtime_observation_conflict",
            "runtime observation conflicts with an immutable record",
            "use a new idempotency key for a distinct observation",
        )
    except Exception:
        session.rollback()
        raise


def get_runtime_observation(
    session: Session, observation_id: uuid.UUID
) -> RuntimeObservation | DomainError:
    row = session.get(RuntimeObservation, observation_id)
    if row is None:
        return DomainError(
            "runtime_observation_not_found", "runtime observation does not exist", None
        )
    return row


def _authorize_observer(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM or not actor.credential_key_id:
        raise DomainError(
            "role_forbidden",
            "only a pre-authorized runtime observer system credential may record runtime facts",
            None,
        )


def _validate_command(command: RuntimeObservationCommand) -> None:
    if command.expected_version not in {None, 0}:
        raise DomainError(
            "version_conflict", "runtime observation requires expected version 0", "reload"
        )
    if not command.idempotency_key.strip():
        raise DomainError("runtime_observation_invalid", "idempotency key is required", None)
    if not CONTAINER_ID.fullmatch(command.container_id):
        raise DomainError("runtime_observation_invalid", "container ID is invalid", None)
    if not IMAGE_REF.fullmatch(command.configured_image_ref):
        raise DomainError(
            "runtime_observation_invalid", "configured image reference is invalid", None
        )
    if not REPO_DIGEST.fullmatch(command.observed_image_digest):
        raise DomainError(
            "runtime_observation_invalid",
            "observed image must be an orchestrator RepoDigest",
            None,
        )
    if not SHA256_DIGEST.fullmatch(command.openapi_sha256):
        raise DomainError("runtime_observation_invalid", "OpenAPI digest must be sha256", None)
    if command.observed_at.tzinfo is None:
        raise DomainError(
            "runtime_observation_invalid", "observed_at must include a timezone", None
        )


def _command_payload(command: RuntimeObservationCommand) -> dict[str, object]:
    payload = asdict(command)
    payload.pop("actor")
    payload["observed_at"] = command.observed_at.astimezone(UTC).isoformat()
    payload["target"] = RUNTIME_OBSERVATION_TARGET
    payload["coolify_application_id"] = COOLIFY_APPLICATION_ID
    return payload


def _validate_idempotent_replay(
    existing: RuntimeObservation,
    payload: dict[str, object],
) -> None:
    expected = {
        "container_id": existing.container_id,
        "configured_image_ref": existing.configured_image_ref,
        "observed_image_digest": existing.observed_image_digest,
        "openapi_sha256": existing.openapi_sha256,
        "observed_at": existing.observed_at.astimezone(UTC).isoformat(),
        "idempotency_key": existing.idempotency_key,
        "expected_version": 0,
        "target": existing.target,
        "coolify_application_id": existing.coolify_application_id,
    }
    replay = dict(payload)
    replay["expected_version"] = replay["expected_version"] or 0
    if replay != expected:
        raise _idempotency_conflict()


def _lock_idempotency_key(session: Session, idempotency_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
        {"namespace": IDEMPOTENCY_LOCK_NAMESPACE, "idempotency_key": idempotency_key},
    )


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_key_reused",
        "idempotency key was already used with different runtime-observation facts",
        "use the original facts or a new idempotency key",
    )
