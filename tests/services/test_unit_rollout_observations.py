"""The watcher's unit-scoped observation, driven through the REAL ingestion service.

ADR-0022. This file exists because the defect it pins was found against a migrated database and
was invisible to every test that stopped at the request body. `deploy_watcher` builds a payload;
`services/observations.py` decides what happens to it, and the two disagreed about what a rollout
IS. A body-shape assertion cannot see that, which is the `response_model` lesson one layer out.

**The refusal being avoided is unrecoverable.** `record_observation` raises
`observation_conflict` for a second row at the same `(source_system, source_reference)` with
different facts, there is no supersession model and no delete, and the watcher reads that as
`incomplete` — so a single re-run would have wedged the hourly pass at exit 3 permanently while
the successful attempt was never attributed to the unit.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from deploy_watcher.model import Merge, Rollout, Run
from deploy_watcher.units import UnitLanding, unit_observation
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Observation
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import ObservationCommand, record_observation

OBSERVER = ActorContext("orchestrator-observer", ActorRole.OBSERVER)
UNIT = "1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"
MERGE_SHA = "2ba9f7f2ef4121aef69153fc2e6dd248cfdcf33b"
REPOSITORY = "AlobarQuest/change-manager"


def _rollout(*, conclusion: str = "success", attempt: int = 1) -> Rollout:
    return Rollout(
        merge=Merge(
            repository=REPOSITORY,
            number=50,
            merged=True,
            merge_commit_sha=MERGE_SHA,
            base_ref="main",
            merged_at=datetime(2026, 8, 13, 8, 55, tzinfo=UTC),
        ),
        workflow_path=".github/workflows/deploy.yml",
        workflow_revision="a47d4b187c93971a5b5915ce87a963bd4ef35e30",
        attestation="revision_confirmed",
        run=Run(
            run_id=31685940716,
            run_attempt=attempt,
            run_url="https://github.com/AlobarQuest/change-manager/actions/runs/31685940716",
            status="completed",
            conclusion=conclusion,
            started_at=None,
            concluded_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        ),
        settled=True,
        rollout_job="build-and-deploy",
        rollout_job_conclusion=conclusion,
        trigger_step="Trigger Coolify redeploy",
        trigger_step_conclusion=conclusion,
    )


def _record(session: Session, rollout: Rollout, *, verdict: str, reached: str) -> Observation:
    """Record one pass's observation, and REFUSE a `DomainError` here rather than downstream.

    `record_observation` RETURNS its refusals rather than raising them, so a test that reached for
    `.id` on the result would fail with an `AttributeError` naming an attribute instead of naming
    the conflict. The narrowing is what makes the two kills below read as what they are.
    """
    body = unit_observation(
        UnitLanding(UNIT, REPOSITORY, 50, MERGE_SHA),
        rollout,
        verdict=verdict,
        production_reached=reached,
    )
    row = record_observation(
        session,
        ObservationCommand(
            actor=OBSERVER,
            source_system=body["source_system"],
            source_reference=body["source_reference"],
            source_url=body["source_url"],
            trust_classification=body["trust_classification"],
            subject_type=body["subject_type"],
            subject_reference=body["subject_reference"],
            environment=body["environment"],
            observation_type=body["observation_type"],
            status=body["status"],
            severity=body["severity"],
            observed_at=datetime.fromisoformat(body["observed_at"]),
            summary=body["summary"],
            facts=body["facts"],
            payload_digest=body["payload_digest"],
            idempotency_key=body["idempotency_key"],
            expected_version=body["expected_version"],
        ),
    )
    assert not isinstance(row, DomainError), f"the ingress refused: {row.code} — {row}"
    return row


def test_the_service_accepts_what_the_watcher_builds(migrated_session: Session) -> None:
    """Every vocabulary, bound and validator the ingress applies, against the real payload."""
    row = _record(migrated_session, _rollout(), verdict="success", reached="yes")
    assert row.subject_type == "work_unit"
    assert row.subject_reference == UNIT
    assert row.status == "passed"


def test_an_unchanged_hourly_pass_replays(migrated_session: Session) -> None:
    first = _record(migrated_session, _rollout(), verdict="success", reached="yes")
    second = _record(migrated_session, _rollout(), verdict="success", reached="yes")
    assert first.id == second.id


def test_A_RE_RUN_APPENDS_AND_DOES_NOT_CONFLICT(migrated_session: Session) -> None:
    """THE KILL. Measured here rather than reasoned about, because reasoning is what got it wrong.

    Attempt 1 fails, the run is re-run, attempt 2 succeeds — the ordinary sequence in the two
    repositories this can fire on. Under a landing-shaped `source_reference` the second call raises
    `observation_conflict`, the watcher reports `incomplete`, and every pass from then on exits 3.
    """
    failed = _record(
        migrated_session,
        _rollout(conclusion="failure"),
        verdict="failed",
        reached="unknown",
    )
    succeeded = _record(
        migrated_session,
        _rollout(attempt=2),
        verdict="success",
        reached="yes",
    )
    assert failed.id != succeeded.id
    assert failed.status == "failed"
    assert succeeded.status == "passed"


def test_a_TRANSCRIBED_workflow_revision_appends_and_does_not_conflict(
    migrated_session: Session,
) -> None:
    """The second route to the same wedge, needing no re-run at all: somebody classifies a rollout
    revision that was `unknown` when the pass first looked."""
    before = _record(
        migrated_session,
        replace(_rollout(), attestation="unknown"),
        verdict="success",
        reached="yes",
    )
    after = _record(migrated_session, _rollout(), verdict="success", reached="yes")
    assert before.id != after.id
