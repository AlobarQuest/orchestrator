import hashlib
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import orchestrator.cli as cli_module
import orchestrator.package_sources as package_sources
from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.cli import app
from orchestrator.identity.auth import M2MCredential
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.states import ActorRole
from orchestrator.main import create_app
from orchestrator.package_sources import VerifiedApproval, load_package_intake_payload
from orchestrator.persistence.models import Claim, WorkUnit
from orchestrator.services.claims import LeaseGrant, reclaim_expired_claim
from orchestrator.services.lifecycle import ActorContext

HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "system-key"}
VERIFIER = {"Authorization": "Bearer verifier-token", "X-Credential-Key-Id": "verifier-key"}
PACKAGE_FIXTURE = Path("tests/fixtures/intent-packages/ws32-approved-software")
AUTHORITY = {
    "capabilities": {"repository_write": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
}
pytest_plugins = ("tests.persistence.conftest",)


@pytest.fixture
def auth_config() -> AuthConfig:
    registry = RegistryAdapter(
        {
            "schema": "orchestrator-actor-bundle/v1",
            "source_revision": "0123456789abcdef0123456789abcdef01234567",
            "actors": [
                {
                    "agent_id": "worker",
                    "version": 3,
                    "status": "active",
                    "runtime": "runner",
                    "authority_profile": "agent-queue-v1",
                },
                {
                    "agent_id": "devon",
                    "version": 1,
                    "status": "active",
                    "runtime": "human",
                    "authority_profile": "human-operator-v1",
                },
                {
                    "agent_id": "system",
                    "version": 1,
                    "status": "active",
                    "runtime": "orchestrator",
                    "authority_profile": "system-v1",
                },
                {
                    "agent_id": "verifier",
                    "version": 1,
                    "status": "active",
                    "runtime": "verifier",
                    "authority_profile": "verifier-v1",
                },
            ],
        }
    )
    return AuthConfig(
        registry=registry,
        m2m_credentials={
            "worker-key": M2MCredential(
                agent_id="worker",
                token_hash=hashlib.sha256(b"fixture-token").hexdigest(),
            ),
            "system-key": M2MCredential(
                agent_id="system",
                token_hash=hashlib.sha256(b"system-token").hexdigest(),
            ),
            "verifier-key": M2MCredential(
                agent_id="verifier",
                token_hash=hashlib.sha256(b"verifier-token").hexdigest(),
            ),
        },
        trusted_proxy_ips=frozenset({"testclient"}),
        proxy_marker_header="X-Alobar-Proxy",
        proxy_marker="fixture-marker",
        email_header="X-Alobar-Email",
        email_to_actor={"devon@example.invalid": "devon"},
        m2m_roles={
            "system-key": ActorRole.SYSTEM,
            "verifier-key": ActorRole.VERIFIER,
        },
        csrf_secret=b"test-only-csrf-secret-with-32-bytes",
    )


@pytest.fixture
def db_client(auth_config: AuthConfig, migrated_engine: Engine) -> Iterator[TestClient]:
    app_instance = create_app(auth_config)

    def database_session() -> Iterator[Session]:
        with Session(migrated_engine) as session:
            yield session

    app_instance.dependency_overrides[get_session] = database_session
    with TestClient(
        app_instance,
        base_url="https://testserver",
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


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


def _verified_approval() -> VerifiedApproval:
    return VerifiedApproval(
        approved_by="devon",
        approved_at="2026-07-05T00:02:00Z",
        approval_event_id="22222222-2222-2222-2222-222222222222",
        approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def _standing_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "code_standards_version": "1.0",
        "security_standards_version": "1.0",
        "project_standards_version": "1.0",
        "agent_id": "worker",
        "authority_profile": "agent-queue-v1",
        "runtime_name": "codex",
        "runtime_version": "1.0",
        "skill_bundle_id": "ws-3.3-protocol-smoke",
        "skill_bundle_version": "1",
        "capabilities": ["repository_write"],
    }
    context.update(overrides)
    return context


def _decomposition_payload(acceptance_criteria: dict[str, str]) -> dict[str, object]:
    ac_ids = list(acceptance_criteria.values())
    return {
        "idempotency_key": "ws33-smoke-proposal",
        "expected_version": 0,
        "rationale": "Smoke suite covers one executable public protocol path.",
        "proposed_units": [
            {
                "unit_key": "smoke-unit",
                "title": "Exercise WS-3.3 smoke protocol",
                "outcome": "The public lifecycle path is smoke tested.",
                "required_capability": "repository_write",
                "authority": AUTHORITY,
                "max_attempts": 3,
            }
        ],
        "dependencies": [],
        "ac_mappings": [{"ac_id": ac_ids[0], "unit_key": "smoke-unit"}],
        "retained_acs": [],
    }


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
    lease: dict[str, Any] | LeaseGrant | None = None,
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


def _lease_value(lease: dict[str, Any] | LeaseGrant, key: str) -> Any:
    if isinstance(lease, dict):
        return lease[key]
    return getattr(lease, key)


def _write_context(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / f"context-{uuid.uuid4()}.json"
    path.write_text(json.dumps(_standing_context(**overrides)), encoding="utf-8")
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
    claim: dict[str, Any] | LeaseGrant | None = None,
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
    lease: dict[str, Any] | LeaseGrant,
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
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/adjudications",
        headers=VERIFIER,
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


def _expire_latest_claim(migrated_engine: Engine, unit_id: str) -> None:
    with Session(migrated_engine) as session:
        unit_uuid = uuid.UUID(unit_id)
        claim = session.scalar(
            select(Claim)
            .where(Claim.work_unit_id == unit_uuid)
            .order_by(Claim.attempt.desc())
            .limit(1)
        )
        assert claim is not None
        claim.lease_expires_at = claim.acquired_at
        session.commit()


def _reclaim(
    migrated_engine: Engine,
    unit_id: str,
    context: dict[str, object] | None = None,
) -> LeaseGrant:
    with Session(migrated_engine) as session:
        result = reclaim_expired_claim(
            session,
            uuid.UUID(unit_id),
            ActorContext("system", ActorRole.SYSTEM),
            ActorContext("worker", ActorRole.WORKER),
            "ws33-smoke-reclaim",
            standing_context=context,
        )
    assert isinstance(result, LeaseGrant), result
    return result


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
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")

    package_payload = load_package_intake_payload(
        PACKAGE_FIXTURE,
        source_repository="AlobarQuest/intent-packages",
    )
    intake_body = {
        **package_payload,
        "idempotency_key": "ws33-smoke-intake",
        "expected_version": 0,
        "enforcement_snapshot": {
            **package_payload["enforcement_snapshot"],
            "required_context": _standing_context(),
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
        json=_decomposition_payload({"AC-001": ac_mapping_id}),
    )
    assert proposal.status_code == 201, proposal.json()
    proposal_id = proposal.json()["id"]

    approved = _invoke(
        monkeypatch,
        "human",
        [
            "approve-decomposition",
            proposal_id,
            "--idempotency-key",
            "ws33-smoke-approve-decomposition",
            "--reason",
            "Approved for smoke activation.",
        ],
    )
    unit_id = approved["created_work_unit_ids"]["smoke-unit"]
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        assert unit.work_package_revision_id == uuid.UUID(revision_id)
        assert unit.state == "draft"

    _command(
        monkeypatch,
        "system",
        "ready",
        unit_id,
        expected_version=1,
        idempotency_key="ws33-smoke-ready-1",
    )
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

    revision_unit_id = _approved_decomposition_unit(
        db_client,
        monkeypatch,
        suffix="revision",
        max_attempts=3,
    )
    _command(
        monkeypatch,
        "system",
        "ready",
        revision_unit_id,
        expected_version=1,
        idempotency_key="ws33-smoke-revision-ready",
    )
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

    retry_unit_id = _approved_decomposition_unit(
        db_client,
        monkeypatch,
        suffix="retry",
        max_attempts=1,
    )
    _command(
        monkeypatch,
        "system",
        "ready",
        retry_unit_id,
        expected_version=1,
        idempotency_key="ws33-smoke-retry-ready",
    )
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

    reclaim_unit_id = _approved_decomposition_unit(
        db_client,
        monkeypatch,
        suffix="reclaim",
        max_attempts=3,
    )
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
    assert reclaim_authority.status_code == 200, reclaim_authority.json()
    _command(
        monkeypatch,
        "system",
        "ready",
        reclaim_unit_id,
        expected_version=1,
        idempotency_key="ws33-smoke-reclaim-ready",
    )
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
    _expire_latest_claim(migrated_engine, reclaim_unit_id)
    reclaimed = _reclaim(migrated_engine, reclaim_unit_id, _standing_context())
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


def _approved_decomposition_unit(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
    max_attempts: int,
) -> str:
    package_payload = load_package_intake_payload(
        PACKAGE_FIXTURE,
        source_repository="AlobarQuest/intent-packages",
    )
    intake_body = {
        **package_payload,
        "idempotency_key": f"ws33-smoke-{suffix}-intake",
        "expected_version": 0,
        "package_id": f"ws33-smoke-{suffix}",
        "enforcement_snapshot": {
            **package_payload["enforcement_snapshot"],
            "required_context": _standing_context(),
        },
    }
    intake = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_body)
    assert intake.status_code == 201, intake.json()
    ac_id = intake.json()["acceptance_criteria"][0]["id"]
    proposal = db_client.post(
        f"/api/v1/package-intakes/{intake.json()['id']}/decomposition-proposals",
        headers=WORKER,
        json={
            **_decomposition_payload({"AC-001": ac_id}),
            "idempotency_key": f"ws33-smoke-{suffix}-proposal",
            "proposed_units": [
                {
                    "unit_key": f"ws33-smoke-{suffix}-unit",
                    "title": f"WS-3.3 smoke {suffix}",
                    "outcome": "Auxiliary smoke path is exercised.",
                    "required_capability": "repository_write",
                    "authority": AUTHORITY,
                    "max_attempts": max_attempts,
                }
            ],
            "ac_mappings": [
                {
                    "ac_id": ac_id,
                    "unit_key": f"ws33-smoke-{suffix}-unit",
                }
            ],
        },
    )
    assert proposal.status_code == 201, proposal.json()
    approved = _invoke(
        monkeypatch,
        "human",
        [
            "approve-decomposition",
            proposal.json()["id"],
            "--idempotency-key",
            f"ws33-smoke-{suffix}-approve-decomposition",
            "--reason",
            "Approved auxiliary smoke activation.",
        ],
    )
    return str(approved["created_work_unit_ids"][f"ws33-smoke-{suffix}-unit"])
