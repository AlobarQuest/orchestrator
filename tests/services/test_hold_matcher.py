"""The orchestrator's hold matcher: which bumps exceed which holds, read from what Dependabot wrote.

The functions are private-prefixed until Task 3 wires the first production caller
(`test_unreachable_guards.py` refuses a public service function nothing reaches).
"""

from datetime import UTC, datetime

import pytest

from orchestrator.services.membership.holds import (
    TOOLING_TO_DEPENDABOT,
    Bump,
    HoldBreach,
    bumps_of,
    dependencies_of,
    ecosystem_of_package_id,
    exceeded,
    holding,
)
from orchestrator.services.membership.members import HoldView

REPO = "AlobarQuest/brain"


def _hold(
    dependency: str = "sqlalchemy",
    ceiling: str = "2.0.54",
    *,
    ecosystem: str = "pip",
    directory: str = "/",
) -> HoldView:
    return HoldView(
        ecosystem=ecosystem,
        directory=directory,
        dependency=dependency,
        ceiling=ceiling,
        reason="pinned until the migration lands",
        set_by="human:devon",
        set_at=datetime(2026, 10, 28, tzinfo=UTC),
    )


def _bump(
    to_version: str,
    dependency: str = "sqlalchemy",
    *,
    ecosystem: str = "pip",
    directory: str | None = "/",
) -> Bump:
    return Bump(
        ecosystem=ecosystem, directory=directory, dependency=dependency, to_version=to_version
    )


@pytest.mark.parametrize("version", ["2.0.53", "2.0.54", "1.9", "2.0.54.0", "v2.0.54", "2.0.54+b1"])
def test_at_or_below_the_ceiling_is_admitted(version: str) -> None:
    assert exceeded([_hold()], REPO, [_bump(version)]) == ()


@pytest.mark.parametrize("version", ["2.0.55", "2.1", "3", "2.0.54.1", "v2.0.55rc1"])
def test_above_the_ceiling_breaches(version: str) -> None:
    assert exceeded([_hold()], REPO, [_bump(version)]) == (
        HoldBreach(
            repository=REPO,
            ecosystem="pip",
            directory="/",
            dependency="sqlalchemy",
            ceiling="2.0.54",
            version=version,
        ),
    )


@pytest.mark.parametrize("version", ["latest", "", "1!2.0", "2.x", "^2.0.55"])
def test_an_unreadable_version_on_a_held_dependency_breaches(version: str) -> None:
    assert len(exceeded([_hold()], REPO, [_bump(version)])) == 1


def test_an_unreadable_ceiling_breaches_every_bump_of_its_dependency() -> None:
    assert len(exceeded([_hold(ceiling="2.x")], REPO, [_bump("0.1")])) == 1


def test_an_unreadable_version_on_an_unheld_dependency_is_admitted() -> None:
    assert exceeded([_hold()], REPO, [_bump("latest", "alembic")]) == ()


def test_no_holds_admits_everything() -> None:
    assert exceeded([], REPO, [_bump("99")]) == ()


def test_a_bump_with_no_directory_matches_a_hold_on_any_directory() -> None:
    hold = _hold(directory="/services/api")
    assert len(exceeded([hold], REPO, [_bump("3", directory=None)])) == 1


def test_a_bump_in_another_directory_is_admitted() -> None:
    hold = _hold(directory="/services/api")
    assert exceeded([hold], REPO, [_bump("3", directory="/")]) == ()


def test_a_trailing_slash_on_the_bump_directory_matches() -> None:
    hold = _hold(directory="/services/api")
    assert len(exceeded([hold], REPO, [_bump("3", directory="/services/api/")])) == 1


def test_another_ecosystem_is_admitted() -> None:
    assert exceeded([_hold()], REPO, [_bump("3", ecosystem="uv")]) == ()


@pytest.mark.parametrize(
    "name", ["Sql-Alchemy", "sql_alchemy", "Sql.Alchemy", "sql--alchemy", "SQL_.alchemy"]
)
@pytest.mark.parametrize("ecosystem", ["pip", "uv"])
def test_pip_and_uv_names_compare_by_pep_503(name: str, ecosystem: str) -> None:
    held = _hold("sql-alchemy", ecosystem=ecosystem)
    assert len(exceeded([held], REPO, [_bump("3", name, ecosystem=ecosystem)])) == 1


def test_holding_names_every_hold_on_the_dependency_whatever_the_versions() -> None:
    """The uv floor's question: is the dependency held at all? Same name, ecosystem and directory
    rules as `exceeded`, but a version below the ceiling still answers yes."""
    below = Bump("uv", None, "SQLAlchemy", "1.0")
    on_it = _hold(ecosystem="uv", directory="/sub")
    others = (_hold("alembic", ecosystem="uv"), _hold(ecosystem="pip"))
    assert holding((on_it, *others), below) == (on_it,)
    assert holding(others, below) == ()
    assert holding((on_it,), Bump("uv", "/", "sqlalchemy", "1.0")) == ()


def test_npm_names_compare_lowercased_and_keep_their_scope() -> None:
    hold = _hold("@types/node", "20", ecosystem="npm_and_yarn")
    assert (
        len(exceeded([hold], REPO, [_bump("22.1.0", "@Types/Node", ecosystem="npm_and_yarn")])) == 1
    )
    # An npm name is not PEP 503 normalised: `_` and `-` are different packages there.
    other = _hold("lodash_es", "1", ecosystem="npm_and_yarn")
    assert exceeded([other], REPO, [_bump("9", "lodash-es", ecosystem="npm_and_yarn")]) == ()


def test_other_ecosystems_compare_names_lowercased() -> None:
    hold = _hold("actions/checkout", "4", ecosystem="github_actions")
    assert (
        len(exceeded([hold], REPO, [_bump("5", "Actions/Checkout", ecosystem="github_actions")]))
        == 1
    )


GROUPED = """chore(deps): bump the python group across 1 directory with 3 updates

Bumps the python group with 3 updates in the / directory.

---
updated-dependencies:
- dependency-name: alembic
  dependency-version: 1.18.0
  dependency-type: direct:production
  update-type: version-update:semver-minor
  dependency-group: python
- dependency-name: sqlalchemy
  dependency-version: 2.1.6
  dependency-type: direct:production
  update-type: version-update:semver-minor
  dependency-group: python
- dependency-name: "httpx"
  dependency-version: '0.28.1'
  dependency-type: direct:production
  update-type: version-update:semver-patch
  dependency-group: python
...

Signed-off-by: dependabot[bot] <support@github.com>
"""

SINGLE = """chore(deps-dev): bump ruff from 0.15.22 to 0.16.1 (#50)

---
updated-dependencies:
- dependency-name: ruff
  dependency-version: 0.16.0
  dependency-type: direct:development
  update-type: version-update:semver-minor
...
"""

WITH_DIRECTORY = """bump the npm group across 2 directories with 2 updates

---
updated-dependencies:
- dependency-name: "@types/node"
  dependency-version: 22.1.0
  dependency-type: direct:development
  update-type: version-update:semver-major
  directory: "/web"
  dependency-group: npm
- dependency-name: react
  dependency-version: 19.0.0
  dependency-type: direct:production
  update-type: version-update:semver-major
  directory: /
  dependency-group: npm
...
"""


def test_a_grouped_message_reads_every_member() -> None:
    assert dependencies_of(GROUPED, "pip") == (
        Bump("pip", None, "alembic", "1.18.0"),
        Bump("pip", None, "sqlalchemy", "2.1.6"),
        Bump("pip", None, "httpx", "0.28.1"),
    )


def test_a_grouped_message_with_one_held_member_breaches() -> None:
    bumps = bumps_of(
        "chore(deps): bump the python group across 1 directory with 3 updates", GROUPED, "pip"
    )
    assert bumps is not None
    breaches = exceeded([_hold()], REPO, bumps)
    assert [b.dependency for b in breaches] == ["sqlalchemy"]
    assert breaches[0].version == "2.1.6"


def test_the_directory_is_read_when_the_block_carries_it() -> None:
    assert dependencies_of(WITH_DIRECTORY, "npm_and_yarn") == (
        Bump("npm_and_yarn", "/web", "@types/node", "22.1.0"),
        Bump("npm_and_yarn", "/", "react", "19.0.0"),
    )


@pytest.mark.parametrize(
    "message",
    [
        "chore: tidy\n\nNo trailer here.\n",
        "---\nupdated-dependencies:\n...\n",
        # An entry with no name is not a list this can read.
        "---\nupdated-dependencies:\n- dependency-version: 1.0\n...\n",
        # A key twice in one entry: which one Dependabot meant is not readable.
        "---\nupdated-dependencies:\n- dependency-name: a\n  dependency-name: b\n...\n",
        # A line inside the block that is not a key and value.
        "---\nupdated-dependencies:\n- dependency-name: a\n  garbage\n...\n",
    ],
)
def test_a_message_with_no_readable_block_returns_none(message: str) -> None:
    assert dependencies_of(message, "pip") is None


def test_a_block_without_a_version_reads_an_empty_version_which_a_hold_refuses() -> None:
    message = "---\nupdated-dependencies:\n- dependency-name: sqlalchemy\n...\n"
    bumps = dependencies_of(message, "pip")
    assert bumps == (Bump("pip", None, "sqlalchemy", ""),)
    assert len(exceeded([_hold()], REPO, bumps or ())) == 1


def test_a_single_update_takes_its_version_from_the_title() -> None:
    # The block lags a rewritten pull request (0.16.0); the title tracked it (0.16.1).
    title = "chore(deps-dev): bump ruff from 0.15.22 to 0.16.1"
    assert bumps_of(title, SINGLE, "pip") == (Bump("pip", None, "ruff", "0.16.1"),)
    hold = _hold("ruff", "0.16.0")
    assert len(exceeded([hold], REPO, bumps_of(title, SINGLE, "pip") or ())) == 1


def test_a_single_update_whose_title_does_not_read_falls_back_to_the_block() -> None:
    title = "chore(deps): bump ruff requirement from >=0.15 to >=0.16"
    assert bumps_of(title, SINGLE, "pip") == (Bump("pip", None, "ruff", "0.16.0"),)


def test_a_grouped_update_takes_both_from_the_block() -> None:
    title = "chore(deps): bump the python group across 1 directory with 3 updates"
    bumps = bumps_of(title, GROUPED, "pip")
    assert bumps is not None
    assert [b.to_version for b in bumps] == ["1.18.0", "2.1.6", "0.28.1"]


def test_no_block_is_none_whatever_the_title_says() -> None:
    assert bumps_of("bump ruff from 0.15.22 to 0.16.1", "no trailer", "pip") is None


def test_the_tooling_mapping_is_dependabot_spelling() -> None:
    assert TOOLING_TO_DEPENDABOT == {"npm": "npm_and_yarn", "pip": "pip", "uv": "uv"}


@pytest.mark.parametrize(
    ("package_id", "repository", "ecosystem"),
    [
        ("brain-pip-sqlalchemy", "AlobarQuest/brain", "pip"),
        ("garexchange-npm-types-node", "AlobarQuest/garexchange", "npm_and_yarn"),
        ("intent-packages-uv-ruff", "AlobarQuest/intent-packages", "uv"),
        # A repository whose own name holds a tooling word reads from after its name.
        ("pip-tools-npm-left-pad", "AlobarQuest/pip-tools", "npm_and_yarn"),
        ("brain-cargo-serde", "AlobarQuest/brain", None),
        ("other-pip-sqlalchemy", "AlobarQuest/brain", None),
    ],
)
def test_the_ecosystem_of_a_package_id(
    package_id: str, repository: str, ecosystem: str | None
) -> None:
    assert ecosystem_of_package_id(package_id, repository) == ecosystem
