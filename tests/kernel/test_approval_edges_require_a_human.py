"""Silence is never approval.

WS-P2.15 makes an unanswered approval gate VISIBLE (the dead-letter view's `stalled_approval`
source). Visibility is safe only because nothing can act on it: an approval gate is answered by
a named human, or it is not answered at all. No timer, no threshold, no age-out, and no
reconciliation pass may move a unit out of `awaiting_approval` or `awaiting_review`.

This assertion is deliberately made against the KERNEL'S OWN EDGE TABLE rather than against any
code WS-P2.15 wrote -- because WS-P2.15 wrote none. The stalled-approval report is a derived
read; it adds no writer, no route, and no transition. So the property "silence cannot approve"
would otherwise rest entirely on pre-existing code that this workstream never touched, and the
acceptance criterion would be satisfied by a test of somebody else's invariant.

Which is the whole problem this workstream exists for. An obligation nobody is forced to
discharge is not a guarantee. So: force it.

The predecessor design proposed WIRING `age_out_human_gates`, which wrote a `blocked`
DispatchRecord and (correctly) never transitioned anything. It was deleted instead -- not
because it was dangerous, but because a report has no business being a write. This test is what
survives of its intent, and it is stronger: it constrains every future edge, not one function.
"""

import pytest

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.kernel.transitions import EDGE_ROLES

APPROVAL_STATES = (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.AWAITING_REVIEW)


def _edges_out_of(state: WorkUnitState) -> dict[tuple[WorkUnitState, WorkUnitState], frozenset]:
    return {edge: roles for edge, roles in EDGE_ROLES.items() if edge[0] is state}


@pytest.mark.parametrize("state", APPROVAL_STATES, ids=lambda s: s.value)
def test_every_edge_out_of_an_approval_state_requires_a_human(state: WorkUnitState) -> None:
    edges = _edges_out_of(state)
    assert edges, f"{state} has no outgoing edges at all -- a unit could never leave it"

    non_human = {
        f"{source} -> {target}": sorted(role.value for role in roles)
        for (source, target), roles in edges.items()
        if ActorRole.HUMAN not in roles
    }
    assert not non_human, (
        f"these edges out of {state.value} do not require a human: {non_human}. "
        "An approval gate is answered by a named human or it is not answered. If a machine "
        "actor can leave this state, then a long enough silence becomes a decision -- which is "
        "exactly the failure the dead-letter view's stalled_approval report exists to make "
        "visible, and must never be allowed to resolve on its own."
    )


@pytest.mark.parametrize("state", APPROVAL_STATES, ids=lambda s: s.value)
def test_no_machine_actor_can_leave_an_approval_state(state: WorkUnitState) -> None:
    """The same invariant from the other side: name the roles that must NOT appear alone.

    A human-plus-machine edge would satisfy the test above while still letting the machine act.
    """
    machine_roles = {ActorRole.SYSTEM, ActorRole.WORKER, ActorRole.VERIFIER}
    offenders = {
        f"{source} -> {target}": sorted(role.value for role in roles & machine_roles)
        for (source, target), roles in _edges_out_of(state).items()
        if roles & machine_roles
    }
    assert not offenders, (
        f"machine actors can leave {state.value}: {offenders}. Silence must never become "
        "approval, and a machine acting on an unanswered gate is silence becoming approval."
    )
