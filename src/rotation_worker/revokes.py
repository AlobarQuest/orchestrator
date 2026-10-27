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
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from rotation_worker.contract import LIVE_STATUS, HumanAct, Plan, Provider, StepKind, UnitPlan
from rotation_worker.values import Value, scrub

LOG = logging.getLogger("rotation_worker")
STATE_FILE: Final = "revokes-observed.json"
STILL_LIVE: Final = "the old value still answers live: it waits for the revoke"
# A sighting older than this belongs to a rotation long finished.
FORGET_AFTER: Final = timedelta(days=30)


class Revoke(StrEnum):
    """What a pass learned about a person's revoke."""

    LIVE = "live"  # the old value still answers live: the revoke is due
    SEEN = "seen"  # the old value is refused
    UNKNOWN = "unknown"  # nothing could tell, this pass


def person_revokes_beside(plan: Plan, unit: UnitPlan, offset: int) -> bool:
    """Whether the segment `offset` away from this unit (1 after, -1 before) is a person retiring
    the old value."""
    segments = plan.segments
    index = segments.index(unit) + offset
    if not 0 <= index < len(segments):
        return False
    segment = segments[index]
    return isinstance(segment, HumanAct) and any(
        step.kind is StepKind.RETIRE_OLD for step in segment.steps
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

    def seen(self, rotation: str) -> datetime | None:
        try:
            return datetime.fromisoformat(self._read()[rotation])
        except KeyError, TypeError, ValueError:
            return None

    def record(self, rotation: str, now: datetime) -> datetime:
        """The first sighting of `rotation`'s refusal, recording `now` if there is none yet."""
        first = self.seen(rotation)
        if first is not None:
            return first
        self._write({**self._read(), rotation: now.isoformat()}, now)
        return now

    def forget(self, rotation: str, now: datetime) -> None:
        """A refusal the old value has since contradicted (it answered live): never count it."""
        seen = self._read()
        if rotation in seen:
            del seen[rotation]
            self._write(seen, now)

    def _read(self) -> dict[str, Any]:
        try:
            found = json.loads(self._path.read_text())
        except OSError, ValueError:
            return {}
        return found if isinstance(found, dict) else {}

    def _write(self, seen: Mapping[str, Any], now: datetime) -> None:
        kept = {
            rotation: at
            for rotation, at in seen.items()
            if isinstance(at, str) and _recent(at, now)
        }
        staged = self._path.with_suffix(".tmp")
        try:
            staged.write_text(json.dumps(kept, sort_keys=True))
            staged.replace(self._path)
        except OSError as error:
            # Unrecorded, the next pass records its own sighting: the wait only grows.
            LOG.warning("revoke_unrecorded", extra={"reason": scrub(str(error))})


def _recent(at: str, now: datetime) -> bool:
    try:
        return now - datetime.fromisoformat(at) < FORGET_AFTER
    except TypeError, ValueError:
        return False


def revoke_wait(
    provider: Provider,
    old: Value | None,
    first_seen: ObservedRevokes,
    rotation: str,
    history: Sequence[Mapping[str, Any]],
    settles: timedelta,
    now: datetime,
) -> tuple[Revoke, str | None]:
    """What this pass knows of the revoke, and why the unit after it can't be claimed yet (`None`
    once it can).

    `old` is the quarantined old value; without one there is nothing to probe, and the contract
    decides what a missing quarantine means. Inside a settling window already begun nothing is
    probed; the probe that ends it is asked again before the claim.
    """
    if old is None:
        return Revoke.SEEN, None
    recorded = recorded_revoke(history)
    first = first_seen.seen(rotation)
    if first is None or now >= max(first, recorded or first) + settles:
        control = provider.probe(provider.known_bad())
        if control not in provider.dead_statuses:
            return Revoke.UNKNOWN, f"the probe didn't refuse a known-bad value ({control})"
        status = provider.probe(old)
        if status == LIVE_STATUS:
            first_seen.forget(rotation, now)
            return Revoke.LIVE, STILL_LIVE
        if status not in provider.dead_statuses:
            return Revoke.UNKNOWN, f"the old value gave no answer to tell live from dead ({status})"
        first = first_seen.record(rotation, now)
    until = max(first, recorded or first) + settles
    return Revoke.SEEN, _settling(until) if now < until else None


def _settling(until: datetime) -> str:
    return f"a session from before the revoke may read until {until}"
