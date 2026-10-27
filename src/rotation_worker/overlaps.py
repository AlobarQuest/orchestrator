"""Which other rotations are mid-overlap: their new value stored, their old one not yet retired.

ADR-0055 amendment 12, "Switch timing". The broad BWS machine token starts the scheduled readers
that other rotations verify by, so switching a machine token while another rotation is between
`store` and `retire_old` can stop the very runs that rotation is waiting on.

A rotation's units are told apart by their package revision, and a rotation has stored once the
unit holding `store` has been claimed, or is no longer listed (the status ledger lists every unit
not completed or cancelled). From then on each of its listed units counts, the one holding `store`
included, since a unit can fail after its `store` step. A failed unit counts until someone requeues
or cancels it, and a unit waiting on a person's revoke counts until it completes. A unit whose
adapter or segment this executor doesn't know always counts. Each errs on the side that waits, and
the log names the unit it waited on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rotation_worker.adapters import ADAPTERS
from rotation_worker.checks import UNIT_KEY
from rotation_worker.contract import StepKind
from rotation_worker.envelope import ADAPTER_KEY, is_rotation_unit
from rotation_worker.envelope import constraints as envelope_constraints
from rotation_worker.orchestrator_client import OrchestratorClient


def mid_overlap(client: OrchestratorClient, revision_id: str) -> list[str]:
    """The key of each listed unit of another rotation that has stored its new value."""
    rotations: dict[str, list[_Listed]] = {}
    for row in client.active_units():
        unit = client.unit(str(row["unit_id"]))
        revision = str(unit.get("work_package_revision_id"))
        if is_rotation_unit(unit) and revision != revision_id:
            listed = _Listed(
                f"{row['unit_key']} ({row['unit_state']})",
                _from_store(envelope_constraints(unit)),
                int(unit["attempt_count"]) > 0,
            )
            rotations.setdefault(revision, []).append(listed)
    found = []
    for units in rotations.values():
        stored = not any(u.from_store == 0 and not u.claimed for u in units)
        found += [u.key for u in units if u.from_store is None or (stored and u.from_store >= 0)]
    return sorted(found)


@dataclass(frozen=True)
class _Listed:
    key: str
    # Segments after the one holding `store` (negative before it); `None` if unknown.
    from_store: int | None
    claimed: bool


def _from_store(constraints: Mapping[str, Any]) -> int | None:
    adapter = ADAPTERS.get(str(constraints.get(ADAPTER_KEY)))
    if adapter is None:
        return None
    # A type's derived plan has the same units, in the same order, as the plan in `Adapter.plan`
    # (`plans.derive` adds or drops steps, never units), so where a unit stands against the store
    # reads the same from either.
    segments = adapter.plan.segments
    names = [segment.name for segment in segments]
    name = str(constraints.get(UNIT_KEY))
    stores = [i for i, s in enumerate(segments) if any(t.kind is StepKind.STORE for t in s.steps)]
    if name not in names or not stores:
        return None
    return names.index(name) - stores[0]
