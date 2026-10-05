"""Drill 1: the orchestrator dies right after it records a dispatch.

The claim it granted is now held by nobody. The drill proves three things:

1. What the dispatch committed is all there is: one dispatch record, the unit still EXECUTING on
   its attempt, and its claim still open. (The drill cannot interrupt a write midway, so it does
   not test that a dispatch is atomic.)
2. A restarted orchestrator sees the same state. Canonical state lives in the database, and the
   process holds none of it. The restart is a fresh application over the same database.
3. The unit recovers through a public route. Once the lease lapses, an operator reclaims it to a
   next owner, and the crash costs one attempt rather than the unit.

Dispatch is switched off, so it is recorded as `skipped` / `dispatch_disabled` and the drill
reaches no GitHub repository. That is asserted, not assumed.
"""

from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig
from orchestrator.config import Settings, get_settings
from orchestrator.persistence.models import DispatchRecord
from tests._support.protocol import (
    SYSTEM,
    WORKER,
    birth_unit,
    claims_of,
    expire_latest_claim,
    make_ready,
    ok,
    ok_rows,
    protocol_client,
    refused,
    stand_in_approved_package,
    standing_context,
    unit_row,
)


def _dispatch_records(engine: Engine, unit_id: str) -> list[DispatchRecord]:
    with Session(engine, expire_on_commit=False) as session:
        rows = list(session.scalars(select(DispatchRecord)))
        session.expunge_all()
    return [row for row in rows if str(row.work_unit_id) == unit_id]


def test_a_crash_after_dispatch_leaves_a_recoverable_unit(
    db_client: TestClient,
    auth_config: AuthConfig,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stand_in_approved_package(monkeypatch)
    switched_off = Settings.model_validate({"dispatch_enabled": False})
    cast(FastAPI, db_client.app).dependency_overrides[get_settings] = lambda: switched_off
    unit = birth_unit(db_client, suffix="drill1").unit_id

    def version() -> int:
        return unit_row(migrated_engine, unit).version

    make_ready(db_client, migrated_engine, unit, key="drill1")
    lease = ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/claim",
            headers=WORKER,
            json={
                "idempotency_key": "drill1-claim",
                "expected_version": version(),
                "standing_context": standing_context(),
            },
        )
    )
    attempt = lease["attempt"]
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/commands/start",
            headers=WORKER,
            json={
                "idempotency_key": "drill1-start",
                "expected_version": version(),
                "attempt": attempt,
                "lease_token": lease["lease_token"],
                "standing_context": standing_context(),
            },
        )
    )
    dispatch = ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/dispatch",
            headers=SYSTEM,
            # No ordinal: the orchestrator assigns the next one (simplification review 3a).
            json={"idempotency_key": "drill1-dispatch", "expected_version": version()},
        )
    )
    assert (dispatch["status"], dispatch["reason_code"]) == ("skipped", "dispatch_disabled")
    before = unit_row(migrated_engine, unit)

    # The crash: the first application is abandoned. Nothing it held survives except what it
    # committed, which is everything there is.
    with protocol_client(auth_config, migrated_engine) as restarted:
        cast(FastAPI, restarted.app).dependency_overrides[get_settings] = lambda: switched_off
        # 1. Nothing was half-written.
        assert len(_dispatch_records(migrated_engine, unit)) == 1
        assert [c.released_at for c in claims_of(migrated_engine, unit)] == [None]
        after = unit_row(migrated_engine, unit)
        assert (after.state, after.version, after.attempt_count) == (
            before.state,
            before.version,
            before.attempt_count,
        )

        # 2. The restarted process reads the same state through its own public surface.
        ledger = ok_rows(
            restarted.get(
                "/api/v1/status-ledger",
                headers=SYSTEM,
                params={"work_unit_id": unit, "include_inactive": "true"},
            )
        )
        assert (ledger[0]["unit_state"], ledger[0]["claim_attempt"]) == ("executing", attempt)

        # 3. A live lease may still have a worker behind it, so nothing reclaims it yet.
        reclaim_body = {
            "expected_version": version(),
            "next_owner_id": "worker",
            "standing_context": standing_context(),
        }
        premature = restarted.post(
            f"/api/v1/work-units/{unit}/reclaim-expired-claim",
            headers=SYSTEM,
            json={**reclaim_body, "idempotency_key": "drill1-premature"},
        )
        assert refused(premature) == "lease_not_expired"

        expire_latest_claim(migrated_engine, unit)
        grant = ok(
            restarted.post(
                f"/api/v1/work-units/{unit}/reclaim-expired-claim",
                headers=SYSTEM,
                json={**reclaim_body, "idempotency_key": "drill1-reclaim"},
            )
        )

    assert grant["attempt"] == attempt + 1
    assert grant["lease_token"]
    claims = claims_of(migrated_engine, unit)
    released = [c for c in claims if c.released_at is not None]
    assert len(released) == 1 and released[0].terminal_reason
    assert [c.attempt for c in claims if c.released_at is None] == [attempt + 1]
    recovered = unit_row(migrated_engine, unit)
    assert recovered.attempt_count == attempt + 1 < recovered.max_attempts
    # Neither the crash nor the recovery reached GitHub.
    assert {r.status for r in _dispatch_records(migrated_engine, unit)} == {"skipped"}
