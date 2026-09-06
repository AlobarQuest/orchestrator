"""The change window, and the one predicate that reads it.

**THE WINDOW IS ASKED OF PRODUCTION, NEVER PARSED FROM THE FILE.** Two reasons, and the second is
the one that is easy to talk yourself out of. The lane then obeys the DEPLOYED policy, which is
the one the factory obeys -- a file read would obey whatever is on disk, which is a different
thing the moment they differ. And the policy artifact itself is held to a single consumer by a
guard in the orchestrator's own suite; a second parser here is the second copy that guard exists to
prevent, even though this tree sits outside the one it scans.

**DO NOT NAME THAT ARTIFACT'S FILENAME ANYWHERE UNDER `src/`, INCLUDING IN PROSE.** The guard is
keyed on the filename appearing in any `.py` and cannot tell a module that LOADS the artifact from
one that merely points at it, so a docstring explaining why this lane does not read it is enough to
red the build. An earlier draft of this paragraph did exactly that. Reword; never allowlist -- the
guard protects the one-reader property itself, and an exemption would be the property being
weakened rather than an entry being justified.

**A WINDOW THAT CANNOT BE READ IS A REFUSAL, NEVER A DEFAULT.** There are no fallback hours. A
default would be this program deciding, from a file it could not read, that now is a fine time to
replace the tool that filters every command on the machine.

**THE BOUNDARY SEMANTICS ARE THE ORCHESTRATOR'S, COPIED DELIBERATELY** from
`orchestrator/factory_policy.py`: start inclusive, end exclusive, and a start after an end wraps
midnight, which is the shape a nightly window naturally has. Copied rather than imported because
this lane imports nothing from `orchestrator.*` -- that is the isolation the invariant scan pins,
and a shared boundary rule is not worth breaking it for. If the orchestrator's predicate ever
moves, this one is wrong and nothing will say so; the mitigation is that both are four lines and
both are tested against the same pair of times.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The reach member whose window governs work landing on the operator's machine. Named here rather
# than passed in: this lane has exactly one reach and inventing a parameter for it would suggest
# the caller had a choice.
OPERATOR_MACHINE = "operator_machine"


class WindowUnreadable(RuntimeError):
    """The deployed policy could not be read, or does not say. Refuse; never assume hours."""


@dataclass(frozen=True)
class ChangeWindow:
    timezone: str
    start: time
    end: time

    def permits(self, moment: datetime) -> bool:
        """Whether `moment` -- an aware instant -- falls inside the window, in the window's zone.

        The caller passes an instant rather than a local time, so the conversion happens HERE and
        cannot be forgotten at a call site. An aware instant is required: a naive one would be
        interpreted in whatever zone the machine happens to hold, which is the assumption the
        window's own rationale exists to remove.
        """
        if moment.tzinfo is None:
            raise WindowUnreadable("the moment to judge must carry a timezone")
        local = moment.astimezone(ZoneInfo(self.timezone)).time()
        if self.start < self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end


def _parse_clock(value: object, field: str) -> time:
    if not isinstance(value, str):
        raise WindowUnreadable(f"the change window's {field} is not a string")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise WindowUnreadable(f"the change window's {field} is not a time") from error
    return parsed


def window_from_policy(policy: object) -> ChangeWindow:
    """Pull the `operator_machine` row's window out of a served factory policy.

    `reach` is a LIST of rows, each carrying its `member`. Every shape that is not the one
    expected raises rather than falling through to a default -- including a row present with no
    window, which is a real state (two of the four members declare none) and means this lane has
    no hours to act in rather than any hours it likes.
    """
    if not isinstance(policy, dict):
        raise WindowUnreadable("the policy is not an object")
    rows = policy.get("reach")
    if not isinstance(rows, list):
        raise WindowUnreadable("the policy carries no reach rows")
    for row in rows:
        if not isinstance(row, dict) or row.get("member") != OPERATOR_MACHINE:
            continue
        window = row.get("change_window")
        if not isinstance(window, dict):
            raise WindowUnreadable(f"the {OPERATOR_MACHINE} row declares no change window")
        zone = window.get("timezone")
        if not isinstance(zone, str) or not zone:
            raise WindowUnreadable("the change window names no timezone")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            # A zone this machine cannot resolve would raise later, inside `permits`, where it
            # would read as a bad clock rather than as an unreadable policy.
            raise WindowUnreadable(f"the change window's timezone is unknown: {zone}") from error
        return ChangeWindow(
            timezone=zone,
            start=_parse_clock(window.get("start"), "start"),
            end=_parse_clock(window.get("end"), "end"),
        )
    raise WindowUnreadable(f"the policy has no {OPERATOR_MACHINE} row")
