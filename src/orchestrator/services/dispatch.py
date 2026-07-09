import hashlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import DispatchRecord, Event, WorkPackageRevision, WorkUnit
from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.lifecycle import ActorContext

ORCHESTRATOR_URL = "https://sds.alobar.net"


@dataclass(frozen=True)
class DispatchSettings:
    enabled: bool
    allowed_change_classes: frozenset[str]
    enabled_capabilities: frozenset[str]
    allowed_target_repositories: frozenset[str]
    workflow_id: str
    workflow_ref: str
    # Whether the App credentials are configured — never the credentials themselves, so no
    # secret can reach a log, a repr, or a dispatch payload through this object.
    github_app_configured: bool
    failure_signature_threshold: int = 3
    orchestrator_url: str = ORCHESTRATOR_URL
    human_gate_age_out_seconds: int | None = None


@dataclass(frozen=True)
class DispatchCommand:
    unit_id: uuid.UUID
    runner_attempt: int
    actor: ActorContext
    idempotency_key: str
    expected_version: int | None = None


class WorkflowDispatcher(Protocol):
    def dispatch_workflow(
        self,
        *,
        repository: str,
        workflow_id: str,
        ref: str,
        inputs: Mapping[str, str],
    ) -> Mapping[str, str | None]: ...


class GitHubDispatchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GitHubActionsDispatcher:
    def __init__(self, token_provider: Callable[[], str], *, timeout: float = 10.0) -> None:
        self._token_provider = token_provider
        self._timeout = timeout

    def dispatch_workflow(
        self,
        *,
        repository: str,
        workflow_id: str,
        ref: str,
        inputs: Mapping[str, str],
    ) -> Mapping[str, str | None]:
        url = (
            f"https://api.github.com/repos/{repository}/actions/workflows/{workflow_id}/dispatches"
        )
        # Minting happens inside the error envelope: a credential failure must land in a
        # DispatchRecord like any other, not escape as a 500 that records nothing.
        try:
            token = self._token_provider()
        except GitHubAppTokenError as error:
            raise GitHubDispatchError("app_token_mint", error.code) from error
        try:
            response = httpx.post(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"ref": ref, "inputs": dict(inputs)},
                timeout=self._timeout,
            )
        except httpx.RequestError as error:
            raise GitHubDispatchError("request_error", error.__class__.__name__) from error
        if response.status_code not in {200, 201, 202, 204}:
            raise GitHubDispatchError("github_api", f"status:{response.status_code}")
        return {"workflow_run_id": None, "workflow_run_url": None}


def dispatch_work_unit(
    session: Session,
    command: DispatchCommand,
    settings: DispatchSettings,
    dispatcher: WorkflowDispatcher,
) -> DispatchRecord:
    try:
        record = _dispatch_work_unit(session, command, settings, dispatcher)
        session.commit()
        return record
    except Exception:
        session.rollback()
        raise


def _dispatch_work_unit(
    session: Session,
    command: DispatchCommand,
    settings: DispatchSettings,
    dispatcher: WorkflowDispatcher,
) -> DispatchRecord:
    _authorize_dispatch_actor(command.actor)
    if command.runner_attempt <= 0:
        raise DomainError("dispatch_attempt_invalid", "runner attempt must be positive", None)

    existing = session.scalar(
        select(DispatchRecord).where(DispatchRecord.idempotency_key == command.idempotency_key)
    )
    if existing is not None:
        _validate_idempotent_record(session, existing, command, settings)
        return existing

    unit = session.scalar(select(WorkUnit).where(WorkUnit.id == command.unit_id).with_for_update())
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    if command.expected_version is not None and unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    existing_attempt = session.scalar(
        select(DispatchRecord)
        .where(
            DispatchRecord.work_unit_id == unit.id,
            DispatchRecord.runner_attempt == command.runner_attempt,
        )
        .with_for_update()
    )
    if existing_attempt is not None:
        return existing_attempt
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)

    repository = _target_repository(unit)
    blocked_reason = _blocked_reason(unit, settings)
    if blocked_reason is not None:
        return _record_dispatch(
            session,
            command,
            unit,
            revision,
            settings,
            repository=repository,
            status="skipped" if blocked_reason == "dispatch_disabled" else "blocked",
            reason_code=blocked_reason,
            payload=_payload(unit, settings, repository),
        )

    payload = _payload(unit, settings, repository)
    try:
        result = dispatcher.dispatch_workflow(
            repository=repository,
            workflow_id=settings.workflow_id,
            ref=settings.workflow_ref,
            inputs=payload["workflow_dispatch"]["inputs"],
        )
    except GitHubDispatchError as error:
        signature = failure_signature("workflow_dispatch", error.code, error.message)
        status = "blocked" if _opens_circuit(session, unit.id, signature, settings) else "failed"
        return _record_dispatch(
            session,
            command,
            unit,
            revision,
            settings,
            repository=repository,
            status=status,
            reason_code="failure_signature_circuit_open" if status == "blocked" else error.code,
            failure_signature=signature,
            payload=payload | {"failure_code": error.code},
        )

    return _record_dispatch(
        session,
        command,
        unit,
        revision,
        settings,
        repository=repository,
        status="dispatched",
        payload=payload,
        github_run_id=result.get("workflow_run_id"),
        github_run_url=result.get("workflow_run_url"),
    )


def failure_signature(stage: str, code: str, message: str) -> str:
    normalized = " ".join(message.lower().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"{stage}:{code}:{digest}"


def age_out_human_gates(
    session: Session,
    settings: DispatchSettings,
    actor: ActorContext,
    *,
    now: datetime | None = None,
) -> tuple[DispatchRecord, ...]:
    _authorize_dispatch_actor(actor)
    if settings.human_gate_age_out_seconds is None:
        return ()
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=settings.human_gate_age_out_seconds)
    units = session.scalars(
        select(WorkUnit)
        .where(
            WorkUnit.state.in_(("awaiting_approval", "awaiting_review")),
            WorkUnit.updated_at <= cutoff,
        )
        .order_by(WorkUnit.updated_at, WorkUnit.id)
        .with_for_update()
    ).all()
    records: list[DispatchRecord] = []
    for unit in units:
        idempotency_key = f"human-gate-age-out:{unit.id}:{unit.version}"
        existing = session.scalar(
            select(DispatchRecord).where(DispatchRecord.idempotency_key == idempotency_key)
        )
        if existing is not None:
            records.append(existing)
            continue
        revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
        if revision is None:
            raise DomainError("revision_not_found", "package revision does not exist", None)
        command = DispatchCommand(
            unit_id=unit.id,
            runner_attempt=_next_runner_attempt(session, unit),
            actor=actor,
            idempotency_key=idempotency_key,
            expected_version=unit.version,
        )
        records.append(
            _record_dispatch(
                session,
                command,
                unit,
                revision,
                settings,
                repository=_target_repository(unit),
                status="blocked",
                reason_code="human_gate_aged_out",
                payload={
                    "human_gate_state": unit.state,
                    "age_out_cutoff": cutoff.isoformat(),
                },
            )
        )
    session.commit()
    return tuple(records)


def _authorize_dispatch_actor(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError("role_forbidden", "only the orchestrator system actor may dispatch", None)


def _validate_idempotent_record(
    session: Session,
    record: DispatchRecord,
    command: DispatchCommand,
    settings: DispatchSettings,
) -> None:
    # The routed repository is a property of the unit, so compare the record against the
    # repository this command's unit resolves to — never against a process-wide setting,
    # which would make a legitimate replay look like a conflict.
    unit = session.get(WorkUnit, command.unit_id)
    expected_repository = _target_repository(unit) if unit is not None else None
    if (
        record.work_unit_id != command.unit_id
        or record.runner_attempt != command.runner_attempt
        or (expected_repository is not None and record.target_repository != expected_repository)
        or record.workflow_id != settings.workflow_id
        or record.workflow_ref != settings.workflow_ref
    ):
        raise DomainError(
            "idempotency_conflict",
            "idempotency key belongs to a different dispatch",
            "use a new idempotency key",
        )


def _blocked_reason(unit: WorkUnit, settings: DispatchSettings) -> str | None:
    if not settings.enabled:
        return "dispatch_disabled"
    if not settings.github_app_configured:
        return "github_app_credentials_missing"
    if unit.state != "ready":
        return "work_unit_not_ready"
    if unit.authority_approval_id is None:
        return "authority_approval_missing"
    if unit.required_capability not in settings.enabled_capabilities:
        return "capability_not_enabled"
    change_class = _change_class(unit)
    if change_class not in settings.allowed_change_classes:
        return "change_class_not_allowed"
    if normalize_authority(unit.authority).level_for(unit.required_capability) != "allowed":
        return "capability_not_authorized"
    target_repository = _target_repository(unit)
    if not target_repository:
        return "target_repository_missing"
    if target_repository not in settings.allowed_target_repositories:
        return "target_repository_not_allowed"
    return _conformance_blocked_reason(unit)


def _change_class(unit: WorkUnit) -> str:
    return normalize_authority(unit.authority).change_class or unit.required_capability


def _target_repository(unit: WorkUnit) -> str:
    """Routing is a property of the unit, never of the process.

    The runner refuses to act unless the workflow it runs in IS the unit's target repo,
    so a process-global target would misroute every fan-out unit rather than fail.
    """
    constraints = unit.authority.get("constraints", {})
    if not isinstance(constraints, Mapping):
        return ""
    repository = constraints.get("target_repository")
    return repository if isinstance(repository, str) else ""


def _conformance_blocked_reason(unit: WorkUnit) -> str | None:
    """Conformance is attested per unit, against that unit's own target repository.

    The package revision's enforcement snapshot cannot carry this: it is written once at
    intake, before decomposition has chosen the target repositories, so one snapshot could
    not honestly describe a fan-out across several repos. The unit's envelope can, and
    because the envelope is fingerprinted, the human's authority approval attests it.

    `accepted_standards` must come from a real waiver source (project-standards
    `exceptions:` frontmatter, security-standards `.security-scan-allow.toml`) — never
    echoed from `standards_touched`, or the subset branch below admits everything.
    """
    conformance = normalize_authority(unit.authority).conformance
    if conformance is None:
        return "conformance_missing"
    touched = _string_set(conformance.get("standards_touched"))
    accepted = _string_set(conformance.get("accepted_standards"))
    status = conformance.get("status")
    if status == "green":
        return None
    if touched and touched <= accepted:
        return None
    return "conformance_not_green"


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _opens_circuit(
    session: Session,
    unit_id: uuid.UUID,
    signature: str,
    settings: DispatchSettings,
) -> bool:
    failures = session.scalars(
        select(DispatchRecord).where(
            DispatchRecord.work_unit_id == unit_id,
            DispatchRecord.failure_signature == signature,
            DispatchRecord.status.in_(("failed", "blocked")),
        )
    ).all()
    return len(failures) + 1 >= settings.failure_signature_threshold


def _next_runner_attempt(session: Session, unit: WorkUnit) -> int:
    latest = session.scalar(
        select(DispatchRecord.runner_attempt)
        .where(DispatchRecord.work_unit_id == unit.id)
        .order_by(DispatchRecord.runner_attempt.desc())
        .limit(1)
    )
    return max(unit.attempt_count, latest or 0) + 1


def _payload(unit: WorkUnit, settings: DispatchSettings, repository: str) -> dict[str, Any]:
    inputs = {
        "work_unit_id": str(unit.id),
        "orchestrator_url": settings.orchestrator_url,
    }
    return {
        "workflow_dispatch": {
            "repository": repository,
            "workflow_id": settings.workflow_id,
            "ref": settings.workflow_ref,
            "inputs": inputs,
        },
        "provenance_required": {
            "trailers": ["SDS-Unit", "SDS-Package-Rev"],
            "sds_unit": str(unit.id),
            "sds_package_rev": str(unit.work_package_revision_id),
        },
    }


def _record_dispatch(
    session: Session,
    command: DispatchCommand,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    settings: DispatchSettings,
    *,
    repository: str,
    status: str,
    payload: Mapping[str, Any],
    reason_code: str | None = None,
    failure_signature: str | None = None,
    github_run_id: str | None = None,
    github_run_url: str | None = None,
) -> DispatchRecord:
    record = DispatchRecord(
        work_unit_id=unit.id,
        work_package_revision_id=revision.id,
        runner_attempt=command.runner_attempt,
        status=status,
        reason_code=reason_code,
        idempotency_key=command.idempotency_key,
        target_repository=repository,
        workflow_id=settings.workflow_id,
        workflow_ref=settings.workflow_ref,
        github_run_id=github_run_id,
        github_run_url=github_run_url,
        failure_signature=failure_signature,
        payload=dict(payload),
    )
    session.add(record)
    session.flush()
    event = Event(
        actor_id=command.actor.actor_id,
        action=f"dispatch.{status}",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload={
            "dispatch_record_id": str(record.id),
            "runner_attempt": command.runner_attempt,
            "reason_code": reason_code,
            "target_repository": repository,
            "workflow_id": settings.workflow_id,
            "workflow_ref": settings.workflow_ref,
            "github_run_id": github_run_id,
            "failure_signature": failure_signature,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=f"{command.idempotency_key}:event",
    )
    session.add(event)
    session.flush()
    record.event_id = event.id
    session.flush()
    return record
