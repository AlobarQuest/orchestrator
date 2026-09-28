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

**THE BODY IS SHARED, THE PROGRAM IS NOT** (Tier 3 item 27, Devon 2026-09-28). Asking, classifying,
acting and reporting live in `lander.core`, parameterised by `LANE` below; this module keeps what
is this lane's alone -- its enumeration from change records, its credentials, its client, and the
refusal sets `LANE` names. It stays a separate program from `inert_lander` (ADR-0038).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from change_proposer.change_manager import DEFAULT_BASE_URL as CM_DEFAULT_BASE_URL
from change_proposer.change_manager import ChangeManagerClient, ChangeManagerError
from change_proposer.cli import BOT_CHANGE_CLASS
from estate_lander.orchestrator_client import (
    DEFAULT_BASE_URL,
    OrchestratorClient,
    OrchestratorError,
)
from lander import core
from lander.core import EXIT_TOOL_FAILURE, EXIT_UNUSABLE, LandingClient, Lane, Outcome

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
_LANE_CLASSES = frozenset({BOT_CHANGE_CLASS})

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
    exception=frozenset({"landing_update_type_unparseable"}),
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
    return Selection(subjects=subjects, deferred=deferred)


def _pass(subjects: list[tuple[str, int]], client: LandingClient, submit: bool) -> list[Outcome]:
    """Ask about every routed change."""
    return core.landing_pass(LANE, subjects, client, submit)


def _branch_updates(
    subjects: list[tuple[str, int]], client: LandingClient, submit: bool
) -> list[Outcome]:
    """Bring up to date the branches whose only obstacle is being behind; see `core`."""
    return core.branch_updates(LANE, subjects, client, submit)


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
            # READ ONCE, used by both passes. Reading again between them would put a second
            # network call inside the `try`, where its failure discards `outcomes` entirely and
            # returns a bare tool error -- losing the report of a landing that already happened.
            selection = _subjects(records)
            outcomes = _pass(selection.subjects, client, args.submit)
            outcomes.extend(_branch_updates(selection.subjects, client, args.submit))
    except (ChangeManagerError, OrchestratorError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_TOOL_FAILURE

    return report(outcomes, selection.deferred)


def report(outcomes: list[Outcome], deferred: dict[str, int] | None = None) -> int:
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
    return core.report(outcomes, [], deferral_lines)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
