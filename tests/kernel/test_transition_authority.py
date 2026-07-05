import pytest

from orchestrator.errors import DomainError
from orchestrator.kernel.states import LEGAL_EDGES, ActorRole, WorkUnitState
from orchestrator.kernel.transitions import EDGE_ROLES, TransitionGuards, authorize_transition
from tests.kernel.canonical_expectations import EXPECTED_EDGE_ROLES, EXPECTED_LEGAL_EDGES


@pytest.mark.parametrize(("edge", "roles"), sorted(EXPECTED_EDGE_ROLES.items()))
def test_each_legal_edge_allows_exactly_its_declared_roles(
    edge: tuple[WorkUnitState, WorkUnitState], roles: frozenset[ActorRole]
) -> None:
    guards = TransitionGuards(approval_recorded=True, completion_satisfied=True)

    for role in ActorRole:
        if role in roles:
            authorize_transition(*edge, role, guards)
        else:
            with pytest.raises(DomainError) as exc:
                authorize_transition(*edge, role, guards)
            assert exc.value.code == "role_forbidden"


def test_role_map_covers_every_legal_edge() -> None:
    assert LEGAL_EDGES == EXPECTED_LEGAL_EDGES
    assert EDGE_ROLES == EXPECTED_EDGE_ROLES


@pytest.mark.parametrize(
    ("source", "target"),
    sorted(
        edge
        for edge in EXPECTED_LEGAL_EDGES
        if edge[1]
        in {
            WorkUnitState.CANCELLED,
            WorkUnitState.COMPLETED,
            WorkUnitState.READY,
            WorkUnitState.REVISION_REQUIRED,
            WorkUnitState.AWAITING_REVIEW,
            WorkUnitState.VERIFYING,
        }
        or edge[0] in {WorkUnitState.SUBMITTED, WorkUnitState.VERIFYING}
    ),
)
def test_worker_cannot_control_lifecycle_outcomes(
    source: WorkUnitState, target: WorkUnitState
) -> None:
    with pytest.raises(DomainError) as exc:
        authorize_transition(
            source,
            target,
            ActorRole.WORKER,
            TransitionGuards(approval_recorded=True, completion_satisfied=True),
        )

    assert exc.value.code == "role_forbidden"


def test_approval_must_be_recorded_before_resuming() -> None:
    with pytest.raises(DomainError) as exc:
        authorize_transition(
            WorkUnitState.AWAITING_APPROVAL,
            WorkUnitState.READY,
            ActorRole.HUMAN,
            TransitionGuards(),
        )

    assert exc.value.code == "approval_required"
    assert exc.value.message == "record approval before resuming"
    assert exc.value.recovery == "approve"


@pytest.mark.parametrize(
    ("source", "role"),
    [
        (WorkUnitState.SUBMITTED, ActorRole.VERIFIER),
        (WorkUnitState.SUBMITTED, ActorRole.HUMAN),
        (WorkUnitState.VERIFYING, ActorRole.VERIFIER),
        (WorkUnitState.VERIFYING, ActorRole.HUMAN),
        (WorkUnitState.AWAITING_REVIEW, ActorRole.HUMAN),
    ],
)
def test_completion_requires_all_completion_guards(source: WorkUnitState, role: ActorRole) -> None:
    with pytest.raises(DomainError) as exc:
        authorize_transition(source, WorkUnitState.COMPLETED, role, TransitionGuards())

    assert exc.value.code == "completion_incomplete"
    assert exc.value.message == "completion guards failed"
    assert exc.value.recovery == "verify"


def test_domain_error_exposes_stable_fields() -> None:
    error = DomainError("stable_code", "explanation", "recovery_action")

    assert str(error) == "explanation"
    assert error.code == "stable_code"
    assert error.message == "explanation"
    assert error.recovery == "recovery_action"
