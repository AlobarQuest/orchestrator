from enum import StrEnum


class WorkUnitState(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    CLAIMED = "claimed"
    EXECUTING = "executing"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMITTED = "submitted"
    VERIFYING = "verifying"
    AWAITING_REVIEW = "awaiting_review"
    REVISION_REQUIRED = "revision_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActorRole(StrEnum):
    SYSTEM = "system"
    WORKER = "worker"
    VERIFIER = "verifier"
    HUMAN = "human"
    # WS-P3.6: the observe-and-report role. It may record an observation and nothing else.
    #
    # It is confined by ABSENCE, not by a rule written for it: every role gate in this codebase
    # is an allowlist naming the roles it admits, so a member added here is refused everywhere
    # until some gate names it. `EDGE_ROLES` below is the strongest case -- OBSERVER appears in
    # none of the four edge sets, so `authorize_transition` refuses it on every legal edge, which
    # is every lifecycle command in the system.
    #
    # The one gate that names it is `services/observations.py::_authorize_actor`. If you are
    # adding a second, you are widening what an observation producer may do to this estate --
    # which is the thing this role exists to prevent. See ADR-0017.
    OBSERVER = "observer"


# not-a-vocabulary: DB CHECK-pinned, not a cross-boundary vocabulary. This tuple is the SINGLE
# source of truth for the controlled risk-class vocabulary an adjudication's `risk` may declare.
# `persistence/models.py` builds the `ck_adjudications_risk_class` CHECK's SQL from this same
# tuple, and migration 0017 inlines that identical literal into the schema. The service layer
# (`services/evidence.py::_validate_adjudication_fields`) validates against this tuple too, so an
# out-of-vocab value is rejected with a clean `DomainError` before it can ever reach the CHECK.
# The cross-boundary-vocabulary detector's same-module CheckConstraint scan can't see this because
# the CheckConstraint lives in persistence/models.py while this definition lives here --
# structurally the same as the ~20 schema-pinned enums it excludes automatically, just split
# across modules.
WAIVER_RISK_CLASSES: tuple[str, ...] = ("low", "medium", "high", "critical")


def _edges(
    source: WorkUnitState, *targets: WorkUnitState
) -> set[tuple[WorkUnitState, WorkUnitState]]:
    return {(source, target) for target in targets}


LEGAL_EDGES = frozenset(
    _edges(WorkUnitState.DRAFT, WorkUnitState.READY)
    | _edges(WorkUnitState.READY, WorkUnitState.CLAIMED, WorkUnitState.FAILED)
    | _edges(
        WorkUnitState.CLAIMED,
        WorkUnitState.EXECUTING,
        WorkUnitState.BLOCKED,
        WorkUnitState.AWAITING_APPROVAL,
        WorkUnitState.FAILED,
        WorkUnitState.CANCELLED,
    )
    | _edges(
        WorkUnitState.EXECUTING,
        WorkUnitState.SUBMITTED,
        WorkUnitState.BLOCKED,
        WorkUnitState.AWAITING_APPROVAL,
        WorkUnitState.FAILED,
        WorkUnitState.CANCELLED,
    )
    | _edges(
        WorkUnitState.SUBMITTED,
        WorkUnitState.VERIFYING,
        WorkUnitState.REVISION_REQUIRED,
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.FAILED,
        WorkUnitState.COMPLETED,
    )
    | _edges(
        WorkUnitState.VERIFYING,
        WorkUnitState.REVISION_REQUIRED,
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.FAILED,
        WorkUnitState.COMPLETED,
    )
    | _edges(WorkUnitState.BLOCKED, WorkUnitState.READY)
    | _edges(
        WorkUnitState.AWAITING_APPROVAL,
        WorkUnitState.READY,
        WorkUnitState.CANCELLED,
    )
    | _edges(
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.COMPLETED,
        WorkUnitState.REVISION_REQUIRED,
    )
    | _edges(WorkUnitState.REVISION_REQUIRED, WorkUnitState.READY)
    | _edges(WorkUnitState.FAILED, WorkUnitState.READY, WorkUnitState.CANCELLED)
)
