import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import (
    Approval,
    Dependency,
    WorkPackage,
    WorkPackageRevision,
    WorkUnit,
)


class PackageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def package_for_update(self, package_id: str) -> WorkPackage | None:
        return self.session.scalar(
            select(WorkPackage).where(WorkPackage.package_id == package_id).with_for_update()
        )

    def revision_for_update(
        self, work_package_id: uuid.UUID, revision: int
    ) -> WorkPackageRevision | None:
        return self.session.scalar(
            select(WorkPackageRevision)
            .where(
                WorkPackageRevision.work_package_id == work_package_id,
                WorkPackageRevision.revision == revision,
            )
            .with_for_update()
        )

    def unit_for_update(self, unit_id: uuid.UUID) -> WorkUnit | None:
        return self.session.get(WorkUnit, unit_id, with_for_update=True)

    def units_for_update(self, unit_ids: set[uuid.UUID]) -> tuple[WorkUnit, ...]:
        return tuple(
            self.session.scalars(
                select(WorkUnit)
                .where(WorkUnit.id.in_(unit_ids))
                .order_by(WorkUnit.id)
                .with_for_update()
            )
        )

    def dependency_for_update(self, dependency_id: uuid.UUID) -> Dependency | None:
        return self.session.get(Dependency, dependency_id, with_for_update=True)

    def dependencies_for_unit(self, unit_id: uuid.UUID) -> tuple[Dependency, ...]:
        return tuple(
            self.session.scalars(
                select(Dependency).where(Dependency.work_unit_id == unit_id).order_by(Dependency.id)
            )
        )

    def internal_dependency_edges(self) -> tuple[tuple[uuid.UUID, uuid.UUID], ...]:
        return tuple(
            (dependency.work_unit_id, dependency.depends_on_work_unit_id)
            for dependency in self.session.scalars(
                select(Dependency).where(Dependency.depends_on_work_unit_id.is_not(None))
            )
            if dependency.depends_on_work_unit_id is not None
        )

    def exact_authority_approval(self, unit: WorkUnit) -> Approval | None:
        if unit.authority_approval_id is None:
            return None
        return self.session.scalar(
            select(Approval).where(
                Approval.id == unit.authority_approval_id,
                Approval.subject_type == "authority",
                Approval.subject_id == unit.id,
                Approval.subject_revision_or_fingerprint == unit.authority_fingerprint,
                Approval.decision == "approved",
            )
        )
