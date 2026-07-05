import itertools

import pytest

from orchestrator.errors import DomainError
from orchestrator.kernel.states import LEGAL_EDGES, ActorRole, WorkUnitState
from orchestrator.kernel.transitions import EDGE_ROLES, TransitionGuards, authorize_transition


@pytest.mark.parametrize(("source", "target"), sorted(LEGAL_EDGES))
def test_every_declared_edge_is_legal(source: WorkUnitState, target: WorkUnitState) -> None:
    authorize_transition(
        source,
        target,
        next(iter(EDGE_ROLES[(source, target)])),
        TransitionGuards(approval_recorded=True, completion_satisfied=True),
    )


INVALID_EDGES = set(itertools.permutations(WorkUnitState, 2)) - LEGAL_EDGES


@pytest.mark.parametrize(("source", "target"), sorted(INVALID_EDGES))
def test_every_undeclared_edge_is_invalid(source: WorkUnitState, target: WorkUnitState) -> None:
    with pytest.raises(DomainError) as exc:
        authorize_transition(source, target, ActorRole.HUMAN, TransitionGuards())

    assert exc.value.code == "invalid_transition"


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (WorkUnitState.DRAFT, WorkUnitState.EXECUTING),
        (WorkUnitState.READY, WorkUnitState.COMPLETED),
        (WorkUnitState.EXECUTING, WorkUnitState.COMPLETED),
        (WorkUnitState.BLOCKED, WorkUnitState.COMPLETED),
        (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.EXECUTING),
    ],
)
def test_named_forbidden_transitions_remain_invalid(
    source: WorkUnitState, target: WorkUnitState
) -> None:
    with pytest.raises(DomainError) as exc:
        authorize_transition(source, target, ActorRole.HUMAN, TransitionGuards())

    assert exc.value.code == "invalid_transition"
