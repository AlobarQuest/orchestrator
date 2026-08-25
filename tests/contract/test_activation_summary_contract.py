"""The activation summary crosses a process boundary, so both halves are pinned here.

`activation_sweep` composes the summary and `orchestrator` validates it, and neither may import
the other — `tests/architecture` enforces that separation, which is why the producer carries a
transcription rather than a shared constant. A rename on one side alone would have the
orchestrator refuse every observation this lane files.

TWO SEPARATE PINS, because they fail in opposite directions and neither implies the other. The
VOCABULARY pin catches a rename: the fact names and the tri-state values must agree. The PAYLOAD
pin catches the shape the ROUTE parses, which is a second rule set on top of the service's — the
binding lane's first live pass refused all twelve candidates with HTTP 422, before any service
code ran, because the producer sent `None` for a required `expected_version`. Tests may import
both sides; production cannot.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from activation_sweep import activation
from activation_sweep.bind import MACHINE_LOCAL_KIND, activation_payload
from orchestrator.api.schemas import DeploymentObservationCommandModel
from orchestrator.persistence.models import (
    MACHINE_LOCAL_OBSERVATION,
    OPERATOR_MACHINE_ENVIRONMENT,
)
from orchestrator.services.deployment_observations import (
    ACTIVATION_FACTS,
    ACTIVATION_RESULTS,
)

UNIT_ID = "eb7c36f7-4f7e-5d00-9709-779c0c1152a4"
HEAD = "6bb63470bdd3c3fa6df1feacf65ce25709590730"
HEAD_COMMITTED_AT = "2026-08-23T10:34:10+00:00"
DIGEST = "sha256:" + "d" * 64


def _facts(**overrides: str) -> activation.ActivationFacts:
    return activation.ActivationFacts.of(
        activation.RepositoryFacts(
            console_entry_points_present=overrides.get(
                "console_entry_points_present", activation.YES
            ),
            environment_matches_lock=overrides.get(
                "environment_matches_lock", activation.NOT_APPLICABLE
            ),
        ),
        merge_commit_present=overrides.get("merge_commit_present", activation.YES),
    )


def _payload(**overrides: str) -> dict[str, Any]:
    return activation_payload(
        work_unit_id=UNIT_ID,
        digest=DIGEST,
        head_committed_at=HEAD_COMMITTED_AT,
        head=HEAD,
        facts=_facts(**overrides),
    )


def test_the_producer_and_the_validator_name_the_same_facts() -> None:
    assert activation.ACTIVATION_FACTS == ACTIVATION_FACTS


def test_the_producer_and_the_validator_share_one_result_vocabulary() -> None:
    assert activation.ACTIVATION_RESULTS == ACTIVATION_RESULTS


def test_the_producer_and_the_validator_agree_on_the_kind_and_the_environment() -> None:
    assert MACHINE_LOCAL_KIND == MACHINE_LOCAL_OBSERVATION
    assert activation.OPERATOR_MACHINE_ENVIRONMENT == OPERATOR_MACHINE_ENVIRONMENT


def test_the_producers_activation_payload_parses_as_the_command_the_route_declares() -> None:
    """The whole point. A payload the route cannot parse never reaches the service at all."""
    command = DeploymentObservationCommandModel.model_validate(_payload())

    assert command.kind == MACHINE_LOCAL_KIND
    assert command.environment == OPERATOR_MACHINE_ENVIRONMENT
    assert command.observed_artifact_digest == DIGEST
    assert command.base_url is None
    assert command.deployment_url is None
    assert command.deployer is None
    assert command.probe_summary == {}
    assert set(command.activation_summary) == set(ACTIVATION_FACTS)


def test_the_route_refuses_an_activation_payload_with_no_expected_version() -> None:
    """The control that discriminates. Without it the test above passes on a model that
    tolerates the very omission that cost the sibling lane a whole live pass."""
    payload = _payload()
    del payload["expected_version"]

    with pytest.raises(ValidationError) as raised:
        DeploymentObservationCommandModel.model_validate(payload)

    assert any(error["loc"] == ("expected_version",) for error in raised.value.errors())


def test_every_fact_the_producer_can_emit_is_a_value_the_validator_accepts() -> None:
    """Each fact, at each of its permitted results, through the real composition."""
    for fact in ACTIVATION_FACTS:
        for result in ACTIVATION_RESULTS:
            if fact == "merge_commit_present" and result == activation.NOT_APPLICABLE:
                continue  # every working copy either holds the commit or does not
            command = DeploymentObservationCommandModel.model_validate(_payload(**{fact: result}))
            assert command.activation_summary[fact] == result
