import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import orchestrator.cli as cli_module
import orchestrator.package_sources as package_sources
from orchestrator.cli import app
from orchestrator.package_sources import load_package_intake_payload
from orchestrator.persistence.models import WorkUnit
from tests._support.protocol import (
    HUMAN,
    PACKAGE_FIXTURE,
    WORKER,
    birth_unit,
    decomposition_payload,
    expire_latest_claim,
    standing_context,
    verified,
)
from tests._support.review_forms import decide_decomposition


@pytest.fixture
def in_process_transport(monkeypatch: pytest.MonkeyPatch, db_client: TestClient) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        headers = dict(request.headers)
        if headers.get("authorization") == "Bearer human-token":
            headers.pop("authorization", None)
            headers.pop("x-credential-key-id", None)
            headers.update(HUMAN)
        response = db_client.request(
            request.method,
            request.url.path,
            headers=headers,
            content=request.content,
        )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    monkeypatch.setattr(cli_module, "HTTP_TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://testserver")


def _set_token(monkeypatch: pytest.MonkeyPatch, actor: str) -> None:
    tokens = {
        "human": ("human-token", None),
        "worker": ("fixture-token", "worker-key"),
        "system": ("system-token", "system-key"),
        "verifier": ("verifier-token", "verifier-key"),
    }
    token, credential_key_id = tokens[actor]
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", token)
    if credential_key_id is None:
        monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)
    else:
        monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", credential_key_id)


def _invoke(monkeypatch: pytest.MonkeyPatch, actor: str, args: list[str]) -> Any:
    _set_token(monkeypatch, actor)
    result = CliRunner().invoke(app, [*args, "--json"])
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def _invoke_error(monkeypatch: pytest.MonkeyPatch, actor: str, args: list[str]) -> dict[str, Any]:
    _set_token(monkeypatch, actor)
    result = CliRunner().invoke(app, [*args, "--json"])
    assert result.exit_code == 1
    return json.loads(result.stdout)["error"]


def _command(
    monkeypatch: pytest.MonkeyPatch,
    actor: str,
    name: str,
    unit_id: str,
    *,
    expected_version: int,
    idempotency_key: str,
    lease: dict[str, Any] | None = None,
    context_path: Path | None = None,
) -> dict[str, Any]:
    args = [
        name,
        unit_id,
        "--idempotency-key",
        idempotency_key,
        "--expected-version",
        str(expected_version),
    ]
    if lease is not None:
        args.extend(["--attempt", str(_lease_value(lease, "attempt"))])
        args.extend(["--lease-token", str(_lease_value(lease, "lease_token"))])
    if context_path is not None:
        args.extend(["--context", f"@{context_path}"])
    return _invoke(monkeypatch, actor, args)


def _lease_value(lease: dict[str, Any], key: str) -> Any:
    return lease[key]


def _write_context(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / f"context-{uuid.uuid4()}.json"
    path.write_text(json.dumps(standing_context(**overrides)), encoding="utf-8")
    return path


def _ledger_row(
    db_client: TestClient,
    unit_id: str,
    *,
    state: str | None = None,
    include_inactive: bool = True,
) -> dict[str, Any]:
    params: dict[str, str] = {"work_unit_id": unit_id}
    if state is not None:
        params["state"] = state
    if include_inactive:
        params["include_inactive"] = "true"
    response = db_client.get("/api/v1/status-ledger", headers=HUMAN, params=params)
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    return rows[0]


def _assert_state(
    db_client: TestClient,
    unit_id: str,
    state: str,
    *,
    claim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = _ledger_row(db_client, unit_id, state=state)
    assert row["unit_state"] == state
    if claim is not None:
        assert row["claim_attempt"] == _lease_value(claim, "attempt")
        assert row["claim_id"] == str(_lease_value(claim, "claim_id"))
    return row


def _append_evidence(
    db_client: TestClient,
    revision_id: str,
    unit_id: str,
    ac_id: str,
    lease: dict[str, Any],
) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/evidence",
        headers=WORKER,
        json={
            "idempotency_key": "ws33-smoke-evidence",
            "expected_version": 12,
            "work_package_revision_id": revision_id,
            "ac_id": ac_id,
            "attempt": _lease_value(lease, "attempt"),
            "lease_token": _lease_value(lease, "lease_token"),
            "evidence_type": "automated_test",
            "stable_ref": "artifact://ws33-smoke/focused-test",
            "source_revision": "deadbeef",
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()


def _adjudicate(
    db_client: TestClient,
    revision_id: str,
    unit_id: str,
    ac_id: str,
    evidence_id: str,
) -> None:
    # The HUMAN gate, not the verifier's credential (WS-P2.32). `AC-001` declares
    # `automated_test`, whose floor is deterministic-permitted, and the smoke evidence carries no
    # machine-readable payload -- so the verifier defers and hands the unit to `awaiting_review`,
    # which is exactly clause (b) of `human_may_adjudicate`. The old form used the verifier bearer
    # to type a rationale, which is now refused: a verifier decides only what it has evaluated.
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/adjudications",
        headers=HUMAN,
        json={
            "idempotency_key": "ws33-smoke-adjudication",
            "expected_version": 15,
            "work_package_revision_id": revision_id,
            "ac_id": ac_id,
            "outcome": "passed",
            "evidence_id": evidence_id,
            "rationale": "Smoke evidence satisfies acceptance.",
        },
    )
    assert response.status_code == 200, response.json()


def _reclaim(
    monkeypatch: pytest.MonkeyPatch,
    unit_id: str,
    context_path: Path,
) -> dict[str, Any]:
    return _invoke(
        monkeypatch,
        "system",
        [
            "reclaim-expired-claim",
            unit_id,
            "--idempotency-key",
            "ws33-smoke-reclaim",
            "--expected-version",
            "3",
            "--next-owner-id",
            "worker",
            "--context",
            f"@{context_path}",
        ],
    )


def _approve_authority(db_client: TestClient, unit_id: str, *, key: str) -> None:
    """Record the human authority approval, which readies a born unit (SDS 1.1 item 2d-1)."""
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": key,
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "Authority envelope approved for the smoke.",
        },
    )
    assert response.status_code == 200, response.json()
    _assert_state(db_client, unit_id, "ready")


def test_ws33_end_to_end_protocol_smoke_suite(
    db_client: TestClient,
    in_process_transport: None,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: verified(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")

    package_payload = load_package_intake_payload(
        PACKAGE_FIXTURE,
        source_repository="AlobarQuest/intent-packages",
    )
    enforcement_snapshot = package_payload["enforcement_snapshot"]
    assert isinstance(enforcement_snapshot, dict)
    intake_body = {
        **package_payload,
        "idempotency_key": "ws33-smoke-intake",
        "expected_version": 0,
        "enforcement_snapshot": {
            **enforcement_snapshot,
            "required_context": standing_context(),
        },
    }
    intake = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_body)
    assert intake.status_code == 201, intake.json()
    revision_id = intake.json()["id"]
    ac_mapping_id = intake.json()["acceptance_criteria"][0]["id"]
    evidence_ac_id = intake.json()["acceptance_criteria"][0]["ac_id"]

    proposal = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=WORKER,
        json=decomposition_payload({"AC-001": ac_mapping_id}),
    )
    assert proposal.status_code == 201, proposal.json()
    proposal_id = proposal.json()["id"]

    # A breakdown is approved in /review (Tier 3 item 24b deleted the /api route and its CLI).
    approved = decide_decomposition(
        db_client, proposal_id, "approve", "Approved for smoke activation.", headers=HUMAN
    )
    unit_id = approved["created_work_unit_ids"]["smoke-unit"]
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        assert unit.work_package_revision_id == uuid.UUID(revision_id)
        assert unit.state == "draft"

    # The human authority approval makes readiness hold, and the orchestrator readies the unit
    # itself (SDS 1.1 item 2d-1); there is no separate `ready` command to send.
    authority = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "ws33-smoke-authority-1",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "Authority envelope approved for the smoke.",
        },
    )
    assert authority.status_code == 200, authority.json()
    _assert_state(db_client, unit_id, "ready")

    claim_context = _write_context(tmp_path)
    claim = _invoke(
        monkeypatch,
        "worker",
        [
            "claim",
            unit_id,
            "--idempotency-key",
            "ws33-smoke-claim-1",
            "--expected-version",
            "2",
            "--context",
            f"@{claim_context}",
        ],
    )
    assert claim["context_snapshot_id"]
    claimed_row = _assert_state(db_client, unit_id, "claimed", claim=claim)
    assert claimed_row["context_decision"] == "accepted"

    renewed = _invoke(
        monkeypatch,
        "worker",
        [
            "renew",
            unit_id,
            "--data",
            json.dumps(
                {
                    "idempotency_key": "ws33-smoke-renew-1",
                    "expected_version": 3,
                    "attempt": claim["attempt"],
                    "lease_token": claim["lease_token"],
                }
            ),
        ],
    )
    assert renewed["expires_at"]
    assert renewed["lease_token"] == ""

    execute_context = _write_context(tmp_path, runtime_version="1.1")
    _command(
        monkeypatch,
        "worker",
        "start",
        unit_id,
        expected_version=3,
        idempotency_key="ws33-smoke-start-1",
        lease=claim,
        context_path=execute_context,
    )
    executing_row = _assert_state(db_client, unit_id, "executing", claim=claim)
    execution_context_id = executing_row["context_snapshot_id"]
    assert execution_context_id and execution_context_id != claim["context_snapshot_id"]

    _command(
        monkeypatch,
        "worker",
        "block",
        unit_id,
        expected_version=4,
        idempotency_key="ws33-smoke-block-1",
        lease=claim,
    )
    _assert_state(db_client, unit_id, "blocked", claim=claim)
    _command(
        monkeypatch,
        "system",
        "ready",
        unit_id,
        expected_version=5,
        idempotency_key="ws33-smoke-ready-2",
    )
    _assert_state(db_client, unit_id, "ready", claim=claim)

    approval_claim = _invoke(
        monkeypatch,
        "worker",
        [
            "claim",
            unit_id,
            "--idempotency-key",
            "ws33-smoke-claim-2",
            "--expected-version",
            "6",
            "--context",
            f"@{claim_context}",
        ],
    )
    _command(
        monkeypatch,
        "worker",
        "start",
        unit_id,
        expected_version=7,
        idempotency_key="ws33-smoke-start-2",
        lease=approval_claim,
        context_path=execute_context,
    )
    _command(
        monkeypatch,
        "worker",
        "request-approval",
        unit_id,
        expected_version=8,
        idempotency_key="ws33-smoke-request-approval",
        lease=approval_claim,
    )
    awaiting_row = _assert_state(db_client, unit_id, "awaiting_approval", claim=approval_claim)
    assert awaiting_row["pending_human_approvals"]
    approval = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "ws33-smoke-action-approval",
            "expected_version": 9,
            "subject_type": "action",
            "reason": "Approved requested smoke action.",
        },
    )
    assert approval.status_code == 200, approval.json()
    _command(
        monkeypatch,
        "human",
        "approve",
        unit_id,
        expected_version=9,
        idempotency_key="ws33-smoke-approve-action",
    )
    _assert_state(db_client, unit_id, "ready", claim=approval_claim)

    final_claim = _invoke(
        monkeypatch,
        "worker",
        [
            "claim",
            unit_id,
            "--idempotency-key",
            "ws33-smoke-claim-3",
            "--expected-version",
            "10",
            "--context",
            f"@{claim_context}",
        ],
    )
    _command(
        monkeypatch,
        "worker",
        "start",
        unit_id,
        expected_version=11,
        idempotency_key="ws33-smoke-start-3",
        lease=final_claim,
        context_path=execute_context,
    )
    evidence = _append_evidence(db_client, revision_id, unit_id, evidence_ac_id, final_claim)
    evidence_row = _ledger_row(db_client, unit_id)
    assert evidence_row["latest_evidence"]["id"] == evidence["id"]
    final_execution_context_id = evidence_row["context_snapshot_id"]
    assert final_execution_context_id and final_execution_context_id != execution_context_id
    assert evidence_row["latest_evidence"]["context_snapshot_id"] == final_execution_context_id

    worker_complete_error = _invoke_error(
        monkeypatch,
        "worker",
        [
            "complete",
            unit_id,
            "--idempotency-key",
            "ws33-smoke-worker-complete",
            "--expected-version",
            "12",
            "--attempt",
            str(final_claim["attempt"]),
            "--lease-token",
            final_claim["lease_token"],
        ],
    )
    assert worker_complete_error["code"] == "invalid_transition"

    _command(
        monkeypatch,
        "worker",
        "submit",
        unit_id,
        expected_version=12,
        idempotency_key="ws33-smoke-submit",
        lease=final_claim,
    )
    _assert_state(db_client, unit_id, "submitted", claim=final_claim)
    _command(
        monkeypatch,
        "verifier",
        "verify",
        unit_id,
        expected_version=13,
        idempotency_key="ws33-smoke-verify",
    )
    _assert_state(db_client, unit_id, "verifying", claim=final_claim)
    _command(
        monkeypatch,
        "verifier",
        "review",
        unit_id,
        expected_version=14,
        idempotency_key="ws33-smoke-review",
    )
    _assert_state(db_client, unit_id, "awaiting_review", claim=final_claim)
    _adjudicate(db_client, revision_id, unit_id, evidence_ac_id, evidence["id"])
    _command(
        monkeypatch,
        "human",
        "complete",
        unit_id,
        expected_version=15,
        idempotency_key="ws33-smoke-complete",
    )
    completed_row = _ledger_row(db_client, unit_id, include_inactive=True)
    assert completed_row["unit_state"] == "completed"
    assert completed_row["latest_adjudication"]["outcome"] == "passed"

    revision_unit_id = birth_unit(db_client, suffix="revision", max_attempts=3).unit_id
    _approve_authority(db_client, revision_unit_id, key="ws33-smoke-revision-ready")
    revision_claim = _invoke(
        monkeypatch,
        "worker",
        [
            "claim",
            revision_unit_id,
            "--idempotency-key",
            "ws33-smoke-revision-claim",
            "--expected-version",
            "2",
            "--context",
            f"@{claim_context}",
        ],
    )
    _command(
        monkeypatch,
        "worker",
        "start",
        revision_unit_id,
        expected_version=3,
        idempotency_key="ws33-smoke-revision-start",
        lease=revision_claim,
        context_path=execute_context,
    )
    _command(
        monkeypatch,
        "worker",
        "submit",
        revision_unit_id,
        expected_version=4,
        idempotency_key="ws33-smoke-revision-submit",
        lease=revision_claim,
    )
    _command(
        monkeypatch,
        "verifier",
        "revision-required",
        revision_unit_id,
        expected_version=5,
        idempotency_key="ws33-smoke-revision-required",
    )
    _assert_state(db_client, revision_unit_id, "revision_required")
    _command(
        monkeypatch,
        "system",
        "ready",
        revision_unit_id,
        expected_version=6,
        idempotency_key="ws33-smoke-revision-ready-again",
    )
    _assert_state(db_client, revision_unit_id, "ready")

    retry_unit_id = birth_unit(db_client, suffix="retry", max_attempts=1).unit_id
    _approve_authority(db_client, retry_unit_id, key="ws33-smoke-retry-ready")
    retry_claim = _invoke(
        monkeypatch,
        "worker",
        [
            "claim",
            retry_unit_id,
            "--idempotency-key",
            "ws33-smoke-retry-claim",
            "--expected-version",
            "2",
            "--context",
            f"@{claim_context}",
        ],
    )
    _command(
        monkeypatch,
        "worker",
        "fail",
        retry_unit_id,
        expected_version=3,
        idempotency_key="ws33-smoke-fail",
        lease=retry_claim,
    )
    failed_row = _assert_state(db_client, retry_unit_id, "failed", claim=retry_claim)
    assert failed_row["last_failure"]
    _invoke(
        monkeypatch,
        "human",
        [
            "authorize-retry",
            retry_unit_id,
            "--data",
            json.dumps(
                {
                    "idempotency_key": "ws33-smoke-authorize-retry",
                    "expected_version": 4,
                    "new_max_attempts": 2,
                    "reason": "Approve one more smoke attempt.",
                }
            ),
        ],
    )
    _assert_state(db_client, retry_unit_id, "ready", claim=retry_claim)

    reclaim_unit_id = birth_unit(db_client, suffix="reclaim", max_attempts=3).unit_id
    reclaim_authority = db_client.post(
        f"/api/v1/work-units/{reclaim_unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "ws33-smoke-reclaim-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "Approved authority for reclaim smoke.",
        },
    )
    assert reclaim_authority.status_code == 200, reclaim_authority.json()  # and readies the unit
    stale_claim = _invoke(
        monkeypatch,
        "worker",
        [
            "claim",
            reclaim_unit_id,
            "--idempotency-key",
            "ws33-smoke-reclaim-claim-1",
            "--expected-version",
            "2",
            "--context",
            f"@{claim_context}",
        ],
    )
    expire_latest_claim(migrated_engine, reclaim_unit_id)
    reclaimed = _reclaim(monkeypatch, reclaim_unit_id, claim_context)
    reclaimed_row = _assert_state(db_client, reclaim_unit_id, "claimed", claim=reclaimed)
    assert reclaimed_row["last_failure"]["reason"] == "lease_expired"
    stale_error = _invoke_error(
        monkeypatch,
        "worker",
        [
            "start",
            reclaim_unit_id,
            "--idempotency-key",
            "ws33-smoke-stale-start",
            "--expected-version",
            "6",
            "--attempt",
            str(stale_claim["attempt"]),
            "--lease-token",
            stale_claim["lease_token"],
        ],
    )
    assert stale_error["code"] == "active_claim_required"

    history = db_client.get(f"/api/v1/work-units/{unit_id}/history", headers=HUMAN)
    assert history.status_code == 200
    assert [
        (event["from_state"], event["to_state"])
        for event in history.json()
        if event["action"] == "work_unit.transitioned"
    ] == [
        ("draft", "ready"),
        ("ready", "claimed"),
        ("claimed", "executing"),
        ("executing", "blocked"),
        ("blocked", "ready"),
        ("ready", "claimed"),
        ("claimed", "executing"),
        ("executing", "awaiting_approval"),
        ("awaiting_approval", "ready"),
        ("ready", "claimed"),
        ("claimed", "executing"),
        ("executing", "submitted"),
        ("submitted", "verifying"),
        ("verifying", "awaiting_review"),
        ("awaiting_review", "completed"),
    ]
