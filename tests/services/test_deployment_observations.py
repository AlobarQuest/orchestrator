import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import DeploymentObservation, Event, Evidence, WorkUnit
from orchestrator.services.deployment_observations import (
    DeploymentObservationCommand,
    list_deployment_observations,
    record_deployment_observation,
)
from orchestrator.services.evidence import record_adjudication
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.release_artifacts import record_release_artifact
from orchestrator.services.verifier import VerifyCommand, verify_work_unit
from tests.services.test_release_artifacts import (
    DIGEST,
    MACHINE_DIGEST,
    OTHER_DIGEST,
    completed_unit,
    machine_local_command,
)
from tests.services.test_release_artifacts import (
    command as release_command,
)

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
VERIFIER = ActorContext("verifier-1", ActorRole.VERIFIER)
OBSERVED_AT = datetime(2026, 7, 8, 20, 0, tzinfo=UTC)
BASE_URL = "https://sds.alobar.net"
DEPLOYMENT_REF = "coolify:eqj5l7k705fhi12x9i74fqf0:ws53"
DEPLOYMENT_URL = "https://coolify.example.invalid/project/orchestrator/ws53"


def release_binding(session: Session, *, key: str = "deploy-observation"):
    unit = completed_unit(session, key=key)
    binding = record_release_artifact(
        session,
        release_command(unit, key=f"{key}-binding"),
    )
    assert not isinstance(binding, DomainError)
    return unit, binding


def observation_command(binding, *, key: str = "deployment-observation"):
    return DeploymentObservationCommand(
        release_artifact_binding_id=binding.id,
        actor=SYSTEM,
        environment="production",
        base_url=BASE_URL,
        observed_artifact_digest=DIGEST,
        deployment_ref=DEPLOYMENT_REF,
        deployment_url=DEPLOYMENT_URL,
        deployer="coolify",
        observed_at=OBSERVED_AT,
        probe_summary={
            "probes": [
                {
                    "name": "live",
                    "method": "GET",
                    "endpoint": "/health/live",
                    "expected_status_min": 200,
                    "expected_status_max": 299,
                    "status_code": 200,
                    "observed_at": OBSERVED_AT.isoformat(),
                },
                {
                    "name": "ready",
                    "method": "GET",
                    "endpoint": "/health/ready",
                    "expected_status_min": 200,
                    "expected_status_max": 299,
                    "status_code": 200,
                    "observed_at": OBSERVED_AT.isoformat(),
                },
            ]
        },
        route_summary={
            "routes": [
                {
                    "path": "/api/v1/work-units/{unit_id}/verify",
                    "present": True,
                },
                {
                    "path": "/api/v1/work-units/{unit_id}/release-artifacts",
                    "present": True,
                },
                {
                    "path": "/api/v1/release-artifacts/{binding_id}/deployment-observations",
                    "present": True,
                },
            ]
        },
        auth_summary={"missing_m2m_status": 401, "configured_m2m_status": 200},
        dispatch_summary={"dispatch_enabled": False},
        status_summary={"status": "observed", "summary": "bounded production facts"},
        idempotency_key=key,
        expected_version=0,
    )


def test_records_deployment_observation_and_generated_post_deploy_unit(
    migrated_session: Session,
) -> None:
    implementation_unit, binding = release_binding(migrated_session)
    original_state = implementation_unit.state
    original_version = implementation_unit.version

    observation = record_deployment_observation(
        migrated_session,
        observation_command(binding),
    )

    assert isinstance(observation, DeploymentObservation)
    assert observation.release_artifact_binding_id == binding.id
    assert observation.implementation_work_unit_id == implementation_unit.id
    assert observation.work_package_revision_id == binding.work_package_revision_id
    assert observation.package_revision_hash == binding.package_revision_hash
    assert observation.environment == "production"
    assert observation.base_url == BASE_URL
    assert observation.observed_artifact_digest == DIGEST
    assert observation.deployment_ref == DEPLOYMENT_REF

    generated = migrated_session.get(WorkUnit, observation.post_deploy_work_unit_id)
    assert generated is not None
    assert generated.unit_key == f"post-deploy:{binding.id}:production"
    assert generated.state == WorkUnitState.SUBMITTED
    assert generated.required_capability == "post_deploy_verification"
    assert generated.work_package_revision_id == binding.work_package_revision_id
    assert generated.decomposition_approved_by == SYSTEM.actor_id
    assert generated.decomposition_approved_at is not None
    assert "deploy" not in generated.authority.get("capabilities", {})
    assert "repo.edit" not in generated.authority.get("capabilities", {})

    assert implementation_unit.state == original_state
    assert implementation_unit.version == original_version


def test_rejects_unknown_release_binding(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="unknown-binding")

    result = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="unknown-binding-observation"),
            release_artifact_binding_id=uuid.uuid4(),
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "release_artifact_not_found"


def test_rejects_release_binding_when_implementation_unit_is_not_completed(
    migrated_session: Session,
) -> None:
    unit, binding = release_binding(migrated_session, key="not-completed-binding")
    unit.state = WorkUnitState.READY
    migrated_session.commit()

    result = record_deployment_observation(
        migrated_session,
        observation_command(binding, key="not-completed-observation"),
    )

    assert isinstance(result, DomainError)
    assert result.code == "work_unit_not_completed"


def test_rejects_digest_mismatch(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="digest-mismatch")

    result = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="digest-mismatch-observation"),
            observed_artifact_digest=OTHER_DIGEST,
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "deployment_observation_digest_mismatch"


def test_rejects_missing_required_observation_facts(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="missing-facts")

    missing_environment = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="missing-environment"),
            environment="",
        ),
    )
    missing_probe_status = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="missing-probe-status"),
            probe_summary={"probes": [{"endpoint": "/health/live"}]},
        ),
    )

    assert isinstance(missing_environment, DomainError)
    assert missing_environment.code == "deployment_observation_invalid"
    assert isinstance(missing_probe_status, DomainError)
    assert missing_probe_status.code == "deployment_observation_invalid"


def test_rejects_secret_shaped_observation_metadata(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="secret-shaped-observation")

    result = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="secret-observation"),
            status_summary={"api_token": "not-a-real-token-fixture"},
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "deployment_observation_secret_rejected"


def test_replay_is_idempotent_and_conflict_rejects_changed_facts(
    migrated_session: Session,
) -> None:
    _unit, binding = release_binding(migrated_session, key="idempotent-observation")

    first = record_deployment_observation(migrated_session, observation_command(binding))
    replay = record_deployment_observation(migrated_session, observation_command(binding))
    same_env_changed_facts = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="changed-observation"),
            route_summary={"routes": [{"path": "/health/live", "present": False}]},
        ),
    )
    same_key_changed_command = record_deployment_observation(
        migrated_session,
        replace(observation_command(binding), deployer="manual"),
    )

    assert isinstance(first, DeploymentObservation)
    assert isinstance(replay, DeploymentObservation)
    assert replay.id == first.id
    assert replay.post_deploy_work_unit_id == first.post_deploy_work_unit_id
    assert isinstance(same_env_changed_facts, DomainError)
    assert same_env_changed_facts.code == "deployment_observation_conflict"
    assert isinstance(same_key_changed_command, DomainError)
    assert same_key_changed_command.code == "idempotency_conflict"
    event_count = migrated_session.scalar(
        select(func.count()).where(Event.action == "deployment.observed")
    )
    assert event_count == 1


def test_observation_records_bounded_evidence_and_events(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="bounded-evidence")

    observation = record_deployment_observation(
        migrated_session,
        observation_command(binding, key="bounded-evidence-observation"),
    )

    assert isinstance(observation, DeploymentObservation)
    events = tuple(
        migrated_session.scalars(
            select(Event).where(
                Event.id.in_([observation.event_id, observation.post_deploy_event_id])
            )
        )
    )
    assert {event.action for event in events} == {
        "deployment.observed",
        "post_deploy_verification.created",
    }
    assert "token" not in str([event.payload for event in events]).lower()

    evidence = tuple(
        migrated_session.scalars(
            select(Evidence)
            .where(Evidence.work_unit_id == observation.post_deploy_work_unit_id)
            .order_by(Evidence.ac_id)
        )
    )
    assert [row.ac_id for row in evidence] == [
        "post-deploy-artifact",
        "post-deploy-auth",
        "post-deploy-dispatch",
        "post-deploy-health",
        "post-deploy-routes",
    ]
    assert {row.evidence_type for row in evidence} == {
        "release.deployment_observed",
        "production.auth_behavior",
        "production.dispatch_posture",
        "production.health",
        "production.route_presence",
    }
    assert "Authorization" not in str([row.payload for row in evidence])


def test_generated_post_deploy_unit_verifies_through_ws51(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="verifier-integration")
    observation = record_deployment_observation(
        migrated_session,
        observation_command(binding, key="verifier-integration-observation"),
    )
    assert isinstance(observation, DeploymentObservation)
    generated = migrated_session.get(WorkUnit, observation.post_deploy_work_unit_id)
    assert generated is not None

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=generated.id,
            actor=VERIFIER,
            expected_version=generated.version,
            idempotency_key="verify-post-deploy",
        ),
    )

    assert result.result == "completed"
    assert result.state is WorkUnitState.COMPLETED
    assert {evaluation.ac_id for evaluation in result.evaluations} == {
        "post-deploy-artifact",
        "post-deploy-auth",
        "post-deploy-dispatch",
        "post-deploy-health",
        "post-deploy-routes",
    }
    assert all(evaluation.outcome == "passed" for evaluation in result.evaluations)


def test_generated_post_deploy_unit_rejects_direct_public_adjudication(
    migrated_session: Session,
) -> None:
    _unit, binding = release_binding(migrated_session, key="direct-adjudication")
    observation = record_deployment_observation(
        migrated_session,
        observation_command(binding, key="direct-adjudication-observation"),
    )
    assert isinstance(observation, DeploymentObservation)
    # A HOSTED observation always mints one; the column is nullable because a machine-local
    # activation has none, and narrowing here says which case this test is about.
    assert observation.post_deploy_work_unit_id is not None

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=observation.work_package_revision_id,
        work_unit_id=observation.post_deploy_work_unit_id,
        ac_id="post-deploy-artifact",
        outcome="passed",
        actor=VERIFIER,
        rationale="attempt to bypass verifier evaluation",
        idempotency_key="direct-post-deploy-adjudication",
    )

    assert isinstance(result, DomainError)
    assert result.code == "post_deploy_verifier_required"


def test_generated_post_deploy_unit_fails_closed_for_bad_route_fact(
    migrated_session: Session,
) -> None:
    _unit, binding = release_binding(migrated_session, key="bad-route")
    command = replace(
        observation_command(binding, key="bad-route-observation"),
        route_summary={"routes": [{"path": "/health/live", "present": False}]},
    )
    observation = record_deployment_observation(migrated_session, command)
    assert isinstance(observation, DeploymentObservation)
    generated = migrated_session.get(WorkUnit, observation.post_deploy_work_unit_id)
    assert generated is not None

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=generated.id,
            actor=VERIFIER,
            expected_version=generated.version,
            idempotency_key="verify-bad-route",
        ),
    )

    assert result.result == "revision_required"
    assert result.state is WorkUnitState.REVISION_REQUIRED
    assert any(
        evaluation.ac_id == "post-deploy-routes" and evaluation.outcome == "failed"
        for evaluation in result.evaluations
    )


def test_generated_post_deploy_unit_fails_closed_for_dispatch_enabled(
    migrated_session: Session,
) -> None:
    _unit, binding = release_binding(migrated_session, key="dispatch-enabled")
    command = replace(
        observation_command(binding, key="dispatch-enabled-observation"),
        dispatch_summary={"dispatch_enabled": True},
    )
    observation = record_deployment_observation(migrated_session, command)
    assert isinstance(observation, DeploymentObservation)
    generated = migrated_session.get(WorkUnit, observation.post_deploy_work_unit_id)
    assert generated is not None

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=generated.id,
            actor=VERIFIER,
            expected_version=generated.version,
            idempotency_key="verify-dispatch-enabled",
        ),
    )

    assert result.result == "revision_required"
    assert result.state is WorkUnitState.REVISION_REQUIRED
    assert any(
        evaluation.ac_id == "post-deploy-dispatch" and evaluation.outcome == "failed"
        for evaluation in result.evaluations
    )


def test_rejects_unbounded_raw_observation_fields(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="raw-fields")

    raw_body = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="raw-body"),
            status_summary={"status": "observed", "response_body": "raw external body"},
        ),
    )
    large_probe_list = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="large-probe-list"),
            probe_summary={
                "probes": [
                    {
                        "endpoint": f"/health/{index}",
                        "status_code": 200,
                    }
                    for index in range(11)
                ]
            },
        ),
    )

    assert isinstance(raw_body, DomainError)
    assert raw_body.code == "deployment_observation_secret_rejected"
    assert isinstance(large_probe_list, DomainError)
    assert large_probe_list.code == "deployment_observation_invalid"


def test_canonicalizes_base_url_and_rejects_invalid_urls(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="url-validation")

    observation = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="canonical-url"),
            base_url="https://SDS.ALOBAR.NET/",
        ),
    )
    invalid = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="invalid-url"),
            deployment_url="https://",
        ),
    )

    assert isinstance(observation, DeploymentObservation)
    assert observation.base_url == BASE_URL
    assert isinstance(invalid, DomainError)
    assert invalid.code == "deployment_observation_invalid"


def test_list_deployment_observations(migrated_session: Session) -> None:
    _unit, binding = release_binding(migrated_session, key="list-observations")
    observation = record_deployment_observation(
        migrated_session,
        observation_command(binding, key="list-observations-observation"),
    )

    rows = list_deployment_observations(migrated_session, binding.id)
    missing = list_deployment_observations(migrated_session, uuid.uuid4())

    assert isinstance(observation, DeploymentObservation)
    assert not isinstance(rows, DomainError)
    assert [row.id for row in rows] == [observation.id]
    assert isinstance(missing, DomainError)
    assert missing.code == "release_artifact_not_found"


# ---------------------------------------------------------------------------
# ADR-0030: a machine-local activation is a deployment too, and its record is shaped
# differently. The five probe-shaped summaries describe probing a URL; a working copy on the
# operator machine has none, so the shapes are conditional on `kind` -- the same answer the
# release-artifact table reached one migration earlier, rather than a second mechanism.
# ---------------------------------------------------------------------------

ACTIVATED = {
    "merge_commit_present": "yes",
    "console_entry_points_present": "yes",
    "environment_matches_lock": "yes",
}


def machine_local_binding(session: Session, *, key: str = "machine-activation"):
    unit = completed_unit(session, key=key)
    binding = record_release_artifact(
        session,
        machine_local_command(unit, key=f"{key}-binding"),
    )
    assert not isinstance(binding, DomainError)
    return unit, binding


def activation_command(binding, *, key: str = "machine-activation-check"):
    """The machine-local shape: no URLs, no deployer, no probe-shaped summaries."""
    return DeploymentObservationCommand(
        release_artifact_binding_id=binding.id,
        actor=SYSTEM,
        environment="operator_machine",
        base_url=None,
        observed_artifact_digest=MACHINE_DIGEST,
        deployment_ref="b" * 40,
        deployment_url=None,
        deployer=None,
        observed_at=OBSERVED_AT,
        probe_summary={},
        route_summary={},
        auth_summary={},
        dispatch_summary={},
        status_summary={},
        idempotency_key=key,
        expected_version=0,
        kind="machine_local",
        activation_summary=dict(ACTIVATED),
    )


def test_a_machine_local_activation_is_recorded_with_no_url_and_no_verification_unit(
    migrated_session: Session,
) -> None:
    unit, binding = machine_local_binding(migrated_session)
    # The binding itself records one evidence row; what must not move is that count.
    before = migrated_session.scalar(select(func.count()).select_from(Evidence))

    observation = record_deployment_observation(migrated_session, activation_command(binding))

    assert isinstance(observation, DeploymentObservation)
    assert observation.kind == "machine_local"
    assert observation.implementation_work_unit_id == unit.id
    assert observation.activation_summary == ACTIVATED
    # Absent rather than blanked: a NULL says nobody probed a URL.
    assert observation.base_url is None
    assert observation.deployment_url is None
    assert observation.deployer is None
    # No verification unit and no evidence. The five post-deploy criteria describe probing a
    # hosted application; a unit carrying criteria a working copy can never evidence would be
    # permanently unverifiable, one row per binding.
    assert observation.post_deploy_work_unit_id is None
    assert observation.post_deploy_event_id is None
    assert observation.evidence_ids == []
    assert migrated_session.scalar(select(func.count()).select_from(Evidence)) == before
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(WorkUnit)
            .where(WorkUnit.unit_key.like("post-deploy:%"))
        )
        == 0
    )


def test_the_two_activation_models_are_told_apart_by_one_field(
    migrated_session: Session,
) -> None:
    """Acceptance: a reader distinguishes them without knowing which repository is which."""
    _, hosted = release_binding(migrated_session, key="hosted-side")
    _, machine = machine_local_binding(migrated_session, key="machine-side")

    hosted_row = record_deployment_observation(
        migrated_session, observation_command(hosted, key="hosted-observation")
    )
    machine_row = record_deployment_observation(
        migrated_session, activation_command(machine, key="machine-observation")
    )

    assert not isinstance(hosted_row, DomainError)
    assert not isinstance(machine_row, DomainError)
    assert {hosted_row.kind, machine_row.kind} == {"container_image", "machine_local"}
    assert hosted_row.activation_summary == {}
    assert machine_row.probe_summary == {}


def test_a_machine_local_activation_refuses_an_environment_other_than_the_machine(
    migrated_session: Session,
) -> None:
    """One mistaken payload naming `production` would put a working copy into the answer for
    what is serving production -- the collapse the discriminator exists to prevent, arriving
    through the one column the discriminator does not cover."""
    _, binding = machine_local_binding(migrated_session, key="wrong-environment")

    error = record_deployment_observation(
        migrated_session,
        replace(activation_command(binding), environment="production"),
    )

    assert isinstance(error, DomainError)
    assert error.code == "deployment_observation_invalid"


def test_an_observation_must_describe_the_same_activation_model_as_its_binding(
    migrated_session: Session,
) -> None:
    _, container = release_binding(migrated_session, key="kind-disagreement")

    error = record_deployment_observation(
        migrated_session,
        replace(activation_command(container), observed_artifact_digest=DIGEST),
    )

    assert isinstance(error, DomainError)
    assert error.code == "deployment_observation_kind_mismatch"


def test_a_machine_local_activation_refuses_a_digest_its_binding_does_not_name(
    migrated_session: Session,
) -> None:
    """The digest tie is what makes the observation about THIS artifact rather than merely
    about this repository."""
    _, binding = machine_local_binding(migrated_session, key="digest-tie")

    error = record_deployment_observation(
        migrated_session,
        replace(activation_command(binding), observed_artifact_digest=OTHER_DIGEST),
    )

    assert isinstance(error, DomainError)
    assert error.code == "deployment_observation_digest_mismatch"


def test_a_machine_local_activation_refuses_a_probe_shaped_summary(
    migrated_session: Session,
) -> None:
    """Refused rather than ignored: an empty probe summary on a hosted row and a populated one
    on a machine-local row must not both be storable, or the models are not distinguishable."""
    _, binding = machine_local_binding(migrated_session, key="hosted-summary-on-machine")

    error = record_deployment_observation(
        migrated_session,
        replace(
            activation_command(binding),
            dispatch_summary={"dispatch_enabled": False},
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "deployment_observation_invalid"


def test_a_hosted_observation_refuses_an_activation_summary(
    migrated_session: Session,
) -> None:
    _, binding = release_binding(migrated_session, key="activation-summary-on-hosted")

    error = record_deployment_observation(
        migrated_session,
        replace(
            observation_command(binding, key="hosted-with-activation"),
            activation_summary=dict(ACTIVATED),
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "deployment_observation_invalid"


def test_an_activation_summary_missing_a_fact_is_refused(
    migrated_session: Session,
) -> None:
    """EXACT membership: a missing member would read as a fact nobody thought to check, which
    is the shape a reader would take for a clean answer."""
    _, binding = machine_local_binding(migrated_session, key="partial-activation")
    partial = dict(ACTIVATED)
    del partial["environment_matches_lock"]

    error = record_deployment_observation(
        migrated_session,
        replace(activation_command(binding), activation_summary=partial),
    )

    assert isinstance(error, DomainError)
    assert error.code == "deployment_observation_invalid"


def test_an_activation_fact_outside_the_vocabulary_is_refused(
    migrated_session: Session,
) -> None:
    _, binding = machine_local_binding(migrated_session, key="bad-activation-value")

    error = record_deployment_observation(
        migrated_session,
        replace(
            activation_command(binding),
            activation_summary={**ACTIVATED, "console_entry_points_present": True},
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "deployment_observation_invalid"


def test_the_commit_fact_may_not_be_excused_as_not_applicable(
    migrated_session: Session,
) -> None:
    """Every working copy either holds the commit in its history or does not. The other two
    facts genuinely do not apply to a repository with no Python toolchain."""
    _, binding = machine_local_binding(migrated_session, key="commit-not-applicable")

    error = record_deployment_observation(
        migrated_session,
        replace(
            activation_command(binding),
            activation_summary={**ACTIVATED, "merge_commit_present": "not_applicable"},
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "deployment_observation_invalid"


def test_the_two_toolchain_facts_may_be_not_applicable(
    migrated_session: Session,
) -> None:
    """One of the four enrolled working copies is a TypeScript project with no `pyproject.toml`,
    no lockfile and no virtual environment."""
    _, binding = machine_local_binding(migrated_session, key="typescript-repository")

    observation = record_deployment_observation(
        migrated_session,
        replace(
            activation_command(binding),
            activation_summary={
                "merge_commit_present": "yes",
                "console_entry_points_present": "not_applicable",
                "environment_matches_lock": "not_applicable",
            },
        ),
    )

    assert isinstance(observation, DeploymentObservation)
    assert observation.activation_summary["console_entry_points_present"] == "not_applicable"


def test_recording_the_same_activation_twice_replays_rather_than_duplicating(
    migrated_session: Session,
) -> None:
    _, binding = machine_local_binding(migrated_session, key="activation-replay")

    first = record_deployment_observation(migrated_session, activation_command(binding))
    second = record_deployment_observation(migrated_session, activation_command(binding))

    assert isinstance(first, DeploymentObservation)
    assert isinstance(second, DeploymentObservation)
    assert first.id == second.id
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(DeploymentObservation)
            .where(DeploymentObservation.release_artifact_binding_id == binding.id)
        )
        == 1
    )


def _insert_observation(session: Session, row: DeploymentObservation, **overrides: object) -> None:
    """Write a second observation ROUND the service, which is the only reader that sees the
    database's own constraints. The service compares in Python and would dedupe either way."""
    values = {
        "id": uuid.uuid4(),
        "binding_id": row.release_artifact_binding_id,
        "unit_id": row.implementation_work_unit_id,
        "revision_id": row.work_package_revision_id,
        "package_revision_hash": row.package_revision_hash,
        "environment": row.environment,
        "digest": row.observed_artifact_digest,
        "deployment_ref": row.deployment_ref,
        "event_id": row.event_id,
        "activation_summary": '{"merge_commit_present": "yes"}',
        "idempotency_key": "a-second-key-entirely",
        **overrides,
    }
    session.execute(
        text(
            """
            INSERT INTO deployment_observations (
                id, release_artifact_binding_id, implementation_work_unit_id,
                work_package_revision_id, package_revision_hash, kind, environment,
                observed_artifact_digest, deployment_ref, observed_at, activation_summary,
                recorded_by, recorded_at, event_id, evidence_ids, idempotency_key
            ) VALUES (
                :id, :binding_id, :unit_id, :revision_id, :package_revision_hash,
                'machine_local', :environment, :digest, :deployment_ref, now(),
                CAST(:activation_summary AS jsonb),
                'system', now(), :event_id, '[]'::jsonb, :idempotency_key
            )
            """
        ),
        values,
    )


def test_the_binding_environment_constraint_still_refuses_a_second_activation(
    migrated_session: Session,
) -> None:
    """The unique constraint gained NO `NULLS NOT DISTINCT`, and this is what says that was
    right rather than forgotten.

    The sibling table needed that flag because three columns OF ITS UNIQUE TUPLE became
    nullable. Here neither `release_artifact_binding_id` nor `environment` is nullable under
    either kind, so the constraint keeps deduplicating -- measured at the DATABASE, because the
    service does its own comparison and would dedupe whatever the database did.
    """
    _, binding = machine_local_binding(migrated_session, key="db-dedup")
    row = record_deployment_observation(migrated_session, activation_command(binding))
    assert isinstance(row, DeploymentObservation)

    # The CONSTRAINT NAME, not merely that something was refused: this insert satisfies every
    # other rule on the table, so a refusal blamed on anything else would be the test passing
    # for the wrong reason.
    with pytest.raises(IntegrityError) as refusal:
        _insert_observation(migrated_session, row)
    assert "uq_deployment_observation_binding_environment" in str(refusal.value)
    migrated_session.rollback()


def test_the_database_refuses_a_machine_local_row_naming_a_url(
    migrated_session: Session,
) -> None:
    """The kind-conditional CHECK, measured round the service for the same reason. A machine-local
    row carrying a URL would make the two models indistinguishable in the columns a reader
    separates them by, whatever the service happens to validate today."""
    _, binding = machine_local_binding(migrated_session, key="db-kind-check")
    row = record_deployment_observation(migrated_session, activation_command(binding))
    assert isinstance(row, DeploymentObservation)
    migrated_session.execute(
        text("DELETE FROM deployment_observations WHERE id = :id"), {"id": row.id}
    )

    with pytest.raises(IntegrityError) as refusal:
        migrated_session.execute(
            text(
                """
                INSERT INTO deployment_observations (
                    id, release_artifact_binding_id, implementation_work_unit_id,
                    work_package_revision_id, package_revision_hash, kind, environment,
                    base_url, observed_artifact_digest, deployment_ref, observed_at,
                    activation_summary, recorded_by, recorded_at, event_id, evidence_ids,
                    idempotency_key
                ) VALUES (
                    :id, :binding_id, :unit_id, :revision_id, :package_revision_hash,
                    'machine_local', 'operator_machine', 'https://example.invalid', :digest,
                    :deployment_ref, now(), '{"merge_commit_present": "yes"}'::jsonb,
                    'system', now(), :event_id, '[]'::jsonb, 'url-on-a-machine-row'
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "binding_id": row.release_artifact_binding_id,
                "unit_id": row.implementation_work_unit_id,
                "revision_id": row.work_package_revision_id,
                "package_revision_hash": row.package_revision_hash,
                "digest": row.observed_artifact_digest,
                "deployment_ref": row.deployment_ref,
                "event_id": row.event_id,
            },
        )
    assert "ck_deployment_observations_by_kind" in str(refusal.value)
    migrated_session.rollback()
