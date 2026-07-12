"""WS-P2.1 Task 6: on-ingest github_pr conflict detection (AC-001)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Observation, ReconciliationCondition, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import ObservationCommand, record_observation
from orchestrator.services.pr_bindings import record_verification_read_head, upsert_pr_binding
from orchestrator.services.reconciliation_detection import (
    DetectionCounters,
    detect_observation_conditions,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
HEAD = "a" * 40
NEW_HEAD = "b" * 40
OBSERVED_AT = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def pr_facts(**overrides: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "pr_number": 42,
        "head_sha": HEAD,
        "state": "open",
        "merged": False,
    }
    facts.update(overrides)
    return facts


def ingest(
    session: Session,
    subject_id: uuid.UUID,
    *,
    key: str,
    facts: dict[str, Any],
    observed_at: datetime = OBSERVED_AT,
    observation_type: str = "github_pr",
    subject_type: str = "work_unit",
) -> Observation:
    result = record_observation(
        session,
        ObservationCommand(
            actor=SYSTEM,
            source_system="github",
            source_reference=f"{observation_type}:{key}",
            source_url=None,
            trust_classification="delivery_system",
            subject_type=subject_type,
            subject_reference=str(subject_id),
            environment=None,
            observation_type=observation_type,
            status="observed",
            severity="info",
            observed_at=observed_at,
            summary="observed",
            facts=facts,
            payload_digest=None,
            idempotency_key=key,
        ),
    )
    assert isinstance(result, Observation)
    return result


def conditions(session: Session) -> list[ReconciliationCondition]:
    return list(session.scalars(select(ReconciliationCondition)))


def bound_unit(session: Session, key: str, *, pr_number: int = 42) -> WorkUnit:
    unit = register_unit(session, key)
    upsert_pr_binding(
        session, actor=SYSTEM, work_unit_id=unit.id, pr_number=pr_number, head_sha=HEAD
    )
    session.commit()
    return unit


# --- fail-open: a bad correlation must never reject a valid observation -------------------


def test_a_forged_work_unit_reference_is_skipped_and_counted(migrated_session: Session) -> None:
    """A forged correlation must never RAISE.

    Detection runs on the ingest path. If it could raise, a bad correlation field would turn a
    valid observation into a rejected ingest -- a denial of service on the observation path.
    """
    unit = bound_unit(migrated_session, "pr-forged")
    observation = ingest(migrated_session, unit.id, key="pr-forged-1", facts=pr_facts(merged=True))
    observation.subject_reference = str(uuid.uuid4())  # a work unit that does not exist

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters == DetectionCounters(skipped_correlations=1)
    assert conditions(migrated_session) == []


def test_a_pr_number_that_disagrees_with_the_binding_is_skipped(
    migrated_session: Session,
) -> None:
    unit = bound_unit(migrated_session, "pr-mismatch", pr_number=42)
    observation = ingest(
        migrated_session, unit.id, key="pr-mismatch-1", facts=pr_facts(pr_number=99, merged=True)
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.skipped_correlations == 1
    assert conditions(migrated_session) == []


def test_a_unit_with_no_pr_binding_is_skipped(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-unbound")
    migrated_session.commit()
    observation = ingest(migrated_session, unit.id, key="pr-unbound-1", facts=pr_facts(merged=True))

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.skipped_correlations == 1
    assert conditions(migrated_session) == []


def test_malformed_facts_never_raise(migrated_session: Session) -> None:
    unit = bound_unit(migrated_session, "pr-malformed")
    observation = ingest(
        migrated_session,
        unit.id,
        key="pr-malformed-1",
        facts={"pr_number": "forty-two", "head_sha": 7, "state": ["open"], "merged": "yes"},
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.skipped_correlations == 1
    assert conditions(migrated_session) == []


# --- the three detection rules -------------------------------------------------------------


def test_a_merged_pr_on_an_incomplete_unit_records_the_external_merge_alarm(
    migrated_session: Session,
) -> None:
    """The never-auto-merge alarm. Nothing merges, nothing completes -- a condition is recorded."""
    unit = bound_unit(migrated_session, "pr-merged")
    state_before, version_before = unit.state, unit.version
    observation = ingest(
        migrated_session, unit.id, key="pr-merged-1", facts=pr_facts(state="closed", merged=True)
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.conditions_recorded == 1
    recorded = conditions(migrated_session)
    assert [row.condition_type for row in recorded] == ["external_merge_alarm"]
    assert recorded[0].work_unit_id == unit.id
    assert recorded[0].observation_id == observation.id
    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None
    assert (refreshed.state, refreshed.version) == (state_before, version_before)


def test_a_closed_pr_records_a_state_divergence(migrated_session: Session) -> None:
    unit = bound_unit(migrated_session, "pr-closed")
    observation = ingest(
        migrated_session, unit.id, key="pr-closed-1", facts=pr_facts(state="closed", merged=False)
    )

    detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert [row.condition_type for row in conditions(migrated_session)] == ["pr_state_divergence"]


def test_a_head_change_BEFORE_verification_read_it_does_not_alarm(
    migrated_session: Session,
) -> None:
    """A worker rebase or force-push during revision is NORMAL. Alarming here would fire on
    every legitimate iteration."""
    unit = bound_unit(migrated_session, "pr-rebase")
    observation = ingest(
        migrated_session, unit.id, key="pr-rebase-1", facts=pr_facts(head_sha=NEW_HEAD)
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_a_head_change_AFTER_verification_read_it_records_a_divergence(
    migrated_session: Session,
) -> None:
    """Once verification has read a head, a head that moves underneath it is a real divergence --
    this is AC-001's 'on the exact pull-request head'."""
    unit = register_unit(migrated_session, "pr-forcepush")
    upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD
    )
    record_verification_read_head(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD, attempt=1
    )
    migrated_session.commit()
    observation = ingest(
        migrated_session, unit.id, key="pr-forcepush-1", facts=pr_facts(head_sha=NEW_HEAD)
    )

    detect_observation_conditions(migrated_session, observation, SYSTEM)

    recorded = conditions(migrated_session)
    assert [row.condition_type for row in recorded] == ["pr_state_divergence"]
    assert recorded[0].observed_state["head_sha"] == NEW_HEAD


def test_a_completed_unit_is_never_un_completed_by_an_external_merge(
    migrated_session: Session,
) -> None:
    """Failure mode #3: detection never auto-un-completes a completed unit."""
    unit = bound_unit(migrated_session, "pr-completed")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()
    state_before, version_before = unit.state, unit.version
    observation = ingest(
        migrated_session,
        unit.id,
        key="pr-completed-1",
        facts=pr_facts(state="closed", merged=True),
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.conditions_recorded == 0
    assert conditions(migrated_session) == []
    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None
    assert (refreshed.state, refreshed.version) == (state_before, version_before)


# --- newest-wins, and a re-detection is a COUNTED duplicate ---------------------------------


def test_the_newest_observation_wins_and_re_detection_is_a_counted_duplicate(
    migrated_session: Session,
) -> None:
    unit = bound_unit(migrated_session, "pr-newest")
    older = ingest(
        migrated_session,
        unit.id,
        key="pr-newest-open",
        facts=pr_facts(state="open"),
        observed_at=OBSERVED_AT,
    )
    newer = ingest(
        migrated_session,
        unit.id,
        key="pr-newest-merged",
        facts=pr_facts(state="closed", merged=True),
        observed_at=OBSERVED_AT + timedelta(minutes=5),
    )

    stale = detect_observation_conditions(migrated_session, older, SYSTEM)
    first = detect_observation_conditions(migrated_session, newer, SYSTEM)
    second = detect_observation_conditions(migrated_session, newer, SYSTEM)

    assert stale == DetectionCounters()  # a late-arriving OLDER fact is not current
    assert first.conditions_recorded == 1
    assert second == DetectionCounters(suppressed_duplicates=1)
    assert len(conditions(migrated_session)) == 1
