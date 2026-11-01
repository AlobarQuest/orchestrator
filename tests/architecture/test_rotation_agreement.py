"""Copies that must agree across the rotation lanes and with infraops.

`touches_no_hosted_service` is spelled three times, because the executor and the decomposer may not
import the orchestrator, which spells it twice. The proposer's unit states are `WorkUnitState`'s,
for the same reason. `CLASS_TYPES` names infraops' classes:
`tests/fixtures/rotation_class_policy.json` is a byte-identical copy of the file of the same name
in infraops-mcp-server, which that repository's test generates from `CLASS_POLICY`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from orchestrator import factory_policy
from orchestrator.kernel.states import WorkUnitState
from orchestrator.services.lifecycle import rotation_claims
from rotation_proposer import cli
from rotation_worker import envelope
from rotation_worker.types import CLASS_TYPES

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rotation_class_policy.json"

# Classes infraops knows and the SDS deliberately has no type for: nothing is generated for them.
UNTYPED: Final = frozenset(
    {
        "anthropic-api-key",
        "brain-mcp-key",
        "coolify-api-token",
        "coolify-pg-password",
        "github-app-private-key",
        "github-pat-classic",
        "openai-key",
        "porkbun-api-key",
        "rotation-canary",
    }
)


def test_every_spelling_of_the_non_hosted_marker_is_equal() -> None:
    assert (
        envelope.NON_HOSTED_KEY
        == rotation_claims.NON_HOSTED_CONSTRAINT
        == factory_policy._ROTATION_NON_HOSTED_FIELD
    )


def test_the_proposers_unit_states_are_the_orchestrators() -> None:
    assert cli.STALLED_STATES == {WorkUnitState.FAILED.value, WorkUnitState.CANCELLED.value}
    assert cli.COMPLETED_STATE == WorkUnitState.COMPLETED.value


def test_every_type_names_a_class_infraops_knows() -> None:
    assert set(CLASS_TYPES) <= set(json.loads(FIXTURE.read_text()))


def test_every_class_infraops_knows_is_typed_or_deliberately_not() -> None:
    assert set(json.loads(FIXTURE.read_text())) == set(CLASS_TYPES) | UNTYPED


def test_no_class_is_both_typed_and_deliberately_untyped() -> None:
    assert not set(CLASS_TYPES) & UNTYPED
