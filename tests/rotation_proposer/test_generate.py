"""A standing rotation package generated from a registry row (ADR-0055 amendment 15, increment 4).

The fixture is held byte-identical with intent-packages' copy, whose `create-rotation` test
validates it: a change to what is rendered must still be a package intent-packages accepts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rotation_proposer.generate import (
    GenerateError,
    acceptance,
    authority,
    plan_for,
    reach,
    render,
)
from rotation_worker.adapters import ADAPTERS
from rotation_worker.plans import select_plan
from rotation_worker.registry import RegistryEntry
from rotation_worker.types import CLASS_TYPES, TYPES

FIXTURE = Path(__file__).parents[1] / "fixtures" / "rotation_package_generated.json"
KEEPER_UUID = "11111111-1111-4111-8111-111111111111"


def _entry(credential_class: str, **fields: Any) -> RegistryEntry:
    keeper: dict[str, Any] = (
        {"keeper_service": "Claude", "keeper_account": "BWS_ACCESS_TOKEN_X"}
        if TYPES[CLASS_TYPES[credential_class]].store_kind == "keychain"
        else {"bws_uuid": KEEPER_UUID}
    )
    credential = {"id": "example", "class": credential_class, "provider": "p", **keeper, **fields}
    return RegistryEntry("AlobarQuest/example-registry", "c" * 40, credential)


def test_the_rendered_package_is_the_one_intent_packages_validates() -> None:
    entry = RegistryEntry(
        "AlobarQuest/example-registry",
        "c" * 40,
        {
            "id": "example-pat",
            "class": "github-pat-fine-grained",
            "provider": "github",
            "provider_identity": "AlobarQuest (fine-grained PAT)",
            "bws_uuid": KEEPER_UUID,
            "rotated_by_sds": True,
            "consumer": [
                {
                    "kind": "gh-actions-secret",
                    "repo": "AlobarQuest/example-app",
                    "name": "EXAMPLE_TOKEN",
                }
            ],
        },
    )
    rendered = render(entry, "2026-10-28-requested", "2026-10-28T12:00:00Z")
    text = json.dumps(rendered, indent=2, sort_keys=True) + "\n"
    assert text == FIXTURE.read_text(encoding="utf-8")


@pytest.mark.parametrize("credential_class", sorted(CLASS_TYPES))
def test_every_type_s_criteria_describe_its_derived_plan(credential_class: str) -> None:
    # The executor runs the plan an approved package's criteria describe (`select_plan`), so a
    # generated package must describe the plan derived for its row, step for step.
    entry = _entry(credential_class)
    _, plan = plan_for(entry)
    assert select_plan((plan,), acceptance(plan)) is plan


def test_a_step_names_its_unit_only_where_its_kind_repeats() -> None:
    _, plan = plan_for(_entry("openrouter-key"))
    evidence = [criterion["evidence"] for criterion in acceptance(plan)]
    assert "external: the mint step's" in evidence[0]
    assert sum("unit's verify_consumers" in text for text in evidence) == 2
    assert not any("unit's mint" in text for text in evidence)


@pytest.mark.parametrize(
    ("credential_class", "expected_reach", "allowed"),
    [
        ("openrouter-key", ["external_system"], ["credential_create", "credential_revoke"]),
        ("github-pat-fine-grained", ["external_system"], ["credential_revoke"]),
        ("atlassian-api-token", ["external_system"], []),
        ("bws-machine-token", ["external_system", "operator_machine"], []),
        (
            "orchestrator-m2m-bearer",
            ["external_system", "live_estate"],
            ["credential_create", "credential_revoke", "infra_mutation"],
        ),
    ],
)
def test_reach_and_authority_follow_from_the_type(
    credential_class: str, expected_reach: list[str], allowed: list[str]
) -> None:
    adapter = CLASS_TYPES[credential_class]
    assert reach(adapter, []) == expected_reach
    granted = authority(adapter, [])
    assert set(granted["allowed"]) == {"secret_read", "secret_write", *allowed}
    assert set(granted["allowed"]).isdisjoint(granted["prohibited"])
    assert "repository_write" in granted["prohibited"]
    assert granted["budgets"] == {"max_attempts": 3, "max_llm_calls": None}


def test_a_class_the_sds_doesn_t_rotate_generates_nothing() -> None:
    entry = RegistryEntry(
        "o/r", "c" * 40, {"id": "x", "class": "openai-key", "bws_uuid": KEEPER_UUID}
    )
    with pytest.raises(GenerateError, match="no type the SDS rotates"):
        render(entry, "2026-10-28-requested", "2026-10-28T12:00:00Z")


def test_every_type_is_an_adapter_s_and_the_adapter_reads_it_from_there() -> None:
    for credential_class, adapter in CLASS_TYPES.items():
        assert adapter in TYPES, credential_class
    for name, declared in TYPES.items():
        assert ADAPTERS[name].capabilities == declared.capabilities, name
        assert ADAPTERS[name].store_kind == declared.store_kind, name


def test_the_package_carries_the_row_s_destinations_and_the_occurrence() -> None:
    entry = _entry("openrouter-key")
    rendered = render(entry, "2026-10-28-requested", "2026-10-28T12:00:00Z")
    fields = rendered["profile_fields"]
    assert fields["destinations"] == entry.destinations
    assert (fields["occurrence"], fields["credential_id"], fields["standing"]) == (
        "2026-10-28-requested",
        "example",
        True,
    )
    assert (rendered["package_id"], rendered["revision"], rendered["status"]) == (
        "rotation-example",
        1,
        "draft",
    )


def test_a_held_copy_puts_a_type_that_doesn_t_host_into_the_live_estate() -> None:
    """Distribute writes a held copy's Coolify setting and restarts its holder."""
    held = (
        'coolify-env-held instance="prod" resource_type="application" '
        'uuid="eqj5l7k705fhi12x9i74fqf0" key="EXAMPLE_TOKEN"'
    )
    assert reach("github-pat", [held]) == ["external_system", "live_estate"]
    assert "infra_mutation" in authority("github-pat", [held])["allowed"]
    assert "infra_mutation" in authority("github-pat", [])["prohibited"]
