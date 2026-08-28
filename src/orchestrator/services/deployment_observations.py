import json
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import authority_fingerprint, normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    CONTAINER_IMAGE_OBSERVATION,
    DEPLOYMENT_OBSERVATION_KINDS,
    MACHINE_LOCAL_OBSERVATION,
    OPERATOR_MACHINE_ENVIRONMENT,
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
SECRET_KEY_PARTS = (
    "api_key",
    "authorization",
    "bearer",
    "body",
    "credential",
    "instruction",
    "log",
    "password",
    "response",
    "secret",
    "token",
)
MAX_FACT_STRING = 512
MAX_FACT_BYTES = 4096
MAX_PROBES = 10
MAX_ROUTES = 30

# THE MACHINE-LOCAL SUMMARY: three facts about one working copy, kept separate.
#
# They are separate members rather than one verdict because "not current" for three different
# reasons is exactly the state-collapse this estate has repeatedly paid for. Each names what was
# MEASURED rather than what it implies. The second is the one with a recorded failure mode: the
# editable install is a `.pth` pointing at the source tree, so an ordinary module change is live
# the moment a pull lands -- console entry points are the exception, which is why "the code is at
# the right commit" is not the same statement as "the program that will run is the new one".
#
# This vocabulary CROSSES A PROCESS BOUNDARY. `src/activation_sweep` composes the summary and
# cannot import this module (it is a separate program, and `tests/architecture` enforces that),
# so it carries a transcription that `tests/contract/test_activation_summary_contract.py` holds
# to these names. A rename here without one there would empty the lane in silence.
ACTIVATION_MERGE_COMMIT = "merge_commit_present"
ACTIVATION_ENTRY_POINTS = "console_entry_points_present"
ACTIVATION_ENVIRONMENT = "environment_matches_lock"
ACTIVATION_FACTS = (ACTIVATION_MERGE_COMMIT, ACTIVATION_ENTRY_POINTS, ACTIVATION_ENVIRONMENT)

# Tri-state, not boolean, and `not_applicable` is the load-bearing member. One of the four
# enrolled working copies is a TypeScript project with no `pyproject.toml`, no lockfile and no
# virtual environment, so two of the three questions genuinely do not apply to it -- and this
# estate's standing ruling is that "not applicable" is a distinct answer from "not met".
ACTIVATION_YES = "yes"
ACTIVATION_NO = "no"
ACTIVATION_NOT_APPLICABLE = "not_applicable"
ACTIVATION_RESULTS = (ACTIVATION_YES, ACTIVATION_NO, ACTIVATION_NOT_APPLICABLE)

# The one fact that can never be `not_applicable`: every working copy either holds the commit in
# its history or does not, whatever it is written in.
ALWAYS_ANSWERABLE = (ACTIVATION_MERGE_COMMIT,)


@dataclass(frozen=True)
class DeploymentObservationCommand:
    release_artifact_binding_id: uuid.UUID
    actor: ActorContext
    environment: str
    base_url: str | None
    observed_artifact_digest: str
    deployment_ref: str
    deployment_url: str | None
    deployer: str | None
    observed_at: datetime
    probe_summary: dict[str, Any]
    route_summary: dict[str, Any]
    auth_summary: dict[str, Any]
    dispatch_summary: dict[str, Any]
    status_summary: dict[str, Any]
    idempotency_key: str
    expected_version: int | None = None
    # Defaulted so every existing caller keeps its meaning. `kind` decides which of the two
    # activation models this row describes, and the shapes above are conditional on it: a hosted
    # observation carries the five probe-shaped summaries and no activation summary, a
    # machine-local one carries the reverse.
    kind: str = CONTAINER_IMAGE_OBSERVATION
    activation_summary: dict[str, Any] = field(default_factory=dict)


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
        return DomainError(
            "release_artifact_not_found",
            "release artifact binding does not exist",
            None,
        )
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
    command = _normalized_command(command)
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
    # NO VERIFICATION UNIT FOR A MACHINE-LOCAL ROW, and it is a decision rather than an omission.
    # The generated unit exists to carry five acceptance criteria a verifier evaluates about a
    # hosted deployment -- health, routes, auth posture, dispatch posture -- none of which a
    # working copy can evidence. Minting one anyway would produce a unit whose required criteria
    # can never be met: permanently unverifiable debris in the review queue, one row per binding,
    # growing daily. What the observation measured is already in its own summary, so there is
    # nothing left for a verifier to decide.
    generated_unit = (
        None
        if command.kind == MACHINE_LOCAL_OBSERVATION
        else _post_deploy_work_unit(session, binding, command, now)
    )
    evidence_ids = (
        []
        if generated_unit is None
        else _deployment_evidence(
            session,
            command,
            binding,
            generated_unit,
            observation_id,
            now,
        )
    )
    observed_event_id = uuid.uuid4()
    created_event_id = None if generated_unit is None else uuid.uuid4()
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
    if generated_unit is not None:
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
        kind=command.kind,
        post_deploy_work_unit_id=None if generated_unit is None else generated_unit.id,
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
        activation_summary=command.activation_summary,
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
    # No "unknown_fields" key here: it is not itself a KNOWN_FIELD, so passing it made
    # normalize_authority record an unknown field literally named "unknown_fields" --
    # every post-deploy unit was minted with a self-referential envelope that is not a
    # fixed point of normalisation. WS-P2.34 shipped that fail-closed gate
    # (runner_envelope_field_violation) and this path does NOT traverse it -- minting
    # constructs the unit directly -- so the two units minted before this line was fixed
    # keep their recorded unknown field, terminal and inert. Keep the key out of here: were
    # this envelope ever routed through unit ingress it would be refused, correctly.
    authority = normalize_authority(
        {
            "capabilities": {"post_deploy_verification": "allowed"},
            "budgets": {"max_attempts": 1},
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
    if binding.kind != command.kind:
        raise DomainError(
            "deployment_observation_kind_mismatch",
            "observation kind does not match the release artifact binding",
            "observe the activation model the binding actually describes",
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
    _validate_command_envelope(command)
    _validate_required_text_and_urls(command)
    _validate_observed_at_and_digest(command)
    secret_path = _secret_metadata_path(_command_payload(command))
    if secret_path is not None:
        raise DomainError(
            "deployment_observation_secret_rejected",
            f"deployment observation contains secret-shaped content at {secret_path}",
            "store only bounded non-secret references",
        )
    if command.kind == MACHINE_LOCAL_OBSERVATION:
        _validate_machine_local_shape(command)
    else:
        _validate_hosted_shape(command)
    _validate_bounded_json_size(command)


def _validate_hosted_shape(command: DeploymentObservationCommand) -> None:
    if command.activation_summary:
        raise DomainError(
            "deployment_observation_invalid",
            "a hosted observation carries no activation summary",
            None,
        )
    _validate_probe_summary(command.probe_summary)
    _validate_route_summary(command.route_summary)
    _validate_auth_summary(command.auth_summary)
    _validate_dispatch_summary(command.dispatch_summary)
    _validate_status_summary(command.status_summary)


def _validate_machine_local_shape(command: DeploymentObservationCommand) -> None:
    """The five probe-shaped summaries are REFUSED here, not merely unused.

    An empty one would be indistinguishable from a hosted observation whose probe list nobody
    filled in, and the whole point of the discriminator is that the two models cannot be mistaken
    for one another in the data.
    """
    for name, value in _hosted_summaries(command).items():
        if value:
            raise DomainError(
                "deployment_observation_invalid",
                f"a machine-local observation carries no {name}",
                None,
            )
    if command.environment != OPERATOR_MACHINE_ENVIRONMENT:
        raise DomainError(
            "deployment_observation_invalid",
            f"a machine-local observation names environment {OPERATOR_MACHINE_ENVIRONMENT}",
            None,
        )
    _validate_activation_summary(command.activation_summary)


def _hosted_summaries(command: DeploymentObservationCommand) -> dict[str, dict[str, Any]]:
    return {
        "probe_summary": command.probe_summary,
        "route_summary": command.route_summary,
        "auth_summary": command.auth_summary,
        "dispatch_summary": command.dispatch_summary,
        "status_summary": command.status_summary,
    }


def _validate_activation_summary(payload: dict[str, Any]) -> None:
    """Every fact present, every value in the vocabulary, and one of them never excused.

    EXACT membership rather than a subset: a missing member would read as a fact nobody thought
    to check, which is the shape a reader would take for a clean answer.
    """
    _require_keys(payload, set(ACTIVATION_FACTS), "activation summary")
    if set(payload) != set(ACTIVATION_FACTS):
        raise DomainError(
            "deployment_observation_invalid",
            "activation summary is missing a fact",
            None,
        )
    for fact in ACTIVATION_FACTS:
        value = payload[fact]
        if value not in ACTIVATION_RESULTS:
            raise DomainError(
                "deployment_observation_invalid",
                f"activation summary fact {fact} is malformed",
                None,
            )
        if value == ACTIVATION_NOT_APPLICABLE and fact in ALWAYS_ANSWERABLE:
            raise DomainError(
                "deployment_observation_invalid",
                f"activation summary fact {fact} always applies",
                None,
            )


def _validate_command_envelope(command: DeploymentObservationCommand) -> None:
    if command.expected_version not in {None, 0}:
        raise DomainError(
            "version_conflict",
            "deployment observation requires expected version 0",
            "reload",
            current_version=0,
        )
    if not command.idempotency_key.strip():
        raise DomainError("deployment_observation_invalid", "idempotency key is required", None)


def _validate_required_text_and_urls(command: DeploymentObservationCommand) -> None:
    if command.kind not in DEPLOYMENT_OBSERVATION_KINDS:
        raise DomainError("deployment_observation_invalid", "kind is invalid", None)
    if not ENVIRONMENT.fullmatch(command.environment):
        raise DomainError("deployment_observation_invalid", "environment is invalid", None)
    if not command.deployment_ref.strip():
        raise DomainError("deployment_observation_invalid", "deployment_ref is required", None)
    # The three a working copy has no honest value for. ABSENT for a machine-local row rather
    # than blank: a NULL says nobody probed a URL, where an empty string says somebody probed
    # one and it was this.
    hosted_only = {
        "base_url": command.base_url,
        "deployment_url": command.deployment_url,
        "deployer": command.deployer,
    }
    for name, value in hosted_only.items():
        if command.kind == MACHINE_LOCAL_OBSERVATION:
            if value is not None:
                raise DomainError(
                    "deployment_observation_invalid",
                    f"a machine-local observation carries no {name}",
                    None,
                )
        elif value is None or not value.strip():
            raise DomainError("deployment_observation_invalid", f"{name} is required", None)


def _validate_observed_at_and_digest(command: DeploymentObservationCommand) -> None:
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


def _validate_probe_summary(payload: dict[str, Any]) -> None:
    _require_keys(payload, {"probes"}, "probe summary")
    probes = payload.get("probes")
    if not isinstance(probes, list) or not probes or len(probes) > MAX_PROBES:
        raise DomainError("deployment_observation_invalid", "probe summary is missing", None)
    for probe in probes:
        _require_keys(
            probe if isinstance(probe, dict) else {},
            {
                "endpoint",
                "expected_status_max",
                "expected_status_min",
                "method",
                "name",
                "observed_at",
                "status_code",
            },
            "probe summary",
        )
        if not isinstance(probe, dict):
            raise DomainError("deployment_observation_invalid", "probe summary is malformed", None)
        if not isinstance(probe.get("endpoint"), str) or not probe["endpoint"].strip():
            raise DomainError("deployment_observation_invalid", "probe endpoint is missing", None)
        if not _valid_status_code(probe.get("status_code")):
            raise DomainError("deployment_observation_invalid", "probe status is missing", None)
        if not _valid_status_code(probe.get("expected_status_min")) or not _valid_status_code(
            probe.get("expected_status_max")
        ):
            raise DomainError(
                "deployment_observation_invalid",
                "probe status range is malformed",
                None,
            )


def _validate_route_summary(payload: dict[str, Any]) -> None:
    _require_keys(payload, {"routes"}, "route summary")
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes or len(routes) > MAX_ROUTES:
        raise DomainError("deployment_observation_invalid", "route summary is missing", None)
    for route in routes:
        _require_keys(
            route if isinstance(route, dict) else {},
            {"path", "present"},
            "route summary",
        )
        if (
            not isinstance(route, dict)
            or not isinstance(route.get("path"), str)
            or not isinstance(route.get("present"), bool)
        ):
            raise DomainError("deployment_observation_invalid", "route summary is malformed", None)


def _validate_auth_summary(payload: dict[str, Any]) -> None:
    _require_keys(
        payload,
        {"configured_m2m_status", "missing_m2m_status"},
        "auth summary",
    )
    if payload.get("missing_m2m_status") != 401:
        raise DomainError(
            "deployment_observation_invalid",
            "auth summary must include missing M2M 401",
            None,
        )
    configured = payload.get("configured_m2m_status")
    if configured is not None and not _valid_status_code(configured):
        raise DomainError("deployment_observation_invalid", "auth summary is malformed", None)


def _validate_dispatch_summary(payload: dict[str, Any]) -> None:
    _require_keys(payload, {"dispatch_enabled"}, "dispatch posture")
    if not isinstance(payload.get("dispatch_enabled"), bool):
        raise DomainError("deployment_observation_invalid", "dispatch posture is malformed", None)


def _validate_status_summary(payload: dict[str, Any]) -> None:
    _require_keys(payload, {"status", "summary"}, "status summary")
    if not isinstance(payload.get("status"), str) or not payload["status"].strip():
        raise DomainError("deployment_observation_invalid", "status summary is missing", None)


def _require_keys(payload: dict[str, Any], allowed: set[str], label: str) -> None:
    if not isinstance(payload, dict) or not set(payload).issubset(allowed):
        raise DomainError(
            "deployment_observation_invalid",
            f"{label} contains unbounded fields",
            None,
        )


def _valid_status_code(value: object) -> bool:
    return isinstance(value, int) and 100 <= value <= 599


def _validate_bounded_json_size(command: DeploymentObservationCommand) -> None:
    payload = {**_hosted_summaries(command), "activation_summary": command.activation_summary}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_FACT_BYTES:
        raise DomainError(
            "deployment_observation_invalid",
            "deployment observation facts exceed bounded size",
            None,
        )


def _normalized_command(command: DeploymentObservationCommand) -> DeploymentObservationCommand:
    """Canonicalise the two URLs a hosted observation carries, and leave a machine-local row be.

    Called before shape validation, so it must tolerate the absence rather than assume the kind
    has already been checked: a machine-local command legitimately has neither URL, and a hosted
    one missing them is refused a moment later by name.
    """
    return replace(
        command,
        base_url=(_canonical_base_url(command.base_url) if command.base_url is not None else None),
        deployment_url=(
            _canonical_https_url(command.deployment_url, "deployment_url")
            if command.deployment_url is not None
            else None
        ),
    )


def _canonical_base_url(value: str) -> str:
    parsed = _https_parts(value, "base_url")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise DomainError(
            "deployment_observation_invalid",
            "base_url must be a canonical origin URL",
            None,
        )
    return urlunsplit(("https", parsed.netloc.lower(), "", "", ""))


def _canonical_https_url(value: str, field: str) -> str:
    parsed = _https_parts(value, field)
    return urlunsplit(
        (
            "https",
            parsed.netloc.lower(),
            parsed.path or "",
            parsed.query,
            parsed.fragment,
        )
    )


def _https_parts(value: str, field: str):
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DomainError(
            "deployment_observation_invalid",
            f"{field} must be an https URL with a host and no userinfo",
            None,
        )
    return parsed


def _same_observation_facts(
    row: DeploymentObservation,
    command: DeploymentObservationCommand,
) -> bool:
    return (
        row.kind == command.kind
        and row.activation_summary == command.activation_summary
        and row.base_url == command.base_url
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
        return _secret_dict_path(value, path)
    if isinstance(value, list):
        return _secret_list_path(value, path)
    if isinstance(value, str):
        return _secret_string_path(value, path)
    return None


def _secret_dict_path(value: dict[object, object], path: str) -> str | None:
    for key, child in value.items():
        key_text = str(key)
        lowered = key_text.lower()
        child_path = f"{path}.{key_text}"
        if any(part in lowered for part in SECRET_KEY_PARTS):
            return child_path
        found = _secret_metadata_path(child, child_path)
        if found is not None:
            return found
    return None


def _secret_list_path(value: list[object], path: str) -> str | None:
    for index, child in enumerate(value):
        found = _secret_metadata_path(child, f"{path}[{index}]")
        if found is not None:
            return found
    return None


def _secret_string_path(value: str, path: str) -> str | None:
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
