"""Seeding a work unit for the API tests, shared by the tests that each used to copy it.

Production units are born through intake, breakdown and the /review approval. Tests need a unit
without an approved intent package behind it, which is what the WS-3.1 bootstrap routes
(`POST /api/v1/revisions`, `POST /api/v1/revisions/{id}/work-units`) gave them. Those routes were
unreachable in production and ADR-0049 deleted them; their two service functions --
`register_revision` and `register_approved_unit` -- are still production code, reached by intake
and breakdown approval. So the two routes live on here as a TEST-ONLY router, mounted by the test
clients and never by `create_app()`: same request models, same service calls, same error
envelopes, and nothing production serves.

`register_ready_unit` drives the rest of a hand-registered unit's sequence through real routes:
record the human authority approval and the SYSTEM ``ready`` edge.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.api.schemas import CommandBase
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorContext
from orchestrator.services.intake.packages import register_approved_unit, register_revision

SEED_REVISIONS = "/test-support/revisions"


def seed_units_path(revision_id: object) -> str:
    return f"{SEED_REVISIONS}/{revision_id}/work-units"


class AcceptanceCriterionDeclaration(BaseModel):
    """What one of the revision's required acceptance criteria actually IS (WS-P2.32)."""

    ac_id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    approver: str = Field(min_length=1)


class RevisionRegistration(CommandBase):
    package_id: str
    source_repository: str
    revision: int = Field(gt=0)
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    approved_at: datetime
    approval_event_id: str = Field(min_length=1)
    enforcement_snapshot: dict[str, Any]
    authority: dict[str, Any]
    registry_version: int = Field(ge=0)
    acceptance_criteria: list[AcceptanceCriterionDeclaration] | None = None


class UnitRegistration(CommandBase):
    unit_key: str
    title: str
    outcome: str
    required_capability: str
    authority: dict[str, Any]
    max_attempts: int = Field(ge=0, default=3)
    approved_by: str
    approved_at: datetime


_SessionDep = Annotated[Session, Depends(get_session)]
_ActorDep = Annotated[ActorContext, Depends(get_actor)]

seeding_router = APIRouter(prefix=SEED_REVISIONS, include_in_schema=False)


@seeding_router.post("", status_code=201)
def _seed_revision(
    body: RevisionRegistration, actor: _ActorDep, session: _SessionDep
) -> dict[str, object]:
    revision = register_revision(
        session,
        **body.model_dump(exclude={"authority"}),
        authority=normalize_authority(body.authority),
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    session.commit()
    return {"id": str(revision.id), "revision": revision.revision}


@seeding_router.post("/{revision_id}/work-units", status_code=201)
def _seed_unit(
    revision_id: uuid.UUID, body: UnitRegistration, actor: _ActorDep, session: _SessionDep
) -> dict[str, object]:
    unit = register_approved_unit(
        session,
        revision_id=revision_id,
        **body.model_dump(exclude={"authority"}),
        authority=normalize_authority(body.authority),
        authority_payload=body.authority,
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    session.commit()
    return {"id": str(unit.id), "state": unit.state, "version": unit.version}


def mount_seeding_routes(app: FastAPI) -> None:
    """Give a test application the two seeding routes production no longer serves."""
    app.include_router(seeding_router)


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
    # Imported here, not at the top: test_lifecycle_api imports this module for SEED_REVISIONS,
    # so a module-level import back into it would be circular.
    from tests.api.test_lifecycle_api import HUMAN, SYSTEM

    snapshot = enforcement_snapshot
    if snapshot is None:
        snapshot = {"acceptance_criteria": ["ac-1"]}
    revision = db_client.post(
        SEED_REVISIONS,
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
        seed_units_path(revision.json()["id"]),
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
