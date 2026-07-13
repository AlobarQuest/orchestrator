import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.main import app
from orchestrator.persistence.models import WorkUnit


def test_api_is_versioned() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/api/v1/work-units/{unit_id}/readiness" in paths
    assert "/api/v1/work-units/{unit_id}/commands/{command}" in paths


def test_creation_routes_declare_201_and_response_schemas() -> None:
    document = TestClient(app).get("/openapi.json").json()
    paths = document["paths"]

    revision = paths["/api/v1/revisions"]["post"]
    unit = paths["/api/v1/revisions/{revision_id}/work-units"]["post"]

    assert set(revision["responses"]) >= {"201", "401", "403"}
    assert set(unit["responses"]) >= {"201", "401", "403"}
    assert revision["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
    assert unit["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]


def test_every_api_success_response_has_an_explicit_schema() -> None:
    document = TestClient(app).get("/openapi.json").json()

    for path, operations in document["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for operation in operations.values():
            success = next(
                response
                for status, response in operation["responses"].items()
                if status.startswith("2")
            )
            assert success["content"]["application/json"]["schema"]


def test_every_api_mutation_requires_idempotency_key_and_expected_version() -> None:
    document = TestClient(app).get("/openapi.json").json()

    for path, operations in document["paths"].items():
        if not path.startswith("/api/v1") or "post" not in operations:
            continue
        schema = operations["post"]["requestBody"]["content"]["application/json"]["schema"]
        name = schema["$ref"].rsplit("/", 1)[-1]
        required = set(document["components"]["schemas"][name]["required"])
        assert {"idempotency_key", "expected_version"} <= required, path


HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "system-key"}
VERIFIER = {
    "Authorization": "Bearer verifier-token",
    "X-Credential-Key-Id": "verifier-key",
}
AUTHORITY = {
    "capabilities": {"repository_write": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
}


def test_full_lifecycle_api_contract(db_client: TestClient, migrated_engine: Engine) -> None:
    revision_body = {
        "idempotency_key": "revision-1",
        "expected_version": 0,
        "package_id": "pkg-api",
        "source_repository": "owner/repo",
        "revision": 1,
        "content_hash": "sha256:api",
        "source_path": "intent.md",
        "source_commit": "abc123",
        "approved_by": "devon",
        "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        "approval_event_id": str(uuid.uuid4()),
        "enforcement_snapshot": {"acceptance_criteria": ["ac-1"]},
        "authority": AUTHORITY,
        "registry_version": 1,
    }
    first_revision = db_client.post("/api/v1/revisions", headers=HUMAN, json=revision_body)
    replay_revision = db_client.post("/api/v1/revisions", headers=HUMAN, json=revision_body)
    assert first_revision.status_code == replay_revision.status_code == 201
    assert first_revision.json() == replay_revision.json()
    conflicting_revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={**revision_body, "content_hash": "sha256:different"},
    )
    assert conflicting_revision.status_code == 409
    assert conflicting_revision.json()["error"]["code"] == "idempotency_conflict"
    revision_id = first_revision.json()["id"]

    unit_body = {
        "idempotency_key": "unit-1",
        "expected_version": 0,
        "unit_key": "unit-1",
        "title": "Exercise API lifecycle",
        "outcome": "Lifecycle works",
        "required_capability": "repository_write",
        "authority": AUTHORITY,
        "max_attempts": 3,
        "approved_by": "devon",
        "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
    }
    first_unit = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units", headers=HUMAN, json=unit_body
    )
    replay_unit = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units", headers=HUMAN, json=unit_body
    )
    assert first_unit.status_code == replay_unit.status_code == 201
    assert first_unit.json() == replay_unit.json()
    conflicting_unit = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json={**unit_body, "title": "Different title"},
    )
    assert conflicting_unit.status_code == 409
    assert conflicting_unit.json()["error"]["code"] == "idempotency_conflict"
    unit_id = first_unit.json()["id"]

    worker_approval = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=WORKER,
        json={
            "idempotency_key": "worker-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "worker may not approve",
        },
    )
    assert worker_approval.status_code == 403

    dependency = db_client.post(
        f"/api/v1/work-units/{unit_id}/dependencies",
        headers=HUMAN,
        json={
            "idempotency_key": "dependency-1",
            "expected_version": 1,
            "kind": "external_system",
            "required_state_or_condition": "passed",
            "external_ref": "ci/build",
        },
    )
    assert dependency.status_code == 200
    resolved = db_client.post(
        f"/api/v1/dependencies/{dependency.json()['id']}/resolve",
        headers=SYSTEM,
        json={
            "idempotency_key": "dependency-resolve-1",
            "expected_version": 1,
            "status": "satisfied",
            "detail": {"run": 42},
        },
    )
    assert resolved.status_code == 200

    authority_approval = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "authority-1",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved package authority",
        },
    )
    assert authority_approval.status_code == 200

    readiness = db_client.get(f"/api/v1/work-units/{unit_id}/readiness", headers=HUMAN)
    assert readiness.status_code == 200
    assert readiness.json() == {"status": "ready", "reasons": []}

    def command(
        name: str,
        version: int,
        key: str,
        headers: dict[str, str],
        lease_proof: dict[str, object] | None = None,
    ):
        body = {"idempotency_key": key, "expected_version": version}
        if lease_proof is not None:
            body.update(
                attempt=lease_proof["attempt"],
                lease_token=lease_proof["lease_token"],
            )
        return db_client.post(
            f"/api/v1/work-units/{unit_id}/commands/{name}",
            headers=headers,
            json=body,
        )

    assert command("ready", 1, "ready-1", SYSTEM).json()["version"] == 2
    stale = command("start", 1, "stale-1", WORKER)
    assert stale.status_code == 409
    assert stale.json()["error"]["current_state"] == "ready"
    assert stale.json()["error"]["current_version"] == 2

    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "claim-1", "expected_version": 2},
    )
    assert claim.status_code == 200
    lease = claim.json()
    renew_body = {
        "idempotency_key": "renew-1",
        "expected_version": 3,
        "attempt": lease["attempt"],
        "lease_token": lease["lease_token"],
    }
    renewed = db_client.post(f"/api/v1/work-units/{unit_id}/renew", headers=WORKER, json=renew_body)
    replayed_renewal = db_client.post(
        f"/api/v1/work-units/{unit_id}/renew", headers=WORKER, json=renew_body
    )
    assert renewed.status_code == replayed_renewal.status_code == 200
    assert renewed.json()["expires_at"] == replayed_renewal.json()["expires_at"]

    missing_lease = command("start", 3, "start-missing-lease", WORKER)
    assert missing_lease.status_code == 409
    assert missing_lease.json()["error"]["code"] == "active_claim_required"
    wrong_lease = command(
        "start",
        3,
        "start-wrong-lease",
        WORKER,
        {**lease, "lease_token": "wrong-token"},
    )
    assert wrong_lease.status_code == 409
    assert wrong_lease.json()["error"]["code"] == "active_claim_required"
    assert command("start", 3, "start-1", WORKER, lease).json()["version"] == 4
    assert command("block", 4, "block-1", WORKER, lease).json()["version"] == 5
    assert command("ready", 5, "ready-2", SYSTEM).json()["version"] == 6
    second_claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "claim-2", "expected_version": 6},
    ).json()
    assert command("start", 7, "start-2", WORKER, second_claim).json()["version"] == 8
    assert (
        command("request-approval", 8, "request-approval-1", WORKER, second_claim).json()["version"]
        == 9
    )
    action_approval = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "action-approval-1",
            "expected_version": 9,
            "subject_type": "action",
            "reason": "approved requested action",
        },
    )
    assert action_approval.status_code == 200
    assert command("approve", 9, "approve-1", HUMAN).json()["version"] == 10
    final_claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "claim-3", "expected_version": 10},
    ).json()
    assert command("start", 11, "start-3", WORKER, final_claim).json()["version"] == 12
    invalid = command("complete", 12, "invalid-complete", WORKER, final_claim)
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "invalid_transition"

    evidence = db_client.post(
        f"/api/v1/work-units/{unit_id}/evidence",
        headers=WORKER,
        json={
            "idempotency_key": "evidence-1",
            "expected_version": 12,
            "work_package_revision_id": revision_id,
            "ac_id": "ac-1",
            "attempt": final_claim["attempt"],
            "lease_token": final_claim["lease_token"],
            "evidence_type": "test",
            "stable_ref": "artifact://run/1",
            "source_revision": "abc123",
        },
    )
    assert evidence.status_code == 200
    worker_adjudication = db_client.post(
        f"/api/v1/work-units/{unit_id}/adjudications",
        headers=WORKER,
        json={
            "idempotency_key": "worker-adjudication",
            "expected_version": 12,
            "work_package_revision_id": revision_id,
            "ac_id": "ac-1",
            "outcome": "passed",
            "evidence_id": evidence.json()["id"],
            "rationale": "worker may not adjudicate",
        },
    )
    assert worker_adjudication.status_code == 403
    assert second_claim["attempt"] == 2
    assert command("submit", 12, "submit-1", WORKER, final_claim).json()["version"] == 13
    assert command("verify", 13, "verify-1", VERIFIER).json()["version"] == 14
    assert command("review", 14, "review-1", VERIFIER).json()["version"] == 15

    forbidden = command("complete", 15, "worker-complete", WORKER, final_claim)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "role_forbidden"

    adjudication = db_client.post(
        f"/api/v1/work-units/{unit_id}/adjudications",
        headers=VERIFIER,
        json={
            "idempotency_key": "adjudication-1",
            "expected_version": 15,
            "work_package_revision_id": revision_id,
            "ac_id": "ac-1",
            "outcome": "passed",
            "evidence_id": evidence.json()["id"],
            "rationale": "verified",
        },
    )
    assert adjudication.status_code == 200

    assert command("complete", 15, "complete-1", HUMAN).json()["state"] == "completed"
    assert len(db_client.get(f"/api/v1/work-units/{unit_id}/evidence", headers=HUMAN).json()) == 1
    history = db_client.get(f"/api/v1/work-units/{unit_id}/history", headers=HUMAN)
    assert history.status_code == 200
    assert len(history.json()) >= 7

    recovery_body = {
        **unit_body,
        "idempotency_key": "unit-recovery",
        "unit_key": "unit-recovery",
        "title": "Exercise recovery API",
        "max_attempts": 1,
    }
    recovery = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units", headers=HUMAN, json=recovery_body
    ).json()
    recovery_id = recovery["id"]
    assert (
        db_client.post(
            f"/api/v1/work-units/{recovery_id}/approvals",
            headers=HUMAN,
            json={
                "idempotency_key": "authority-recovery",
                "expected_version": 1,
                "subject_type": "authority",
                "reason": "approved package authority",
            },
        ).status_code
        == 200
    )

    def recovery_command(name: str, version: int, key: str, headers: dict[str, str]):
        return db_client.post(
            f"/api/v1/work-units/{recovery_id}/commands/{name}",
            headers=headers,
            json={"idempotency_key": key, "expected_version": version},
        )

    assert recovery_command("ready", 1, "recovery-ready", SYSTEM).status_code == 200
    recovery_lease = db_client.post(
        f"/api/v1/work-units/{recovery_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "recovery-claim-1", "expected_version": 2},
    )
    assert recovery_lease.status_code == 200
    lease_body = recovery_lease.json()
    failure = db_client.post(
        f"/api/v1/work-units/{recovery_id}/commands/fail",
        headers=WORKER,
        json={
            "idempotency_key": "recovery-fail",
            "expected_version": 3,
            "attempt": lease_body["attempt"],
            "lease_token": lease_body["lease_token"],
        },
    )
    assert failure.json()["version"] == 4
    retry = db_client.post(
        f"/api/v1/work-units/{recovery_id}/retry-authorization",
        headers=HUMAN,
        json={
            "idempotency_key": "recovery-retry",
            "expected_version": 4,
            "new_max_attempts": 2,
            "reason": "approved one additional attempt",
        },
    )
    assert retry.status_code == 200
    assert (
        db_client.post(
            f"/api/v1/work-units/{recovery_id}/claim",
            headers=WORKER,
            json={"idempotency_key": "recovery-claim-2", "expected_version": 5},
        ).status_code
        == 200
    )
    assert recovery_command("cancel", 6, "recovery-cancel", HUMAN).json()["state"] == "cancelled"

    with Session(migrated_engine) as session:
        assert session.scalar(select(WorkUnit).where(WorkUnit.id == uuid.UUID(unit_id))) is not None


def test_missing_unit_reads_return_404(db_client: TestClient) -> None:
    missing = uuid.uuid4()

    for suffix in ("evidence", "history"):
        response = db_client.get(f"/api/v1/work-units/{missing}/{suffix}", headers=HUMAN)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "work_unit_not_found"


def test_work_unit_registration_idempotency_conflicts_on_raw_authority_change(
    db_client: TestClient,
) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "revision-raw-authority",
            "expected_version": 0,
            "package_id": "pkg-raw-authority",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": "sha256:raw-authority",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {"acceptance_criteria": ["ac-1"]},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    revision_id = revision.json()["id"]

    raw_authority = {
        "capabilities": {"repository_write": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "owner/repo-a",
            "allowed_commands": ["make check"],
        },
    }
    conflicting_raw_authority = {
        "capabilities": {"repository_write": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "owner/repo-b",
            "allowed_commands": ["make check"],
        },
    }
    unit_body = {
        "idempotency_key": "unit-raw-authority",
        "expected_version": 0,
        "unit_key": "unit-raw-authority",
        "title": "Raw authority unit",
        "outcome": "Registration identity includes raw authority.",
        "required_capability": "repository_write",
        "authority": raw_authority,
        "max_attempts": 3,
        "approved_by": "devon",
        "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
    }

    first = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json=unit_body,
    )
    replay = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json=unit_body,
    )
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()

    conflicting = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json={**unit_body, "authority": conflicting_raw_authority},
    )

    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "idempotency_conflict"
