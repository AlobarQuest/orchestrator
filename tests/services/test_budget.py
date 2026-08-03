import uuid

from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.budget import (
    cumulative_llm_calls,
    declared_ceiling,
    is_over_budget,
)
from orchestrator.services.packages import register_approved_unit, register_revision
from tests.services.test_package_registration import AUTHORITY as READY_UNIT_AUTHORITY

READY_UNIT_MAX_LLM_CALLS = READY_UNIT_AUTHORITY.budgets.max_llm_calls

NO_CEILING_AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=None),
)


def _build_unit_with_ceiling(session: Session, key: str, *, ceiling: int | None) -> WorkUnit:
    authority = AuthorityEnvelope(
        capabilities={"repo.edit": "allowed"},
        budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=ceiling),
    )
    now = TransactionClock().now(session)
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=now,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=authority,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    return register_approved_unit(
        session,
        unit_id=None,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=authority,
        max_attempts=3,
        approved_by="human-1",
        approved_at=now,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def _build_unit_no_ceiling(session: Session, key: str) -> WorkUnit:
    return _build_unit_with_ceiling(session, key, ceiling=None)


def _cost_event(
    session: Session, unit_id: uuid.UUID, *, llm_calls: int | None, cost_known: bool = True
) -> None:
    session.add(
        Event(
            occurred_at=TransactionClock().now(session),
            actor_id="worker",
            action="attempt.cost_recorded",
            subject_type="work_unit",
            subject_id=unit_id,
            from_state=None,
            to_state=None,
            payload={
                "attempt": 1,
                "cost_known": cost_known,
                "llm_calls": llm_calls if cost_known else None,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=f"cost-{uuid.uuid4()}",
        )
    )
    session.flush()


def test_cumulative_sums_known_calls(migrated_session: Session, ready_unit: WorkUnit) -> None:
    _cost_event(migrated_session, ready_unit.id, llm_calls=3)
    _cost_event(migrated_session, ready_unit.id, llm_calls=5)
    assert cumulative_llm_calls(migrated_session, ready_unit.id) == 8


def test_cumulative_excludes_unknown_cost(migrated_session: Session, ready_unit: WorkUnit) -> None:
    _cost_event(migrated_session, ready_unit.id, llm_calls=4)
    _cost_event(migrated_session, ready_unit.id, llm_calls=None, cost_known=False)
    assert cumulative_llm_calls(migrated_session, ready_unit.id) == 4


def test_declared_ceiling_reads_authority(migrated_session: Session, ready_unit: WorkUnit) -> None:
    assert declared_ceiling(ready_unit) == READY_UNIT_MAX_LLM_CALLS


def test_over_budget_boundary(migrated_session: Session, ready_unit: WorkUnit) -> None:
    ceiling = declared_ceiling(ready_unit)
    assert ceiling is not None
    _cost_event(migrated_session, ready_unit.id, llm_calls=ceiling - 1)
    assert is_over_budget(migrated_session, ready_unit) is False
    _cost_event(migrated_session, ready_unit.id, llm_calls=1)  # cumulative now == ceiling
    assert is_over_budget(migrated_session, ready_unit) is True


def test_no_ceiling_never_over_budget(migrated_session: Session) -> None:
    unit = _build_unit_no_ceiling(migrated_session, "no-ceiling")
    _cost_event(migrated_session, unit.id, llm_calls=10_000)
    assert declared_ceiling(unit) is None
    assert is_over_budget(migrated_session, unit) is False
