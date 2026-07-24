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


# not-a-vocabulary: DB CHECK-pinned, not a cross-boundary vocabulary. The adjudications.risk
# CHECK (ck_adjudications_risk_class, migration 0017) is the single source of truth; models.py
# builds that CHECK's SQL from this same tuple. The cross-boundary-vocabulary detector's
# same-module CheckConstraint scan can't see this because the CheckConstraint lives in
# persistence/models.py while this definition lives here -- structurally the same as the ~20
# schema-pinned enums it excludes automatically, just split across modules.
# The controlled risk-class vocabulary a waiver must declare. Structurally enforced by the
# adjudications risk CHECK (migration 0017) and validated for a clean error in the service layer.
WAIVER_RISK_CLASSES: tuple[str, ...] = ("low", "medium", "high", "critical")


def _edges(
    source: WorkUnitState, *targets: WorkUnitState
) -> set[tuple[WorkUnitState, WorkUnitState]]:
    return {(source, target) for target in targets}


LEGAL_EDGES = frozenset(
    _edges(WorkUnitState.DRAFT, WorkUnitState.READY)
    | _edges(WorkUnitState.READY, WorkUnitState.CLAIMED)
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
