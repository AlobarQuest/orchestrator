from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, RuntimeObservation
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.runtime_observations import (
    COOLIFY_APPLICATION_ID,
    RUNTIME_OBSERVATION_TARGET,
    RuntimeObservationCommand,
    record_runtime_observation,
)

OBSERVER = ActorContext("runtime-observer", ActorRole.SYSTEM, "runtime-observer-key")
OBSERVED_AT = datetime(2026, 7, 12, 16, 30, tzinfo=UTC)
CONTAINER_ID = "a" * 64
IMAGE_REF = "ghcr.io/alobarquest/orchestrator:abc123-production-amd64"
REPO_DIGEST = "ghcr.io/alobarquest/orchestrator@sha256:" + "b" * 64
OPENAPI_SHA256 = "sha256:" + "c" * 64


def command(*, key: str = "runtime-observation") -> RuntimeObservationCommand:
    return RuntimeObservationCommand(
        actor=OBSERVER,
        container_id=CONTAINER_ID,
        configured_image_ref=IMAGE_REF,
        observed_image_digest=REPO_DIGEST,
        openapi_sha256=OPENAPI_SHA256,
        observed_at=OBSERVED_AT,
        idempotency_key=key,
        expected_version=0,
    )


def test_records_fixed_runtime_facts_and_immutable_event(migrated_session: Session) -> None:
    result = record_runtime_observation(migrated_session, command())

    assert isinstance(result, RuntimeObservation)
    assert result.target == RUNTIME_OBSERVATION_TARGET
    assert result.coolify_application_id == COOLIFY_APPLICATION_ID
    assert result.observer_actor_id == OBSERVER.actor_id
    assert result.observer_credential_key_id == OBSERVER.credential_key_id
    event = migrated_session.get(Event, result.event_id)
    assert event is not None
    assert event.action == "runtime.observed"
    assert event.payload["command"]["target"] == RUNTIME_OBSERVATION_TARGET

    with pytest.raises(Exception, match="append-only"):
        migrated_session.execute(
            text("UPDATE runtime_observations SET target = 'https://other.invalid' WHERE id = :id"),
            {"id": result.id},
        )
    migrated_session.rollback()


def test_idempotency_replays_only_identical_observation(migrated_session: Session) -> None:
    first = record_runtime_observation(migrated_session, command())
    replay = record_runtime_observation(migrated_session, command())
    conflict = record_runtime_observation(
        migrated_session,
        replace(command(), container_id="d" * 64),
    )

    assert isinstance(first, RuntimeObservation)
    assert not isinstance(replay, DomainError), getattr(replay, "code", None)
    assert replay.id == first.id
    assert isinstance(conflict, DomainError)
    assert conflict.code == "idempotency_key_reused"


def test_rejects_nonobserver_actor_and_malformed_facts(migrated_session: Session) -> None:
    unauthorized = record_runtime_observation(
        migrated_session,
        replace(command(), actor=ActorContext("system", ActorRole.SYSTEM)),
    )
    malformed = record_runtime_observation(
        migrated_session,
        replace(command(key="malformed"), observed_image_digest="sha256:" + "a" * 64),
    )
    wrong_version = record_runtime_observation(
        migrated_session,
        replace(command(key="wrong-version"), expected_version=1),
    )

    assert isinstance(unauthorized, DomainError)
    assert unauthorized.code == "role_forbidden"
    assert isinstance(malformed, DomainError)
    assert malformed.code == "runtime_observation_invalid"
    assert isinstance(wrong_version, DomainError)
    assert wrong_version.code == "version_conflict"
