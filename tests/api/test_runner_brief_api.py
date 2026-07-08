import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

import orchestrator.services.runner_brief as runner_brief_service
from tests.api.test_decomposition_api import (
    HUMAN as DECOMPOSITION_HUMAN,
)
from tests.api.test_decomposition_api import (
    acceptance_criteria_by_key,
    decision_payload,
    proposal_payload,
    register_intake,
)

HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
AUTHORITY = {
    "capabilities": {
        "repo.read": "allowed",
        "repo.edit": "allowed",
        "command.run": "allowed",
        "github.pr.create": "allowed",
        "orchestrator.claim": "allowed",
        "orchestrator.evidence.write": "allowed",
    },
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
    "constraints": {
        "work_unit_id": "unit-1",
        "target_repository": "AlobarQuest/orchestrator",
        "allowed_commands": ["make check"],
    },
}


def test_runner_brief_requires_m2m_or_human_auth(db_client: TestClient) -> None:
    response = db_client.get(f"/api/v1/work-units/{uuid.uuid4()}/runner-brief")

    assert response.status_code == 401


def test_runner_brief_returns_canonical_unit_facts(db_client: TestClient) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "runner-brief-revision",
            "expected_version": 0,
            "package_id": "ws-4.1-pilot",
            "source_repository": "AlobarQuest/intent-packages",
            "revision": 1,
            "content_hash": "sha256:runner-brief",
            "source_path": "packages/ws-4.1/package.yaml",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
            "approval_event_id": "evt-runner-brief",
            "enforcement_snapshot": {"required_context": {"capabilities": ["repository_write"]}},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    revision_id = revision.json()["id"]

    unit = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "runner-brief-unit",
            "expected_version": 0,
            "unit_key": "pilot",
            "title": "Pilot factory runner",
            "outcome": "Open a PR with evidence",
            "required_capability": "repository_write",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]

    response = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER)

    assert response.status_code == 200
    body = response.json()
    assert body["work_unit"]["id"] == unit_id
    assert body["work_unit"]["state"] == "draft"
    assert body["package"]["id"] == "ws-4.1-pilot"
    assert body["target"]["repository"] == "AlobarQuest/orchestrator"
    assert body["authority"]["envelope"] == AUTHORITY
    assert "token" not in str(body).lower()


def test_runner_brief_scopes_ac_mapping_to_active_approved_proposal(
    db_client: TestClient,
) -> None:
    revision_id = register_intake(
        db_client,
        package_id="pkg-runner-brief-decomposition",
        content_hash="sha256:runner-brief-decomposition",
        idempotency_key="package-intake-runner-brief-decomposition",
    )
    ac_ids = acceptance_criteria_by_key(db_client, revision_id)
    rejected_authority = {
        "capabilities": {
            "repo.read": "allowed",
            "repo.edit": "allowed",
            "command.run": "allowed",
        },
        "budgets": {"max_attempts": 2, "max_llm_calls": 5},
        "constraints": {
            "target_repository": "AlobarQuest/rejected-repo",
            "allowed_commands": ["pytest"],
        },
    }
    approved_authority = {
        "capabilities": {
            "repo.read": "allowed",
            "repo.edit": "allowed",
            "command.run": "allowed",
        },
        "budgets": {"max_attempts": 2, "max_llm_calls": 5},
        "constraints": {
            "target_repository": "AlobarQuest/orchestrator",
            "allowed_commands": ["make check"],
        },
    }

    rejected = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=WORKER,
        json=proposal_payload(
            revision_id,
            ac_ids,
            idempotency_key="proposal-runner-brief-rejected",
            proposed_units=[
                {
                    "unit_key": "unit-1",
                    "title": "Implement service",
                    "outcome": "Service persists proposals.",
                    "required_capability": "repository_write",
                    "authority": rejected_authority,
                    "max_attempts": 2,
                },
                {
                    "unit_key": "unit-2",
                    "title": "Implement tests",
                    "outcome": "Service is covered by focused tests.",
                    "required_capability": "repository_write",
                    "authority": AUTHORITY,
                    "max_attempts": 3,
                },
            ],
            ac_mappings=[{"ac_id": ac_ids["AC-002"], "unit_key": "unit-1"}],
            retained_acs=[
                {
                    "ac_id": ac_ids["AC-001"],
                    "rationale": "Leave AC-001 at package scope for the rejected split.",
                }
            ],
        ),
    )
    assert rejected.status_code == 201
    rejected_decision = db_client.post(
        f"/api/v1/decomposition-proposals/{rejected.json()['id']}/reject",
        headers=DECOMPOSITION_HUMAN,
        json=decision_payload(
            "proposal-runner-brief-rejected-decision",
            "Reject the first mapping.",
        ),
    )
    assert rejected_decision.status_code == 200

    approved = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=WORKER,
        json=proposal_payload(
            revision_id,
            ac_ids,
            idempotency_key="proposal-runner-brief-approved",
            proposed_units=[
                {
                    "unit_key": "unit-1",
                    "title": "Implement service",
                    "outcome": "Service persists proposals.",
                    "required_capability": "repository_write",
                    "authority": approved_authority,
                    "max_attempts": 2,
                },
                {
                    "unit_key": "unit-2",
                    "title": "Implement tests",
                    "outcome": "Service is covered by focused tests.",
                    "required_capability": "repository_write",
                    "authority": AUTHORITY,
                    "max_attempts": 3,
                },
            ],
            ac_mappings=[{"ac_id": ac_ids["AC-001"], "unit_key": "unit-1"}],
            retained_acs=[
                {
                    "ac_id": ac_ids["AC-002"],
                    "rationale": "Leave AC-002 at package scope for the approved split.",
                }
            ],
        ),
    )
    assert approved.status_code == 201
    approved_decision = db_client.post(
        f"/api/v1/decomposition-proposals/{approved.json()['id']}/approve",
        headers=DECOMPOSITION_HUMAN,
        json=decision_payload(
            "proposal-runner-brief-approved-decision",
            "Approve the active mapping.",
        ),
    )

    assert approved_decision.status_code == 200
    unit_id = approved_decision.json()["created_work_unit_ids"]["unit-1"]

    response = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER)

    assert response.status_code == 200
    body = response.json()
    assert [criterion["ac_id"] for criterion in body["acceptance_criteria"]] == ["AC-001"]
    assert body["authority"]["envelope"] == approved_authority
    assert body["target"]["repository"] == "AlobarQuest/orchestrator"


def test_runner_brief_uses_read_only_readiness_evaluation(
    db_client: TestClient,
    monkeypatch,
) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "runner-brief-readonly-revision",
            "expected_version": 0,
            "package_id": "ws-4.1-pilot-readonly",
            "source_repository": "AlobarQuest/intent-packages",
            "revision": 1,
            "content_hash": "sha256:runner-brief-readonly",
            "source_path": "packages/ws-4.1/package.yaml",
            "source_commit": "def456",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
            "approval_event_id": "evt-runner-brief-readonly",
            "enforcement_snapshot": {"required_context": {"capabilities": ["repository_write"]}},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    revision_id = revision.json()["id"]

    unit = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "runner-brief-readonly-unit",
            "expected_version": 0,
            "unit_key": "pilot-readonly",
            "title": "Pilot factory runner readonly",
            "outcome": "Open a PR with evidence",
            "required_capability": "repository_write",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    calls: list[bool] = []

    def fake_evaluate_readiness(session, unit_id_arg, *, for_update=True):
        del session, unit_id_arg
        calls.append(for_update)
        return SimpleNamespace(status="not_ready", reasons=())

    monkeypatch.setattr(runner_brief_service, "evaluate_readiness", fake_evaluate_readiness)

    response = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER)

    assert response.status_code == 200
    assert calls == [False]
