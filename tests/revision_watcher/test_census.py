"""The four states, and which of them is a finding.

The split is the whole design, so each state is pinned by a case that can only pass if the
classifier makes that distinction -- and `unstamped` NOT being a finding is pinned hardest,
because it is the one an over-general rule would collapse into `behind` and make this lane
permanently red about six applications that never claimed to be askable.
"""

from __future__ import annotations

import pytest

from revision_watcher.census import (
    BEHIND,
    CURRENT,
    DIVERGED,
    UNREADABLE,
    UNSTAMPED,
    Pass,
    Reading,
    as_lines,
    read_subject,
    sweep,
)
from revision_watcher.estate import ApplicationReader, PlatformReader
from revision_watcher.github import GitHubReader
from revision_watcher.subjects import SUBJECTS, Subject, revision_of
from tests.revision_watcher.conftest import (
    ELSEWHERE,
    OLD,
    TIP,
    WHEN_OLD,
    WHEN_TIP,
    application_transport,
    github_transport,
    platform_transport,
)

SUBJECT = Subject("app-brain", "https://app-brain.example/api/health", "AlobarQuest/brain")


def _read(body, *, relations=None, branch_status=200) -> Reading:
    with (
        GitHubReader(
            token="fixture",
            transport=github_transport(relations=relations, branch_status=branch_status),
        ) as reader,
        ApplicationReader(
            transport=application_transport({"app-brain.example": body})
        ) as applications,
    ):
        return read_subject(SUBJECT, applications=applications, reader=reader)


def test_an_application_serving_the_branch_tip_is_current() -> None:
    reading = _read({"status": "ok", "revision": TIP})

    assert reading.state == CURRENT
    assert not reading.is_finding


def test_an_application_serving_an_ancestor_is_BEHIND() -> None:
    """The condition this lane exists for: something was landed and is not serving."""
    reading = _read({"status": "ok", "revision": OLD}, relations={OLD: "behind"})

    assert reading.state == BEHIND
    assert reading.is_finding


def test_an_application_serving_a_commit_NOT_ON_THE_BRANCH_is_DIVERGED_rather_than_behind() -> None:
    """A different finding with a different remedy: `behind` clears itself the next time a deploy
    succeeds and this does not. Collapsing the two would make the report a summary rather than a
    diagnosis, which is the shape this estate keeps paying for."""
    reading = _read({"status": "ok", "revision": ELSEWHERE}, relations={ELSEWHERE: "diverged"})

    assert reading.state == DIVERGED
    assert reading.is_finding


def test_a_commit_github_will_not_compare_is_DIVERGED_rather_than_behind() -> None:
    """GitHub refuses to compare a commit that is not in the repository. Reading that as `behind`
    would understate it -- production is serving something the repository does not contain."""
    reading = _read({"status": "ok", "revision": ELSEWHERE}, relations={})

    assert reading.state == DIVERGED


def test_an_application_that_names_no_commit_is_UNSTAMPED_and_is_NOT_A_FINDING() -> None:
    """Six of the eighteen applications running on 2026-09-08 answer health with no revision. They
    never claimed to be askable, and a lane that called each of them a finding would be red
    forever about applications that opted out by never opting in."""
    reading = _read({"status": "ok"})

    assert reading.state == UNSTAMPED
    assert not reading.is_finding


def test_an_application_that_cannot_be_ASKED_is_unreadable_rather_than_current() -> None:
    reading = _read(503)

    assert reading.state == UNREADABLE
    assert not reading.is_finding


def test_a_branch_github_will_not_name_is_unreadable_rather_than_a_finding() -> None:
    """Nothing was measured, so nothing may be asserted -- the answer is missing, not clean, and
    not a finding about the application either."""
    reading = _read({"status": "ok", "revision": TIP}, branch_status=500)

    assert reading.state == UNREADABLE


def test_a_NON_2XX_body_that_still_names_a_revision_is_read() -> None:
    """A stamped application whose database is unreachable answers 503 and its body still names
    the commit it is serving. That application is current and unwell, and those are different
    findings owned by different lanes -- discarding the body would report the wrong one."""
    with (
        GitHubReader(token="fixture", transport=github_transport()) as reader,
        ApplicationReader(transport=httpx_503_with_revision()) as applications,
    ):
        reading = read_subject(SUBJECT, applications=applications, reader=reader)

    assert reading.state == CURRENT


def httpx_503_with_revision():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "unavailable", "revision": TIP})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------------------------
# The clock. A wall clock here wedges the producer permanently, so it is pinned by value.
# ---------------------------------------------------------------------------------------------


def test_the_clock_is_the_SERVED_commits_date_rather_than_the_wall() -> None:
    """The orchestrator hashes the whole stored command, `observed_at` included, so a wall clock
    gives unchanged reality a new fact hash every pass -- which at a stable reference is
    `observation_conflict` from the second pass onward, forever."""
    reading = _read({"status": "ok", "revision": OLD}, relations={OLD: "behind"})

    assert reading.observed_at == WHEN_OLD


def test_an_unstamped_subject_falls_back_to_the_BRANCHS_date() -> None:
    """It names no commit, so it has no date of its own. The branch tip's is equally a function of
    the facts -- the tip is in them -- and moves only when the branch moves, which is the property
    the rule actually requires."""
    reading = _read({"status": "ok"})

    assert reading.observed_at == WHEN_TIP


# ---------------------------------------------------------------------------------------------
# Coverage: the declared table must report its own gaps, or it is the list this lane exists to
# refuse. Proven live on 2026-09-08 by removing change-manager from the table and watching the
# pass name it; these are the same differential in-process.
# ---------------------------------------------------------------------------------------------


def _sweep_with_platform(applications_running, bodies):
    with (
        GitHubReader(token="fixture", transport=github_transport()) as reader,
        ApplicationReader(transport=application_transport(bodies)) as applications,
        PlatformReader(
            base_url="https://platform.example",
            token="fixture",
            transport=platform_transport(applications_running),
        ) as platform,
    ):
        return sweep(applications=applications, reader=reader, platform=platform)


def test_an_undeclared_application_that_serves_a_revision_is_a_finding() -> None:
    result = _sweep_with_platform(
        [
            {
                "name": "newcomer",
                "fqdn": "https://newcomer.example",
                "status": "running:healthy",
                "health_check_path": "/api/health",
            }
        ],
        {
            **{s.health_url.split("/")[2]: {"revision": TIP} for s in SUBJECTS},
            "newcomer.example": {"status": "ok", "revision": TIP},
        },
    )

    assert result.undeclared == ["newcomer"]


def test_an_undeclared_application_with_NO_revision_is_not_reported() -> None:
    """Twelve of eighteen are in this state. Listing them would bury the one that matters."""
    result = _sweep_with_platform(
        [
            {
                "name": "quiet",
                "fqdn": "https://quiet.example",
                "status": "running:healthy",
                "health_check_path": "/api/health",
            }
        ],
        {
            **{s.health_url.split("/")[2]: {"revision": TIP} for s in SUBJECTS},
            "quiet.example": {"status": "ok"},
        },
    )

    assert result.undeclared == []


def test_a_STOPPED_application_is_not_a_coverage_gap() -> None:
    """It serves nothing, so it cannot be behind."""
    result = _sweep_with_platform(
        [
            {
                "name": "stopped",
                "fqdn": "https://stopped.example",
                "status": "exited:unhealthy",
                "health_check_path": "/api/health",
            }
        ],
        {
            **{s.health_url.split("/")[2]: {"revision": TIP} for s in SUBJECTS},
            "stopped.example": {"status": "ok", "revision": TIP},
        },
    )

    assert result.undeclared == []


def test_a_pass_with_no_platform_credential_says_coverage_went_unmeasured() -> None:
    """Deliberately not silent. The coverage sweep is what stops the table rotting, so a pass that
    could not run it has not answered the whole question."""
    with (
        GitHubReader(token="fixture", transport=github_transport()) as reader,
        ApplicationReader(
            transport=application_transport(
                {s.health_url.split("/")[2]: {"revision": TIP} for s in SUBJECTS}
            )
        ) as applications,
    ):
        result = sweep(applications=applications, reader=reader, platform=None)

    assert result.coverage_unmeasured


# ---------------------------------------------------------------------------------------------
# The report a launcher log carries.
# ---------------------------------------------------------------------------------------------


def test_a_finding_is_MARKED_in_the_report() -> None:
    result = Pass(readings=[Reading(SUBJECT, BEHIND, served=OLD, expected=TIP)])

    assert as_lines(result)[0].startswith("!!")


def test_a_current_application_is_not_marked() -> None:
    result = Pass(readings=[Reading(SUBJECT, CURRENT, served=TIP, expected=TIP)])

    assert not as_lines(result)[0].startswith("!!")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"revision": "abc"}, "abc"),
        ({"commit": "abc"}, "abc"),
        ({"git_sha": "abc"}, "abc"),
        ({"revision": ""}, None),
        ({"revision": "  "}, None),
        ({"status": "ok"}, None),
        ("not an object", None),
        (None, None),
    ],
)
def test_what_counts_as_an_application_naming_a_commit(body, expected) -> None:
    """An empty value is None: an application serving the key with no value knows no more about
    itself than one that omits it, and treating them differently would put a subject into the
    decidable set on the strength of a field that says nothing."""
    assert revision_of(body) == expected
