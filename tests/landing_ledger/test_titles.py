"""The delta parser, held to the one it copies.

`landing_ledger.titles` is a second copy of
`orchestrator.services.estate_landing_admission.update_type_of`, because the programs that
read it cannot import that module -- they are out-of-process programs and that module reaches
SQLAlchemy. The copy is therefore PINNED here rather than trusted: a test that imports both,
and asserts they agree on a corpus, which includes every open Dependabot pull request the
estate carried on the day this shipped.

TWO CONSUMERS READ IT, which is why it lives in the ledger rather than in either of them:
`bump_proposer` decides which bumps it may propose, and `landing_ledger.audit` decides which
open updates can never be classified at all. This pin is what keeps all three answers one
answer.

A byte comparison of the two regexes is also asserted, but it is the weaker of the two checks
and is here only to name the drift quickly. What actually matters is the agreement.
"""

from __future__ import annotations

import pytest

from landing_ledger.titles import BUMP_PATTERN, bump_of
from orchestrator.services.estate_landing_admission import _BUMP, update_type_of

# The DISTINCT titles of every open Dependabot pull request across the six cascade
# repositories, measured 2026-08-19: thirteen pull requests, ten distinct titles, because the
# same setuptools requirement bump is open on four repositories. Seven of the thirteen -- four
# of the ten -- state no single delta, and that distribution is the point: the lane's real
# subject is the classifiable remainder, and a parser that classified a requirement range would
# put unrunnable proposals into it.
LIVE_TITLES = [
    "chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
    "chore(deps): bump dependabot/fetch-metadata from 2.5.0 to 3.1.0",
    "chore(deps): update uvicorn[standard] requirement from >=0.52.0 to >=0.52.1",
    "chore(deps): update pyjwt[crypto] requirement from >=2.8 to >=2.13.0",
    "chore(deps): bump python from 3.12-slim to 3.14-slim",
    "build(deps-dev): bump eslint from 9.39.4 to 10.8.1",
    "build(deps-dev): bump typescript from 5.9.3 to 7.0.2",
    "build(deps): bump zod from 3.25.76 to 4.4.3",
    "chore(actions): bump astral-sh/setup-uv from 5 to 7",
    "chore(actions): bump actions/checkout from 4 to 7",
]

EDGE_TITLES = [
    "bump a from 1.2.3 to 1.2.4",
    "bump a from 1.2.3 to 1.3.0",
    "bump a from 1.2.3 to 2.0.0",
    "bump a from v1.2.3 to v2.0.0",
    "bump a from 4 to 7",
    "bump a from 2.0.0 to 1.0.0",
    "bump a from 1.0.0 to 1.0.0",
    "bump a from 1.2.3.4 to 1.2.3.5",
    "bump a from 1.2.3 to 2.0.0 in the group x",
    "bump a from >=1 to >=2",
    "bump a from 3.12-slim to 3.14-slim",
    "",
    "no delta here",
]


@pytest.mark.parametrize("title", LIVE_TITLES + EDGE_TITLES)
def test_the_copy_classifies_exactly_as_the_original(title) -> None:
    bump = bump_of(title)
    kind = None if bump is None else bump.kind
    assert kind == update_type_of(title), title


def test_the_two_patterns_are_byte_identical() -> None:
    assert BUMP_PATTERN == _BUMP.pattern


def test_the_versions_are_carried_through() -> None:
    """The whole reason a copy exists: the original computes these and discards them."""
    bump = bump_of("build(deps): bump zod from 3.25.76 to 4.4.3")
    assert bump is not None
    assert (bump.from_version, bump.to_version) == ("3.25.76", "4.4.3")
    assert bump.declared == "version-update:semver-major"


def test_four_of_the_ten_live_titles_state_no_delta() -> None:
    """A census, not a shape check.

    If a future change made requirement ranges classifiable, this lane would start proposing
    work whose two versions nobody can name -- and the failure would be a package revision
    carrying a range, not a parser error.
    """
    unclassifiable = [t for t in LIVE_TITLES if bump_of(t) is None]
    assert len(unclassifiable) == 4
    assert all("requirement from" in t or "slim" in t for t in unclassifiable)
