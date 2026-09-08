"""What being behind MEANS depends on how landing behaves, and the estate already records which.

  `redeploys`  merging IS deploying, so a gap means something went wrong -- a rollout that failed,
               a webhook that did not fire, an image that never built. Reported at once.
  `inert`      merge and deploy are separate tracks, so a gap is the QUEUE. Every merge creates
               one by design, and reporting it immediately makes the control red for the system
               working exactly as intended.

That is not hypothetical. This lane's first live finding was the orchestrator going `behind`
because a LAUNCHER-ONLY commit merged: correct by the measurement, noise by intent, and noise
precisely because the orchestrator is the one `inert` subject.

A queue that never drains is still a defect, so the tolerance is BOUNDED. Every case below pins one
edge of that, and the strict readings are pinned hardest -- an over-general tolerance is how this
lane would go quiet about the failure it was built for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from revision_watcher.census import (
    AWAITING_DEPLOY,
    BEHIND,
    CURRENT,
    DIVERGED,
    SEPARATE_TRACK_GRACE_SECONDS,
    Reading,
    read_subject,
    tolerated_gap,
)
from revision_watcher.estate import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    LANDING_UNKNOWN,
    ApplicationReader,
)
from revision_watcher.github import GitHubReader
from revision_watcher.record import revision_facts, revision_observation
from revision_watcher.subjects import Subject
from tests.revision_watcher.conftest import (
    ELSEWHERE,
    OLD,
    TIP,
    WHEN_TIP,
    application_transport,
    github_transport,
)

SUBJECT = Subject("app-brain", "https://app-brain.example/api/health", "AlobarQuest/brain")
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
FRESH = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
STALE = (NOW - timedelta(days=5)).isoformat().replace("+00:00", "Z")


class _Landings:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def landing(self, repository: str) -> str:
        self.asked.append(repository)
        return self.answer


def _read(landing: str, *, tip_date: str, served: str = OLD, relation: str = "behind") -> Reading:
    with (
        GitHubReader(
            token="fixture",
            transport=github_transport(
                relations={served: relation}, dates={TIP: tip_date, served: tip_date}
            ),
        ) as reader,
        ApplicationReader(
            transport=application_transport({"app-brain.example": {"revision": served}})
        ) as applications,
    ):
        return read_subject(
            SUBJECT,
            applications=applications,
            reader=reader,
            landings=_Landings(landing),
            now=NOW,
        )


def test_a_gap_where_merging_IS_deploying_is_a_finding_however_fresh() -> None:
    """Nothing was supposed to be queued: the merge should have deployed it. Age is irrelevant."""
    reading = _read(LANDING_REDEPLOYS, tip_date=FRESH)

    assert reading.state == BEHIND
    assert reading.is_finding


def test_a_FRESH_gap_on_a_separate_track_is_the_queue_rather_than_a_defect() -> None:
    reading = _read(LANDING_INERT, tip_date=FRESH)

    assert reading.state == AWAITING_DEPLOY
    assert not reading.is_finding


def test_a_STALE_gap_on_a_separate_track_is_still_a_finding() -> None:
    """A queue that never drains is a defect. The tolerance is bounded, not absent -- without this
    the one application whose deploy is performed by hand could sit behind forever, quietly."""
    reading = _read(LANDING_INERT, tip_date=STALE)

    assert reading.state == BEHIND
    assert reading.is_finding


def test_an_UNCLASSIFIED_repository_is_read_strictly() -> None:
    """Reporting a gap that turns out to be an expected queue costs a look; staying quiet about one
    that is a failed rollout costs what 2026-09-06 cost. So `unknown` is judged as though merging
    deploys, and a reader can see which it was because the classification is in the report."""
    reading = _read(LANDING_UNKNOWN, tip_date=FRESH)

    assert reading.state == BEHIND
    assert reading.is_finding


def test_DIVERGED_is_never_tolerated_however_the_repository_lands() -> None:
    """Production serving a commit that is not on the branch at all is not explained by waiting,
    and no deploy was ever going to produce it."""
    reading = _read(LANDING_INERT, tip_date=FRESH, served=ELSEWHERE, relation="diverged")

    assert reading.state == DIVERGED
    assert reading.is_finding


def test_a_gap_whose_age_cannot_be_read_is_not_tolerated() -> None:
    """Nothing establishes it is young, so nothing may treat it as young."""
    assert not tolerated_gap(
        landing=LANDING_INERT, expected_at=None, now=NOW, grace=SEPARATE_TRACK_GRACE_SECONDS
    )
    assert not tolerated_gap(
        landing=LANDING_INERT,
        expected_at="not a date",
        now=NOW,
        grace=SEPARATE_TRACK_GRACE_SECONDS,
    )


def test_a_NAIVE_timestamp_is_not_tolerated_rather_than_compared() -> None:
    """Comparing a naive datetime with an aware one raises `TypeError` deep in the caller, which
    this repository already records as a route to a bare 500 one layer out."""
    assert not tolerated_gap(
        landing=LANDING_INERT,
        expected_at="2026-09-08T11:00:00",
        now=NOW,
        grace=SEPARATE_TRACK_GRACE_SECONDS,
    )


@pytest.mark.parametrize("landing", [LANDING_REDEPLOYS, LANDING_UNKNOWN])
def test_only_a_separate_track_repository_is_ever_tolerated(landing: str) -> None:
    assert not tolerated_gap(
        landing=landing, expected_at=FRESH, now=NOW, grace=SEPARATE_TRACK_GRACE_SECONDS
    )


def test_the_boundary_is_the_grace_itself() -> None:
    """Pinned as a PAIR either side of the threshold, so a predicate that ignores the clock
    entirely reddens rather than passing on whichever side the real time happens to fall."""
    just_inside = (NOW - timedelta(seconds=SEPARATE_TRACK_GRACE_SECONDS - 60)).isoformat()
    just_outside = (NOW - timedelta(seconds=SEPARATE_TRACK_GRACE_SECONDS + 60)).isoformat()

    assert tolerated_gap(
        landing=LANDING_INERT,
        expected_at=just_inside,
        now=NOW,
        grace=SEPARATE_TRACK_GRACE_SECONDS,
    )
    assert not tolerated_gap(
        landing=LANDING_INERT,
        expected_at=just_outside,
        now=NOW,
        grace=SEPARATE_TRACK_GRACE_SECONDS,
    )


def test_the_classification_is_asked_once_per_REPOSITORY() -> None:
    """Four of the six subjects share one repository and one answer that cannot differ within a
    pass, so an uncached reader would ask the estate the same question four times an hour."""
    landings = _Landings(LANDING_REDEPLOYS)
    with (
        GitHubReader(token="fixture", transport=github_transport()) as reader,
        ApplicationReader(
            transport=application_transport({"app-brain.example": {"revision": TIP}})
        ) as applications,
    ):
        for _ in range(3):
            read_subject(
                SUBJECT, applications=applications, reader=reader, landings=landings, now=NOW
            )

    assert landings.asked == ["AlobarQuest/brain"] * 3  # the READER caches; this is the call count


# ---------------------------------------------------------------------------------------------
# The record.
# ---------------------------------------------------------------------------------------------


def test_the_row_carries_the_classification_so_the_state_reads_a_month_later() -> None:
    """`awaiting_deploy` is only a sane answer for a repository where merging does not deploy, and
    a reader must not have to go and ask which it was."""
    row = revision_observation(
        Reading(
            SUBJECT,
            AWAITING_DEPLOY,
            served=OLD,
            expected=TIP,
            observed_at=WHEN_TIP,
            landing=LANDING_INERT,
        )
    )

    assert row["facts"]["landing"] == LANDING_INERT


def test_awaiting_a_deploy_is_recorded_as_PASSED_rather_than_degraded() -> None:
    row = revision_observation(
        Reading(
            SUBJECT,
            AWAITING_DEPLOY,
            served=OLD,
            expected=TIP,
            observed_at=WHEN_TIP,
            landing=LANDING_INERT,
        )
    )

    assert (row["status"], row["severity"]) == ("passed", "info")


def test_NOTHING_TIME_DEPENDENT_reaches_the_facts() -> None:
    """THE PROPERTY THAT KEEPS THIS PRODUCER RUNNING. The facts are content-addressed into the
    row's reference, so a fact carrying the AGE of a gap would compose a new row every hour and
    never replay -- and the state now depends on a clock, which is exactly the shape that invites
    one. Two readings of the same reality, judged an hour apart, must be byte-identical."""
    earlier = Reading(
        SUBJECT, CURRENT, served=TIP, expected=TIP, observed_at=WHEN_TIP, landing=LANDING_INERT
    )

    assert revision_facts(earlier) == revision_facts(earlier)
    assert revision_observation(earlier) == revision_observation(earlier)
