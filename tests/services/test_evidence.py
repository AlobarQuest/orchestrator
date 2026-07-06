import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, cast

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, ContextSnapshot, Event, Evidence
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.evidence import (
    append_evidence,
    current_evidence,
    supersede_evidence,
)
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from tests.services.test_context_preflight import register_context_unit, valid_context
from tests.services.test_dependencies import register_unit


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


def executing_context_claim(session: Session, unit) -> LeaseGrant:
    grant = claim_unit(
        session,
        unit.id,
        worker(),
        "claim-1",
        standing_context=valid_context(),
    )
    assert isinstance(grant, LeaseGrant)
    result = transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=worker(),
            expected_version=unit.version,
            idempotency_key="start-1",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            standing_context=valid_context(),
            context_snapshot_id=grant.context_snapshot_id,
        ),
    )
    assert result.state is WorkUnitState.EXECUTING
    return grant


def test_evidence_defaults_to_active_execution_context(migrated_session: Session) -> None:
    unit = register_context_unit(migrated_session, valid_context(), "evidence-context")
    grant = executing_context_claim(migrated_session, unit)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    assert claim.execution_context_snapshot_id is not None

    result = append(migrated_session, evidence_kwargs(unit, grant))

    assert isinstance(result, Evidence)
    assert result.context_snapshot_id == claim.execution_context_snapshot_id
    event = migrated_session.get(Event, result.event_id)
    assert event is not None
    assert event.payload["command"]["context_snapshot_id"] == str(
        claim.execution_context_snapshot_id
    )

    conflicting_replay = append(
        migrated_session,
        evidence_kwargs(unit, grant) | {"context_snapshot_id": grant.context_snapshot_id},
    )
    assert isinstance(conflicting_replay, DomainError)
    assert conflicting_replay.code == "idempotency_conflict"


def test_evidence_rejects_context_snapshot_from_old_attempt(migrated_session: Session) -> None:
    unit = register_context_unit(migrated_session, valid_context(), "evidence-stale-context")
    grant = executing_context_claim(migrated_session, unit)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    assert claim.execution_context_snapshot_id is not None
    stale_snapshot = migrated_session.get(ContextSnapshot, claim.execution_context_snapshot_id)
    assert stale_snapshot is not None

    claim.released_at = claim.acquired_at
    claim.terminal_reason = "test_release"
    unit.state = "ready"
    migrated_session.commit()
    second = claim_unit(
        migrated_session,
        unit.id,
        worker(),
        "claim-2",
        standing_context=valid_context(),
    )
    assert isinstance(second, LeaseGrant)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=worker(),
            expected_version=unit.version,
            idempotency_key="start-2",
            attempt=second.attempt,
            lease_token=second.lease_token,
            standing_context=valid_context(),
            context_snapshot_id=second.context_snapshot_id,
        ),
    )

    result = append(
        migrated_session,
        evidence_kwargs(unit, second)
        | {
            "context_snapshot_id": stale_snapshot.id,
            "idempotency_key": "evidence-stale-context",
        },
    )

    assert isinstance(result, DomainError)
    assert result.code == "context_snapshot_invalid"


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


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_evidence_idempotency_is_stable(
    migrated_engine: Engine, conflicting: bool
) -> None:
    with Session(migrated_engine) as setup:
        unit = register_unit(setup, "concurrent-evidence")
        unit.state = "ready"
        setup.commit()
        grant = active_claim(setup, unit)
        command = evidence_kwargs(unit, grant)

    start = Barrier(2)

    def record(source_revision: str) -> tuple[str, uuid.UUID | str]:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            start.wait(timeout=5)
            result = append(session, command | {"source_revision": source_revision})
            if isinstance(result, Evidence):
                return ("evidence", result.id)
            return ("error", result.code)

    revisions = ("abc123", "def456" if conflicting else "abc123")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(record, revision) for revision in revisions)
        results = tuple(future.result(timeout=10) for future in futures)

    evidence = tuple(value for kind, value in results if kind == "evidence")
    errors = tuple(value for kind, value in results if kind == "error")
    assert len(evidence) == (1 if conflicting else 2)
    assert len(set(evidence)) <= 1
    assert list(errors) == (["idempotency_conflict"] if conflicting else [])
