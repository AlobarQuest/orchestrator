"""The two hold matchers decide alike: one corpus, both copies, identical answers.

The orchestrator copy (`orchestrator.services.membership.holds`) serves the in-process admissions;
the lane copy (`sds_members.holds`) serves the bump proposer and the work carrier, which can't
import the orchestrator. Neither may drift, so every input below goes through both.
"""

from dataclasses import astuple

import pytest

from landing_ledger.titles import BUMP_PATTERN
from orchestrator.services.membership import holds as service
from sds_members import holds as lane
from sds_members.client import Hold
from standing_identity import TOOLING_NAMES, ecosystem_of

HOLDS = (
    Hold("pip", "/", "sqlalchemy", "2.0.54"),
    Hold("uv", "/", "Ruff", "0.16.0"),
    Hold("npm_and_yarn", "/web", "@types/node", "20"),
    Hold("github_actions", "/", "actions/checkout", "4.2"),
    Hold("pip", "/", "broken", "2.x"),
    Hold("pip", "/", "flask-cors", "4"),
    Hold("npm_and_yarn", "/", "lodash-es", "4"),
)
REPO = "AlobarQuest/brain"

VERSIONS = (
    "2.0.54",
    "2.0.54.0",
    "2.0.55",
    "v2.0.55rc1",
    "2.0.54+b1",
    "1.2.3-beta.1",
    "1.2.3.post1",
    "1!2.0",
    "latest",
    "",
    "1.x",
    "1.2.*",
    "1.",
    "2..0",
    " 1.0",
    "^1.2",
)

GROUPED = """bump the python group across 1 directory with 3 updates

---
updated-dependencies:
- dependency-name: alembic
  dependency-version: 1.18.0
  dependency-type: direct:production
  dependency-group: python
- dependency-name: SQL_Alchemy
  dependency-version: 2.1.6
  dependency-group: python
- dependency-name: broken
  dependency-version: 0.1
...
"""
SINGLE = """chore(deps-dev): bump ruff from 0.15.22 to 0.16.1 (#50)

---
updated-dependencies:
- dependency-name: ruff
  dependency-version: 0.16.0
  update-type: version-update:semver-minor
...
"""
DIRECTORIES = """bump the npm group across 2 directories with 2 updates

---
updated-dependencies:
- dependency-name: "@Types/Node"
  dependency-version: '22.1.0'
  directory: "/web/"
- dependency-name: "@types/node"
  dependency-version: 22.1.0
  directory: /
...
"""

# (title, commit message, ecosystem)
# Spellings PEP 503 equates with `flask-cors` and npm does not equate with `lodash-es`: a copy
# that case-folds pip names instead of normalising them, or normalises npm names, disagrees here.
SPELLINGS = (
    "Flask_Cors",
    "flask.cors",
    "flask_cors",
    "FLASK--CORS",
    "lodash_es",
    "Lodash.ES",
    "other",
)

# A single update whose block lags its rewritten pull request (block 0.16.2, see below).
LAGGING = SINGLE.replace("0.16.0", "0.16.2")

# (title, commit message, ecosystem, expected): `None` unreadable, else whether a hold breaches.
# Each of these is decided by both copies against an expected answer, not only against each other.
DECIDED = (
    # A subdirectory single update: the title's 0.16.1 beats the lagging block's 0.16.0 (> 0.16.0).
    ("chore(deps-dev): bump ruff from 0.15.22 to 0.16.1 in /tools", SINGLE, "uv", True),
    # A title lower than its block: the block's 0.16.2 stands, beyond Ruff's 0.16.0.
    ("chore(deps-dev): bump ruff from 0.15.22 to 0.15.9", LAGGING, "uv", True),
    # Title readable, block version not: the version is unreadable, so a hold breaches.
    ("bump ruff from 0.15.22 to 0.15.9", SINGLE.replace("0.16.0", "latest"), "uv", True),
    # A blank line inside a block: the list is unreadable, never cut short before sqlalchemy.
    (
        "x",
        "---\nupdated-dependencies:\n- dependency-name: alembic\n  dependency-version: 1\n\n"
        "- dependency-name: sqlalchemy\n  dependency-version: 3\n...\n",
        "pip",
        None,
    ),
    # A block the message ends before closing.
    (
        "x",
        "---\nupdated-dependencies:\n- dependency-name: alembic\n  dependency-version: 1\n",
        "pip",
        None,
    ),
    # Under the ceiling, read in full: admitted.
    ("chore(deps-dev): bump ruff from 0.15.22 to 0.15.30 in /", SINGLE, "uv", False),
)

MESSAGES = (
    ("bump the python group across 1 directory with 3 updates", GROUPED, "pip"),
    ("chore(deps-dev): bump ruff from 0.15.22 to 0.16.1", SINGLE, "uv"),
    ("chore(deps-dev): bump ruff from 0.15.22 to 0.16.0", SINGLE, "uv"),
    ("chore(deps): bump ruff requirement from >=0.15 to >=0.16", SINGLE, "uv"),
    ("bump the npm group across 2 directories with 2 updates", DIRECTORIES, "npm_and_yarn"),
    (
        "bump actions/checkout from 4.2 to 5",
        SINGLE.replace("ruff", "Actions/Checkout"),
        "github_actions",
    ),
    ("chore: tidy", "no block at all", "pip"),
    ("x", "---\nupdated-dependencies:\n...\n", "pip"),
    ("x", "---\nupdated-dependencies:\n- dependency-version: 1\n...\n", "pip"),
    ("x", "---\nupdated-dependencies:\n- dependency-name: a\n  dependency-name: b\n...\n", "pip"),
    ("x", "---\nupdated-dependencies:\n- dependency-name: a\n  junk\n...\n", "pip"),
    ("x", "---\nupdated-dependencies:\n- dependency-name: sqlalchemy\n...\n", "pip"),
    *((title, message, ecosystem) for title, message, ecosystem, _ in DECIDED),
)


def _fields(result: object) -> object:
    if result is None:
        return None
    assert isinstance(result, tuple)
    return tuple(astuple(item) for item in result)


@pytest.mark.parametrize("version", VERSIONS)
def test_both_copies_read_a_release_alike(version: str) -> None:
    assert service.release(version) == lane.release(version)


@pytest.mark.parametrize(("title", "message", "ecosystem"), MESSAGES)
def test_both_copies_read_a_message_alike(title: str, message: str, ecosystem: str) -> None:
    assert _fields(service.dependencies_of(message, ecosystem)) == _fields(
        lane.dependencies_of(message, ecosystem)
    )
    ours, theirs = (
        service.bumps_of(title, message, ecosystem),
        lane.bumps_of(title, message, ecosystem),
    )
    assert _fields(ours) == _fields(theirs)
    if ours is not None and theirs is not None:
        assert _fields(service.exceeded(HOLDS, REPO, ours)) == _fields(
            lane.exceeded(HOLDS, REPO, theirs)
        )


@pytest.mark.parametrize(("title", "message", "ecosystem", "expected"), DECIDED)
def test_both_copies_decide_these_messages_as_expected(
    title: str, message: str, ecosystem: str, expected: bool | None
) -> None:
    ours, theirs = (
        service.bumps_of(title, message, ecosystem),
        lane.bumps_of(title, message, ecosystem),
    )
    if expected is None:
        assert ours is None
        assert theirs is None
        return
    assert ours is not None
    assert theirs is not None
    assert bool(service.exceeded(HOLDS, REPO, ours)) is expected
    assert bool(lane.exceeded(HOLDS, REPO, theirs)) is expected


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("hold", HOLDS)
def test_both_copies_decide_a_bump_alike(version: str, hold: Hold) -> None:
    for directory in (None, "/", "/web", "/web/"):
        for dependency in (hold.dependency, hold.dependency.upper(), *SPELLINGS):
            ours = (service.Bump(hold.ecosystem, directory, dependency, version),)
            theirs = (lane.Bump(hold.ecosystem, directory, dependency, version),)
            assert _fields(service.exceeded([hold], REPO, ours)) == _fields(
                lane.exceeded([hold], REPO, theirs)
            )


def test_some_corpus_entry_breaches_and_some_admits() -> None:
    """Kills: a corpus that agrees only because both copies return nothing."""
    outcomes = set()
    for title, message, ecosystem in MESSAGES:
        bumps = lane.bumps_of(title, message, ecosystem)
        outcomes.add("none" if bumps is None else bool(lane.exceeded(HOLDS, REPO, bumps)))
    assert outcomes == {"none", True, False}


def test_both_title_reads_are_the_landers() -> None:
    assert service._BUMP_PATTERN == lane.BUMP_PATTERN == BUMP_PATTERN


def test_both_copies_map_tooling_alike_and_cover_every_tooling_name() -> None:
    assert service.TOOLING_TO_DEPENDABOT == lane.TOOLING_TO_DEPENDABOT
    assert set(service.TOOLING_TO_DEPENDABOT) == set(TOOLING_NAMES)


@pytest.mark.parametrize(
    ("package_id", "repository"),
    [
        ("brain-pip-sqlalchemy", "AlobarQuest/brain"),
        ("garexchange-npm-types-node", "AlobarQuest/garexchange"),
        ("intent-packages-uv-ruff", "AlobarQuest/intent-packages"),
        ("pip-tools-npm-left-pad", "AlobarQuest/pip-tools"),
        ("brain-cargo-serde", "AlobarQuest/brain"),
        ("other-pip-sqlalchemy", "AlobarQuest/brain"),
    ],
)
def test_the_package_id_read_is_the_lanes(package_id: str, repository: str) -> None:
    tooling = ecosystem_of(package_id, repository)
    expected = None if tooling is None else lane.TOOLING_TO_DEPENDABOT[tooling]
    assert service.ecosystem_of_package_id(package_id, repository) == expected
    assert lane.ecosystem_of_package_id(package_id, repository) == expected


@pytest.mark.parametrize("hold", HOLDS)
def test_both_copies_find_the_holds_on_a_dependency_alike(hold: Hold) -> None:
    for directory in (None, "/", "/web", "/web/"):
        for dependency in (hold.dependency, hold.dependency.upper(), *SPELLINGS):
            for ecosystem in (hold.ecosystem, "docker"):
                ours = service.Bump(ecosystem, directory, dependency, "0")
                theirs = lane.Bump(ecosystem, directory, dependency, "0")
                # Both return the hold objects they were given, so equal tuples are equal answers.
                assert service.holding(HOLDS, ours) == lane.holding(HOLDS, theirs)


@pytest.mark.parametrize("version", ("0.1", "0.15.9", "0.16.0", "0.17", "1", "99", "latest"))
@pytest.mark.parametrize("hold", HOLDS)
def test_both_copies_decide_a_factory_bump_alike(version: str, hold: Hold) -> None:
    for ecosystem in ("uv", "UV", "pip", "npm_and_yarn", "github_actions"):
        for dependency in (hold.dependency, *SPELLINGS):
            ours = service.Bump(ecosystem, None, dependency, version)
            theirs = lane.Bump(ecosystem, None, dependency, version)
            assert _fields(service.factory_breaches(HOLDS, REPO, ours)) == _fields(
                lane.factory_breaches(HOLDS, REPO, theirs)
            )


@pytest.mark.parametrize(
    ("ecosystem", "dependency", "version", "breaches"),
    [
        # uv's version is a `>=` floor: below the ceiling still breaches.
        ("uv", "ruff", "0.1", True),
        ("uv", "other", "0.1", False),
        # pip pins exactly: below the ceiling is admitted, beyond it breaches.
        ("pip", "sqlalchemy", "2.0.1", False),
        ("pip", "sqlalchemy", "2.0.55", True),
    ],
)
def test_both_copies_apply_the_uv_floor_to_a_factory_bump(
    ecosystem: str, dependency: str, version: str, breaches: bool
) -> None:
    for copy in (service, lane):
        bump = copy.Bump(ecosystem, None, dependency, version)
        assert bool(copy.factory_breaches(HOLDS, REPO, bump)) is breaches, copy.__name__


def test_holding_finds_some_dependencies_and_not_others() -> None:
    """Kills: two `holding` copies that agree only because both return nothing, or everything."""
    found = {
        bool(lane.holding(HOLDS, lane.Bump("pip", None, name, "0")))
        for name in ("Flask_Cors", "other")
    }
    assert found == {True, False}
