"""The runner command cross-repo contract, orchestrator side.

factory-runner's `tests/test_orchestrator_command_contract.py` pins
`tests/fixtures/orchestrator_command_contract.json` -- the required and optional fields of the
three request bodies the runner POSTs (`EvidenceCommand`, `LifecycleCommand`, `PrBindingCommand`)
-- and checks its own payload builders against it. Until 2026-09-27 that was the only side: the
fixture says it is "generated from `orchestrator.api.schemas`", and nothing here compared the two.
So this repository could make a field required, the runner would keep sending payloads without
it, and every one would be a 422 in production with nothing red on either side. That is exactly
how the evidence seam first broke (2026-07-10: payloads missing `idempotency_key` and
`expected_version`, both required by `CommandBase`).

The fixture here is BYTE-IDENTICAL to factory-runner's and `CONTRACT_SHA256` is the same constant
there, hashed over the raw bytes as the runner hashes it. The hash alone proves only that a file is
unchanged, so the second test compares the fixture with what the SERVED routes accept: the request
body schema each runner-facing route actually declares in this application's OpenAPI document.
A field added, removed, or moved between required and optional here reds that test; change the
fixture and `CONTRACT_SHA256` in both repositories together, runner first.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.main import create_app

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "orchestrator_command_contract.json"
CONTRACT_SHA256 = "8efbb271ef09f6b1cba423e993af56cb0a762739fc06a160e63fb874bee45c3a"

# The route the runner POSTs each command body to (factory-runner `client.py`), and the schema
# name the fixture files it under.
RUNNER_ROUTES = {
    "EvidenceCommand": "/api/v1/work-units/{unit_id}/evidence",
    "LifecycleCommand": "/api/v1/work-units/{unit_id}/commands/{command}",
    "PrBindingCommand": "/api/v1/work-units/{unit_id}/pr-binding",
}


def golden_contract() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _served_fields(schema_name: str) -> tuple[set[str], set[str]]:
    """(required, optional) of the request body the served route actually declares."""
    document = create_app().openapi()
    body = document["paths"][RUNNER_ROUTES[schema_name]]["post"]["requestBody"]
    ref = body["content"]["application/json"]["schema"]["$ref"]
    served_name = ref.rsplit("/", 1)[-1]
    assert served_name == schema_name, (
        f"{RUNNER_ROUTES[schema_name]} now accepts {served_name}, not {schema_name}; the runner's "
        "fixture describes a body this route no longer takes"
    )
    schema = document["components"]["schemas"][served_name]
    required = set(schema.get("required", ()))
    return required, set(schema["properties"]) - required


def test_the_fixture_is_byte_identical_to_the_runner_s() -> None:
    """A one-sided edit here means factory-runner's copy has silently drifted."""
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == CONTRACT_SHA256


def test_the_fixture_names_exactly_the_runner_s_commands() -> None:
    assert set(golden_contract()) == set(RUNNER_ROUTES)


@pytest.mark.parametrize("schema_name", sorted(RUNNER_ROUTES))
def test_the_served_route_accepts_exactly_the_contracted_shape(schema_name: str) -> None:
    """The derivation half: what the application serves, not what a file contains."""
    contract = golden_contract()[schema_name]
    required, optional = _served_fields(schema_name)

    assert (required, optional) == (set(contract["required"]), set(contract["optional"])), (
        f"{schema_name} as served has drifted from the cross-repo fixture: "
        f"required {sorted(required)} vs {sorted(contract['required'])}, "
        f"optional {sorted(optional)} vs {sorted(contract['optional'])}. A new required field "
        "is a 422 on every runner call. Teach factory-runner first, advance the caller pin, then "
        "change both fixtures and CONTRACT_SHA256 together."
    )
