import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_revision
from orchestrator.services.production_drills import StartProductionDrill, start_production_drill
from orchestrator.services.runtime_observations import (
    RuntimeObservationCommand,
    record_runtime_observation,
)
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
    runtime_observation_id: uuid.UUID,
) -> StartProductionDrill:
    return StartProductionDrill(
        revision_id=revision_id,
        actor=actor,
        idempotency_key=key,
        expected_version=0,
        runtime_observation_id=runtime_observation_id,
    )


def runtime_observation(
    session: Session,
    *,
    key: str,
    image_digest: str = "ghcr.io/alobarquest/orchestrator@sha256:" + "a" * 64,
    observed_at: datetime | None = None,
) -> uuid.UUID:
    observation = record_runtime_observation(
        session,
        RuntimeObservationCommand(
            actor=ActorContext("runtime-observer", ActorRole.SYSTEM, "runtime-observer-key"),
            container_id="a" * 64,
            configured_image_ref="ghcr.io/alobarquest/orchestrator:production",
            observed_image_digest=image_digest,
            openapi_sha256="sha256:" + "b" * 64,
            observed_at=observed_at or TransactionClock().now(session),
            idempotency_key=f"{key}-runtime-observation",
            expected_version=0,
        ),
    )
    assert not isinstance(observation, DomainError)
    return observation.id


def test_human_starts_authorized_production_drill_and_replays_exactly(
    migrated_session: Session,
) -> None:
    package_revision = revision(migrated_session)
    observation_id = runtime_observation(migrated_session, key="drill-1")

    first = start_production_drill(
        migrated_session,
        command(package_revision.id, runtime_observation_id=observation_id),
    )
    replay = start_production_drill(
        migrated_session,
        command(package_revision.id, runtime_observation_id=observation_id),
    )

    assert not isinstance(first, DomainError)
    assert not isinstance(replay, DomainError)
    assert first.id == replay.id
    assert first.revision_id == package_revision.id
    assert first.owner_actor_id == HUMAN.actor_id
    assert first.status == "open"
    assert first.image_digest == "ghcr.io/alobarquest/orchestrator@sha256:" + "a" * 64
    assert first.runtime_observation_id == observation_id
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
    observation_id = runtime_observation(migrated_session, key=f"non-human-{actor.actor_id}")

    result = start_production_drill(
        migrated_session,
        command(package_revision.id, actor=actor, runtime_observation_id=observation_id),
    )

    assert isinstance(result, DomainError)
    assert result.code == "human_actor_required"


def test_production_drill_run_provenance_is_database_immutable(
    migrated_session: Session,
) -> None:
    package_revision = revision(migrated_session)
    observation_id = runtime_observation(migrated_session, key="immutable")
    run = start_production_drill(
        migrated_session,
        command(package_revision.id, runtime_observation_id=observation_id),
    )
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


def test_start_requires_a_fresh_persisted_runtime_observation(
    migrated_session: Session,
) -> None:
    package_revision = revision(migrated_session)
    stale_observation_id = runtime_observation(
        migrated_session,
        key="stale",
        observed_at=TransactionClock().now(migrated_session) - timedelta(minutes=6),
    )

    missing = start_production_drill(
        migrated_session,
        command(package_revision.id, key="missing", runtime_observation_id=uuid.uuid4()),
    )
    stale = start_production_drill(
        migrated_session,
        command(
            package_revision.id,
            key="stale",
            runtime_observation_id=stale_observation_id,
        ),
    )

    assert isinstance(missing, DomainError)
    assert missing.code == "runtime_observation_not_found"
    assert isinstance(stale, DomainError)
    assert stale.code == "runtime_observation_stale"


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_same_key_starts_replay_or_return_payload_conflict(
    migrated_engine: Engine, conflicting: bool
) -> None:
    with Session(migrated_engine) as setup:
        revision_id = revision(setup).id
        first_observation_id = runtime_observation(
            setup,
            key="production-drill-concurrent-observation-first",
        )
        second_observation_id = (
            runtime_observation(
                setup,
                key="production-drill-concurrent-observation-second",
                image_digest="ghcr.io/alobarquest/orchestrator@sha256:" + "c" * 64,
            )
            if conflicting
            else first_observation_id
        )

    start = Barrier(2)

    def submit(runtime_observation_id: uuid.UUID) -> tuple[str, uuid.UUID | str]:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            start.wait(timeout=5)
            result = start_production_drill(
                session,
                command(
                    revision_id,
                    key="production-drill-concurrent",
                    runtime_observation_id=runtime_observation_id,
                ),
            )
            if isinstance(result, DomainError):
                return ("error", result.code)
            return ("run", result.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(submit, observation_id)
            for observation_id in (first_observation_id, second_observation_id)
        )
        results = tuple(future.result(timeout=10) for future in futures)

    runs = tuple(value for kind, value in results if kind == "run")
    errors = tuple(value for kind, value in results if kind == "error")
    assert len(set(runs)) == 1
    assert errors == (("idempotency_conflict",) if conflicting else ())
