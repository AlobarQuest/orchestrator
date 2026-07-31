import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.readiness import ReadinessStatus
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Approval, Dependency, WorkUnit
from orchestrator.services.packages import (
    DependencySpec,
    evaluate_readiness,
    register_approved_unit,
    register_dependency,
    resolve_dependency,
)
from tests.services.test_package_registration import AUTHORITY, NOW, register_test_revision


def register_unit(
    session: Session,
    key: str,
    *,
    unit_id: uuid.UUID | None = None,
    dependencies: tuple[DependencySpec, ...] = (),
    acceptance_criteria: tuple[str, ...] = ("ac-1",),
) -> WorkUnit:
    revision = register_test_revision(session, acceptance_criteria=acceptance_criteria)
    return register_approved_unit(
        session,
        unit_id=unit_id,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        dependencies=dependencies,
    )


def test_internal_dependency_cycle_is_rejected(migrated_session: Session) -> None:
    first = register_unit(migrated_session, "first")
    second = register_unit(
        migrated_session,
        "second",
        dependencies=(DependencySpec.work_unit(first.id, "completed"),),
    )

    with pytest.raises(DomainError) as error:
        register_dependency(
            migrated_session,
            work_unit_id=first.id,
            spec=DependencySpec.work_unit(second.id, "completed"),
        )

    assert error.value.code == "dependency_cycle"


def test_resolved_dependency_is_attributable(migrated_session: Session) -> None:
    unit = register_unit(
        migrated_session,
        "first",
        dependencies=(DependencySpec.external("ci/build", "passed"),),
    )
    dependency = migrated_session.query(Dependency).filter_by(work_unit_id=unit.id).one()
    event_id = uuid.uuid4()

    resolved = resolve_dependency(
        migrated_session,
        dependency_id=dependency.id,
        status="satisfied",
        resolved_by="human-1",
        resolution_event_id=event_id,
        detail={"run": 42},
    )

    assert resolved.status == "satisfied"
    assert resolved.resolved_by == "human-1"
    assert resolved.resolution_event_id == event_id


def test_readiness_requires_exact_authority_approval(migrated_session: Session) -> None:
    unit_id = uuid.uuid4()
    unit = register_unit(migrated_session, "first", unit_id=unit_id)
    approval = Approval(
        subject_type="authority",
        subject_id=unit.id,
        subject_revision_or_fingerprint="wrong",
        decision="approved",
        approved_by="human-1",
        reason="approved",
        event_id=uuid.uuid4(),
        idempotency_key="approval-1",
    )
    migrated_session.add(approval)
    migrated_session.flush()
    unit.authority_approval_id = approval.id
    migrated_session.flush()

    decision = evaluate_readiness(migrated_session, unit.id)

    assert decision.status is ReadinessStatus.NOT_AUTHORIZED


def test_readiness_is_ready_after_dependency_and_exact_approval(
    migrated_session: Session,
) -> None:
    unit = register_unit(
        migrated_session,
        "first",
        dependencies=(DependencySpec.external("ci/build", "passed"),),
    )
    dependency = migrated_session.query(Dependency).filter_by(work_unit_id=unit.id).one()
    resolve_dependency(
        migrated_session,
        dependency_id=dependency.id,
        status="satisfied",
        resolved_by="human-1",
        resolution_event_id=uuid.uuid4(),
        detail={"run": 42},
    )
    approval = Approval(
        subject_type="authority",
        subject_id=unit.id,
        subject_revision_or_fingerprint=unit.authority_fingerprint,
        decision="approved",
        approved_by="human-1",
        reason="approved",
        event_id=uuid.uuid4(),
        idempotency_key="approval-1",
    )
    migrated_session.add(approval)
    migrated_session.flush()
    unit.authority_approval_id = approval.id
    migrated_session.flush()

    decision = evaluate_readiness(migrated_session, unit.id)

    assert decision.status is ReadinessStatus.READY


def test_concurrent_opposite_edges_allow_one_and_reject_cycle(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as setup:
        first = register_unit(setup, "first")
        second = register_unit(setup, "second")
        setup.commit()
        first_id = first.id
        second_id = second.id

    start = Barrier(2)
    before_unit_lock = Barrier(2)

    def synchronize_unit_lock(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "FROM work_units" in statement and "FOR UPDATE" in statement:
            before_unit_lock.wait(timeout=5)

    event.listen(migrated_engine, "before_cursor_execute", synchronize_unit_lock)

    def add_edge(edge: tuple[uuid.UUID, uuid.UUID]) -> str:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            try:
                register_dependency(
                    session,
                    work_unit_id=edge[0],
                    spec=DependencySpec.work_unit(edge[1], "completed"),
                )
                session.commit()
                return "registered"
            except DomainError as error:
                session.rollback()
                return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(add_edge, edge)
                for edge in ((first_id, second_id), (second_id, first_id))
            ]
            results = tuple(future.result(timeout=10) for future in futures)
    finally:
        event.remove(migrated_engine, "before_cursor_execute", synchronize_unit_lock)

    assert sorted(results) == ["dependency_cycle", "registered"]
