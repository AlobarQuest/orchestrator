"""Seeding a work unit through the public API, shared by the API tests that each used to copy it.

The route sequence is the production one for a hand-registered unit: register the revision,
register the unit, record the human authority approval, and drive the SYSTEM ``ready`` edge.
Each caller keeps the values its own assertions depend on (repository, snapshot, titles) and
leaves the rest at these defaults.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import HUMAN, SYSTEM


def register_ready_unit(
    db_client: TestClient,
    key: str,
    *,
    authority: dict[str, Any],
    unit_key: str | None = None,
    title: str | None = None,
    outcome: str = "the answer is inspectable",
    source_repository: str = "owner/repo",
    enforcement_snapshot: dict[str, Any] | None = None,
    approved_at: datetime = datetime(2026, 7, 5, tzinfo=UTC),
) -> str:
    """Register a revision and one unit under ``key``, approve its authority, and make it ready."""
    snapshot = enforcement_snapshot
    if snapshot is None:
        snapshot = {"acceptance_criteria": ["ac-1"]}
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-revision",
            "expected_version": 0,
            "package_id": f"{key}-package",
            "source_repository": source_repository,
            "revision": 1,
            "content_hash": f"sha256:{key}",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": approved_at.isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": snapshot,
            "authority": authority,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201, revision.text
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-unit",
            "expected_version": 0,
            "unit_key": unit_key or f"{key}-unit",
            "title": title or f"{key} unit",
            "outcome": outcome,
            "required_capability": "repo.edit",
            "authority": authority,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": approved_at.isoformat(),
        },
    )
    assert unit.status_code == 201, unit.text
    unit_id = str(unit.json()["id"])
    approved = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert approved.status_code == 200, approved.text
    ready = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready",
        headers=SYSTEM,
        json={"idempotency_key": f"{key}-ready", "expected_version": 1},
    )
    assert ready.status_code == 200, ready.text
    return unit_id
