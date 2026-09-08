"""What one pass concluded about each subject, and which of those answers is a finding.

FOUR STATES, and the split between them is the whole design:

  `current`    production serves the commit the branch names.
  `behind`     production serves an ancestor of it. A FINDING: something was landed and is not
               serving, which is the condition this lane exists to report.
  `diverged`   production serves a commit that is not on the branch at all. Also a finding, and a
               different one -- `behind` clears itself the next time a deploy succeeds, and this
               does not. Kept apart for the reason the estate keeps re-learning: a status that
               covers several causes is a summary, and a summary is not a diagnosis.
  `unstamped`  the application answers and names no commit. NOT a finding. Six of the eighteen
               applications running on 2026-09-08 are in this state and none of them ever claimed
               to be askable; calling each one a finding would make this lane permanently red
               about applications that opted out by never opting in. Reported in its own count,
               the way the landing ledger reports exceptions.

An application that could not be asked, or a branch GitHub would not name, is `unreadable` -- the
answer is MISSING rather than clean, and it drives the incomplete exit rather than the finding one.
That ordering is every sibling lane's: an incomplete pass cannot claim it found everything there
was to find.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from revision_watcher.estate import (
    LANDING_INERT,
    LANDING_UNKNOWN,
    UnreadablePlatform,
    UnreadableSubject,
    candidate_urls,
)
from revision_watcher.github import GitHubReader, GitHubUnreadable, branch_tip, relation
from revision_watcher.subjects import SUBJECTS, Subject, revision_of

CURRENT = "current"
BEHIND = "behind"
AWAITING_DEPLOY = "awaiting_deploy"
DIVERGED = "diverged"
UNSTAMPED = "unstamped"
UNREADABLE = "unreadable"

FINDING_STATES = frozenset({BEHIND, DIVERGED})

# HOW LONG A SEPARATE-TRACK APPLICATION MAY SIT BEHIND BEFORE IT IS A FINDING.
#
# For a repository where merging IS deploying, a gap means something went wrong and is reported at
# once. For one where merge and deploy are separate tracks, a gap is the QUEUE: every merge creates
# one by design, and reporting it immediately makes the control red for doing exactly what it is
# supposed to do. That is not hypothetical -- this lane's first live finding was the orchestrator
# going `behind` because a LAUNCHER-ONLY commit merged, correct by the measurement and noise by
# intent.
#
# A queue that never drains is still a defect, so the tolerance is bounded rather than absent. 48
# hours is chosen against the one separate-track application there is: its deploy is performed by
# hand, so the failure mode is somebody forgetting, and two days is a forgetting window that does
# not fire on ordinary same-day work. It is the number to change if that rhythm changes; nothing
# else here depends on its value.
#
# MEASURED FROM THE BRANCH TIP'S COMMITTER DATE -- how long ago the thing production is missing
# landed -- rather than from anything about the pass. A gap is old because the change is old.
SEPARATE_TRACK_GRACE_SECONDS = 172_800

# GitHub's own vocabulary for `/compare`. `identical` cannot reach the classifier -- an equal sha
# is decided before anything is compared -- and is named so the mapping is total rather than
# relying on a fall-through nobody stated.
_FROM_RELATION = {
    "behind": BEHIND,
    "identical": CURRENT,
    "ahead": DIVERGED,
    "diverged": DIVERGED,
}


@dataclass(frozen=True)
class Reading:
    """One subject's answer. `served` and `expected` are the facts; `state` is the judgment."""

    subject: Subject
    state: str
    served: str | None = None
    expected: str | None = None
    detail: str = ""
    observed_at: str | None = None
    # What being behind MEANS for this subject, per the estate's own record. Carried on the
    # reading rather than looked up again at reporting time, so one pass cannot judge a subject
    # under one classification and describe it under another.
    landing: str = LANDING_UNKNOWN

    @property
    def is_finding(self) -> bool:
        return self.state in FINDING_STATES


@dataclass
class Pass:
    readings: list[Reading] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)
    coverage_unmeasured: str = ""

    @property
    def findings(self) -> list[Reading]:
        return [reading for reading in self.readings if reading.is_finding]

    @property
    def unreadable(self) -> list[Reading]:
        return [reading for reading in self.readings if reading.state == UNREADABLE]

    @property
    def awaiting(self) -> list[Reading]:
        return [reading for reading in self.readings if reading.state == AWAITING_DEPLOY]

    @property
    def unstamped(self) -> list[Reading]:
        return [reading for reading in self.readings if reading.state == UNSTAMPED]


def tolerated_gap(*, landing: str, expected_at: str | None, now: datetime, grace: int) -> bool:
    """Is this gap the expected queue rather than a defect?

    TRUE only for a separate-track repository whose gap is younger than the grace. Everything else
    is False, and each `False` is a different reason worth stating:

    - `redeploys`: merging IS deploying, so a gap means something went wrong. Never tolerated.
    - `unknown`: the estate has not said, or could not be asked. Read strictly -- reporting a gap
      that turns out to be an expected queue costs a look, and staying quiet about one that is a
      failed rollout costs what 2026-09-06 cost.
    - a gap with no readable date: nothing establishes it is young, so it is not tolerated.
    """
    if landing != LANDING_INERT or expected_at is None:
        return False
    try:
        landed = datetime.fromisoformat(expected_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if landed.tzinfo is None:
        return False
    return (now - landed).total_seconds() < grace


def classify(
    subject: Subject,
    *,
    served: str | None,
    expected: str,
    reader: GitHubReader,
) -> str:
    """The judgment, given what production serves and what the branch names.

    An equal sha short-circuits: comparing a commit with itself is a request that can fail, and a
    subject that IS current must not be reported unreadable because GitHub declined to say so.
    """
    if served is None:
        return UNSTAMPED
    if served == expected:
        return CURRENT
    status = relation(reader, subject.repository, expected, served)
    if status is None:
        # GitHub will not compare them, which for a well-formed sha means it is not in the
        # repository. That is not `behind` and saying so would understate it.
        return DIVERGED
    return _FROM_RELATION.get(status, DIVERGED)


def read_subject(
    subject: Subject,
    *,
    applications,
    reader: GitHubReader,
    landings=None,
    now: datetime | None = None,
    grace: int = SEPARATE_TRACK_GRACE_SECONDS,
) -> Reading:
    """One subject, end to end.

    Never raises: an unreadable subject costs itself and nothing else.
    """
    try:
        expected = branch_tip(reader, subject.repository, subject.branch)
    except GitHubUnreadable as error:
        return Reading(subject, UNREADABLE, detail=f"branch: {error}")
    try:
        body = applications.health(subject.health_url)
    except UnreadableSubject as error:
        return Reading(subject, UNREADABLE, expected=expected, detail=f"health: {error}")

    served = revision_of(body)
    landing = LANDING_UNKNOWN if landings is None else landings.landing(subject.repository)
    try:
        state = classify(subject, served=served, expected=expected, reader=reader)
    except GitHubUnreadable as error:
        return Reading(
            subject,
            UNREADABLE,
            served=served,
            expected=expected,
            detail=str(error),
            landing=landing,
        )

    # A FACT-DERIVED CLOCK, never the wall. The orchestrator hashes the whole stored command,
    # `observed_at` included, so a wall clock gives unchanged reality a new fact hash every pass --
    # which at a stable reference is `observation_conflict` from the second pass onward, forever.
    # The served commit's date is a function of the facts and moves only when production moves; a
    # subject that names no commit falls back to the branch tip's date, which is equally in the
    # facts and moves only when the branch does.
    anchor = served if state != UNSTAMPED and served else expected
    try:
        observed_at = reader.committed_at(subject.repository, anchor)
        expected_at = (
            observed_at if anchor == expected else reader.committed_at(subject.repository, expected)
        )
    except GitHubUnreadable as error:
        return Reading(
            subject,
            UNREADABLE,
            served=served,
            expected=expected,
            detail=str(error),
            landing=landing,
        )
    # A GAP ON A SEPARATE-TRACK REPOSITORY IS THE QUEUE, NOT A DEFECT -- until it is old. Applied
    # to `behind` only: `diverged` means production is serving something that is not on the branch
    # at all, which no amount of waiting explains and no deploy was ever going to produce.
    if state == BEHIND and tolerated_gap(
        landing=landing,
        expected_at=expected_at,
        now=now or datetime.now(UTC),
        grace=grace,
    ):
        state = AWAITING_DEPLOY
    return Reading(
        subject,
        state,
        served=served,
        expected=expected,
        observed_at=observed_at,
        landing=landing,
    )


def undeclared_subjects(applications, platform) -> list[str]:
    """Applications the platform runs, absent from the table, that answer with a revision.

    This is what keeps the declared table from rotting silently, and it reports only the case that
    matters: an application with no revision has not opted in, so listing it would bury the one
    that has under a dozen that never will.
    """
    declared = {subject.health_url for subject in SUBJECTS}
    named = {subject.name for subject in SUBJECTS}
    found: list[str] = []
    for name, url in sorted(candidate_urls(platform.applications()).items()):
        if url in declared or name in named:
            continue
        try:
            body = applications.health(url)
        except UnreadableSubject:
            continue
        if revision_of(body) is not None:
            found.append(name)
    return found


def sweep(*, applications, reader: GitHubReader, platform=None, landings=None, now=None) -> Pass:
    result = Pass(
        readings=[
            read_subject(
                subject, applications=applications, reader=reader, landings=landings, now=now
            )
            for subject in SUBJECTS
        ]
    )
    if platform is None:
        result.coverage_unmeasured = "no platform credential was configured"
        return result
    try:
        result.undeclared = undeclared_subjects(applications, platform)
    except UnreadablePlatform as error:
        result.coverage_unmeasured = str(error)
    return result


def as_lines(result: Pass) -> list[str]:
    lines = []
    for reading in result.readings:
        served = (reading.served or "<none>")[:12]
        expected = (reading.expected or "<unknown>")[:12]
        mark = "!!" if reading.is_finding else "  "
        detail = f" — {reading.detail}" if reading.detail else ""
        lines.append(
            f"{mark} {reading.subject.name:<16} {reading.state:<15} "
            f"serving {served} of {expected} [{reading.landing}]{detail}"
        )
    for name in result.undeclared:
        lines.append(f"!! {name:<16} undeclared serves a revision and is not a declared subject")
    if result.coverage_unmeasured:
        lines.append(f"[incomplete] coverage not measured: {result.coverage_unmeasured}")
    return lines
