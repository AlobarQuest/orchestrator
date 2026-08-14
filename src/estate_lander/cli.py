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
which waits on a person.

**THERE ARE TWO ACTS, and the second one is new in ADR-0019 Increment 6.** After the landing pass,
the program asks the orchestrator to bring up to date any branch whose ONLY remaining obstacle is
that it is behind its base -- a condition this lane creates itself, because a landing moves the
base and stales every sibling in that repository. Which ones qualify is the orchestrator's answer,
composed from the same terms and composed again inside the transaction that acts; this program
relays it, exactly as it does for the landing. A record can therefore print two lines in one pass,
one per act considered, and the summary counts lines rather than records.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from change_proposer.change_manager import DEFAULT_BASE_URL as CM_DEFAULT_BASE_URL
from change_proposer.change_manager import ChangeManagerClient, ChangeManagerError
from estate_lander.orchestrator_client import (
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

# The ONE status this pass asks about, stated as an allowlist rather than as the set to skip. A
# denylist admits every status nobody has thought of -- `in_progress`, `handed_off`, `failed` --
# and each would be asked about, held, and reported as a finding nobody can act on. The service
# whose records these are warns about exactly this polarity.
_ASK_ABOUT = frozenset({"approved"})

# Refusals that mean the SUBJECT IS SETTLED rather than that a condition is unmet. Neither is
# something a person can act on, and reporting them as findings would make one landing -- or one
# pull request a person merged themselves, which is still the ordinary case -- a nightly page
# forever. The line is still printed; it just is not a finding.
#
# Whether a change record should also LEAVE `approved` once its subject has settled is an open
# lifecycle question, named in this increment's report rather than decided here.
_SETTLED = frozenset({"landing_already_recorded", "landing_pull_request_not_open"})

# Refusals that are the system REFUSING ON PURPOSE: the daily pace for this repository is spent, or
# the clock is outside the hours policy declares for changing something already serving. Neither
# names a condition anybody can act on, and each clears itself when the window next opens.
_DELIBERATE = frozenset({"landing_pace_exhausted", "landing_outside_change_window"})

# Refusals that CURRENT POLICY can never clear. A requirement-range or grouped bump states no single
# delta, so no rule about update types applies to it -- decided in ADR-0018 and deliberately left,
# which is what makes it an exception rather than a defect. It waits on a person, forever, and no
# pass of this program will ever change that.
#
# KEPT SEPARATE FROM `_DELIBERATE` ON PURPOSE, though today they have the same effect on the exit
# code. WHICH ONE a line is IS the information: one will clear tonight and one will not, and a
# single set would say "quiet" about both while losing which is which.
_EXCEPTION = frozenset({"landing_update_type_unparseable"})

# Statuses that are not findings, stated as the set to EXCLUDE so a status nobody has thought of
# fails toward being reported. Same polarity argument as `_ASK_ABOUT`, one column over.
#
# A branch brought up to date is the lane clearing a condition the lane itself caused, which is
# the system working -- so it is printed and it is not a finding. `would-update` likewise: it is
# what a dry run has to say in order to be worth running.
_NOT_A_FINDING = frozenset(
    {"landed", "would-land", "settled", "deliberate", "exception", "updated", "would-update"}
)

# Every status a pass can produce, in report order, so the summary's counts sum to what was
# considered. A summary whose parts do not add up leaves the reader to infer the remainder, and the
# remainder is where the findings are -- `unreadable`, `error` and (on a dry run) `would-land` were
# all absent from it before.
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


class RecordSource(Protocol):
    """The one read this pass needs from the change service: which changes were routed."""

    def records(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Outcome:
    repository: str
    number: int
    status: str
    detail: str


def _key(repository: str, number: int, head_sha: str) -> str:
    """CONTENT-ADDRESSED over the subject and the head, so a replay is a replay.

    A random key would make every pass a new request for the same act, which the orchestrator
    would refuse as a spent key belonging to a different subject -- turning an ordinary re-run
    into a finding. Naming the head as well as the pull request means a genuinely new attempt
    after a rebase is a genuinely new key.
    """
    return f"estate-landing:{repository}:{number}:{head_sha[:12]}"


def _update_key(repository: str, number: int, head_sha: str) -> str:
    """Content-addressed over the head, for the reason above and one more that is specific here.

    A successful update CHANGES the head, so the next legitimate update -- after the base moves
    again -- necessarily carries a different key and can never be barred by this one. That is what
    makes an idempotency key safe on an act whose whole nature is that repeating it is right.
    """
    return f"estate-branch-update:{repository}:{number}:{head_sha[:12]}"


def _held_status(refusals: list[str]) -> str:
    """SUBSET, never intersection -- and that is the whole of this function.

    `_SETTLED` above is tested with intersection, correctly: a settled subject's other refusals are
    meaningless because the pull request is gone. **A deliberate refusal says nothing about the
    other conditions.** `landing_pace_exhausted` co-occurs on every held pull request once the day's
    landing is spent, so an intersection rule here would silence a pull request whose checks are
    failing because a deliberate refusal happened to sit beside the real one -- i.e. essentially
    everything, every night after the first landing.

    So a held pull request stops being a finding only when EVERY refusal is one nobody can act on.
    An unclassified code -- present or future -- leaves the line a finding, which is the polarity
    the file argues for elsewhere: a denylist would silence every code nobody has thought of.

    NO refusals at all is a FINDING, not a vacuous pass. An answer that is unsatisfied while naming
    nothing is the orchestrator failing to say why, which is exactly the thing worth reporting; the
    subset test alone would call it deliberate.

    An exception outranks a deliberate refusal when both are present, because the exception is the
    durable fact: the pace resets tonight and the record still cannot land.
    """
    unexplained = set(refusals) - _DELIBERATE - _EXCEPTION
    if unexplained or not refusals:
        return "held"
    return "exception" if _EXCEPTION & set(refusals) else "deliberate"


def _consider(client: OrchestratorClient, repository: str, number: int, submit: bool) -> Outcome:
    try:
        answer = client.admission(repository, number)
    except OrchestratorError as error:
        return Outcome(repository, number, "unreadable", str(error))

    refusals = [str(r) for r in (answer.get("refusals") or [])]
    if _SETTLED & set(refusals):
        return Outcome(repository, number, "settled", ", ".join(refusals))
    if not answer.get("satisfied"):
        return Outcome(repository, number, _held_status(refusals), ", ".join(refusals))

    head = answer.get("head_sha")
    if not isinstance(head, str) or not head:
        # Admissible with no head is unreachable through the orchestrator's own cascade, which
        # refuses an unreadable pull request. Stated rather than assumed, because acting without a
        # head would be asking for whatever has been pushed since.
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


def _subjects(records: RecordSource) -> list[tuple[str, int]]:
    """Every routed change worth asking about, in a stable order.

    Sorted, so a pass that lands one of several is reproducible rather than dependent on whatever
    order the listing happened to answer in -- which matters because the orchestrator permits one
    landing per repository per window, so WHICH one lands is decided here.

    ONE function, used by both passes, so the landing pass and the branch-update pass can never
    disagree about which pull requests this program is for. Each pass calls it for itself rather
    than sharing a snapshot, for the same reason each re-reads the composed answer: the landing
    pass may have changed what the second one is looking at.
    """
    subjects: list[tuple[str, int]] = []
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
        subjects.append((repository, number))
    return subjects


def _pass(records: RecordSource, client: OrchestratorClient, submit: bool) -> list[Outcome]:
    """Ask about every routed change."""
    return [
        _consider(client, repository, number, submit) for repository, number in _subjects(records)
    ]


def _branch_updates(
    records: RecordSource, client: OrchestratorClient, submit: bool
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
    again inside the transaction that acts. A record that does not qualify gets no line, because
    the landing pass has already printed one naming every condition it misses.
    """
    outcomes: list[Outcome] = []
    for repository, number in _subjects(records):
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
            client.update_branch(
                repository,
                number,
                head_sha=head,
                idempotency_key=_update_key(repository, number, head),
            )
        except LandingRefused as error:
            outcomes.append(Outcome(repository, number, "held", str(error)))
        except OrchestratorError as error:
            outcomes.append(Outcome(repository, number, "error", str(error)))
        else:
            outcomes.append(Outcome(repository, number, "updated", f"was behind at {head[:12]}"))
    return outcomes


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
    if not cm_token:
        print("ESTATE_LANDING_CHANGE_MANAGER_TOKEN is unset", file=sys.stderr)
        return EXIT_UNUSABLE
    if not token:
        print("ESTATE_LANDING_ORCHESTRATOR_TOKEN is unset", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        with (
            ChangeManagerClient(cm_token, base_url=cm_url or CM_DEFAULT_BASE_URL) as records,
            OrchestratorClient(token, SYSTEM_KEY_ID, base_url=url or DEFAULT_BASE_URL) as client,
        ):
            outcomes = _pass(records, client, args.submit)
            outcomes.extend(_branch_updates(records, client, args.submit))
    except (ChangeManagerError, OrchestratorError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_TOOL_FAILURE

    return report(outcomes)


def report(outcomes: list[Outcome]) -> int:
    for outcome in outcomes:
        subject = f"{outcome.repository}#{outcome.number}"
        print(f"{subject}  {outcome.status:<11} {outcome.detail}")
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
