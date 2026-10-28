"""Which open pull requests this lane's subject is -- measured against the real population.

**THE CORPUS IS THE ESTATE, not an invention.** Every case below is an open update-bot pull
request that existed on 2026-08-31, with the check state it actually carried, classified against
the rule change-manager actually declares. A classifier tested only on shapes somebody thought of
passes while being wrong for every real repository, which this estate has already paid for once.

**IT WAS RE-MEASURED FOR ADR-0038 RATHER THAN RE-EXPRESSED**, and the reason is that the corpus's
COLUMNS moved. Until now each row carried the gate blob installed on its repository, because six
repositories could be on six transcribed revisions; one declaration now covers all of them, so
that column has no values left to vary. What varies instead is what the required checks concluded
-- the axis ADR-0034 moved the split onto -- and those states were never recorded for the
2026-08-19 population, most of which has since landed. A corpus of closed pull requests carrying
invented check states, judged by a rule that did not exist when they were open, would be a
fiction wearing the word "measured".

**WHAT THE RE-MEASUREMENT COST, STATED PLAINLY:** the estate had no GREEN open update on
2026-08-31 -- all four carry at least one concluded failure -- so the corpus contains no row
exercising the permitted path. That path is guarded by the named counterfactual controls below,
on a real subject, and this note exists so a later reader does not mistake its absence from the
corpus for an untested branch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bump_proposer.cli import FAILURE_SETTLE_SECONDS, _consider
from bump_proposer.landing_policy import parse
from bump_proposer.standing import StandingPackage
from landing_ledger.model import Check, PendingUpdate, UpdateMetadata
from sds_members.client import Hold
from tests.bump_proposer.test_landing_policy import LIVE

# The declaration `GET /api/landing-policy` served on 2026-08-31, parsed. ONE rule for every
# repository: `docker` excluded, nothing asked about the version delta, and the required checks
# deciding the rest.
RULE = parse(LIVE)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
SETTLED = NOW - timedelta(seconds=FAILURE_SETTLE_SECONDS + 1)
JUST_CONCLUDED = NOW - timedelta(seconds=FAILURE_SETTLE_SECONDS - 1)
FAILED = (Check(name="Quality", conclusion="failure", run=1),)
PASSED = (Check(name="Quality", conclusion="success", run=1),)
# orchestrator#3's real state on 2026-08-31: one required check failed and one passed. A pull
# request is refused by ONE failure, so a corpus row carrying only the failure would understate
# what the classifier had to read.
MIXED = (
    Check(name="Quality", conclusion="failure", run=1),
    Check(name="Runner consumer compatibility", conclusion="success", run=2),
)

MAJOR = "version-update:semver-major"
MINOR = "version-update:semver-minor"


def _pending(
    repository: str,
    number: int,
    title: str,
    *,
    dependency: str | None,
    ecosystem: str | None,
    update_type: str | None,
    checks: tuple[Check, ...] = (),
    concluded_at: datetime | None = None,
) -> PendingUpdate:
    """One open pull request as the ledger's own reader would return it.

    `update` is None exactly when the commit carries no `dependency-name`, which is what
    `landing_ledger.github.update_metadata` returns None for -- and ONLY that, since 2026-08-28.
    A requirement-range bump carries a dependency name and no update type, so it arrives as
    metadata whose `update_type` is None rather than as no metadata at all: the gate reads the
    ecosystem from the BRANCH and always has one, so discarding it with the missing update type
    threw away the only value revision 3457db3c reads. An earlier version of this helper passed
    `str(None)` to satisfy a type checker and turned every such row into a classification
    disagreement -- a fixture that no longer described the estate.

    `checks` and `concluded_at` are what the producer now reads to tell a bump the cascade WILL
    NOT land from one it is about to. They default to the state of a pull request whose checks
    have not concluded, which is fail-closed: neither lane acts.
    """
    return PendingUpdate(
        repository=repository,
        number=number,
        head_commit="a" * 40,
        opened_at=datetime(2026, 8, 1, tzinfo=UTC),
        armed=False,
        title=title,
        update=(
            UpdateMetadata(
                dependency=dependency,
                ecosystem=ecosystem,
                update_type=update_type,
            )
            if dependency is not None
            else None
        ),
        checks=checks,
        last_concluded_at=concluded_at,
    )


def _package(dependency, from_version="0.0.0", to_version="0.0.1", *, state="approved"):
    return StandingPackage(
        package_id=f"infraops-mcp-server-npm-{dependency}",
        path=Path("packages") / f"infraops-mcp-server-npm-{dependency}",
        target_repository="AlobarQuest/infraops-mcp-server",
        dependency=dependency,
        revision=1,
        state=state,
        from_version=from_version,
        to_version=to_version,
    )


PACKAGES = {
    ("alobarquest/infraops-mcp-server", name): _package(name)
    for name in ("zod", "typescript", "eslint")
}


# The open update-bot population on 2026-08-31, read through the ledger's own reader: four pull
# requests across the six declared repositories, each with the checks it actually carried.
#
# (label, repository, number, title, dependency, ecosystem, update_type, checks, expected)
LIVE_POPULATION = [
    # THE LANE'S SUBJECT. Two required checks concluded failure on 2026-08-30, and ADR-0034
    # assigns exactly that remainder to the factory: the declaration permits an npm bump, and
    # this one will never land, because the MCP SDK's `server.tool()` signature shifts under
    # zod 4 and the diff needs a code change before it is correct.
    (
        "zod",
        "AlobarQuest/infraops-mcp-server",
        71,
        "build(deps): bump zod from 3.25.76 to 4.4.3",
        "zod",
        "npm_and_yarn",
        MAJOR,
        FAILED,
        "candidate",
    ),
    # Refused by the checks, and the factory has no tooling for an action: since bridge Part 3
    # each holds one alert open for a person.
    (
        "checkout",
        "AlobarQuest/factory-runner",
        28,
        "chore(actions): bump actions/checkout from 4 to 7",
        "actions/checkout",
        "github_actions",
        MAJOR,
        FAILED,
        "alert",
    ),
    (
        "setup-uv",
        "AlobarQuest/factory-runner",
        31,
        "chore(actions): bump astral-sh/setup-uv from 5 to 7",
        "astral-sh/setup-uv",
        "github_actions",
        MAJOR,
        FAILED,
        "alert",
    ),
    # A docker tag that does not parse as semver. The declaration excludes `docker` outright and
    # the factory has no docker tooling, so it alerts whatever its title says (Ruling 6).
    (
        "python",
        "AlobarQuest/orchestrator",
        3,
        "chore(deps): bump python from 3.12-slim to 3.14-slim",
        "python",
        "docker",
        None,
        MIXED,
        "alert",
    ),
]


@pytest.mark.parametrize("case", LIVE_POPULATION, ids=[c[0] for c in LIVE_POPULATION])
def test_the_live_population_is_classified_as_measured(case) -> None:
    label, repository, number, title, dependency, ecosystem, update_type, checks, expected = case
    pending = _pending(
        repository,
        number,
        title,
        dependency=dependency,
        ecosystem=ecosystem,
        update_type=update_type,
        checks=checks,
        concluded_at=SETTLED,
    )
    route = _consider(pending, RULE, PACKAGES, NOW, holds=())
    if expected == "candidate":
        assert route.action == "act" and route.package is not None, route.detail
    else:
        assert route.action == "alert", route.detail
        expected_key = f"{repository.lower()}:{_encoded(ecosystem)}:{dependency}"
        assert route.key == f"dependency-bump:{expected_key}"


def _encoded(ecosystem: str) -> str:
    return ecosystem.replace("_", "__")


def test_a_bump_with_no_standing_package_is_routed_to_creation() -> None:
    """Bridge Part 3: a package is created for it, in its ecosystem's tooling.

    Its checks must have SETTLED AGAINST IT to get this far -- the declaration permits an npm
    major on its type, so nothing but the outcome keeps a bump in this lane.
    """
    pending = _pending(
        "AlobarQuest/infraops-mcp-server",
        99,
        "build(deps): bump left-pad from 1.0.0 to 2.0.0",
        dependency="left-pad",
        ecosystem="npm_and_yarn",
        update_type=MAJOR,
        checks=FAILED,
        concluded_at=SETTLED,
    )
    route = _consider(pending, RULE, PACKAGES, NOW, holds=())
    assert (route.action, route.package, route.tooling) == ("act", None, "npm"), route.detail


def test_a_title_and_a_trailer_that_disagree_are_ambiguous() -> None:
    """Kills: dropping the agreement check.

    The direction that matters is this one: the title says major, so this producer would
    propose it as work, while the gate -- which reads the TRAILER -- sees a minor and arms
    auto-merge. The factory would be sent to do something GitHub was already merging.
    """
    pending = _pending(
        "AlobarQuest/infraops-mcp-server",
        71,
        "build(deps): bump zod from 3.25.76 to 4.4.3",
        dependency="zod",
        ecosystem="npm_and_yarn",
        update_type=MINOR,
    )
    route = _consider(pending, RULE, PACKAGES, NOW, holds=())
    assert route.action == "ambiguous" and route.detail.startswith("ambiguous"), route.detail


def test_a_classifiable_title_with_no_trailer_is_ambiguous() -> None:
    """The other direction, and it is not the same case: the gate saw no declared intent, so
    it refused for a reason this producer cannot see from the title."""
    pending = _pending(
        "AlobarQuest/infraops-mcp-server",
        71,
        "build(deps): bump zod from 3.25.76 to 4.4.3",
        dependency=None,
        ecosystem=None,
        update_type=None,
    )
    route = _consider(pending, RULE, PACKAGES, NOW, holds=())
    assert route.action == "ambiguous" and route.detail.startswith("ambiguous"), route.detail


# ---------------------------------------------------------------------------------------------
# ADR-0034. Under revision 3457db3c the cascade permits anything it does not exclude, so
# `permits` alone no longer separates the lanes -- what the required checks concluded does.
# These are the acceptance controls for that change, stated as the questions they answer.
# ---------------------------------------------------------------------------------------------


def _zod(*, checks: tuple[Check, ...] = (), concluded_at: datetime | None = None) -> PendingUpdate:
    """infraops-mcp-server #71, which ADR-0034 names as the factory's archetype: the MCP SDK's
    `server.tool()` signature shifts under zod 4, so the bump needs a code change before its
    diff is correct. Only its check state varies below -- that is the axis the split moved to."""
    return _pending(
        "AlobarQuest/infraops-mcp-server",
        71,
        "build(deps): bump zod from 3.25.76 to 4.4.3",
        dependency="zod",
        ecosystem="npm_and_yarn",
        update_type=MAJOR,
        checks=checks,
        concluded_at=concluded_at,
    )


def test_a_settled_failure_is_still_this_lanes_subject_under_the_outcome_rule() -> None:
    """CONTROL 1. The factory's queue must not empty when the cascade stops refusing on type.

    zod #71 is the archetype ADR-0034 names: the MCP SDK's `server.tool()` signature shifts
    under zod 4, so the bump needs a code change before its diff is correct. Revision 3457db3c
    PERMITS it -- there is no update-type arm left to refuse it with -- and GitHub will never
    land it, because two required checks concluded failure on 2026-08-23. A producer reading
    `permits` alone would report it as the cascade's business and propose nothing, leaving the
    one bump the factory exists for in neither lane.
    """
    pending = _zod(checks=FAILED, concluded_at=SETTLED)

    route = _consider(pending, RULE, PACKAGES, NOW, holds=())

    assert route.action == "act" and route.package is not None, route.detail
    assert route.bump is not None and route.bump.kind == "semver-major"


def test_the_same_bump_passing_its_checks_is_left_to_the_cascade() -> None:
    """The other half of control 1, and the reason it is not simply "always take a major".

    Nothing about the bump changes here except what CI concluded, which is precisely the axis
    ADR-0034 moved the split onto. Sending the factory at a pull request GitHub is about to land
    would mint a package revision for a diff that was already correct.
    """
    pending = _zod(checks=PASSED, concluded_at=SETTLED)

    route = _consider(pending, RULE, PACKAGES, NOW, holds=())

    assert route.action == "skip"
    assert "permits" in route.detail and "have passed" in route.detail, route.detail


def test_a_failure_that_has_not_settled_belongs_to_neither_lane_yet() -> None:
    """`PendingUpdate.checks` holds only jobs that have CONCLUDED, so one failure among several
    still-running jobs is indistinguishable from a final verdict except by how recently the last
    conclusion arrived. Taking it early mints a package revision that cannot be unminted, and a
    re-run that then goes green strands the record as superseded with a human approval spent."""
    pending = _zod(checks=FAILED, concluded_at=JUST_CONCLUDED)

    route = _consider(pending, RULE, PACKAGES, NOW, holds=())

    assert route.action == "skip"
    assert "have not concluded against it" in route.detail, route.detail


def test_a_pull_request_whose_checks_have_not_concluded_at_all_is_not_taken() -> None:
    """The state every Dependabot pull request is in for its first minutes. Fail-closed here
    means waiting: a pass that skips it costs a pass, and the next one picks it up."""
    pending = _zod()

    route = _consider(pending, RULE, PACKAGES, NOW, holds=())

    assert route.action == "skip"
    assert "have not concluded against it" in route.detail, route.detail


def test_an_excluded_ecosystem_alerts_however_green_its_checks() -> None:
    """THE ONE CELL WHERE THE DECLARATION AND "the checks decide" DISAGREE, and it has no open
    subject today, which is why it is stated rather than measured. Since bridge Part 3 the factory
    has no docker tooling, so the answer is an alert, even with a package naming the dependency
    (Ruling 6).

    `docker` is excluded because the required checks do not exercise what changed: a tag is not
    semver, so the version number promises nothing, and nothing RUNS the built image -- a package
    that installs cleanly and fails at import on a removed standard-library module passes every
    check this estate has. So a green docker bump is still refused by the lane, and refused is
    what puts it here. A predicate that asked only what CI concluded would hand it to nobody.

    The estate's one open docker update (orchestrator#3) never reaches this question: its title
    states no single delta, so it is unclassifiable one step earlier. A parseable docker tag is
    an ordinary shape -- `postgres:16.2` to `postgres:17.0` -- and the day one is opened against
    a repository with a standing package, this is the answer it must get.
    """
    pending = _pending(
        "AlobarQuest/infraops-mcp-server",
        90,
        "chore(deps): bump postgres from 16.2 to 17.0",
        dependency="postgres",
        ecosystem="docker",
        update_type=MAJOR,
        checks=PASSED,
        concluded_at=SETTLED,
    )
    packages = dict(PACKAGES)
    packages[("alobarquest/infraops-mcp-server", "postgres")] = _package(
        "postgres", from_version="16.2", to_version="17.0"
    )

    route = _consider(pending, RULE, packages, NOW, holds=())

    assert route.action == "alert", route.detail


def test_the_exclusion_is_what_decides_that_and_not_the_ecosystems_name() -> None:
    """THE CONTROL for the case above, and it is the same bump one field apart.

    Without it the assertion passes for any predicate that takes every docker bump, or every
    major, or everything at all. Move the ecosystem to one the declaration does not exclude and
    the identical green pull request is left to the lane.
    """
    pending = _pending(
        "AlobarQuest/infraops-mcp-server",
        90,
        "chore(deps): bump postgres from 16.2 to 17.0",
        dependency="postgres",
        ecosystem="npm_and_yarn",
        update_type=MAJOR,
        checks=PASSED,
        concluded_at=SETTLED,
    )
    packages = dict(PACKAGES)
    packages[("alobarquest/infraops-mcp-server", "postgres")] = _package(
        "postgres", from_version="16.2", to_version="17.0"
    )

    route = _consider(pending, RULE, packages, NOW, holds=())

    assert route.action == "skip"
    assert "permits" in route.detail and "have passed" in route.detail, route.detail


def _range(ecosystem: str, checks: tuple[Check, ...]) -> PendingUpdate:
    return _pending(
        "AlobarQuest/infraops-mcp-server",
        69,
        "chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
        dependency="setuptools",
        ecosystem=ecosystem,
        update_type=None,
        checks=checks,
        concluded_at=SETTLED,
    )


def test_a_passing_requirement_range_is_left_to_the_cascade() -> None:
    """CONTROL 2, the producer's half. The cascade PERMITS a range (ADR-0034), so a green one is
    the cascade's, exactly as a green plain bump is."""
    route = _consider(_range("uv", PASSED), RULE, PACKAGES, NOW, holds=())

    assert route.action == "skip" and "have passed" in route.detail, route.detail


def test_a_failed_requirement_range_in_uv_carries_its_bare_versions() -> None:
    """Ruling 7: a single-clause range names two versions, and those are what a revision
    carries."""
    route = _consider(_range("uv", FAILED), RULE, PACKAGES, NOW, holds=())

    assert route.action == "act" and route.tooling == "uv", route.detail
    assert route.bump is not None
    assert (route.bump.from_version, route.bump.to_version) == ("83.0.0", "84.0.0")


def test_a_failed_requirement_range_in_pip_alerts() -> None:
    """Review focus 5: pip's mutation rewrites `==` lines only."""
    route = _consider(_range("pip", FAILED), RULE, PACKAGES, NOW, holds=())

    assert route.action == "alert", route.detail
    assert route.key == "dependency-bump:alobarquest/infraops-mcp-server:pip:setuptools"


def test_a_multi_clause_range_stays_unclassifiable() -> None:
    pending = _pending(
        "AlobarQuest/infraops-mcp-server",
        70,
        "chore(deps): update httpx requirement from <0.30,>=0.29 to >=0.29,<0.31",
        dependency="httpx",
        ecosystem="uv",
        update_type=None,
        checks=FAILED,
        concluded_at=SETTLED,
    )

    route = _consider(pending, RULE, PACKAGES, NOW, holds=())

    assert route.action == "skip" and route.detail.startswith("unclassifiable"), route.detail


CANCELLED = (Check(name="Quality", conclusion="cancelled", run=1),)
STALE = (Check(name="Quality", conclusion="stale", run=1),)


@pytest.mark.parametrize("checks", [CANCELLED, STALE], ids=["cancelled", "stale"])
def test_a_conclusion_that_is_no_verdict_does_not_hand_the_bump_to_the_factory(checks) -> None:
    """A cancelled run was STOPPED and a stale one SUPERSEDED. Neither says the change is bad.

    The estate has paid for reading them as failures once already -- the landing lane held three
    clean bumps for four days on runs GitHub cancelled when the Actions quota ran out -- and here
    the cost is worse than a delay: the gate's arming stays live, so treating a cancelled job as
    the cascade's answer mints a package revision that cannot be unminted, and the bump lands by
    itself the moment somebody re-runs the job green.
    """
    route = _consider(_zod(checks=checks, concluded_at=SETTLED), RULE, PACKAGES, NOW, holds=())

    assert route.action == "skip"
    assert "have not concluded against it" in route.detail, route.detail


# ---------------------------------------------------------------------------------------------
# Bridge Part 4. A failed bump past a hold in its repository is skipped `ceiling-exceeded`, with
# no alert key, so a `dependency-bump:` alert this lane raised for it before the hold resolves.
# ---------------------------------------------------------------------------------------------


def test_a_failed_bump_past_a_hold_is_skipped_with_no_alert_key() -> None:
    held = (Hold("npm_and_yarn", "/", "zod", "3.99"),)

    route = _consider(_zod(checks=FAILED, concluded_at=SETTLED), RULE, PACKAGES, NOW, holds=held)

    assert route.action == "skip", route.detail
    assert route.detail.startswith("ceiling-exceeded"), route.detail
    assert "zod" in route.detail and "3.99" in route.detail and "4.4.3" in route.detail
    assert route.key == ""


def test_a_failed_bump_at_or_below_its_ceiling_is_still_acted_on() -> None:
    """The control: a hold on the dependency is not by itself a skip for npm."""
    held = (Hold("npm_and_yarn", "/", "zod", "4.4.3"),)

    route = _consider(_zod(checks=FAILED, concluded_at=SETTLED), RULE, PACKAGES, NOW, holds=held)

    assert route.action == "act", route.detail


def test_a_hold_on_another_dependency_or_ecosystem_changes_nothing() -> None:
    held = (Hold("npm_and_yarn", "/", "typescript", "1"), Hold("pip", "/", "zod", "1"))

    route = _consider(_zod(checks=FAILED, concluded_at=SETTLED), RULE, PACKAGES, NOW, holds=held)

    assert route.action == "act", route.detail


@pytest.mark.parametrize("ceiling", ["84.0.0", "999"])
def test_a_uv_bump_of_a_held_dependency_is_skipped_whatever_its_version(ceiling) -> None:
    """The factory's uv mutator asks for `>=<to_version>`, so what it lands can pass any ceiling;
    a package proposed for it would only make a record the carrier refuses."""
    held = (Hold("uv", "/", "SetupTools", ceiling),)

    route = _consider(_range("uv", FAILED), RULE, PACKAGES, NOW, holds=held)

    assert route.action == "skip" and route.detail.startswith("ceiling-exceeded"), route.detail
    assert route.key == ""


def _checkout(checks: tuple[Check, ...]) -> PendingUpdate:
    return _pending(
        "AlobarQuest/factory-runner",
        28,
        "chore(actions): bump actions/checkout from 4 to 7",
        dependency="actions/checkout",
        ecosystem="github_actions",
        update_type=MAJOR,
        checks=checks,
        concluded_at=SETTLED,
    )


@pytest.mark.parametrize("ceiling", ["4", "7"])
def test_a_held_bump_with_no_tooling_is_skipped_rather_than_alerted(ceiling) -> None:
    """Kills: the no-tooling alert asked before the hold, raising an alert key a person already
    answered. No version is read on that branch, so a hold at or past the bump skips it too."""
    held = (Hold("github_actions", "/", "Actions/Checkout", ceiling),)

    route = _consider(_checkout(FAILED), RULE, PACKAGES, NOW, holds=held)

    assert route.action == "skip", route.detail
    assert route.detail.startswith("ceiling-exceeded"), route.detail
    assert "no tooling" in route.detail and ceiling in route.detail
    assert route.key == ""


def test_a_hold_on_another_dependency_leaves_the_no_tooling_alert() -> None:
    """The control: only a hold on this dependency answers the alert."""
    held = (
        Hold("github_actions", "/", "astral-sh/setup-uv", "4"),
        Hold("npm_and_yarn", "/", "actions/checkout", "4"),
    )

    route = _consider(_checkout(FAILED), RULE, PACKAGES, NOW, holds=held)

    assert route.action == "alert", route.detail


def test_a_held_pip_range_is_skipped_rather_than_alerted() -> None:
    """A held dependency's alert would ask a person a question the hold already answered."""
    held = (Hold("pip", "/", "setuptools", "83"),)

    route = _consider(_range("pip", FAILED), RULE, PACKAGES, NOW, holds=held)

    assert route.action == "skip" and route.detail.startswith("ceiling-exceeded"), route.detail
    assert route.key == ""
