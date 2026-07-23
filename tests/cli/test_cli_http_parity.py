import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import orchestrator.package_sources as package_sources
from orchestrator.cli import app
from orchestrator.package_sources import VerifiedApproval, load_package_intake_payload
from tests.api.test_context_api import standing_context

HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "system-key"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
AUTHORITY = {
    "capabilities": {"repo.edit": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
}
PACKAGE_FIXTURE = Path("tests/fixtures/intent-packages/ws32-approved-software")


def _verified_approval() -> VerifiedApproval:
    return VerifiedApproval(
        approved_by="devon",
        approved_at="2026-07-05T00:02:00Z",
        approval_event_id="22222222-2222-2222-2222-222222222222",
        approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def decomposition_payload(
    acceptance_criteria: dict[str, str],
    **overrides: object,
) -> dict[str, object]:
    ac_ids = list(acceptance_criteria.values())
    base = {
        "idempotency_key": "cli-http-proposal",
        "expected_version": 0,
        "rationale": "Split by independent delivery path.",
        "proposed_units": [
            {
                "unit_key": "unit-1",
                "title": "Implement service",
                "outcome": "Service persists proposals.",
                "required_capability": "repo.edit",
                "authority": AUTHORITY,
                "max_attempts": 3,
            },
            {
                "unit_key": "unit-2",
                "title": "Implement tests",
                "outcome": "Service is covered by focused tests.",
                "required_capability": "repo.edit",
                "authority": AUTHORITY,
                "max_attempts": 3,
            },
        ],
        "dependencies": [
            {
                "source_unit_key": "unit-2",
                "kind": "work_unit",
                "required_state_or_condition": "completed",
                "target_unit_key": "unit-1",
                "external_ref": None,
            }
        ],
        "ac_mappings": [{"ac_id": ac_ids[0], "unit_key": "unit-1"}],
        "retained_acs": [],
    }
    return {**base, **overrides}


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

    monkeypatch.setattr("orchestrator.cli.HTTP_TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://testserver")


def test_real_http_api_and_cli_have_success_and_error_parity(
    db_client: TestClient,
    in_process_transport: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "cli-http-revision",
            "expected_version": 0,
            "package_id": "cli-http-package",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": "sha256:cli-http",
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
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "cli-http-unit",
            "expected_version": 0,
            "unit_key": "cli-http-unit",
            "title": "Exercise real CLI HTTP",
            "outcome": "CLI and API agree",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    approval = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "cli-http-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert approval.status_code == 200

    body = {"idempotency_key": "cli-http-ready", "expected_version": 1}
    api_success = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready", headers=SYSTEM, json=body
    )
    assert api_success.status_code == 200
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "system-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "system-key")
    cli_success = CliRunner().invoke(
        app,
        [
            "ready",
            unit_id,
            "--idempotency-key",
            "cli-http-ready",
            "--expected-version",
            "1",
            "--json",
        ],
    )
    assert cli_success.exit_code == 0
    assert json.loads(cli_success.stdout) == api_success.json()
    assert {
        key: json.loads(cli_success.stdout)[key] for key in ("state", "version", "event_id")
    } == {key: api_success.json()[key] for key in ("state", "version", "event_id")}

    error_body = {"idempotency_key": "cli-http-stale", "expected_version": 1}
    api_error = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/start", headers=WORKER, json=error_body
    )
    assert api_error.status_code == 409
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "fixture-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "worker-key")
    cli_error = CliRunner().invoke(
        app,
        [
            "start",
            unit_id,
            "--idempotency-key",
            "cli-http-stale",
            "--expected-version",
            "1",
            "--json",
        ],
    )
    assert cli_error.exit_code == 1
    assert {
        key: json.loads(cli_error.stdout)["error"].get(key)
        for key in ("code", "current_version", "recovery")
    } == {
        key: api_error.json()["error"].get(key) for key in ("code", "current_version", "recovery")
    }

    api_history = db_client.get(f"/api/v1/work-units/{unit_id}/history", headers=WORKER)
    assert api_history.status_code == 200
    assert isinstance(api_history.json(), list)
    cli_history = CliRunner().invoke(app, ["history", unit_id, "--json"])
    assert cli_history.exit_code == 0
    assert json.loads(cli_history.stdout) == api_history.json()


def test_real_http_package_intake_and_decomposition_cli_have_parity(
    db_client: TestClient,
    in_process_transport: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")

    intake_body = {
        **load_package_intake_payload(
            PACKAGE_FIXTURE,
            source_repository="AlobarQuest/intent-packages",
        ),
        "idempotency_key": "cli-http-intake",
        "expected_version": 0,
    }
    api_intake = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_body)
    assert api_intake.status_code == 201

    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "human-token")
    monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)
    cli_intake = CliRunner().invoke(
        app,
        [
            "intake-package",
            str(PACKAGE_FIXTURE),
            "--source-repository",
            "AlobarQuest/intent-packages",
            "--idempotency-key",
            "cli-http-intake",
            "--json",
        ],
    )
    assert cli_intake.exit_code == 0
    assert json.loads(cli_intake.stdout) == api_intake.json()

    revision_id = api_intake.json()["id"]
    api_show = db_client.get(f"/api/v1/package-intakes/{revision_id}", headers=HUMAN)
    assert api_show.status_code == 200
    cli_show = CliRunner().invoke(app, ["show-package-intake", revision_id, "--json"])
    assert cli_show.exit_code == 0
    assert json.loads(cli_show.stdout) == api_show.json()

    acceptance_criteria = {
        criterion["ac_id"]: criterion["id"] for criterion in api_show.json()["acceptance_criteria"]
    }
    proposal_body = decomposition_payload(acceptance_criteria)
    api_proposal = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=WORKER,
        json=proposal_body,
    )
    assert api_proposal.status_code == 201

    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "fixture-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "worker-key")
    cli_proposal = CliRunner().invoke(
        app,
        [
            "propose-decomposition",
            revision_id,
            "--data",
            json.dumps(proposal_body),
            "--json",
        ],
    )
    assert cli_proposal.exit_code == 0
    assert json.loads(cli_proposal.stdout) == api_proposal.json()

    api_list = db_client.get(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=WORKER,
    )
    assert api_list.status_code == 200
    cli_list = CliRunner().invoke(app, ["list-decomposition-proposals", revision_id, "--json"])
    assert cli_list.exit_code == 0
    assert json.loads(cli_list.stdout) == api_list.json()

    proposal_id = api_proposal.json()["id"]
    api_detail = db_client.get(f"/api/v1/decomposition-proposals/{proposal_id}", headers=WORKER)
    assert api_detail.status_code == 200
    cli_detail = CliRunner().invoke(app, ["show-decomposition-proposal", proposal_id, "--json"])
    assert cli_detail.exit_code == 0
    assert json.loads(cli_detail.stdout) == api_detail.json()

    decision_body = {
        "idempotency_key": "cli-http-approve",
        "expected_version": 0,
        "reason": "Approved for draft activation.",
    }
    api_approve = db_client.post(
        f"/api/v1/decomposition-proposals/{proposal_id}/approve",
        headers=HUMAN,
        json=decision_body,
    )
    assert api_approve.status_code == 200

    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "human-token")
    monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)
    cli_approve = CliRunner().invoke(
        app,
        [
            "approve-decomposition",
            proposal_id,
            "--idempotency-key",
            "cli-http-approve",
            "--reason",
            "Approved for draft activation.",
            "--json",
        ],
    )
    assert cli_approve.exit_code == 0
    assert json.loads(cli_approve.stdout) == api_approve.json()


def test_real_http_claim_context_cli_matches_api(
    db_client: TestClient,
    in_process_transport: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    revision_body = {
        "idempotency_key": "cli-http-context-revision",
        "expected_version": 0,
        "package_id": "cli-http-context-package",
        "source_repository": "owner/repo",
        "revision": 1,
        "content_hash": "sha256:cli-http-context",
        "source_path": "intent.md",
        "source_commit": "abc123",
        "approved_by": "devon",
        "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        "approval_event_id": str(uuid.uuid4()),
        "enforcement_snapshot": {
            "acceptance_criteria": ["ac-1"],
            "required_context": standing_context(),
        },
        "authority": AUTHORITY,
        "registry_version": 1,
    }
    revision = db_client.post("/api/v1/revisions", headers=HUMAN, json=revision_body)
    assert revision.status_code == 201
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "cli-http-context-unit",
            "expected_version": 0,
            "unit_key": "cli-http-context-unit",
            "title": "Exercise CLI context",
            "outcome": "CLI context works",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    authority = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "cli-http-context-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert authority.status_code == 200
    ready = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready",
        headers=SYSTEM,
        json={"idempotency_key": "cli-http-context-ready", "expected_version": 1},
    )
    assert ready.status_code == 200
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(standing_context()), encoding="utf-8")

    api_claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={
            "idempotency_key": "cli-http-context-claim",
            "expected_version": 2,
            "standing_context": standing_context(),
        },
    )
    assert api_claim.status_code == 200

    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "fixture-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "worker-key")
    cli_claim = CliRunner().invoke(
        app,
        [
            "claim",
            unit_id,
            "--idempotency-key",
            "cli-http-context-claim",
            "--expected-version",
            "2",
            "--context",
            f"@{context_path}",
            "--json",
        ],
    )

    assert cli_claim.exit_code == 0
    cli_body = json.loads(cli_claim.stdout)
    api_body = api_claim.json()
    assert cli_body["lease_token"] == ""
    assert {
        key: cli_body[key] for key in ("claim_id", "attempt", "expires_at", "context_snapshot_id")
    } == {
        key: api_body[key] for key in ("claim_id", "attempt", "expires_at", "context_snapshot_id")
    }
    assert cli_body["context_snapshot_id"]
