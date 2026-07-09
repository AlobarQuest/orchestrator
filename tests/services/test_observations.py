from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, Observation, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import (
    ObservationCommand,
    ObservationFilters,
    canonical_fact_hash,
    list_observations,
    record_observation,
)
from tests.services.test_release_artifacts import completed_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
VERIFIER = ActorContext("verifier-1", ActorRole.VERIFIER)
OBSERVED_AT = datetime(2026, 7, 8, 22, 30, tzinfo=UTC)


def command(*, key: str = "observation-1") -> ObservationCommand:
    return ObservationCommand(
        actor=SYSTEM,
        source_system="github",
        source_reference="github:AlobarQuest/orchestrator:check:28981229890",
        source_url="https://github.com/AlobarQuest/orchestrator/actions/runs/28981229890",
        trust_classification="delivery_system",
        subject_type="repo",
        subject_reference="AlobarQuest/orchestrator",
        environment=None,
        observation_type="github_check",
        status="passed",
        severity="info",
        observed_at=OBSERVED_AT,
        summary="Quality workflow passed",
        facts={
            "workflow": "Quality",
            "run_id": "28981229890",
            "head_sha": "a6161e603686d8e85a4e7e80e4cdee30a624be79",
            "conclusion": "success",
            "attempt": 1,
        },
        payload_digest=None,
        idempotency_key=key,
        expected_version=0,
    )


def test_records_observation_event_and_does_not_mutate_lifecycle(
    migrated_session: Session,
) -> None:
    unit = completed_unit(migrated_session, key="observation-no-lifecycle")
    original_state = unit.state
    original_version = unit.version

    observation = record_observation(migrated_session, command())

    assert isinstance(observation, Observation)
    assert observation.source_system == "github"
    assert observation.source_reference == "github:AlobarQuest/orchestrator:check:28981229890"
    assert (
        observation.source_url
        == "https://github.com/AlobarQuest/orchestrator/actions/runs/28981229890"
    )
    assert observation.trust_classification == "delivery_system"
    assert observation.subject_type == "repo"
    assert observation.subject_reference == "AlobarQuest/orchestrator"
    assert observation.observation_type == "github_check"
    assert observation.status == "passed"
    assert observation.severity == "info"
    assert observation.normalized_fact_hash == canonical_fact_hash(command())
    assert observation.recorded_by == SYSTEM.actor_id

    event = migrated_session.get(Event, observation.event_id)
    assert event is not None
    assert event.action == "observation.recorded"
    assert event.subject_type == "observation"
    assert event.subject_id == observation.id
    assert event.payload["command"]["normalized_fact_hash"] == observation.normalized_fact_hash
    assert "token" not in str(event.payload).lower()

    migrated_session.expire_all()
    stored_unit = migrated_session.get(WorkUnit, unit.id)
    assert stored_unit is not None
    assert stored_unit.state == original_state
    assert stored_unit.version == original_version
    assert migrated_session.scalar(select(func.count()).select_from(WorkUnit)) == 1


def test_rejects_unsupported_source_and_malformed_facts(migrated_session: Session) -> None:
    unsupported = record_observation(
        migrated_session,
        replace(command(key="unsupported-source"), source_system="email"),
    )
    unbounded = record_observation(
        migrated_session,
        replace(
            command(key="unbounded-facts"),
            facts={"summary": "x" * 513},
        ),
    )
    missing_status = record_observation(
        migrated_session,
        replace(command(key="missing-status"), status="green"),
    )
    unsupported_fact = record_observation(
        migrated_session,
        replace(command(key="unsupported-fact"), facts={"observed": OBSERVED_AT}),
    )

    assert isinstance(unsupported, DomainError)
    assert unsupported.code == "observation_invalid"
    assert isinstance(unbounded, DomainError)
    assert unbounded.code == "observation_invalid"
    assert isinstance(missing_status, DomainError)
    assert missing_status.code == "observation_invalid"
    assert isinstance(unsupported_fact, DomainError)
    assert unsupported_fact.code == "observation_invalid"


def test_rejects_secret_shaped_observation_metadata(migrated_session: Session) -> None:
    secret_key = record_observation(
        migrated_session,
        replace(command(key="secret-key"), facts={"api_token": "redacted"}),
    )
    secret_value = record_observation(
        migrated_session,
        replace(command(key="secret-value"), summary="Authorization: Bearer redacted"),
    )

    assert isinstance(secret_key, DomainError)
    assert secret_key.code == "observation_secret_rejected"
    assert isinstance(secret_value, DomainError)
    assert secret_value.code == "observation_secret_rejected"


def test_replay_is_idempotent_and_conflict_rejects_changed_facts(
    migrated_session: Session,
) -> None:
    first = record_observation(migrated_session, command())
    replay = record_observation(migrated_session, command())
    same_fact_different_key = record_observation(
        migrated_session,
        replace(command(key="same-fact-new-key")),
    )
    changed = record_observation(
        migrated_session,
        replace(command(key="changed-fact"), facts={**command().facts, "conclusion": "failure"}),
    )
    same_key_changed_command = record_observation(
        migrated_session,
        replace(command(), summary="Different summary"),
    )

    assert isinstance(first, Observation)
    assert isinstance(replay, Observation)
    assert isinstance(same_fact_different_key, Observation)
    assert replay.id == first.id
    assert same_fact_different_key.id == first.id
    assert isinstance(changed, DomainError)
    assert changed.code == "observation_conflict"
    assert isinstance(same_key_changed_command, DomainError)
    assert same_key_changed_command.code == "idempotency_conflict"
    assert migrated_session.scalar(select(func.count()).select_from(Observation)) == 1


def test_rejects_non_system_actors(migrated_session: Session) -> None:
    worker = record_observation(
        migrated_session,
        replace(command(key="worker-observation"), actor=WORKER),
    )
    verifier = record_observation(
        migrated_session,
        replace(command(key="verifier-observation"), actor=VERIFIER),
    )

    assert isinstance(worker, DomainError)
    assert worker.code == "role_forbidden"
    assert isinstance(verifier, DomainError)
    assert verifier.code == "role_forbidden"


def test_lists_observations_with_filters(migrated_session: Session) -> None:
    github = record_observation(migrated_session, command(key="github-observation"))
    health = record_observation(
        migrated_session,
        ObservationCommand(
            actor=SYSTEM,
            source_system="uptime_monitor",
            source_reference="uptime:sds-live",
            source_url="https://status.example.invalid/monitors/sds-live",
            trust_classification="monitor",
            subject_type="endpoint",
            subject_reference="https://sds.alobar.net/health/live",
            environment="production",
            observation_type="uptime",
            status="healthy",
            severity="info",
            observed_at=OBSERVED_AT,
            summary="Live endpoint healthy",
            facts={"status_code": 200, "duration_ms": 83},
            payload_digest="sha256:" + "2" * 64,
            idempotency_key="uptime-observation",
            expected_version=0,
        ),
    )

    assert isinstance(github, Observation)
    assert isinstance(health, Observation)
    assert list_observations(
        migrated_session,
        ObservationFilters(source_system="github"),
    ) == (github,)
    assert list_observations(
        migrated_session,
        ObservationFilters(subject_type="endpoint", environment="production"),
    ) == (health,)
    assert list_observations(
        migrated_session,
        ObservationFilters(observation_type="uptime", observed_from=OBSERVED_AT),
    ) == (health,)
