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

from revision_watcher.estate import UnreadablePlatform, UnreadableSubject, candidate_urls
from revision_watcher.github import GitHubReader, GitHubUnreadable, branch_tip, relation
from revision_watcher.subjects import SUBJECTS, Subject, revision_of

CURRENT = "current"
BEHIND = "behind"
DIVERGED = "diverged"
UNSTAMPED = "unstamped"
UNREADABLE = "unreadable"

FINDING_STATES = frozenset({BEHIND, DIVERGED})

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
    def unstamped(self) -> list[Reading]:
        return [reading for reading in self.readings if reading.state == UNSTAMPED]


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


def read_subject(subject: Subject, *, applications, reader: GitHubReader) -> Reading:
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
    try:
        state = classify(subject, served=served, expected=expected, reader=reader)
    except GitHubUnreadable as error:
        return Reading(subject, UNREADABLE, served=served, expected=expected, detail=str(error))

    # A FACT-DERIVED CLOCK, never the wall. The orchestrator hashes the whole stored command,
    # `observed_at` included, so a wall clock gives unchanged reality a new fact hash every pass --
    # which at a stable reference is `observation_conflict` from the second pass onward, forever.
    # The served commit's date is a function of the facts and moves only when production moves; a
    # subject that names no commit falls back to the branch tip's date, which is equally in the
    # facts and moves only when the branch does.
    anchor = served if state != UNSTAMPED and served else expected
    try:
        observed_at = reader.committed_at(subject.repository, anchor)
    except GitHubUnreadable as error:
        return Reading(subject, UNREADABLE, served=served, expected=expected, detail=str(error))
    return Reading(subject, state, served=served, expected=expected, observed_at=observed_at)


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


def sweep(*, applications, reader: GitHubReader, platform=None) -> Pass:
    result = Pass(
        readings=[read_subject(s, applications=applications, reader=reader) for s in SUBJECTS]
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
            f"{mark} {reading.subject.name:<16} {reading.state:<10} "
            f"serving {served} of {expected}{detail}"
        )
    for name in result.undeclared:
        lines.append(f"!! {name:<16} undeclared serves a revision and is not a declared subject")
    if result.coverage_unmeasured:
        lines.append(f"[incomplete] coverage not measured: {result.coverage_unmeasured}")
    return lines
