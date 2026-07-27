"""Pure projection planning: given canonical units + existing bindings, decide per-unit actions.

No I/O. The orchestrator is canonical; this only computes what the tracker should reflect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Frozen copy of the orchestrator lifecycle states at which a tracker item is closed.
# The kernel exposes no named terminal set; COMPLETED and CANCELLED are the true sinks
# (FAILED can return to READY, so it is intentionally NOT terminal here).
# Guarded against drift by tests/tracker_projection_adapter/test_vocabulary_sync.py.
TERMINAL_STATES = frozenset({"completed", "cancelled"})


@dataclass(frozen=True)
class UnitView:
    work_unit_id: str
    unit_key: str
    unit_title: str
    unit_state: str


@dataclass(frozen=True)
class BindingView:
    work_unit_id: str
    tracker_system: str
    external_item_id: str
    external_url: str | None
    projected_state: str


@dataclass(frozen=True)
class Action:
    kind: str  # "create" | "update" | "complete" | "skip"
    unit: UnitView
    binding: BindingView | None


def unit_view(row: dict[str, Any]) -> UnitView:
    return UnitView(
        work_unit_id=str(row["unit_id"]),
        unit_key=row["unit_key"],
        unit_title=row["unit_title"],
        unit_state=row["unit_state"],
    )


def binding_view(row: dict[str, Any]) -> BindingView:
    return BindingView(
        work_unit_id=str(row["work_unit_id"]),
        tracker_system=row["tracker_system"],
        external_item_id=row["external_item_id"],
        external_url=row.get("external_url"),
        projected_state=row["projected_state"],
    )


def plan_actions(units: list[UnitView], bindings: list[BindingView]) -> list[Action]:
    by_unit = {b.work_unit_id: b for b in bindings}
    actions: list[Action] = []
    for unit in units:
        binding = by_unit.get(unit.work_unit_id)
        terminal = unit.unit_state in TERMINAL_STATES
        if binding is None:
            actions.append(Action("skip" if terminal else "create", unit, None))
        elif binding.projected_state == unit.unit_state:
            actions.append(Action("skip", unit, binding))
        elif terminal:
            actions.append(Action("complete", unit, binding))
        else:
            actions.append(Action("update", unit, binding))
    return actions
