"""The hosted observation's summaries cross a process boundary, so both halves are pinned here.

`image_release` composes the probe, route, auth and status summaries and `orchestrator` declares
their shapes, and neither may import the other (`tests/architecture` enforces it). The producer's
real composition -- `measure_production` through `observation_payload` -- is validated against the
route's model and then the service's own rules, so a key added, renamed or dropped on one side
alone reddens here rather than on the first live pass.

THE STATUS PINS make decision 2 of 2026-10-06 checkable: where the ingest shapes and the
verifier's evaluators disagreed, the evaluator's rule was taken. The producer's own idea of
"healthy" and of the two M2M answers must be the values those shapes and evaluators accept.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from image_release import release as image_release
from orchestrator.api.schemas.release import DeploymentObservationCommandModel
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.services.release import deployment_observations
from orchestrator.services.release.deployment_observations import AuthSummary
from orchestrator.services.verifier.verifier_evaluators import EVALUATORS
from tests.image_release.conftest import (
    BINDING_ID,
    FakeProduction,
    FakeSystem,
    release_for,
)

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
BUILT = "c" * 40


def _payload(**production: Any) -> dict[str, Any]:
    measured = image_release.measure_production(
        FakeProduction(BUILT, **production),
        FakeSystem([]),
        release_for(BUILT, previous="b" * 40),
        now=NOW,
    )
    return image_release.observation_payload(
        BINDING_ID, release_for(BUILT, previous="b" * 40), measured
    )


def _service_command(
    payload: dict[str, Any],
) -> deployment_observations.DeploymentObservationCommand:
    """The route's handoff, reproduced: the model's scalar fields plus its stored summaries."""
    model = DeploymentObservationCommandModel.model_validate(payload)
    summaries = model.stored_summaries()
    fields: dict[str, Any] = {**model.model_dump(exclude=set(summaries)), **summaries}
    return deployment_observations.DeploymentObservationCommand(
        release_artifact_binding_id=uuid.UUID(BINDING_ID),
        actor=ActorContext("system", ActorRole.SYSTEM),
        **fields,
    )


@pytest.mark.parametrize(
    "production",
    [{}, {"ready_status": 503}, {"paths": ("/health/live",)}],
    ids=["healthy", "ready-unavailable", "route-missing"],
)
def test_every_observation_the_producer_files_is_one_the_server_admits(
    production: dict[str, Any],
) -> None:
    """Healthy and degraded alike: a degraded probe is filed as it is, so it must be admitted."""
    payload = _payload(**production)

    command = _service_command(payload)
    deployment_observations._validate_command_shape(
        deployment_observations._normalized_command(command)
    )

    # Stored exactly as composed: the route's handoff adds no default and drops no key.
    for name in ("probe_summary", "route_summary", "auth_summary", "status_summary"):
        assert getattr(command, name) == payload[name]
    assert command.dispatch_summary == {} and command.activation_summary == {}


def test_the_producer_no_longer_sends_an_expected_status_range() -> None:
    probes = _payload()["probe_summary"]["probes"]
    assert all(
        set(probe) == {"name", "endpoint", "method", "observed_at", "status_code"}
        for probe in probes
    )


def test_the_route_refuses_the_retired_probe_fields() -> None:
    """The control that discriminates: the test above would pass on a model that tolerated them."""
    payload = _payload()
    payload["probe_summary"]["probes"][0]["expected_status_min"] = 200

    with pytest.raises(ValidationError) as raised:
        DeploymentObservationCommandModel.model_validate(payload)

    assert raised.value.errors()[0]["type"] == "extra_forbidden"


def test_the_producers_m2m_answers_are_the_ones_the_auth_shape_admits() -> None:
    fields = AuthSummary.model_fields
    assert get_args(fields["missing_m2m_status"].annotation) == (
        image_release.UNAUTHENTICATED_STATUS,
    )
    assert image_release.AUTHENTICATED_STATUS in get_args(
        get_args(fields["configured_m2m_status"].annotation)[0]
    )


def test_the_producers_healthy_range_is_the_evaluators() -> None:
    """The producer retries until every probe is within its range; the evaluator passes 2xx."""
    evaluate = EVALUATORS["production.health"]

    def verdict(status: int) -> str:
        return evaluate({"probes": [{"status_code": status}]})[0]

    assert verdict(image_release.HEALTHY_MIN) == "passed"
    assert verdict(image_release.HEALTHY_MAX) == "passed"
    assert verdict(image_release.HEALTHY_MIN - 1) == "failed"
    assert verdict(image_release.HEALTHY_MAX + 1) == "failed"


def test_an_unrecordable_configured_status_is_refused_by_the_service_as_well() -> None:
    """Why the producer refuses a non-200 authenticated read before writing anything."""
    command = _service_command(_payload())
    broken = {**command.auth_summary, "configured_m2m_status": 401}

    with pytest.raises(DomainError) as raised:
        deployment_observations._validate_command_shape(
            deployment_observations._normalized_command(replace(command, auth_summary=broken))
        )

    assert raised.value.code == "deployment_observation_invalid"
