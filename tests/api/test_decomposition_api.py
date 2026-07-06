import uuid
from datetime import UTC, datetime
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import WorkUnit

HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
AUTHORITY = {
    "capabilities": {"repository_write": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
}
NOW = datetime(2026, 7, 5, tzinfo=UTC)


def intake_payload(**overrides: object) -> dict[str, object]:
    base = {
        "idempotency_key": "package-intake-1",
        "expected_version": 0,
        "package_id": "pkg-ws32",
        "source_repository": "owner/repo",
        "revision": 1,
        "content_hash": "sha256:one",
        "source_path": "/tmp/pkg-ws32",
        "source_commit": "abc123",
        "approved_by": "human-1",
        "approved_at": NOW.isoformat(),
        "approval_event_id": str(uuid.UUID(int=1)),
        "approval_ledger_commit": "a" * 40,
        "profile": "software",
        "status_at_intake": "approved",
        "verification_mode": "caller_attested_cli_verified",
        "verification_limitations": {
            "api_recomputes_remote_git_object": False,
            "cli_verified_local_package_hash": True,
            "cli_verified_approval_lineage": True,
        },
        "enforcement_snapshot": {
            "title": "Implement one",
            "outcome": "One works",
            "scope": {"in": ["feature"]},
            "dependencies": [],
            "applicable_standards": ["STD-1"],
        },
        "authority": AUTHORITY,
        "registry_version": 1,
        "acceptance_criteria": [
            {
                "ac_id": "AC-001",
                "condition": "The change is tested.",
                "evidence_type": "automated_test",
                "evidence": "gate: focused tests pass",
                "approver": "policy",
            },
            {
                "ac_id": "AC-002",
                "condition": "The review notes are captured.",
                "evidence_type": "review_note",
                "evidence": "review: reviewer confirmed coverage",
                "approver": "policy",
            },
        ],
    }
    return {**base, **overrides}


def revision_payload(**overrides: object) -> dict[str, object]:
    base = {
        "idempotency_key": "manual-revision-1",
        "expected_version": 0,
        "package_id": "pkg-manual",
        "source_repository": "owner/repo",
        "revision": 1,
        "content_hash": "sha256:manual",
        "source_path": "/tmp/pkg-manual",
        "source_commit": "abc123",
        "approved_by": "human-1",
        "approved_at": NOW.isoformat(),
        "approval_event_id": str(uuid.UUID(int=2)),
        "enforcement_snapshot": {"title": "Manual revision"},
        "authority": AUTHORITY,
        "registry_version": 1,
    }
    return {**base, **overrides}


def proposal_payload(
    revision_id: str,
    acceptance_criteria: dict[str, str],
    **overrides: object,
) -> dict[str, object]:
    base = {
        "idempotency_key": "proposal-1",
        "expected_version": 0,
        "rationale": "Split by independent delivery path.",
        "proposed_units": [
            {
                "unit_key": "unit-1",
                "title": "Implement service",
                "outcome": "Service persists proposals.",
                "required_capability": "repository_write",
                "authority": AUTHORITY,
                "max_attempts": 3,
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
        "dependencies": [
            {
                "source_unit_key": "unit-2",
                "kind": "work_unit",
                "required_state_or_condition": "completed",
                "target_unit_key": "unit-1",
                "external_ref": None,
            },
            {
                "source_unit_key": "unit-1",
                "kind": "external_system",
                "required_state_or_condition": "ci green",
                "target_unit_key": None,
                "external_ref": "ci/build/123",
            },
        ],
        "ac_mappings": [
            {
                "ac_id": acceptance_criteria["AC-001"],
                "unit_key": "unit-1",
            }
        ],
        "retained_acs": [
            {
                "ac_id": acceptance_criteria["AC-002"],
                "rationale": "Reviewed at proposal approval time as a package-level gate.",
            }
        ],
    }
    return {**base, **overrides}


def decision_payload(idempotency_key: str, reason: str) -> dict[str, object]:
    return {
        "idempotency_key": idempotency_key,
        "expected_version": 0,
        "reason": reason,
    }


def register_intake(db_client: TestClient, **overrides: object) -> str:
    response = db_client.post(
        "/api/v1/package-intakes",
        headers=HUMAN,
        json=intake_payload(**overrides),
    )
    assert response.status_code == 201
    return cast(str, response.json()["id"])


def acceptance_criteria_by_key(db_client: TestClient, revision_id: str) -> dict[str, str]:
    response = db_client.get(f"/api/v1/package-intakes/{revision_id}", headers=HUMAN)
    assert response.status_code == 200
    return {
        criterion["ac_id"]: criterion["id"]
        for criterion in response.json()["acceptance_criteria"]
    }


def create_proposal(
    db_client: TestClient,
    revision_id: str,
    *,
    idempotency_key: str = "proposal-1",
    rationale: str = "Split by independent delivery path.",
) -> dict[str, object]:
    ac_ids = acceptance_criteria_by_key(db_client, revision_id)
    response = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=WORKER,
        json=proposal_payload(
            revision_id,
            ac_ids,
            idempotency_key=idempotency_key,
            rationale=rationale,
        ),
    )
    assert response.status_code == 201
    return response.json()


def test_decomposition_proposal_post_returns_proposed_state_without_creating_units(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    revision_id = register_intake(db_client)
    ac_ids = acceptance_criteria_by_key(db_client, revision_id)

    response = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=WORKER,
        json=proposal_payload(revision_id, ac_ids),
    )

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["work_package_revision_id"] == revision_id
    assert body["proposal_number"] == 1
    assert body["state"] == "proposed"
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(WorkUnit)) == 0


def test_decomposition_proposal_list_and_get_expose_review_projection(
    db_client: TestClient,
) -> None:
    revision_id = register_intake(db_client)
    first = create_proposal(db_client, revision_id)
    second = create_proposal(
        db_client,
        revision_id,
        idempotency_key="proposal-2",
        rationale="Alternative split.",
    )

    listed = db_client.get(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=HUMAN,
    )
    detail = db_client.get(
        f"/api/v1/decomposition-proposals/{first['id']}",
        headers=HUMAN,
    )

    assert listed.status_code == 200
    assert [proposal["id"] for proposal in listed.json()] == [first["id"], second["id"]]
    assert [proposal["proposal_number"] for proposal in listed.json()] == [1, 2]
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == first["id"]
    assert [unit["unit_key"] for unit in body["proposed_units"]] == ["unit-1", "unit-2"]
    assert body["proposed_units"][0]["authority"] == {
        "capabilities": {"repository_write": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "unknown_fields": [],
    }
    assert [
        (
            dependency["source_unit_key"],
            dependency["kind"],
            dependency["target_unit_key"],
            dependency["external_ref"],
        )
        for dependency in body["dependencies"]
    ] == [
        ("unit-1", "external_system", None, "ci/build/123"),
        ("unit-2", "work_unit", "unit-1", None),
    ]
    assert body["ac_mappings"] == [
        {
            "unit_key": "unit-1",
            "package_acceptance_criterion": {
                "id": body["ac_mappings"][0]["package_acceptance_criterion"]["id"],
                "ac_id": "AC-001",
                "condition": "The change is tested.",
                "evidence_type": "automated_test",
                "evidence": "gate: focused tests pass",
                "approver": "policy",
            },
        }
    ]
    assert body["retained_acs"] == [
        {
            "rationale": "Reviewed at proposal approval time as a package-level gate.",
            "package_acceptance_criterion": {
                "id": body["retained_acs"][0]["package_acceptance_criterion"]["id"],
                "ac_id": "AC-002",
                "condition": "The review notes are captured.",
                "evidence_type": "review_note",
                "evidence": "review: reviewer confirmed coverage",
                "approver": "policy",
            },
        }
    ]


def test_decomposition_proposal_list_rejects_non_intaken_revision(
    db_client: TestClient,
) -> None:
    created = db_client.post("/api/v1/revisions", headers=HUMAN, json=revision_payload())
    revision_id = created.json()["id"]

    listed = db_client.get(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=HUMAN,
    )

    assert listed.status_code == 404
    assert listed.json()["error"]["code"] == "package_intake_not_found"


def test_decomposition_decision_routes_update_state_and_approval_creates_drafts(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    approve_revision_id = register_intake(
        db_client,
        package_id="pkg-approve",
        content_hash="sha256:approve",
    )
    reject_revision_id = register_intake(
        db_client,
        package_id="pkg-reject",
        content_hash="sha256:reject",
        idempotency_key="package-intake-2",
    )
    revise_revision_id = register_intake(
        db_client,
        package_id="pkg-revise",
        content_hash="sha256:revise",
        idempotency_key="package-intake-3",
    )

    approved_proposal = create_proposal(db_client, approve_revision_id)
    rejected_proposal = create_proposal(
        db_client,
        reject_revision_id,
        idempotency_key="proposal-reject",
    )
    revised_proposal = create_proposal(
        db_client,
        revise_revision_id,
        idempotency_key="proposal-revise",
    )

    approved = db_client.post(
        f"/api/v1/decomposition-proposals/{approved_proposal['id']}/approve",
        headers=HUMAN,
        json=decision_payload("proposal-approve-1", "Approved for draft activation."),
    )
    rejected = db_client.post(
        f"/api/v1/decomposition-proposals/{rejected_proposal['id']}/reject",
        headers=HUMAN,
        json=decision_payload("proposal-reject-1", "Missing split rationale."),
    )
    revised = db_client.post(
        f"/api/v1/decomposition-proposals/{revised_proposal['id']}/require-revision",
        headers=HUMAN,
        json=decision_payload(
            "proposal-revision-1",
            "Please break out the dependency chain more clearly.",
        ),
    )

    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"
    assert set(approved.json()["created_work_unit_ids"]) == {"unit-1", "unit-2"}
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["created_work_unit_ids"] is None
    assert revised.status_code == 200
    assert revised.json()["state"] == "revision_required"
    assert revised.json()["created_work_unit_ids"] is None
    with Session(migrated_engine) as session:
        units = tuple(
            session.scalars(
                select(WorkUnit)
                .where(WorkUnit.work_package_revision_id == uuid.UUID(approve_revision_id))
                .order_by(WorkUnit.unit_key)
            )
        )
    assert [unit.state for unit in units] == ["draft", "draft"]
