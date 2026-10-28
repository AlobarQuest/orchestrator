"""The proposer's own reading of a requirement-range title (Ruling 7).

The shared title regex is pinned to the estate lander's and must not start admitting ranges, so the
range parse lives beside it, in the proposer, and is used by nothing else.
"""

from __future__ import annotations

import pytest

from bump_proposer.titles import range_bump_of
from landing_ledger.titles import BUMP_PATTERN, Bump, bump_of


def test_a_range_bump_is_classified() -> None:
    bump = range_bump_of("chore(deps): update zod requirement from >=1.2.0 to >=1.3.0")

    assert bump == (">=", Bump(from_version="1.2.0", to_version="1.3.0", kind="semver-minor"))


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
            (">=", Bump("83.0.0", "84.0.0", "semver-major")),
        ),
        (
            "chore(deps): update pyjwt[crypto] requirement from >=2.8 to >=2.13.0",
            (">=", Bump("2.8", "2.13.0", "semver-minor")),
        ),
        (
            "build(deps): update react requirement from ^18.2.0 to ^19.0.0",
            ("^", Bump("18.2.0", "19.0.0", "semver-major")),
        ),
        (
            "build(deps): update lodash requirement from ~4.17.20 to ~4.17.21",
            ("~", Bump("4.17.20", "4.17.21", "semver-patch")),
        ),
        (
            "chore(deps): update attrs requirement from ~=23.1 to ~=24.2",
            ("~=", Bump("23.1", "24.2", "semver-major")),
        ),
    ],
)
def test_single_operator_ranges_give_their_bare_versions(
    title: str, expected: tuple[str, Bump]
) -> None:
    assert range_bump_of(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        # Two clauses: no single pair of versions.
        "chore(deps): update httpx requirement from <0.30,>=0.29 to >=0.29,<0.31",
        # The operator changed, so the two sides do not state one kind of constraint.
        "chore(deps): update zod requirement from >=1.2.0 to ^1.3.0",
        # A downgrade is no bump.
        "chore(deps): update zod requirement from >=1.3.0 to >=1.2.0",
        # A plain bump is the shared parser's, never this one's.
        "build(deps): bump zod from 3.25.76 to 4.4.3",
        "chore(deps): bump python from 3.12-slim to 3.14-slim",
    ],
)
def test_anything_else_is_not_a_range_bump(title: str) -> None:
    assert range_bump_of(title) is None


def test_the_shared_parser_still_refuses_a_range() -> None:
    """Kills: widening `BUMP_PATTERN` instead. It is the lander's, byte for byte."""
    assert BUMP_PATTERN == r"\bfrom v?(\d[\d.]*) to v?(\d[\d.]*)$"
    assert bump_of("chore(deps): update zod requirement from >=1.2.0 to >=1.3.0") is None
