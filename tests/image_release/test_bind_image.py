"""The pass, against a real git history and hermetic fakes for the three identities.

Every payload the pass composes is also validated against the orchestrator's own wire models and
hosted-shape validators, so a shape this program sends cannot drift from what the server admits
without a test here failing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from image_release.release import (
    ALREADY_BOUND,
    ALREADY_OBSERVED,
    AUTH_POSTURE_UNRECORDABLE,
    BOUND,
    COMPLETED,
    DRY_RUN,
    EARLIER_IMAGE,
    NOT_CARRIED,
    OBSERVED,
    REFUSED,
    REVISION_MISMATCH,
    REVISION_REQUIRED,
    UNAVAILABLE,
    has_conditions,
    has_findings,
    release_pass,
)
from orchestrator.api.schemas.release import (
    DeploymentObservationCommandModel,
    ReleaseArtifactCommandModel,
)
from orchestrator.api.schemas.verifier import VerifyCommandModel
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.services.release import deployment_observations, release_artifacts
from tests.image_release.conftest import (
    BINDING_ID,
    DIGEST,
    OTHER_DIGEST,
    POST_DEPLOY_UNIT_ID,
    SECOND_UNIT_ID,
    FakeProduction,
    FakeSystem,
    FakeVerifier,
    History,
    candidate_row,
    release_for,
)

NOW = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
SYSTEM_ACTOR = ActorContext("system", ActorRole.SYSTEM)


def _run(
    history: History,
    system: FakeSystem,
    *,
    production: FakeProduction | None = None,
    verifier: FakeVerifier | None = None,
    dry_run: bool = False,
    **release: Any,
) -> dict[str, Any]:
    return release_pass(
        release_for(history.built, **release),
        checkout=history.path,
        system=system,
        verifier=verifier if verifier is not None else FakeVerifier(),
        anonymous=production if production is not None else FakeProduction(history.built),
        dry_run=dry_run,
        now=NOW,
    )


def test_binds_a_carried_unit_and_skips_one_that_landed_after_the_build(history: History) -> None:
    system = FakeSystem(
        [candidate_row(history.merge), candidate_row(history.later, unit_id=SECOND_UNIT_ID)]
    )
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier)

    carried, later = summary["units"]
    assert carried["binding"] == {"outcome": BOUND, "binding_id": BINDING_ID}
    assert carried["observation"]["outcome"] == OBSERVED
    assert carried["verification"]["outcome"] == COMPLETED
    assert later["binding"]["outcome"] == NOT_CARRIED
    assert "observation" not in later
    assert [unit for unit, _ in system.bound] == [carried["work_unit_id"]]
    assert [binding for binding, _ in system.observed] == [BINDING_ID]
    assert [unit for unit, _ in verifier.verified] == [POST_DEPLOY_UNIT_ID]
    assert not has_findings(summary) and not has_conditions(summary)


def test_the_built_commit_itself_is_carried(history: History) -> None:
    system = FakeSystem([candidate_row(history.built)])
    summary = _run(history, system)
    assert summary["units"][0]["binding"]["outcome"] == BOUND


def test_a_commit_the_checkout_never_held_is_not_carried_and_says_so(history: History) -> None:
    system = FakeSystem([candidate_row("c" * 40)])

    summary = _run(history, system)

    assert summary["units"][0]["binding"] == {
        "outcome": NOT_CARRIED,
        "reason": "the merge commit is not in the checkout",
    }
    assert system.bound == []
    assert not has_findings(summary)


def test_a_unit_bound_to_this_digest_is_observed_and_not_rebound(history: History) -> None:
    system = FakeSystem(
        [candidate_row(history.merge, binding_id=BINDING_ID, binding_digest=DIGEST)]
    )

    summary = _run(history, system)

    unit = summary["units"][0]
    assert unit["binding"] == {"outcome": ALREADY_BOUND, "binding_id": BINDING_ID}
    assert unit["observation"]["outcome"] == OBSERVED
    assert system.bound == []
    assert [binding for binding, _ in system.observed] == [BINDING_ID]


def test_an_existing_observation_is_found_and_its_unit_verified_without_refiling(
    history: History,
) -> None:
    """Observed and never verified -- an interrupted pass -- must not strand the unit."""
    system = FakeSystem(
        [
            candidate_row(
                history.merge, binding_id=BINDING_ID, binding_digest=DIGEST, observation_id="o"
            )
        ],
        existing=[
            {"id": "elsewhere", "environment": "staging", "post_deploy_work_unit_id": "x"},
            {"id": "o", "environment": "production", "post_deploy_work_unit_id": "pd"},
        ],
    )
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier)

    unit = summary["units"][0]
    assert unit["observation"] == {"outcome": ALREADY_OBSERVED, "observation_id": "o"}
    assert system.observed == []
    assert [unit_id for unit_id, _ in verifier.verified] == ["pd"]


def test_a_unit_bound_to_another_digest_was_released_by_an_earlier_image(
    history: History,
) -> None:
    system = FakeSystem(
        [candidate_row(history.merge, binding_id=BINDING_ID, binding_digest=OTHER_DIGEST)]
    )

    summary = _run(history, system)

    assert summary["units"][0]["binding"] == {
        "outcome": EARLIER_IMAGE,
        "binding_artifact_digest": OTHER_DIGEST,
    }
    assert system.bound == [] and system.observed == []
    assert not has_findings(summary) and not has_conditions(summary)


def test_a_bound_candidate_without_its_digest_is_a_narrowed_contract(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge, binding_id=BINDING_ID)])
    summary = _run(history, system)
    assert summary["units"][0]["binding"]["outcome"] == UNAVAILABLE
    assert has_findings(summary)


def test_a_served_revision_that_is_not_the_built_commit_refuses_every_observation(
    history: History,
) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    verifier = FakeVerifier()

    summary = _run(history, system, production=FakeProduction(history.later), verifier=verifier)

    unit = summary["units"][0]
    assert unit["binding"]["outcome"] == BOUND
    assert unit["observation"]["outcome"] == REVISION_MISMATCH
    assert system.observed == [] and verifier.verified == []
    assert has_conditions(summary) and not has_findings(summary)


def test_an_unknown_served_revision_is_a_mismatch_too(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    summary = _run(history, system, production=FakeProduction(None))
    assert summary["units"][0]["observation"]["outcome"] == REVISION_MISMATCH


def test_an_unauthenticated_read_that_was_not_refused_is_not_filed(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    production = FakeProduction(history.built, missing_status=200)

    summary = _run(history, system, production=production)

    assert summary["units"][0]["observation"]["outcome"] == AUTH_POSTURE_UNRECORDABLE
    assert system.observed == []
    assert has_conditions(summary)


def test_a_degraded_production_is_still_observed_and_verification_decides(
    history: History,
) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    verifier = FakeVerifier(result=REVISION_REQUIRED)

    summary = _run(
        history,
        system,
        production=FakeProduction(history.built, ready_status=503),
        verifier=verifier,
    )

    _, payload = system.observed[0]
    assert payload["status_summary"]["status"] == "degraded"
    assert summary["units"][0]["verification"]["outcome"] == REVISION_REQUIRED
    assert has_conditions(summary)


def test_verification_is_asked_with_the_minted_version_and_a_stable_key(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    verifier = FakeVerifier()

    _run(history, system, verifier=verifier)

    [(unit_id, payload)] = verifier.verified
    assert payload == {"idempotency_key": f"image-verify:{unit_id}", "expected_version": 1}
    VerifyCommandModel.model_validate(payload)


def test_the_probe_runs_once_per_pass(history: History) -> None:
    system = FakeSystem(
        [
            candidate_row(history.merge),
            candidate_row(history.built, unit_id=SECOND_UNIT_ID),
        ]
    )
    production = FakeProduction(history.built)

    _run(history, system, production=production)

    assert production.asked.count("/health/live") == 1


def test_a_dry_run_writes_nothing_and_shows_what_it_would(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier, dry_run=True)

    unit = summary["units"][0]
    assert unit["binding"]["dry_run"] is True
    assert unit["binding"]["record"]["artifact_digest"] == DIGEST
    assert unit["observation"]["dry_run"] is True
    assert system.bound == [] and system.observed == [] and verifier.verified == []


def test_a_dry_run_over_a_bound_unit_neither_observes_nor_verifies(history: History) -> None:
    system = FakeSystem(
        [candidate_row(history.merge, binding_id=BINDING_ID, binding_digest=DIGEST)]
    )
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier, dry_run=True)

    assert summary["units"][0]["observation"]["record"]["idempotency_key"] == (
        f"image-observation:{BINDING_ID}:production"
    )
    assert system.observed == [] and verifier.verified == []


def test_a_checkout_without_the_built_commit_refuses_the_whole_pass(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])

    summary = release_pass(
        release_for("d" * 40),
        checkout=history.path,
        system=system,
        verifier=FakeVerifier(),
        anonymous=FakeProduction("d" * 40),
        dry_run=False,
        now=NOW,
    )

    assert summary["unavailable"] is True
    assert system.bound == []
    assert has_findings(summary)


def test_a_refused_binding_is_a_finding(history: History) -> None:
    from image_release.client import ReleaseCallError

    class Refusing(FakeSystem):
        def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise ReleaseCallError("rejected POST x: 409 (release_artifact_conflict)")

    summary = _run(history, Refusing([candidate_row(history.merge)]))

    assert summary["units"][0]["binding"]["outcome"] == REFUSED
    assert has_findings(summary)


def test_no_candidates_is_quiet_and_probes_nothing(history: History) -> None:
    production = FakeProduction(history.built)
    summary = _run(history, FakeSystem([]), production=production)
    assert summary["units"] == [] and production.asked == []
    assert not has_findings(summary) and not has_conditions(summary)


def test_a_verifier_absent_reports_dry_run_rather_than_verifying(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    summary = release_pass(
        release_for(history.built),
        checkout=history.path,
        system=system,
        verifier=None,
        anonymous=FakeProduction(history.built),
        dry_run=False,
        now=NOW,
    )
    assert summary["units"][0]["verification"]["outcome"] == DRY_RUN


# ------------------------------------------------------------------------------------------------
# The payloads, held to the server's own models and validators
# ------------------------------------------------------------------------------------------------


@pytest.fixture
def sent(history: History) -> tuple[dict[str, Any], dict[str, Any]]:
    system = FakeSystem([candidate_row(history.merge)])
    _run(history, system)
    [(_, binding)] = system.bound
    [(_, observation)] = system.observed
    return binding, observation


def test_the_binding_payload_is_one_the_server_admits(
    sent: tuple[dict[str, Any], dict[str, Any]], history: History
) -> None:
    binding, _ = sent
    model = ReleaseArtifactCommandModel.model_validate(binding)
    fields = model.model_dump()
    command = release_artifacts.ReleaseArtifactCommand(
        work_unit_id=uuid.uuid4(), actor=SYSTEM_ACTOR, **fields
    )
    release_artifacts._validate_command_shape(command)
    assert binding["kind"] == "container_image"
    assert binding["artifact_tag"] == f"sha-{history.built}"
    assert binding["summary"]["image"]["built_commit"] == history.built


def test_the_observation_payload_is_one_the_server_admits(
    sent: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, observation = sent
    model = DeploymentObservationCommandModel.model_validate(observation)
    fields = model.model_dump()
    command = deployment_observations.DeploymentObservationCommand(
        release_artifact_binding_id=uuid.UUID(BINDING_ID), actor=SYSTEM_ACTOR, **fields
    )
    command = deployment_observations._normalized_command(command)
    deployment_observations._validate_command_shape(command)
    assert observation["kind"] == "container_image"
    assert "dispatch_summary" not in observation
    assert observation["observed_at"] == NOW.isoformat()


def test_the_shape_check_is_live_and_would_refuse_a_wrong_payload(
    sent: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """The two tests above are not vacuous: the same validator refuses a payload one key off."""
    _, observation = sent
    broken = {**observation, "auth_summary": {"configured_m2m_status": 200}}
    fields = DeploymentObservationCommandModel.model_validate(broken).model_dump()
    command = deployment_observations.DeploymentObservationCommand(
        release_artifact_binding_id=uuid.UUID(BINDING_ID), actor=SYSTEM_ACTOR, **fields
    )
    with pytest.raises(DomainError):
        deployment_observations._validate_command_shape(command)


def test_a_dry_run_over_an_observed_unit_does_not_verify_it(history: History) -> None:
    system = FakeSystem(
        [
            candidate_row(
                history.merge, binding_id=BINDING_ID, binding_digest=DIGEST, observation_id="o"
            )
        ],
        existing=[{"id": "o", "environment": "production", "post_deploy_work_unit_id": "pd"}],
    )
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier, dry_run=True)

    assert summary["units"][0]["verification"]["outcome"] == DRY_RUN
    assert verifier.verified == []


@pytest.mark.parametrize(
    ("production_kwargs", "configured"),
    [({"ready_status": 503}, 200), ({}, 403)],
    ids=["ready-unavailable", "configured-m2m-refused"],
)
def test_any_failed_probe_makes_the_status_degraded(
    history: History, production_kwargs: dict[str, Any], configured: int
) -> None:
    system = FakeSystem([candidate_row(history.merge)], configured_status=configured)

    _run(history, system, production=FakeProduction(history.built, **production_kwargs))

    _, payload = system.observed[0]
    assert payload["status_summary"]["status"] == "degraded"
    assert payload["auth_summary"]["configured_m2m_status"] == configured


def test_a_verification_answer_outside_the_vocabulary_is_a_refusal(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    summary = _run(history, system, verifier=FakeVerifier(result="failed"))
    assert summary["units"][0]["verification"]["outcome"] == REFUSED
    assert has_findings(summary)


def test_a_healthy_production_says_so(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    _run(history, system)
    _, payload = system.observed[0]
    assert payload["status_summary"]["status"] == "healthy"
