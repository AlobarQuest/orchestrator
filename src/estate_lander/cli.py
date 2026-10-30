"""Land the changes the estate has routed and approved. ADR-0019 increment 5b.

**This program composes nothing and decides nothing.** It reads which pull requests have a change
record, asks the orchestrator about each, prints the answer, and -- only with `--submit` -- asks
for the ones the orchestrator says are admissible. Every term lives inside the orchestrator, in
the transaction that records the act. That is what makes a scheduled caller acceptable here: the
unattended thing is a caller, not a judge.

WHY IT ENUMERATES FROM THE CHANGE RECORDS rather than from GitHub. The population this lane serves
is *changes somebody routed*, and the record is the only place that set exists. Reading GitHub
would produce the set of open pull requests, which is a different question and a larger one -- and
would put this program in the business of deciding which of them belongs here.

**EXPECT A PASS TO LAND NOTHING, and read the report rather than the count.** Every held pull
request names the condition it misses. A night on which four are held for want of a head current
with its base is the freshness condition working: those four squash into a tree no check has run,
and on these repositories that tree is what starts serving.

EXIT CODES: 0 clean, 1 tool failure, 2 unusable input, 3 findings. A HELD pull request is a
finding -- somebody has to decide whether to act on the condition it names -- while a landing and
a pull request the orchestrator has already acted on, are not. Nor are the two kinds of refusal
below, which the report still prints and which drive no exit code: a DELIBERATE refusal, which is
the system working and clears itself, and an EXCEPTION, which current policy can never clear and
which waits on a person. **And nor, CONDITIONALLY, is a refusal caused by the head being behind its
base when an exception sits beside it** -- there and only there, being behind is this program's own
deliberate declining rather than a condition; see `lander.core.FRESHNESS` for the condition and
`lander.core.freshness_derived` for which refusals are caused by the position. Every refusal is
printed either way, so the line always says what was missed.

**THERE ARE TWO ACTS, and the second one is new in ADR-0019 Increment 6.** After the landing pass,
the program asks the orchestrator to bring up to date any branch whose ONLY remaining obstacle is
that it is behind its base -- a condition this lane creates itself, because a landing moves the
base and stales every sibling in that repository. Which ones qualify is the orchestrator's answer,
composed from the same terms and composed again inside the transaction that acts; this program
relays it, exactly as it does for the landing. A record can therefore print two lines in one pass,
one per act considered, and the summary counts lines rather than records.

A third act replaces the landing for a pull request whose answer names
`landing_version_ceiling_exceeded` (bridge layer Part 4): the pass asks the orchestrator to close
it with a comment naming the hold, and the orchestrator decides again in its own transaction.

**THE BODY IS SHARED, THE PROGRAM IS NOT** (Tier 3 item 27, Devon 2026-09-28). Asking, classifying,
acting and reporting live in `lander.core`, parameterised by `LANE` below; this module keeps what
is this lane's alone -- its enumeration from change records, its credentials, its client, and the
refusal sets `LANE` names. It stays a separate program from `inert_lander` (ADR-0038).

**IT RUNS HOURLY; IT LANDS BUMPS ONLY BETWEEN 02:00 AND 06:00 NEW YORK** (Landing Part 3b). Every
pass first reads the watch of this lane's landings and takes the next step for any that went bad
(`revert_pass`: point production back at the build before the bump, open the revert, restore the
moving tag), and lands and freshens the SDS's own revert pull requests, which policy exempts from
the pace and the window. Ordinary bumps are landed and freshened only in passes inside those hours,
keeping the cadence they always had -- the orchestrator refuses outside its window regardless --
and only those passes sync the held-pull-request alerts, which would otherwise resolve every bump
the other twenty hours do not look at. Without `--submit` nothing is asked for and nothing on the
hosting platform or in the registry is read.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol
from zoneinfo import ZoneInfo

from change_proposer.cli import BOT_CHANGE_CLASS, REVERT_CHANGE_CLASS
from deploy_watcher.github import GitHubReader, ReadError
from estate_lander.change_manager import DEFAULT_BASE_URL as CM_DEFAULT_BASE_URL
from estate_lander.change_manager import ChangeManagerClient, ChangeManagerError
from estate_lander.image_repoint import (
    DEFAULT_COOLIFY_URL,
    AppSpec,
    CoolifyImages,
    Registry,
    RegistryUnreadable,
    RepointRefused,
)
from estate_lander.orchestrator_client import (
    DEFAULT_BASE_URL,
    OrchestratorClient,
    OrchestratorError,
)
from estate_lander.revert_pass import Production, lane_rows, revert_pass
from estate_lander.served import served_revision
from lander import core
from lander.core import EXIT_TOOL_FAILURE, EXIT_UNUSABLE, LandingClient, Lane, Outcome
from lander.held_alerts import sync_held_alerts, sync_revert_alerts
from sds_members.client import Member, MembersUnreadable, read_members

# The credential key id the orchestrator resolves the bearer against. A constant rather than a
# setting: an operator who could change it could only ever make the call unauthenticated.
SYSTEM_KEY_ID = "orchestrator-system"

# The ONE status this pass asks about, stated as an allowlist rather than as the set to skip. A
# denylist admits every status nobody has thought of -- `in_progress`, `handed_off`, `failed` --
# and each would be asked about, held, and reported as a finding nobody can act on. The service
# whose records these are warns about exactly this polarity.
_ASK_ABOUT = frozenset({"approved"})

# The change classes this lane is FOR. An allowlist, for the same reason `_ASK_ABOUT` is one, and
# the polarity matters more here than there: excluded means not landed, so a class nobody has
# thought of falls toward being left alone rather than toward being merged.
#
# WHY THIS EXISTS AT ALL. Until deploy policy version 4 (change-manager, 2026-08-25, ADR-0025) the
# only class the policy approved was `dependency-update`, so a factory record never reached
# `approved` and never became a subject here. Version 4 approves `factory-delivery` too, and this
# lane selects subjects on status alone -- so without this, a factory pull request would become a
# subject of a lane that asks NOTHING about the things a factory landing rests on: whether the work
# unit completed, whether its acceptance criteria were decided by the verifier from observed
# evidence, and whether a human's authority approval is bound to the envelope's exact fingerprint.
# Those are asked by the factory lane (`estate-pr-merge` is not it; `pr_merge_admission` is), and
# they are the whole basis of ADR-0020.
#
# WHAT USED TO KEEP FACTORY RECORDS OUT WAS AN ACCIDENT, WHICH IS WHY PROSE WOULD NOT DO. The
# update-type term reads a version delta out of a title and the pattern is only END-anchored, so
# `SDS <unit>: Reformat embedded code blocks` yields nothing while `SDS <unit>: Bump ruff from
# 0.15.20 to 0.16.2` yields `semver-patch` -- both measured. A unit title is free text a human
# writes, so the separation rested on wording nobody chose as a control.
#
# THE CONSTANTS ARE THE PRODUCER'S OWN. `change_proposer` writes these two strings onto every
# record this lane reads, so importing them is what makes the two sides agree by construction. A
# third spelling here would be a copy of a vocabulary that already has two, which is the defect
# this estate keeps paying for.
#
# `dependency-revert` (Landing Part 3b): the SDS's own revert of a bump in a repository that
# redeploys. The admission reads the record's class and asks a revert the revert's terms -- the
# served `revert_landing` conditions and Part 3a's revert terms -- and refuses a class and pull
# request that disagree, either way.
_LANE_CLASSES = frozenset({BOT_CHANGE_CLASS, REVERT_CHANGE_CLASS})

# The hours in which this lane lands and freshens ordinary bumps: 02:00 to 06:00 in the zone policy
# declares its change window in. A revert subject, and the watch's steps, are every pass.
LANDING_ZONE: Final = ZoneInfo("America/New_York")
LANDING_HOURS: Final = range(2, 6)
_WATCH_ROW: Final = "landing-watch"
# The member record's applications that serve a repository's default branch: every member here
# lands on `main` (the estate's convention), and an application on any other branch is a preview.
_SERVED_BRANCH: Final = "main"
# GitHub's compare statuses that mean the served commit holds the one asked about.
_HOLDS: Final = frozenset({"identical", "ahead"})  # not-a-vocabulary: GitHub's compare statuses

# What a deferral is called in the report, keyed by the class that caused it. Named rather than
# counted as one lump, because "it belongs to the factory lane" and "nobody has taught this program
# what that class is" are different facts with different next steps.
_DEFERRAL_UNREADABLE = "unreadable-class"

# What THIS lane does differently inside the shared body. Each field is pinned by a test that
# reddens if it is collapsed into the inert lane's.
LANE = Lane(
    name="estate",
    # Refusals that are the system REFUSING ON PURPOSE: the daily pace for this repository is
    # spent, or the clock is outside the hours policy declares for changing something already
    # serving. Neither names a condition anybody can act on, and each clears itself when the
    # window next opens.
    deliberate=frozenset({"landing_pace_exhausted", "landing_outside_change_window"}),
    # Refusals that CURRENT POLICY can never clear. A requirement-range or grouped bump states no
    # single delta, so no rule about update types applies to it -- decided in ADR-0018 and
    # deliberately left, which is what makes it an exception rather than a defect. It waits on a
    # person, forever, and no pass of this program will ever change that.
    #
    # `landing_version_ceiling_exceeded` (bridge layer Part 4) is the same class: a hold in the SDS
    # member record clears only when a person lifts it. Its sibling
    # `landing_dependency_list_unreadable` is deliberately NOT here -- an unreadable input stops
    # loudly, as a finding. `landing_version_reverted` (Landing Part 3a) is the same class again:
    # the SDS reverted that version here, and it lands only by a person's hand.
    exception=frozenset(
        {
            "landing_update_type_unparseable",
            "landing_version_ceiling_exceeded",
            "landing_repository_not_granted",
            "landing_version_reverted",
        }
    ),
    # ADR-0019 Increment 6 and ADR-0045. The branch-update act's refusals that say only *the
    # answer moved between the read and the request*. `landing_mergeability_unknown` in particular
    # is ordinary rather than exotic: the platform answers `unknown` while it works.
    update_self_clearing=frozenset(
        {
            "estate_branch_update_head_moved",
            "estate_branch_update_not_qualified",
            "estate_branch_update_sibling_holding",
        }
    ),
    # ADR-0024: this lane evaluates a rollout pin, so a stale head can cause a pin refusal.
    reads_rollout_pin=True,
)


class RecordSource(Protocol):
    """The one read this pass needs from the change service: which changes were routed."""

    def records(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Selection:
    """What one read of the listing yielded: what to act on, and what was left to another lane.

    BOTH, from ONE read, because `records()` is a live request rather than a cached list -- so a
    second call to count the deferrals would put a second network call inside the `try` in `run`,
    where its failure discards the outcomes entirely. That is the same reason the two passes share
    this result instead of each reading for itself.
    """

    subjects: list[tuple[str, int]]
    deferred: dict[str, int]
    # Landing Part 3b: the subjects whose record is the SDS's revert of a bump, landed every pass.
    reverts: frozenset[tuple[str, int]] = field(default_factory=frozenset)


def _deferral_reason(row: dict[str, Any]) -> str | None:
    """Why this lane is not the one for this record, or None when it is.

    ONE predicate with two readers -- the subject list and the tally -- rather than two functions
    that agree today. Read only for a record that would OTHERWISE be a subject, so the number it
    produces means "approved records this lane declined because they belong elsewhere" rather than
    "every row of a class we do not land", which would count records nobody was going to act on.
    """
    change_class = row.get("change_class")
    if not isinstance(change_class, str) or not change_class:
        return _DEFERRAL_UNREADABLE
    if change_class not in _LANE_CLASSES:
        return change_class
    return None


def _subjects(records: RecordSource) -> Selection:
    """Every routed change worth asking about, in a stable order, and what was left alone.

    Sorted, so a pass that lands one of several is reproducible rather than dependent on whatever
    order the listing happened to answer in -- which matters because the orchestrator permits one
    landing per repository per window, so WHICH one lands is decided here.

    ONE function, used by both passes, so the landing pass and the branch-update pass can never
    disagree about which pull requests this program is for. Each pass is given this result rather
    than reading for itself, because the read is a live request.

    A DEFERRED RECORD GETS NO OUTCOME AND IS NOT A FINDING. It is not that this lane tried and
    could not; it is that the record is another lane's business, the way a draft or a person's own
    pull request is. But it is COUNTED and reported, because a subject that vanishes without a line
    is the silent failure this program is written against everywhere else.
    """
    subjects: list[tuple[str, int]] = []
    deferred: dict[str, int] = {}
    reverts: set[tuple[str, int]] = set()
    rows = sorted(
        (row for row in records.records() if isinstance(row, dict)),
        key=lambda row: (str(row.get("target_repository") or ""), row.get("id") or 0),
    )
    for row in rows:
        repository = row.get("target_repository")
        number = row.get("pull_request_number")
        # `bool` is an `int` and `True == 1`, so a boolean number would be asked about as pull
        # request one. Both the record reader and the producer's sweep exclude it for this exact
        # field; this is the third place that has to.
        if (
            not isinstance(repository, str)
            or not isinstance(number, int)
            or isinstance(number, bool)
        ):
            continue
        if row.get("status") not in _ASK_ABOUT:
            continue
        reason = _deferral_reason(row)
        if reason is not None:
            deferred[reason] = deferred.get(reason, 0) + 1
            continue
        subjects.append((repository, number))
        if row.get("change_class") == REVERT_CHANGE_CLASS:
            reverts.add((repository, number))
    return Selection(subjects=subjects, deferred=deferred, reverts=frozenset(reverts))


def _pass(subjects: list[tuple[str, int]], client: LandingClient, submit: bool) -> list[Outcome]:
    """Ask about every routed change."""
    return core.landing_pass(LANE, subjects, client, submit)


def _branch_updates(
    subjects: list[tuple[str, int]], client: LandingClient, submit: bool
) -> list[Outcome]:
    """Bring up to date the branches whose only obstacle is being behind; see `core`."""
    return core.branch_updates(LANE, subjects, client, submit)


def _now() -> datetime:
    return datetime.now(UTC)


def in_landing_hours(now: datetime) -> bool:
    """Is `now` inside the hours this lane lands ordinary bumps?"""
    return now.astimezone(LANDING_ZONE).hour in LANDING_HOURS


def _read_watch(client: OrchestratorClient) -> tuple[list[dict[str, Any]] | None, list[Outcome]]:
    """This lane's rows of the watch, or None and the one row saying it could not be read. A watch
    the orchestrator cannot answer for does not stop the landing pass; the alerts hold."""
    try:
        return lane_rows(client.landing_watch()), []
    except OrchestratorError as error:
        return None, [Outcome(_WATCH_ROW, 0, "unreadable", str(error))]


def _production(
    reader: GitHubReader,
    registry: Registry,
    members: _Members,
    coolify_url: str,
    coolify_token: str,
) -> Production:
    """What the watch pass reaches production through. No Coolify token: every step that would
    change production is refused by name, and alerted."""

    def apps(repository: str) -> tuple[AppSpec, ...]:
        return members.apps(repository)

    def at_or_after(repository: str, commit: str, served: str) -> bool:
        try:
            return reader.compare_status(repository, commit, served) in _HOLDS
        except ReadError:
            return False

    return Production(
        images=(lambda: CoolifyImages(coolify_url, coolify_token)) if coolify_token else None,
        registry=registry,
        apps=apps,
        served=served_revision,
        at_or_after=at_or_after,
    )


class _Members:
    """The member record, read once, on first need: the applications of one repository."""

    def __init__(self, token: str, base_url: str) -> None:
        self._token, self._base_url = token, base_url
        self._members: list[Member] | None = None

    def apps(self, repository: str) -> tuple[AppSpec, ...]:
        if self._members is None:
            try:
                self._members = read_members(
                    token=self._token, credential_key_id=SYSTEM_KEY_ID, base_url=self._base_url
                )
            except MembersUnreadable as error:
                raise RepointRefused(f"the member record could not be read: {error}") from None
        return tuple(
            AppSpec(app.name, app.health_url)
            for member in self._members
            if member.repository.lower() == repository.lower() and member.state != "retired"
            for app in member.apps
            if app.branch == _SERVED_BRANCH
        )


def _stranded(
    rows: Sequence[dict[str, Any]] | None, reader: GitHubReader
) -> tuple[frozenset[tuple[str, int]], frozenset[tuple[str, int]]]:
    """The landings whose recorded revert pull request GitHub no longer lists open, and those whose
    repository could not be listed (their alert is held)."""
    stranded: set[tuple[str, int]] = set()
    unknown: set[tuple[str, int]] = set()
    listed: dict[str, set[int] | None] = {}
    for row in rows or []:
        repository, number, revert = (
            row.get("repository"),
            row.get("pr_number"),
            row.get("revert_pr_number"),
        )
        if row.get("status") != "revert_opened" or not isinstance(repository, str):
            continue
        if not isinstance(number, int) or not isinstance(revert, int):
            continue
        if repository not in listed:
            try:
                listed[repository] = {
                    pull["number"]
                    for pull in reader.open_pull_requests(repository)
                    if isinstance(pull.get("number"), int)
                }
            except ReadError:
                listed[repository] = None
        open_numbers = listed[repository]
        if open_numbers is None:
            unknown.add((repository, number))
        elif revert not in open_numbers:
            stranded.add((repository, number))
    return frozenset(stranded), frozenset(unknown)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submit",
        action="store_true",
        help="actually ask for the landings. Without it the pass reports and asks for nothing.",
    )
    args = parser.parse_args(argv)

    cm_token = os.environ.get("ESTATE_LANDING_CHANGE_MANAGER_TOKEN", "")
    cm_url = os.environ.get("ESTATE_LANDING_CHANGE_MANAGER_URL", "")
    token = os.environ.get("ESTATE_LANDING_ORCHESTRATOR_TOKEN", "")
    url = os.environ.get("ESTATE_LANDING_ORCHESTRATOR_URL", "")
    github_token = os.environ.get("ESTATE_LANDING_GITHUB_TOKEN", "")
    # Optional: fetched by the launcher on `--submit` only. Without it the watch's steps that
    # change production are refused by name and alerted; nothing else is affected.
    coolify_token = os.environ.get("ESTATE_LANDING_COOLIFY_TOKEN", "")
    coolify_url = os.environ.get("ESTATE_LANDING_COOLIFY_URL", "") or DEFAULT_COOLIFY_URL
    for name, value in (
        ("ESTATE_LANDING_CHANGE_MANAGER_TOKEN", cm_token),
        ("ESTATE_LANDING_ORCHESTRATOR_TOKEN", token),
        ("ESTATE_LANDING_GITHUB_TOKEN", github_token),
    ):
        if not value:
            print(f"{name} is unset", file=sys.stderr)
            return EXIT_UNUSABLE

    now = _now()
    in_hours = in_landing_hours(now)
    base_url = url or DEFAULT_BASE_URL
    try:
        with (
            ChangeManagerClient(cm_token, base_url=cm_url or CM_DEFAULT_BASE_URL) as records,
            OrchestratorClient(token, SYSTEM_KEY_ID, base_url=base_url) as client,
            GitHubReader(github_token) as reader,
            Registry(github_token) as registry,
        ):
            # The watch first (Landing Part 3b): a bad landing is pointed back before anything
            # else lands on top of it.
            rows, watched = _read_watch(client)
            production = (
                _production(reader, registry, _Members(token, base_url), coolify_url, coolify_token)
                if args.submit
                else None
            )
            stepped = revert_pass(rows or [], client, production, args.submit)
            watched.extend(stepped.outcomes)
            # READ ONCE, used by both passes. Reading again between them would put a second
            # network call inside the `try`, where its failure discards `outcomes` entirely and
            # returns a bare tool error -- losing the report of a landing that already happened.
            selection = _subjects(records)
            subjects = (
                selection.subjects
                if in_hours
                else [subject for subject in selection.subjects if subject in selection.reverts]
            )
            outcomes = _pass(subjects, client, args.submit)
            outcomes.extend(_branch_updates(subjects, client, args.submit))
            stranded, unknown = _stranded(rows, reader)
            watched.extend(
                Outcome(repository, number, "revert-stranded", "its revert closed without landing")
                for repository, number in sorted(stranded)
            )
    except (ChangeManagerError, OrchestratorError, ReadError, RegistryUnreadable) as error:
        print(str(error), file=sys.stderr)
        return EXIT_TOOL_FAILURE

    hours = "inside" if in_hours else "outside"
    code = report([*outcomes, *watched], selection.deferred, [f"{hours} the landing hours"])
    if args.submit:
        # A pull request held for a day raises one alert (`lander.held_alerts`), only from a pass
        # that looked at every bump: outside the hours the bumps are not asked about, and a sync
        # would resolve their alerts. Each revert record raises its own, every pass, and so does a
        # landing this pass could not point back. A pass that returned above measured nothing and
        # must not resolve anything.
        reasons = [
            reason
            for reason in (
                sync_held_alerts("estate-landing", outcomes, now=now) if in_hours else None,
                sync_revert_alerts(
                    "estate-landing",
                    rows,
                    stranded=stranded,
                    unknown=unknown,
                    unpinned=stepped.unpinned,
                ),
            )
            if reason is not None
        ]
        if reasons:
            print(f"alert-hub: {'; '.join(reasons)}", file=sys.stderr)
            return EXIT_TOOL_FAILURE
    return code


def report(
    outcomes: list[Outcome],
    deferred: dict[str, int] | None = None,
    preamble: list[str] | None = None,
) -> int:
    """Print every act considered, then what was left to another lane, then the summary.

    IT DOES NOT AFFECT THE EXIT CODE that something was deferred. Deferring is this program
    working: the record belongs to a lane with its own admission, and nothing here is unmet.
    `deferred` defaults to nothing said so that a caller reporting a list of outcomes it produced
    itself is not obliged to invent a tally -- and `run` is held to passing the real one by its own
    test, since a default that quietly meant "none" would be a wiring bug nothing could see. It is
    printed only when there is something to say, because a standing "0 deferred" is noise.
    """
    deferral_lines = [
        f"{count} {reason} record(s) not considered; they belong to another lane"
        for reason, count in sorted((deferred or {}).items())
    ]
    return core.report(outcomes, preamble or [], deferral_lines)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
