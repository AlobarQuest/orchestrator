"""Seeding work units for tests, shared by the tests that each used to copy it.

Two layers. The API tests seed through a test-only router, described next. The service tests seed
through the same service functions with a session (`register_test_revision`, `register_unit`).

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
from typing import Annotated, Any, Required, TypedDict, Unpack

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.api.schemas.common import CommandBase
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope, normalize_authority
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.services.intake.packages import (
    DependencySpec,
    register_approved_unit,
    register_revision,
)

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


class SeededUnitFields(TypedDict, total=False):
    """`register_seeded_unit`'s keyword arguments, so the wrappers that forward them stay typed."""

    authority: Required[dict[str, Any]]
    unit_key: str | None
    title: str | None
    outcome: str
    source_repository: str
    content_hash: str | None
    source_commit: str
    enforcement_snapshot: dict[str, Any] | None
    approved_at: datetime


def register_seeded_unit(
    db_client: TestClient,
    key: str,
    *,
    authority: dict[str, Any],
    unit_key: str | None = None,
    title: str | None = None,
    outcome: str = "the answer is inspectable",
    source_repository: str = "owner/repo",
    content_hash: str | None = None,
    source_commit: str = "abc123",
    enforcement_snapshot: dict[str, Any] | None = None,
    approved_at: datetime = datetime(2026, 7, 5, tzinfo=UTC),
) -> tuple[str, str]:
    """Register a revision and one unit under ``key`` through the seeding routes; return the ids."""
    # Imported here, not at the top: test_lifecycle_api imports this module for SEED_REVISIONS,
    # so a module-level import back into it would be circular.
    from tests.api.test_lifecycle_api import HUMAN

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
            "content_hash": f"sha256:{key}" if content_hash is None else content_hash,
            "source_path": "intent.md",
            "source_commit": source_commit,
            "approved_by": "devon",
            "approved_at": approved_at.isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": snapshot,
            "authority": authority,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201, revision.text
    revision_id = str(revision.json()["id"])
    unit = db_client.post(
        seed_units_path(revision_id),
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
    return revision_id, str(unit.json()["id"])


def register_ready_unit(
    db_client: TestClient, key: str, **registration: Unpack[SeededUnitFields]
) -> str:
    """Register a unit as `register_seeded_unit` does, approve its authority, and make it ready."""
    from tests.api.test_lifecycle_api import HUMAN, SYSTEM

    _, unit_id = register_seeded_unit(db_client, key, **registration)
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


def register_completed_unit(
    db_client: TestClient, engine: Engine, key: str, **registration: Unpack[SeededUnitFields]
) -> tuple[str, str]:
    """Register a unit as `register_seeded_unit` does, then write it straight to COMPLETED.

    The write skips the lifecycle on purpose: these tests are about what happens after completion.
    """
    revision_id, unit_id = register_seeded_unit(db_client, key, **registration)
    with Session(engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        unit.state = WorkUnitState.COMPLETED
        session.commit()
    return revision_id, unit_id


# Session-level seeding, for tests that call the service functions directly rather than the routes.

AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
NOW = datetime(2026, 7, 5, tzinfo=UTC)
APPROVAL_EVENT_ID = str(uuid.UUID(int=1))


def register_test_revision(
    session: Session, *, acceptance_criteria: tuple[str, ...] = ("ac-1",)
) -> WorkPackageRevision:
    """The canonical single-criterion revision, or a revision declaring several criteria.

    `work_package_revisions` is append-only at the database (`reject_append_only_mutation`), so a
    test that needs more than one declared criterion must say so at registration -- the list cannot
    be widened afterwards. A non-default list gets its own package id and content hash so it is a
    genuinely different revision rather than a conflicting registration of the canonical one.
    """
    suffix = "" if acceptance_criteria == ("ac-1",) else "-" + "-".join(acceptance_criteria)
    return register_revision(
        session,
        package_id=f"pkg-1{suffix}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:one{suffix}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=NOW,
        approval_event_id=APPROVAL_EVENT_ID if not suffix else f"{APPROVAL_EVENT_ID}{suffix}",
        enforcement_snapshot={"acceptance_criteria": list(acceptance_criteria)},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def register_unit(
    session: Session,
    key: str,
    *,
    unit_id: uuid.UUID | None = None,
    dependencies: tuple[DependencySpec, ...] = (),
    acceptance_criteria: tuple[str, ...] = ("ac-1",),
    authority: AuthorityEnvelope = AUTHORITY,
) -> WorkUnit:
    """Register one unit under the canonical test revision; a second unit shares that revision."""
    revision = register_test_revision(session, acceptance_criteria=acceptance_criteria)
    return register_approved_unit(
        session,
        unit_id=unit_id,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=authority,
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        dependencies=dependencies,
    )
