"""AC-009: the runner's bodies against the REAL orchestrator, not a mock.

Dedup, conflict, and the secret scanner are properties of the service. A mock cannot prove the
contract holds; only the real ingest can.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Observation, WorkUnit
from reconciliation_runner.facts import pr_observation
from tests.api.test_lifecycle_api import SYSTEM
from tests.services.test_dependencies import register_unit

HEAD = "a" * 40
UPDATED_AT = datetime(2026, 7, 10, 9, 15, tzinfo=UTC)
LATER = datetime(2026, 7, 10, 11, 0, tzinfo=UTC)


def _unit(engine: Engine) -> str:
    with Session(engine) as session:
        unit = register_unit(session, "runner-contract")
        unit.state = WorkUnitState.SUBMITTED
        session.commit()
        return str(unit.id)


def _observations(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(Observation)) or 0


def _body(unit_id: str, **overrides):
    kwargs = {
        "work_unit_id": unit_id,
        "pr_number": 41,
        "head_sha": HEAD,
        "state": "open",
        "merged": False,
        "observed_at": UPDATED_AT,
    }
    kwargs.update(overrides)
    return pr_observation(**kwargs)


def test_unchanged_reality_repulled_dedups_and_grows_no_rows(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The failure mode this contract exists to prevent: a wall-clock observed_at would make this
    a 409 observation_conflict on every pass, forever."""
    unit_id = _unit(migrated_engine)
    body = _body(unit_id)

    first = db_client.post("/api/v1/observations", headers=SYSTEM, json=body)
    after_first = _observations(migrated_engine)
    second = db_client.post("/api/v1/observations", headers=SYSTEM, json=body)

    assert first.status_code == 201
    assert second.status_code in {200, 201}
    assert second.json()["id"] == first.json()["id"]
    assert _observations(migrated_engine) == after_first  # no unbounded growth


def test_changed_reality_mints_a_new_row(db_client: TestClient, migrated_engine: Engine) -> None:
    unit_id = _unit(migrated_engine)
    db_client.post("/api/v1/observations", headers=SYSTEM, json=_body(unit_id))
    before = _observations(migrated_engine)

    merged = db_client.post(
        "/api/v1/observations",
        headers=SYSTEM,
        json=_body(unit_id, state="closed", merged=True, observed_at=LATER),
    )

    assert merged.status_code == 201
    assert _observations(migrated_engine) == before + 1


def test_a_raw_provider_payload_is_rejected_and_the_normalized_one_is_accepted(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """SECRET_KEY_PARTS contains "log", so GitHub's standard `logs_url` is rejected outright."""
    unit_id = _unit(migrated_engine)
    normalized = _body(unit_id)
    raw = dict(normalized)
    raw["idempotency_key"] = "raw-payload"
    raw["facts"] = dict(
        normalized["facts"], logs_url="https://api.github.com/repos/x/y/check-runs/1/logs"
    )

    rejected = db_client.post("/api/v1/observations", headers=SYSTEM, json=raw)
    accepted = db_client.post("/api/v1/observations", headers=SYSTEM, json=normalized)

    assert rejected.status_code == 409
    assert accepted.status_code == 201


def test_a_full_pass_transitions_no_existing_unit(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """Report-only, end to end against the real service."""
    unit_id = _unit(migrated_engine)
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, unit_id)
        assert unit is not None
        before = (unit.state, unit.version, unit.attempt_count)

    db_client.post(
        "/api/v1/observations",
        headers=SYSTEM,
        json=_body(unit_id, state="closed", merged=True),
    )
    db_client.post(
        "/api/v1/reconciliation/detect",
        headers=SYSTEM,
        json={"idempotency_key": "runner-detect", "expected_version": 0},
    )

    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, unit_id)
        assert unit is not None
        assert (unit.state, unit.version, unit.attempt_count) == before
