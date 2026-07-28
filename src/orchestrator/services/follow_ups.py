"""Package-declared follow-up scheduling (WS-P2.8).

The intent package declares WHETHER an outcome should be revisited; this module owns the
orchestrator's side of that contract. `revisit_when` and `signals` are prose written for a human
and are never parsed -- the timing comes from configuration, not from the text.

The four field names mirror the intent-packages schema exactly. A fifth key is a validation
error rather than an ignored extra, because a silently-dropped key is how a declaration and its
reader drift apart.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from orchestrator.errors import DomainError

# The intent-packages `follow_up` block, mirrored field for field. Every key is mandatory-present;
# `revisit_when` and `owner` may be null. Registered in the cross-boundary vocabulary registry.
FOLLOW_UP_FIELDS = ("required", "revisit_when", "signals", "owner")


def _invalid(detail: str) -> DomainError:
    return DomainError(
        "follow_up_invalid",
        f"package follow_up declaration is invalid: {detail}",
        "correct the package follow_up block and re-emit the intake payload",
    )


def validate_follow_up(value: object) -> dict[str, Any] | None:
    """Return the normalized declaration, or None when the package carried none."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("it must be a mapping")
    unknown = sorted(set(value) - set(FOLLOW_UP_FIELDS))
    if unknown:
        raise _invalid(f"unknown key {unknown[0]!r}")
    missing = [field for field in FOLLOW_UP_FIELDS if field not in value]
    if missing:
        raise _invalid(f"missing required key {missing[0]!r}")
    if not isinstance(value["required"], bool):
        raise _invalid("`required` must be a boolean")
    for field in ("revisit_when", "owner"):
        if value[field] is not None and not isinstance(value[field], str):
            raise _invalid(f"`{field}` must be a string or null")
    signals = value["signals"]
    if not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
        raise _invalid("`signals` must be a list of strings")
    return {
        "required": value["required"],
        "revisit_when": value["revisit_when"],
        "signals": list(signals),
        "owner": value["owner"],
    }


# The capability a follow-up review unit requires. Registered in ORCHESTRATOR_ONLY_CAPABILITIES,
# never in the runner vocabulary: no runner works one of these, and the byte-pinned cross-repo
# envelope fixture stays untouched.
FOLLOW_UP_CAPABILITY = "follow_up_review"

# Why a revision was passed over. Individual constants rather than a collection: a module-level
# tuple of strings used in a membership test becomes a discovered subject of the cross-boundary
# vocabulary detector, and these are internal policy, not a contract with another repo.
SKIP_NOT_REQUIRED = "not_required"
SKIP_NO_COMPLETED_UNIT = "no_completed_unit"
SKIP_UNITS_IN_FLIGHT = "units_in_flight"
SKIP_UNSETTLED_FAILED_UNIT = "unsettled_failed_unit"
SKIP_NOT_YET_DUE = "not_yet_due"
SKIP_ALREADY_MINTED = "already_minted"
SKIP_DECLARATION_MALFORMED = "declaration_malformed"

_COMPLETED = "completed"
_CANCELLED = "cancelled"
_FAILED = "failed"


@dataclass(frozen=True)
class UnitFacts:
    """One work unit, reduced to what due-ness depends on.

    `settled_at` is when the unit ENTERED its settled state, read from the event ledger -- never
    from `work_units.updated_at`, which a trigger rewrites on every write and which therefore
    cannot be back-dated or trusted as a state-entry time.
    """

    required_capability: str
    state: str
    settled_at: datetime | None


@dataclass(frozen=True)
class RevisionFacts:
    revision_id: uuid.UUID
    follow_up: dict[str, object] | None
    units: tuple[UnitFacts, ...]
    has_follow_up_unit: bool


@dataclass(frozen=True)
class DueDecision:
    revision_id: uuid.UUID
    due_at: datetime | None
    skip_reason: str | None


def evaluate_due(facts: RevisionFacts, *, now: datetime, due_after_days: int) -> DueDecision:
    """Decide whether a revision's declared follow-up review is due. Pure: no I/O, no clock.

    A revision qualifies when its declaration asks for one, its work actually shipped (at least
    one unit completed) and has stopped moving, the window has elapsed since the last unit
    settled, and no review unit exists yet.

    FAILED deliberately blocks. It is not a terminal state -- a failed unit can return to READY
    or be retired -- so the package's outcome is not yet knowable and there is nothing to
    schedule a revisit of. It gets its OWN skip reason rather than being folded into
    `units_in_flight`, because "still working" and "stopped, and nobody decided" call for
    different operator actions.
    """
    if facts.has_follow_up_unit:
        return DueDecision(facts.revision_id, None, SKIP_ALREADY_MINTED)
    declaration = facts.follow_up
    if not isinstance(declaration, dict) or declaration.get("required") is not True:
        return DueDecision(facts.revision_id, None, SKIP_NOT_REQUIRED)

    # The review unit is itself a unit of this revision; counting it would make the revision look
    # eligible again the moment a human completes it.
    subjects = tuple(
        unit for unit in facts.units if unit.required_capability != FOLLOW_UP_CAPABILITY
    )
    if any(unit.state == _FAILED for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_UNSETTLED_FAILED_UNIT)
    if any(unit.state not in (_COMPLETED, _CANCELLED) for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_UNITS_IN_FLIGHT)
    if not any(unit.state == _COMPLETED for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_NO_COMPLETED_UNIT)

    settled = [unit.settled_at for unit in subjects if unit.settled_at is not None]
    if not settled:
        return DueDecision(facts.revision_id, None, SKIP_UNITS_IN_FLIGHT)
    due_at = max(settled) + timedelta(days=due_after_days)
    if now < due_at:
        return DueDecision(facts.revision_id, due_at, SKIP_NOT_YET_DUE)
    return DueDecision(facts.revision_id, due_at, None)
