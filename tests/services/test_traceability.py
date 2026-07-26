import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.services.deployment_observations import record_deployment_observation
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_approved_unit
from orchestrator.services.pr_bindings import upsert_pr_binding
from orchestrator.services.release_artifacts import record_release_artifact
from orchestrator.services.traceability import TraceabilityAnchor, resolve_anchors
from tests.services.test_deployment_observations import observation_command, release_binding
from tests.services.test_package_registration import AUTHORITY
from tests.services.test_release_artifacts import (
    DIGEST,
    HUMAN,
    MERGE_COMMIT,
    OTHER_DIGEST,
    SOURCE_COMMIT,
    command,
    completed_unit,
)

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
EARLIER = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 8, 20, 0, tzinfo=UTC)
OTHER_MERGE_COMMIT = "5cd4132" + "c" * 33
HEAD_SHA = "1" * 40


def test_resolve_by_work_unit_id(migrated_session: Session):
    unit = completed_unit(migrated_session)
    anchor = TraceabilityAnchor(kind="work_unit", work_unit_id=unit.id)
    assert resolve_anchors(migrated_session, anchor) == (unit.id,)


def test_resolve_named_unit_missing_raises(migrated_session: Session):
    anchor = TraceabilityAnchor(kind="work_unit", work_unit_id=uuid.uuid4())
    with pytest.raises(DomainError) as exc:
        resolve_anchors(migrated_session, anchor)
    assert exc.value.code == "work_unit_not_found"


def test_resolve_by_artifact_digest_filter_empty_is_ok(migrated_session: Session):
    anchor = TraceabilityAnchor(kind="artifact_digest", artifact_digest="sha256:" + "0" * 64)
    assert resolve_anchors(migrated_session, anchor) == ()


def test_resolve_named_revision_missing_raises(migrated_session: Session):
    anchor = TraceabilityAnchor(kind="revision", revision_id=uuid.uuid4())
    with pytest.raises(DomainError) as exc:
        resolve_anchors(migrated_session, anchor)
    assert exc.value.code == "revision_not_found"


def test_resolve_by_revision_id(migrated_session: Session):
    unit_1 = completed_unit(migrated_session, key="fan-out-unit")
    revision_id = unit_1.work_package_revision_id
    unit_2 = register_approved_unit(
        migrated_session,
        revision_id=revision_id,
        unit_key="a-earlier-unit-key",
        title="Second unit on the same revision",
        outcome="Fan-out ordering is proven",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=EARLIER,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    migrated_session.commit()

    anchor = TraceabilityAnchor(kind="revision", revision_id=revision_id)

    # unit_2's key ("a-earlier-unit-key") sorts before unit_1's key ("fan-out-unit").
    assert resolve_anchors(migrated_session, anchor) == (unit_2.id, unit_1.id)


def test_resolve_by_artifact_digest(migrated_session: Session):
    unit = completed_unit(migrated_session, key="digest-anchor")
    binding = record_release_artifact(migrated_session, command(unit, key="digest-anchor-binding"))
    assert not isinstance(binding, DomainError)

    anchor = TraceabilityAnchor(kind="artifact_digest", artifact_digest=DIGEST)

    assert resolve_anchors(migrated_session, anchor) == (unit.id,)


def test_resolve_by_commit(migrated_session: Session):
    unit = completed_unit(migrated_session, key="commit-anchor")
    binding = record_release_artifact(migrated_session, command(unit, key="commit-anchor-binding"))
    assert not isinstance(binding, DomainError)

    by_source_commit = TraceabilityAnchor(kind="commit", commit=SOURCE_COMMIT)
    by_merge_commit = TraceabilityAnchor(kind="commit", commit=MERGE_COMMIT)

    assert resolve_anchors(migrated_session, by_source_commit) == (unit.id,)
    assert resolve_anchors(migrated_session, by_merge_commit) == (unit.id,)


def test_resolve_by_pr(migrated_session: Session):
    unit = completed_unit(migrated_session, key="pr-anchor")
    binding = record_release_artifact(migrated_session, command(unit, key="pr-anchor-binding"))
    assert not isinstance(binding, DomainError)

    with_repo = TraceabilityAnchor(
        kind="pr",
        pr_number=binding.implementation_pr_number,
        source_repository=binding.source_repository,
    )
    assert resolve_anchors(migrated_session, with_repo) == (unit.id,)

    # A repo-less PR anchor falls back to the UnitPrBinding table (the worker's live PR head),
    # not the release-artifact record.
    upsert_pr_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        pr_number=987,
        head_sha=HEAD_SHA,
        attempt=1,
    )

    repo_less = TraceabilityAnchor(kind="pr", pr_number=987)
    assert resolve_anchors(migrated_session, repo_less) == (unit.id,)


def test_resolve_by_environment_picks_latest_observation_per_unit(migrated_session: Session):
    unit, binding_1 = release_binding(migrated_session, key="env-anchor")
    binding_2 = record_release_artifact(
        migrated_session,
        replace(
            command(unit, key="env-anchor-binding-2"),
            merge_commit=OTHER_MERGE_COMMIT,
            artifact_digest=OTHER_DIGEST,
        ),
    )
    assert not isinstance(binding_2, DomainError)

    first = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding_1, key="env-anchor-obs-1"),
            observed_at=EARLIER,
        ),
    )
    second = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding_2, key="env-anchor-obs-2"),
            observed_at=LATER,
            observed_artifact_digest=OTHER_DIGEST,
        ),
    )
    assert not isinstance(first, DomainError)
    assert not isinstance(second, DomainError)

    anchor = TraceabilityAnchor(kind="environment", environment="production")

    # Two observations for the same unit + environment collapse to a single entry, selected by
    # the newest observed_at.
    assert resolve_anchors(migrated_session, anchor) == (unit.id,)
