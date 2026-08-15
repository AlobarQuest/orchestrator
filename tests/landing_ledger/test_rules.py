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

from landing_ledger.rules import (
    GATE_PATH,
    REGISTRY,
    SEMVER_MINOR,
    SEMVER_PATCH,
    Rule,
    rule_for,
)

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


def test_this_repositorys_own_gate_is_transcribed() -> None:
    """The pairing made mechanical rather than requested.

    Every test above compares a FIXTURE to its own filename, which cannot notice the live gate
    being edited: nothing in the suite reads `.github/workflows/` at all, so a byte changed there
    leaves the registry describing a file that no longer exists and says so only in production,
    as `current_rule_revision_unknown`, per repository, for every open update.

    It became checkable only when the lane was vendored here -- while the gate lived solely in
    other repositories there was no local file to hash. A repository with no gate is a normal
    state (two of the eight the ledger covers), so its absence is not a failure.
    """
    gate = Path(GATE_PATH)
    if not gate.exists():
        pytest.skip(f"{GATE_PATH} is not installed in this repository")

    assert _blob_sha(gate.read_bytes()) in REGISTRY, (
        f"{GATE_PATH} has been edited without transcribing the new revision in "
        "src/landing_ledger/rules.py. The audit fails closed on a revision it does not know."
    )


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


def test_the_cascade_permits_a_major_only_in_the_ecosystem_that_exercises_it() -> None:
    """ADR-0018. The distinguishing behaviour of `e849b3a8` against the revision before it.

    Asserted across the whole grid rather than on one happy case: the predecessor permitted
    ANYTHING in github_actions, and the only way to see the difference is to ask about an
    intent that is neither patch, minor, nor major.
    """
    cascade = rule_for("e849b3a8411fabeff1dedd138e6e3e3a2f535319")
    previous = rule_for("12880ce77ab97c3f4d9281195041eed8c5d52609")
    assert cascade is not None and previous is not None

    # Identical everywhere the estate has ever been.
    for update_type in (SEMVER_PATCH, SEMVER_MINOR, SEMVER_MAJOR):
        for ecosystem in ("uv", "npm_and_yarn", "docker", "github_actions"):
            assert cascade.permits(update_type, ecosystem) == previous.permits(
                update_type, ecosystem
            ), f"{update_type} / {ecosystem} must not have changed"

    # The one cell that differs: no declared intent, in github_actions.
    assert previous.permits(None, "github_actions") is True
    assert cascade.permits(None, "github_actions") is False


def test_the_docker_exclusion_changes_exactly_the_docker_column() -> None:
    """ADR-0023, and the first revision that REFUSES something its predecessor permitted.

    Asserted as a differential over the whole grid rather than on the docker cells alone: a
    field that can refuse is the one shape in this registry able to narrow a rule by accident,
    and the way that would show is somewhere other than the column it was written for.
    """
    excluded = rule_for("72391c0f7343477193b5c896680a083500c45227")
    cascade = rule_for("e849b3a8411fabeff1dedd138e6e3e3a2f535319")
    assert excluded is not None and cascade is not None

    for update_type in (SEMVER_PATCH, SEMVER_MINOR, SEMVER_MAJOR, None):
        for ecosystem in ("uv", "npm_and_yarn", "github_actions", None):
            assert excluded.permits(update_type, ecosystem) == cascade.permits(
                update_type, ecosystem
            ), f"{update_type} / {ecosystem} must not have changed"

    # The column that did change. Patch and minor armed under the cascade and no longer do;
    # a major was already refused there and still is, so the exclusion is not what stops it.
    assert cascade.permits(SEMVER_PATCH, "docker") is True
    assert cascade.permits(SEMVER_MINOR, "docker") is True
    assert excluded.permits(SEMVER_PATCH, "docker") is False
    assert excluded.permits(SEMVER_MINOR, "docker") is False
    assert excluded.permits(SEMVER_MAJOR, "docker") is False
    # orchestrator#3 (`python` 3.12-slim -> 3.14-slim) is THIS case, not a minor: `3.14-slim`
    # does not parse as semver, so Dependabot declares no intent at all and the pull request is
    # refused at Q1 with or without the exclusion. ADR-0023's own account of its live subject
    # says `semver-minor`; the trailer on the pull request says nothing. Measured 2026-08-15.
    assert excluded.permits(None, "docker") is False
    assert cascade.permits(None, "docker") is False


def test_a_major_outside_that_ecosystem_is_still_refused_by_the_cascade() -> None:
    cascade = rule_for("e849b3a8411fabeff1dedd138e6e3e3a2f535319")
    assert cascade is not None
    assert cascade.permits(SEMVER_MAJOR, "uv") is False
    assert cascade.permits(SEMVER_MAJOR, "docker") is False
