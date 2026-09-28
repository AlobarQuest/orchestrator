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
having none is the statement. `waiting` (ADR-0045) does not change that: it is keyed on an
update-bot sibling the orchestrator observed edited and queued to land, not on a clock.

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
    OrchestratorClient,
    OrchestratorError,
)
from lander import core
from lander.core import EXIT_TOOL_FAILURE, EXIT_UNUSABLE, LandingClient, Lane, Outcome

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
    exception=frozenset({"landing_ecosystem_excluded"}),
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


def _pass(subjects: list[tuple[str, int]], client: LandingClient, submit: bool) -> list[Outcome]:
    """Ask about every pull request in scope."""
    return core.landing_pass(LANE, subjects, client, submit)


def _branch_updates(
    subjects: list[tuple[str, int]], client: LandingClient, submit: bool
) -> list[Outcome]:
    """Bring up to date the branches whose only obstacle is being behind; see `core`."""
    return core.branch_updates(LANE, subjects, client, submit)


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
