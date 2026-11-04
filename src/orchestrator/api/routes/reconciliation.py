from fastapi import APIRouter

from orchestrator.api.dependencies import ActorDep, SessionDep, SettingsDep
from orchestrator.api.routes.common import (
    ERROR_RESPONSES,
    require_zero_expected_version,
)
from orchestrator.api.schemas.reconciliation import (
    ConsistencyReportResponse,
    InFlightUnitsResponse,
    ReconciliationDetectCommand,
    ReconciliationDetectResponse,
)
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.services.lifecycle.lifecycle import require_operator_actor
from orchestrator.services.reconciliation.consistency import check_consistency
from orchestrator.services.reconciliation.in_flight import in_flight_snapshot
from orchestrator.services.reconciliation.reconciliation_detection import (
    detect_reconciliation_conditions,
)

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.post("/reconciliation/detect", response_model=ReconciliationDetectResponse)
def reconciliation_detect(
    body: ReconciliationDetectCommand,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    """AC-003. Run by hand with `orchestrator reconcile-detect`; no scheduled runner exists
    (ADR-0048). Creates no unit and sets no lifecycle state."""
    require_zero_expected_version(body.expected_version, "reconciliation detection")
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may run reconciliation detection",
            None,
        )
    return detect_reconciliation_conditions(
        session,
        actor,
        stall_seconds=settings.reconcile_split_brain_stall_seconds,
    ).as_dict()


@router.get("/in-flight-units", response_model=InFlightUnitsResponse)
def in_flight_units(
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-009. Read-only. Kept by ADR-0048 (with release_bindings); no program in this repository
    calls it."""
    require_operator_actor(actor)
    return in_flight_snapshot(session)


@router.get("/consistency-check", response_model=ConsistencyReportResponse)
def consistency_check(
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-008. Reports projection-vs-source divergence. Never repairs."""
    require_operator_actor(actor)
    return check_consistency(session)
