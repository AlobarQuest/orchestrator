"""WS-P2.1 Task 8: the deploy_split_brain detect-pass (AC-003).

This is the one approved deviation from "conflict detection on observation ingest". It cannot be
an ingest hook under ANY design: the post-deploy verification unit is minted SUBMITTED *inside*
the deployment-ingest transaction, i.e. zero seconds old. "Verification stalled" is a statement
about elapsed time and simply cannot be true at the instant of ingest.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    DeploymentObservation,
    ReconciliationCondition,
    WorkUnit,
)
from orchestrator.services.deployment_observations import record_deployment_observation
from orchestrator.services.reconciliation_detection import (
    DetectionCounters,
    detect_reconciliation_conditions,
)
from tests.services.test_deployment_observations import (
    observation_command,
    release_binding,
)
from tests.services.test_reconciliation_detection_pr import SYSTEM, conditions, ingest


@pytest.fixture
def unreported_binding(migrated_session: Session):
    """A release binding whose deploy was never ingested -- so there is no post-deploy unit."""
    _unit, binding = release_binding(migrated_session, key="split-brain-unreported")
    migrated_session.commit()
    return binding


@pytest.fixture
def deployed_binding(migrated_session: Session):
    """A binding whose deploy WAS ingested, minting a post-deploy unit in SUBMITTED.

    Note the threshold, not the clock, is the injection point: the tests vary `stall_seconds`
    rather than moving time, which is exactly how drill 4 drives it without a sleep.
    """
    _unit, binding = release_binding(migrated_session, key="split-brain-deployed")
    result = record_deployment_observation(
        migrated_session, observation_command(binding, key="split-brain-obs")
    )
    assert not isinstance(result, DomainError)
    migrated_session.commit()
    return binding


def test_a_stalled_post_deploy_verification_records_deploy_split_brain(
    migrated_session: Session, deployed_binding
) -> None:
    """The marquee case: the deployment succeeded, and verification never finished."""
    observation = migrated_session.scalar(select(DeploymentObservation))
    assert observation is not None
    post_deploy = migrated_session.get(WorkUnit, observation.post_deploy_work_unit_id)
    assert post_deploy is not None and post_deploy.state == WorkUnitState.SUBMITTED
    state_before, version_before = post_deploy.state, post_deploy.version

    counters = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)

    assert counters.conditions_recorded == 1
    rows = conditions(migrated_session)
    assert [row.condition_type for row in rows] == ["deploy_split_brain"]
    assert rows[0].work_unit_id == post_deploy.id
    assert rows[0].deployment_observation_id == observation.id
    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, post_deploy.id)
    assert refreshed is not None
    assert (refreshed.state, refreshed.version) == (state_before, version_before)


def test_a_normal_in_progress_verification_under_the_threshold_does_not_fire(
    migrated_session: Session, deployed_binding
) -> None:
    """Explicitly non-conflicting. A verification that is simply still running is not a split
    brain, and firing here would make the alarm worthless."""
    counters = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=3600)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_a_completed_post_deploy_verification_does_not_fire(
    migrated_session: Session, deployed_binding
) -> None:
    observation = migrated_session.scalar(select(DeploymentObservation))
    assert observation is not None
    post_deploy = migrated_session.get(WorkUnit, observation.post_deploy_work_unit_id)
    assert post_deploy is not None
    post_deploy.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    counters = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_the_deploy_nobody_reported_fires_without_any_threshold(
    migrated_session: Session, unreported_binding
) -> None:
    """The case ADR-0002 rejects Alternative A over: drift nobody reported.

    With no ingested deployment observation there is no post-deploy unit at all, so
    elapsed-since-SUBMITTED can never fire. The runner reporting a deploy for a binding that has
    no verification unit IS the signal -- a cheap read, and no threshold.
    """
    binding = unreported_binding
    assert list(migrated_session.scalars(select(DeploymentObservation))) == []
    deploy = ingest(
        migrated_session,
        binding.id,
        key="deploy-unreported-1",
        facts={"deploy_status": "succeeded", "artifact_digest": binding.artifact_digest},
        observation_type="deployment",
        subject_type="release_binding",
    )

    counters = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=3600)

    assert counters.conditions_recorded == 1
    rows = conditions(migrated_session)
    assert [row.condition_type for row in rows] == ["deploy_split_brain"]
    assert rows[0].work_unit_id == binding.work_unit_id
    assert rows[0].observation_id == deploy.id


def test_the_detect_pass_is_idempotent_and_reports_suppressed_duplicates(
    migrated_session: Session, deployed_binding
) -> None:
    first = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)
    second = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)

    assert first == DetectionCounters(conditions_recorded=1)
    assert second == DetectionCounters(suppressed_duplicates=1)
    assert len(conditions(migrated_session)) == 1


def test_the_detect_pass_creates_no_unit_and_transitions_nothing(
    migrated_session: Session, deployed_binding
) -> None:
    before = {
        (unit.id, unit.state, unit.version) for unit in migrated_session.scalars(select(WorkUnit))
    }

    detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)

    migrated_session.expire_all()
    after = {
        (unit.id, unit.state, unit.version) for unit in migrated_session.scalars(select(WorkUnit))
    }
    assert after == before
    assert len(list(migrated_session.scalars(select(ReconciliationCondition)))) == 1
