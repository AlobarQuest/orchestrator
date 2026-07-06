import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Claim, ContextSnapshot, Event
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.context import PreflightCommand, record_preflight
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)
from tests.services.test_package_registration import AUTHORITY, NOW


def valid_context(**overrides: Any) -> dict[str, object]:
    value: dict[str, object] = {
        "code_standards_version": "1.0",
        "security_standards_version": "1.0",
        "project_standards_version": "1.0",
        "agent_id": "worker-1",
        "authority_profile": "agent-queue-v1",
        "runtime_name": "codex",
        "runtime_version": "1.0",
        "skill_bundle_id": "ws-3.3-protocol-smoke-runtime-semantics",
        "skill_bundle_version": "1",
        "capabilities": ["python"],
    }
    value.update(overrides)
    return value


def register_context_unit(session: Session, context: dict[str, object], unit_key: str):
    revision = register_revision(
        session,
        package_id=f"pkg-{unit_key}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:{unit_key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=NOW,
        approval_event_id=uuid.uuid4(),
        enforcement_snapshot={"required_context": context},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=unit_key,
        title=unit_key,
        outcome=f"{unit_key} complete",
        required_capability="python",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit.state = "ready"
    session.commit()
    return unit


def test_diagnostic_preflight_records_snapshot_and_event(
    migrated_session: Session,
) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "diagnostic")

    result = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=valid_context(),
            previous_context_snapshot_id=None,
            approval_id=None,
            purpose="diagnostic",
            idempotency_key="preflight-1",
        ),
        ActorContext("worker-1", ActorRole.WORKER),
    )

    assert isinstance(result, ContextSnapshot)
    assert result.work_unit_id == ready_unit.id
    assert result.classification == "accepted"
    assert result.decision == "accepted"
    event = migrated_session.scalar(select(Event).where(Event.id == result.event_id))
    assert event is not None
    assert event.action == "context.preflight_recorded"
    assert event.subject_id == result.id


def test_preflight_idempotent_replay_returns_same_snapshot(
    migrated_session: Session,
) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "idempotent")
    command = PreflightCommand(
        work_unit_id=ready_unit.id,
        standing_context=valid_context(),
        previous_context_snapshot_id=None,
        approval_id=None,
        purpose="diagnostic",
        idempotency_key="preflight-1",
    )

    first = record_preflight(migrated_session, command, ActorContext("worker-1", ActorRole.WORKER))
    replay = record_preflight(migrated_session, command, ActorContext("worker-1", ActorRole.WORKER))

    assert isinstance(first, ContextSnapshot)
    assert isinstance(replay, ContextSnapshot)
    assert replay.id == first.id


def test_missing_required_context_returns_domain_error(
    migrated_session: Session,
) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "missing-context")
    context = valid_context()
    context.pop("runtime_name")

    result = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=context,
            previous_context_snapshot_id=None,
            approval_id=None,
            purpose="claim",
            idempotency_key="preflight-missing",
        ),
        ActorContext("worker-1", ActorRole.WORKER),
    )

    assert isinstance(result, DomainError)
    assert result.code == "context_missing_required"


def test_authority_expansion_before_claim_returns_domain_error(
    migrated_session: Session,
) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "authority-expansion")

    result = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=valid_context(capabilities=["python", "deploy"]),
            previous_context_snapshot_id=None,
            approval_id=None,
            purpose="claim",
            idempotency_key="preflight-expansion",
        ),
        ActorContext("worker-1", ActorRole.WORKER),
    )

    assert isinstance(result, DomainError)
    assert result.code == "context_authority_expanding"


def test_same_scope_update_records_update_accepted_event(
    migrated_session: Session,
) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "same-scope")
    actor = ActorContext("worker-1", ActorRole.WORKER)
    first = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=valid_context(),
            previous_context_snapshot_id=None,
            approval_id=None,
            purpose="diagnostic",
            idempotency_key="preflight-1",
        ),
        actor,
    )
    assert isinstance(first, ContextSnapshot)

    update = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=valid_context(code_standards_version="1.1"),
            previous_context_snapshot_id=first.id,
            approval_id=None,
            purpose="diagnostic",
            idempotency_key="preflight-2",
        ),
        actor,
    )

    assert isinstance(update, ContextSnapshot)
    event = migrated_session.get(Event, update.event_id)
    assert event is not None
    assert event.action == "context.update_accepted"


def test_authority_expansion_with_matching_approval_records_accepted_snapshot(
    migrated_session: Session,
) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "approved-expansion")
    expanded_context = valid_context(capabilities=["python", "deploy"])
    approval = record_approval(
        migrated_session,
        unit_id=ready_unit.id,
        subject_type="authority",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="allow authority envelope for this unit",
        idempotency_key="context-approval-1",
        expected_version=ready_unit.version,
    )

    result = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=expanded_context,
            previous_context_snapshot_id=None,
            approval_id=approval.id,
            purpose="claim",
            idempotency_key="preflight-approved-expansion",
        ),
        ActorContext("worker-1", ActorRole.WORKER),
    )

    assert isinstance(result, ContextSnapshot)
    assert result.approval_id == approval.id
    assert result.classification == "authority_expanding"
    assert result.decision == "accepted"


def test_execution_preflight_requires_matching_active_claim_credentials(
    migrated_session: Session,
) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "execution-claim")
    grant = claim_unit(
        migrated_session,
        ready_unit.id,
        ActorContext("worker-1", ActorRole.WORKER),
        "claim-1",
    )
    assert isinstance(grant, LeaseGrant)

    result = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=valid_context(),
            previous_context_snapshot_id=None,
            approval_id=None,
            purpose="execution",
            idempotency_key="execution-preflight-1",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
        ActorContext("worker-1", ActorRole.WORKER),
    )

    assert isinstance(result, ContextSnapshot)
    assert result.claim_id == grant.claim_id
    assert result.attempt == grant.attempt


def test_execution_preflight_rejects_wrong_or_expired_claim_credentials(
    migrated_session: Session,
) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "execution-reject")
    grant = claim_unit(
        migrated_session,
        ready_unit.id,
        ActorContext("worker-1", ActorRole.WORKER),
        "claim-1",
    )
    assert isinstance(grant, LeaseGrant)

    wrong_token = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=valid_context(),
            previous_context_snapshot_id=None,
            approval_id=None,
            purpose="execution",
            idempotency_key="execution-preflight-wrong-token",
            attempt=grant.attempt,
            lease_token="wrong-token",
        ),
        ActorContext("worker-1", ActorRole.WORKER),
    )
    assert isinstance(wrong_token, DomainError)
    assert wrong_token.code == "active_claim_required"

    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    claim.lease_expires_at = claim.acquired_at
    migrated_session.commit()

    expired = record_preflight(
        migrated_session,
        PreflightCommand(
            work_unit_id=ready_unit.id,
            standing_context=valid_context(),
            previous_context_snapshot_id=None,
            approval_id=None,
            purpose="execution",
            idempotency_key="execution-preflight-expired",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
        ActorContext("worker-1", ActorRole.WORKER),
    )
    assert isinstance(expired, DomainError)
    assert expired.code == "active_claim_required"
