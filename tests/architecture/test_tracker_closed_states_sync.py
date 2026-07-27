"""WS-P2.7 Increment 2 Task 2: inbound/outbound closed-state vocabulary coupling guard."""

from orchestrator.services.reconciliation_detection import TRACKER_CLOSED_STATES
from tracker_projection_adapter.projection import TERMINAL_STATES


def test_inbound_closed_set_mirrors_outbound_terminal_states() -> None:
    # If outbound changes which states close a card, inbound must change in lockstep, or it
    # false-fires on a state whose card the projection now legitimately closes.
    assert set(TRACKER_CLOSED_STATES) == set(TERMINAL_STATES)
