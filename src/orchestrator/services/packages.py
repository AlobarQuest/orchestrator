import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import (
    AuthorityEnvelope,
    authority_fingerprint,
    normalize_authority,
)
from orchestrator.kernel.context import context_fingerprint
from orchestrator.kernel.leases import DEFAULT_MAX_ATTEMPTS
from orchestrator.kernel.readiness import (
    DependencyReadiness,
    ReadinessDecision,
    ReadinessFacts,
    evaluate_readiness_facts,
)
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Approval,
    ApprovedDecomposition,
    DecompositionProposalUnit,
    Dependency,
    Event,
    WorkPackage,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.persistence.repositories import PackageRepository


@dataclass(frozen=True)
class DependencySpec:
    kind: str
    required_state_or_condition: str
    depends_on_work_unit_id: uuid.UUID | None = None
    external_ref: str | None = None

    @classmethod
    def work_unit(cls, unit_id: uuid.UUID, required_state: str) -> "DependencySpec":
        return cls("work_unit", required_state, depends_on_work_unit_id=unit_id)

    @classmethod
    def external(cls, reference: str, condition: str) -> "DependencySpec":
        return cls("external_system", condition, external_ref=reference)


def record_approval(
    session: Session,
    *,
    unit_id: uuid.UUID,
    subject_type: str,
    actor_id: str,
    actor_role: ActorRole,
    reason: str,
    idempotency_key: str,
    expected_version: int,
    standing_context: Mapping[str, object] | None = None,
) -> Approval:
    _require_human(actor_id, actor_role)
    if subject_type not in {"authority", "action"}:
        raise DomainError("approval_type_invalid", "unsupported approval type", None)
    unit = PackageRepository(session).unit_for_update(unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    existing = session.scalar(select(Approval).where(Approval.idempotency_key == idempotency_key))
    fingerprint = _approval_fingerprint(unit, subject_type, standing_context, expected_version)
    if existing is not None:
        event = session.get(Event, existing.event_id)
        if (
            existing.subject_type == subject_type
            and existing.subject_id == unit.id
            and existing.subject_revision_or_fingerprint == fingerprint
            and existing.approved_by == actor_id
            and existing.reason == reason
            and event is not None
            and event.payload.get("expected_version") == expected_version
        ):
            return existing
        raise DomainError(
            "idempotency_conflict",
            "idempotency key belongs to a different operation",
            "use a new idempotency key",
        )
    if unit.version != expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    event_id = uuid.uuid4()
    approval = Approval(
        subject_type=subject_type,
        subject_id=unit.id,
        subject_revision_or_fingerprint=fingerprint,
        decision="approved",
        approved_by=actor_id,
        reason=reason,
        event_id=event_id,
        idempotency_key=idempotency_key,
    )
    session.add(approval)
    session.flush()
    if subject_type == "authority" and not standing_context:
        unit.authority_approval_id = approval.id
    session.add(
        Event(
            id=event_id,
            actor_id=actor_id,
            action=f"{subject_type}.approved",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=unit.state,
            to_state=unit.state,
            payload={
                "approval_id": str(approval.id),
                "expected_version": expected_version,
                "context_fingerprint": (
                    fingerprint
                    if subject_type == "authority" and standing_context is not None
                    else None
                ),
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
        )
    )
    session.flush()
    return approval


def _approval_fingerprint(
    unit: WorkUnit,
    subject_type: str,
    standing_context: Mapping[str, object] | None,
    expected_version: int,
) -> str:
    if subject_type == "authority":
        if standing_context is not None:
            return context_fingerprint(standing_context)
        return unit.authority_fingerprint
    return str(expected_version)


def register_revision(
    session: Session,
    *,
    package_id: str,
    source_repository: str,
    revision: int,
    content_hash: str,
    source_path: str,
    source_commit: str,
    approved_by: str,
    approved_at: datetime,
    approval_event_id: str,
    enforcement_snapshot: Mapping[str, Any],
    authority: AuthorityEnvelope,
    registry_version: int,
    profile: str | None = None,
    status_at_intake: str | None = None,
    intake_source: str = "manual_ws31",
    approval_ledger_commit: str | None = None,
    verification_mode: str | None = None,
    verification_limitations: Mapping[str, Any] | list[Any] | None = None,
    actor_id: str,
    actor_role: ActorRole,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> WorkPackageRevision:
    _require_human(actor_id, actor_role)
    if expected_version not in {None, 0}:
        raise DomainError(
            "version_conflict",
            "revision registration requires expected version 0",
            "reload",
            current_version=0,
        )
    repository = PackageRepository(session)
    observed_package = repository.package(package_id)
    repository.lock_package_registration(package_id)
    package = repository.package_for_update(package_id)
    concurrent_initial_registration = observed_package is None and package is not None
    if package is None:
        package = WorkPackage(package_id=package_id, source_repository=source_repository)
        session.add(package)
        session.flush()
    elif package.source_repository != source_repository:
        raise _revision_conflict()

    fingerprint = authority_fingerprint(authority)
    normalized_snapshot = _normalize_json(
        {**enforcement_snapshot, "authority": authority.normalized()}
    )
    existing = repository.revision_for_update(package.id, revision)
    candidate = {
        "content_hash": content_hash,
        "source_path": source_path,
        "source_commit": source_commit,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "approval_event_id": approval_event_id,
        "enforcement_snapshot": normalized_snapshot,
        "authority_fingerprint": fingerprint,
        "registry_version": registry_version,
        "profile": profile,
        "status_at_intake": status_at_intake,
        "intake_source": intake_source,
        "approval_ledger_commit": approval_ledger_commit,
        "verification_mode": verification_mode,
        "verification_limitations": _normalize_json(verification_limitations),
        "registered_by": actor_id,
    }
    command = {
        "action": "revision.registered",
        "package_id": package_id,
        "source_repository": source_repository,
        "revision": revision,
        **{field: _json_identity(value) for field, value in candidate.items()},
    }
    replay = _registration_replay(
        session, idempotency_key, "revision.registered", command, WorkPackageRevision
    )
    if replay is not None:
        return replay
    if existing is not None:
        if all(getattr(existing, field) == value for field, value in candidate.items()):
            _record_registration(session, existing.id, actor_id, idempotency_key, command)
            return existing
        raise _revision_conflict()
    if concurrent_initial_registration:
        raise _revision_conflict()

    registered = WorkPackageRevision(
        work_package_id=package.id,
        revision=revision,
        **candidate,
    )
    session.add(registered)
    session.flush()
    _record_registration(session, registered.id, actor_id, idempotency_key, command)
    return registered


def register_approved_unit(
    session: Session,
    *,
    revision_id: uuid.UUID,
    unit_key: str,
    title: str,
    outcome: str,
    required_capability: str,
    authority: AuthorityEnvelope,
    authority_payload: Mapping[str, Any] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    approved_by: str,
    approved_at: datetime,
    actor_id: str,
    actor_role: ActorRole,
    unit_id: uuid.UUID | None = None,
    dependencies: tuple[DependencySpec, ...] = (),
    activation_source: str = "legacy_manual",
    approved_decomposition_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> WorkUnit:
    _require_human(actor_id, actor_role)
    if expected_version not in {None, 0}:
        raise DomainError(
            "version_conflict",
            "work unit registration requires expected version 0",
            "reload",
            current_version=0,
        )
    revision = session.get(WorkPackageRevision, revision_id, with_for_update=True)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    _require_allowed_unit_activation(
        session,
        revision,
        unit_key=unit_key,
        title=title,
        outcome=outcome,
        required_capability=required_capability,
        authority=authority,
        max_attempts=max_attempts,
        approved_by=approved_by,
        approved_at=approved_at,
        activation_source=activation_source,
        approved_decomposition_id=approved_decomposition_id,
    )
    command = {
        "action": "work_unit.registered",
        "revision_id": str(revision_id),
        "unit_key": unit_key,
        "title": title,
        "outcome": outcome,
        "required_capability": required_capability,
        "authority": _normalize_json(
            authority_payload if authority_payload is not None else authority.normalized()
        ),
        "max_attempts": max_attempts,
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat(),
        "unit_id": str(unit_id) if unit_id is not None else None,
        "activation_source": activation_source,
        "approved_decomposition_id": str(approved_decomposition_id)
        if approved_decomposition_id is not None
        else None,
        "dependencies": [_json_identity(spec.__dict__) for spec in dependencies],
    }
    replay = _registration_replay(
        session, idempotency_key, "work_unit.registered", command, WorkUnit
    )
    if replay is not None:
        return replay
    existing_unit = session.scalar(
        select(WorkUnit)
        .where(
            WorkUnit.work_package_revision_id == revision.id,
            WorkUnit.unit_key == unit_key,
        )
        .with_for_update()
    )
    unit_candidate = {
        "title": title,
        "outcome": outcome,
        "required_capability": required_capability,
        "authority": _normalize_json(
            authority_payload if authority_payload is not None else authority.normalized()
        ),
        "authority_fingerprint": authority_fingerprint(authority),
        "max_attempts": max_attempts,
        "decomposition_approved_by": approved_by,
        "decomposition_approved_at": approved_at,
    }
    if existing_unit is not None:
        if all(getattr(existing_unit, field) == value for field, value in unit_candidate.items()):
            _record_registration(session, existing_unit.id, actor_id, idempotency_key, command)
            return existing_unit
        raise DomainError(
            "unit_conflict",
            "work unit is already registered with different content",
            None,
        )
    unit = WorkUnit(
        id=unit_id or uuid.uuid4(),
        work_package_revision_id=revision.id,
        unit_key=unit_key,
        title=title,
        outcome=outcome,
        state=WorkUnitState.DRAFT,
        decomposition_approved_by=approved_by,
        decomposition_approved_at=approved_at,
        required_capability=required_capability,
        authority=unit_candidate["authority"],
        authority_fingerprint=authority_fingerprint(authority),
        max_attempts=max_attempts,
    )
    session.add(unit)
    session.flush()
    for dependency in dependencies:
        register_dependency(session, work_unit_id=unit.id, spec=dependency)
    _record_registration(session, unit.id, actor_id, idempotency_key, command)
    return unit


def _registration_replay[RegistrationModel: (WorkPackageRevision, WorkUnit)](
    session: Session,
    idempotency_key: str | None,
    action: str,
    command: dict[str, Any],
    model: type[RegistrationModel],
) -> RegistrationModel | None:
    if idempotency_key is None:
        return None
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if event is None:
        return None
    if event.action != action or event.payload.get("command") != command:
        raise DomainError(
            "idempotency_conflict",
            "idempotency key belongs to a different operation",
            "use a new idempotency key",
        )
    value = session.get(model, event.subject_id)
    if value is None:
        raise DomainError("event_invalid", "registration event subject does not exist", None)
    return value


def _require_allowed_unit_activation(
    session: Session,
    revision: WorkPackageRevision,
    *,
    unit_key: str,
    title: str,
    outcome: str,
    required_capability: str,
    authority: AuthorityEnvelope,
    max_attempts: int,
    approved_by: str,
    approved_at: datetime,
    activation_source: str,
    approved_decomposition_id: uuid.UUID | None,
) -> None:
    if revision.intake_source == "protocol_fixture":
        raise DomainError(
            "protocol_fixture_not_executable",
            "protocol fixture intake cannot create executable work units",
            None,
        )
    if revision.intake_source != "package_cli":
        return
    if activation_source != "approved_decomposition" or approved_decomposition_id is None:
        raise _decomposition_approval_required()
    approved = session.scalar(
        select(ApprovedDecomposition)
        .where(
            ApprovedDecomposition.id == approved_decomposition_id,
            ApprovedDecomposition.work_package_revision_id == revision.id,
            ApprovedDecomposition.superseded_at.is_(None),
        )
        .with_for_update()
    )
    if approved is None:
        raise _decomposition_approval_required()
    proposal_unit = session.scalar(
        select(DecompositionProposalUnit)
        .where(
            DecompositionProposalUnit.proposal_id == approved.proposal_id,
            DecompositionProposalUnit.unit_key == unit_key,
        )
        .with_for_update()
    )
    if proposal_unit is None:
        raise _decomposition_approval_required()
    if (
        proposal_unit.title != title
        or proposal_unit.outcome != outcome
        or proposal_unit.required_capability != required_capability
        or proposal_unit.max_attempts != max_attempts
        or authority_fingerprint(normalize_authority(proposal_unit.authority))
        != authority_fingerprint(authority)
        or approved.approved_by != approved_by
        or approved.approved_at != approved_at
    ):
        raise _decomposition_approval_required()


def _decomposition_approval_required() -> DomainError:
    return DomainError(
        "decomposition_approval_required",
        "WS-3.2 package revisions require approved decomposition",
        None,
    )


def _record_registration(
    session: Session,
    subject_id: uuid.UUID,
    actor_id: str,
    idempotency_key: str | None,
    command: dict[str, Any],
) -> None:
    if idempotency_key is None:
        return
    session.add(
        Event(
            occurred_at=TransactionClock().now(session),
            actor_id=actor_id,
            action=str(command["action"]),
            subject_type="work_package_revision"
            if command["action"] == "revision.registered"
            else "work_unit",
            subject_id=subject_id,
            from_state=None,
            to_state=None,
            payload={"command": command},
            correlation_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
        )
    )
    session.flush()


def _json_identity(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {key: _json_identity(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_identity(item) for item in value]
    return value


def register_dependency(
    session: Session,
    *,
    work_unit_id: uuid.UUID,
    spec: DependencySpec,
) -> Dependency:
    _validate_dependency_spec(spec)
    repository = PackageRepository(session)
    unit_ids = {work_unit_id}
    if spec.depends_on_work_unit_id is not None:
        unit_ids.add(spec.depends_on_work_unit_id)
    locked_units = repository.units_for_update(unit_ids)
    if not any(unit.id == work_unit_id for unit in locked_units):
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    if len(locked_units) != len(unit_ids):
        raise DomainError("dependency_target_not_found", "dependency target does not exist", None)
    if spec.depends_on_work_unit_id == work_unit_id:
        raise DomainError("dependency_cycle", "internal dependency cycle detected", None)
    candidate_edges = repository.internal_dependency_edges()
    if spec.depends_on_work_unit_id is not None:
        candidate_edges += ((work_unit_id, spec.depends_on_work_unit_id),)
    if _has_internal_dependency_cycle(candidate_edges):
        raise DomainError("dependency_cycle", "internal dependency cycle detected", None)
    dependency = Dependency(
        work_unit_id=work_unit_id,
        kind=spec.kind,
        required_state_or_condition=spec.required_state_or_condition,
        depends_on_work_unit_id=spec.depends_on_work_unit_id,
        external_ref=spec.external_ref,
        status="pending",
    )
    session.add(dependency)
    session.flush()
    return dependency


def register_dependency_command(
    session: Session,
    *,
    work_unit_id: uuid.UUID,
    spec: DependencySpec,
    actor_id: str,
    actor_role: ActorRole,
    expected_version: int,
    idempotency_key: str,
) -> Dependency:
    if actor_role not in {ActorRole.HUMAN, ActorRole.SYSTEM}:
        raise DomainError("role_forbidden", "actor may not register dependencies", None)
    unit = PackageRepository(session).unit_for_update(work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    identity = _dependency_command_identity(
        actor_id=actor_id,
        expected_version=expected_version,
        spec=spec,
    )
    replay = _dependency_replay(
        session,
        work_unit_id=unit.id,
        identity=identity,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    if unit.version != expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    return _register_dependency_with_event(
        session,
        unit=unit,
        spec=spec,
        actor_id=actor_id,
        actor_role=actor_role,
        idempotency_key=idempotency_key,
        identity=identity,
    )


def register_dependency_with_event(
    session: Session,
    *,
    work_unit_id: uuid.UUID,
    spec: DependencySpec,
    actor_id: str,
    actor_role: ActorRole,
    idempotency_key: str,
) -> Dependency:
    if actor_role not in {ActorRole.HUMAN, ActorRole.SYSTEM}:
        raise DomainError("role_forbidden", "actor may not register dependencies", None)
    unit = PackageRepository(session).unit_for_update(work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    identity = _dependency_command_identity(
        actor_id=actor_id,
        expected_version=unit.version,
        spec=spec,
    )
    replay = _dependency_replay(
        session,
        work_unit_id=unit.id,
        identity=identity,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    return _register_dependency_with_event(
        session,
        unit=unit,
        spec=spec,
        actor_id=actor_id,
        actor_role=actor_role,
        idempotency_key=idempotency_key,
        identity=identity,
    )


def resolve_dependency(
    session: Session,
    *,
    dependency_id: uuid.UUID,
    status: str,
    resolved_by: str,
    resolution_event_id: uuid.UUID,
    detail: Mapping[str, Any],
) -> Dependency:
    if status not in {"satisfied", "failed"}:
        raise DomainError(
            "invalid_dependency_status",
            "resolution must be satisfied or failed",
            None,
        )
    dependency = PackageRepository(session).dependency_for_update(dependency_id)
    if dependency is None:
        raise DomainError("dependency_not_found", "dependency does not exist", None)
    dependency.status = status
    dependency.resolved_by = resolved_by
    dependency.resolved_at = datetime.now(UTC)
    dependency.resolution_event_id = resolution_event_id
    dependency.detail = _normalize_json(detail)
    session.flush()
    return dependency


def resolve_dependency_command(
    session: Session,
    *,
    dependency_id: uuid.UUID,
    status: str,
    detail: Mapping[str, Any],
    actor_id: str,
    actor_role: ActorRole,
    expected_version: int,
    idempotency_key: str,
) -> Dependency:
    if actor_role not in {ActorRole.HUMAN, ActorRole.SYSTEM}:
        raise DomainError("role_forbidden", "actor may not resolve dependencies", None)
    dependency = PackageRepository(session).dependency_for_update(dependency_id)
    if dependency is None:
        raise DomainError("dependency_not_found", "dependency does not exist", None)
    unit = PackageRepository(session).unit_for_update(dependency.work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    existing = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    identity = {
        "actor_id": actor_id,
        "dependency_id": str(dependency_id),
        "detail": _json_identity(detail),
        "expected_version": expected_version,
        "status": status,
    }
    if existing is not None:
        if existing.payload.get("command") != identity:
            raise DomainError(
                "idempotency_conflict",
                "idempotency key belongs to a different operation",
                "use a new idempotency key",
            )
        return dependency
    if unit.version != expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    resolved = resolve_dependency(
        session,
        dependency_id=dependency_id,
        status=status,
        resolved_by=actor_id,
        resolution_event_id=uuid.uuid4(),
        detail=detail,
    )
    session.add(
        Event(
            id=resolved.resolution_event_id,
            actor_id=actor_id,
            action="dependency.resolved",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=unit.state,
            to_state=unit.state,
            payload={"command": identity},
            correlation_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
        )
    )
    session.flush()
    return resolved


def evaluate_readiness(
    session: Session, unit_id: uuid.UUID, *, for_update: bool = True
) -> ReadinessDecision:
    repository = PackageRepository(session)
    unit = repository.unit_for_update(unit_id) if for_update else session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    dependencies = tuple(
        DependencyReadiness(
            dependency_id=dependency.id,
            status=dependency.status,
            detail=_dependency_detail(dependency),
        )
        for dependency in repository.dependencies_for_unit(unit.id)
    )
    return evaluate_readiness_facts(
        ReadinessFacts(
            revision_approved=bool(revision.approved_by and revision.approval_event_id),
            decomposition_approved=bool(
                unit.decomposition_approved_by and unit.decomposition_approved_at
            ),
            authority_approved=repository.exact_authority_approval(unit) is not None,
            dependencies=dependencies,
        )
    )


def _require_human(actor_id: str, actor_role: ActorRole) -> None:
    if not actor_id or actor_role is not ActorRole.HUMAN:
        raise DomainError(
            "human_actor_required",
            "registration requires a registered human actor",
            None,
        )


def _revision_conflict() -> DomainError:
    return DomainError(
        "revision_conflict",
        "package revision is already registered with different content",
        None,
    )


def _normalize_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _normalize_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    return value


def _validate_dependency_spec(spec: DependencySpec) -> None:
    has_internal = spec.depends_on_work_unit_id is not None
    has_external = spec.external_ref is not None
    if has_internal == has_external:
        raise DomainError(
            "invalid_dependency",
            "dependency requires exactly one reference",
            None,
        )
    if spec.kind == "work_unit" and not has_internal:
        raise DomainError("invalid_dependency", "work-unit dependency requires a unit", None)
    if spec.kind != "work_unit" and not has_external:
        raise DomainError("invalid_dependency", "external dependency requires a reference", None)


def _dependency_command_identity(
    *, actor_id: str, expected_version: int, spec: DependencySpec
) -> dict[str, Any]:
    return {
        "actor_id": actor_id,
        "expected_version": expected_version,
        "spec": _json_identity(spec.__dict__),
    }


def _dependency_replay(
    session: Session,
    *,
    work_unit_id: uuid.UUID,
    identity: dict[str, Any],
    idempotency_key: str,
) -> Dependency | None:
    existing = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if existing is None:
        return None
    dependency_id = existing.payload.get("dependency_id")
    if (
        existing.action != "dependency.registered"
        or existing.subject_type != "work_unit"
        or existing.subject_id != work_unit_id
        or existing.payload.get("command") != identity
        or not isinstance(dependency_id, str)
    ):
        raise DomainError(
            "idempotency_conflict",
            "idempotency key belongs to a different operation",
            "use a new idempotency key",
        )
    dependency = session.get(Dependency, uuid.UUID(dependency_id))
    if dependency is None:
        raise DomainError("dependency_not_found", "dependency does not exist", None)
    return dependency


def _register_dependency_with_event(
    session: Session,
    *,
    unit: WorkUnit,
    spec: DependencySpec,
    actor_id: str,
    actor_role: ActorRole,
    idempotency_key: str,
    identity: dict[str, Any],
) -> Dependency:
    if actor_role not in {ActorRole.HUMAN, ActorRole.SYSTEM}:
        raise DomainError("role_forbidden", "actor may not register dependencies", None)
    dependency = register_dependency(session, work_unit_id=unit.id, spec=spec)
    session.add(
        Event(
            occurred_at=TransactionClock().now(session),
            actor_id=actor_id,
            action="dependency.registered",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=unit.state,
            to_state=unit.state,
            payload={"command": identity, "dependency_id": str(dependency.id)},
            correlation_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
        )
    )
    session.flush()
    return dependency


def _has_internal_dependency_cycle(
    edges: tuple[tuple[uuid.UUID, uuid.UUID], ...],
) -> bool:
    graph: dict[uuid.UUID, set[uuid.UUID]] = {}
    for source, target in edges:
        graph.setdefault(source, set()).add(target)
    visiting: set[uuid.UUID] = set()
    visited: set[uuid.UUID] = set()

    def visit(node: uuid.UUID) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(dependency) for dependency in graph.get(node, ())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _dependency_detail(dependency: Dependency) -> str:
    reference = dependency.depends_on_work_unit_id or dependency.external_ref
    return f"{dependency.kind} {reference} requires {dependency.required_state_or_condition}"
