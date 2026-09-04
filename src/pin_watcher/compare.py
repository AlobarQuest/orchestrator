"""What a caller's pin is, relative to the recommendation -- four answers, kept apart.

FOUR STATES, NOT A BOOLEAN, and the estate's own reason: "'not current' for three different
reasons is exactly the state collapse this estate has paid for repeatedly"
(`activation_sweep/activation.py`). The four differ in what a person must do about them, which is
the test for whether a distinction earns its keep:

  `current`      the pin equals the recommendation. Nothing to do.
  `behind`       the pin is an ancestor of the recommendation. Advance it; this is the ordinary
                 case and the one that went unreported for a week.
  `ahead`        the recommendation is an ancestor of the pin. Somebody pinned a revision newer
                 than the one factory-runner recommends -- not wrong, but not chosen either, and
                 it means the recommendation is the thing that is stale.
  `diverged`     neither is an ancestor of the other. The caller is pinned to something that is
                 not on factory-runner's default branch at all.

Two further conditions are not comparisons, so they are not states of one:

  `unpinned`     the caller names a branch or tag rather than a forty-character SHA. This is the
                 GAP-4 class -- `intent-packages` and `security-standards` both used `@main` until
                 2026-08-03 -- and it is worse than being behind, because what runs is whatever
                 the branch holds at the moment of the dispatch.
  `unresolvable` the SHA is well-formed and factory-runner does not have it.

`unresolvable` and a read that failed are DIFFERENT and must stay so: the first is a fact about
the caller, the second is a fact about the pass, and a pass that could not read cannot claim it
found everything there was to find.
"""

from __future__ import annotations

from dataclasses import dataclass

CURRENT = "current"
BEHIND = "behind"
AHEAD = "ahead"
DIVERGED = "diverged"
UNPINNED = "unpinned"
UNRESOLVABLE = "unresolvable"

# Every state except `current` is worth a person's attention. Spelled as the complement of the one
# clean state rather than as a list of the bad ones: a state added later is a finding until
# somebody decides otherwise, which is the direction that fails safe.
CLEAN_STATE = CURRENT


@dataclass(frozen=True)
class Caller:
    """One repository's caller, as measured.

    `pin` is what the workflow names -- a SHA, or a branch name when `state` is `unpinned`.

    `behind_by` and `ahead_by` are carried SEPARATELY rather than folded into one distance. A
    diverged caller has both, and a single number for it would have to invent a meaning; keeping
    the pair means a reader can check the state against the counts that produced it rather than
    taking it. Both are None whenever the two revisions could not be compared at all.

    `pinned_at` is the committer date of the pinned revision. It is what the record uses for
    `observed_at`, and `record.py` explains why that field may not be a clock.
    """

    repository: str
    pin: str
    state: str
    behind_by: int | None
    ahead_by: int | None
    pinned_at: str | None

    @property
    def is_finding(self) -> bool:
        return self.state != CLEAN_STATE


def state_from_comparison(status: str) -> str:
    """Map GitHub's compare `status` onto our vocabulary.

    GitHub answers `identical`, `behind`, `ahead` or `diverged` for
    `compare/{recommendation}...{pin}` -- read in that direction, so `behind` means the PIN is
    behind, which is the way round a reader expects. An unknown value maps to `diverged` rather
    than raising: a comparison that came back and that we cannot name is still a comparison, and
    calling it diverged reports it instead of failing the pass.
    """
    if status == "identical":
        return CURRENT
    if status in {BEHIND, AHEAD, DIVERGED}:
        return status
    return DIVERGED
