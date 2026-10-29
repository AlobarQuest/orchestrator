"""A rotation's decomposition, generated from its intake (ADR-0055 amendment 15, increment 5).

The intakes are built from the packages the rotation proposer generates, so a generated package
decomposes for every type the SDS rotates; the envelopes are judged by the policy file's own
rotation pattern, so no unit needs an authority click.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from orchestrator.factory_policy import load_factory_policy
from orchestrator.kernel.authority import normalize_authority
from rotation_decomposer.decompose import DecomposeError, decomposition
from rotation_proposer.generate import plan_for, render
from rotation_worker.adapters import bws_machine_token
from rotation_worker.plans import criteria_steps
from rotation_worker.registry import RegistryEntry
from rotation_worker.types import CLASS_TYPES, TYPES

KEEPER_UUID = "11111111-1111-4111-8111-111111111111"
HELD = (
    'coolify-env-held instance="prod" resource_type="application" '
    'uuid="eqj5l7k705fhi12x9i74fqf0" key="EXAMPLE_TOKEN"'
)
HOSTED = (
    'coolify-env-hash instance="prod" resource_type="application" '
    'uuid="eqj5l7k705fhi12x9i74fqf0" key="ORCHESTRATOR_M2M_CREDENTIALS" entry="example"'
)


def _entry(credential_class: str, *consumers: dict[str, Any]) -> RegistryEntry:
    keeper: dict[str, Any] = (
        {"keeper_service": "Claude", "keeper_account": "BWS_ACCESS_TOKEN_X"}
        if TYPES[CLASS_TYPES[credential_class]].store_kind == "keychain"
        else {"bws_uuid": KEEPER_UUID}
    )
    credential = {
        "id": "example",
        "class": credential_class,
        "provider": "p",
        **keeper,
        "rotated_by_sds": True,
        "consumer": list(consumers),
    }
    return RegistryEntry("AlobarQuest/example-registry", "c" * 40, credential)


def _intake(entry: RegistryEntry, acceptance: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """An intake as `GET /package-intakes/{id}` answers it, for the package generated for it."""
    package = render(entry, "2026-10-29-requested", "2026-10-29T12:00:00Z")
    return {
        "package_id": package["package_id"],
        "revision": 2,
        "acceptance_criteria": [
            {"id": str(uuid.uuid4()), "ac_id": c["id"], "evidence": c["evidence"]}
            for c in (acceptance if acceptance is not None else package["acceptance"])
        ],
        "enforcement_snapshot": {"profile_fields": package["profile_fields"]},
    }


def _constraints(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {u["unit_key"]: u["authority"]["constraints"] for u in body["proposed_units"]}


@pytest.mark.parametrize("credential_class", sorted(CLASS_TYPES))
def test_every_type_decomposes_into_its_derived_plan_s_units(credential_class: str) -> None:
    entry = _entry(credential_class)
    intake = _intake(entry)
    body = decomposition(intake, entry)

    _, plan = plan_for(entry)
    steps = criteria_steps(plan)
    units = [u["authority"]["constraints"]["rotation_unit"] for u in body["proposed_units"]]
    assert units == list(dict.fromkeys(unit for unit, _ in steps.values()))
    # Every criterion maps once, to the unit holding its step.
    database_ids = {c["id"]: c["ac_id"] for c in intake["acceptance_criteria"]}
    mapped = {database_ids[m["ac_id"]]: m["unit_key"] for m in body["ac_mappings"]}
    assert mapped == {ac: f"example-{steps[ac][0]}" for ac in steps}
    assert len(body["ac_mappings"]) == len(steps) and body["retained_acs"] == []


@pytest.mark.parametrize("credential_class", sorted(CLASS_TYPES))
def test_each_unit_waits_only_on_the_one_before_it(credential_class: str) -> None:
    body = decomposition(_intake(_entry(credential_class)), _entry(credential_class))
    keys = [u["unit_key"] for u in body["proposed_units"]]
    assert body["dependencies"] == [
        {
            "source_unit_key": later,
            "kind": "work_unit",
            "target_unit_key": earlier,
            "required_state_or_condition": "completed",
        }
        for earlier, later in zip(keys, keys[1:], strict=False)
    ]


@pytest.mark.parametrize("credential_class", sorted(CLASS_TYPES))
def test_every_envelope_is_one_the_rotation_pattern_recognises(credential_class: str) -> None:
    """Kills: an envelope field the pattern doesn't name, which would put the authority click
    back on every unit."""
    entry = _entry(credential_class)
    intake = _intake(entry)
    reach = render(entry, "x", "2026-10-29T12:00:00Z")["reach"]
    policy = load_factory_policy()
    for unit in decomposition(intake, entry)["proposed_units"]:
        unit_id = uuid.uuid4()
        # The orchestrator adds the unit's own id when the decomposition is approved.
        authority = {
            **unit["authority"],
            "constraints": {**unit["authority"]["constraints"], "work_unit_id": str(unit_id)},
        }
        recognition = policy.authority_refusals(
            reach, normalize_authority(authority), unit_id, rotation_unit=True
        )
        assert recognition.refusals == (), (credential_class, unit["unit_key"])
        assert unit["required_capability"] == "operational_action"
        assert unit["max_attempts"] == 3
        assert "host_without_overlap" not in unit["authority"]["constraints"]


def test_a_unit_writing_no_hosted_service_says_so_and_only_such_a_unit() -> None:
    hosted = _entry(
        "orchestrator-m2m-bearer",
        {
            "kind": "coolify-env-hash",
            "instance": "prod",
            "resource_type": "application",
            "uuid": "eqj5l7k705fhi12x9i74fqf0",
            "key": "ORCHESTRATOR_M2M_CREDENTIALS",
            "entry": "example",
        },
    )
    assert not any(
        c.get("touches_no_hosted_service")
        for c in _constraints(decomposition(_intake(hosted), hosted)).values()
    )
    plain = _entry("openrouter-key")
    assert all(
        c.get("touches_no_hosted_service") is True
        for c in _constraints(decomposition(_intake(plain), plain)).values()
    )
    held = _entry(
        "github-pat-fine-grained",
        {
            "kind": "coolify-env-held",
            "instance": "prod",
            "resource_type": "application",
            "uuid": "eqj5l7k705fhi12x9i74fqf0",
            "key": "EXAMPLE_TOKEN",
        },
    )
    marked = {
        k: c.get("touches_no_hosted_service")
        for k, c in _constraints(decomposition(_intake(held), held)).items()
    }
    # Distribute writes the held copy and restarts its holder; retire touches only GitHub.
    assert marked == {"example-store": None, "example-retire": True}


def test_the_envelope_carries_the_package_s_destinations() -> None:
    entry = _entry("openrouter-key")
    intake = _intake(entry)
    listed = intake["enforcement_snapshot"]["profile_fields"]["destinations"]
    assert all(
        c["destinations"] == listed for c in _constraints(decomposition(intake, entry)).values()
    )


def test_a_package_on_a_pre_derivation_plan_is_refused_with_what_to_do() -> None:
    entry = _entry("bws-machine-token")
    pinned = [
        {
            "id": step.ac_id,
            "evidence": f"external: the {segment.name} unit's {step.kind.value} "
            "step's rotation-step/1 evidence, filed by the rotation executor.",
        }
        for segment in bws_machine_token.PLAN.segments
        for step in segment.steps
        if step.ac_id is not None
    ]
    with pytest.raises(DecomposeError, match="revise the package onto the derived plan"):
        decomposition(_intake(entry, pinned), entry)


def test_an_intake_for_another_credential_or_type_is_refused() -> None:
    entry = _entry("openrouter-key")
    intake = {**_intake(entry), "package_id": "rotation-someone-else"}
    with pytest.raises(DecomposeError, match="no standing rotation package of this credential"):
        decomposition(intake, entry)
    untyped = RegistryEntry(
        "o/r",
        "c",
        {"id": "example", "class": "openai-key", "bws_uuid": KEEPER_UUID, "rotated_by_sds": True},
    )
    with pytest.raises(DecomposeError, match="no type the SDS rotates"):
        decomposition(_intake(entry), untyped)


@pytest.mark.parametrize(
    "mangle",
    [
        lambda i: {**i, "acceptance_criteria": [{"ac_id": "AC-001"}]},
        lambda i: {**i, "enforcement_snapshot": {"profile_fields": {}}},
    ],
)
def test_an_intake_missing_what_the_decomposition_needs_is_refused(mangle: Any) -> None:
    entry = _entry("openrouter-key")
    with pytest.raises(DecomposeError):
        decomposition(mangle(_intake(entry)), entry)


def test_a_credential_the_registry_doesn_t_hand_over_is_refused() -> None:
    entry = _entry("openrouter-key")
    intake = _intake(entry)
    kept = RegistryEntry(
        entry.repository, entry.commit, {**entry.credential, "rotated_by_sds": False}
    )
    with pytest.raises(DecomposeError, match="rotated_by_sds"):
        decomposition(intake, kept)


def test_destinations_that_moved_since_the_package_was_approved_are_refused() -> None:
    """Kills: proposing envelopes the executor refuses at every unit, behind a write-once
    envelope that can't be corrected."""
    entry = _entry("openrouter-key")
    intake = _intake(entry)
    grown = _entry(
        "openrouter-key",
        {
            "kind": "runtime-fetch",
            "repo": "orchestrator",
            "ref": "scripts/run-new-lane.sh",
            "healthchecks_check": "new-lane",
        },
    )
    with pytest.raises(DecomposeError, match="differ from the registry's"):
        decomposition(intake, grown)
