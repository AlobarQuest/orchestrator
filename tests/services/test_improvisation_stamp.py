import uuid

from orchestrator.clock import TransactionClock
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)

AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
HUMAN = ActorContext("human-1", ActorRole.HUMAN)


def _revision(session):
    now = TransactionClock().now(session)
    return register_revision(
        session,
        package_id="pkg-improv",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:improv",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=now,
        approval_event_id=str(uuid.UUID(int=1)),
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def _approved_unit(session, key):
    now = TransactionClock().now(session)
    revision = _revision(session)
    unit = register_approved_unit(
        session,
        unit_id=None,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=now,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    # register_approved_unit creates the unit in DRAFT; DRAFT -> READY is a separate SYSTEM
    # step (see CLAUDE.md invariant). Fixture setup bypasses that transition directly, the
    # same way tests/services/test_context_preflight.py::register_context_unit does.
    unit.state = WorkUnitState.READY
    session.commit()
    return unit


def _latest_transition(session, unit_id, to_state):
    from sqlalchemy import select

    return session.scalar(
        select(Event)
        .where(Event.subject_id == unit_id, Event.to_state == to_state.value)
        .order_by(Event.occurred_at.desc(), Event.id.desc())
    )


def executing_unit_version(session, unit_id):
    return session.get(WorkUnit, unit_id).version


def test_worker_transition_is_not_improvisation(migrated_session):
    unit = _approved_unit(migrated_session, "u-worker")
    grant = claim_unit(migrated_session, unit.id, WORKER, "claim-1")
    assert isinstance(grant, LeaseGrant)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="start-1",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            context_snapshot_id=grant.context_snapshot_id,
        ),
    )
    event = _latest_transition(migrated_session, unit.id, WorkUnitState.EXECUTING)
    assert event is not None
    assert event.improvisation is False


def test_human_cancel_is_improvisation(migrated_session):
    unit = _approved_unit(migrated_session, "u-cancel")
    grant = claim_unit(migrated_session, unit.id, WORKER, "claim-2")
    assert isinstance(grant, LeaseGrant)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="start-2",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            context_snapshot_id=grant.context_snapshot_id,
        ),
    )
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.CANCELLED,
            actor=HUMAN,
            expected_version=executing_unit_version(migrated_session, unit.id),
            idempotency_key="cancel-2",
            reason="operator override",
        ),
    )
    event = _latest_transition(migrated_session, unit.id, WorkUnitState.CANCELLED)
    assert event is not None
    assert event.improvisation is True


def test_approval_resume_is_not_improvisation(migrated_session):
    """Negative control: a HUMAN driving the CONTRACT'S designed approval gate
    (AWAITING_APPROVAL -> READY) must NOT be counted as improvisation -- it is a
    sanctioned human-in-the-loop step, not an operator override (WS-P2.2 spec S7).
    """
    unit = _approved_unit(migrated_session, "u-gate")
    grant = claim_unit(migrated_session, unit.id, WORKER, "claim-3")
    assert isinstance(grant, LeaseGrant)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.AWAITING_APPROVAL,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="request-approval-3",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            context_snapshot_id=grant.context_snapshot_id,
        ),
    )
    current_version = executing_unit_version(migrated_session, unit.id)
    record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="action",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="approved for resume",
        idempotency_key="approval-3",
        expected_version=current_version,
    )
    migrated_session.commit()

    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.READY,
            actor=HUMAN,
            expected_version=current_version,
            idempotency_key="resume-3",
        ),
    )

    event = _latest_transition(migrated_session, unit.id, WorkUnitState.READY)
    assert event is not None
    assert event.improvisation is False
