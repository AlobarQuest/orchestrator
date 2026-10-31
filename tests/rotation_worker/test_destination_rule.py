"""One rule compares destination lists: the executor, the proposer and the decomposer all ask it."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bump_proposer.standing import checkout_root
from rotation_decomposer.decompose import DecomposeError, decomposition
from rotation_proposer.standing import discover
from rotation_worker.contract import ContractError
from rotation_worker.destinations import same
from rotation_worker.git_mirror import RepoMirror
from tests.rotation_decomposer.test_decompose import _entry, _intake
from tests.rotation_proposer.test_standing import DESTINATIONS as PROPOSER_DESTINATIONS
from tests.rotation_proposer.test_standing import write_package
from tests.rotation_worker.conftest import Origins
from tests.rotation_worker.test_checks import DESTINATIONS, World


@pytest.fixture
def world(origins: Origins, mirror: RepoMirror) -> World:
    return World(origins, mirror)


def test_same_ignores_order() -> None:
    assert same(["a", "b"], ["b", "a"])


@pytest.mark.parametrize(
    ("a", "b"),
    [(["a"], ["a", "b"]), (["a", "b"], ["a", "c"]), (["a", "a", "b"], ["a", "b", "b"])],
)
def test_same_refuses_a_different_list(a, b) -> None:
    assert not same(a, b)


def test_a_repeated_destination_is_not_the_same_list() -> None:
    """A set comparison would call these equal, and a repeat is a malformed list."""
    assert not same(["a", "a", "b"], ["a", "b"])


def test_the_executor_accepts_any_order(world: World) -> None:
    assert world.check(world.unit(destinations=list(reversed(DESTINATIONS))))


@pytest.mark.parametrize("enveloped", ["not a list", [1, 2, 3], [{"kind": "x"}]])
def test_the_executor_refuses_a_malformed_envelope_list(world: World, enveloped) -> None:
    with pytest.raises(ContractError, match="differ"):
        world.check(world.unit(destinations=enveloped))


def test_the_proposer_asks_the_rule(tmp_path: Path) -> None:
    write_package(tmp_path, occurrence="'2025-01-02-age'")
    (package,) = discover(tmp_path).values()
    assert package.lists(list(reversed(PROPOSER_DESTINATIONS)))
    assert not package.lists([*PROPOSER_DESTINATIONS, PROPOSER_DESTINATIONS[0]])


def test_the_decomposer_asks_the_rule() -> None:
    entry = _entry("openrouter-key")
    intake = _intake(entry)
    listed = intake["enforcement_snapshot"]["profile_fields"]["destinations"]
    listed.append(listed[0])
    with pytest.raises(DecomposeError, match="differ from the registry's"):
        decomposition(intake, entry)


PACKAGES = sorted((checkout_root() / "packages").glob("rotation-*/package.yaml"))


@pytest.mark.skipif(not PACKAGES, reason="no intent-packages checkout on this machine")
def test_the_rule_agrees_with_exact_equality_on_every_approved_package() -> None:
    """Every real list is sorted and unique, which the registry's own rendering writes, so moving
    the executor's check to the shared rule changes nothing any approved package does."""
    lists = []
    for path in PACKAGES:
        document = yaml.safe_load(path.read_text())
        listed = (document.get("profile_fields") or {}).get("destinations")
        if document.get("status") == "approved" and listed:
            lists.append(listed)
    assert len(lists) >= 17
    for listed in lists:
        assert listed == sorted(set(listed))
        assert same(listed, list(reversed(listed)))
