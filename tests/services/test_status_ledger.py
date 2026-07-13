import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.config import ProductionDrillMode, get_settings
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Dependency, Event
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.evidence import append_evidence, record_adjudication
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import DependencySpec
from orchestrator.services.status_ledger import StatusLedgerFilters, status_ledger
from tests.services.test_context_preflight import register_context_unit, valid_context
from tests.services.test_dependencies import register_unit
from tests.services.test_production_drill_resources import (
    mark_work_unit_as_production_drill_resource,
)
from tests.services.test_reclaim import authorize_readiness, expire

WORKER = ActorContext("worker-1", ActorRole.WORKER)
SECOND_WORKER = ActorContext("worker-2", ActorRole.WORKER)
SYSTEM = ActorContext("system", ActorRole.SYSTEM)
VERIFIER = ActorContext("verifier-1", ActorRole.VERIFIER)


@pytest.mark.parametrize(
    "mode",
    (ProductionDrillMode.STANDBY, ProductionDrillMode.ENABLED),
)
def test_status_ledger_hides_drill_work_from_default_and_direct_queries(
    migrated_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_PRODUCTION_DRILL_MODE", mode.value)
    get_settings.cache_clear()
    ordinary = register_unit(migrated_session, f"ordinary-status-{mode.value}")
    drill = register_unit(migrated_session, f"drill-status-{mode.value}")
    mark_work_unit_as_production_drill_resource(migrated_session, drill)

    default_rows = status_ledger(
        migrated_session,
        StatusLedgerFilters(include_inactive=True),
    )
    direct_rows = status_ledger(
        migrated_session,
        StatusLedgerFilters(work_unit_id=drill.id, include_inactive=True),
    )

    assert ordinary.id in {row.unit_id for row in default_rows}
    assert drill.id not in {row.unit_id for row in default_rows}
    assert direct_rows == ()


def _claim_and_start(session: Session, unit) -> LeaseGrant:
    grant = claim_unit(session, unit.id, WORKER, "claim-main", standing_context=valid_context())
    assert isinstance(grant, LeaseGrant)
    result = transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="start-main",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            standing_context=valid_context(),
            context_snapshot_id=grant.context_snapshot_id,
        ),
    )
    assert result.state is WorkUnitState.EXECUTING
    return grant


def test_status_ledger_projects_runtime_state_without_writes(
    migrated_session: Session,
) -> None:
    unit = register_context_unit(migrated_session, valid_context(), "ledger-runtime")
    grant = _claim_and_start(migrated_session, unit)
    evidence = append_evidence(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=grant.attempt,
        actor=WORKER,
        lease_token=grant.lease_token,
        evidence_type="pytest",
        stable_ref="artifact://ledger-runtime",
        payload={"exit_code": 0},
        source_revision="abc123",
        idempotency_key="evidence-main",
    )
    assert not isinstance(evidence, Exception)
    adjudication = record_adjudication(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome="failed",
        actor=VERIFIER,
        rationale="needs follow-up",
        idempotency_key="adjudication-main",
        evidence_id=evidence.id,
    )
    assert not isinstance(adjudication, Exception)
    failure = transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.FAILED,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="fail-main",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            reason="tests failed",
        ),
    )

    awaiting = register_context_unit(migrated_session, valid_context(), "ledger-approval")
    awaiting_grant = claim_unit(
        migrated_session,
        awaiting.id,
        WORKER,
        "claim-awaiting",
        standing_context=valid_context(),
    )
    assert isinstance(awaiting_grant, LeaseGrant)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=awaiting.id,
            target=WorkUnitState.AWAITING_APPROVAL,
            actor=WORKER,
            expected_version=awaiting.version,
            idempotency_key="approval-request",
            attempt=awaiting_grant.attempt,
            lease_token=awaiting_grant.lease_token,
        ),
    )

    dependency_target = register_unit(migrated_session, "ledger-dependency-source")
    blocked = register_unit(
        migrated_session,
        "ledger-blocked",
        dependencies=(DependencySpec.work_unit(dependency_target.id, "completed"),),
    )
    blocker = migrated_session.scalar(
        select(Dependency).where(Dependency.work_unit_id == blocked.id)
    )
    assert blocker is not None

    before_events = migrated_session.scalar(select(func.count()).select_from(Event))
    rows = status_ledger(migrated_session, StatusLedgerFilters())
    after_events = migrated_session.scalar(select(func.count()).select_from(Event))

    assert after_events == before_events
    by_key = {row.unit_key: row for row in rows}
    runtime = by_key["ledger-runtime"]
    assert runtime.actor_id == "worker-1"
    assert runtime.unit_id == unit.id
    assert runtime.unit_title == "ledger-runtime"
    assert runtime.unit_state == "failed"
    assert runtime.claim_id == grant.claim_id
    assert runtime.claim_attempt == grant.attempt
    assert runtime.claim_lease_expires_at == grant.expires_at
    assert runtime.last_event_at is not None
    assert runtime.last_heartbeat_at is None
    assert runtime.latest_evidence is not None
    assert runtime.latest_evidence.id == evidence.id
    assert runtime.latest_evidence.stable_ref == "artifact://ledger-runtime"
    assert runtime.latest_adjudication is not None
    assert runtime.latest_adjudication.id == adjudication.id
    assert runtime.latest_adjudication.outcome == "failed"
    assert runtime.last_failure is not None
    assert runtime.last_failure.event_id == failure.event_id
    assert runtime.last_failure.reason == "tests failed"
    assert runtime.context_snapshot_id is not None
    assert runtime.context_classification == "accepted"
    assert runtime.context_decision == "accepted"

    approval = by_key["ledger-approval"]
    assert approval.pending_human_approvals == (
        {
            "subject_type": "action",
            "subject_revision_or_fingerprint": str(awaiting.version),
        },
    )

    blocked_row = by_key["ledger-blocked"]
    assert blocked_row.blockers == (
        {
            "dependency_id": str(blocker.id),
            "kind": "work_unit",
            "required_state_or_condition": "completed",
            "depends_on_work_unit_id": str(dependency_target.id),
            "external_ref": None,
            "status": "pending",
        },
    )


def test_status_ledger_uses_latest_claim_without_stale_credentials(
    migrated_session: Session,
) -> None:
    unit = register_context_unit(migrated_session, valid_context(), "ledger-reclaimed")
    authorize_readiness(migrated_session, unit)
    first = claim_unit(
        migrated_session,
        unit.id,
        WORKER,
        "claim-first",
        standing_context=valid_context(),
    )
    assert isinstance(first, LeaseGrant)
    expire(migrated_session, first.claim_id)
    second = claim_unit(
        migrated_session,
        unit.id,
        SECOND_WORKER,
        "claim-second",
        standing_context=valid_context(),
    )
    if not isinstance(second, LeaseGrant):
        from orchestrator.services.claims import reclaim_expired_claim

        second = reclaim_expired_claim(
            migrated_session,
            unit.id,
            SYSTEM,
            SECOND_WORKER,
            "claim-second",
            standing_context=valid_context(),
        )
    assert isinstance(second, LeaseGrant)

    row = status_ledger(
        migrated_session,
        StatusLedgerFilters(actor_id="worker-2"),
    )[0]

    assert row.actor_id == "worker-2"
    assert row.claim_id == second.claim_id
    assert row.claim_attempt == second.attempt
    stale_claim = migrated_session.get(Claim, first.claim_id)
    assert stale_claim is not None
    assert stale_claim.terminal_reason is not None


def test_status_ledger_filters_and_sorts_by_last_event_desc_then_actor(
    migrated_session: Session,
) -> None:
    first = register_context_unit(migrated_session, valid_context(), "ledger-sort-first")
    second = register_context_unit(migrated_session, valid_context(), "ledger-sort-second")
    claim_unit(
        migrated_session,
        first.id,
        WORKER,
        "claim-sort-first",
        standing_context=valid_context(),
    )
    claim_unit(
        migrated_session,
        second.id,
        SECOND_WORKER,
        "claim-sort-second",
        standing_context=valid_context(),
    )

    rows = status_ledger(migrated_session, StatusLedgerFilters(state="claimed"))

    assert [row.actor_id for row in rows] == ["worker-2", "worker-1"]
    assert rows[0].last_event_at is not None
    assert rows[1].last_event_at is not None
    assert rows[0].last_event_at >= rows[1].last_event_at


def test_status_ledger_excludes_completed_and_cancelled_units_unless_requested(
    migrated_session: Session,
) -> None:
    active = register_context_unit(migrated_session, valid_context(), "ledger-active")
    completed = register_context_unit(migrated_session, valid_context(), "ledger-completed")
    completed.state = "completed"
    migrated_session.commit()

    default_rows = status_ledger(migrated_session, StatusLedgerFilters())
    inactive_rows = status_ledger(
        migrated_session,
        StatusLedgerFilters(include_inactive=True),
    )

    assert {row.unit_id for row in default_rows} == {active.id}
    assert {row.unit_id for row in inactive_rows} == {active.id, completed.id}
