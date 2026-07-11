"""Conflict detection on pushed observations (AC-001..003).

Detection runs on the ingest path and therefore **never raises**. A malformed or unknown
correlation is skipped and COUNTED. If detection could raise, a forged correlation field would
turn a valid observation into a rejected ingest -- a denial of service on the observation path.
Fail-open, but never silently: the counters make a miss observable.

It also never writes `work_units` and never transitions. Detection surfaces a divergence for an
operator decision; it never auto-un-completes a completed unit and never auto-resolves.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Observation, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import get_pr_binding
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    record_reconciliation_condition,
)

EXTERNAL_MERGE_ALARM = "external_merge_alarm"
PR_STATE_DIVERGENCE = "pr_state_divergence"

SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class DetectionCounters:
    """Fail-open is COUNTED, not silent: a skipped correlation or a suppressed duplicate is a
    miss, and a miss nobody can see is indistinguishable from a system with nothing to report."""

    conditions_recorded: int = 0
    skipped_correlations: int = 0
    suppressed_duplicates: int = 0

    def __add__(self, other: "DetectionCounters") -> "DetectionCounters":
        return DetectionCounters(
            self.conditions_recorded + other.conditions_recorded,
            self.skipped_correlations + other.skipped_correlations,
            self.suppressed_duplicates + other.suppressed_duplicates,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "conditions_recorded": self.conditions_recorded,
            "skipped_correlations": self.skipped_correlations,
            "suppressed_duplicates": self.suppressed_duplicates,
        }


SKIPPED = DetectionCounters(skipped_correlations=1)


def detect_observation_conditions(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    """The post-commit ingest hook. `record_observation` has already committed, so this runs in
    the next transaction -- a rejected ingest never reaches it, and a detection failure cannot
    roll the observation back."""
    try:
        if (
            observation.observation_type == "github_pr"
            and observation.subject_type == "work_unit"
        ):
            return _detect_pull_request(session, observation, actor)
        return DetectionCounters()
    except Exception:
        session.rollback()
        return SKIPPED


def _detect_pull_request(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    unit = _correlated_unit(session, observation)
    if unit is None:
        return SKIPPED
    facts = _pull_request_facts(observation.facts)
    if facts is None:
        return SKIPPED
    binding = get_pr_binding(session, unit.id)
    # Cross-check the PR IDENTITY -- and identity is the pr_number, never the head. Gating on
    # `head_sha == binding.head_sha` would make the head-change rule below unfireable by
    # construction: the only observations that reached it would be the ones whose head already
    # matched. The head is a rule INPUT, not a correlation gate.
    if binding is None or binding.pr_number != facts["pr_number"]:
        return SKIPPED
    if _current_observation(session, observation.subject_reference, "github_pr") != observation:
        # A late-arriving OLDER fact. The current one has already been (or will be) evaluated.
        return DetectionCounters()

    stored = {
        "state": unit.state,
        "pr_number": binding.pr_number,
        "head_sha": binding.head_sha,
        "verification_read_head_sha": binding.verification_read_head_sha,
    }
    completed = WorkUnitState(unit.state) is WorkUnitState.COMPLETED
    counters = DetectionCounters()

    if facts["merged"] and not completed:
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=EXTERNAL_MERGE_ALARM,
            key_facts={"pr_number": facts["pr_number"], "head_sha": facts["head_sha"]},
            stored_state=stored,
            observed_state=facts,
            detail="pull request was merged outside the session before the unit completed",
        )
    if completed:
        # Never un-complete, and do not re-alarm on a unit that legitimately finished.
        return counters

    read_head = binding.verification_read_head_sha
    if facts["state"] == "closed" and not facts["merged"]:
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=PR_STATE_DIVERGENCE,
            key_facts={"pr_number": facts["pr_number"], "head_sha": facts["head_sha"]},
            stored_state=stored,
            observed_state=facts,
            detail="pull request was closed outside the session",
        )
    elif read_head is not None and facts["head_sha"] != read_head:
        # The alarm arms only once verification has READ a head. Before that, a rebase or
        # force-push is normal iteration and must not fire.
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=PR_STATE_DIVERGENCE,
            key_facts={"pr_number": facts["pr_number"], "head_sha": facts["head_sha"]},
            stored_state=stored,
            observed_state=facts,
            detail="pull request head changed after verification read it",
        )
    return counters


def _record(
    session: Session,
    actor: ActorContext,
    unit: WorkUnit,
    observation: Observation,
    *,
    condition_type: str,
    key_facts: dict[str, Any],
    stored_state: dict[str, Any],
    observed_state: dict[str, Any],
    detail: str,
) -> DetectionCounters:
    outcome = record_reconciliation_condition(
        session,
        ConditionCommand(
            actor=actor,
            work_unit_id=unit.id,
            observation_kind=observation.observation_type,
            condition_type=condition_type,
            key_facts=key_facts,
            stored_state=stored_state,
            observed_state=observed_state,
            detail=detail,
            observation_id=observation.id,
        ),
    )
    if isinstance(outcome, DomainError):
        return SKIPPED
    if not isinstance(outcome, ConditionOutcome):  # pragma: no cover - defensive
        return SKIPPED
    if outcome.suppressed:
        return DetectionCounters(suppressed_duplicates=1)
    return DetectionCounters(conditions_recorded=1)


def _correlated_unit(session: Session, observation: Observation) -> WorkUnit | None:
    try:
        unit_id = uuid.UUID(observation.subject_reference)
    except ValueError:
        return None
    return session.get(WorkUnit, unit_id)


def _current_observation(
    session: Session,
    subject_reference: str,
    observation_type: str,
    partition: tuple[str, object] | None = None,
) -> Observation | None:
    """The current observation is the newest by `(observed_at, received_at, id)` -- never
    `observed_at` alone. Two upstream facts can carry the same upstream timestamp, and upstream
    clocks skew."""
    stmt = select(Observation).where(
        Observation.subject_type == "work_unit",
        Observation.subject_reference == subject_reference,
        Observation.observation_type == observation_type,
    )
    if partition is not None:
        name, value = partition
        stmt = stmt.where(Observation.facts[name].astext == str(value))
    stmt = stmt.order_by(
        Observation.observed_at.desc(),
        Observation.received_at.desc(),
        Observation.id.desc(),
    ).limit(1)
    return session.scalar(stmt)


def _pull_request_facts(facts: dict[str, Any]) -> dict[str, Any] | None:
    number = facts.get("pr_number")
    head_sha = facts.get("head_sha")
    state = facts.get("state")
    merged = facts.get("merged")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not isinstance(head_sha, str)
        or SHA.fullmatch(head_sha) is None
        or not isinstance(state, str)
        or not isinstance(merged, bool)
    ):
        return None
    return {"pr_number": number, "head_sha": head_sha, "state": state, "merged": merged}
