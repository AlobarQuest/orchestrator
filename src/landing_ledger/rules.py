"""The permission rules, keyed by the git blob sha of the gate workflow that encodes them.

WHY A REGISTRY AND NOT A PARSER. The gate's condition is a GitHub expression evaluated by GitHub,
and a re-implementation that reads the YAML would be a second interpreter of a language nobody
owns -- wrong in ways that look right. So each rule this estate has actually run is transcribed
here BY HAND, keyed by the blob sha the ledger already pins, and the transcription is held to the
bytes it claims to encode by a fixture whose git blob sha is recomputed and compared
(`tests/landing_ledger/test_rules.py`). The registry cannot silently drift from the file.

FAIL CLOSED ON AN UNKNOWN REVISION. A blob sha with no entry is not "probably fine": it is a rule
nobody classified, deciding landings. `rule_for` returns None and every caller treats that as a
finding. That is the whole reason the ledger pins a revision instead of a filename.

THE LITERAL IS TRANSCRIBED, NOT CORRECTED. Revision 4d87d9b7 compares against `github-actions`
with a HYPHEN, while the value it is compared to -- `fetch-metadata`'s `package-ecosystem`, which
is the second segment of the branch name -- spells it with an UNDERSCORE. That revision therefore
permitted nothing beyond patch and minor, silently. Transcribing the literal as written reproduces
that; "fixing" it here would erase the defect from the audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GATE_PATH = ".github/workflows/dependabot-auto-merge.yml"

SEMVER_PATCH = "version-update:semver-patch"
SEMVER_MINOR = "version-update:semver-minor"
SEMVER_MAJOR = "version-update:semver-major"


@dataclass(frozen=True)
class Rule:
    """One revision of the gate, as a predicate over what the ledger records.

    `update_types` and `ecosystems` are the two arms of the workflow step's `if:`, which is a
    DISJUNCTION -- so a named ecosystem is permitted at ANY update type, majors included. That is
    the point of naming one: an Actions major changes how CI runs, and the check that gates it is
    the thing being bumped.

    `excluded_ecosystems` is not a third arm but a precondition on all of them, and it is the
    only field that can REFUSE what another field permits. It records the leading
    `package-ecosystem != '...'` a revision may carry (ADR-0023).

    `requires_upstream_author` is the job-level condition (`pull_request.user.login ==
    'dependabot[bot]'`). The ledger does not record the pull request's author, so the honest
    proxy is the `updated-dependencies` trailer -- the same text the gate's own metadata step
    parses. A landing with no trailer cannot be shown to have satisfied this arm.
    """

    revision: str
    update_types: frozenset[str]
    ecosystems: frozenset[str] = field(default_factory=frozenset)
    major_ecosystems: frozenset[str] = field(default_factory=frozenset)
    excluded_ecosystems: frozenset[str] = field(default_factory=frozenset)
    requires_upstream_author: bool = True

    def permits(self, update_type: str | None, ecosystem: str | None) -> bool:
        """Evaluated as the cascade the gate became on 2026-08-08 (ADR-0018).

        Two ecosystem fields, because the revisions genuinely differ in SHAPE and this is a
        transcription rather than a policy. `ecosystems` is the older, unconditional form --
        anything at all in that ecosystem, which is what those bytes actually said. From
        `e849b3a8` the gate asks a major only, so `major_ecosystems` records that. Collapsing
        them would make the registry describe a rule no revision implemented.
        """
        # Q0, from 3bff4dec -- does the cascade's reasoning reach this ecosystem at all? An
        # ABSENT ecosystem is not excluded here, faithfully: the workflow compares a missing
        # output against a literal and proceeds, and such an update is refused at Q1 anyway
        # unless it declares an intent.
        if ecosystem in self.excluded_ecosystems:
            return False
        # Q1 -- is the declared intent sufficient on its own?
        if update_type in self.update_types:
            return True
        # Q2, older form -- the ecosystem permitted anything, whatever the intent.
        if ecosystem is not None and ecosystem in self.ecosystems:
            return True
        # Q2, from e849b3a8 -- a major, where the check that gates it exercises the thing bumped.
        return (
            update_type == SEMVER_MAJOR
            and ecosystem is not None
            and ecosystem in self.major_ecosystems
        )


# Patch and minor, every ecosystem. The first cut: a major bump falls through to a person.
_PATCH_AND_MINOR = Rule(
    revision="77ab867d1080d18baea3a2b230655c2729716970",
    update_types=frozenset({SEMVER_PATCH, SEMVER_MINOR}),
)

# The first attempt at widening to GitHub Actions majors. Its literal does not match the value it
# is compared against, so in practice it is _PATCH_AND_MINOR wearing a wider description.
_HYPHENATED_ACTIONS = Rule(
    revision="4d87d9b7465e3b59bd9bdee2086de18eb1cab1dd",
    update_types=frozenset({SEMVER_PATCH, SEMVER_MINOR}),
    ecosystems=frozenset({"github-actions"}),
)

# The corrected literal: `github_actions`, which is what the branch's second segment says.
_UNDERSCORED_ACTIONS = Rule(
    revision="12880ce77ab97c3f4d9281195041eed8c5d52609",
    update_types=frozenset({SEMVER_PATCH, SEMVER_MINOR}),
    ecosystems=frozenset({"github_actions"}),
)

# Byte-identical to the revision above apart from the pinned `fetch-metadata` version, which one
# of the landings under audit is itself the bump of. Same predicate, different bytes, and the
# ledger keys on bytes -- so it needs its own entry rather than an alias.
_UNDERSCORED_ACTIONS_NEWER_METADATA = Rule(
    revision="43e37ed97823aec25cc5bac63f636914637e219c",
    update_types=frozenset({SEMVER_PATCH, SEMVER_MINOR}),
    ecosystems=frozenset({"github_actions"}),
)

# ADR-0018 restated the disjunction as a cascade: majors are permitted only in github_actions,
# where the required check that gates the pull request IS the thing being bumped. Behaviourally
# this differs from the revision above in exactly one cell -- an absent update-type in
# github_actions, which armed before and refuses now, and which has never occurred.
_CASCADE = Rule(
    revision="e849b3a8411fabeff1dedd138e6e3e3a2f535319",
    update_types=frozenset({SEMVER_PATCH, SEMVER_MINOR}),
    major_ecosystems=frozenset({"github_actions"}),
)

# The cascade as it landed in factory-runner: same predicate, four bytes apart, because that
# repository is a `fetch-metadata` release ahead. The ledger keys on bytes, so it needs its own
# entry -- the same reason 12880ce7 and 43e37ed9 are both present.
_CASCADE_NEWER_METADATA = Rule(
    revision="a4a4b8da035292fe434badd007607d8a69bc54e2",
    update_types=frozenset({SEMVER_PATCH, SEMVER_MINOR}),
    major_ecosystems=frozenset({"github_actions"}),
)

# ADR-0023, and the first revision whose predicate is not a widening of the one before it. The
# cascade minus the one ecosystem its reasoning does not reach: docker tags are not semver, so a
# language replacement arrives wearing a minor digit, and nothing RUNS the built image, so the
# "the gate exercises it" ground fails too. Differs from _CASCADE in exactly the docker column.
#
# It exists because the lane was vendored to orchestrator, the only repository declaring that
# ecosystem -- the five that already carried the gate are untouched and stay on _CASCADE.
_CASCADE_WITHOUT_DOCKER = Rule(
    revision="3bff4dec2db984836da744f22de70f6dc5e8c37e",
    update_types=frozenset({SEMVER_PATCH, SEMVER_MINOR}),
    major_ecosystems=frozenset({"github_actions"}),
    excluded_ecosystems=frozenset({"docker"}),
)

REGISTRY: dict[str, Rule] = {
    rule.revision: rule
    for rule in (
        _PATCH_AND_MINOR,
        _HYPHENATED_ACTIONS,
        _UNDERSCORED_ACTIONS,
        _UNDERSCORED_ACTIONS_NEWER_METADATA,
        _CASCADE,
        _CASCADE_NEWER_METADATA,
        _CASCADE_WITHOUT_DOCKER,
    )
}


def rule_for(revision: str | None) -> Rule | None:
    """The transcribed rule for a blob sha, or None -- which callers must treat as a finding."""
    if revision is None:
        return None
    return REGISTRY.get(revision)
