"""The sweep's records, driven through the route an OBSERVER credential actually posts to.

Everything else about this producer is proven against the service or against its own bytes. This
goes through the HTTP surface -- `ObservationCommandModel`, the OBSERVER confinement, the CHECK
constraints migration 0029 widened -- and re-reads through a DIFFERENT session, because a service
that flushes without committing returns the right object to its caller and leaves no row behind.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from activation_sweep.checkout import read_checkout
from activation_sweep.record import activation_observation, record_digest
from orchestrator.persistence.models import Observation
from tests.activation_sweep.conftest import Estate

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}


def _post(client: TestClient, estate: Estate) -> tuple[int, dict]:
    body = activation_observation(read_checkout(estate.local))
    return client.post("/api/v1/observations", headers=OBSERVER, json=body).status_code, body


def _rows(engine: Engine) -> list[Observation]:
    with Session(engine) as session:
        return list(session.scalars(select(Observation).order_by(Observation.received_at)).all())


def test_every_state_the_sweep_reports_is_accepted_by_the_real_route(
    db_client: TestClient, migrated_engine: Engine, tmp_path: Path
) -> None:
    """The premise made executable: the widened vocabulary, the bounds, the secret detector and
    the request model all admit these records unchanged, over the wire."""
    estate = Estate(tmp_path)

    assert _post(db_client, estate)[0] == 201
    estate.land_upstream("chore(deps-dev): bump ruff from 0.16.2 to 0.16.3 (#76)")
    assert _post(db_client, estate)[0] == 201
    estate.modify_tracked()
    assert _post(db_client, estate)[0] == 201

    rows = _rows(migrated_engine)
    assert {row.source_system for row in rows} == {"machine_activation"}
    assert {row.observation_type for row in rows} == {"activation"}
    assert [row.facts["conditions"] for row in rows] == [[], ["behind"], ["behind", "dirty"]]
    assert [row.status for row in rows] == ["passed", "degraded", "degraded"]


def test_a_second_sweep_over_unchanged_reality_replays_over_the_wire(
    db_client: TestClient, migrated_engine: Engine, tmp_path: Path
) -> None:
    estate = Estate(tmp_path)

    for _ in range(3):
        assert _post(db_client, estate)[0] == 201

    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 1


def test_the_stored_facts_are_the_bytes_the_reference_was_digested_over(
    db_client: TestClient, migrated_engine: Engine, tmp_path: Path
) -> None:
    """What content-addressing buys, and it is only true if it survives the round trip.

    The orchestrator normalizes every stored string with `.strip()` and sorts every key, so a
    producer that truncated a commit subject onto a trailing space would store bytes that no
    longer digest to the reference they are filed under -- and nobody could check one against
    the other afterwards.
    """
    estate = Estate(tmp_path)
    estate.land_upstream("a subject padded so its truncation lands on a space" + " x" * 90)
    status, body = _post(db_client, estate)

    assert status == 201
    row = _rows(migrated_engine)[0]
    assert row.facts == body["facts"]
    record = {
        key: value
        for key, value in body.items()
        if key not in {"idempotency_key", "source_reference"}
    }
    assert row.source_reference.endswith(record_digest(record))
