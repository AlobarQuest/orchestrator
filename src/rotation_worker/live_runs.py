"""Whether restarting the orchestrator now would strand someone's run (ADR-0055 decision 7).

The executor restarts the orchestrator when it rotates one of its bearers. A restart strands a unit
whose runner calls back at the end of its run, so this asks the same two questions as the deploy
script's precheck (`scripts/deploy_orchestrator.py`, #82): is any unit live, and has any ready unit
been dispatched without being claimed yet? It leaves out rotation units, because the executor's own
claim is live while it restarts, and no other rotation unit runs while the executor is busy with
this one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from rotation_worker.envelope import is_rotation_unit
from rotation_worker.orchestrator_client import OrchestratorClient

# A restart in these states strands the unit with its attempt spent.
LIVE_STATES: Final = frozenset({"claimed", "executing", "submitted", "verifying"})
READY_STATE: Final = "ready"
CLAIMED_STATE: Final = "claimed"
DISPATCHED_ACTION: Final = "dispatch.dispatched"


def dispatched_and_unclaimed(history: Sequence[Mapping[str, Any]]) -> bool:
    """A dispatch the runner hasn't claimed yet: its run is starting in the target repository."""
    pending = False
    for event in history:
        if event.get("action") == DISPATCHED_ACTION:
            pending = True
        elif event.get("to_state") == CLAIMED_STATE:
            pending = False
    return pending


def stranded_by_a_restart(client: OrchestratorClient) -> list[str]:
    """The key of every unit a restart now would strand. Empty means a restart is safe."""
    stranded = []
    for row in client.active_units():
        state, unit_id = row["unit_state"], str(row["unit_id"])
        if state in LIVE_STATES and not is_rotation_unit(client.unit(unit_id)):
            stranded.append(f"{row['unit_key']} ({state})")
        elif state == READY_STATE and dispatched_and_unclaimed(client.history(unit_id)):
            stranded.append(f"{row['unit_key']} (dispatched)")
    return sorted(stranded)
