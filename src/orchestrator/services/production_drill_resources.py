import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    PRODUCTION_DRILL_RESOURCE_TYPES,
    DeploymentObservation,
    Evidence,
    Observation,
    ProductionDrillResource,
    ProductionDrillRun,
    ReconciliationCondition,
    ReleaseArtifactBinding,
    WorkUnit,
)

RESOURCE_MODELS = {
    "work_unit": WorkUnit,
    "evidence": Evidence,
    "observation": Observation,
    "reconciliation_condition": ReconciliationCondition,
    "release_artifact": ReleaseArtifactBinding,
    "deployment_observation": DeploymentObservation,
}


def bind_created_drill_work_unit(
    session: Session,
    run_id: uuid.UUID,
    unit: WorkUnit,
) -> ProductionDrillResource:
    return _bind_created_resource(session, run_id, "work_unit", unit, WorkUnit)


def bind_created_drill_evidence(
    session: Session, run_id: uuid.UUID, evidence: Evidence
) -> ProductionDrillResource:
    return _bind_created_resource(session, run_id, "evidence", evidence, Evidence)


def bind_created_drill_observation(
    session: Session, run_id: uuid.UUID, observation: Observation
) -> ProductionDrillResource:
    return _bind_created_resource(session, run_id, "observation", observation, Observation)


def bind_created_drill_reconciliation_condition(
    session: Session, run_id: uuid.UUID, condition: ReconciliationCondition
) -> ProductionDrillResource:
    return _bind_created_resource(
        session, run_id, "reconciliation_condition", condition, ReconciliationCondition
    )


def bind_created_drill_release_artifact(
    session: Session, run_id: uuid.UUID, artifact: ReleaseArtifactBinding
) -> ProductionDrillResource:
    return _bind_created_resource(
        session, run_id, "release_artifact", artifact, ReleaseArtifactBinding
    )


def bind_created_drill_deployment_observation(
    session: Session, run_id: uuid.UUID, observation: DeploymentObservation
) -> ProductionDrillResource:
    return _bind_created_resource(
        session, run_id, "deployment_observation", observation, DeploymentObservation
    )


def _bind_created_resource[
    ResourceModel: (
        WorkUnit,
        Evidence,
        Observation,
        ReconciliationCondition,
        ReleaseArtifactBinding,
        DeploymentObservation,
    )
](
    session: Session,
    run_id: uuid.UUID,
    resource_type: str,
    resource: ResourceModel,
    expected_model: type[ResourceModel],
) -> ProductionDrillResource:
    """Register a row created by the writer that holds the concrete resource type."""
    if not isinstance(resource, expected_model):
        raise TypeError(f"expected {expected_model.__name__} for {resource_type}")
    return _bind_production_drill_resource(session, run_id, resource_type, resource.id)


def _bind_production_drill_resource(
    session: Session,
    run_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID,
) -> ProductionDrillResource:
    run = require_open_production_drill_run(session, run_id)
    if resource_type not in PRODUCTION_DRILL_RESOURCE_TYPES:
        raise DomainError(
            "production_drill_resource_type_invalid", "unsupported drill resource type", None
        )
    typed_resource = session.get(RESOURCE_MODELS[resource_type], resource_id)
    if typed_resource is None:
        raise DomainError(
            "production_drill_resource_not_found", "drill resource does not exist", None
        )
    if resource_type == "work_unit" and typed_resource.work_package_revision_id != run.revision_id:
        raise DomainError(
            "production_drill_resource_revision_mismatch",
            "work unit does not belong to the production drill revision",
            None,
        )
    existing = session.scalar(
        select(ProductionDrillResource)
        .where(
            ProductionDrillResource.resource_type == resource_type,
            ProductionDrillResource.resource_id == resource_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.run_id == run_id:
            return existing
        raise DomainError(
            "production_drill_resource_owned",
            "resource already belongs to a production drill run",
            None,
        )
    resource = ProductionDrillResource(
        run_id=run_id,
        resource_type=resource_type,
        resource_id=resource_id,
        closed_at=None,
    )
    session.add(resource)
    session.flush()
    return resource


def require_production_drill_resource(
    session: Session,
    run_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID,
) -> ProductionDrillResource:
    require_open_production_drill_run(session, run_id)
    resource = session.scalar(
        select(ProductionDrillResource).where(
            ProductionDrillResource.run_id == run_id,
            ProductionDrillResource.resource_type == resource_type,
            ProductionDrillResource.resource_id == resource_id,
        )
    )
    if resource is None:
        raise DomainError(
            "production_drill_resource_not_owned",
            "resource does not belong to the production drill run",
            None,
        )
    return resource


def is_not_production_drill_resource(resource_type: str, resource_id: object):
    return ~exists().where(
        ProductionDrillResource.resource_type == resource_type,
        ProductionDrillResource.resource_id == resource_id,
    )


def reject_production_drill_resource(
    session: Session, resource_type: str, resource_id: uuid.UUID
) -> None:
    if (
        session.scalar(
            select(ProductionDrillResource.id).where(
                ProductionDrillResource.resource_type == resource_type,
                ProductionDrillResource.resource_id == resource_id,
            )
        )
        is not None
    ):
        raise DomainError(
            "production_drill_resource_requires_drill_writer",
            "production drill resources must use the production drill writer",
            None,
        )


def require_open_production_drill_run(session: Session, run_id: uuid.UUID) -> ProductionDrillRun:
    run = session.get(ProductionDrillRun, run_id, with_for_update=True)
    if run is None:
        raise DomainError(
            "production_drill_run_not_found", "production drill run does not exist", None
        )
    if run.status not in {"open", "asserting"}:
        raise DomainError("production_drill_run_not_open", "production drill run is not open", None)
    return run
