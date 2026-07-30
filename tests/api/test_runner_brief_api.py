import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

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
            "required_capability": "repo.edit",
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
                    "required_capability": "repo.edit",
                    "authority": rejected_authority,
                    "max_attempts": 2,
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
                    "required_capability": "repo.edit",
                    "authority": approved_authority,
                    "max_attempts": 2,
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
    # The brief serves the envelope the approver saw, carrying the orchestrator's
    # work_unit_id stamp — this is exactly what the runner validates against.
    assert body["authority"]["envelope"] == {
        **approved_authority,
        "constraints": {**approved_authority["constraints"], "work_unit_id": unit_id},
    }
    assert body["target"]["repository"] == "AlobarQuest/orchestrator"


def test_runner_brief_returns_no_acceptance_criteria_for_unmapped_unit_in_approved_decomposition(
    db_client: TestClient,
) -> None:
    revision_id = register_intake(
        db_client,
        package_id="pkg-runner-brief-empty-mapping",
        content_hash="sha256:runner-brief-empty-mapping",
        idempotency_key="package-intake-runner-brief-empty-mapping",
    )
    ac_ids = acceptance_criteria_by_key(db_client, revision_id)

    approved = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=WORKER,
        json=proposal_payload(
            revision_id,
            ac_ids,
            idempotency_key="proposal-runner-brief-empty-mapping",
            proposed_units=[
                {
                    "unit_key": "unit-1",
                    "title": "Implement service",
                    "outcome": "Service persists proposals.",
                    "required_capability": "repo.edit",
                    "authority": {
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
                    },
                    "max_attempts": 2,
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
            "proposal-runner-brief-empty-mapping-decision",
            "Approve the active mapping.",
        ),
    )
    assert approved_decision.status_code == 200
    unit_id = approved_decision.json()["created_work_unit_ids"]["unit-2"]

    response = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER)

    assert response.status_code == 200
    assert response.json()["acceptance_criteria"] == []


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
            "required_capability": "repo.edit",
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


ENRICHED_CONSTRAINTS = {"target_repository": "AlobarQuest/orchestrator"}
ENRICHMENT_DOCUMENT = {
    "schema_version": 1,
    "profile": "software-delivery",
    "change_class": "software-delivery",
    "roads": [
        {
            "brain": "code",
            "slug": "error-logging",
            "name": "Error handling & structured logging",
            "status": "paved",
            "decided_approach": "structlog + asgi-correlation-id (Python) / pino (TS).",
        }
    ],
    "rules": [
        {
            "brain": "code",
            "road_slug": "error-logging",
            "id": 5,
            "category": "security",
            "severity": "BLOCK",
            "authority": "informational",
            "rule": "Never log secrets, credentials, auth headers, tokens, or full bodies.",
            "reason": "A secret in a log is a leaked secret.",
        }
    ],
    "exemplars": [],
    "content_fingerprint": "sha256:" + "0" * 64,
    "resolved_at": "2026-07-30T00:00:00+00:00",
    "sources": [{"brain": "code", "endpoint": "/api/road/error-logging", "query": "error-logging"}],
}


def _approved_unit_with_enrichment(db_client: TestClient, document: object) -> str:
    revision_id = register_intake(db_client, package_id="enriched-pkg", idempotency_key="enriched")
    ac_ids = acceptance_criteria_by_key(db_client, revision_id)
    payload = proposal_payload(revision_id, ac_ids, idempotency_key="enriched-proposal")
    for unit in cast(list[dict[str, object]], payload["proposed_units"]):
        unit["context_enrichment"] = document
        # runner_brief refuses a unit with no target repository, and the shared
        # decomposition fixture declares none.
        authority = cast(dict[str, object], unit["authority"])
        unit["authority"] = {**authority, "constraints": ENRICHED_CONSTRAINTS}
    proposal = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=DECOMPOSITION_HUMAN,
        json=payload,
    )
    assert proposal.status_code == 201, proposal.text
    proposal_id = proposal.json()["id"]
    approved = db_client.post(
        f"/api/v1/decomposition-proposals/{proposal_id}/approve",
        headers=DECOMPOSITION_HUMAN,
        json=decision_payload("enriched-approve", "Approved with its governed material."),
    )
    assert approved.status_code == 200, approved.text
    return approved.json()["created_work_unit_ids"]["unit-1"]


def test_the_brief_carries_the_enrichment_document_verbatim(db_client: TestClient) -> None:
    """Verbatim is what makes the projection deterministic: stored bytes out.

    Canonical-JSON equality rather than field spot-checks, because "the worker saw
    what the human approved" is a claim about the whole document.
    """
    unit_id = _approved_unit_with_enrichment(db_client, ENRICHMENT_DOCUMENT)

    response = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER)

    assert response.status_code == 200, response.text
    served = response.json()["enrichment"]
    assert json.dumps(served, sort_keys=True) == json.dumps(ENRICHMENT_DOCUMENT, sort_keys=True)


def test_the_brief_is_byte_identical_across_repeated_reads(db_client: TestClient) -> None:
    """Determinism, at the surface a worker actually reads."""
    unit_id = _approved_unit_with_enrichment(db_client, ENRICHMENT_DOCUMENT)

    first = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER)
    second = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER)

    assert json.dumps(first.json()["enrichment"], sort_keys=True) == json.dumps(
        second.json()["enrichment"], sort_keys=True
    )


def test_an_oversized_enrichment_document_is_refused_at_ingress(db_client: TestClient) -> None:
    """A DomainError, never a bare 500 — only two exception types have handlers."""
    bloated = {
        **ENRICHMENT_DOCUMENT,
        "rules": [
            {**ENRICHMENT_DOCUMENT["rules"][0], "id": n, "reason": "x" * 500} for n in range(150)
        ],
    }
    revision_id = register_intake(db_client, package_id="bloated-pkg", idempotency_key="bloated")
    ac_ids = acceptance_criteria_by_key(db_client, revision_id)
    payload = proposal_payload(revision_id, ac_ids, idempotency_key="bloated-proposal")
    for unit in cast(list[dict[str, object]], payload["proposed_units"]):
        unit["context_enrichment"] = bloated
        authority = cast(dict[str, object], unit["authority"])
        unit["authority"] = {**authority, "constraints": ENRICHED_CONSTRAINTS}

    response = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=DECOMPOSITION_HUMAN,
        json=payload,
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "context_enrichment_too_large"
