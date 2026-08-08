"""The transcribed rules, held to the bytes they claim to encode.

A registry of hand-written predicates is only trustworthy if it cannot drift from the file it
describes. So each entry carries a fixture of the real workflow, and the test below recomputes
that fixture's GIT BLOB SHA and asserts it equals the registry key. Git's blob sha is
`sha1("blob <len>\\0" + content)` -- content-addressed, which is also why one revision appears in
two different repositories under one key.

Editing a fixture without editing the key, or the key without the fixture, fails here.
"""

import hashlib
from pathlib import Path

import pytest

from landing_ledger.rules import REGISTRY, SEMVER_MINOR, SEMVER_PATCH, Rule, rule_for

FIXTURES = Path("tests/fixtures/auto-merge-rules")

SEMVER_MAJOR = "version-update:semver-major"


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


@pytest.mark.parametrize("revision", sorted(REGISTRY))
def test_each_transcribed_rule_names_the_bytes_it_encodes(revision: str) -> None:
    fixture = FIXTURES / f"{revision}.yml"

    assert fixture.exists(), f"rule {revision} is transcribed with no fixture of the file"
    assert _blob_sha(fixture.read_bytes()) == revision


def test_the_fixtures_and_the_registry_name_the_same_revisions() -> None:
    """Both directions. A fixture nobody transcribed is as useless as a transcription nobody
    pinned -- and the second is the one that would let an entry describe a file that changed."""
    assert {path.stem for path in FIXTURES.glob("*.yml")} == set(REGISTRY)


def test_an_unrecognised_revision_is_refused_rather_than_assumed_harmless() -> None:
    """The whole reason the ledger pins a revision instead of a filename."""
    assert rule_for("0" * 40) is None
    assert rule_for(None) is None


# ---------------------------------------------------------------------------------------------
# What each revision actually permitted. Read against the fixture beside it.
# ---------------------------------------------------------------------------------------------


def test_the_first_cut_permitted_patch_and_minor_and_no_major_at_all() -> None:
    rule = REGISTRY["77ab867d1080d18baea3a2b230655c2729716970"]

    assert rule.permits(SEMVER_PATCH, "uv")
    assert rule.permits(SEMVER_MINOR, "github_actions")
    assert not rule.permits(SEMVER_MAJOR, "github_actions")
    assert not rule.permits(SEMVER_MAJOR, "uv")


def test_the_hyphenated_revision_widened_nothing_because_its_literal_never_matched() -> None:
    """The 2026-08-07 defect, transcribed rather than corrected.

    It compares against `github-actions` while the value it is compared to -- the second segment
    of the branch name, which is what `fetch-metadata` reports -- spells it `github_actions`. So
    the widening it describes did not happen, and a registry that quietly wrote the underscore
    here would report the resulting landings as permitted by a rule that did not permit them.
    """
    rule = REGISTRY["4d87d9b7465e3b59bd9bdee2086de18eb1cab1dd"]

    assert not rule.permits(SEMVER_MAJOR, "github_actions")
    assert rule.permits(SEMVER_MAJOR, "github-actions")
    assert rule.permits(SEMVER_MINOR, "npm_and_yarn")


def test_the_corrected_revision_permits_any_actions_update_including_a_major() -> None:
    """The two arms are a DISJUNCTION, so naming an ecosystem permits it at every update type."""
    rule = REGISTRY["12880ce77ab97c3f4d9281195041eed8c5d52609"]

    assert rule.permits(SEMVER_MAJOR, "github_actions")
    assert rule.permits(SEMVER_PATCH, "npm_and_yarn")
    assert not rule.permits(SEMVER_MAJOR, "npm_and_yarn")


def test_a_revision_that_differs_only_in_its_pinned_action_still_needs_its_own_entry() -> None:
    """The ledger keys on BYTES. Two revisions with one predicate are two revisions, and one of
    them is the landing that bumped the pin -- so aliasing them would hide that landing's own
    rule from the audit."""
    older = REGISTRY["12880ce77ab97c3f4d9281195041eed8c5d52609"]
    newer = REGISTRY["43e37ed97823aec25cc5bac63f636914637e219c"]

    assert older.revision != newer.revision
    assert (older.update_types, older.ecosystems) == (newer.update_types, newer.ecosystems)


def test_a_rule_that_names_no_ecosystem_never_permits_on_an_absent_one() -> None:
    """`None in frozenset()` is False either way; this pins that an absent ecosystem cannot
    become a match by some future membership check being written the other way round."""
    rule = Rule(revision="x", update_types=frozenset({SEMVER_PATCH}), ecosystems=frozenset({"uv"}))

    assert not rule.permits(SEMVER_MAJOR, None)
    assert not rule.permits(None, None)
