"""Drill 2: the lease lapses after the worker records evidence and before it submits.

The work was done, and the worker can no longer report it. The drill proves the work is recovered
rather than redone:

1. An expired lease buys nothing: the worker's late evidence is refused.
2. An operator attaches the evidence through the recovery route. It supersedes the worker's head
   without forking the chain or overwriting the original, and a replay writes nothing new.
3. Recovery releases the dead claim and fails the unit without minting an attempt.
4. A requeued unit's next attempt submits on the recovered evidence, and the unit completes on
   it: a human passes the criterion citing the recovered row.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Evidence
from tests._support.protocol import (
    HUMAN,
    SYSTEM,
    VERIFIER,
    WORKER,
    birth_unit,
    claims_of,
    expire_latest_claim,
    make_ready,
    ok,
    refused,
    stand_in_approved_package,
    standing_context,
    unit_row,
)


def _chain(engine: Engine, unit_id: str, ac_id: str) -> list[Evidence]:
    with Session(engine, expire_on_commit=False) as session:
        rows = list(
            session.scalars(
                select(Evidence).where(
                    Evidence.work_unit_id == uuid.UUID(unit_id), Evidence.ac_id == ac_id
                )
            )
        )
        session.expunge_all()
    return rows


def _heads(chain: list[Evidence]) -> list[uuid.UUID]:
    superseded = {row.supersedes_evidence_id for row in chain}
    return [row.id for row in chain if row.id not in superseded]


def test_a_lapsed_lease_recovers_the_evidence_instead_of_the_work(
    db_client: TestClient,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stand_in_approved_package(monkeypatch)
    born = birth_unit(db_client, suffix="drill2")
    unit, ac_id = born.unit_id, born.ac_id
    make_ready(db_client, migrated_engine, unit, key="drill2")

    def version() -> int:
        return unit_row(migrated_engine, unit).version

    def claim_and_start(key: str) -> dict:
        lease = ok(
            db_client.post(
                f"/api/v1/work-units/{unit}/claim",
                headers=WORKER,
                json={
                    "idempotency_key": f"{key}-claim",
                    "expected_version": version(),
                    "standing_context": standing_context(),
                },
            )
        )
        ok(
            db_client.post(
                f"/api/v1/work-units/{unit}/commands/start",
                headers=WORKER,
                json={
                    "idempotency_key": f"{key}-start",
                    "expected_version": version(),
                    "attempt": lease["attempt"],
                    "lease_token": lease["lease_token"],
                    "standing_context": standing_context(),
                },
            )
        )
        return lease

    def evidence(key: str, lease: dict, ref: str):
        return db_client.post(
            f"/api/v1/work-units/{unit}/evidence",
            headers=WORKER,
            json={
                "idempotency_key": key,
                "expected_version": version(),
                "work_package_revision_id": born.revision_id,
                "ac_id": ac_id,
                "attempt": lease["attempt"],
                "lease_token": lease["lease_token"],
                "evidence_type": "automated_test",
                "stable_ref": ref,
                "source_revision": "abc123",
            },
        )

    lease = claim_and_start("drill2")
    attempt = lease["attempt"]
    first_id = ok(evidence("drill2-evidence-1", lease, "artifact://drill2/run-1"))["id"]

    expire_latest_claim(migrated_engine, unit)

    # 1. The lapse locks the worker out.
    late = evidence("drill2-evidence-late", lease, "artifact://drill2/too-late")
    assert refused(late) == "claim_not_active"

    # 2. The operator recovers the evidence the dead attempt produced.
    recover = {
        "idempotency_key": "drill2-recover",
        "expected_version": version(),
        "work_package_revision_id": born.revision_id,
        "ac_id": ac_id,
        "evidence_type": "automated_test",
        "stable_ref": "artifact://drill2/run-1",
        "source_revision": "abc123",
    }
    recover_path = f"/api/v1/work-units/{unit}/attempts/{attempt}/recover-evidence"
    recovered_id = ok(db_client.post(recover_path, headers=SYSTEM, json=recover), 201)["id"]
    chain = _chain(migrated_engine, unit, ac_id)
    by_id = {str(row.id): row for row in chain}
    assert str(by_id[recovered_id].supersedes_evidence_id) == first_id
    assert _heads(chain) == [uuid.UUID(recovered_id)]
    assert len(chain) == 2  # the worker's evidence is kept, not overwritten

    replay = ok(db_client.post(recover_path, headers=SYSTEM, json=recover), 201)
    assert replay["id"] == recovered_id
    assert _heads(_chain(migrated_engine, unit, ac_id)) == [uuid.UUID(recovered_id)]
    impostor = db_client.post(
        recover_path,
        headers=SYSTEM,
        json={
            **recover,
            "expected_version": version(),
            "stable_ref": "artifact://drill2/something-else",
        },
    )
    assert refused(impostor) == "idempotency_conflict"

    # 3. The dead claim is released and the unit failed, at the same attempt.
    dead = [c for c in claims_of(migrated_engine, unit) if c.attempt == attempt]
    assert dead[0].terminal_reason == "lease_expired"
    failed = unit_row(migrated_engine, unit)
    assert (failed.state, failed.attempt_count) == ("failed", attempt)
    forbidden = db_client.post(
        f"/api/v1/work-units/{unit}/commands/complete",
        headers=WORKER,
        json={"idempotency_key": "drill2-worker-complete", "expected_version": version()},
    )
    assert refused(forbidden) == "invalid_transition"

    # 4. Requeued, the next attempt submits on the recovered evidence without redoing the work.
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/requeue",
            headers=SYSTEM,
            json={
                "idempotency_key": "drill2-requeue",
                "expected_version": version(),
                "reason": "lease lapsed; evidence recovered",
            },
        )
    )
    assert unit_row(migrated_engine, unit).state == "ready"
    second = claim_and_start("drill2-2")
    assert second["attempt"] == attempt + 1
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/pr-binding",
            headers=WORKER,
            json={
                "idempotency_key": "drill2-binding",
                "expected_version": 0,
                "pr_number": 200,
                "head_sha": "a" * 40,
                "attempt": second["attempt"],
                "lease_token": second["lease_token"],
            },
        )
    )
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/commands/submit",
            headers=WORKER,
            json={
                "idempotency_key": "drill2-submit",
                "expected_version": version(),
                "attempt": second["attempt"],
                "lease_token": second["lease_token"],
            },
        )
    )
    assert unit_row(migrated_engine, unit).state == "submitted"
    assert _heads(_chain(migrated_engine, unit, ac_id)) == [uuid.UUID(recovered_id)]

    # The recovered evidence is what the unit is judged on: the verifier hands it to a human, who
    # passes the criterion on it, and the unit completes.
    for name, headers in (("verify", VERIFIER), ("review", VERIFIER)):
        ok(
            db_client.post(
                f"/api/v1/work-units/{unit}/commands/{name}",
                headers=headers,
                json={"idempotency_key": f"drill2-{name}", "expected_version": version()},
            )
        )
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/adjudications",
            headers=HUMAN,
            json={
                "idempotency_key": "drill2-adjudicate",
                "expected_version": version(),
                "work_package_revision_id": born.revision_id,
                "ac_id": ac_id,
                "outcome": "passed",
                "evidence_id": recovered_id,
                "rationale": "drill: the recovered evidence satisfies the criterion",
            },
        )
    )
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/commands/complete",
            headers=HUMAN,
            json={"idempotency_key": "drill2-complete", "expected_version": version()},
        )
    )
    assert unit_row(migrated_engine, unit).state == "completed"
