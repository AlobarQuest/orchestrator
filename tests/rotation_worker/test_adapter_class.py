"""`check_unit`'s class term (`adapters.runs_on_class`, #826 concern 3): a unit's adapter is its
registry row's class type, or a fixed-plan name of that type.

The regression bar: every `rotated_by_sds` row in this repository's `.cred-consumers.toml`, and a
row of every class the SDS rotates, decomposes into envelopes whose adapter the term passes,
issue and reissue plans both; the fixed-plan names pass on their type's rows; and the unit open on
the host when the term landed (`secrets-broker-bws-token`'s `confirm`) passes it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from rotation_decomposer.decompose import decomposition
from rotation_worker.adapters import ADAPTERS, FIXED_PLAN_TYPES, fake, runs_on_class
from rotation_worker.registry import REGISTRY_PATH, RegistryEntry
from rotation_worker.types import CLASS_TYPES, TYPES
from tests.rotation_decomposer.test_decompose import CASES, _entry, _intake

ROOT = Path(__file__).resolve().parents[2]
OPEN_UNIT = ("secrets-broker-bws-token", "confirm")


def _handed_over() -> list[dict[str, Any]]:
    document = tomllib.loads((ROOT / REGISTRY_PATH).read_text())
    return [row for row in document["credential"] if row.get("rotated_by_sds") is True]


def _variants(credential_class: str) -> tuple[bool | None, ...]:
    capabilities = TYPES[CLASS_TYPES[credential_class]].capabilities
    if not capabilities.issues:
        return (None,)
    return (True, False) if capabilities.rotates else (True,)


def _envelopes(entry: RegistryEntry, first_issue: bool | None) -> list[dict[str, Any]]:
    body = decomposition(_intake(entry, first_issue=first_issue), entry)
    return [unit["authority"]["constraints"] for unit in body["proposed_units"]]


def _typed_rows() -> list[tuple[str, bool | None]]:
    return [
        (str(row["id"]), first_issue)
        for row in _handed_over()
        if row.get("class") in CLASS_TYPES
        for first_issue in _variants(str(row["class"]))
    ]


@pytest.mark.parametrize(("credential_id", "first_issue"), _typed_rows())
def test_every_handed_over_row_s_decomposed_envelopes_pass(
    credential_id: str, first_issue: bool | None
) -> None:
    (row,) = [row for row in _handed_over() if row["id"] == credential_id]
    entry = RegistryEntry("AlobarQuest/orchestrator", "c" * 40, row)
    envelopes = _envelopes(entry, first_issue)
    assert envelopes
    for constraints in envelopes:
        assert runs_on_class(constraints["rotation_adapter"], row["class"])


@pytest.mark.parametrize(("credential_class", "first_issue"), CASES)
def test_every_class_s_decomposed_envelopes_pass(
    credential_class: str, first_issue: bool | None
) -> None:
    for constraints in _envelopes(_entry(credential_class), first_issue):
        assert runs_on_class(constraints["rotation_adapter"], credential_class)


def test_the_only_untyped_handed_over_row_is_the_canary_and_the_fake_adapter_runs_it() -> None:
    untyped = [row for row in _handed_over() if row.get("class") not in CLASS_TYPES]
    assert [(row["id"], row["class"]) for row in untyped] == [("rotation-canary", fake.CLASS)]
    assert runs_on_class(fake.NAME, fake.CLASS)


def test_the_unit_open_on_the_host_passes() -> None:
    credential_id, unit = OPEN_UNIT
    (row,) = [row for row in _handed_over() if row["id"] == credential_id]
    entry = RegistryEntry("AlobarQuest/orchestrator", "c" * 40, row)
    (constraints,) = [c for c in _envelopes(entry, False) if c["rotation_unit"] == unit]
    assert runs_on_class(constraints["rotation_adapter"], row["class"])


@pytest.mark.parametrize("name", sorted(FIXED_PLAN_TYPES))
def test_a_fixed_plan_name_runs_on_its_type_s_class_alone(name: str) -> None:
    (own,) = [c for c, t in CLASS_TYPES.items() if t == FIXED_PLAN_TYPES[name]]
    assert runs_on_class(name, own)
    assert not any(runs_on_class(name, c) for c in CLASS_TYPES if c != own)
    assert not runs_on_class(name, fake.CLASS)


def test_every_adapter_is_a_type_a_fixed_plan_of_one_or_the_canary_s() -> None:
    assert set(ADAPTERS) == set(TYPES) | set(FIXED_PLAN_TYPES) | {fake.NAME}
    assert set(FIXED_PLAN_TYPES.values()) <= set(TYPES)


@pytest.mark.parametrize("name", sorted(set(ADAPTERS) - {fake.NAME}))
def test_no_adapter_but_fake_runs_an_untyped_class(name: str) -> None:
    assert not runs_on_class(name, fake.CLASS)
    assert not runs_on_class(name, None)
    assert not runs_on_class(name, "unknown-class")


def test_the_fake_adapter_runs_no_typed_class() -> None:
    assert not any(runs_on_class(fake.NAME, c) for c in CLASS_TYPES)


def test_the_two_github_types_share_a_provider_and_not_a_class() -> None:
    """The case the term exists for: the provider comparison passes both, the class tells them
    apart."""
    assert ADAPTERS["github-pat"].provider_name == ADAPTERS["github-app-key"].provider_name
    assert not runs_on_class("github-pat", "github-app-private-key")
    assert not runs_on_class("github-app-key", "github-pat-fine-grained")
