"""Seeing a person's revoke rather than being told of it (ADR-0055 amendment 15, increment 3).

Where a plan has a person revoke the old value, the unit after the revoke (`confirm`) waits,
unclaimed, until a fresh probe of the old value is refused beside a refused known-bad control,
the same probe `confirm_dead` will make. So the revoke needs no click: the executor sees it.

A type whose old value can keep working for a while after the revoke (a BWS session that logged
in before it, amendment 1) declares how long (`Adapter.revoke_settles`), counted from the later of
two instants: when this executor first saw the refusal, and when a person recorded the revoke, for
a decomposition approved with the revoke as a human act. The first sighting is kept in the state
directory; losing it restarts the count, so a loss only lengthens the wait.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from rotation_worker.contract import LIVE_STATUS, HumanAct, Plan, Provider, StepKind, UnitPlan
from rotation_worker.values import Value, scrub

LOG = logging.getLogger("rotation_worker")
STATE_FILE: Final = "revokes-observed.json"
STILL_LIVE: Final = "the old value still answers live: it waits for the revoke"


def person_revokes_before(plan: Plan, unit: UnitPlan) -> bool:
    """Whether the segment straight before this unit is a person retiring the old value."""
    segments = plan.segments
    before = segments[: segments.index(unit)][-1:]
    return any(
        isinstance(segment, HumanAct)
        and any(step.kind is StepKind.RETIRE_OLD for step in segment.steps)
        for segment in before
    )


def recorded_revoke(history: Sequence[Mapping[str, Any]]) -> datetime | None:
    """When a person recorded the revoke, for a decomposition that holds it as a human act.

    The unit's one human act is the revoke; a completed unit it waited on resolves with a
    `reason`, never a `command`.
    """
    found = [
        datetime.fromisoformat(str(event["occurred_at"]))
        for event in history
        if event.get("action") == "dependency.resolved"
        and ((event.get("payload") or {}).get("command") or {}).get("status") == "satisfied"
    ]
    return max(found) if found else None


class ObservedRevokes:
    """The first instant this executor saw each rotation's old value refused."""

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / STATE_FILE

    def first(self, rotation: str, now: datetime) -> datetime:
        """The first sighting of `rotation`'s refusal, recording `now` if there is none yet."""
        seen = self._read()
        if rotation in seen:
            try:
                return datetime.fromisoformat(seen[rotation])
            except TypeError, ValueError:
                pass
        seen[rotation] = now.isoformat()
        self._write(seen)
        return now

    def _read(self) -> dict[str, Any]:
        try:
            found = json.loads(self._path.read_text())
        except OSError, ValueError:
            return {}
        return found if isinstance(found, dict) else {}

    def _write(self, seen: Mapping[str, Any]) -> None:
        staged = self._path.with_suffix(".tmp")
        try:
            staged.write_text(json.dumps(dict(seen), sort_keys=True))
            staged.replace(self._path)
        except OSError as error:
            # Unrecorded, the next pass records its own sighting: the wait only grows.
            LOG.warning("revoke_unrecorded", extra={"reason": scrub(str(error))})


def revoke_wait(
    provider: Provider,
    old: Value | None,
    first_seen: ObservedRevokes,
    rotation: str,
    history: Sequence[Mapping[str, Any]],
    settles: timedelta,
    now: datetime,
) -> str | None:
    """Why the unit after a person's revoke can't be claimed yet, or `None`.

    `old` is the quarantined old value; without one there is nothing to probe, and the contract
    decides what a missing quarantine means.
    """
    if old is None:
        return None
    control = provider.probe(provider.known_bad())
    if control not in provider.dead_statuses:
        return f"the probe didn't refuse a known-bad value ({control})"
    status = provider.probe(old)
    if status == LIVE_STATUS:
        return STILL_LIVE
    if status not in provider.dead_statuses:
        return f"the old value gave no answer to tell live from dead ({status})"
    seen = first_seen.first(rotation, now)
    since = max(seen, recorded_revoke(history) or seen)
    if now < since + settles:
        return f"a session from before the revoke may read until {since + settles}"
    return None
