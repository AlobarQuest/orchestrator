from orchestrator.kernel.states import WorkUnitState
from tracker_projection_adapter.projection import TERMINAL_STATES


def test_terminal_states_are_valid_lifecycle_states():
    valid = {s.value for s in WorkUnitState}
    assert TERMINAL_STATES <= valid


def test_terminal_states_include_the_true_sinks():
    assert WorkUnitState.COMPLETED.value in TERMINAL_STATES
    assert WorkUnitState.CANCELLED.value in TERMINAL_STATES
