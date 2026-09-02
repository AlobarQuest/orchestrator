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

## WHY IT IS A SEPARATE PROGRAM FROM ITS SIBLING, WHICH WAS THE OPEN QUESTION

Measured against `estate_lander` before this was written, and recorded because "one lane, two
passes" was the cheaper-looking answer:

- FOUR of that program's five classification constructs are UNREACHABLE on an answer from this
  lane. `_DELIBERATE` names the pace and the change window, and this lane has neither. `_EXCEPTION`
  names an unparseable update type, which this lane never asks about. `_ROLLOUT_MOVED` and
  `_BASE_MATCHES_PIN` name a rollout pin this lane does not evaluate, and whose key the admission
  response deliberately does not carry. Sharing that classifier would install four suppressions
  that cannot fire -- and that file's own `_held_status` argues, about a fifth, that "an inert
  suppression is worse than none, because a later change to the freshness rule would switch it on
  with nobody re-deciding it."
- The one construct that IS reachable could not have been shared anyway: the branch update's
  self-clearing refusals are spelled `inert_*` here and `estate_*` there, so a second set was
  needed either way and the saving does not exist.
- The schedule's stated reason inverts. That lane runs in the change window because "this pass
  ends in something changing a running service, and policy declares the hours in which something
  already serving may change." The defining property of this lane's population is that landing
  changes nothing already serving.
- One dead-man check per lane. Folding both into one exit code lets a standing finding in one hide
  a new finding in the other from whoever reads it.

## THERE IS NO DELIBERATE REFUSAL HERE, AND THAT IS DERIVED RATHER THAN OMITTED

Its sibling classifies two refusals as the system refusing on purpose -- the day's pace is spent,
or the clock is outside the declared hours -- and neither is a finding because each clears itself.
**This lane has no clock.** It has no change window and no pace rule, decided rather than
inherited: given freshness, a landing stales every sibling, so at most one pull request per
repository is landable per pass and freshness serialises the lane by itself. So there is no
refusal it can raise that clears on a clock, and every refusal that is not a settled subject is a
finding somebody can act on. An empty set copied from the sibling would look like an oversight;
having none is the statement.

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
from typing import Any, Protocol

from bump_proposer.change_manager import DEFAULT_BASE_URL as CM_DEFAULT_BASE_URL
from bump_proposer.landing_policy import (
    InertLanding,
    LandingPolicyError,
    read_inert_landing,
)
from deploy_watcher.github import GitHubReader, ReadError
from inert_lander.orchestrator_client import (
    DEFAULT_BASE_URL,
    LandingRefused,
    OrchestratorClient,
    OrchestratorError,
)

EXIT_OK = 0
EXIT_TOOL_FAILURE = 1
EXIT_UNUSABLE = 2
EXIT_FINDINGS = 3

# The credential key id the orchestrator resolves the bearer against. A constant rather than a
# setting: an operator who could change it could only ever make the call unauthenticated.
SYSTEM_KEY_ID = "orchestrator-system"

# Refusals that mean the SUBJECT IS SETTLED rather than that a condition is unmet -- the pull
# request is gone, or this lane has already acted on it and the row it wrote has no delete path.
# Neither is something a person can act on, and reporting them as findings would make one landing
# a nightly page forever. The line is still printed; it just is not a finding.
#
# TESTED WITH INTERSECTION, deliberately, and this is the one place that polarity is right: a
# settled subject's other refusals are meaningless because there is nothing left to land. Its
# sibling's `_held_status` argues the opposite polarity for a DELIBERATE refusal, which says
# nothing about the conditions beside it -- and this lane has no deliberate refusal at all.
_SETTLED = frozenset({"landing_already_recorded", "landing_pull_request_not_open"})

# Refusals the BRANCH-UPDATE act raises that say only *the answer moved between the read and the
# request*, which the next pass re-decides on its own.
#
# The answer and the act are separate transactions by design: the orchestrator recomposes every
# term inside the one that acts, and it reads the platform again while doing so. So a head the
# update bot rebased in that window, and an answer that no longer qualifies, both arrive here --
# and neither is a condition anybody can act on. Every OTHER refusal stays a finding, including
# one this program cannot parse a code from.
#
# SPELLED `inert_*`, WHICH IS WHY IT COULD NOT HAVE BEEN SHARED with the sibling lane's set even
# had everything else been shareable.
_UPDATE_SELF_CLEARING = frozenset(
    {"inert_branch_update_head_moved", "inert_branch_update_not_qualified"}
)

# Refusals that CURRENT POLICY can never clear. The deploy policy names the ecosystems whose
# changes the required checks on a pull request do not exercise, and a pull request in one of them
# waits on a person forever -- no pass of this program will ever change that. Devon's ruling,
# 2026-08-13, made for the sibling lane: a record that cannot land under current policy is an
# EXCEPTION, not a finding.
#
# THIS SET IS NOT `_DELIBERATE` UNDER ANOTHER NAME, and the module docstring's claim that this lane
# has no deliberate refusal still stands. A deliberate refusal clears on a CLOCK and this lane has
# no clock; an exception clears on nothing at all. Increment 2b surveyed the sibling's `_EXCEPTION`,
# found it holding `landing_update_type_unparseable` -- which this lane never asks about -- and
# concluded the construct was unreachable here. `landing_ecosystem_excluded` is a second member of
# the same class and the census missed it, by searching for the shape of the answer rather than for
# the class.
#
# WHAT AN EXCEPTION DOES NOT DO IS RETIRE THE QUESTION. `orchestrator#3`, the live specimen, is a
# language-version replacement the estate has to decide about; the exclusion says the FACTORY must
# not land it, never that nobody should. Suppressing the line without recording that decision
# elsewhere converts a deferred decision into silence, which is the failure this category exists to
# prevent wearing the category's own clothes.
_EXCEPTION = frozenset({"landing_ecosystem_excluded"})

# The refusal that says only THIS BRANCH IS BEHIND ITS BASE. It belongs to no set: alone it is a
# FINDING -- transient, and a later branch-update pass clears it -- while beside an exception it is
# not, because such a branch is behind precisely because this lane has decided, permanently, not to
# touch it. Membership is a property of a code; this is a property of the company it keeps.
#
# KEYED ON THE EXCEPTION BEING PRESENT, NEVER ON THIS LANE HAVING DECLINED TO FRESHEN. Those read
# as one rule and are two: the lane declines to freshen anything it cannot clear, INCLUDING a
# failing check, so keying on the declining would silence a red build. The discriminator is
# DURABILITY -- red checks can go green, an exception never clears. (Devon's third refusal ruling,
# 2026-08-14, transcribed here rather than imported: the sibling's module cannot be imported by
# this one, which is isolated from it on purpose.)
#
# NO ROLLOUT PIN ENTERS THIS. The sibling subtracts a criterion rather than a member because a
# stale head can also produce a rollout-pin refusal; this lane does not evaluate a rollout pin at
# all and the admission response deliberately does not carry its key, so being behind is the whole
# of what a position can cause here. If that ever stops being true, this becomes a criterion.
_FRESHNESS = "landing_head_not_current_with_base"

# What a pull request outside the declared authors is called in the report. ONE bucket, not one
# per author, because there is one fact and it is the same fact each time: this lane is for the
# accounts the declaration names and every other pull request belongs to a person. Its sibling
# keys its deferrals by change class because there two different next steps hide behind one
# count; here there is only one.
_DEFERRAL_AUTHOR = "not-a-declared-author"

# Statuses that are not findings, stated as the set to EXCLUDE so a status nobody has thought of
# fails toward being reported.
#
# A branch brought up to date is the lane clearing a condition the lane itself caused, which is
# the system working. `would-update` likewise: it is what a dry run has to say in order to be
# worth running.
_NOT_A_FINDING = frozenset(
    {"landed", "would-land", "settled", "deliberate", "exception", "updated", "would-update"}
)

# Every status a pass can produce, in report order, so the summary's counts sum to what was
# considered. A summary whose parts do not add up leaves the reader to infer the remainder, and
# the remainder is where the findings are.
_REPORTED = (
    "landed",
    "would-land",
    "held",
    "deliberate",
    "exception",
    "settled",
    "unreadable",
    "error",
    "updated",
    "would-update",
)


class PullRequestSource(Protocol):
    """The ONE read this pass needs from GitHub: which pull requests are open in a repository.

    A protocol rather than the concrete reader, for the reason `estate_lander` states about its
    own record source: it names the surface this program depends on, so a test can substitute it
    without a suppression comment, and it makes plain that nothing here reads anything else from
    GitHub. The reader it is satisfied by is read-only by construction -- every verb a GET,
    checked before the transport -- and projects a pull request to six named fields.
    """

    def open_pull_requests(self, repository: str) -> list[dict[str, Any]]: ...


class LandingClient(Protocol):
    """The whole orchestrator surface these passes use: one question and two acts.

    Named here so the surface is a statement rather than whatever the concrete client happens to
    expose. The client that satisfies it enforces the three paths before the transport, which is
    the control; this is the declaration of intent above it.
    """

    def admission(self, repository: str, pr_number: int) -> dict[str, Any]: ...

    def land(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]: ...

    def update_branch(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Outcome:
    repository: str
    number: int
    status: str
    detail: str


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


def _key(repository: str, number: int, head_sha: str) -> str:
    """CONTENT-ADDRESSED over the subject and the head, so a replay is a replay.

    A random key would make every pass a new request for the same act, which the orchestrator
    would refuse as a spent key belonging to a different subject -- turning an ordinary re-run
    into a finding. Naming the head as well as the pull request means a genuinely new attempt
    after a rebase is a genuinely new key.

    THE PREFIX IS THIS LANE'S OWN. The two lanes cannot have the same subject -- each requires
    the opposite answer from the estate about a repository -- but a shared prefix would make that
    a fact a reader has to know rather than one the key states.
    """
    return f"inert-landing:{repository}:{number}:{head_sha[:12]}"


def _update_key(repository: str, number: int, head_sha: str) -> str:
    """Content-addressed over the head, for the reason above and one more that is specific here.

    A successful update CHANGES the head, so the next legitimate update -- after the base moves
    again -- necessarily carries a different key and can never be barred by this one. That is what
    makes an idempotency key safe on an act whose whole nature is that repeating it is right.
    """
    return f"inert-branch-update:{repository}:{number}:{head_sha[:12]}"


def _subjects(reader: PullRequestSource, rule: InertLanding) -> Selection:
    """Every open pull request this lane is for, in a stable order, and what was left alone.

    THE SCOPE IS THE DECLARATION'S, twice over: the repositories are the ones a person pinned, and
    the authors are the ones the same declaration names. Neither is written here.

    **THE AUTHOR FILTER IS A SCOPE TEST, NOT A PERMISSION TEST**, and the orchestrator decides the
    permission again -- on the login AND on the platform's own answer about whether the account is
    a machine, which a rename cannot take. Asking about every open pull request instead would
    report a person's own work as held on a condition they cannot act on, every pass, forever.

    SORTED, so a pass that lands one of several is reproducible rather than dependent on whatever
    order GitHub answered in -- which matters because freshness lets at most one pull request per
    repository land per pass, so WHICH one lands is decided here.

    ONE enumeration, used by both passes, so the landing pass and the branch-update pass can never
    disagree about which pull requests this program is for.
    """
    subjects: list[tuple[str, int]] = []
    deferred: dict[str, int] = {}
    unreadable: list[Outcome] = []
    for repository in sorted(rule.repositories):
        try:
            pulls = reader.open_pull_requests(repository)
        except ReadError as error:
            unreadable.append(Outcome(repository, 0, "unreadable", str(error)))
            continue
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
            author = pull.get("author")
            if not isinstance(author, str) or not rule.covers_author(author):
                deferred[_DEFERRAL_AUTHOR] = deferred.get(_DEFERRAL_AUTHOR, 0) + 1
                continue
            subjects.append((repository, number))
    return Selection(subjects=subjects, deferred=deferred, unreadable=unreadable)


def _unsatisfied_status(refusals: list[str]) -> str:
    """`held` or `exception`, for an answer that is unsatisfied and not settled.

    NO REFUSALS AT ALL IS HELD, never a vacuous pass. An answer unsatisfied while naming nothing is
    the orchestrator failing to say why, which is exactly the thing worth reporting -- and a bare
    subset test would call it an exception.

    A FRESHNESS REFUSAL IS SUPPRESSED WHEN, AND ONLY WHEN, AN EXCEPTION IS PRESENT. Conditional,
    never unconditional: unconditionally, a branch that is merely behind would read as quiet, and
    `{behind, checks_not_clean}` would go quiet with it. Both are the over-general version of this
    rule, which is the shape every fix in this family has taken.

    EVERY OTHER CODE STAYS A FINDING, including one this program does not enumerate, so a refusal
    nobody has thought of fails toward being reported.
    """
    present = set(refusals)
    unexplained = present - _EXCEPTION
    if _EXCEPTION & present:
        unexplained.discard(_FRESHNESS)
    if unexplained or not refusals:
        return "held"
    return "exception"


def _consider(client: LandingClient, repository: str, number: int, submit: bool) -> Outcome:
    """Ask about one pull request, and act when told the answer is yes.

    AN UNSATISFIED ANSWER THAT IS NOT SETTLED IS HELD OR AN EXCEPTION -- see `_unsatisfied_status`,
    and `_EXCEPTION` for why this lane grew that one suppression while still having no deliberate
    refusal to suppress.
    """
    try:
        answer = client.admission(repository, number)
    except OrchestratorError as error:
        return Outcome(repository, number, "unreadable", str(error))

    refusals = [str(r) for r in (answer.get("refusals") or [])]
    if _SETTLED & set(refusals):
        return Outcome(repository, number, "settled", ", ".join(refusals))
    if not answer.get("satisfied"):
        return Outcome(repository, number, _unsatisfied_status(refusals), ", ".join(refusals))

    head = answer.get("head_sha")
    if not isinstance(head, str) or not head:
        # Admissible with no head is unreachable through the orchestrator's own cascade, which
        # refuses an unreadable pull request. Stated rather than assumed, because acting without
        # a head would be asking for whatever has been pushed since.
        return Outcome(repository, number, "unreadable", "admissible but names no head")
    if not submit:
        return Outcome(repository, number, "would-land", f"head {head[:12]}")

    try:
        landed = client.land(
            repository, number, head_sha=head, idempotency_key=_key(repository, number, head)
        )
    except LandingRefused as error:
        return Outcome(repository, number, "held", str(error))
    except OrchestratorError as error:
        return Outcome(repository, number, "error", str(error))
    return Outcome(repository, number, "landed", f"status={landed.get('status')}")


def _pass(subjects: list[tuple[str, int]], client: LandingClient, submit: bool) -> list[Outcome]:
    """Ask about every pull request in scope."""
    return [_consider(client, repository, number, submit) for repository, number in subjects]


def _branch_updates(
    subjects: list[tuple[str, int]], client: LandingClient, submit: bool
) -> list[Outcome]:
    """Bring up to date the branches whose only remaining obstacle is that they are behind.

    **AFTER the landing pass, and that ordering is load-bearing.** A landing moves the base, so it
    is the act that puts every sibling behind; going first would bring a branch up to date and
    then immediately stale it again by landing something else, spending a real build on a tree
    that is out of date before it finishes.

    IT RUNS ON EVERY PASS, not only on one that landed something. A pull request a person merged
    themselves stales its siblings exactly as ours does, and one staled that way is invisible to
    anything that only reacts to this program's own acts.

    The answer is READ AGAIN rather than carried over from the landing pass, because the landing
    pass may have changed it -- which is the whole reason this runs second.

    WHICH ONES QUALIFY IS NOT DECIDED HERE. The orchestrator says so on the answer, and it says so
    again inside the transaction that acts; this program relays it. A `branch_update_qualifies`
    key the deployed image does not serve reads as False, which withholds the act -- the direction
    to fail in, and not hypothetical: its sibling read a key that was not there for two days and
    freshened nothing while reporting zero.
    """
    outcomes: list[Outcome] = []
    for repository, number in subjects:
        try:
            answer = client.admission(repository, number)
        except OrchestratorError as error:
            outcomes.append(Outcome(repository, number, "unreadable", str(error)))
            continue
        if not answer.get("branch_update_qualifies"):
            continue
        head = answer.get("head_sha")
        if not isinstance(head, str) or not head:
            outcomes.append(
                Outcome(repository, number, "unreadable", "qualifies but names no head")
            )
            continue
        if not submit:
            outcomes.append(Outcome(repository, number, "would-update", f"head {head[:12]}"))
            continue
        try:
            answered = client.update_branch(
                repository,
                number,
                head_sha=head,
                idempotency_key=_update_key(repository, number, head),
            )
        except LandingRefused as error:
            status = "deliberate" if error.code in _UPDATE_SELF_CLEARING else "held"
            outcomes.append(Outcome(repository, number, status, str(error)))
        except OrchestratorError as error:
            outcomes.append(Outcome(repository, number, "error", str(error)))
        else:
            if answered.get("replayed"):
                # ASKED BEFORE, AT THIS SAME HEAD, AND THE BRANCH HAS NOT MOVED. The key is
                # content-addressed over the head and a success moves it, so this is the platform
                # having accepted the work and not delivered it. Reporting it as an update would
                # describe that as success on every pass, forever.
                outcomes.append(
                    Outcome(
                        repository, number, "held", f"asked before at {head[:12]}, still behind"
                    )
                )
            else:
                outcomes.append(
                    Outcome(repository, number, "updated", f"was behind at {head[:12]}")
                )
    return outcomes


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
        rule = read_inert_landing(cm_token, base_url=cm_url or CM_DEFAULT_BASE_URL)
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
            selection = _subjects(reader, rule)
            outcomes = list(selection.unreadable)
            outcomes.extend(_pass(selection.subjects, client, args.submit))
            outcomes.extend(_branch_updates(selection.subjects, client, args.submit))
    except (ReadError, OrchestratorError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_TOOL_FAILURE

    return report(outcomes, selection.deferred, rule.version)


def report(outcomes: list[Outcome], deferred: dict[str, int], policy_version: int) -> int:
    """Print every act considered, then what was left to a person, then the summary.

    THE POLICY VERSION IS PRINTED FIRST, because it is the whole of the permission this pass
    exercised and one number covers both of the document's populations -- so a reader comparing
    two nights needs to know whether the declaration moved under them.

    THE DEFERRAL LINE IS SEPARATE FROM THE SUMMARY, NOT FOLDED INTO IT. `_REPORTED` exists so the
    summary's parts add up to what was considered, and a deferred pull request was never
    considered. It is printed only when there is something to say, because a standing "0 deferred"
    is noise on a lane that will usually have none.

    IT DOES NOT AFFECT THE EXIT CODE. Deferring is this program working: a person's own pull
    request is not this lane's business, and nothing here is unmet.
    """
    print(f"landing policy version {policy_version}")
    for outcome in outcomes:
        subject = f"{outcome.repository}#{outcome.number}"
        print(f"{subject}  {outcome.status:<12} {outcome.detail}")
    for reason, count in sorted(deferred.items()):
        print(f"{count} open pull request(s) {reason}; they are not this lane's business")
    counted = {status: sum(o.status == status for o in outcomes) for status in _REPORTED}
    findings = [o for o in outcomes if o.status not in _NOT_A_FINDING]
    print(
        f"\n{len(outcomes)} considered, "
        + ", ".join(f"{counted[status]} {status}" for status in _REPORTED)
    )
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
