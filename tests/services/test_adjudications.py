from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, cast

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Adjudication, PackageAcceptanceCriterion
from orchestrator.services.evidence import current_adjudication, record_adjudication
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_dependencies import register_unit


def record(session: Session, command: dict[str, Any]) -> Adjudication | DomainError:
    return record_adjudication(session, **cast(Any, command))


def add_criterion(session: Session, unit, ac_id: str, evidence_type: str) -> None:
    session.add(
        PackageAcceptanceCriterion(
            work_package_revision_id=unit.work_package_revision_id,
            ac_id=ac_id,
            condition="condition",
            evidence_type=evidence_type,
            evidence="evidence",
            approver="human-1",
        )
    )
    session.flush()


def test_human_may_pass_a_judgment_type_ac(migrated_session: Session, ready_unit) -> None:
    add_criterion(migrated_session, ready_unit, "ac-1", "human.review")
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="reviewed and met",
        idempotency_key="human-pass-1",
    )
    assert isinstance(result, Adjudication)
    assert result.outcome == "passed"
    assert result.decided_by == "human-1"


def test_human_may_not_pass_a_deterministic_ac(migrated_session: Session, ready_unit) -> None:
    add_criterion(migrated_session, ready_unit, "ac-1", "test")
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="looks green to me",
        idempotency_key="human-pass-det",
    )
    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_human_may_not_record_failed(migrated_session: Session, ready_unit) -> None:
    add_criterion(migrated_session, ready_unit, "ac-1", "human.review")
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="failed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="not met",
        idempotency_key="human-failed-1",
    )
    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


@pytest.mark.parametrize("outcome", ["passed", "failed", "not_applicable"])
def test_verifier_records_each_non_waiver_outcome(
    migrated_session: Session, ready_unit, outcome: str
) -> None:
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome=outcome,
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="verified",
        idempotency_key=f"adjudication-{outcome}",
    )

    assert isinstance(result, Adjudication)
    assert result.outcome == outcome
    assert result.decided_by == "verifier-1"


def test_non_waiver_risk_outside_vocabulary_is_a_clean_error(
    migrated_session: Session, ready_unit
) -> None:
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("verifier-1", ActorRole.VERIFIER),
        rationale="verified",
        idempotency_key="non-waiver-bad-risk",
        risk="catastrophic",
    )

    assert isinstance(result, DomainError)
    assert result.code == "adjudication_invalid"


def test_worker_cannot_record_adjudication(migrated_session: Session, ready_unit) -> None:
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("worker-1", ActorRole.WORKER),
        rationale="looks good",
        idempotency_key="adjudication-1",
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_correction_supersedes_current_terminal_and_query_returns_it(
    migrated_session: Session, ready_unit
) -> None:
    common = {
        "work_package_revision_id": ready_unit.work_package_revision_id,
        "work_unit_id": ready_unit.id,
        "ac_id": "ac-1",
        "actor": ActorContext("verifier-1", ActorRole.VERIFIER),
        "rationale": "verified",
    }
    first = record_adjudication(
        migrated_session, outcome="failed", idempotency_key="adjudication-1", **common
    )
    second = record_adjudication(
        migrated_session, outcome="passed", idempotency_key="adjudication-2", **common
    )

    assert isinstance(first, Adjudication)
    assert isinstance(second, Adjudication)
    assert second.supersedes_adjudication_id == first.id
    current = current_adjudication(
        migrated_session,
        ready_unit.work_package_revision_id,
        ready_unit.id,
        "ac-1",
    )
    assert current is not None
    assert current.id == second.id


def test_adjudication_idempotency_is_exact(migrated_session: Session, ready_unit) -> None:
    command: dict[str, Any] = {
        "work_package_revision_id": ready_unit.work_package_revision_id,
        "work_unit_id": ready_unit.id,
        "ac_id": "ac-1",
        "outcome": "passed",
        "actor": ActorContext("verifier-1", ActorRole.VERIFIER),
        "rationale": "verified",
        "idempotency_key": "adjudication-1",
    }
    first = record(migrated_session, command)
    replay = record(migrated_session, command)
    changed = record(migrated_session, command | {"outcome": "failed"})

    assert isinstance(first, Adjudication)
    assert isinstance(replay, Adjudication)
    assert replay.id == first.id
    assert isinstance(changed, DomainError)
    assert changed.code == "idempotency_conflict"


def test_adjudication_replay_precedes_current_version_validation(
    migrated_session: Session, ready_unit
) -> None:
    command: dict[str, Any] = {
        "work_package_revision_id": ready_unit.work_package_revision_id,
        "work_unit_id": ready_unit.id,
        "ac_id": "ac-1",
        "outcome": "passed",
        "actor": ActorContext("verifier-1", ActorRole.VERIFIER),
        "rationale": "verified",
        "idempotency_key": "adjudication-versioned",
        "expected_version": ready_unit.version,
    }
    first = record(migrated_session, command)
    assert isinstance(first, Adjudication)
    ready_unit.version += 1
    migrated_session.commit()

    replay = record(migrated_session, command)
    changed_actor = record(
        migrated_session,
        command | {"actor": ActorContext("verifier-2", ActorRole.VERIFIER)},
    )

    assert isinstance(replay, Adjudication)
    assert replay.id == first.id
    assert isinstance(changed_actor, DomainError)
    assert changed_actor.code == "idempotency_conflict"


def test_concurrent_identical_adjudications_converge(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as setup:
        unit = register_unit(setup, "concurrent-adjudication")
        setup.commit()
        command: dict[str, Any] = {
            "work_package_revision_id": unit.work_package_revision_id,
            "work_unit_id": unit.id,
            "ac_id": "ac-1",
            "outcome": "passed",
            "actor": ActorContext("verifier-1", ActorRole.VERIFIER),
            "rationale": "verified",
            "idempotency_key": "concurrent-adjudication-1",
        }

    start = Barrier(2)

    def decide() -> tuple[str, object]:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            start.wait(timeout=5)
            result = record(session, command)
            if isinstance(result, Adjudication):
                return ("adjudication", result.id)
            return ("error", result.code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(decide) for _index in range(2))
        results = tuple(future.result(timeout=10) for future in futures)

    assert all(kind == "adjudication" for kind, _value in results)
    assert len({value for _kind, value in results}) == 1
