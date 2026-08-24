"""The producer's binding payload, validated against the model the ROUTE actually parses.

WHY THIS EXISTS, stated plainly because it is a defect this repository shipped to production.

`activation_sweep` is a separate program; `src/orchestrator` imports nothing from it and it
imports nothing back. So the payload it composes met the SERVICE's dataclass in every test --
`ReleaseArtifactCommand.expected_version` is `int | None = None` -- and never met
`ReleaseArtifactCommandModel`, where `CommandBase.expected_version` is `int = Field(ge=0)`:
required and non-nullable. The producer sent `None`. FastAPI answered **422 before any service
code ran**, so none of the service's named errors was reachable and none of its tests could see
it. Measured on the lane's first live pass: twelve candidates, twelve refusals, nothing written.

The lesson generalises past this field. A payload composed in one program and parsed in another
has a boundary no unit test crosses, and the request model is a SECOND set of rules on top of the
service's -- looser in some places (the registry three are optional here and conditional there)
and STRICTER in others. Tests may import both sides, so this is where the two are made to agree.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from activation_sweep.bind import Candidate, binding_payload
from orchestrator.api.schemas import ReleaseArtifactCommandModel

CANDIDATE_ROW: dict[str, Any] = {
    "work_unit_id": "eb7c36f7-4f7e-5d00-9709-779c0c1152a4",
    "work_package_revision_id": "11111111-2222-3333-4444-555555555555",
    "package_revision_hash": "sha256:package",
    "unit_key": "infraops-mcp-server-ac-001",
    "work_unit_version": 3,
    "source_repository": "AlobarQuest/infraops-mcp-server",
    "pr_number": 81,
    "source_commit": "fcc4f8811b51ea74293b79e16ddabc4250d00b41",
    "merge_commit": "ac01f838fdc96e2ce3916f5a2601d3e9c232c064",
    "binding_id": None,
}


def _payload(**overrides: Any) -> dict[str, Any]:
    row = {**CANDIDATE_ROW, **overrides}
    return binding_payload(
        Candidate.of(row),
        path="/Users/devon/Projects/infraops-mcp-server",
        head="6bb63470bdd3c3fa6df1feacf65ce25709590730",
        digest="sha256:" + "d" * 64,
    )


def test_the_producers_payload_parses_as_the_command_the_route_declares() -> None:
    """The whole point. A payload the route cannot parse never reaches the service at all."""
    command = ReleaseArtifactCommandModel.model_validate(_payload())

    assert command.kind == "machine_local"
    assert command.expected_version == CANDIDATE_ROW["work_unit_version"]
    assert command.merge_commit == CANDIDATE_ROW["merge_commit"]
    assert command.artifact_registry is None
    assert command.artifact_repository is None
    assert command.artifact_name is None


def test_a_version_of_zero_is_carried_rather_than_dropped() -> None:
    """0 is a real version and the only falsy one, so a truthiness test would drop it."""
    command = ReleaseArtifactCommandModel.model_validate(_payload(work_unit_version=0))

    assert command.expected_version == 0


def test_the_route_refuses_a_null_expected_version() -> None:
    """The control that discriminates. Without it the test above passes on a model that
    tolerates the very value that failed in production."""
    payload = _payload()
    payload["expected_version"] = None

    with pytest.raises(ValidationError) as raised:
        ReleaseArtifactCommandModel.model_validate(payload)

    assert any(error["loc"] == ("expected_version",) for error in raised.value.errors())


def test_a_candidate_without_a_version_is_a_narrowed_contract_rather_than_a_guess() -> None:
    row = {name: value for name, value in CANDIDATE_ROW.items() if name != "work_unit_version"}

    with pytest.raises(Exception) as raised:
        Candidate.of(row)

    assert "work_unit_version" in str(raised.value)
