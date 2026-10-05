"""Whether a work unit is ready to run: the facts read from the database, judged by the kernel.

Lives in `services/lifecycle` because the lifecycle both enforces it (the `DRAFT -> READY` edge
is guarded on it) and acts on it (`ready_if_satisfied` takes that edge once it holds, SDS 1.1
item 2d-1). The judgement itself is `kernel/readiness.py`.
"""

import uuid

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.readiness import (
    DependencyReadiness,
    ReadinessDecision,
    ReadinessFacts,
    evaluate_readiness_facts,
)
from orchestrator.persistence.models import Dependency, WorkPackageRevision, WorkUnit
from orchestrator.persistence.repositories import PackageRepository
from orchestrator.services.intake.authority_gate import human_authority_gate


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
            authority_recognised_by_policy=not human_authority_gate(unit, revision).refusals,
            dependencies=dependencies,
        )
    )


def _dependency_detail(dependency: Dependency) -> str:
    reference = dependency.depends_on_work_unit_id or dependency.external_ref
    return f"{dependency.kind} {reference} requires {dependency.required_state_or_condition}"
