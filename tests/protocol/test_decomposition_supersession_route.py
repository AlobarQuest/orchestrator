"""Superseding an unworked approval through `/review`, end to end on public routes (ADR-0052)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests._support.protocol import (
    HUMAN,
    WORKER,
    birth_unit,
    decomposition_payload,
    make_ready,
    ok,
    ok_rows,
    stand_in_approved_package,
    unit_row,
)
from tests._support.review_forms import decide_decomposition


def test_a_ready_approval_is_superseded_and_its_key_re_approved(
    db_client: TestClient, migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in_approved_package(monkeypatch)
    born = birth_unit(db_client, suffix="r5")
    make_ready(db_client, migrated_engine, born.unit_id, key="r5")
    first = ok_rows(
        db_client.get(
            f"/api/v1/package-intakes/{born.revision_id}/decomposition-proposals", headers=HUMAN
        )
    )[0]

    decide_decomposition(db_client, first["id"], "supersede", "Wrong split.", headers=HUMAN)

    assert unit_row(migrated_engine, born.unit_id).state == "cancelled"
    criterion = ok(db_client.get(f"/api/v1/package-intakes/{born.revision_id}", headers=HUMAN))[
        "acceptance_criteria"
    ][0]
    second = db_client.post(
        f"/api/v1/package-intakes/{born.revision_id}/decomposition-proposals",
        headers=WORKER,
        json={
            **decomposition_payload({"AC-001": criterion["id"]}),
            "idempotency_key": "r5-second-proposal",
            "proposed_units": [
                {
                    "unit_key": "ws33-smoke-r5-unit",
                    "title": "WS-3.3 smoke r5, corrected",
                    "outcome": "Auxiliary smoke path is exercised.",
                    "required_capability": "repo.edit",
                    "authority": {
                        "capabilities": {"repo.edit": "allowed"},
                        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
                    },
                    "max_attempts": 3,
                }
            ],
            "ac_mappings": [{"ac_id": criterion["id"], "unit_key": "ws33-smoke-r5-unit"}],
        },
    )
    assert second.status_code == 201, second.json()

    approved = decide_decomposition(
        db_client, second.json()["id"], "approve", "Corrected split.", headers=HUMAN
    )

    new_unit = approved["created_work_unit_ids"]["ws33-smoke-r5-unit"]
    assert new_unit != born.unit_id
    assert unit_row(migrated_engine, new_unit).state == "draft"
