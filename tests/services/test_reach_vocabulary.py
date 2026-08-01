"""The reach vocabulary, validated against the POPULATION rather than against examples (ADR-0009).

A vocabulary that cannot express the packages that actually exist is wrong however elegant it
looks, and a member no real package needs is decoration. Both are checked here against
`tests/fixtures/reach_census.json` -- a census of all 24 authored intent packages, classified from
each package's own declared fields.

The load-bearing assertion is the SET-EQUALITY one. A census that only validates proves the
vocabulary is big enough; equality also proves it is no bigger than the population needs. Proven
to discriminate by control: deleting `operator_machine` from the vocabulary reds it, along with
the expressibility assertion.
"""

import json
import pathlib

import pytest

from orchestrator.errors import DomainError
from orchestrator.reach_vocabulary import (
    REACH_VOCABULARY,
    carry_reach,
    reach_from_snapshot,
    reach_statement,
    validate_reach,
)

CENSUS = json.loads((pathlib.Path("tests/fixtures/reach_census.json")).read_text())
PACKAGES = CENSUS["packages"]

# The five registered delivery profiles, plus the profile-less packages authored before profiles
# existed. The census must cover all six populations or it is not a census.
#
# not-a-vocabulary: a coverage checklist for THIS test's fixture, not a set any producer must
# agree with. A profile absent here fails the coverage assertion, which is the point.
CENSUS_POPULATIONS = (
    "software-delivery",
    "infrastructure-change",
    "dependency-update",
    "maintenance-remediation",
    "non-software-operational",
    None,
)


def test_the_census_covers_the_whole_population_exactly_once() -> None:
    ids = [package["package_id"] for package in PACKAGES]

    assert len(ids) == 24
    assert len(set(ids)) == len(ids)


def test_every_real_package_is_expressible_in_the_vocabulary() -> None:
    # The increment's gate. Not "some examples classify" -- every authored package.
    for package in PACKAGES:
        assert validate_reach(package["reach"]) is not None, package["package_id"]


def test_every_delivery_profile_including_the_profile_less_packages_is_represented() -> None:
    represented = {package["profile"] for package in PACKAGES}

    assert represented == set(CENSUS_POPULATIONS)


def test_the_vocabulary_is_exactly_what_the_population_needs() -> None:
    """Both directions at once, which is what makes this discriminate.

    A member no authored package uses is decoration -- remove it. A member the population needs
    that the vocabulary lacks makes some package inexpressible -- add it. Set equality fails on
    either, and the removal control (deleting `operator_machine` and watching this red) is what
    proves it is not merely a restatement of the vocabulary.
    """
    used = {member for package in PACKAGES for member in package["reach"]}

    assert used == set(REACH_VOCABULARY)


def test_a_single_valued_field_would_misclassify_real_packages_permissively() -> None:
    """Why reach is a set: the collapse always drops the more restrictive half.

    Each multi-member package pairs `source_repository` (or nothing) with something that reaches
    further. Forced to one value, the repository half is the visible one and the estate, external
    or operator-machine half is what gets lost.
    """
    multi = [package for package in PACKAGES if len(package["reach"]) > 1]

    assert len(multi) == 5
    for package in multi:
        beyond = set(package["reach"]) - {"source_repository"}
        assert beyond, package["package_id"]


def test_the_change_class_of_a_package_does_not_determine_its_reach() -> None:
    """The measured reason `change_class` is the wrong key.

    `software-delivery` -- the largest profile in the population -- carries three distinct reaches.
    A policy keyed on the class would treat a repository-only change and one that restarts four
    running services identically.
    """
    by_profile: dict[str | None, set[tuple[str, ...]]] = {}
    for package in PACKAGES:
        by_profile.setdefault(package["profile"], set()).add(tuple(sorted(package["reach"])))

    assert len(by_profile["software-delivery"]) == 3
    assert len(by_profile["non-software-operational"]) == 1
    assert any(len(reaches) > 1 for reaches in by_profile.values())


def test_validate_reach_normalizes_to_a_sorted_tuple() -> None:
    assert validate_reach(["source_repository", "live_estate"]) == (
        "live_estate",
        "source_repository",
    )


def test_an_undeclared_reach_is_none_rather_than_an_empty_set() -> None:
    # Absence must never read as "reaches nothing" -- that is the most permissive claim there is,
    # and 14 packages predate the field and can never be edited.
    assert validate_reach(None) is None
    assert carry_reach({"title": "one"}, None) == {"title": "one"}
    assert reach_from_snapshot({"title": "one"}) is None
    assert reach_statement(None) is None


@pytest.mark.parametrize(
    ("declaration", "detail"),
    [
        ("source_repository", "list"),
        ([], "at least one"),
        (["source_repository", "source_repository"], "repeat"),
        (["nowhere_in_particular"], "not a recognised"),
        ([{"reach": "source_repository"}], "string"),
    ],
)
def test_an_invalid_declaration_is_refused_by_name(declaration: object, detail: str) -> None:
    with pytest.raises(DomainError) as raised:
        validate_reach(declaration)

    assert raised.value.code == "reach_invalid"
    assert detail in str(raised.value)


def test_a_stored_reach_that_is_not_a_list_of_known_members_reads_as_undeclared() -> None:
    # The snapshot is data from another repository and intake is not the only writer of revisions.
    # A shape nobody validated must fall back to the unknown, never to a partial answer.
    assert reach_from_snapshot({"reach": "source_repository"}) is None
    assert reach_from_snapshot({"reach": ["invented"]}) is None
    assert reach_from_snapshot("not a snapshot") is None


def test_the_statement_names_every_declared_member_in_words() -> None:
    statement = reach_statement(("live_estate", "operator_machine"))

    assert statement is not None
    assert REACH_VOCABULARY["live_estate"] in statement
    assert REACH_VOCABULARY["operator_machine"] in statement
    assert REACH_VOCABULARY["source_repository"] not in statement
