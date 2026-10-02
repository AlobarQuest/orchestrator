"""Drill 3: the pull request moves after submit, then merges outside the session.

Reconciliation must detect both and decide neither:

1. Observations that match the reported head raise nothing, and a rebase before submit is normal
   iteration. Reporting a head does not arm the alarm; submitting does.
2. A head change after submit raises `pr_state_divergence`, recording what was observed and what
   was expected.
3. A merge outside the session raises `external_merge_alarm` naming the pull request.
4. Detection changes nothing else: the unit stays submitted, no adjudication or dispatch is
   written, and every condition stays open for a human.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from orchestrator.persistence.models import (
    Adjudication,
    DispatchRecord,
    ReconciliationCondition,
    ReconciliationResolution,
)
from tests._support.protocol import (
    SYSTEM,
    WORKER,
    birth_unit,
    make_ready,
    ok,
    rows,
    stand_in_approved_package,
    standing_context,
    unit_row,
)

PR = 4242
HEAD_A, HEAD_B, HEAD_C, HEAD_X = "a" * 40, "b" * 40, "c" * 40, "d" * 40


def test_a_moved_head_and_an_external_merge_are_detected_and_left_open(
    db_client: TestClient,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stand_in_approved_package(monkeypatch)
    unit = birth_unit(db_client, suffix="drill3").unit_id
    unit_uuid = uuid.UUID(unit)
    make_ready(db_client, migrated_engine, unit, key="drill3")

    def version() -> int:
        return unit_row(migrated_engine, unit).version

    def conditions(kind: str | None = None) -> list[ReconciliationCondition]:
        found = rows(
            migrated_engine,
            ReconciliationCondition,
            ReconciliationCondition.work_unit_id == unit_uuid,
        )
        return [c for c in found if kind is None or c.condition_type == kind]

    def observe(key: str, head: str, state: str, merged: bool, minute: int) -> None:
        ok(
            db_client.post(
                "/api/v1/observations",
                headers=SYSTEM,
                json={
                    "idempotency_key": key,
                    "expected_version": 0,
                    "source_system": "github",
                    "source_reference": f"github_pr:{key}",
                    "trust_classification": "delivery_system",
                    "subject_type": "work_unit",
                    "subject_reference": unit,
                    "observation_type": "github_pr",
                    "status": "observed",
                    "severity": "info",
                    "observed_at": f"2026-07-11T12:{minute:02d}:00+00:00",
                    "summary": "pull request observed",
                    "facts": {"pr_number": PR, "head_sha": head, "state": state, "merged": merged},
                },
            ),
            201,
        )

    lease = ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/claim",
            headers=WORKER,
            json={
                "idempotency_key": "drill3-claim",
                "expected_version": version(),
                "standing_context": standing_context(),
            },
        )
    )
    held = {"attempt": lease["attempt"], "lease_token": lease["lease_token"]}
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/commands/start",
            headers=WORKER,
            json={
                "idempotency_key": "drill3-start",
                "expected_version": version(),
                "standing_context": standing_context(),
                **held,
            },
        )
    )

    def bind(key: str, head: str) -> dict:
        return ok(
            db_client.post(
                f"/api/v1/work-units/{unit}/pr-binding",
                headers=WORKER,
                json={
                    "idempotency_key": key,
                    "expected_version": 0,
                    "pr_number": PR,
                    "head_sha": head,
                    **held,
                },
            )
        )

    # 1. Reporting and rebasing before submit is ordinary work.
    binding = bind("drill3-binding-1", HEAD_A)
    assert (binding["head_sha"], binding["verification_read_head_sha"]) == (HEAD_A, None)
    observe("drill3-obs-1", HEAD_A, "open", merged=False, minute=0)
    assert conditions() == []
    # GitHub can show a rebase before the worker reports it. Unarmed, that is not a divergence:
    # the alarm compares against the head SUBMIT hands over, never the one last reported.
    observe("drill3-obs-early", HEAD_X, "open", merged=False, minute=2)
    assert conditions() == []
    assert bind("drill3-binding-2", HEAD_B)["head_sha"] == HEAD_B
    observe("drill3-obs-2", HEAD_B, "open", merged=False, minute=5)
    assert conditions() == []

    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/commands/submit",
            headers=WORKER,
            json={"idempotency_key": "drill3-submit", "expected_version": version(), **held},
        )
    )
    in_flight = ok(db_client.get("/api/v1/in-flight-units", headers=SYSTEM))["units"]
    armed = [u["verification_read_head_sha"] for u in in_flight if u["work_unit_id"] == unit]
    assert armed == [HEAD_B]

    # 2. A head change after arming is a divergence.
    observe("drill3-obs-3", HEAD_C, "open", merged=False, minute=10)
    [divergence] = conditions("pr_state_divergence")
    assert divergence.observed_state["head_sha"] == HEAD_C
    assert divergence.stored_state["verification_read_head_sha"] == HEAD_B

    # 3. A merge nobody in the session made is an alarm.
    observe("drill3-obs-4", HEAD_C, "closed", merged=True, minute=15)
    [alarm] = conditions("external_merge_alarm")
    assert alarm.observed_state["pr_number"] == PR

    # 4. Detection decided nothing.
    assert unit_row(migrated_engine, unit).state == "submitted"
    assert rows(migrated_engine, Adjudication, Adjudication.work_unit_id == unit_uuid) == []
    assert rows(migrated_engine, DispatchRecord, DispatchRecord.work_unit_id == unit_uuid) == []
    condition_ids = [c.id for c in conditions()]
    assert len(condition_ids) == 2
    assert (
        rows(
            migrated_engine,
            ReconciliationResolution,
            ReconciliationResolution.condition_id.in_(condition_ids),
        )
        == []
    )
