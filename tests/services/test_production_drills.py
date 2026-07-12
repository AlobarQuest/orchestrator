import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_revision
from orchestrator.services.production_drills import StartProductionDrill, start_production_drill
from tests.services.test_package_registration import AUTHORITY, NOW

HUMAN = ActorContext("human-1", ActorRole.HUMAN)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def revision(session: Session):
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
    session.commit()
    return value


def command(
    revision_id: uuid.UUID,
    *,
    actor: ActorContext = HUMAN,
    key: str = "drill-1",
    image_digest: str = "sha256:" + "a" * 64,
) -> StartProductionDrill:
    return StartProductionDrill(
        revision_id=revision_id,
        actor=actor,
        idempotency_key=key,
        expected_version=0,
        image_ref="ghcr.io/alobarquest/orchestrator:production",
        image_digest=image_digest,
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
    assert event.actor_id == HUMAN.actor_id
    assert event.payload["command"]["actor_role"] == ActorRole.HUMAN.value
    assert event.payload["authorization"] == {
        "revision_approved_by": HUMAN.actor_id,
        "revision_approved_at": package_revision.approved_at.isoformat(),
        "revision_approval_event_id": package_revision.approval_event_id,
    }


@pytest.mark.parametrize("actor", [WORKER, SYSTEM])
def test_non_human_actor_cannot_start_production_drill(
    migrated_session: Session, actor: ActorContext
) -> None:
    package_revision = revision(migrated_session)

    result = start_production_drill(migrated_session, command(package_revision.id, actor=actor))

    assert isinstance(result, DomainError)
    assert result.code == "human_actor_required"


def test_production_drill_run_provenance_is_database_immutable(
    migrated_session: Session,
) -> None:
    package_revision = revision(migrated_session)
    run = start_production_drill(migrated_session, command(package_revision.id))
    assert not isinstance(run, DomainError)

    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text("UPDATE production_drill_runs SET image_digest = 'changed' WHERE id = :id"),
            {"id": run.id},
        )
        migrated_session.commit()
    migrated_session.rollback()

    migrated_session.execute(
        text(
            "UPDATE production_drill_runs SET status = 'closed', closed_at = now(), "
            "closure_reason = 'completed' WHERE id = :id"
        ),
        {"id": run.id},
    )
    migrated_session.commit()


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_same_key_starts_replay_or_return_payload_conflict(
    migrated_engine: Engine, conflicting: bool
) -> None:
    with Session(migrated_engine) as setup:
        revision_id = revision(setup).id

    start = Barrier(2)

    def submit(image_digest: str) -> tuple[str, uuid.UUID | str]:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            start.wait(timeout=5)
            result = start_production_drill(
                session,
                command(
                    revision_id,
                    key="production-drill-concurrent",
                    image_digest=image_digest,
                ),
            )
            if isinstance(result, DomainError):
                return ("error", result.code)
            return ("run", result.id)

    digests = ("sha256:" + "a" * 64, "sha256:" + ("c" if conflicting else "a") * 64)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(submit, digest) for digest in digests)
        results = tuple(future.result(timeout=10) for future in futures)

    runs = tuple(value for kind, value in results if kind == "run")
    errors = tuple(value for kind, value in results if kind == "error")
    assert len(set(runs)) == 1
    assert errors == (("idempotency_conflict",) if conflicting else ())
