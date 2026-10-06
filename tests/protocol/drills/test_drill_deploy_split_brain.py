"""Drill 4: a deploy landed while its post-deploy verification never finished.

The release is live and nothing has confirmed it. Reconciliation must notice and do nothing else:

1. A completed unit is bound to a release artifact and a production deployment is observed,
   which mints a post-deploy verification unit that then stalls in SUBMITTED.
2. A detection pass records one `deploy_split_brain` against the stalled unit.
3. The pass creates no unit, moves no unit, records no dispatch and resolves nothing.
4. A second pass suppresses the duplicate, so the operator sees one alarm.

The stall threshold is shortened through settings rather than by waiting or back-dating a row.
"""

import uuid
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from orchestrator.config import Settings, get_settings
from orchestrator.persistence.models import (
    DeploymentObservation,
    DispatchRecord,
    ReconciliationCondition,
    ReconciliationResolution,
    WorkUnit,
)
from tests._support.protocol import (
    HUMAN,
    SYSTEM,
    VERIFIER,
    WORKER,
    birth_unit,
    make_ready,
    ok,
    rows,
    stand_in_approved_package,
    standing_context,
    unit_row,
)

DIGEST = "sha256:" + "a" * 64
OBSERVED_AT = "2026-07-11T20:00:00+00:00"


def test_a_stalled_post_deploy_verification_is_one_open_alarm(
    db_client: TestClient,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stand_in_approved_package(monkeypatch)
    stalled_at_once = Settings.model_validate({"reconcile_split_brain_stall_seconds": 0})
    cast(FastAPI, db_client.app).dependency_overrides[get_settings] = lambda: stalled_at_once
    born = birth_unit(db_client, suffix="drill4")
    unit = born.unit_id
    make_ready(db_client, migrated_engine, unit, key="drill4")

    def version() -> int:
        return unit_row(migrated_engine, unit).version

    def command(name: str, headers: dict[str, str], **extra: object) -> None:
        ok(
            db_client.post(
                f"/api/v1/work-units/{unit}/commands/{name}",
                headers=headers,
                json={"idempotency_key": f"drill4-{name}", "expected_version": version(), **extra},
            )
        )

    # 1. The unit completes through the public lifecycle.
    lease = ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/claim",
            headers=WORKER,
            json={
                "idempotency_key": "drill4-claim",
                "expected_version": version(),
                "standing_context": standing_context(),
            },
        )
    )
    held = {"attempt": lease["attempt"], "lease_token": lease["lease_token"]}
    command("start", WORKER, standing_context=standing_context(), **held)
    evidence = ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/evidence",
            headers=WORKER,
            json={
                "idempotency_key": "drill4-evidence",
                "expected_version": version(),
                "work_package_revision_id": born.revision_id,
                "ac_id": born.ac_id,
                "evidence_type": "automated_test",
                "stable_ref": "artifact://drill4/tests",
                "source_revision": "abc123",
                **held,
            },
        )
    )
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/pr-binding",
            headers=WORKER,
            json={
                "idempotency_key": "drill4-pr-binding",
                "expected_version": 0,
                "pr_number": 400,
                "head_sha": "b" * 40,
                **held,
            },
        )
    )
    command("submit", WORKER, **held)
    command("verify", VERIFIER)
    command("review", VERIFIER)
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/adjudications",
            headers=HUMAN,
            json={
                "idempotency_key": "drill4-adjudicate",
                "expected_version": version(),
                "work_package_revision_id": born.revision_id,
                "ac_id": born.ac_id,
                "outcome": "passed",
                "evidence_id": evidence["id"],
                "rationale": "drill: evidence accepted",
            },
        )
    )
    command("complete", HUMAN)
    assert unit_row(migrated_engine, unit).state == "completed"

    # ... is released, and the release is observed in production.
    binding = ok(
        db_client.post(
            f"/api/v1/work-units/{unit}/release-artifacts",
            headers=SYSTEM,
            json={
                "idempotency_key": "drill4-release",
                "expected_version": version(),
                "package_revision_id": born.revision_id,
                "package_revision_hash": born.content_hash,
                "source_repository": "AlobarQuest/orchestrator",
                "implementation_pr_number": 20,
                "source_commit": "1" * 40,
                "merge_commit": "2" * 40,
                "artifact_registry": "ghcr.io",
                "artifact_repository": "alobarquest/orchestrator",
                "artifact_name": "orchestrator",
                "artifact_digest": DIGEST,
                "artifact_tag": "drill4",
                "workflow_run_id": "123456789",
            },
        ),
        201,
    )
    ok(
        db_client.post(
            f"/api/v1/release-artifacts/{binding['id']}/deployment-observations",
            headers=SYSTEM,
            json={
                "idempotency_key": "drill4-deploy",
                "expected_version": 0,
                "environment": "production",
                "base_url": "https://sds.example.invalid",
                "observed_artifact_digest": DIGEST,
                "deployment_ref": "release:drill4",
                "deployer": "release-pipeline",
                "deployment_url": "https://release.example.invalid/drill4",
                "observed_at": OBSERVED_AT,
                "probe_summary": {
                    "probes": [
                        {
                            "name": "live",
                            "method": "GET",
                            "endpoint": "/health/live",
                            "status_code": 200,
                            "observed_at": OBSERVED_AT,
                        }
                    ]
                },
                "route_summary": {"routes": [{"path": "/api/v1/observations", "present": True}]},
                "auth_summary": {"missing_m2m_status": 401, "configured_m2m_status": 200},
                "dispatch_summary": {"dispatch_enabled": False},
                "status_summary": {"status": "observed", "summary": "bounded"},
            },
        ),
        201,
    )
    [deployment] = rows(migrated_engine, DeploymentObservation)
    stalled = str(deployment.post_deploy_work_unit_id)
    assert unit_row(migrated_engine, stalled).state == "submitted"
    units_before = len(rows(migrated_engine, WorkUnit))

    # 2. Detection records the split brain against the stalled unit.
    detect = {"expected_version": 0}
    first = ok(
        db_client.post(
            "/api/v1/reconciliation/detect",
            headers=SYSTEM,
            json={**detect, "idempotency_key": "drill4-detect"},
        )
    )
    assert (first["conditions_recorded"], first["skipped_correlations"]) == (1, 0)
    [condition] = rows(migrated_engine, ReconciliationCondition)
    assert condition.condition_type == "deploy_split_brain"
    assert condition.work_unit_id == uuid.UUID(stalled)

    # 3. ... and changes nothing else.
    assert len(rows(migrated_engine, WorkUnit)) == units_before
    assert unit_row(migrated_engine, stalled).state == "submitted"
    assert unit_row(migrated_engine, unit).state == "completed"
    assert rows(migrated_engine, DispatchRecord) == []
    assert rows(migrated_engine, ReconciliationResolution) == []

    # 4. A second pass suppresses the duplicate.
    again = ok(
        db_client.post(
            "/api/v1/reconciliation/detect",
            headers=SYSTEM,
            json={**detect, "idempotency_key": "drill4-detect-2"},
        )
    )
    assert (again["conditions_recorded"], again["suppressed_duplicates"]) == (0, 1)
    assert len(rows(migrated_engine, ReconciliationCondition)) == 1
