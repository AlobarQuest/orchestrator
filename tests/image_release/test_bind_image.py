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

from image_release.client import ReleaseCallError
from image_release.release import (
    ALREADY_BOUND,
    ALREADY_OBSERVED,
    AUTH_POSTURE_UNRECORDABLE,
    BOUND,
    COMPLETED,
    DRY_RUN,
    EARLIER_IMAGE,
    MERGE_COMMIT_MISSING,
    NOT_CARRIED,
    OBSERVED,
    ORPHANED_BINDING,
    PROBE_ATTEMPTS,
    PROBE_INTERVAL_SECONDS,
    REFUSED,
    REVISION_MISMATCH,
    REVISION_REQUIRED,
    SHIPPED_EARLIER,
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


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _run(
    history: History,
    system: FakeSystem,
    *,
    production: FakeProduction | None = None,
    verifier: FakeVerifier | None = None,
    dry_run: bool = False,
    sleeps: Sleeps | None = None,
    built: str | None = None,
    previous: str | None = None,
) -> dict[str, Any]:
    return release_pass(
        release_for(built or history.built, previous=previous or history.previous),
        checkout=history.path,
        system=system,
        verifier=verifier if verifier is not None else FakeVerifier(),
        anonymous=production if production is not None else FakeProduction(history.built),
        dry_run=dry_run,
        now=NOW,
        sleep=sleeps if sleeps is not None else Sleeps(),
    )


def _bound_row(history: History, **overrides: Any) -> dict[str, Any]:
    return candidate_row(history.merge, binding_id=BINDING_ID, binding_digest=DIGEST, **overrides)


# ------------------------------------------------------------------------------------------------
# What is bound
# ------------------------------------------------------------------------------------------------


def test_binds_only_what_this_deploy_shipped(history: History) -> None:
    system = FakeSystem(
        [
            candidate_row(history.merge),
            candidate_row(history.later, unit_id=SECOND_UNIT_ID),
            candidate_row(history.shipped, unit_id="00000000-0000-4000-8000-000000000003"),
        ]
    )
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier)

    carried, later, shipped = summary["units"]
    assert carried["binding"] == {"outcome": BOUND, "binding_id": BINDING_ID}
    assert carried["observation"]["outcome"] == OBSERVED
    assert carried["verification"]["outcome"] == COMPLETED
    assert later["binding"]["outcome"] == NOT_CARRIED
    assert shipped["binding"]["outcome"] == SHIPPED_EARLIER
    assert "observation" not in later and "observation" not in shipped
    assert [unit for unit, _ in system.bound] == [carried["work_unit_id"]]
    assert [binding for binding, _ in system.observed] == [BINDING_ID]
    assert [unit for unit, _ in verifier.verified] == [POST_DEPLOY_UNIT_ID]
    assert not has_findings(summary) and not has_conditions(summary)


def test_the_built_commit_itself_is_carried(history: History) -> None:
    system = FakeSystem([candidate_row(history.built)])
    assert _run(history, system)["units"][0]["binding"]["outcome"] == BOUND


def test_the_previous_commit_itself_was_shipped_earlier(history: History) -> None:
    system = FakeSystem([candidate_row(history.previous)])
    summary = _run(history, system)
    assert summary["units"][0]["binding"]["outcome"] == SHIPPED_EARLIER
    assert system.bound == []


def test_a_merge_commit_the_checkout_never_held_is_a_condition(history: History) -> None:
    system = FakeSystem([candidate_row("c" * 40)])

    summary = _run(history, system)

    assert summary["units"][0]["binding"]["outcome"] == MERGE_COMMIT_MISSING
    assert "fetch and re-run" in summary["units"][0]["binding"]["reason"]
    assert system.bound == []
    assert has_conditions(summary) and not has_findings(summary)


@pytest.mark.parametrize("which", ["built", "previous"])
def test_a_checkout_without_the_built_or_previous_commit_refuses_the_whole_pass(
    history: History, which: str
) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    production = FakeProduction("d" * 40)

    unknown = "d" * 40
    if which == "built":
        summary = _run(history, system, production=production, built=unknown)
    else:
        summary = _run(history, system, production=production, previous=unknown)

    assert summary["unavailable"] is True
    assert which in summary["reason"]
    assert system.bound == [] and production.asked == []
    assert has_findings(summary)


# ------------------------------------------------------------------------------------------------
# What is already bound
# ------------------------------------------------------------------------------------------------


def test_a_unit_bound_to_this_digest_is_observed_and_not_rebound(history: History) -> None:
    system = FakeSystem([_bound_row(history)])

    summary = _run(history, system)

    unit = summary["units"][0]
    assert unit["binding"] == {"outcome": ALREADY_BOUND, "binding_id": BINDING_ID}
    assert unit["observation"]["outcome"] == OBSERVED
    assert system.bound == []
    assert [binding for binding, _ in system.observed] == [BINDING_ID]


def test_a_unit_bound_to_this_digest_whose_merge_commit_is_missing_says_fetch(
    history: History,
) -> None:
    """The same answer the unbound path gives: the checkout cannot say, so it is not a refusal."""
    system = FakeSystem([candidate_row("c" * 40, binding_id=BINDING_ID, binding_digest=DIGEST)])
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier)

    binding = summary["units"][0]["binding"]
    assert binding["outcome"] == MERGE_COMMIT_MISSING
    assert "fetch and re-run" in binding["reason"]
    assert system.observed == [] and verifier.verified == []
    assert has_conditions(summary) and not has_findings(summary)


def test_a_unit_bound_to_this_digest_that_the_build_does_not_carry_is_refused(
    history: History,
) -> None:
    system = FakeSystem(
        [candidate_row(history.later, binding_id=BINDING_ID, binding_digest=DIGEST)]
    )
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier)

    assert summary["units"][0]["binding"]["outcome"] == REFUSED
    assert system.observed == [] and verifier.verified == []
    assert has_findings(summary)


def test_an_existing_observation_is_found_and_its_unit_verified_without_refiling(
    history: History,
) -> None:
    """Observed and never verified -- an interrupted pass -- must not strand the unit."""
    system = FakeSystem(
        [_bound_row(history, observation_id="o")],
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


def test_an_observed_binding_to_another_digest_was_released_by_an_earlier_image(
    history: History,
) -> None:
    system = FakeSystem(
        [
            candidate_row(
                history.merge,
                binding_id=BINDING_ID,
                binding_digest=OTHER_DIGEST,
                observation_id="o",
            )
        ]
    )

    summary = _run(history, system)

    assert summary["units"][0]["binding"] == {
        "outcome": EARLIER_IMAGE,
        "binding_artifact_digest": OTHER_DIGEST,
    }
    assert system.bound == [] and system.observed == []
    assert not has_findings(summary) and not has_conditions(summary)


def test_an_unobserved_binding_to_another_digest_is_orphaned_and_surfaced(
    history: History,
) -> None:
    system = FakeSystem(
        [candidate_row(history.merge, binding_id=BINDING_ID, binding_digest=OTHER_DIGEST)]
    )

    summary = _run(history, system)

    assert summary["units"][0]["binding"]["outcome"] == ORPHANED_BINDING
    assert system.bound == [] and system.observed == []
    assert has_conditions(summary) and not has_findings(summary)


def test_a_bound_candidate_without_its_digest_is_a_narrowed_contract(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge, binding_id=BINDING_ID)])
    summary = _run(history, system)
    assert summary["units"][0]["binding"]["outcome"] == UNAVAILABLE
    assert has_findings(summary)


# ------------------------------------------------------------------------------------------------
# Production is confirmed before anything is written
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("served", ["later", None], ids=["another-commit", "unknown"])
def test_a_served_revision_that_is_not_the_build_writes_nothing(
    history: History, served: str | None
) -> None:
    system = FakeSystem([candidate_row(history.merge), _bound_row(history)])
    verifier = FakeVerifier()
    sleeps = Sleeps()
    revision = history.later if served == "later" else None

    summary = _run(
        history, system, production=FakeProduction(revision), verifier=verifier, sleeps=sleeps
    )

    assert summary["refusal"]["outcome"] == REVISION_MISMATCH
    assert summary["units"] == []
    assert system.bound == [] and system.observed == [] and verifier.verified == []
    assert summary["production"]["attempts"] == PROBE_ATTEMPTS
    assert sleeps.calls == [PROBE_INTERVAL_SECONDS] * (PROBE_ATTEMPTS - 1)
    assert has_conditions(summary) and not has_findings(summary)


def test_an_unauthenticated_read_that_was_not_refused_writes_nothing(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])

    summary = _run(history, system, production=FakeProduction(history.built, missing_status=200))

    assert summary["refusal"]["outcome"] == AUTH_POSTURE_UNRECORDABLE
    assert system.bound == [] and system.observed == []
    assert has_conditions(summary)


def test_a_swap_still_settling_is_retried_until_it_is_healthy(history: History) -> None:
    system = FakeSystem([candidate_row(history.merge)])
    sleeps = Sleeps()
    production = FakeProduction(history.built, settles_after=2)

    summary = _run(history, system, production=production, sleeps=sleeps)

    assert summary["production"]["attempts"] == 3
    assert summary["production"]["status"] == "healthy"
    assert len(sleeps.calls) == 2
    _, payload = system.observed[0]
    assert payload["status_summary"]["status"] == "healthy"


def test_a_healthy_first_probe_does_not_sleep(history: History) -> None:
    sleeps = Sleeps()
    summary = _run(history, FakeSystem([candidate_row(history.merge)]), sleeps=sleeps)
    assert summary["production"]["attempts"] == 1
    assert sleeps.calls == []


def test_a_production_still_degraded_after_every_retry_is_filed_as_it_is(
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

    assert summary["production"]["attempts"] == PROBE_ATTEMPTS
    _, payload = system.observed[0]
    assert payload["status_summary"]["status"] == "degraded"
    assert summary["units"][0]["verification"]["outcome"] == REVISION_REQUIRED
    assert has_conditions(summary)


@pytest.mark.parametrize(
    ("production_kwargs", "configured"),
    [({"ready_status": 503}, 200), ({}, 403), ({"paths": ("/health/live",)}, 200)],
    ids=["ready-unavailable", "configured-m2m-refused", "route-missing"],
)
def test_any_failed_probe_makes_the_status_degraded(
    history: History, production_kwargs: dict[str, Any], configured: int
) -> None:
    system = FakeSystem([candidate_row(history.merge)], configured_status=configured)

    _run(history, system, production=FakeProduction(history.built, **production_kwargs))

    _, payload = system.observed[0]
    assert payload["status_summary"]["status"] == "degraded"
    assert payload["auth_summary"]["configured_m2m_status"] == configured


def test_a_production_that_cannot_be_asked_is_unavailable_and_writes_nothing(
    history: History,
) -> None:
    class Unreachable(FakeProduction):
        def probe(self, path: str) -> Any:
            self.asked.append(path)
            raise ReleaseCallError("unreachable for GET /health/live: ConnectError")

    system = FakeSystem([candidate_row(history.merge)])
    production = Unreachable(history.built)

    summary = _run(history, system, production=production)

    assert summary["unavailable"] is True
    assert production.passes == PROBE_ATTEMPTS
    assert system.bound == []
    assert has_findings(summary)


def test_the_probe_runs_once_per_pass_when_healthy(history: History) -> None:
    system = FakeSystem(
        [candidate_row(history.merge), candidate_row(history.built, unit_id=SECOND_UNIT_ID)]
    )
    production = FakeProduction(history.built)
    _run(history, system, production=production)
    assert production.passes == 1
    assert len(system.observed) == 2


def test_no_candidates_is_quiet_and_probes_nothing(history: History) -> None:
    production = FakeProduction(history.built)
    summary = _run(history, FakeSystem([]), production=production)
    assert summary["units"] == [] and production.asked == []
    assert not has_findings(summary) and not has_conditions(summary)


# ------------------------------------------------------------------------------------------------
# Verification, dry runs, refusals
# ------------------------------------------------------------------------------------------------


def test_verification_is_asked_with_the_minted_version_and_a_stable_key(history: History) -> None:
    verifier = FakeVerifier()
    _run(history, FakeSystem([candidate_row(history.merge)]), verifier=verifier)
    [(unit_id, payload)] = verifier.verified
    assert payload == {"idempotency_key": f"image-verify:{unit_id}", "expected_version": 1}
    VerifyCommandModel.model_validate(payload)


def test_a_verification_answer_outside_the_vocabulary_is_a_refusal(history: History) -> None:
    summary = _run(
        history, FakeSystem([candidate_row(history.merge)]), verifier=FakeVerifier("failed")
    )
    assert summary["units"][0]["verification"]["outcome"] == REFUSED
    assert has_findings(summary)


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
    system = FakeSystem([_bound_row(history)])
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier, dry_run=True)

    assert summary["units"][0]["observation"]["record"]["idempotency_key"] == (
        f"image-observation:{BINDING_ID}:production"
    )
    assert system.observed == [] and verifier.verified == []


def test_a_dry_run_over_an_observed_unit_does_not_verify_it(history: History) -> None:
    system = FakeSystem(
        [_bound_row(history, observation_id="o")],
        existing=[{"id": "o", "environment": "production", "post_deploy_work_unit_id": "pd"}],
    )
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier, dry_run=True)

    assert summary["units"][0]["verification"]["outcome"] == DRY_RUN
    assert verifier.verified == []


def test_a_verifier_absent_reports_dry_run_rather_than_verifying(history: History) -> None:
    summary = release_pass(
        release_for(history.built, previous=history.previous),
        checkout=history.path,
        system=FakeSystem([candidate_row(history.merge)]),
        verifier=None,
        anonymous=FakeProduction(history.built),
        dry_run=False,
        now=NOW,
        sleep=Sleeps(),
    )
    assert summary["units"][0]["verification"]["outcome"] == DRY_RUN


def test_a_refused_binding_is_a_finding(history: History) -> None:
    class Refusing(FakeSystem):
        def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise ReleaseCallError("rejected POST x: 409 (release_artifact_conflict)")

    summary = _run(history, Refusing([candidate_row(history.merge)]))

    assert summary["units"][0]["binding"]["outcome"] == REFUSED
    assert has_findings(summary)


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
    fields = ReleaseArtifactCommandModel.model_validate(binding).model_dump()
    command = release_artifacts.ReleaseArtifactCommand(
        work_unit_id=uuid.uuid4(), actor=SYSTEM_ACTOR, **fields
    )
    release_artifacts._validate_command_shape(command)
    assert binding["kind"] == "container_image"
    assert binding["artifact_tag"] == f"sha-{history.built}"
    assert binding["summary"]["image"]["built_commit"] == history.built


def _observation_command(payload: dict[str, Any]) -> Any:
    fields = DeploymentObservationCommandModel.model_validate(payload).model_dump()
    command = deployment_observations.DeploymentObservationCommand(
        release_artifact_binding_id=uuid.UUID(BINDING_ID), actor=SYSTEM_ACTOR, **fields
    )
    return deployment_observations._normalized_command(command)


def test_the_observation_payload_is_one_the_server_admits(
    sent: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, observation = sent
    deployment_observations._validate_command_shape(_observation_command(observation))
    assert observation["kind"] == "container_image"
    assert "dispatch_summary" not in observation
    assert observation["observed_at"] == NOW.isoformat()


def test_the_shape_check_is_live_and_would_refuse_a_wrong_payload(
    sent: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """The two tests above are not vacuous: the same validator refuses a payload one key off."""
    _, observation = sent
    broken = {**observation, "auth_summary": {"configured_m2m_status": 200}}
    with pytest.raises(DomainError):
        deployment_observations._validate_command_shape(_observation_command(broken))


def test_a_redeploy_of_the_same_image_binds_nothing_and_replays_verification(
    history: History,
) -> None:
    """`--previous-commit` == `--built-commit`: everything was shipped by the image already
    running, and what that image bound is observed and replays its verification."""
    system = FakeSystem(
        [
            _bound_row(history, observation_id="o"),
            candidate_row(history.built, unit_id=SECOND_UNIT_ID),
        ],
        existing=[{"id": "o", "environment": "production", "post_deploy_work_unit_id": "pd"}],
    )
    verifier = FakeVerifier()

    summary = _run(history, system, verifier=verifier, previous=history.built)

    bound, fresh = summary["units"]
    assert bound["binding"]["outcome"] == ALREADY_BOUND
    assert bound["observation"]["outcome"] == ALREADY_OBSERVED
    assert bound["verification"]["outcome"] == COMPLETED
    assert fresh["binding"]["outcome"] == SHIPPED_EARLIER
    assert system.bound == [] and system.observed == []
    assert [unit for unit, _ in verifier.verified] == ["pd"]
    assert not has_findings(summary) and not has_conditions(summary)


def test_a_revision_that_arrives_on_a_later_attempt_is_bound_only_then(history: History) -> None:
    """The first reads see the outgoing image; nothing may be written from them."""

    class Swapping(FakeProduction):
        def probe(self, path: str) -> Any:
            if path == "/health/live":
                self.revision = history.built if self.passes >= 2 else history.previous
            return super().probe(path)

    events: list[str] = []

    class Recording(FakeSystem):
        def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            events.append(f"bind after {production.passes} probes")
            return super().bind(work_unit_id, payload)

    production = Swapping(None)
    system = Recording([candidate_row(history.merge)])

    summary = _run(history, system, production=production)

    assert summary["production"]["attempts"] == 3
    assert summary["production"]["served_revision"] == history.built
    assert events == ["bind after 3 probes"]
    [(_, observation)] = system.observed
    assert observation["status_summary"]["status"] == "healthy"
