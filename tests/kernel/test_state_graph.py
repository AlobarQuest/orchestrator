import itertools

import pytest

from orchestrator.errors import DomainError
from orchestrator.kernel.states import LEGAL_EDGES, ActorRole, WorkUnitState
from orchestrator.kernel.transitions import TransitionGuards, authorize_transition
from tests.kernel.canonical_expectations import (
    EXPECTED_ACTOR_ROLE_VALUES,
    EXPECTED_EDGE_ROLES,
    EXPECTED_LEGAL_EDGES,
    EXPECTED_WORK_UNIT_STATE_VALUES,
)


def test_work_unit_state_names_and_wire_values_match_canonical_contract() -> None:
    assert {state.name: state.value for state in WorkUnitState} == EXPECTED_WORK_UNIT_STATE_VALUES


def test_actor_role_names_and_wire_values_match_canonical_contract() -> None:
    assert {role.name: role.value for role in ActorRole} == EXPECTED_ACTOR_ROLE_VALUES


def test_declared_graph_matches_canonical_edges() -> None:
    assert LEGAL_EDGES == EXPECTED_LEGAL_EDGES


@pytest.mark.parametrize(("source", "target"), sorted(EXPECTED_LEGAL_EDGES))
def test_every_declared_edge_is_legal(source: WorkUnitState, target: WorkUnitState) -> None:
    authorize_transition(
        source,
        target,
        next(iter(EXPECTED_EDGE_ROLES[(source, target)])),
        TransitionGuards(
            approval_recorded=True, completion_satisfied=True, submission_binding_recorded=True
        ),
    )


INVALID_EDGES = set(itertools.product(WorkUnitState, repeat=2)) - EXPECTED_LEGAL_EDGES


def test_graph_partition_covers_all_169_ordered_state_pairs() -> None:
    all_ordered_pairs = set(itertools.product(WorkUnitState, repeat=2))

    assert len(all_ordered_pairs) == 169
    assert len(EXPECTED_LEGAL_EDGES) == 29
    assert len(INVALID_EDGES) == 140
    assert EXPECTED_LEGAL_EDGES | INVALID_EDGES == all_ordered_pairs


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
