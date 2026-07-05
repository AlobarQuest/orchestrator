from orchestrator.kernel.states import ActorRole, WorkUnitState

Edge = tuple[WorkUnitState, WorkUnitState]

SYSTEM = frozenset({ActorRole.SYSTEM})
WORKER = frozenset({ActorRole.WORKER})
SYSTEM_OR_WORKER = frozenset({ActorRole.SYSTEM, ActorRole.WORKER})
VERIFIER = frozenset({ActorRole.VERIFIER})
HUMAN = frozenset({ActorRole.HUMAN})
VERIFIER_OR_HUMAN = frozenset({ActorRole.VERIFIER, ActorRole.HUMAN})

EXPECTED_EDGE_ROLES: dict[Edge, frozenset[ActorRole]] = {
    (WorkUnitState.DRAFT, WorkUnitState.READY): SYSTEM,
    (WorkUnitState.READY, WorkUnitState.CLAIMED): SYSTEM,
    (WorkUnitState.CLAIMED, WorkUnitState.EXECUTING): WORKER,
    (WorkUnitState.CLAIMED, WorkUnitState.BLOCKED): WORKER,
    (WorkUnitState.CLAIMED, WorkUnitState.AWAITING_APPROVAL): WORKER,
    (WorkUnitState.CLAIMED, WorkUnitState.FAILED): SYSTEM_OR_WORKER,
    (WorkUnitState.CLAIMED, WorkUnitState.CANCELLED): HUMAN,
    (WorkUnitState.EXECUTING, WorkUnitState.SUBMITTED): WORKER,
    (WorkUnitState.EXECUTING, WorkUnitState.BLOCKED): WORKER,
    (WorkUnitState.EXECUTING, WorkUnitState.AWAITING_APPROVAL): WORKER,
    (WorkUnitState.EXECUTING, WorkUnitState.FAILED): SYSTEM_OR_WORKER,
    (WorkUnitState.EXECUTING, WorkUnitState.CANCELLED): HUMAN,
    (WorkUnitState.SUBMITTED, WorkUnitState.VERIFYING): VERIFIER,
    (WorkUnitState.SUBMITTED, WorkUnitState.REVISION_REQUIRED): VERIFIER,
    (WorkUnitState.SUBMITTED, WorkUnitState.AWAITING_REVIEW): VERIFIER,
    (WorkUnitState.SUBMITTED, WorkUnitState.FAILED): VERIFIER,
    (WorkUnitState.SUBMITTED, WorkUnitState.COMPLETED): VERIFIER_OR_HUMAN,
    (WorkUnitState.VERIFYING, WorkUnitState.REVISION_REQUIRED): VERIFIER,
    (WorkUnitState.VERIFYING, WorkUnitState.AWAITING_REVIEW): VERIFIER,
    (WorkUnitState.VERIFYING, WorkUnitState.FAILED): VERIFIER,
    (WorkUnitState.VERIFYING, WorkUnitState.COMPLETED): VERIFIER_OR_HUMAN,
    (WorkUnitState.BLOCKED, WorkUnitState.READY): SYSTEM,
    (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.READY): HUMAN,
    (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.CANCELLED): HUMAN,
    (WorkUnitState.AWAITING_REVIEW, WorkUnitState.COMPLETED): HUMAN,
    (WorkUnitState.AWAITING_REVIEW, WorkUnitState.REVISION_REQUIRED): HUMAN,
    (WorkUnitState.REVISION_REQUIRED, WorkUnitState.READY): SYSTEM,
    (WorkUnitState.FAILED, WorkUnitState.READY): SYSTEM,
    (WorkUnitState.FAILED, WorkUnitState.CANCELLED): HUMAN,
}

EXPECTED_LEGAL_EDGES = frozenset(EXPECTED_EDGE_ROLES)
