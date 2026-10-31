"""Land the update bot's pull requests where landing on the default branch changes nothing
already serving. ADR-0038 part 2a.

**This program composes nothing and decides nothing.** It reads which repositories a person
declared, reads which pull requests are open in them, asks the orchestrator about each, prints the
answer, and -- only with `--submit` -- asks for the ones the orchestrator says are admissible.
Every term lives inside the orchestrator, in the transaction that records the act. That is what
makes a scheduled caller acceptable: the unattended thing is a caller, not a judge.

## Why this exists at all, and what it replaces

A GitHub Actions workflow armed auto-merge on these pull requests in each of six repositories.
ADR-0038 removes it and makes the orchestrator the merger, for a reason measured rather than
argued: an auto-merge armed with `GITHUB_TOKEN` fires no `on: push` workflow, so every one of
those landings skipped the default-branch CI that a landing by any other identity runs. The
orchestrator has a merge act, an admission cascade and a branch update. It had no caller.

## WHY IT ENUMERATES FROM GITHUB, WHICH ITS SIBLING DECLINES TO DO

`estate_lander` enumerates from change records and says why: reading GitHub "would produce the set
of open pull requests, which is a different question and a larger one -- and would put this
program in the business of deciding which of them belongs here."

**That objection is answered by the declaration, not overridden.** There are no change records
here and there cannot be: a record exists to carry acceptance criteria and a rollback plan for a
rollout, and a repository where landing deploys nothing has no subject for any of the three. What
takes the record's place is `inert_landing` in change-manager's landing policy -- a list of
repositories and permitted authors a person pinned. So this program reads a human-pinned
declaration and enumerates within it. It decides nothing about which repositories belong; it is
told, by the same holder its sibling is told by, through a different projection of one document.

## A SEPARATE PROGRAM FROM ITS SIBLING, SHARING ONE BODY WITH IT

Two programs (ADR-0038), one body (Tier 3 item 27, Devon 2026-09-28). Asking, classifying,
acting and reporting live in `lander.core`, parameterised by `LANE` below; what stays here is this
lane's alone -- the declaration it reads, its GitHub enumeration, its three credentials, its
confined client, and the refusal sets `LANE` names.

Before the merge this section argued against sharing, measured against `estate_lander`: four of
that program's classification constructs are unreachable on an answer from this lane, and sharing
the classifier would install suppressions that cannot fire. **That objection is answered by the
descriptor, not overridden.** `LANE` carries an EMPTY deliberate set, this lane's own exception,
its own `inert_*` self-clearing set, and `reads_rollout_pin=False` -- so the shared body installs
nothing for this lane that this lane did not already have, and each of those four fields is pinned
by a test that reddens if it is collapsed into the sibling's. The two remaining reasons still hold
and are why the PROGRAMS stay two: the schedule's reason inverts (that lane runs in the change
window because landing changes something serving; this lane's population is defined by changing
nothing), and one dead-man check per lane keeps a standing finding in one from hiding a new finding
in the other.

## THERE IS NO DELIBERATE REFUSAL HERE, AND THAT IS DERIVED RATHER THAN OMITTED

Its sibling classifies two refusals as the system refusing on purpose -- the day's pace is spent,
or the clock is outside the declared hours -- and neither is a finding because each clears itself.
**This lane has no clock.** It has no change window and no pace rule, decided rather than
inherited: given freshness, a landing stales every sibling, so at most one pull request per
repository is landable per pass and freshness serialises the lane by itself. So there is no
refusal it can raise that clears on a clock, and every refusal that is not a settled subject is a
finding somebody can act on. An empty set copied from the sibling would look like an oversight;
having none is the statement. `waiting` does not change that: it is keyed on an update-bot sibling
the orchestrator observed edited and queued to land (ADR-0045), or on a revert in progress in the
repository (`landing_repository_reverting`, Landing Part 3a), and each clears when that lands, not
on a clock.

**A refusal excused for ACTING is not thereby excused for REPORTING**, and the one candidate was
measured rather than assumed. `qualifies_for_branch_update` excuses `landing_checks_awaiting_
verdict` when deciding whether the lane may freshen a branch, because freshening is what re-runs
an abandoned check. A matching suppression HERE could never fire: qualifying requires the head to
be behind its base, being behind is itself a refusal in no set, so the line is held whatever is
said about the other code. Its sibling wrote that suppression, measured it inert and removed it;
this program does not write it.

## EXPECT A PASS TO LAND AT MOST ONE PULL REQUEST PER REPOSITORY, and read the report

Freshness is required, so a landing puts every sibling in that repository behind its base and the
branch-update pass brings them up to date for the next run. A night on which one lands and two are
held for want of a head current with its base is the design working.

**UNTIL THE ORCHESTRATOR SERVES THESE ROUTES, EVERY SUBJECT REPORTS `unreadable`.** They are
merged and not deployed, and a route the deployed image does not serve answers 404 -- which this
program reports as a pull request it could not ask about, never as one it asked about and was
refused. The two are different lines and different words, so a pass in that state is
distinguishable from a genuine finding by reading it.

EXIT CODES, the whole interface a scheduled run has:
  0  everything was measured; nothing was held for a reason that needs a person.
  1  the tool itself failed (a missing or unreadable credential, an unhandled error).
  2  the tool ran but could not use its inputs.
  3  something was found -- a pull request held on a condition somebody has to act on.

**THE VOCABULARY IS THE LANDER GROUP'S, CHOSEN RATHER THAN INHERITED.** The estate's seven
launchers split into two groups in which 2 and 3 mean opposite things, so a new lane must pick. It
picks this one because the two landers will be read side by side by the same person, and two
programs doing the same job with opposite codes is the worst available outcome. Note also that
`2` is reachable here and is not decoration: one declaration governs every repository at once, so
a policy this program cannot read stops the whole pass rather than one repository's.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from bump_proposer.change_manager import DEFAULT_BASE_URL as CM_DEFAULT_BASE_URL
from bump_proposer.landing_policy import (
    LandingPolicy,
    LandingPolicyError,
    read_landing_policy,
)
from deploy_watcher.github import GitHubReader, ReadError
from inert_lander.orchestrator_client import (
    DEFAULT_BASE_URL,
    LandingRefused,
    OrchestratorClient,
    OrchestratorError,
)
from lander import core
from lander.core import EXIT_TOOL_FAILURE, EXIT_UNUSABLE, LandingClient, Lane, Outcome
from lander.held_alerts import sync_held_alerts, sync_revert_alerts

# The credential key id the orchestrator resolves the bearer against. A constant rather than a
# setting: an operator who could change it could only ever make the call unauthenticated.
SYSTEM_KEY_ID = "orchestrator-system"

# What THIS lane does differently inside the shared body. Each field is pinned by a test that
# reddens if it is collapsed into the estate lane's.
LANE = Lane(
    name="inert",
    # NONE, and that is derived rather than omitted -- see the module docstring. This lane has no
    # clock, so no refusal it raises clears on one.
    deliberate=frozenset(),
    # Refusals that CURRENT POLICY can never clear. The deploy policy names the ecosystems whose
    # changes the required checks on a pull request do not exercise, and a pull request in one of
    # them waits on a person forever -- no pass of this program will ever change that. Devon's
    # ruling, 2026-08-13, made for the sibling lane: a record that cannot land under current policy
    # is an EXCEPTION, not a finding.
    #
    # THIS SET IS NOT `deliberate` UNDER ANOTHER NAME, and the module docstring's claim that this
    # lane has no deliberate refusal still stands. A deliberate refusal clears on a CLOCK and this
    # lane has no clock; an exception clears on nothing at all. Increment 2b surveyed the sibling's
    # exception set, found it holding `landing_update_type_unparseable` -- which this lane never
    # asks about -- and concluded the construct was unreachable here. `landing_ecosystem_excluded`
    # is a second member of the same class and the census missed it, by searching for the shape of
    # the answer rather than for the class.
    #
    # WHAT AN EXCEPTION DOES NOT DO IS RETIRE THE QUESTION. `orchestrator#3`, the live specimen, is
    # a language-version replacement the estate has to decide about; the exclusion says the FACTORY
    # must not land it, never that nobody should. Suppressing the line without recording that
    # decision elsewhere converts a deferred decision into silence, which is the failure this
    # category exists to prevent wearing the category's own clothes.
    #
    # `landing_version_ceiling_exceeded` (bridge layer Part 4) is the same class: a hold in the SDS
    # member record clears only when a person lifts it. Its sibling
    # `landing_dependency_list_unreadable` is deliberately NOT here -- an unreadable input stops
    # loudly, as a finding. `landing_version_reverted` (Landing Part 3a) is the same class again:
    # the SDS reverted that version here, and it lands only by a person's hand.
    exception=frozenset(
        {
            "landing_ecosystem_excluded",
            "landing_version_ceiling_exceeded",
            "landing_repository_not_granted",
            "landing_version_reverted",
        }
    ),
    # The branch-update act's refusals that say only *the answer moved between the read and the
    # request*. SPELLED `inert_*`, which is why it could never have been one set with the sibling's.
    update_self_clearing=frozenset(
        {
            "inert_branch_update_head_moved",
            "inert_branch_update_not_qualified",
            "inert_branch_update_sibling_holding",
        }
    ),
    # NO ROLLOUT PIN ENTERS THIS LANE. It does not evaluate one and the admission response
    # deliberately does not carry the key, so being behind is the whole of what a position can
    # cause here. If that ever stops being true, flipping this is a decision, not a default.
    reads_rollout_pin=False,
)

# What a pull request outside the declared authors is called in the report. ONE bucket, not one
# per author, because there is one fact and it is the same fact each time: this lane is for the
# accounts the declaration names and every other pull request belongs to a person. Its sibling
# keys its deferrals by change class because there two different next steps hide behind one
# count; here there is only one.
_DEFERRAL_AUTHOR = "not-a-declared-author"

# Landing Part 3a. The verdicts the orchestrator acts on, and how this program reports each
# record status and each act's answer. The orchestrator judges again inside the act; this program
# only relays.
_ACTED_ON = frozenset({"flaky_retry", "bad"})  # not-a-vocabulary: the watch's verdicts, relayed
_DRY_RUN = {"flaky_retry": "would-rerun", "bad": "would-revert"}
_RECORDED = {
    "rerun_requested": "reran",
    "revert_opened": "revert-opened",
    "revert_landed": "reverted",
    "refused_overlap": "revert-refused",
    "revert_failed": "revert-failed",
}
# The record statuses a watched landing is reported by on a pass that did not act on it: a refused
# or failed revert is a finding until it leaves the watch, and a landed one is said once a pass.
_REPORTED_RECORDS = frozenset({"revert_landed", "refused_overlap", "revert_failed"})
_WATCH_ROW = "landing-watch"


class Watch(Protocol):
    """The two orchestrator calls the watch needs, beside the landing client's."""

    def landing_watch(self) -> list[dict[str, Any]]: ...

    def revert_landing(
        self,
        repository: str,
        pr_number: int,
        *,
        expected_status: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


class PullRequestSource(Protocol):
    """The ONE read this pass needs from GitHub: which pull requests are open in a repository.

    A protocol rather than the concrete reader, for the reason `estate_lander` states about its
    own record source: it names the surface this program depends on, so a test can substitute it
    without a suppression comment, and it makes plain that nothing here reads anything else from
    GitHub. The reader it is satisfied by is read-only by construction -- every verb a GET,
    checked before the transport -- and projects a pull request to six named fields.
    """

    def open_pull_requests(self, repository: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Selection:
    """What one enumeration yielded: what to ask about, what was left alone, and what could not
    be read at all.

    ALL THREE FROM ONE ENUMERATION, because each repository costs a live request -- so a second
    walk to count the deferrals would double the reads and could disagree with the first.

    `unreadable` carries REPOSITORY-level outcomes rather than raising, so one repository GitHub
    could not answer for does not discard the pull requests of the other five. It is a finding:
    a repository nobody could enumerate is a repository whose queue is unmeasured, which is
    exactly the silence this lane exists to end.
    """

    subjects: list[tuple[str, int]]
    deferred: dict[str, int]
    unreadable: list[Outcome]
    # Landing Part 3a: every pull request GitHub listed open, and every repository it listed, so a
    # recorded revert that is no longer open can be told from one whose repository went unread.
    open_pulls: frozenset[tuple[str, int]] = frozenset()
    listed: frozenset[str] = frozenset()


def _subjects(
    reader: PullRequestSource,
    rule: LandingPolicy,
    reverts: frozenset[tuple[str, int]] = frozenset(),
) -> Selection:
    """Every open pull request this lane is for, in a stable order, and what was left alone.

    THE SCOPE IS THE DECLARATION'S, twice over: the repositories are the ones a person pinned, and
    the authors are the ones the same declaration names. Neither is written here. Both are the
    INERT block's (`rule.inert`, `covers_author`): the policy's deploying population is
    the bump proposer's to read, never this lane's.

    **THE AUTHOR FILTER IS A SCOPE TEST, NOT A PERMISSION TEST**, and the orchestrator decides the
    permission again -- on the login AND on the platform's own answer about whether the account is
    a machine, which a rename cannot take. Asking about every open pull request instead would
    report a person's own work as held on a condition they cannot act on, every pass, forever.

    SORTED, so a pass that lands one of several is reproducible rather than dependent on whatever
    order GitHub answered in -- which matters because freshness lets at most one pull request per
    repository land per pass, so WHICH one lands is decided here.

    ONE enumeration, used by both passes, so the landing pass and the branch-update pass can never
    disagree about which pull requests this program is for.

    `reverts` names the revert pull requests the orchestrator opened and recorded (Landing Part
    3a). The App opens them, which no declared author is, so each is a subject by that record
    rather than by its author; the orchestrator asks its own terms of it.
    """
    subjects: list[tuple[str, int]] = []
    deferred: dict[str, int] = {}
    unreadable: list[Outcome] = []
    open_pulls: set[tuple[str, int]] = set()
    listed: set[str] = set()
    for repository in sorted(rule.inert):
        try:
            pulls = reader.open_pull_requests(repository)
        except ReadError as error:
            unreadable.append(Outcome(repository, 0, "unreadable", str(error)))
            continue
        listed.add(repository)
        # NUMBERED FIRST, SORTED SECOND, and the order of those two is not cosmetic: sorting a
        # list whose numbers have not been checked compares whatever the platform answered, and
        # one string beside one integer raises a `TypeError` that no caller here catches -- a
        # scheduled pass ending in a traceback instead of a report.
        numbered: list[tuple[int, dict[str, Any]]] = []
        for pull in pulls:
            if not isinstance(pull, dict):
                continue
            number = pull.get("number")
            # `bool` is an `int` and `True == 1`, so a boolean number would be asked about as pull
            # request one. Three other readers in this repository exclude it for this exact field.
            if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                continue
            numbered.append((number, pull))
        for number, pull in sorted(numbered, key=lambda item: item[0]):
            open_pulls.add((repository, number))
            author = pull.get("author")
            if (repository, number) in reverts:
                subjects.append((repository, number))
                continue
            if not isinstance(author, str) or not rule.covers_author(author):
                deferred[_DEFERRAL_AUTHOR] = deferred.get(_DEFERRAL_AUTHOR, 0) + 1
                continue
            subjects.append((repository, number))
    return Selection(
        subjects=subjects,
        deferred=deferred,
        unreadable=unreadable,
        open_pulls=frozenset(open_pulls),
        listed=frozenset(listed),
    )


def _pass(subjects: list[tuple[str, int]], client: LandingClient, submit: bool) -> list[Outcome]:
    """Ask about every pull request in scope."""
    return core.landing_pass(LANE, subjects, client, submit)


def _branch_updates(
    subjects: list[tuple[str, int]], client: LandingClient, submit: bool
) -> list[Outcome]:
    """Bring up to date the branches whose only obstacle is being behind; see `core`."""
    return core.branch_updates(LANE, subjects, client, submit)


def revert_key(repository: str, number: int, row: dict[str, Any]) -> str:
    """CONTENT-ADDRESSED over the landing, the verdict and the failing runs: a pass that asks again
    about an unchanged judgment replays, and a new failing run is a new request."""
    runs = row.get("failed_runs")
    named = ",".join(
        f"{run[0]}.{run[1]}"
        for run in (runs if isinstance(runs, list) else [])
        if isinstance(run, list) and len(run) == 2
    )
    return f"{LANE.name}-landing-revert:{repository}:{number}:{row.get('verdict')}:{named}"


def _read_watch(client: Watch) -> tuple[list[dict[str, Any]] | None, list[Outcome]]:
    """This lane's rows of the watch, or None and the one row saying it could not be read. A watch
    the orchestrator cannot answer for does not stop the landing pass: it is reported, and held by
    the alerts.

    The client asks for this lane's landings alone; the filter stays as defence in depth, since a
    landing that redeploys is the estate lander's to act on and alert about. A row naming no lane
    is this lane's: an orchestrator that predates the field watched this lane alone.
    """
    try:
        watch = client.landing_watch()
    except OrchestratorError as error:
        return None, [Outcome(_WATCH_ROW, 0, "unreadable", str(error))]
    return [row for row in watch if row.get("lane", LANE.name) == LANE.name], []


def _open_reverts(watch: list[dict[str, Any]] | None) -> frozenset[tuple[str, int]]:
    """Each revert pull request the orchestrator opened and has not yet seen land."""
    return frozenset(
        (row["repository"], row["revert_pr_number"])
        for row in watch or []
        if row.get("status") == "revert_opened"
        and isinstance(row.get("repository"), str)
        and isinstance(row.get("revert_pr_number"), int)
    )


def _stranded(
    watch: list[dict[str, Any]] | None, selection: Selection, population: frozenset[str]
) -> tuple[frozenset[tuple[str, int]], frozenset[tuple[str, int]], frozenset[tuple[str, int]]]:
    """The landings whose recorded revert will not land, those whose repository went unlisted, and
    which of the first are stranded because their repository is outside this lane's population.

    Stranded means GitHub no longer lists the revert open (closed without landing: its record
    still says `revert_opened`), or the repository is one this lane never lists, so nothing lands
    it however open it is: a factory merge with a person's approval can land where the estate says
    `inert` and the policy doesn't declare. That is not an unknown: no later pass will read it.
    """
    stranded: set[tuple[str, int]] = set()
    unknown: set[tuple[str, int]] = set()
    outside: set[tuple[str, int]] = set()
    for row in watch or []:
        repository, number, revert = (
            row.get("repository"),
            row.get("pr_number"),
            row.get("revert_pr_number"),
        )
        if row.get("status") != "revert_opened" or not isinstance(repository, str):
            continue
        if not isinstance(number, int) or not isinstance(revert, int):
            continue
        if repository not in population:
            stranded.add((repository, number))
            outside.add((repository, number))
        elif repository not in selection.listed:
            unknown.add((repository, number))
        elif (repository, revert) not in selection.open_pulls:
            stranded.add((repository, number))
    return frozenset(stranded), frozenset(unknown), frozenset(outside)


def _revert_pass(watch: list[dict[str, Any]], client: Watch, submit: bool) -> list[Outcome]:
    """Ask the orchestrator to act on each landing it judged `flaky_retry` or `bad`, and report the
    rest of the watch that says something. Without `--submit` it asks for nothing."""
    outcomes: list[Outcome] = []
    for row in watch:
        repository, number = row.get("repository"), row.get("pr_number")
        if not isinstance(repository, str) or not isinstance(number, int):
            continue
        verdict, status = row.get("verdict"), row.get("status")
        detail = str(row.get("detail") or "")
        if verdict in _ACTED_ON:
            outcomes.append(_ask_to_act(client, repository, number, row, submit))
        elif verdict == "unreadable":
            outcomes.append(Outcome(repository, number, "unreadable", detail))
        elif verdict is None and status in _REPORTED_RECORDS:
            outcomes.append(Outcome(repository, number, _RECORDED[status], detail))
    return outcomes


def _ask_to_act(
    client: Watch, repository: str, number: int, row: dict[str, Any], submit: bool
) -> Outcome:
    verdict = str(row.get("verdict"))
    if not submit:
        return Outcome(repository, number, _DRY_RUN[verdict], str(row.get("detail") or ""))
    try:
        status = row.get("status")
        answer = client.revert_landing(
            repository,
            number,
            expected_status=status if isinstance(status, str) else None,
            idempotency_key=revert_key(repository, number, row),
        )
    except (LandingRefused, OrchestratorError) as error:
        return Outcome(repository, number, "error", str(error))
    status = answer.get("status")
    detail = str(answer.get("detail") or "")
    if answer.get("acted") is False:
        # A read inside the act failed, so nothing was rerun or reverted and the bad bump stays:
        # a finding, as the watch's own `unreadable` is.
        if answer.get("verdict") == "unreadable":
            return Outcome(repository, number, "unreadable", detail or "a read in the act failed")
        # Otherwise the act judged again and found nothing to do: the watch and the act
        # disagreed, a harmless race, and the next pass asks again. Not a finding.
        return Outcome(repository, number, "waiting", detail or "nothing to do yet")
    if answer.get("acted") is not True or not isinstance(status, str) or status not in _RECORDED:
        return Outcome(repository, number, "unreadable", detail or "nothing was done")
    return Outcome(repository, number, _RECORDED[status], detail)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Land the update bot's inert-population work.")
    parser.add_argument(
        "--submit",
        action="store_true",
        help="actually ask for the landings. Without it the pass reports and asks for nothing.",
    )
    args = parser.parse_args(argv)

    cm_token = os.environ.get("INERT_LANDING_CHANGE_MANAGER_TOKEN", "")
    cm_url = os.environ.get("INERT_LANDING_CHANGE_MANAGER_URL", "")
    token = os.environ.get("INERT_LANDING_ORCHESTRATOR_TOKEN", "")
    url = os.environ.get("INERT_LANDING_ORCHESTRATOR_URL", "")
    github_token = os.environ.get("INERT_LANDING_GITHUB_TOKEN", "")
    # NAMED ONE AT A TIME rather than as a single "credentials missing". Three different people
    # fix these three, and a launcher that fetched two of three would otherwise report the same
    # line whichever it dropped.
    for name, value in (
        ("INERT_LANDING_CHANGE_MANAGER_TOKEN", cm_token),
        ("INERT_LANDING_ORCHESTRATOR_TOKEN", token),
        ("INERT_LANDING_GITHUB_TOKEN", github_token),
    ):
        if not value:
            print(f"{name} is unset", file=sys.stderr)
            return EXIT_UNUSABLE

    try:
        rule = read_landing_policy(cm_token, base_url=cm_url or CM_DEFAULT_BASE_URL)
    except LandingPolicyError as error:
        # THE WHOLE PASS, not one repository. One declaration covers every repository at once, so
        # reporting this per repository would be N copies of one fact -- and a rule this program
        # cannot read is a rule it will not guess at.
        print(str(error), file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        with (
            GitHubReader(github_token) as reader,
            OrchestratorClient(token, SYSTEM_KEY_ID, base_url=url or DEFAULT_BASE_URL) as client,
        ):
            # The watch is read and acted on FIRST (Landing Part 3a): a bad landing is rerun or
            # reverted before anything else lands, since a sibling landed on top of it would make
            # its revert an overlap. The revert pull requests that read names are subjects of the
            # landing and branch-update passes below; one this pass's act just opened is not, and
            # needs none yet: its checks have not run.
            watch, watched = _read_watch(client)
            if watch is not None:
                watched.extend(_revert_pass(watch, client, args.submit))
            selection = _subjects(reader, rule, _open_reverts(watch))
            outcomes = list(selection.unreadable)
            outcomes.extend(_pass(selection.subjects, client, args.submit))
            outcomes.extend(_branch_updates(selection.subjects, client, args.submit))
            stranded, unknown, outside = _stranded(watch, selection, rule.inert)
            watched.extend(
                Outcome(
                    repository,
                    number,
                    "revert-stranded",
                    "its repository is outside this lane's population, so nothing lands its revert"
                    if (repository, number) in outside
                    else "its revert closed without landing",
                )
                for repository, number in sorted(stranded)
            )
    except (ReadError, OrchestratorError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_TOOL_FAILURE

    code = report([*outcomes, *watched], selection.deferred, rule.version)
    if args.submit:
        # A pull request held for a day raises one alert (`lander.held_alerts`), and each revert
        # record one of its own. Only a submitting pass sends; a pass that returned above measured
        # nothing and must not resolve anything. The watch's rows stay out of the held alerts:
        # they are landings, not pull requests held.
        now = datetime.now(UTC)
        reasons = [
            reason
            for reason in (
                sync_held_alerts("inert-landing", outcomes, now=now),
                sync_revert_alerts(
                    "inert-landing", watch, stranded=stranded, unknown=unknown, outside=outside
                ),
            )
            if reason is not None
        ]
        if reasons:
            print(f"alert-hub: {'; '.join(reasons)}", file=sys.stderr)
            return EXIT_TOOL_FAILURE
    return code


def report(outcomes: list[Outcome], deferred: dict[str, int], policy_version: int) -> int:
    """Print the policy version, every act considered, what was left to a person, the summary.

    THE POLICY VERSION IS PRINTED FIRST, because it is the whole of the permission this pass
    exercised and one number covers both of the document's populations -- so a reader comparing
    two nights needs to know whether the declaration moved under them.

    A DEFERRED PULL REQUEST DOES NOT AFFECT THE EXIT CODE. Deferring is this program working: a
    person's own pull request is not this lane's business, and nothing here is unmet. It is printed
    only when there is something to say, because a standing "0 deferred" is noise.
    """
    deferral_lines = [
        f"{count} open pull request(s) {reason}; they are not this lane's business"
        for reason, count in sorted(deferred.items())
    ]
    return core.report(outcomes, [f"landing policy version {policy_version}"], deferral_lines)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
