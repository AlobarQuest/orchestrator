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


def bind_production_drill_resource(
    session: Session,
    run_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID,
) -> ProductionDrillResource:
    """Bind a synthetic resource while its run is open.

    This is intentionally not part of ordinary lifecycle command signatures: only
    production-drill commands import this boundary and may introduce a run ID.
    """
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


def require_open_production_drill_run(session: Session, run_id: uuid.UUID) -> ProductionDrillRun:
    run = session.get(ProductionDrillRun, run_id, with_for_update=True)
    if run is None:
        raise DomainError(
            "production_drill_run_not_found", "production drill run does not exist", None
        )
    if run.status != "open":
        raise DomainError("production_drill_run_not_open", "production drill run is not open", None)
    return run
