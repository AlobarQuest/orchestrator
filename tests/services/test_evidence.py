import uuid
from typing import Any, cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, Evidence
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.evidence import (
    append_evidence,
    current_evidence,
    supersede_evidence,
)
from orchestrator.services.lifecycle import ActorContext


def worker() -> ActorContext:
    return ActorContext("worker-1", ActorRole.WORKER)


def active_claim(session: Session, unit) -> LeaseGrant:
    grant = claim_unit(session, unit.id, worker(), "claim-1")
    assert isinstance(grant, LeaseGrant)
    return grant


def evidence_kwargs(unit, grant: LeaseGrant) -> dict[str, Any]:
    return {
        "work_package_revision_id": unit.work_package_revision_id,
        "work_unit_id": unit.id,
        "ac_id": "ac-1",
        "attempt": grant.attempt,
        "actor": worker(),
        "lease_token": grant.lease_token,
        "evidence_type": "test",
        "stable_ref": "artifact://result",
        "payload": {"exit_code": 0},
        "source_revision": "abc123",
        "idempotency_key": "evidence-1",
    }


def append(session: Session, command: dict[str, Any]) -> Evidence | DomainError:
    return append_evidence(session, **cast(Any, command))


def supersede(session: Session, command: dict[str, Any]) -> Evidence | DomainError:
    return supersede_evidence(session, **cast(Any, command))


def test_evidence_requires_stable_ref_or_structured_payload(
    migrated_session: Session, ready_unit
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    command = evidence_kwargs(ready_unit, grant)
    command.update(stable_ref=None, payload=None)

    result = append(migrated_session, command)

    assert isinstance(result, DomainError)
    assert result.code == "evidence_required"


def test_evidence_binds_subject_attempt_actor_source_and_event(
    migrated_session: Session, ready_unit
) -> None:
    grant = active_claim(migrated_session, ready_unit)

    result = append(migrated_session, evidence_kwargs(ready_unit, grant))

    assert isinstance(result, Evidence)
    assert (
        result.work_package_revision_id,
        result.work_unit_id,
        result.ac_id,
        result.attempt,
        result.recorded_by,
        result.source_revision,
    ) == (
        ready_unit.work_package_revision_id,
        ready_unit.id,
        "ac-1",
        grant.attempt,
        "worker-1",
        "abc123",
    )
    event = migrated_session.get(Event, result.event_id)
    assert event is not None
    assert event.action == "evidence.recorded"
    assert event.subject_id == result.id


def test_exact_evidence_replay_returns_original_and_conflicting_reuse_fails(
    migrated_session: Session, ready_unit
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    command = evidence_kwargs(ready_unit, grant)
    first = append(migrated_session, command)
    replay = append(migrated_session, command)
    changed = append(migrated_session, command | {"source_revision": "def456"})
    wrong_token = append(migrated_session, command | {"lease_token": "wrong-token"})

    assert isinstance(first, Evidence)
    assert isinstance(replay, Evidence)
    assert replay.id == first.id
    assert isinstance(changed, DomainError)
    assert changed.code == "idempotency_conflict"
    assert isinstance(wrong_token, DomainError)
    assert wrong_token.code == "idempotency_conflict"


def test_supersession_appends_after_current_terminal(migrated_session: Session, ready_unit) -> None:
    grant = active_claim(migrated_session, ready_unit)
    original = append(migrated_session, evidence_kwargs(ready_unit, grant))
    assert isinstance(original, Evidence)

    replacement = supersede(
        migrated_session,
        evidence_kwargs(ready_unit, grant)
        | {
            "stable_ref": "artifact://replacement",
            "idempotency_key": "evidence-2",
        },
    )

    assert isinstance(replacement, Evidence)
    assert replacement.supersedes_evidence_id == original.id
    current = current_evidence(
        migrated_session,
        ready_unit.work_package_revision_id,
        ready_unit.id,
        "ac-1",
    )
    assert current is not None
    assert current.id == replacement.id


def test_subject_revision_and_acceptance_criterion_must_match(
    migrated_session: Session, ready_unit
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    command = evidence_kwargs(ready_unit, grant)

    for changed in (
        {"work_package_revision_id": uuid.uuid4()},
        {"ac_id": "unknown-ac"},
    ):
        result = append(migrated_session, command | changed)
        assert isinstance(result, DomainError)
        assert result.code == "evidence_subject_invalid"


def test_stale_or_invalid_attempt_credentials_are_rejected(
    migrated_session: Session, ready_unit
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    command = evidence_kwargs(ready_unit, grant)

    for changed in (
        {"attempt": grant.attempt + 1},
        {"lease_token": "wrong-token"},
    ):
        result = append(migrated_session, command | changed)
        assert isinstance(result, DomainError)
        assert result.code == "claim_not_owned"


def test_evidence_rows_remain_database_immutable(migrated_session: Session, ready_unit) -> None:
    grant = active_claim(migrated_session, ready_unit)
    row = append(migrated_session, evidence_kwargs(ready_unit, grant))
    assert isinstance(row, Evidence)

    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text("UPDATE evidence SET source_revision = 'changed' WHERE id = :id"),
            {"id": row.id},
        )
        migrated_session.commit()
    migrated_session.rollback()

    assert migrated_session.scalar(select(Evidence).where(Evidence.id == row.id)) is not None
