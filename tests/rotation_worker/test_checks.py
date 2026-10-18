"""The checks before acting: each one refuses when what it checks is wrong, and passes otherwise."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from rotation_worker.canonical import CanonicalError, package_hash
from rotation_worker.checks import check_unit
from rotation_worker.contract import ContractError
from rotation_worker.envelope import is_rotation_unit
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.readers import CheckRead
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    REGISTRY_PATH,
    REGISTRY_REPOSITORIES,
    RegistryError,
)
from tests.rotation_worker.conftest import Origins

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
KEEPER = "11111111-2222-4333-8444-555555555555"
RUNTIME = 'runtime-fetch repo="orchestrator" ref="a person attests"'
DESTINATIONS = sorted([f'bws-secret uuid="{KEEPER}"', 'provider name="fake"', RUNTIME])
PATH = "packages/rotation-canary/package.yaml"
LINEAGE = "packages/rotation-canary/lineage.yaml"
REVISION = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
WORK = "99999999-0000-4000-8000-000000000000"


def _registry(**changes: str) -> str:
    entry = {
        "provider": '"fake"',
        "bws_uuid": f'"{KEEPER}"',
        "rotated_by_sds": "true",
        **changes,
    }
    fields = "\n".join(f"{key} = {value}" for key, value in entry.items())
    return textwrap.dedent(
        f"""
        [[credential]]
        id = "canary"
        class = "rotation-canary"
        {fields}

          [[credential.consumer]]
          kind = "bws-secret"
          uuid = "{KEEPER}"

          [[credential.consumer]]
          kind = "runtime-fetch"
          repo = "orchestrator"
          ref = "a person attests"
        """
    ).replace("\n        ", "\n")


def _package(**fields: Any) -> dict[str, Any]:
    profile_fields = {
        "standing": True,
        "credential_id": "canary",
        "occurrence": "2026-10-11-requested",
        "destinations": DESTINATIONS,
        **fields,
    }
    return {"profile": "non-software-operational", "profile_fields": profile_fields}


class World:
    def __init__(self, origins: Origins, mirror: RepoMirror) -> None:
        self.origins = origins
        self.mirror = mirror
        for repository in REGISTRY_REPOSITORIES:
            canary = _registry() if repository.endswith("/orchestrator") else ""
            origins.commit(repository, {REGISTRY_PATH: "version = 1\n" + canary})
        self.publish(_package())

    def publish(self, package: dict[str, Any], *, approve: bool = True) -> None:
        """Publish a revision; approved, its hash becomes the lineage's latest approval."""
        files = {PATH: yaml.safe_dump(package)}
        if approve:
            self.package = package
            approval = {"revision": 1, "approved_hash": package_hash(package), "approver": "devon"}
            files[LINEAGE] = yaml.safe_dump({"approvals": [approval]})
        commit = self.origins.commit(PACKAGES_REPOSITORY, files)
        if approve:
            self.commit = commit

    def registry(self, text: str) -> None:
        self.origins.commit(REGISTRY_REPOSITORIES[1], {REGISTRY_PATH: "version = 1\n" + text})

    def unit(self, **constraints: Any) -> dict[str, Any]:
        merged = {
            "rotation_adapter": "fake",
            "rotation_unit": "store",
            "destinations": DESTINATIONS,
            "touches_no_hosted_service": True,
            **constraints,
        }
        return {
            "id": "unit-1",
            "work_package_revision_id": REVISION,
            "authority": {"constraints": {k: v for k, v in merged.items() if v is not None}},
        }

    def intake(self, **changes: Any) -> dict[str, Any]:
        return {
            "id": REVISION,
            "source_repository": PACKAGES_REPOSITORY,
            "source_commit": self.commit,
            "package_id": "rotation-canary",
            "source_path": "/Users/someone/intent-packages/packages/rotation-canary",
            "content_hash": package_hash(self.package),
            "acceptance_criteria": [{"ac_id": f"AC-00{n}"} for n in range(1, 9)],
            **changes,
        }

    def check(self, unit: dict[str, Any] | None = None, intake: dict[str, Any] | None = None):
        return check_unit(unit or self.unit(), intake or self.intake(), self.mirror, WORK)


@pytest.fixture
def world(origins: Origins, mirror: RepoMirror) -> World:
    return World(origins, mirror)


def test_an_approved_unit_passes_and_names_what_it_may_do(world: World) -> None:
    approved = world.check()
    assert approved.unit.name == "store"
    assert approved.rotation.keeper_id == KEEPER
    assert approved.rotation.occurrence == "2026-10-11-requested"
    assert approved.rotation.destinations == tuple(DESTINATIONS)
    assert approved.rotation.work_project_id == WORK


def test_only_a_unit_naming_an_adapter_is_a_rotation_unit(world: World) -> None:
    assert is_rotation_unit(world.unit())
    assert not is_rotation_unit(world.unit(rotation_adapter=None))
    assert not is_rotation_unit({"authority": {"constraints": "not a mapping"}})


Case = Callable[[World], tuple[dict[str, Any] | None, dict[str, Any] | None]]
REFUSALS: dict[str, tuple[Case, str]] = {
    "another revision's intake": (
        lambda w: (None, w.intake(id="ffffffff-bbbb-4ccc-8ddd-eeeeeeeeeeee")),
        "isn't the unit's",
    ),
    "an unknown adapter": (lambda w: (w.unit(rotation_adapter="nope"), None), "no adapter"),
    "an unknown unit": (lambda w: (w.unit(rotation_unit="nope"), None), "no unit named"),
    "a human act named as a unit": (
        lambda w: (w.unit(rotation_unit="attest"), None),
        "no unit named",
    ),
    "a step's criterion missing": (
        lambda w: (None, w.intake(acceptance_criteria=[{"ac_id": "AC-004"}])),
        "no AC-005",
    ),
    "a revision from elsewhere": (
        lambda w: (None, w.intake(source_repository="AlobarQuest/elsewhere")),
        "isn't from intent-packages",
    ),
    "a commit not on main": (
        lambda w: (None, w.intake(source_commit="0" * 40)),
        "isn't on intent-packages' main",
    ),
    "a content hash that doesn't recompute": (
        lambda w: (None, w.intake(content_hash="0" * 64)),
        "doesn't hash",
    ),
    "envelope destinations that differ": (
        lambda w: (w.unit(destinations=DESTINATIONS[:2]), None),
        "differ",
    ),
}


@pytest.mark.parametrize("case", REFUSALS.values(), ids=REFUSALS.keys())
def test_each_check_refuses(world: World, case: tuple[Case, str]) -> None:
    build, message = case
    unit, intake = build(world)
    with pytest.raises(ContractError, match=message):
        world.check(unit, intake)


def test_a_package_published_after_approval_is_read_at_the_approved_commit(world: World) -> None:
    world.publish(_package(occurrence="2026-12-01-requested"), approve=False)
    assert world.check().rotation.occurrence == "2026-10-11-requested"


@pytest.mark.parametrize(
    ("package", "error", "message"),
    [
        ({"profile": "software-delivery", "profile_fields": {}}, ContractError, "profile isn't"),
        ({"profile": "non-software-operational"}, RegistryError, "no profile_fields"),
        (_package(standing=False), RegistryError, "standing isn't true"),
        (_package(occurrence=" "), ContractError, "names no occurrence"),
        (_package(destinations=DESTINATIONS[:2]), ContractError, "differ"),
    ],
)
def test_the_approved_package_itself_is_checked(
    world: World, package: dict[str, Any], error: type[Exception], message: str
) -> None:
    world.publish(package)
    with pytest.raises(error, match=message):
        world.check()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rotated_by_sds": "false"}, "isn't rotated_by_sds"),
        ({"provider": '"openrouter"'}, "provider isn't"),
        ({"bws_uuid": '"22222222-2222-4222-8222-222222222222"'}, "keeper isn't"),
    ],
)
def test_the_registry_entry_is_checked(world: World, change: dict[str, str], message: str) -> None:
    world.registry(_registry(**change))
    with pytest.raises(ContractError, match=message):
        world.check()


def test_a_registry_with_another_consumer_refuses(world: World) -> None:
    world.registry(
        _registry() + '\n  [[credential.consumer]]\n  kind = "bws-secret"\n  uuid = "x"\n'
    )
    with pytest.raises(ContractError, match="differ"):
        world.check()


def test_a_unit_marked_non_hosted_that_writes_a_hosted_consumer_is_refused(world: World) -> None:
    hosted = 'coolify-env-hash instance="p" resource_type="application" uuid="u" key="K" entry="E"'
    destinations = sorted([*DESTINATIONS, hosted])
    world.registry(
        _registry()
        + '\n  [[credential.consumer]]\n  kind = "coolify-env-hash"\n  instance = "p"\n'
        + '  resource_type = "application"\n  uuid = "u"\n  key = "K"\n  entry = "E"\n'
    )
    world.publish(_package(destinations=destinations))
    # Retire takes the old value away from the hosted service, so it writes one.
    with pytest.raises(ContractError, match="marked non-hosted but writes"):
        world.check(world.unit(destinations=destinations, rotation_unit="retire"))
    # Store writes BWS only, so it may be marked non-hosted, and is refused for the hosted
    # consumer alone: the fake adapter has no host step, so it can reach none.
    with pytest.raises(ContractError, match="reaches no hosted consumer"):
        world.check(world.unit(destinations=destinations))


def test_a_destination_too_long_for_evidence_is_refused(world: World) -> None:
    long_ref = "x" * 200
    long = f'runtime-fetch repo="orchestrator" ref="{long_ref}"'
    destinations = sorted([f'bws-secret uuid="{KEEPER}"', 'provider name="fake"', long])
    world.registry(_registry().replace('ref = "a person attests"', f'ref = "{long_ref}"'))
    world.publish(_package(destinations=destinations))
    with pytest.raises(ContractError, match="longer than"):
        world.check(world.unit(destinations=destinations))


def test_the_hash_is_intent_packages_own() -> None:
    """Pinned to `rotation-openrouter-generic` revision 1's approved hash in its lineage."""
    package = yaml.safe_load((FIXTURES / "rotation_openrouter_generic_rev1.yaml").read_text())
    assert package_hash(package) == (
        "c1b259d7f6b876cdeac9c425385917ecf4068e95c4497947ba14a3f1b9405d8b"
    )
    assert package_hash({**package, "status": "draft"}) == package_hash(package)


@pytest.mark.parametrize("value", [1.5, {1: "x"}, object()])
def test_the_hash_refuses_what_intent_packages_refuses(value: object) -> None:
    with pytest.raises(CanonicalError):
        package_hash({"x": value})


def test_the_hash_escapes_and_orders_as_rfc_8785() -> None:
    assert package_hash({"b": '\u0001\n"\\', "a": [None, True, 3]}) == package_hash(
        {"a": [None, True, 3], "b": '\u0001\n"\\'}
    )


def test_only_a_literal_true_marks_a_unit_non_hosted(world: World) -> None:
    """As at the orchestrator's claim: any other value leaves the unit windowed, so not marked."""
    hosted = 'coolify-env-hash instance="p" resource_type="application" uuid="u" key="K" entry="E"'
    destinations = sorted([*DESTINATIONS, hosted])
    world.registry(
        _registry()
        + '\n  [[credential.consumer]]\n  kind = "coolify-env-hash"\n  instance = "p"\n'
        + '  resource_type = "application"\n  uuid = "u"\n  key = "K"\n  entry = "E"\n'
    )
    world.publish(_package(destinations=destinations))
    # Retire writes the hosted service: marked, it is refused for that; unmarked, it gets past
    # the marking and is refused only because the fake adapter reaches no hosted consumer.
    with pytest.raises(ContractError, match="marked non-hosted but writes"):
        world.check(world.unit(destinations=destinations, rotation_unit="retire"))
    with pytest.raises(ContractError, match="reaches no hosted consumer"):
        world.check(
            world.unit(
                destinations=destinations, rotation_unit="retire", touches_no_hosted_service="yes"
            )
        )


def test_a_revision_superseded_by_a_later_approval_is_refused(world: World) -> None:
    older = world.intake()
    world.publish(_package(occurrence="2026-12-01-requested"))
    with pytest.raises(ContractError, match="latest approval"):
        world.check(intake=older)


def test_a_revision_at_another_package_s_path_is_refused(world: World) -> None:
    other = "packages/rotation-other/package.yaml"
    commit = world.origins.commit(PACKAGES_REPOSITORY, {other: yaml.safe_dump(world.package)})
    with pytest.raises(ContractError, match="standing package"):
        world.check(intake=world.intake(source_commit=commit, package_id="rotation-other"))


@pytest.mark.parametrize(
    "changes",
    [
        {"source_commit": None},
        {"source_commit": "abc123"},
        {"package_id": None},
        {"package_id": "canary"},
        {"package_id": "rotation-"},
        {"package_id": "rotation-a/../rotation-canary"},
    ],
)
def test_a_malformed_intake_is_refused_not_skipped(world: World, changes: dict[str, Any]) -> None:
    with pytest.raises(ContractError, match="no full commit and standing rotation package"):
        world.check(intake=world.intake(**changes))


@pytest.mark.parametrize("source_path", [None, "", "packages/rotation-other/package.yaml"])
def test_the_intake_s_source_path_is_never_read(world: World, source_path: str | None) -> None:
    world.check(intake=world.intake(source_path=source_path))


def test_a_path_missing_at_the_commit_is_refused_not_skipped(world: World) -> None:
    with pytest.raises(ContractError, match="can't be read"):
        world.check(intake=world.intake(package_id="rotation-missing"))


def test_a_package_with_no_approval_at_main_is_refused(world: World) -> None:
    world.origins.commit(PACKAGES_REPOSITORY, {LINEAGE: yaml.safe_dump({"approvals": []})})
    with pytest.raises(ContractError, match="latest approval"):
        world.check()


def test_both_of_the_orchestrator_adapter_s_units_write_the_hosted_service(world: World) -> None:
    """Mint hosts the new value and retire takes the old one away, so neither may be marked."""
    hosted = (
        'coolify-env-hash instance="prod" resource_type="application" uuid="u" '
        'key="ORCHESTRATOR_M2M_CREDENTIALS" entry="orchestrator-operator"'
    )
    destinations = sorted([f'bws-secret uuid="{KEEPER}"', 'provider name="orchestrator"', hosted])
    world.registry(
        _registry(provider='"orchestrator"').replace(
            '  [[credential.consumer]]\n  kind = "runtime-fetch"\n  repo = "orchestrator"\n'
            '  ref = "a person attests"\n',
            '  [[credential.consumer]]\n  kind = "coolify-env-hash"\n  instance = "prod"\n'
            '  resource_type = "application"\n  uuid = "u"\n'
            '  key = "ORCHESTRATOR_M2M_CREDENTIALS"\n  entry = "orchestrator-operator"\n',
        )
    )
    world.publish(_package(destinations=destinations))
    intake = world.intake(acceptance_criteria=[{"ac_id": f"AC-00{n}"} for n in range(1, 10)])
    for unit in ("mint", "retire"):
        marked = world.unit(
            destinations=destinations, rotation_adapter="orchestrator", rotation_unit=unit
        )
        with pytest.raises(ContractError, match="marked non-hosted but writes"):
            world.check(marked, intake)
        approved = world.check(
            {
                **marked,
                "authority": {
                    "constraints": {
                        **marked["authority"]["constraints"],
                        "touches_no_hosted_service": False,
                    }
                },
            },
            intake,
        )
        assert approved.unit.name == unit


def _orchestrator_world(world: World, reader_fields: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """An orchestrator-bearer rotation with one scheduled reader: its retire unit and intake."""
    hosted = (
        'coolify-env-hash instance="prod" resource_type="application" uuid="u" '
        'key="ORCHESTRATOR_M2M_CREDENTIALS" entry="orchestrator-observer"'
    )
    reader = 'runtime-fetch repo="orchestrator" ref="scripts/run-x.sh"'
    destinations = sorted(
        [f'bws-secret uuid="{KEEPER}"', 'provider name="orchestrator"', hosted, reader]
    )
    world.registry(
        _registry(provider='"orchestrator"').replace(
            '  ref = "a person attests"\n',
            '  ref = "scripts/run-x.sh"\n'
            + reader_fields
            + '\n  [[credential.consumer]]\n  kind = "coolify-env-hash"\n  instance = "prod"\n'
            '  resource_type = "application"\n  uuid = "u"\n'
            '  key = "ORCHESTRATOR_M2M_CREDENTIALS"\n  entry = "orchestrator-observer"\n',
        )
    )
    world.publish(_package(destinations=destinations))
    intake = world.intake(acceptance_criteria=[{"ac_id": f"AC-00{n}"} for n in range(1, 10)])
    unit = world.unit(
        destinations=destinations,
        rotation_adapter="orchestrator",
        rotation_unit="retire",
        touches_no_hosted_service=None,
    )
    return unit, intake


def test_a_reader_with_no_read_is_refused_when_no_human_act_could_attest_it(world: World) -> None:
    unit, intake = _orchestrator_world(world, "")
    with pytest.raises(ContractError, match="no read and no human act verifies"):
        world.check(unit, intake)


def test_a_reader_whose_row_names_its_read_is_approved_with_it(world: World) -> None:
    unit, intake = _orchestrator_world(world, '  healthchecks_check = "sds-x"\n')
    approved = world.check(unit, intake)
    assert approved.reads == {
        'runtime-fetch repo="orchestrator" ref="scripts/run-x.sh"': CheckRead("sds-x")
    }


def test_a_plan_with_a_human_act_lets_a_person_attest_a_reader_with_no_read(world: World) -> None:
    assert world.check().reads == {}


def _with_constraints(unit: dict[str, Any], **constraints: Any) -> dict[str, Any]:
    merged = {**unit["authority"]["constraints"], **constraints}
    return {**unit, "authority": {"constraints": merged}}


def test_a_host_without_overlap_is_declared_only_on_a_hosting_unit(world: World) -> None:
    retire, intake = _orchestrator_world(world, '  healthchecks_check = "sds-x"\n')
    mint = _with_constraints(retire, rotation_unit="mint")
    assert world.check(mint, intake).host_without_overlap is False
    declared = world.check(_with_constraints(mint, host_without_overlap=True), intake)
    assert declared.host_without_overlap is True
    with pytest.raises(ContractError, match="unit with no host step"):
        world.check(_with_constraints(retire, host_without_overlap=True), intake)


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_only_the_literal_true_declares_a_host_without_overlap(world: World, value: Any) -> None:
    retire, intake = _orchestrator_world(world, '  healthchecks_check = "sds-x"\n')
    mint = _with_constraints(retire, rotation_unit="mint", host_without_overlap=value)
    with pytest.raises(ContractError, match="only by the literal true"):
        world.check(mint, intake)
