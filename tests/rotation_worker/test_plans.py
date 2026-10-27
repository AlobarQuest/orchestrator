"""A credential type's plan, derived from its capabilities and its registry row (ADR-0055
amendment 15): it reproduces every plan the approved packages pin, and the approved package's
criteria choose which plan runs."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from rotation_worker.adapters import (
    ADAPTERS,
    bitbucket,
    bws_machine_token,
    change_manager,
    github_pat,
    openrouter,
    orchestrator_bearer,
)
from rotation_worker.checks import _plan
from rotation_worker.contract import ContractError, HumanAct, Plan, StepKind, UnitPlan
from rotation_worker.plans import Capabilities, derive, packaged_steps, select_plan
from rotation_worker.registry import RegistryEntry
from tests.rotation_worker.stores import MemoryStore

KEEPER = 'bws-secret uuid="11111111-2222-4333-8444-555555555555"'
COPY = 'bws-secret uuid="66666666-7777-4888-8999-aaaaaaaaaaaa"'
KEYCHAIN_KEEPER = 'keychain service="bws" account="ops"'
KEYCHAIN_COPY = 'keychain service="bws" account="ops-copy"'
ACTIONS = 'gh-actions-secret repo="AlobarQuest/x" name="FACTORY_PR_TOKEN"'
HELD = 'coolify-env-held instance="prod" resource_type="application" uuid="u" key="CM_TOKEN"'
HOSTED = (
    'coolify-env-hash instance="prod" resource_type="application" uuid="u" '
    'key="ORCHESTRATOR_M2M_CREDENTIALS" entry="orchestrator-operator"'
)
READER = 'runtime-fetch repo="orchestrator" ref="scripts/run-x.sh"'
# The unit names `factory-policy.toml`'s rotation patterns recognise.
POLICY_UNITS = {"confirm", "mint", "retire", "store", "verify"}


def _capabilities(name: str) -> Capabilities:
    capabilities = ADAPTERS[name].capabilities
    assert capabilities is not None
    return capabilities


@pytest.mark.parametrize(
    ("adapter", "destinations", "keeper", "store_kind", "pinned"),
    [
        ("openrouter", [KEEPER, 'provider name="openrouter"'], KEEPER, "bws-secret",
         openrouter.PLAN),
        ("orchestrator", [KEEPER, HOSTED, READER], KEEPER, "bws-secret", orchestrator_bearer.PLAN),
        ("orchestrator", [KEEPER, HOSTED, ACTIONS], KEEPER, "bws-secret",
         orchestrator_bearer.DISTRIBUTED_PLAN),
        ("change-manager", [KEEPER, HOSTED, READER], KEEPER, "bws-secret", change_manager.PLAN),
        ("change-manager", [KEEPER, HOSTED, HELD], KEEPER, "bws-secret",
         change_manager.DISTRIBUTED_PLAN),
        ("github-pat", [KEEPER, ACTIONS, READER], KEEPER, "bws-secret", github_pat.PLAN),
        ("bitbucket", [KEEPER, READER], KEEPER, "bws-secret", bitbucket.PLAN),
        ("bws-machine-token", [KEYCHAIN_KEEPER, KEYCHAIN_COPY], KEYCHAIN_KEEPER, "keychain",
         bws_machine_token.PLAN),
    ],
)  # fmt: skip
def test_the_derived_plan_is_the_plan_a_package_pins_for_that_shape(
    adapter: str, destinations: list[str], keeper: str, store_kind: str, pinned: Plan
) -> None:
    assert derive(_capabilities(adapter), destinations, keeper, store_kind) == pinned


@pytest.mark.parametrize(
    ("destination", "distributes"),
    [
        (ACTIONS, True),
        (HELD, True),
        (COPY, True),
        (READER, False),
        (KEYCHAIN_COPY, False),
        ('provider name="github"', False),
    ],
)
def test_distribute_runs_only_when_a_copy_beside_the_keeper_is_written(
    destination: str, distributes: bool
) -> None:
    plan = derive(_capabilities("github-pat"), [KEEPER, destination], KEEPER, "bws-secret")
    assert (StepKind.DISTRIBUTE in {step.kind for step in plan.steps()}) is distributes


def test_the_keeper_alone_is_never_distributed_to() -> None:
    plan = derive(
        _capabilities("bws-machine-token"), [KEYCHAIN_KEEPER], KEYCHAIN_KEEPER, "keychain"
    )
    assert StepKind.DISTRIBUTE not in {step.kind for step in plan.steps()}


@pytest.mark.parametrize(
    ("mint", "revoke", "hosted", "distributes"), list(itertools.product([True, False], repeat=4))
)
def test_every_shape_is_a_valid_plan_the_rotation_policy_recognises(
    mint: bool, revoke: bool, hosted: bool, distributes: bool
) -> None:
    destinations = [KEEPER, ACTIONS] if distributes else [KEEPER]
    plan = derive(Capabilities(mint, revoke, hosted), destinations, KEEPER, "bws-secret")
    units = [segment for segment in plan.segments if isinstance(segment, UnitPlan)]
    acts = [segment for segment in plan.segments if isinstance(segment, HumanAct)]
    assert {unit.name for unit in units} <= POLICY_UNITS
    criteria = [step.ac_id for step in plan.steps() if step.ac_id is not None]
    assert criteria == [f"AC-{n:03d}" for n in range(1, len(criteria) + 1)]
    # A person's acts are only minting and revoking.
    expected_acts = {
        name for name, by_machine in (("mint", mint), ("revoke", revoke)) if not by_machine
    }
    assert {act.name for act in acts} == expected_acts
    kinds = [step.kind for step in plan.steps()]
    assert (StepKind.HOST in kinds) is hosted
    assert kinds[-1] is StepKind.CONFIRM_DEAD


def _criteria(plan: Plan, **overrides: str) -> list[dict[str, Any]]:
    return [
        {
            "id": step.ac_id,
            "evidence": overrides.get(
                str(step.ac_id),
                f"external: the {step.kind} step's rotation-step/1 evidence, "
                "filed by the rotation executor.",
            ),
        }
        for step in plan.steps()
        if step.ac_id is not None
    ]


def test_the_package_s_criteria_choose_the_derived_plan_or_the_one_it_was_approved_with() -> None:
    derived = derive(_capabilities("github-pat"), [KEEPER, READER], KEEPER, "bws-secret")
    assert derived != github_pat.PLAN
    candidates = (derived, github_pat.PLAN)
    assert select_plan(candidates, _criteria(derived)) is derived
    assert select_plan(candidates, _criteria(github_pat.PLAN)) is github_pat.PLAN


def test_a_unit_s_name_in_the_evidence_doesn_t_change_the_step_it_covers() -> None:
    worded = _criteria(
        bitbucket.PLAN,
        **{
            "AC-001": "external: the store unit's verify_new step's rotation-step/1 evidence, "
            "filed by the rotation executor."
        },
    )
    assert select_plan((bitbucket.PLAN,), worded) is bitbucket.PLAN


@pytest.mark.parametrize(
    "acceptance",
    [
        None,
        [],
        "AC-001",
        ["AC-001"],
        _criteria(bitbucket.PLAN)[:-1],
        [*_criteria(bitbucket.PLAN), {"id": "AC-005", "evidence": "a person reads the log"}],
        [*_criteria(bitbucket.PLAN), "AC-005"],
        [*_criteria(bitbucket.PLAN), _criteria(bitbucket.PLAN)[0]],
        [{**row, "id": None} for row in _criteria(bitbucket.PLAN)],
        _criteria(bitbucket.PLAN, **{"AC-001": "external: the confirm unit's verify_new step's "
                                     "rotation-step/1 evidence, filed by the rotation executor."}),
        _criteria(bitbucket.PLAN, **{"AC-002": "external: the mint step's rotation-step/1 "
                                     "evidence, filed by the rotation executor."}),
        _criteria(bitbucket.PLAN, **{"AC-002": "external: the shred step's rotation-step/1 "
                                     "evidence, filed by the rotation executor."}),
    ],
)  # fmt: skip
def test_a_package_whose_criteria_describe_no_candidate_is_refused(acceptance: Any) -> None:
    with pytest.raises(ContractError, match="describe no plan this type runs"):
        select_plan((bitbucket.PLAN,), acceptance)


def test_a_criterion_that_isn_t_a_rotation_step_s_describes_nothing() -> None:
    assert packaged_steps([{"id": "AC-001", "evidence": "external: a person reads it."}]) is None


def _entry(provider: str, destinations: list[str]) -> RegistryEntry:
    consumers = [{"kind": "bws-secret", "uuid": KEEPER.split('"')[1]}]
    if READER in destinations:
        consumers.append(
            {"kind": "runtime-fetch", "repo": "orchestrator", "ref": "scripts/run-x.sh"}
        )
    credential = {"id": "x", "provider": provider, "bws_uuid": KEEPER.split('"')[1]}
    return RegistryEntry(
        "AlobarQuest/orchestrator", "c" * 40, {**credential, "consumer": consumers}
    )


def test_a_credential_type_runs_its_derived_plan_and_a_fixed_name_its_one_plan() -> None:
    entry = _entry("github", [KEEPER, READER])
    derived = derive(_capabilities("github-pat"), entry.destinations, entry.keeper, "bws-secret")
    store = MemoryStore()
    assert _plan(ADAPTERS["github-pat"], entry, store, _criteria(derived)) == derived
    assert _plan(ADAPTERS["github-pat"], entry, store, _criteria(github_pat.PLAN)) is (
        github_pat.PLAN
    )
    fixed = ADAPTERS[change_manager.DISTRIBUTED_NAME]
    assert fixed.capabilities is None
    assert _plan(fixed, entry, store, None) is change_manager.DISTRIBUTED_PLAN


def test_the_wait_before_a_person_s_revoke_reads_the_plan_that_runs() -> None:
    from types import SimpleNamespace
    from typing import cast

    from rotation_worker.checks import Approved
    from rotation_worker.worker import _gates_retire

    derived = derive(
        _capabilities("bws-machine-token"), [KEYCHAIN_KEEPER], KEYCHAIN_KEEPER, "keychain"
    )
    assert derived != bws_machine_token.PLAN

    def gates(unit: str) -> bool:
        approved = SimpleNamespace(
            adapter=ADAPTERS["bws-machine-token"], plan=derived, unit=derived.unit(unit)
        )
        return _gates_retire(cast(Approved, approved))

    assert gates("verify")
    assert not gates("store")
    assert not gates("confirm")


@pytest.mark.parametrize("name", [n for n, a in ADAPTERS.items() if a.capabilities is not None])
@pytest.mark.parametrize("extra", [[], [ACTIONS], [HELD], [COPY], [KEYCHAIN_COPY]])
def test_a_type_s_derived_plan_has_its_pinned_plan_s_units_in_order(
    name: str, extra: list[str]
) -> None:
    """`overlaps._from_store` places a unit against the store from `Adapter.plan`, so the plan a
    credential runs must have the same units, in the same order."""
    adapter = ADAPTERS[name]
    keeper = KEYCHAIN_KEEPER if adapter.store_kind == "keychain" else KEEPER
    plan = derive(_capabilities(name), [keeper, *extra], keeper, adapter.store_kind)
    assert [s.name for s in plan.segments] == [s.name for s in adapter.plan.segments]
