"""Which open pull requests this lane's subject is -- measured against the real population.

**THE CORPUS IS THE ESTATE, not an invention.** Every case below is an open Dependabot pull
request that existed on 2026-08-19, paired with the rule actually installed on its repository.
A classifier tested only on shapes somebody thought of passes while being wrong for every real
repository, which this estate has already paid for once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bump_proposer.cli import FAILURE_SETTLE_SECONDS, _consider
from bump_proposer.standing import StandingPackage
from landing_ledger.model import Check, PendingUpdate, UpdateMetadata
from landing_ledger.rules import REGISTRY

# The gate blob each repository carried, measured 2026-08-19 by reading the contents API.
ORCHESTRATOR_GATE = "72391c0f7343477193b5c896680a083500c45227"
FIVE_REPO_GATE = "e849b3a8411fabeff1dedd138e6e3e3a2f535319"
NEWER_METADATA_GATE = "a4a4b8da035292fe434badd007607d8a69bc54e2"
# The one revision all six carry from 2026-08-28 (ADR-0034): it excludes `docker` and asks
# nothing else, so what the checks concluded is the only thing left separating the two lanes.
OUTCOME_GATE = "311aaa1dc0fb50bd9cb2350fe2d358e8ff973ccd"

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
SETTLED = NOW - timedelta(seconds=FAILURE_SETTLE_SECONDS + 1)
JUST_CONCLUDED = NOW - timedelta(seconds=FAILURE_SETTLE_SECONDS - 1)
FAILED = (Check(name="Quality", conclusion="failure", run=1),)
PASSED = (Check(name="Quality", conclusion="success", run=1),)

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
    threw away the only value revision 311aaa1d reads. An earlier version of this helper passed
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


# (label, repository, number, title, dependency, ecosystem, update_type, gate, expected)
LIVE = [
    # The lane's subject: three npm majors the cascade refuses, all with a standing package.
    (
        "zod",
        "AlobarQuest/infraops-mcp-server",
        71,
        "build(deps): bump zod from 3.25.76 to 4.4.3",
        "zod",
        "npm_and_yarn",
        MAJOR,
        NEWER_METADATA_GATE,
        "candidate",
    ),
    (
        "typescript",
        "AlobarQuest/infraops-mcp-server",
        73,
        "build(deps-dev): bump typescript from 5.9.3 to 7.0.2",
        "typescript",
        "npm_and_yarn",
        MAJOR,
        NEWER_METADATA_GATE,
        "candidate",
    ),
    (
        "eslint",
        "AlobarQuest/infraops-mcp-server",
        78,
        "build(deps-dev): bump eslint from 9.39.4 to 10.8.1",
        "eslint",
        "npm_and_yarn",
        MAJOR,
        NEWER_METADATA_GATE,
        "candidate",
    ),
    # Majors the cascade TAKES, because the required check that gates them is the thing being
    # bumped. Proposing these would put the factory to work on something GitHub is about to
    # merge.
    (
        "fetch-metadata",
        "AlobarQuest/orchestrator",
        173,
        "chore(deps): bump dependabot/fetch-metadata from 2.5.0 to 3.1.0",
        "dependabot/fetch-metadata",
        "github_actions",
        MAJOR,
        ORCHESTRATOR_GATE,
        "permitted",
    ),
    (
        "setup-uv",
        "AlobarQuest/factory-runner",
        31,
        "chore(actions): bump astral-sh/setup-uv from 5 to 7",
        "astral-sh/setup-uv",
        "github_actions",
        MAJOR,
        NEWER_METADATA_GATE,
        "permitted",
    ),
    (
        "checkout",
        "AlobarQuest/factory-runner",
        28,
        "chore(actions): bump actions/checkout from 4 to 7",
        "actions/checkout",
        "github_actions",
        MAJOR,
        NEWER_METADATA_GATE,
        "permitted",
    ),
    # Requirement ranges: no single delta, on four repositories.
    (
        "setuptools",
        "AlobarQuest/orchestrator",
        174,
        "chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
        "setuptools",
        "uv",
        None,
        ORCHESTRATOR_GATE,
        "unclassifiable",
    ),
    (
        "uvicorn",
        "AlobarQuest/orchestrator",
        151,
        "chore(deps): update uvicorn[standard] requirement from >=0.52.0 to >=0.52.1",
        "uvicorn[standard]",
        "uv",
        None,
        ORCHESTRATOR_GATE,
        "unclassifiable",
    ),
    (
        "pyjwt",
        "AlobarQuest/orchestrator",
        126,
        "chore(deps): update pyjwt[crypto] requirement from >=2.8 to >=2.13.0",
        "pyjwt[crypto]",
        "uv",
        None,
        ORCHESTRATOR_GATE,
        "unclassifiable",
    ),
    (
        "setuptools-ip",
        "AlobarQuest/intent-packages",
        69,
        "chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
        "setuptools",
        "uv",
        None,
        FIVE_REPO_GATE,
        "unclassifiable",
    ),
    # A docker tag that does not parse as semver, so the bot declares no update type either.
    (
        "python",
        "AlobarQuest/orchestrator",
        3,
        "chore(deps): bump python from 3.12-slim to 3.14-slim",
        "python",
        "docker",
        None,
        ORCHESTRATOR_GATE,
        "unclassifiable",
    ),
]


@pytest.mark.parametrize("case", LIVE, ids=[c[0] for c in LIVE])
def test_the_live_population_is_classified_as_measured(case) -> None:
    label, repository, number, title, dependency, ecosystem, update_type, gate, expected = case
    pending = _pending(
        repository,
        number,
        title,
        dependency=dependency,
        ecosystem=ecosystem,
        update_type=update_type,
    )
    package, bump, detail = _consider(pending, REGISTRY[gate], PACKAGES, NOW)
    if expected == "candidate":
        assert package is not None and bump is not None, detail
    elif expected == "permitted":
        assert package is None and "permits" in detail, detail
    else:
        assert package is None and detail.startswith("unclassifiable"), detail


def test_a_bump_with_no_standing_package_is_unlaned() -> None:
    """Not a finding. Scope is the authored set, and authoring one is what changes it."""
    pending = _pending(
        "AlobarQuest/infraops-mcp-server",
        99,
        "build(deps): bump left-pad from 1.0.0 to 2.0.0",
        dependency="left-pad",
        ecosystem="npm_and_yarn",
        update_type=MAJOR,
    )
    package, bump, detail = _consider(pending, REGISTRY[NEWER_METADATA_GATE], PACKAGES, NOW)
    assert package is None and detail.startswith("unlaned")


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
    package, bump, detail = _consider(pending, REGISTRY[NEWER_METADATA_GATE], PACKAGES, NOW)
    assert package is None and detail.startswith("ambiguous"), detail


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
    package, bump, detail = _consider(pending, REGISTRY[NEWER_METADATA_GATE], PACKAGES, NOW)
    assert package is None and detail.startswith("ambiguous"), detail


# ---------------------------------------------------------------------------------------------
# ADR-0034. Under revision 311aaa1d the cascade permits anything it does not exclude, so
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
    under zod 4, so the bump needs a code change before its diff is correct. Revision 311aaa1d
    PERMITS it -- there is no update-type arm left to refuse it with -- and GitHub will never
    land it, because two required checks concluded failure on 2026-08-23. A producer reading
    `permits` alone would report it as the cascade's business and propose nothing, leaving the
    one bump the factory exists for in neither lane.
    """
    pending = _zod(checks=FAILED, concluded_at=SETTLED)

    package, bump, detail = _consider(pending, REGISTRY[OUTCOME_GATE], PACKAGES, NOW)

    assert package is not None and bump is not None, detail
    assert bump.kind == "semver-major"


def test_the_same_bump_passing_its_checks_is_left_to_the_cascade() -> None:
    """The other half of control 1, and the reason it is not simply "always take a major".

    Nothing about the bump changes here except what CI concluded, which is precisely the axis
    ADR-0034 moved the split onto. Sending the factory at a pull request GitHub is about to land
    would mint a package revision for a diff that was already correct.
    """
    pending = _zod(checks=PASSED, concluded_at=SETTLED)

    package, _, detail = _consider(pending, REGISTRY[OUTCOME_GATE], PACKAGES, NOW)

    assert package is None
    assert "permits" in detail and "have passed" in detail, detail


def test_a_failure_that_has_not_settled_belongs_to_neither_lane_yet() -> None:
    """`PendingUpdate.checks` holds only jobs that have CONCLUDED, so one failure among several
    still-running jobs is indistinguishable from a final verdict except by how recently the last
    conclusion arrived. Taking it early mints a package revision that cannot be unminted, and a
    re-run that then goes green strands the record as superseded with a human approval spent."""
    pending = _zod(checks=FAILED, concluded_at=JUST_CONCLUDED)

    package, _, detail = _consider(pending, REGISTRY[OUTCOME_GATE], PACKAGES, NOW)

    assert package is None
    assert "have not concluded against it" in detail, detail


def test_a_pull_request_whose_checks_have_not_concluded_at_all_is_not_taken() -> None:
    """The state every Dependabot pull request is in for its first minutes. Fail-closed here
    means waiting: a pass that skips it costs a pass, and the next one picks it up."""
    pending = _zod()

    package, _, detail = _consider(pending, REGISTRY[OUTCOME_GATE], PACKAGES, NOW)

    assert package is None
    assert "have not concluded against it" in detail, detail


def test_the_old_and_new_gates_take_zod_for_DIFFERENT_reasons() -> None:
    """The differential that shows the change is real rather than coincidental.

    Under the cascade the bump was this lane's subject because a package major was refused on
    its DECLARATION, whatever CI said -- so a green zod was a candidate too. Under 311aaa1d only
    the failure keeps it here. Asserting the candidate case alone would pass under both and
    prove nothing about which question is being asked.
    """
    green = _zod(checks=PASSED, concluded_at=SETTLED)

    cascade_package, _, _ = _consider(green, REGISTRY[NEWER_METADATA_GATE], PACKAGES, NOW)
    outcome_package, _, _ = _consider(green, REGISTRY[OUTCOME_GATE], PACKAGES, NOW)

    assert cascade_package is not None
    assert outcome_package is None


def test_a_requirement_range_stays_unclassifiable_under_the_outcome_rule() -> None:
    """CONTROL 2, the producer's half. The cascade now PERMITS these -- that is the whole point
    of ADR-0034 -- but this lane still cannot describe one: a range states no single delta, so
    there are no two versions for a package revision to carry. Unchanged, and not a finding."""
    pending = _pending(
        "AlobarQuest/infraops-mcp-server",
        69,
        "chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
        dependency="setuptools",
        ecosystem="uv",
        update_type=None,
        checks=PASSED,
        concluded_at=SETTLED,
    )

    package, _, detail = _consider(pending, REGISTRY[OUTCOME_GATE], PACKAGES, NOW)

    assert package is None and detail.startswith("unclassifiable"), detail
