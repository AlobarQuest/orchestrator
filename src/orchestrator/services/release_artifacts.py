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
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Event,
    Evidence,
    ReleaseArtifactBinding,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.production_drill_resources import (
    bind_created_drill_evidence,
    bind_created_drill_release_artifact,
    require_production_drill_resource,
)

IDEMPOTENCY_LOCK_NAMESPACE = 0x57533532
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{7,40}$")
BWS_TOKEN_SHAPE = re.compile(r"\b0\.[0-9a-fA-F-]{36}\.[A-Za-z0-9_=-]{8,}")
SECRET_KEY_PARTS = ("authorization", "password", "secret", "token")


@dataclass(frozen=True)
class ReleaseArtifactCommand:
    work_unit_id: uuid.UUID
    actor: ActorContext
    package_revision_id: uuid.UUID
    package_revision_hash: str
    source_repository: str
    implementation_pr_number: int | None
    source_commit: str
    merge_commit: str
    artifact_registry: str
    artifact_repository: str
    artifact_name: str
    artifact_digest: str
    artifact_tag: str | None
    workflow_run_id: str | None
    workflow_run_attempt: int | None
    workflow_path: str | None
    workflow_ref: str | None
    workflow_run_url: str | None
    builder_id: str | None
    builder_class: str | None
    provenance_ref: str | None
    provenance_digest: str | None
    sbom_ref: str | None
    sbom_digest: str | None
    summary: dict[str, Any] | None
    idempotency_key: str
    expected_version: int | None = None


def record_release_artifact(
    session: Session,
    command: ReleaseArtifactCommand,
) -> ReleaseArtifactBinding | DomainError:
    try:
        row, _created = _record_release_artifact(session, command)
        if not session.info.get("production_drill_scenario_atomic"):
            session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "release_artifact_conflict",
            "release artifact binding conflicts with an existing source tuple",
            "verify digest and provenance before retrying",
        )
    except Exception:
        session.rollback()
        raise


def record_production_drill_release_artifact(
    session: Session, *, run_id: uuid.UUID, command: ReleaseArtifactCommand
) -> ReleaseArtifactBinding | DomainError:
    try:
        require_production_drill_resource(session, run_id, "work_unit", command.work_unit_id)
        row, created = _record_release_artifact(session, command)
        if created:
            bind_created_drill_release_artifact(session, run_id, row)
            evidence = session.get(Evidence, row.evidence_id)
            assert evidence is not None
            bind_created_drill_evidence(session, run_id, evidence)
        else:
            require_production_drill_resource(session, run_id, "release_artifact", row.id)
            require_production_drill_resource(session, run_id, "evidence", row.evidence_id)
        if not session.info.get("production_drill_scenario_atomic"):
            session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "release_artifact_conflict",
            "release artifact binding conflicts with an existing source tuple",
            "verify digest and provenance before retrying",
        )
    except Exception:
        session.rollback()
        raise


def list_release_artifacts(
    session: Session, work_unit_id: uuid.UUID
) -> tuple[ReleaseArtifactBinding, ...] | DomainError:
    if session.get(WorkUnit, work_unit_id) is None:
        return DomainError("work_unit_not_found", "work unit does not exist", None)
    return tuple(
        session.scalars(
            select(ReleaseArtifactBinding)
            .where(ReleaseArtifactBinding.work_unit_id == work_unit_id)
            .order_by(ReleaseArtifactBinding.recorded_at, ReleaseArtifactBinding.id)
        )
    )


def _record_release_artifact(
    session: Session,
    command: ReleaseArtifactCommand,
) -> tuple[ReleaseArtifactBinding, bool]:
    _authorize_actor(command.actor)
    _validate_command_shape(command)
    payload = _command_payload(command)
    _lock_idempotency_key(session, command.idempotency_key)

    existing = session.scalar(
        select(ReleaseArtifactBinding).where(
            ReleaseArtifactBinding.idempotency_key == command.idempotency_key
        )
    )
    if existing is not None:
        _validate_idempotent_replay(session, existing, payload)
        return existing, False
    event_with_key = session.scalar(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    )
    if event_with_key is not None:
        raise _idempotency_conflict()

    unit, revision = _validated_subject(session, command)

    same_tuple = _existing_source_tuple(session, command)
    if same_tuple is not None:
        if _same_binding_facts(same_tuple, command):
            return same_tuple, False
        raise DomainError(
            "release_artifact_conflict",
            "source tuple is already bound to a different immutable artifact",
            "record an explicit supersession design before changing release lineage",
        )

    now = TransactionClock().now(session)
    binding_id = uuid.uuid4()
    evidence = _release_evidence(session, command, binding_id, now)
    event_id = uuid.uuid4()
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="release_artifact.bound",
            subject_type="release_artifact_binding",
            subject_id=binding_id,
            from_state=unit.state,
            to_state=unit.state,
            payload={"command": payload, "evidence_id": str(evidence.id)},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    session.flush()
    row = ReleaseArtifactBinding(
        id=binding_id,
        work_unit_id=unit.id,
        work_package_revision_id=revision.id,
        package_revision_hash=command.package_revision_hash,
        source_repository=command.source_repository,
        implementation_pr_number=command.implementation_pr_number,
        source_commit=command.source_commit,
        merge_commit=command.merge_commit,
        artifact_registry=command.artifact_registry,
        artifact_repository=command.artifact_repository,
        artifact_name=command.artifact_name,
        artifact_digest=command.artifact_digest,
        artifact_tag=_optional_text(command.artifact_tag),
        workflow_run_id=_optional_text(command.workflow_run_id),
        workflow_run_attempt=command.workflow_run_attempt,
        workflow_path=_optional_text(command.workflow_path),
        workflow_ref=_optional_text(command.workflow_ref),
        workflow_run_url=_optional_text(command.workflow_run_url),
        builder_id=_optional_text(command.builder_id),
        builder_class=_optional_text(command.builder_class),
        provenance_ref=_optional_text(command.provenance_ref),
        provenance_digest=_optional_text(command.provenance_digest),
        sbom_ref=_optional_text(command.sbom_ref),
        sbom_digest=_optional_text(command.sbom_digest),
        summary=command.summary or {},
        recorded_by=command.actor.actor_id,
        recorded_at=now,
        event_id=event_id,
        evidence_id=evidence.id,
        idempotency_key=command.idempotency_key,
    )
    session.add(row)
    session.flush()
    return row, True


def _release_evidence(
    session: Session,
    command: ReleaseArtifactCommand,
    binding_id: uuid.UUID,
    now: datetime,
) -> Evidence:
    evidence_id = uuid.uuid4()
    evidence_key = f"{command.idempotency_key}:evidence"
    payload = {
        "binding_id": str(binding_id),
        "package_revision_id": str(command.package_revision_id),
        "package_revision_hash": command.package_revision_hash,
        "source_repository": command.source_repository,
        "implementation_pr_number": command.implementation_pr_number,
        "source_commit": command.source_commit,
        "merge_commit": command.merge_commit,
        "artifact_registry": command.artifact_registry,
        "artifact_repository": command.artifact_repository,
        "artifact_name": command.artifact_name,
        "artifact_digest": command.artifact_digest,
        "artifact_tag": _optional_text(command.artifact_tag),
        "workflow": {
            "run_id": _optional_text(command.workflow_run_id),
            "attempt": command.workflow_run_attempt,
            "path": _optional_text(command.workflow_path),
            "ref": _optional_text(command.workflow_ref),
            "url": _optional_text(command.workflow_run_url),
        },
        "builder": {
            "id": _optional_text(command.builder_id),
            "class": _optional_text(command.builder_class),
        },
        "provenance": {
            "ref": _optional_text(command.provenance_ref),
            "digest": _optional_text(command.provenance_digest),
        },
        "sbom": {
            "ref": _optional_text(command.sbom_ref),
            "digest": _optional_text(command.sbom_digest),
        },
        "summary": command.summary or {},
    }
    evidence = Evidence(
        id=evidence_id,
        work_package_revision_id=command.package_revision_id,
        work_unit_id=command.work_unit_id,
        ac_id="release-artifact",
        attempt=1,
        evidence_type="release.artifact_bound",
        stable_ref=_artifact_ref(command),
        payload=payload,
        source_revision=command.merge_commit,
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
                    "ac_id": evidence.ac_id,
                    "actor_id": command.actor.actor_id,
                    "actor_role": command.actor.role,
                    "attempt": 1,
                    "evidence_type": evidence.evidence_type,
                    "payload": payload,
                    "source_revision": command.merge_commit,
                    "stable_ref": evidence.stable_ref,
                    "work_package_revision_id": str(command.package_revision_id),
                    "work_unit_id": str(command.work_unit_id),
                }
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=evidence_key,
        )
    )
    session.flush()
    return evidence


def _validate_command_shape(command: ReleaseArtifactCommand) -> None:
    _validate_required_text(command)
    _validate_digests(command)
    _validate_commits(command)
    _validate_positive_numbers(command)
    secret_path = _secret_metadata_path(_command_payload(command))
    if secret_path is not None:
        raise DomainError(
            "release_artifact_secret_rejected",
            f"release artifact metadata contains secret-shaped content at {secret_path}",
            "store only bounded non-secret references",
        )


def _validated_subject(
    session: Session,
    command: ReleaseArtifactCommand,
) -> tuple[WorkUnit, WorkPackageRevision]:
    unit = session.scalar(
        select(WorkUnit).where(WorkUnit.id == command.work_unit_id).with_for_update()
    )
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    _validate_unit_version_and_state(unit, command)
    if unit.work_package_revision_id != command.package_revision_id:
        raise DomainError(
            "release_artifact_revision_mismatch",
            "release artifact package revision does not match the work unit",
            None,
        )
    revision = session.get(WorkPackageRevision, command.package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    if revision.content_hash != command.package_revision_hash:
        raise DomainError(
            "release_artifact_package_hash_mismatch",
            "release artifact package revision hash does not match approved revision",
            None,
        )
    return unit, revision


def _validate_unit_version_and_state(
    unit: WorkUnit,
    command: ReleaseArtifactCommand,
) -> None:
    if command.expected_version is not None and unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    if WorkUnitState(unit.state) is not WorkUnitState.COMPLETED:
        raise DomainError(
            "work_unit_not_completed",
            "release artifact binding requires a completed work unit",
            "verify and complete the work unit before binding a release artifact",
        )


def _validate_required_text(command: ReleaseArtifactCommand) -> None:
    required_text = {
        "package_revision_hash": command.package_revision_hash,
        "source_repository": command.source_repository,
        "source_commit": command.source_commit,
        "merge_commit": command.merge_commit,
        "artifact_registry": command.artifact_registry,
        "artifact_repository": command.artifact_repository,
        "artifact_name": command.artifact_name,
        "artifact_digest": command.artifact_digest,
    }
    for field, value in required_text.items():
        if not _text(value):
            if field == "artifact_digest":
                raise _digest_error()
            raise DomainError("release_artifact_invalid", f"{field} is required", None)


def _validate_digests(command: ReleaseArtifactCommand) -> None:
    if not SHA256_DIGEST.fullmatch(command.artifact_digest):
        raise _digest_error()
    for field, value in (
        ("provenance_digest", command.provenance_digest),
        ("sbom_digest", command.sbom_digest),
    ):
        normalized = _optional_text(value)
        if normalized is not None and not SHA256_DIGEST.fullmatch(normalized):
            raise DomainError(
                "release_artifact_digest_invalid",
                f"{field} must be a sha256 digest when supplied",
                None,
            )


def _validate_commits(command: ReleaseArtifactCommand) -> None:
    if not GIT_SHA.fullmatch(command.source_commit) or not GIT_SHA.fullmatch(command.merge_commit):
        raise DomainError(
            "release_artifact_commit_invalid",
            "source and merge commits must be git SHA references",
            None,
        )


def _validate_positive_numbers(command: ReleaseArtifactCommand) -> None:
    if command.implementation_pr_number is not None and command.implementation_pr_number <= 0:
        raise DomainError("release_artifact_invalid", "PR number must be positive", None)
    if command.workflow_run_attempt is not None and command.workflow_run_attempt <= 0:
        raise DomainError("release_artifact_invalid", "workflow run attempt must be positive", None)


def _digest_error() -> DomainError:
    return DomainError(
        "release_artifact_digest_invalid",
        "artifact identity must include an immutable sha256 digest",
        "provide registry digest, not a mutable tag",
    )


def _authorize_actor(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may bind release artifacts",
            None,
        )


def _existing_source_tuple(
    session: Session, command: ReleaseArtifactCommand
) -> ReleaseArtifactBinding | None:
    return session.scalar(
        select(ReleaseArtifactBinding)
        .where(
            ReleaseArtifactBinding.work_package_revision_id == command.package_revision_id,
            ReleaseArtifactBinding.work_unit_id == command.work_unit_id,
            ReleaseArtifactBinding.source_repository == command.source_repository,
            ReleaseArtifactBinding.merge_commit == command.merge_commit,
            ReleaseArtifactBinding.source_commit == command.source_commit,
            ReleaseArtifactBinding.artifact_registry == command.artifact_registry,
            ReleaseArtifactBinding.artifact_repository == command.artifact_repository,
            ReleaseArtifactBinding.artifact_name == command.artifact_name,
        )
        .with_for_update()
    )


def _same_binding_facts(row: ReleaseArtifactBinding, command: ReleaseArtifactCommand) -> bool:
    return (
        row.package_revision_hash == command.package_revision_hash
        and row.implementation_pr_number == command.implementation_pr_number
        and row.artifact_digest == command.artifact_digest
        and row.artifact_tag == _optional_text(command.artifact_tag)
        and row.workflow_run_id == _optional_text(command.workflow_run_id)
        and row.workflow_run_attempt == command.workflow_run_attempt
        and row.workflow_path == _optional_text(command.workflow_path)
        and row.workflow_ref == _optional_text(command.workflow_ref)
        and row.workflow_run_url == _optional_text(command.workflow_run_url)
        and row.builder_id == _optional_text(command.builder_id)
        and row.builder_class == _optional_text(command.builder_class)
        and row.provenance_ref == _optional_text(command.provenance_ref)
        and row.provenance_digest == _optional_text(command.provenance_digest)
        and row.sbom_ref == _optional_text(command.sbom_ref)
        and row.sbom_digest == _optional_text(command.sbom_digest)
        and row.summary == (command.summary or {})
    )


def _validate_idempotent_replay(
    session: Session,
    row: ReleaseArtifactBinding,
    payload: dict[str, object],
) -> None:
    event = session.get(Event, row.event_id)
    if (
        event is None
        or event.action != "release_artifact.bound"
        or event.subject_id != row.id
        or event.payload.get("command") != payload
    ):
        raise _idempotency_conflict()


def _command_payload(command: ReleaseArtifactCommand) -> dict[str, object]:
    raw = asdict(command)
    actor = raw.pop("actor")
    raw["actor_id"] = actor["actor_id"]
    raw["actor_role"] = actor["role"]
    raw["work_unit_id"] = str(command.work_unit_id)
    raw["package_revision_id"] = str(command.package_revision_id)
    raw["summary"] = command.summary or {}
    return raw


def _artifact_ref(command: ReleaseArtifactCommand) -> str:
    return (
        f"{command.artifact_registry}/{command.artifact_repository}/"
        f"{command.artifact_name}@{command.artifact_digest}"
    )


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


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
        "idempotency key belongs to a different release artifact binding",
        "use a new idempotency key",
    )
