import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import (
    AuthorityEnvelope,
    authority_fingerprint,
)
from orchestrator.kernel.readiness import (
    DependencyReadiness,
    ReadinessDecision,
    ReadinessFacts,
    evaluate_readiness_facts,
)
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Dependency, WorkPackage, WorkPackageRevision, WorkUnit
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
    approval_event_id: uuid.UUID,
    enforcement_snapshot: Mapping[str, Any],
    authority: AuthorityEnvelope,
    registry_version: int,
    actor_id: str,
    actor_role: ActorRole,
) -> WorkPackageRevision:
    _require_human(actor_id, actor_role)
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
        "registered_by": actor_id,
    }
    if existing is not None:
        if all(getattr(existing, field) == value for field, value in candidate.items()):
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
    max_attempts: int,
    approved_by: str,
    approved_at: datetime,
    actor_id: str,
    actor_role: ActorRole,
    unit_id: uuid.UUID | None = None,
    dependencies: tuple[DependencySpec, ...] = (),
) -> WorkUnit:
    _require_human(actor_id, actor_role)
    revision = session.get(WorkPackageRevision, revision_id, with_for_update=True)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
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
        authority_fingerprint=authority_fingerprint(authority),
        max_attempts=max_attempts,
    )
    session.add(unit)
    session.flush()
    for dependency in dependencies:
        register_dependency(session, work_unit_id=unit.id, spec=dependency)
    return unit


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


def evaluate_readiness(session: Session, unit_id: uuid.UUID) -> ReadinessDecision:
    repository = PackageRepository(session)
    unit = repository.unit_for_update(unit_id)
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
