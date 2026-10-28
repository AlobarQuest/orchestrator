"""A requirement-range title, read by this producer alone (bridge Part 3, Ruling 7).

`landing_ledger.titles.BUMP_PATTERN` is byte-identical to the estate lander's and pinned to it, so
widening it would start the lander admitting ranges. This parse sits beside it instead, and only
the proposer reads it.

A range with ONE clause and the same operator on both sides (`from >=1.2.0 to >=1.3.0`) names two
versions, and those bare versions are what a standing package revision carries. Anything with two
clauses, a changed operator or no increase is still no single delta and answers None.
"""

from __future__ import annotations

import re
from typing import Final

from landing_ledger.titles import Bump, bump_of

_RANGE: Final = re.compile(
    r"\brequirement from (>=|~=|==|\^|~)\s*v?(\d[\d.]*) to (>=|~=|==|\^|~)\s*v?(\d[\d.]*)$"
)


def range_bump_of(title: str) -> tuple[str, Bump] | None:
    """The operator, and the two bare versions and kind of change, a single-clause range title
    states. Which operators a tooling profile can carry is the caller's question."""
    match = _RANGE.search(title.strip())
    if match is None or match.group(1) != match.group(3):
        return None
    # Classified by the shared parser, so a range bump's kind is the same answer a plain bump of
    # the same two versions gets.
    bump = bump_of(f"from {match.group(2)} to {match.group(4)}")
    return None if bump is None else (match.group(1), bump)
