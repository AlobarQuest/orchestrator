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


@dataclass(frozen=True)
class Rule:
    """One revision of the gate, as a predicate over what the ledger records.

    `update_types` and `ecosystems` are the two arms of the workflow step's `if:`, which is a
    DISJUNCTION -- so a named ecosystem is permitted at ANY update type, majors included. That is
    the point of naming one: an Actions major changes how CI runs, and the check that gates it is
    the thing being bumped.

    `requires_upstream_author` is the job-level condition (`pull_request.user.login ==
    'dependabot[bot]'`). The ledger does not record the pull request's author, so the honest
    proxy is the `updated-dependencies` trailer -- the same text the gate's own metadata step
    parses. A landing with no trailer cannot be shown to have satisfied this arm.
    """

    revision: str
    update_types: frozenset[str]
    ecosystems: frozenset[str] = field(default_factory=frozenset)
    requires_upstream_author: bool = True

    def permits(self, update_type: str | None, ecosystem: str | None) -> bool:
        return update_type in self.update_types or (
            ecosystem is not None and ecosystem in self.ecosystems
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

REGISTRY: dict[str, Rule] = {
    rule.revision: rule
    for rule in (
        _PATCH_AND_MINOR,
        _HYPHENATED_ACTIONS,
        _UNDERSCORED_ACTIONS,
        _UNDERSCORED_ACTIONS_NEWER_METADATA,
    )
}


def rule_for(revision: str | None) -> Rule | None:
    """The transcribed rule for a blob sha, or None -- which callers must treat as a finding."""
    if revision is None:
        return None
    return REGISTRY.get(revision)
