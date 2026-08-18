"""ADR-0021's recovery floor, driven through the route its producers actually post to.

The four scheduled jobs are shell scripts on the operator machine; nothing in this repository
executes them. What IS testable here is the half that wedges: that the vocabulary admits the shape
they send, that a second night appends rather than conflicting, and that an unchanged re-post of
the same night replays instead of raising -- the trap ADR-0022's first draft fell into by
identifying the JOB rather than the RUN.

Re-read through a session the write never touched: a service that flushes without committing
returns the right object to its caller and leaves no row behind.
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Observation

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}


def run_observation(
    *, started_at: str, status: str = "passed", severity: str = "info", **facts: Any
) -> dict[str, Any]:
    """The exact shape `observe-run.sh` builds, in this repository's own vocabulary."""
    body = {"stage": "complete", "exit_code": 0, "warnings": 0, **facts}
    return {
        "idempotency_key": f"recovery-floor:vps-backup:{started_at}",
        "expected_version": 0,
        "source_system": "recovery_floor",
        "source_reference": f"recovery-floor:vps-backup:{started_at}",
        "source_url": None,
        "trust_classification": "monitor",
        "subject_type": "external_run",
        "subject_reference": "backup:vps-production",
        "environment": "production",
        "observation_type": "backup",
        "status": status,
        "severity": severity,
        "observed_at": started_at,
        "summary": f"backup:vps-production — {status}",
        "facts": body,
        "payload_digest": None,
    }


def stored(engine: Engine) -> list[Observation]:
    with Session(engine) as session:
        return list(
            session.scalars(select(Observation).order_by(Observation.source_reference)).all()
        )


def test_the_recovery_floor_shape_is_admitted_end_to_end(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    response = db_client.post(
        "/api/v1/observations",
        headers=OBSERVER,
        json=run_observation(started_at="2026-08-18T02:00:04+00:00", databases=13),
    )
    assert response.status_code == 201, response.text

    (row,) = stored(migrated_engine)
    assert row.source_system == "recovery_floor"
    assert row.observation_type == "backup"
    assert row.subject_type == "external_run"
    assert row.facts["databases"] == 13


def test_a_chain_integrity_run_is_admitted(db_client: TestClient, migrated_engine: Engine) -> None:
    body = run_observation(started_at="2026-08-18T03:30:02+00:00")
    body["observation_type"] = "chain_integrity"
    body["subject_reference"] = "chain:factory-events"
    body["idempotency_key"] = body["source_reference"] = "recovery-floor:factory-events:2026-08-18"

    assert db_client.post("/api/v1/observations", headers=OBSERVER, json=body).status_code == 201
    (row,) = stored(migrated_engine)
    assert row.observation_type == "chain_integrity"


def test_two_nights_append_and_a_re_post_of_one_night_replays(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The conflict trap, both directions. The reference identifies the RUN, so differing facts on
    a different night are a new row; identical facts for the same run are the same row."""
    monday = run_observation(started_at="2026-08-18T02:00:04+00:00", databases=13)
    tuesday = run_observation(started_at="2026-08-19T02:00:11+00:00", databases=12)

    for body in (monday, tuesday, monday):
        posted = db_client.post("/api/v1/observations", headers=OBSERVER, json=body)
        assert posted.status_code == 201, posted.text

    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 2
    first, second = stored(migrated_engine)
    assert first.normalized_fact_hash != second.normalized_fact_hash
    assert (first.facts["databases"], second.facts["databases"]) == (13, 12)


def test_the_same_run_reporting_different_facts_is_a_loud_conflict(db_client: TestClient) -> None:
    """The failure the reference shape is chosen to make impossible in practice, proven to still
    be loud when it does happen: a producer whose facts moved under a fixed run identity is
    refused, not silently written as a second row."""
    started = "2026-08-18T02:00:04+00:00"
    assert (
        db_client.post(
            "/api/v1/observations", headers=OBSERVER, json=run_observation(started_at=started)
        ).status_code
        == 201
    )
    drifted = run_observation(started_at=started, warnings=2)
    drifted["idempotency_key"] = "recovery-floor:vps-backup:retry"

    response = db_client.post("/api/v1/observations", headers=OBSERVER, json=drifted)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "observation_conflict"
