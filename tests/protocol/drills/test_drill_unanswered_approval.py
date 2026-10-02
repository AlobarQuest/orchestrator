"""Drill 5: a human approval gate that nobody answers.

A unit that asks for approval waits for a person. If nobody answers, the operator must be told,
and telling them must change nothing:

1. The unanswered gate appears in the dead-letter view with a named reason, at the state it is
   stuck in, and not offered for requeue.
2. Reading the view neither moves the unit nor bumps its version.
3. A second read still reports it: the view is derived from state, not consumed.

The staleness threshold is shortened through settings rather than by waiting or back-dating a row.
"""

from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from orchestrator.config import Settings, get_settings
from tests._support.protocol import (
    HUMAN,
    WORKER,
    birth_unit,
    make_ready,
    ok,
    ok_rows,
    stand_in_approved_package,
    standing_context,
    unit_row,
)


def test_an_unanswered_approval_gate_is_reported_and_left_alone(
    db_client: TestClient,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stand_in_approved_package(monkeypatch)
    stalled_at_once = Settings.model_validate({"dead_letter_stalled_approval_seconds": 0})
    cast(FastAPI, db_client.app).dependency_overrides[get_settings] = lambda: stalled_at_once
    unit = birth_unit(db_client, suffix="drill5").unit_id
    make_ready(db_client, migrated_engine, unit, key="drill5")

    def version() -> int:
        return unit_row(migrated_engine, unit).version

    lease = ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/claim",
            headers=WORKER,
            json={
                "idempotency_key": "drill5-claim",
                "expected_version": version(),
                "standing_context": standing_context(),
            },
        )
    )
    held = {"attempt": lease["attempt"], "lease_token": lease["lease_token"]}
    for name, extra in (
        ("start", {"standing_context": standing_context()}),
        ("request-approval", {}),
    ):
        ok(
            db_client.post(
                f"/api/v1/work-units/{unit}/commands/{name}",
                headers=WORKER,
                json={
                    "idempotency_key": f"drill5-{name}",
                    "expected_version": version(),
                    **held,
                    **extra,
                },
            )
        )
    at_gate = unit_row(migrated_engine, unit)
    assert at_gate.state == "awaiting_approval"

    def stalled_entries() -> list[dict]:
        report = ok_rows(db_client.get("/api/v1/dead-letter", headers=HUMAN))
        return [
            e for e in report if e["work_unit_id"] == unit and e["source"] == "stalled_approval"
        ]

    # 1. The gate is reported, with a reason, and not offered for requeue.
    [entry] = stalled_entries()
    assert entry["reason_code"] == "approval_unanswered"
    assert entry["unit_state"] == "awaiting_approval"
    assert entry["requeue_eligible"] is False

    # 2. Reporting it wrote nothing.
    after = unit_row(migrated_engine, unit)
    assert (after.state, after.version) == (at_gate.state, at_gate.version)

    # 3. The view is derived, so a second read reports it again.
    assert len(stalled_entries()) == 1
