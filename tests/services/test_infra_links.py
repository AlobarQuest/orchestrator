import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, InfraLaneLink
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.infra_links import (
    InfraLaneLinkCommand,
    list_infra_lane_links,
    record_infra_lane_link,
)
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit


def worker() -> ActorContext:
    return ActorContext("worker-1", ActorRole.WORKER)


def active_claim(session: Session, unit) -> LeaseGrant:
    unit.state = "ready"
    session.commit()
    grant = claim_unit(session, unit.id, worker(), "infra-link-claim")
    assert isinstance(grant, LeaseGrant)
    return grant


def command(
    unit,
    grant: LeaseGrant,
    *,
    key: str = "infra-link-1",
    **overrides: Any,
) -> InfraLaneLinkCommand:
    base = InfraLaneLinkCommand(
        work_unit_id=unit.id,
        attempt=grant.attempt,
        actor=worker(),
        lease_token=grant.lease_token,
        status="approved",
        change_manager_ref="change-manager:item:42",
        change_manager_url="https://change-manager.invalid/items/42",
        infraops_ref="infraops:window:2026-07-08",
        approval_ref="https://change-manager.invalid/items/42#approval",
        rollback_ref="https://infraops.invalid/windows/2026-07-08/rollback",
        verify_ref="https://infraops.invalid/windows/2026-07-08/verify",
        final_evidence_ref="s3://evidence/ws44/final.json",
        payload={"summary": "linked to existing infra lane"},
        idempotency_key=key,
        expected_version=unit.version,
    )
    return replace(base, **overrides)


def test_worker_records_infra_lane_link_without_lifecycle_mutation(
    migrated_session: Session,
    ready_unit,
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    original_state = ready_unit.state
    original_version = ready_unit.version

    result = record_infra_lane_link(migrated_session, command(ready_unit, grant))

    assert isinstance(result, InfraLaneLink)
    assert result.work_package_revision_id == ready_unit.work_package_revision_id
    assert result.work_unit_id == ready_unit.id
    assert result.attempt == grant.attempt
    assert result.status == "approved"
    assert result.change_manager_ref == "change-manager:item:42"
    assert result.infraops_ref == "infraops:window:2026-07-08"
    assert result.recorded_by == "worker-1"
    assert ready_unit.state == original_state
    assert ready_unit.version == original_version
    event = migrated_session.get(Event, result.event_id)
    assert event is not None
    assert event.action == "infra_lane_link.recorded"
    assert event.subject_type == "infra_lane_link"
    assert event.subject_id == result.id
    assert event.payload["command"]["change_manager_ref"] == "change-manager:item:42"
    assert "token" not in str(event.payload).lower()


def test_infra_lane_link_replay_and_conflict(
    migrated_session: Session,
    ready_unit,
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    first = record_infra_lane_link(migrated_session, command(ready_unit, grant))
    replay = record_infra_lane_link(migrated_session, command(ready_unit, grant))
    changed = record_infra_lane_link(
        migrated_session,
        command(ready_unit, grant, status="failed"),
    )

    assert isinstance(first, InfraLaneLink)
    assert isinstance(replay, InfraLaneLink)
    assert replay.id == first.id
    assert isinstance(changed, DomainError)
    assert changed.code == "idempotency_conflict"


def test_infra_lane_link_allows_status_progression_for_same_change(
    migrated_session: Session,
    ready_unit,
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    approved = record_infra_lane_link(migrated_session, command(ready_unit, grant, key="approved"))
    completed = record_infra_lane_link(
        migrated_session,
        command(
            ready_unit,
            grant,
            key="completed",
            status="completed",
            final_evidence_ref="https://infraops.invalid/windows/2026-07-08/final",
        ),
    )

    assert isinstance(approved, InfraLaneLink)
    assert isinstance(completed, InfraLaneLink)
    assert completed.id != approved.id
    assert completed.change_manager_ref == approved.change_manager_ref
    assert completed.status == "completed"


def test_infra_lane_link_rejects_stale_or_invalid_claim(
    migrated_session: Session,
    ready_unit,
) -> None:
    grant = active_claim(migrated_session, ready_unit)

    stale_attempt = record_infra_lane_link(
        migrated_session,
        command(ready_unit, grant, key=str(uuid.uuid4()), attempt=grant.attempt + 1),
    )
    wrong_token = record_infra_lane_link(
        migrated_session,
        command(ready_unit, grant, key=str(uuid.uuid4()), lease_token="wrong-token"),
    )

    assert isinstance(stale_attempt, DomainError)
    assert stale_attempt.code == "claim_not_owned"
    assert isinstance(wrong_token, DomainError)
    assert wrong_token.code == "claim_not_owned"


def test_infra_lane_link_rejects_inactive_claim_state(
    migrated_session: Session,
    ready_unit,
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    blocked = transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=ready_unit.id,
            target=WorkUnitState.BLOCKED,
            actor=worker(),
            expected_version=ready_unit.version,
            idempotency_key="infra-link-block",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    assert blocked.state is WorkUnitState.BLOCKED

    result = record_infra_lane_link(
        migrated_session,
        command(ready_unit, grant, key="inactive-claim", expected_version=ready_unit.version),
    )

    assert isinstance(result, DomainError)
    assert result.code == "claim_not_active"


def test_infra_lane_link_rejects_secret_shaped_metadata(
    migrated_session: Session,
    ready_unit,
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    shaped_like_bws_token = f"0.{uuid.uuid4()}.not-a-real-token-fixture"
    bearer_header = "Authorization: Bearer not-a-real-token-fixture"

    for index, changed in enumerate(
        (
            {"change_manager_url": bearer_header},
            {"payload": {"api_token": "not-a-real-token-fixture"}},
            {"payload": {"nested": {"value": shaped_like_bws_token}}},
        )
    ):
        result = record_infra_lane_link(
            migrated_session,
            command(ready_unit, grant, key=f"secret-shaped-{index}", **changed),
        )
        assert isinstance(result, DomainError)
        assert result.code == "infra_link_secret_rejected"


def test_infra_lane_link_rejects_cross_operation_idempotency_key(
    migrated_session: Session,
    ready_unit,
) -> None:
    ready_unit.state = "ready"
    migrated_session.commit()
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "shared-idempotency-key")
    assert isinstance(grant, LeaseGrant)

    result = record_infra_lane_link(
        migrated_session,
        command(ready_unit, grant, key="shared-idempotency-key"),
    )

    assert isinstance(result, DomainError)
    assert result.code == "idempotency_conflict"


def test_list_infra_lane_links_requires_existing_work_unit(
    migrated_session: Session,
    ready_unit,
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    first = record_infra_lane_link(migrated_session, command(ready_unit, grant, key="first"))
    second = record_infra_lane_link(
        migrated_session,
        command(
            ready_unit,
            grant,
            key="second",
            status="completed",
            change_manager_ref="change-manager:item:43",
        ),
    )

    rows = list_infra_lane_links(migrated_session, ready_unit.id)
    missing = list_infra_lane_links(migrated_session, uuid.uuid4())

    assert isinstance(first, InfraLaneLink)
    assert isinstance(second, InfraLaneLink)
    assert not isinstance(rows, DomainError)
    assert [row.id for row in rows] == [first.id, second.id]
    assert isinstance(missing, DomainError)
    assert missing.code == "work_unit_not_found"
