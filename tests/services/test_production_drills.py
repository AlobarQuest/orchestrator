import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Approval, Event
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_revision
from orchestrator.services.production_drills import StartProductionDrill, start_production_drill
from tests.services.test_package_registration import AUTHORITY, NOW

HUMAN = ActorContext("human-1", ActorRole.HUMAN)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def revision(session: Session, *, approved: bool = True):
    value = register_revision(
        session,
        package_id=f"production-drill-{uuid.uuid4()}",
        source_repository="AlobarQuest/orchestrator",
        revision=1,
        content_hash="sha256:production-drill",
        source_path="intent.md",
        source_commit="abc123",
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    if approved:
        session.add(
            Approval(
                subject_type="authority",
                subject_id=value.id,
                subject_revision_or_fingerprint=value.authority_fingerprint,
                decision="approved",
                approved_by=HUMAN.actor_id,
                reason="Production drill is authorized",
                event_id=uuid.uuid4(),
                idempotency_key=f"production-drill-approval-{value.id}",
            )
        )
    session.commit()
    return value


def command(revision_id: uuid.UUID, *, actor: ActorContext = HUMAN, key: str = "drill-1"):
    return StartProductionDrill(
        revision_id=revision_id,
        actor=actor,
        idempotency_key=key,
        expected_version=0,
        image_ref="ghcr.io/alobarquest/orchestrator:production",
        image_digest="sha256:" + "a" * 64,
        openapi_digest="sha256:" + "b" * 64,
    )


def test_human_starts_authorized_production_drill_and_replays_exactly(
    migrated_session: Session,
) -> None:
    package_revision = revision(migrated_session)

    first = start_production_drill(migrated_session, command(package_revision.id))
    replay = start_production_drill(migrated_session, command(package_revision.id))

    assert not isinstance(first, DomainError)
    assert not isinstance(replay, DomainError)
    assert first.id == replay.id
    assert first.revision_id == package_revision.id
    assert first.owner_actor_id == HUMAN.actor_id
    assert first.status == "open"
    assert first.image_digest == "sha256:" + "a" * 64
    event = migrated_session.scalar(select(Event).where(Event.subject_id == first.id))
    assert event is not None


@pytest.mark.parametrize("actor", [WORKER, SYSTEM])
def test_non_human_actor_cannot_start_production_drill(
    migrated_session: Session, actor: ActorContext
) -> None:
    package_revision = revision(migrated_session)

    result = start_production_drill(migrated_session, command(package_revision.id, actor=actor))

    assert isinstance(result, DomainError)
    assert result.code == "human_actor_required"


def test_production_drill_requires_authority_approval(migrated_session: Session) -> None:
    package_revision = revision(migrated_session, approved=False)

    result = start_production_drill(migrated_session, command(package_revision.id))

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_authority_approval_required"


def test_production_drill_rejects_unapproved_revision(migrated_session: Session) -> None:
    package_revision = revision(migrated_session)
    approval = migrated_session.scalar(
        select(Approval).where(Approval.subject_id == package_revision.id)
    )
    assert approval is not None
    approval.decision = "rejected"
    migrated_session.commit()

    result = start_production_drill(migrated_session, command(package_revision.id))

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_authority_approval_required"
