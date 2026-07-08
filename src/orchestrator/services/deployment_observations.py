import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import authority_fingerprint, normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    DeploymentObservation,
    Event,
    Evidence,
    ReleaseArtifactBinding,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.release_artifacts import SHA256_DIGEST

IDEMPOTENCY_LOCK_NAMESPACE = 0x57533533
ENVIRONMENT = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
BWS_TOKEN_SHAPE = re.compile(r"\b0\.[0-9a-fA-F-]{36}\.[A-Za-z0-9_=-]{8,}")
SECRET_KEY_PARTS = ("authorization", "password", "secret", "token")
MAX_FACT_STRING = 512


@dataclass(frozen=True)
class DeploymentObservationCommand:
    release_artifact_binding_id: uuid.UUID
    actor: ActorContext
    environment: str
    base_url: str
    observed_artifact_digest: str
    deployment_ref: str
    deployment_url: str
    deployer: str
    observed_at: datetime
    probe_summary: dict[str, Any]
    route_summary: dict[str, Any]
    auth_summary: dict[str, Any]
    dispatch_summary: dict[str, Any]
    status_summary: dict[str, Any]
    idempotency_key: str
    expected_version: int | None = None


def record_deployment_observation(
    session: Session,
    command: DeploymentObservationCommand,
) -> DeploymentObservation | DomainError:
    try:
        row = _record_deployment_observation(session, command)
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "deployment_observation_conflict",
            "deployment observation conflicts with an existing binding/environment",
            "verify deployment facts before retrying",
        )
    except Exception:
        session.rollback()
        raise


def list_deployment_observations(
    session: Session,
    release_artifact_binding_id: uuid.UUID,
) -> tuple[DeploymentObservation, ...] | DomainError:
    if session.get(ReleaseArtifactBinding, release_artifact_binding_id) is None:
        return DomainError("release_artifact_not_found", "release artifact binding does not exist", None)
    return tuple(
        session.scalars(
            select(DeploymentObservation)
            .where(DeploymentObservation.release_artifact_binding_id == release_artifact_binding_id)
            .order_by(DeploymentObservation.recorded_at, DeploymentObservation.id)
        )
    )


def _record_deployment_observation(
    session: Session,
    command: DeploymentObservationCommand,
) -> DeploymentObservation:
    _authorize_actor(command.actor)
    _validate_command_shape(command)
    payload = _command_payload(command)
    _lock_idempotency_key(session, command.idempotency_key)

    existing = session.scalar(
        select(DeploymentObservation).where(
            DeploymentObservation.idempotency_key == command.idempotency_key
        )
    )
    if existing is not None:
        _validate_idempotent_replay(session, existing, payload)
        return existing
    event_with_key = session.scalar(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    )
    if event_with_key is not None:
        raise _idempotency_conflict()

    binding, implementation_unit, revision = _validated_subject(session, command)
    same_environment = session.scalar(
        select(DeploymentObservation)
        .where(
            DeploymentObservation.release_artifact_binding_id == binding.id,
            DeploymentObservation.environment == command.environment,
        )
        .with_for_update()
    )
    if same_environment is not None:
        if _same_observation_facts(same_environment, command):
            return same_environment
        raise DomainError(
            "deployment_observation_conflict",
            "release binding and environment already have different deployment facts",
            "record an explicit supersession model before changing deployment observations",
        )

    now = TransactionClock().now(session)
    observation_id = uuid.uuid4()
    generated_unit = _post_deploy_work_unit(session, binding, command, now)
    evidence_ids = _deployment_evidence(
        session,
        command,
        binding,
        generated_unit,
        observation_id,
        now,
    )
    observed_event_id = uuid.uuid4()
    created_event_id = uuid.uuid4()
    session.add(
        Event(
            id=observed_event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="deployment.observed",
            subject_type="deployment_observation",
            subject_id=observation_id,
            from_state=implementation_unit.state,
            to_state=implementation_unit.state,
            payload={"command": payload, "evidence_ids": evidence_ids},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    session.add(
        Event(
            id=created_event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="post_deploy_verification.created",
            subject_type="work_unit",
            subject_id=generated_unit.id,
            from_state=None,
            to_state=generated_unit.state,
            payload={
                "deployment_observation_id": str(observation_id),
                "release_artifact_binding_id": str(binding.id),
                "environment": command.environment,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=f"{command.idempotency_key}:post-deploy-unit",
        )
    )
    session.flush()
    row = DeploymentObservation(
        id=observation_id,
        release_artifact_binding_id=binding.id,
        implementation_work_unit_id=implementation_unit.id,
        work_package_revision_id=revision.id,
        package_revision_hash=binding.package_revision_hash,
        post_deploy_work_unit_id=generated_unit.id,
        environment=command.environment,
        base_url=command.base_url,
        observed_artifact_digest=command.observed_artifact_digest,
        deployment_ref=command.deployment_ref,
        deployment_url=command.deployment_url,
        deployer=command.deployer,
        observed_at=command.observed_at,
        probe_summary=command.probe_summary,
        route_summary=command.route_summary,
        auth_summary=command.auth_summary,
        dispatch_summary=command.dispatch_summary,
        status_summary=command.status_summary,
        recorded_by=command.actor.actor_id,
        recorded_at=now,
        event_id=observed_event_id,
        post_deploy_event_id=created_event_id,
        evidence_ids=evidence_ids,
        idempotency_key=command.idempotency_key,
    )
    session.add(row)
    session.flush()
    return row


def _post_deploy_work_unit(
    session: Session,
    binding: ReleaseArtifactBinding,
    command: DeploymentObservationCommand,
    now: datetime,
) -> WorkUnit:
    unit_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"sds:post-deploy:{binding.id}:{command.environment}",
    )
    authority = normalize_authority(
        {
            "capabilities": {"post_deploy_verification": "allowed"},
            "budgets": {"max_attempts": 1},
            "unknown_fields": [],
        }
    )
    unit = WorkUnit(
        id=unit_id,
        work_package_revision_id=binding.work_package_revision_id,
        unit_key=f"post-deploy:{binding.id}:{command.environment}",
        title=f"Post-deploy verification for {command.environment}",
        outcome=f"Verify deployed artifact {binding.artifact_digest} at {command.base_url}",
        state=WorkUnitState.SUBMITTED,
        decomposition_approved_by=command.actor.actor_id,
        decomposition_approved_at=now,
        required_capability="post_deploy_verification",
        authority=authority.normalized(),
        authority_fingerprint=authority_fingerprint(authority),
        max_attempts=1,
    )
    session.add(unit)
    session.flush()
    return unit


def _deployment_evidence(
    session: Session,
    command: DeploymentObservationCommand,
    binding: ReleaseArtifactBinding,
    unit: WorkUnit,
    observation_id: uuid.UUID,
    now: datetime,
) -> list[str]:
    evidence_specs = (
        (
            "post-deploy-artifact",
            "release.deployment_observed",
            {
                "binding_id": str(binding.id),
                "release_artifact_digest": binding.artifact_digest,
                "observed_artifact_digest": command.observed_artifact_digest,
                "environment": command.environment,
                "base_url": command.base_url,
                "deployment_ref": command.deployment_ref,
                "deployment_url": command.deployment_url,
                "deployer": command.deployer,
                "observed_at": command.observed_at.isoformat(),
            },
        ),
        ("post-deploy-health", "production.health", command.probe_summary),
        ("post-deploy-routes", "production.route_presence", command.route_summary),
        ("post-deploy-auth", "production.auth_behavior", command.auth_summary),
        ("post-deploy-dispatch", "production.dispatch_posture", command.dispatch_summary),
    )
    evidence_ids: list[str] = []
    for ac_id, evidence_type, payload in evidence_specs:
        evidence = _system_evidence(
            session,
            command,
            binding,
            unit,
            observation_id,
            now,
            ac_id=ac_id,
            evidence_type=evidence_type,
            payload=payload,
        )
        evidence_ids.append(str(evidence.id))
    return evidence_ids


def _system_evidence(
    session: Session,
    command: DeploymentObservationCommand,
    binding: ReleaseArtifactBinding,
    unit: WorkUnit,
    observation_id: uuid.UUID,
    now: datetime,
    *,
    ac_id: str,
    evidence_type: str,
    payload: dict[str, Any],
) -> Evidence:
    evidence_id = uuid.uuid4()
    evidence_key = f"{command.idempotency_key}:evidence:{ac_id}"
    evidence = Evidence(
        id=evidence_id,
        work_package_revision_id=binding.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        attempt=1,
        evidence_type=evidence_type,
        stable_ref=f"orchestrator://deployment-observations/{observation_id}/{ac_id}",
        payload=payload,
        source_revision=binding.merge_commit,
        recorded_by=command.actor.actor_id,
        recorded_at=now,
        event_id=uuid.uuid4(),
        idempotency_key=evidence_key,
        supersedes_evidence_id=None,
        context_snapshot_id=None,
    )
    session.add(evidence)
    session.flush()
    session.add(
        Event(
            id=evidence.event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="evidence.recorded",
            subject_type="evidence",
            subject_id=evidence.id,
            from_state=None,
            to_state=None,
            payload={
                "command": {
                    "ac_id": ac_id,
                    "actor_id": command.actor.actor_id,
                    "actor_role": command.actor.role,
                    "attempt": 1,
                    "evidence_type": evidence_type,
                    "payload": payload,
                    "source_revision": binding.merge_commit,
                    "stable_ref": evidence.stable_ref,
                    "work_package_revision_id": str(binding.work_package_revision_id),
                    "work_unit_id": str(unit.id),
                }
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=evidence_key,
        )
    )
    session.flush()
    return evidence


def _validated_subject(
    session: Session,
    command: DeploymentObservationCommand,
) -> tuple[ReleaseArtifactBinding, WorkUnit, WorkPackageRevision]:
    binding = session.scalar(
        select(ReleaseArtifactBinding)
        .where(ReleaseArtifactBinding.id == command.release_artifact_binding_id)
        .with_for_update()
    )
    if binding is None:
        raise DomainError(
            "release_artifact_not_found",
            "release artifact binding does not exist",
            None,
        )
    unit = session.scalar(
        select(WorkUnit).where(WorkUnit.id == binding.work_unit_id).with_for_update()
    )
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    if WorkUnitState(unit.state) is not WorkUnitState.COMPLETED:
        raise DomainError(
            "work_unit_not_completed",
            "deployment observation requires a completed implementation work unit",
            "verify and complete the implementation work unit before observing deployment",
        )
    revision = session.get(WorkPackageRevision, binding.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    if revision.content_hash != binding.package_revision_hash:
        raise DomainError(
            "deployment_observation_revision_mismatch",
            "release binding package hash does not match approved revision",
            None,
        )
    if command.observed_artifact_digest != binding.artifact_digest:
        raise DomainError(
            "deployment_observation_digest_mismatch",
            "observed artifact digest does not match the immutable release binding",
            "record the deployment observation against the matching release artifact binding",
        )
    return binding, unit, revision


def _authorize_actor(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may record deployment observations",
            None,
        )


def _validate_command_shape(command: DeploymentObservationCommand) -> None:
    if command.expected_version not in {None, 0}:
        raise DomainError(
            "version_conflict",
            "deployment observation requires expected version 0",
            "reload",
            current_version=0,
        )
    if not command.idempotency_key.strip():
        raise DomainError("deployment_observation_invalid", "idempotency key is required", None)
    if not ENVIRONMENT.fullmatch(command.environment):
        raise DomainError("deployment_observation_invalid", "environment is invalid", None)
    for field, value in {
        "base_url": command.base_url,
        "deployment_url": command.deployment_url,
    }.items():
        if not value.startswith("https://") or not value.strip():
            raise DomainError(
                "deployment_observation_invalid",
                f"{field} must be an https URL",
                None,
            )
    for field, value in {
        "deployment_ref": command.deployment_ref,
        "deployer": command.deployer,
    }.items():
        if not value.strip():
            raise DomainError("deployment_observation_invalid", f"{field} is required", None)
    if command.observed_at.tzinfo is None:
        raise DomainError(
            "deployment_observation_invalid",
            "observed_at must include a timezone",
            None,
        )
    if not SHA256_DIGEST.fullmatch(command.observed_artifact_digest):
        raise DomainError(
            "deployment_observation_invalid",
            "observed artifact digest must be an immutable sha256 digest",
            None,
        )
    secret_path = _secret_metadata_path(_command_payload(command))
    if secret_path is not None:
        raise DomainError(
            "deployment_observation_secret_rejected",
            f"deployment observation contains secret-shaped content at {secret_path}",
            "store only bounded non-secret references",
        )
    _validate_probe_summary(command.probe_summary)
    _validate_route_summary(command.route_summary)
    _validate_auth_summary(command.auth_summary)
    _validate_dispatch_summary(command.dispatch_summary)
    _validate_status_summary(command.status_summary)


def _validate_probe_summary(payload: dict[str, Any]) -> None:
    probes = payload.get("probes")
    if not isinstance(probes, list) or not probes:
        raise DomainError("deployment_observation_invalid", "probe summary is missing", None)
    for probe in probes:
        if not isinstance(probe, dict):
            raise DomainError("deployment_observation_invalid", "probe summary is malformed", None)
        if not isinstance(probe.get("endpoint"), str) or not probe["endpoint"].strip():
            raise DomainError("deployment_observation_invalid", "probe endpoint is missing", None)
        if not isinstance(probe.get("status_code"), int):
            raise DomainError("deployment_observation_invalid", "probe status is missing", None)


def _validate_route_summary(payload: dict[str, Any]) -> None:
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        raise DomainError("deployment_observation_invalid", "route summary is missing", None)
    for route in routes:
        if (
            not isinstance(route, dict)
            or not isinstance(route.get("path"), str)
            or not isinstance(route.get("present"), bool)
        ):
            raise DomainError("deployment_observation_invalid", "route summary is malformed", None)


def _validate_auth_summary(payload: dict[str, Any]) -> None:
    if payload.get("missing_m2m_status") != 401:
        raise DomainError(
            "deployment_observation_invalid",
            "auth summary must include missing M2M 401",
            None,
        )
    configured = payload.get("configured_m2m_status")
    if configured is not None and not isinstance(configured, int):
        raise DomainError("deployment_observation_invalid", "auth summary is malformed", None)


def _validate_dispatch_summary(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("dispatch_enabled"), bool):
        raise DomainError("deployment_observation_invalid", "dispatch posture is malformed", None)


def _validate_status_summary(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("status"), str) or not payload["status"].strip():
        raise DomainError("deployment_observation_invalid", "status summary is missing", None)


def _same_observation_facts(
    row: DeploymentObservation,
    command: DeploymentObservationCommand,
) -> bool:
    return (
        row.base_url == command.base_url
        and row.observed_artifact_digest == command.observed_artifact_digest
        and row.deployment_ref == command.deployment_ref
        and row.deployment_url == command.deployment_url
        and row.deployer == command.deployer
        and row.observed_at == command.observed_at
        and row.probe_summary == command.probe_summary
        and row.route_summary == command.route_summary
        and row.auth_summary == command.auth_summary
        and row.dispatch_summary == command.dispatch_summary
        and row.status_summary == command.status_summary
    )


def _validate_idempotent_replay(
    session: Session,
    row: DeploymentObservation,
    payload: dict[str, object],
) -> None:
    event = session.get(Event, row.event_id)
    if (
        event is None
        or event.action != "deployment.observed"
        or event.subject_id != row.id
        or event.payload.get("command") != payload
    ):
        raise _idempotency_conflict()


def _command_payload(command: DeploymentObservationCommand) -> dict[str, object]:
    raw = asdict(command)
    actor = raw.pop("actor")
    raw["actor_id"] = actor["actor_id"]
    raw["actor_role"] = actor["role"]
    raw["release_artifact_binding_id"] = str(command.release_artifact_binding_id)
    raw["observed_at"] = command.observed_at.isoformat()
    return raw


def _secret_metadata_path(value: object, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            child_path = f"{path}.{key_text}"
            if any(part in lowered for part in SECRET_KEY_PARTS):
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
        if len(value) > MAX_FACT_STRING:
            return path
        lowered = value.lower()
        if "authorization: bearer " in lowered or BWS_TOKEN_SHAPE.search(value):
            return path
    return None


def _lock_idempotency_key(session: Session, idempotency_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
        {
            "namespace": IDEMPOTENCY_LOCK_NAMESPACE,
            "idempotency_key": idempotency_key,
        },
    )


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different deployment observation",
        "use a new idempotency key",
    )
