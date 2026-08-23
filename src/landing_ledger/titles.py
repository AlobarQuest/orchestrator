"""The delta a pull request title declares: which two versions, and what kind of change.

**READ FROM THE TITLE, and that was measured rather than chosen.** The update bot rewrites a
pull request in place when a newer version appears, and on one such pull request the branch
still read `ruff-0.16.0` while the title read `0.16.1` -- and so did the bot's own
machine-readable `dependency-version` trailer, whose diff installs 0.16.1. The title was the
only one of the three that tracked the change.

**THIS IS A SECOND COPY OF A PARSER THAT ALREADY EXISTS, and it is held to the original
rather than trusted.** The authority is
:func:`orchestrator.services.estate_landing_admission.update_type_of`, which the programs
here cannot import: an out-of-process program in this repository imports nothing from
``orchestrator``, and that module reaches SQLAlchemy. The estate's answer to a vocabulary
that must agree across that boundary is a mirror pinned by a test that imports both -- the
arrangement ``estate_lander._DELIBERATE`` already uses. Here the pin is stronger than a
literal comparison: ``tests/landing_ledger/test_titles.py`` asserts that the two agree on the
classification of every title in a corpus, including every open pull request the estate
carried when this shipped, so a divergence is a red test rather than a consumer quietly
selecting a different population from the one the lander refuses.

**IT LIVES HERE, IN THE LEDGER, BECAUSE TWO PROGRAMS NEED IT AND THIS IS THE ONE THEY BOTH
ALREADY DEPEND ON.** ``bump_proposer`` reads it to know which bumps it may propose;
``landing_ledger.audit`` reads it to tell a subject that CANNOT be classified from one that
merely was not. A copy in each would be the estate's own N-copies-of-one-vocabulary defect,
where only the copies that run get corrected -- and the import direction is already settled:
the proposer imports the ledger, for the transcribed gate registry, and never the reverse.

**What this adds beyond the original is the two VERSIONS**, which `update_type_of` computes
and discards. They are the content of the package revision the proposer writes, so they
cannot be recovered from the classification alone -- which is why a mirror was needed at all
rather than a call.

**None is a refusal, never a default.** A requirement-range bump and a grouped bump both land
there, and both are correctly outside this lane: neither states a single delta that any rule
about update types could be applied to, and neither states two versions a revision could
carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# `bump <name> from <a> to <b>`, anchored at the end so a grouped bump -- whose title carries
# trailing text naming the group -- refuses rather than being classified on whichever
# dependency happens to be named. A requirement range (`from >=0.51.0 to >=0.52.1`) does not
# match at all, because the character after `to ` is not a digit. BYTE-IDENTICAL to
# `estate_landing_admission._BUMP`, and pinned to it.
BUMP_PATTERN: Final = r"\bfrom v?(\d[\d.]*) to v?(\d[\d.]*)$"
_BUMP: Final = re.compile(BUMP_PATTERN)

SEMVER_MAJOR: Final = "semver-major"
SEMVER_MINOR: Final = "semver-minor"
SEMVER_PATCH: Final = "semver-patch"

# How the update bot spells the same three in its own `updated-dependencies` trailer, which is
# the value the auto-merge gate is written against and the value `landing_ledger.rules`
# transcribes. Spelled here so the producer can ask the transcribed rule the question the gate
# asks, rather than re-deciding what the gate would have done.
DECLARED_PREFIX: Final = "version-update:"


@dataclass(frozen=True)
class Bump:
    """A single version delta a title states, and what kind of change it is."""

    from_version: str
    to_version: str
    kind: str

    @property
    def declared(self) -> str:
        """The update type in the bot's own vocabulary, for the transcribed gate rule."""
        return f"{DECLARED_PREFIX}{self.kind}"


def bump_of(title: str) -> Bump | None:
    """The delta this title declares, or None when it declares none."""
    match = _BUMP.search(title.strip())
    if match is None:
        return None
    before_text, after_text = match.group(1), match.group(2)
    before, after = _version(before_text), _version(after_text)
    if before is None or after is None or after <= before:
        return None
    if after[0] != before[0]:
        kind = SEMVER_MAJOR
    elif after[1] != before[1]:
        kind = SEMVER_MINOR
    else:
        kind = SEMVER_PATCH
    return Bump(from_version=before_text, to_version=after_text, kind=kind)


def _version(text: str) -> tuple[int, int, int] | None:
    """A dotted version as three components, padding a short one with zeros.

    Padding is what makes `from 4 to 7` -- how the workflow-automation ecosystem is versioned
    -- read as the major change it is, rather than as unparseable.
    """
    parts = text.split(".")
    if len(parts) > 3 or any(not part.isdigit() for part in parts):
        return None
    padded = [*parts, "0", "0"][:3]
    return int(padded[0]), int(padded[1]), int(padded[2])
