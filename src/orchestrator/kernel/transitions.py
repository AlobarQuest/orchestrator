from dataclasses import dataclass

from orchestrator.errors import DomainError
from orchestrator.kernel.states import LEGAL_EDGES, ActorRole, WorkUnitState

Edge = tuple[WorkUnitState, WorkUnitState]


@dataclass(frozen=True)
class TransitionGuards:
    approval_recorded: bool = False
    completion_satisfied: bool = False
    # Guards EXECUTING -> SUBMITTED only. Defaults False, so the bare `TransitionGuards()` call
    # sites (claim release / evidence failure, both targeting FAILED) stay fail-closed-safe. The
    # NEXT caller that targets SUBMITTED with a bare `TransitionGuards()` is a silent deadlock --
    # compute this in the service, never construct it bare on the submit path.
    submission_binding_recorded: bool = False


SYSTEM_EDGES = {
    (WorkUnitState.DRAFT, WorkUnitState.READY),
    (WorkUnitState.READY, WorkUnitState.CLAIMED),
    (WorkUnitState.READY, WorkUnitState.FAILED),
    (WorkUnitState.CLAIMED, WorkUnitState.FAILED),
    (WorkUnitState.EXECUTING, WorkUnitState.FAILED),
    (WorkUnitState.BLOCKED, WorkUnitState.READY),
    (WorkUnitState.REVISION_REQUIRED, WorkUnitState.READY),
    (WorkUnitState.FAILED, WorkUnitState.READY),
}
WORKER_EDGES = {
    (WorkUnitState.CLAIMED, WorkUnitState.EXECUTING),
    (WorkUnitState.CLAIMED, WorkUnitState.BLOCKED),
    (WorkUnitState.CLAIMED, WorkUnitState.AWAITING_APPROVAL),
    (WorkUnitState.CLAIMED, WorkUnitState.FAILED),
    (WorkUnitState.EXECUTING, WorkUnitState.SUBMITTED),
    (WorkUnitState.EXECUTING, WorkUnitState.BLOCKED),
    (WorkUnitState.EXECUTING, WorkUnitState.AWAITING_APPROVAL),
    (WorkUnitState.EXECUTING, WorkUnitState.FAILED),
}
VERIFIER_EDGES = {
    (source, target)
    for source in (WorkUnitState.SUBMITTED, WorkUnitState.VERIFYING)
    for target in (
        WorkUnitState.VERIFYING,
        WorkUnitState.REVISION_REQUIRED,
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.FAILED,
        WorkUnitState.COMPLETED,
    )
    if (source, target) in LEGAL_EDGES
}
HUMAN_EDGES = {
    (WorkUnitState.CLAIMED, WorkUnitState.CANCELLED),
    (WorkUnitState.EXECUTING, WorkUnitState.CANCELLED),
    (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.READY),
    (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.CANCELLED),
    (WorkUnitState.AWAITING_REVIEW, WorkUnitState.COMPLETED),
    (WorkUnitState.AWAITING_REVIEW, WorkUnitState.REVISION_REQUIRED),
    (WorkUnitState.FAILED, WorkUnitState.CANCELLED),
    (WorkUnitState.SUBMITTED, WorkUnitState.COMPLETED),
    (WorkUnitState.VERIFYING, WorkUnitState.COMPLETED),
}
# The contract's designated human decision points -- NOT operator improvisation. The approval-resume
# and the human-review verdicts are declared parts of the lifecycle; the SLO improvisation counter
# excludes them so it measures overrides, not healthy human-in-the-loop steps (WS-P2.2).
DESIGNED_HUMAN_GATES: frozenset[Edge] = frozenset(
    {
        (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.READY),
        (WorkUnitState.AWAITING_REVIEW, WorkUnitState.COMPLETED),
        (WorkUnitState.AWAITING_REVIEW, WorkUnitState.REVISION_REQUIRED),
    }
)
EDGE_ROLES: dict[Edge, frozenset[ActorRole]] = {
    edge: frozenset(
        role
        for role, edges in (
            (ActorRole.SYSTEM, SYSTEM_EDGES),
            (ActorRole.WORKER, WORKER_EDGES),
            (ActorRole.VERIFIER, VERIFIER_EDGES),
            (ActorRole.HUMAN, HUMAN_EDGES),
        )
        if edge in edges
    )
    for edge in LEGAL_EDGES
}
assert all(EDGE_ROLES.values())


def authorize_transition(
    source: WorkUnitState,
    target: WorkUnitState,
    role: ActorRole,
    guards: TransitionGuards,
) -> None:
    if (source, target) not in LEGAL_EDGES:
        raise DomainError("invalid_transition", f"{source} -> {target} is not legal", None)
    if role not in EDGE_ROLES[(source, target)]:
        raise DomainError("role_forbidden", f"{role} may not perform this transition", None)
    if (
        source is WorkUnitState.AWAITING_APPROVAL
        and target is WorkUnitState.READY
        and not guards.approval_recorded
    ):
        raise DomainError("approval_required", "record approval before resuming", "approve")
    if target is WorkUnitState.COMPLETED and not guards.completion_satisfied:
        raise DomainError("completion_incomplete", "completion guards failed", "verify")
    if (
        source is WorkUnitState.EXECUTING
        and target is WorkUnitState.SUBMITTED
        and not guards.submission_binding_recorded
    ):
        raise DomainError(
            "pr_binding_required",
            "record the pull request binding for this attempt before submitting",
            "post the pull request binding for the current attempt",
        )
