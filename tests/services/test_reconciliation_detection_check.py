"""WS-P2.1 Task 7: on-ingest github_check flip detection (AC-002)."""

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.services.pr_bindings import record_verification_read_head, upsert_pr_binding
from orchestrator.services.reconciliation_detection import (
    DetectionCounters,
    detect_observation_conditions,
)
from tests.services.test_dependencies import register_unit
from tests.services.test_reconciliation_detection_pr import (
    HEAD,
    NEW_HEAD,
    OBSERVED_AT,
    SYSTEM,
    conditions,
    ingest,
)


def check_facts(**overrides: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "pr_number": 42,
        "head_sha": HEAD,
        "check_name": "Quality",
        "conclusion": "success",
    }
    facts.update(overrides)
    return facts


def verified_unit(session: Session, key: str, *, read_head: str | None = HEAD):
    """A unit whose verification has read `read_head` (or has not read one at all)."""
    unit = register_unit(session, key)
    upsert_pr_binding(session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    if read_head is not None:
        record_verification_read_head(
            session, actor=SYSTEM, work_unit_id=unit.id, head_sha=read_head
        )
    session.commit()
    return unit


def test_a_check_that_flips_from_success_to_failure_is_recorded(
    migrated_session: Session,
) -> None:
    """AC-002: a check that was green when verification read it, and later goes red, is a
    reconciliation condition -- surfaced for an operator, never auto-acted on."""
    unit = verified_unit(migrated_session, "check-flip")
    state_before, version_before = unit.state, unit.version
    passed = ingest(
        migrated_session,
        unit.id,
        key="check-flip-pass",
        facts=check_facts(conclusion="success"),
        observation_type="github_check",
    )
    detect_observation_conditions(migrated_session, passed, SYSTEM)
    failed = ingest(
        migrated_session,
        unit.id,
        key="check-flip-fail",
        facts=check_facts(conclusion="failure"),
        observed_at=OBSERVED_AT + timedelta(minutes=10),
        observation_type="github_check",
    )

    counters = detect_observation_conditions(migrated_session, failed, SYSTEM)

    assert counters.conditions_recorded == 1
    rows = conditions(migrated_session)
    assert [row.condition_type for row in rows] == ["check_result_flip"]
    assert rows[0].observation_kind == "github_check"
    migrated_session.expire_all()
    refreshed = migrated_session.get(type(unit), unit.id)
    assert refreshed is not None
    assert (refreshed.state, refreshed.version) == (state_before, version_before)


def test_a_check_that_was_never_green_is_not_a_flip(migrated_session: Session) -> None:
    """A check that simply fails was never green under the verified head -- that is a normal
    red build, not reality contradicting stored state."""
    unit = verified_unit(migrated_session, "check-never-green")
    failed = ingest(
        migrated_session,
        unit.id,
        key="check-red-1",
        facts=check_facts(conclusion="failure"),
        observation_type="github_check",
    )

    counters = detect_observation_conditions(migrated_session, failed, SYSTEM)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_a_flip_on_a_head_verification_never_read_is_not_a_flip(
    migrated_session: Session,
) -> None:
    """The flip must be measured against the head verification ACTUALLY read. A green-then-red
    run on some other head says nothing about what verification concluded."""
    unit = verified_unit(migrated_session, "check-other-head", read_head=HEAD)
    ingest(
        migrated_session,
        unit.id,
        key="check-other-pass",
        facts=check_facts(conclusion="success", head_sha=NEW_HEAD),
        observation_type="github_check",
    )
    failed = ingest(
        migrated_session,
        unit.id,
        key="check-other-fail",
        facts=check_facts(conclusion="failure", head_sha=NEW_HEAD),
        observed_at=OBSERVED_AT + timedelta(minutes=10),
        observation_type="github_check",
    )

    counters = detect_observation_conditions(migrated_session, failed, SYSTEM)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_checks_are_partitioned_by_name(migrated_session: Session) -> None:
    """Security going red says nothing about Quality. Each check is its own lineage."""
    unit = verified_unit(migrated_session, "check-partition")
    quality = ingest(
        migrated_session,
        unit.id,
        key="check-quality-pass",
        facts=check_facts(check_name="Quality", conclusion="success"),
        observation_type="github_check",
    )
    detect_observation_conditions(migrated_session, quality, SYSTEM)
    security = ingest(
        migrated_session,
        unit.id,
        key="check-security-fail",
        facts=check_facts(check_name="Security", conclusion="failure"),
        observed_at=OBSERVED_AT + timedelta(minutes=1),
        observation_type="github_check",
    )

    counters = detect_observation_conditions(migrated_session, security, SYSTEM)

    # Security was never green under the verified head, so its failure is not a flip.
    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_a_flip_on_a_completed_unit_is_recorded_but_never_un_completes_it(
    migrated_session: Session,
) -> None:
    """AC-002 explicitly: a check result that flips after verification read it is surfaced --
    and a completed unit is never auto-un-completed."""
    unit = verified_unit(migrated_session, "check-completed")
    passed = ingest(
        migrated_session,
        unit.id,
        key="check-completed-pass",
        facts=check_facts(conclusion="success"),
        observation_type="github_check",
    )
    detect_observation_conditions(migrated_session, passed, SYSTEM)
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()
    state_before, version_before = unit.state, unit.version
    failed = ingest(
        migrated_session,
        unit.id,
        key="check-completed-fail",
        facts=check_facts(conclusion="failure"),
        observed_at=OBSERVED_AT + timedelta(minutes=10),
        observation_type="github_check",
    )

    detect_observation_conditions(migrated_session, failed, SYSTEM)

    assert [row.condition_type for row in conditions(migrated_session)] == ["check_result_flip"]
    migrated_session.expire_all()
    refreshed = migrated_session.get(type(unit), unit.id)
    assert refreshed is not None
    assert (refreshed.state, refreshed.version) == (state_before, version_before)
