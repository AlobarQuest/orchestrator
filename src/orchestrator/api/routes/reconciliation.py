from uuid import UUID

from fastapi import APIRouter

from orchestrator.api.dependencies import ActorDep, SessionDep, SettingsDep
from orchestrator.api.routes.common import (
    ERROR_RESPONSES,
    raise_error,
    require_zero_expected_version,
)
from orchestrator.api.schemas.reconciliation import (
    ConsistencyReportResponse,
    InFlightUnitsResponse,
    ReconciliationDetectCommand,
    ReconciliationDetectResponse,
    TrackerBindingCommand,
    TrackerBindingResponse,
    TrackerReconciliationDetectCommand,
)
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.services.lifecycle.lifecycle import require_operator_actor
from orchestrator.services.reconciliation.consistency import check_consistency
from orchestrator.services.reconciliation.in_flight import in_flight_snapshot
from orchestrator.services.reconciliation.reconciliation_detection import (
    ObservedTrackerItem,
    detect_reconciliation_conditions,
    detect_tracker_conditions,
)
from orchestrator.services.reconciliation.tracker_bindings import (
    list_tracker_bindings,
    upsert_tracker_binding,
)

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.post("/work-units/{unit_id}/tracker-binding", response_model=TrackerBindingResponse)
def tracker_binding(
    unit_id: UUID,
    body: TrackerBindingCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """Record the tracker item a work unit is projected onto. Projection only, SYSTEM-written.

    Deliberately written by our own side of the ledger: the tracker is never canonical, so a
    binding never derives from tracker content and never changes the unit's state.
    """
    require_zero_expected_version(body.expected_version, "tracker binding")
    return raise_error(
        upsert_tracker_binding(
            session,
            actor=actor,
            work_unit_id=unit_id,
            tracker_system=body.tracker_system,
            external_item_id=body.external_item_id,
            external_url=body.external_url,
            projected_state=body.projected_state,
        )
    )


@router.post("/reconciliation/detect", response_model=ReconciliationDetectResponse)
def reconciliation_detect(
    body: ReconciliationDetectCommand,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    """AC-003. Operator/runner-invoked: creates no unit and sets no lifecycle state."""
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


@router.post("/reconciliation/tracker-detect", response_model=ReconciliationDetectResponse)
def tracker_reconciliation_detect(
    body: TrackerReconciliationDetectCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """Inbound tracker reconciliation. SYSTEM-only, report-only: records append-only divergence
    conditions, creates no unit and sets no lifecycle state. The tracker is never canonical."""
    require_zero_expected_version(body.expected_version, "tracker reconciliation detection")
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may run tracker reconciliation detection",
            None,
        )
    return detect_tracker_conditions(
        session,
        actor,
        observed_states=[
            ObservedTrackerItem(i.tracker_system, i.external_item_id, i.observed_completed)
            for i in body.observed_states
        ],
    ).as_dict()


@router.get("/in-flight-units", response_model=InFlightUnitsResponse)
def in_flight_units(
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-009. The reconciliation runner's read surface. Read-only."""
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


@router.get("/tracker-bindings", response_model=list[TrackerBindingResponse])
def tracker_bindings_route(
    _actor: ActorDep,
    session: SessionDep,
    tracker_system: str | None = None,
) -> object:
    return list_tracker_bindings(session, tracker_system=tracker_system)
