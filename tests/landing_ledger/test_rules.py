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


# ---------------------------------------------------------------------------------------------
# ADR-0034: the fourth shape, and the history it must not rewrite.
# ---------------------------------------------------------------------------------------------

OUTCOME = "311aaa1dc0fb50bd9cb2350fe2d358e8ff973ccd"
DOCKER_CASCADE = "72391c0f7343477193b5c896680a083500c45227"

PATCH, MINOR, MAJOR = SEMVER_PATCH, SEMVER_MINOR, SEMVER_MAJOR
UNKNOWN = "version-update:semver-unknown"
UPDATE_TYPES = (PATCH, MINOR, MAJOR, None, UNKNOWN)
# `github-actions` with a HYPHEN is in the grid on purpose: one revision compares against it and
# `fetch-metadata` never reports it, which is the defect 4d87d9b7 preserves. `None` is the
# ledger failing to read an ecosystem the gate always has.
ECOSYSTEMS = ("uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None)

# EVERY ANSWER EVERY REVISION BEFORE 311aaa1d GIVES, as a literal, MEASURED FROM THE UNMODIFIED
# CODE on 2026-08-28 before the fourth shape was added.
#
# It is here because ADR-0034 changes the SHAPE of `Rule` rather than only its values, and the
# registry's whole worth is that a landing can be re-judged years later by the rule that actually
# armed it. A widening that reached backwards would rewrite the audit silently -- every affected
# landing would simply stop being a finding -- so this is written as data rather than derived
# from the rules it checks. A table computed FROM `permits` would agree with `permits` by
# construction and could pin nothing.
HISTORICAL_ANSWERS: dict[str, dict[str | None, list[str | None]]] = {
    # The first cut: patch and minor, every ecosystem, no major anywhere.
    "77ab867d1080d18baea3a2b230655c2729716970": {
        PATCH: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MINOR: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MAJOR: [],
        None: [],
        UNKNOWN: [],
    },
    # The hyphenated literal, which the reported value never matches. The widening it describes
    # reaches only a spelling nothing produces -- transcribed, never corrected.
    "4d87d9b7465e3b59bd9bdee2086de18eb1cab1dd": {
        PATCH: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MINOR: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MAJOR: ["github-actions"],
        None: ["github-actions"],
        UNKNOWN: ["github-actions"],
    },
    # The corrected literal, as a disjunction: the named ecosystem is permitted at ANY intent.
    "12880ce77ab97c3f4d9281195041eed8c5d52609": {
        PATCH: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MINOR: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MAJOR: ["github_actions"],
        None: ["github_actions"],
        UNKNOWN: ["github_actions"],
    },
    "43e37ed97823aec25cc5bac63f636914637e219c": {
        PATCH: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MINOR: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MAJOR: ["github_actions"],
        None: ["github_actions"],
        UNKNOWN: ["github_actions"],
    },
    # ADR-0018 restated the disjunction as a cascade: a major only where the check exercises it.
    "e849b3a8411fabeff1dedd138e6e3e3a2f535319": {
        PATCH: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MINOR: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MAJOR: ["github_actions"],
        None: [],
        UNKNOWN: [],
    },
    "a4a4b8da035292fe434badd007607d8a69bc54e2": {
        PATCH: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MINOR: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", "docker", None],
        MAJOR: ["github_actions"],
        None: [],
        UNKNOWN: [],
    },
    # ADR-0023, the docker column withdrawn.
    "72391c0f7343477193b5c896680a083500c45227": {
        PATCH: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", None],
        MINOR: ["uv", "pip", "npm_and_yarn", "github_actions", "github-actions", None],
        MAJOR: ["github_actions"],
        None: [],
        UNKNOWN: [],
    },
}


@pytest.mark.parametrize("revision", sorted(HISTORICAL_ANSWERS))
def test_a_revision_the_estate_has_already_run_answers_exactly_as_it_did(revision: str) -> None:
    """Adding a shape must not move a rule that already decided landings."""
    rule = rule_for(revision)
    assert rule is not None
    for update_type, permitted in HISTORICAL_ANSWERS[revision].items():
        for ecosystem in ECOSYSTEMS:
            assert rule.permits(update_type, ecosystem) == (ecosystem in permitted), (
                f"{revision[:12]} changed its answer for {update_type} / {ecosystem}"
            )


def test_every_revision_is_either_pinned_as_history_or_is_the_current_one() -> None:
    """Both directions, so a revision cannot join the registry without somebody deciding which
    it is. A new gate transcribed with no answer table would otherwise be pinned by nothing, and
    a historical entry deleted from the table would stop being held to what it did."""
    assert set(HISTORICAL_ANSWERS) | {OUTCOME} == set(REGISTRY)
    assert OUTCOME not in HISTORICAL_ANSWERS


def test_the_outcome_rule_permits_every_update_type_outside_the_excluded_ecosystem() -> None:
    """ADR-0034: a pull request whose required checks pass is routine whatever it declares. The
    absent update type is the cell this revision exists for -- ten of the estate's thirteen open
    updates on 2026-08-28 stated no delta, and no rule keyed on update types could reach one."""
    rule = rule_for(OUTCOME)
    assert rule is not None

    for update_type in UPDATE_TYPES:
        for ecosystem in ("uv", "pip", "npm_and_yarn", "github_actions", "github-actions"):
            assert rule.permits(update_type, ecosystem) is True, f"{update_type} / {ecosystem}"


def test_the_outcome_rule_still_refuses_docker_at_every_update_type() -> None:
    """The one exclusion, and the control that must not move: `python 3.12-slim -> 3.14-slim`
    states no delta, so a rule permitting everything undeclared would arm a language replacement
    that removes standard-library modules and that no check in this estate runs."""
    rule = rule_for(OUTCOME)
    assert rule is not None

    for update_type in UPDATE_TYPES:
        assert rule.permits(update_type, "docker") is False, update_type


def test_the_outcome_rule_refuses_an_ecosystem_this_program_could_not_read() -> None:
    """FAIL-CLOSED RATHER THAN FAITHFUL, and deliberately so.

    The gate always has an ecosystem -- it is the second segment of the branch name -- so `None`
    never means the gate saw nothing; it means the ledger could not read what the gate read. The
    three shapes before this one refused that input anyway, at Q1, for want of a declared intent.
    Here the exclusion is the only question asked, so permitting an unreadable ecosystem would
    bless a landing whose one condition nobody can re-check.
    """
    rule = rule_for(OUTCOME)
    assert rule is not None

    for update_type in UPDATE_TYPES:
        assert rule.permits(update_type, None) is False, update_type


def test_the_outcome_rule_differs_from_its_predecessor_in_three_named_groups() -> None:
    """The differential over the whole grid, so a change nobody intended shows up as one.

    TWO WIDENINGS AND ONE NARROWING, and the narrowing is the one a first draft of this test
    denied. It permits a major outside `github_actions`, which ADR-0018 refused on purpose; it
    permits an update stating no delta, which every earlier shape refused merely for want of
    one; and it REFUSES a declared patch or minor whose ecosystem the ledger could not read,
    which its predecessor permitted. That last is the fail-closed guard: the earlier shapes
    could answer from the update type alone, and this one has nothing to ask but the ecosystem,
    so an unreadable one has to refuse. Unreachable in practice -- a landing the gate armed came
    from a branch this reader parses -- but a real difference, and stating it as an absence
    would be the assertion, not the code, being wrong.
    """
    outcome, cascade = rule_for(OUTCOME), rule_for(DOCKER_CASCADE)
    assert outcome is not None and cascade is not None

    changed = {
        (update_type, ecosystem)
        for update_type in UPDATE_TYPES
        for ecosystem in ECOSYSTEMS
        if outcome.permits(update_type, ecosystem) != cascade.permits(update_type, ecosystem)
    }
    known = {"uv", "pip", "npm_and_yarn", "github_actions", "github-actions"}
    widened = (
        {(MAJOR, e) for e in known if e != "github_actions"}
        | {(UNKNOWN, e) for e in known}
        | {(None, e) for e in known}
    )
    narrowed = {(PATCH, None), (MINOR, None)}

    assert changed == widened | narrowed
    assert {cell for cell in changed if cascade.permits(*cell)} == narrowed
    # The docker column is where the two AGREE, and it is the control ADR-0034 must not move.
    assert not [cell for cell in changed if cell[1] == "docker"]
