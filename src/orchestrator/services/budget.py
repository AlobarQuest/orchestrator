"""Per-unit LLM-call budget vocabulary and predicates (WS-P2.4 Increment 2, WS-P2.31).

The predicates here are pure and read-only: sum the unit's actual llm_calls from
attempt.cost_recorded events and compare to the declared max_llm_calls ceiling. Never writes
WorkUnit.authority -- the ceiling is read through normalize_authority, a pure frozen-dataclass
projection. `BREACH_ACTION` names a row this module does not write; it lives here so the
emitter and the reader cannot drift apart over the spelling.
"""

import uuid

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import Event, WorkUnit

_COST_ACTION = "attempt.cost_recorded"

# The event that records an overrun the moment it becomes known. `is_over_budget` decides
# whether a unit may be granted ANOTHER attempt, so an attempt that blows the ceiling and then
# finishes is never asked the question -- see `orchestrator.services.cost_actuals` for the
# emitter and `orchestrator.services.slo_report` for the reader.
BREACH_ACTION = "attempt.budget_breached"


def cumulative_llm_calls(session: Session, unit_id: uuid.UUID) -> int:
    """Sum actual llm_calls across the unit's cost-known attempts. Unknown-cost attempts
    (cost_known=false, llm_calls=null) are excluded and remain bounded by max_attempts."""
    total = session.scalar(
        select(func.coalesce(func.sum(cast(Event.payload["llm_calls"].astext, Integer)), 0)).where(
            Event.action == _COST_ACTION,
            Event.subject_type == "work_unit",
            Event.subject_id == unit_id,
            Event.payload["cost_known"].astext == "true",
        )
    )
    return int(total or 0)


def declared_ceiling(unit: WorkUnit) -> int | None:
    """The unit's declared max_llm_calls, or None when no ceiling is declared."""
    return normalize_authority(unit.authority).budgets.max_llm_calls


def is_over_budget(session: Session, unit: WorkUnit) -> bool:
    """True once cumulative known llm_calls reaches or exceeds the declared ceiling.
    A unit with no declared ceiling is never over budget."""
    ceiling = declared_ceiling(unit)
    if ceiling is None:
        return False
    return cumulative_llm_calls(session, unit.id) >= ceiling
