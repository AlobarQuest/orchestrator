import uuid
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_actor, get_session
from orchestrator.config import ProductionDrillMode, get_settings
from orchestrator.kernel.leases import LEASE_DURATION
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.claims import LeaseGrant, claim_unit, reclaim_expired_claim, renew_claim
from orchestrator.services.dead_letter import dead_letter
from orchestrator.services.in_flight import in_flight_snapshot
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import record_approval
from orchestrator.services.production_drill_resources import (
    is_not_production_drill_resource,
    reject_production_drill_resource,
)
from orchestrator.services.production_drills import lease_duration_for_work_unit
from tests.conftest import TEST_DATABASE_URL
from tests.services.test_dependencies import register_unit


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: ProductionDrillMode) -> None:
    monkeypatch.setenv("ORCHESTRATOR_PRODUCTION_DRILL_MODE", mode.value)
    get_settings.cache_clear()


def _register_ready_unit(session: Session, key: str, system: ActorContext) -> WorkUnit:
    unit = register_unit(session, key)
    record_approval(
        session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id="devon",
        actor_role=ActorRole.HUMAN,
        reason="approved ordinary work",
        idempotency_key=f"{key}-authority",
        expected_version=unit.version,
    )
    result = transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.READY,
            actor=system,
            expected_version=unit.version,
            idempotency_key=f"{key}-ready",
        ),
    )
    assert result.state is WorkUnitState.READY
    return unit


@pytest.mark.parametrize(
    ("mode", "references_drill_schema"),
    [
        (ProductionDrillMode.OFF, False),
        (ProductionDrillMode.STANDBY, True),
    ],
)
def test_ownership_predicate_only_references_drill_schema_when_active(
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
    references_drill_schema: bool,
) -> None:
    _set_mode(monkeypatch, mode)

    predicate = is_not_production_drill_resource("work_unit", uuid.uuid4())
    sql = str(predicate.compile(dialect=postgresql.dialect()))

    assert ("production_drill_resources" in sql) is references_drill_schema
    if not references_drill_schema:
        assert sql == "true"


@pytest.mark.parametrize(
    ("mode", "queries_ownership"),
    [
        (ProductionDrillMode.OFF, False),
        (ProductionDrillMode.STANDBY, True),
    ],
)
def test_ordinary_resource_rejection_only_queries_when_schema_is_active(
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
    queries_ownership: bool,
) -> None:
    _set_mode(monkeypatch, mode)
    session = Mock(spec=Session)
    session.scalar.return_value = None

    reject_production_drill_resource(session, "work_unit", uuid.uuid4())

    assert session.scalar.called is queries_ownership
    if queries_ownership:
        assert "production_drill_resources" in str(
            session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
        )


@pytest.mark.parametrize(
    ("mode", "queries_ownership"),
    [
        (ProductionDrillMode.OFF, False),
        (ProductionDrillMode.STANDBY, True),
    ],
)
def test_ordinary_lease_only_queries_when_schema_is_active(
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
    queries_ownership: bool,
) -> None:
    _set_mode(monkeypatch, mode)
    session = Mock(spec=Session)
    session.scalar.return_value = None

    duration = lease_duration_for_work_unit(session, uuid.uuid4())

    assert duration == LEASE_DURATION
    assert session.scalar.called is queries_ownership
    if queries_ownership:
        assert "production_drill_resources" in str(
            session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
        )


@pytest.fixture
def schema_0014_session(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Session]:
    _set_mode(monkeypatch, ProductionDrillMode.OFF)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(config, "0014_wsp21_recovery_controls")
    try:
        with Session(migrated_engine) as session:
            yield session
            session.rollback()
    finally:
        command.upgrade(config, "head")


def test_off_mode_preserves_ordinary_flows_on_schema_0014(
    schema_0014_session: Session,
    auth_config: AuthConfig,
) -> None:
    session = schema_0014_session
    worker = ActorContext("worker-1", ActorRole.WORKER)
    system = ActorContext("system", ActorRole.SYSTEM)

    reclaim_unit = _register_ready_unit(session, "off-claim-renew-reclaim", system)
    first_grant = claim_unit(session, reclaim_unit.id, worker, "off-claim")
    assert isinstance(first_grant, LeaseGrant)

    renewed = renew_claim(
        session,
        reclaim_unit.id,
        worker,
        first_grant.attempt,
        first_grant.lease_token,
        idempotency_key="off-renew",
        expected_version=3,
    )
    assert isinstance(renewed, LeaseGrant)
    session.execute(
        text(
            "UPDATE claims SET lease_expires_at = transaction_timestamp() - interval '1 second' "
            "WHERE id = :claim_id"
        ),
        {"claim_id": first_grant.claim_id},
    )
    session.commit()

    reclaimed = reclaim_expired_claim(
        session,
        reclaim_unit.id,
        system,
        ActorContext("worker-2", ActorRole.WORKER),
        "off-reclaim",
        expected_version=3,
    )
    assert isinstance(reclaimed, LeaseGrant)
    assert reclaimed.attempt == 2

    failed_unit = _register_ready_unit(session, "off-lifecycle-dead-letter", system)
    failed_grant = claim_unit(session, failed_unit.id, worker, "off-failed-claim")
    assert isinstance(failed_grant, LeaseGrant)
    transition = transition_unit(
        session,
        TransitionCommand(
            unit_id=failed_unit.id,
            target=WorkUnitState.FAILED,
            actor=worker,
            expected_version=3,
            idempotency_key="off-lifecycle-failed",
            attempt=failed_grant.attempt,
            lease_token=failed_grant.lease_token,
            reason="ordinary failure",
        ),
    )
    assert transition.state is WorkUnitState.FAILED

    entries = dead_letter(
        session,
        failure_signature_threshold=3,
        stalled_approval_seconds=604_800,
    )
    assert failed_unit.id in {entry.work_unit_id for entry in entries}

    queue_unit = _register_ready_unit(session, "off-human-queue", system)
    session.commit()
    snapshot = in_flight_snapshot(session)
    assert {view.work_unit_id for view in snapshot.units} >= {reclaim_unit.id, queue_unit.id}

    from orchestrator.main import create_app

    app = create_app(auth_config, ProductionDrillMode.OFF)

    def database_session() -> Iterator[Session]:
        assert session.bind is not None
        with Session(session.bind) as request_session:
            yield request_session

    app.dependency_overrides[get_session] = database_session
    app.dependency_overrides[get_actor] = lambda: ActorContext("devon", ActorRole.HUMAN)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/review")

    assert response.status_code == 200
    assert queue_unit.title in response.text
