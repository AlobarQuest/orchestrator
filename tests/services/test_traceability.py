import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Observation, ReconciliationCondition, WorkUnit
from orchestrator.services.deployment_observations import record_deployment_observation
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import ObservationCommand, record_observation
from orchestrator.services.packages import record_approval, register_approved_unit
from orchestrator.services.pr_bindings import upsert_pr_binding
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    record_reconciliation_condition,
)
from orchestrator.services.release_artifacts import record_release_artifact
from orchestrator.services.traceability import (
    TraceabilityAnchor,
    build_chain,
    resolve_anchors,
    traceability_response,
)
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


def _record_condition_for(session: Session, unit: WorkUnit) -> ReconciliationCondition:
    outcome = record_reconciliation_condition(
        session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts={"check_name": "Quality"},
            stored_state={"conclusion": "success"},
            observed_state={"conclusion": "failure"},
            detail="Quality flipped from success to failure after verification read it",
        ),
    )
    assert isinstance(outcome, ConditionOutcome)
    return outcome.condition


def _record_observation_for(session: Session, unit: WorkUnit) -> Observation:
    result = record_observation(
        session,
        ObservationCommand(
            actor=SYSTEM,
            source_system="github",
            source_reference=f"github:AlobarQuest/orchestrator:check:{unit.id}",
            source_url=None,
            trust_classification="delivery_system",
            subject_type="work_unit",
            subject_reference=str(unit.id),
            environment=None,
            observation_type="github_check",
            status="passed",
            severity="info",
            observed_at=LATER,
            summary="Quality workflow passed",
            facts={"workflow": "Quality"},
            payload_digest=None,
            idempotency_key=f"trace-obs-{unit.id}",
            expected_version=0,
        ),
    )
    assert not isinstance(result, DomainError)
    return result


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


def test_build_chain_includes_intent_unit_and_artifact(migrated_session: Session):
    unit = completed_unit(migrated_session, key="chain-unit")
    binding = record_release_artifact(migrated_session, command(unit, key="chain-unit-binding"))
    assert not isinstance(binding, DomainError)

    chain = build_chain(migrated_session, unit.id)

    assert chain.unit.id == unit.id
    assert chain.intent.revision == 1
    assert any(a.artifact_digest == DIGEST for a in chain.artifact)


def test_build_chain_selects_canonical_authority_approval_not_first(
    migrated_session: Session,
):
    unit = completed_unit(migrated_session, key="chain-canonical-authority-unit")

    # A decoy authority-type Approval, recorded FIRST (so it sorts earlier by created_at) and
    # therefore NOT the one that ends up bound to the unit. `standing_context` set (non-empty)
    # is what a real standing-context expansion approval looks like, and is also what makes
    # `record_approval` skip writing `unit.authority_approval_id` for this call.
    decoy = record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id="human-decoy",
        actor_role=ActorRole.HUMAN,
        reason="decoy standing-context approval; must not be selected as canonical",
        idempotency_key=f"decoy-authority-{unit.id}",
        expected_version=unit.version,
        standing_context={"decoy": "context"},
    )
    migrated_session.commit()

    # The canonical per-unit authority envelope approval, recorded SECOND (later created_at).
    # Without `standing_context`, `record_approval` binds this one via `unit.authority_approval_id`.
    canonical = record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
        reason="canonical per-unit authority envelope approval",
        idempotency_key=f"canonical-authority-{unit.id}",
        expected_version=unit.version,
    )
    migrated_session.commit()
    migrated_session.expire_all()

    unit = migrated_session.get(WorkUnit, unit.id)
    assert unit is not None
    assert unit.authority_approval_id == canonical.id
    assert unit.authority_approval_id != decoy.id
    assert decoy.created_at <= canonical.created_at

    chain = build_chain(migrated_session, unit.id)

    assert chain.unit.authority_approved_by == HUMAN.actor_id
    assert chain.unit.authority_approved_by != "human-decoy"
    assert chain.unit.authority_decision == "approved"


def test_build_chain_observation_tail_includes_conditions_and_observations(
    migrated_session: Session,
):
    unit = completed_unit(migrated_session, key="chain-tail-unit")
    _record_condition_for(migrated_session, unit)  # helper: record a ReconciliationCondition
    _record_observation_for(migrated_session, unit)  # helper: record an Observation

    chain = build_chain(migrated_session, unit.id)

    assert len(chain.conditions) == 1
    assert chain.conditions[0].open is True
    assert len(chain.observations) == 1


def test_build_chain_empty_tail_when_none(migrated_session: Session):
    unit = completed_unit(migrated_session, key="chain-empty-tail-unit")

    chain = build_chain(migrated_session, unit.id)

    assert chain.conditions == []
    assert chain.observations == []


def test_traceability_response_orders_chains_by_resolution(migrated_session: Session):
    unit = completed_unit(migrated_session, key="chain-response-unit")

    response = traceability_response(
        migrated_session, TraceabilityAnchor(kind="work_unit", work_unit_id=unit.id)
    )

    assert response.anchor.matched_on == "work_unit"
    assert [c.unit.id for c in response.chains] == [unit.id]


def test_build_chain_pr_and_deployment_hops(migrated_session: Session):
    unit, binding = release_binding(migrated_session, key="chain-hops-unit")
    upsert_pr_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        pr_number=456,
        head_sha=HEAD_SHA,
        attempt=1,
    )
    observation = record_deployment_observation(
        migrated_session, observation_command(binding, key="chain-hops-observation")
    )
    assert not isinstance(observation, DomainError)

    chain = build_chain(migrated_session, unit.id)

    assert chain.pr is not None
    assert chain.pr.pr_number == 456
    assert chain.pr.head_sha == HEAD_SHA
    assert len(chain.commit) == 1
    assert chain.commit[0].merge_commit == MERGE_COMMIT
    assert len(chain.artifact) == 1
    assert len(chain.deployment) == 1
    assert chain.deployment[0].environment == "production"


def test_deployment_digest_matches_flag(migrated_session: Session):
    unit, binding = release_binding(migrated_session, key="chain-digest-unit")
    observation = record_deployment_observation(
        migrated_session, observation_command(binding, key="chain-digest-observation")
    )
    assert not isinstance(observation, DomainError)

    matched_chain = build_chain(migrated_session, unit.id)
    assert matched_chain.deployment[0].digest_matches is True

    # `record_deployment_observation` enforces observed_artifact_digest == binding.artifact_digest
    # at write time (`deployment_observation_digest_mismatch`), so there is no writer path that
    # produces a stored mismatch. Mutate the persisted row directly to exercise build_chain's
    # read-time digest_matches computation against that (write-guard-bypassing) row shape.
    observation.observed_artifact_digest = OTHER_DIGEST
    migrated_session.commit()
    migrated_session.expire_all()

    mismatched_chain = build_chain(migrated_session, unit.id)
    assert mismatched_chain.deployment[0].digest_matches is False
