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
a pull request the orchestrator has already acted on, are not.
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

# The refusal that means the act ALREADY HAPPENED. It is not a condition anybody can act on, so
# reporting it as held would make a successful landing a permanent nightly finding: the record
# stays approved after the landing (nothing transitions a decision on a merge), and every later
# pass would read the same row and the same closed pull request, forever. A pager that never
# clears is a pager nobody reads.
ALREADY_RECORDED = "landing_already_recorded"

# Statuses whose records this pass has nothing to ask about. A pull request nobody approved is not
# held on a condition -- it is waiting for the policy to approve its shape, which happens in the
# producer's pass, not here.
_UNAPPROVED = frozenset({"pending", "deferred", "wontfix", "resolved", "blocked"})


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


def _consider(client: OrchestratorClient, repository: str, number: int, submit: bool) -> Outcome:
    try:
        answer = client.admission(repository, number)
    except OrchestratorError as error:
        return Outcome(repository, number, "unreadable", str(error))

    refusals = [str(r) for r in (answer.get("refusals") or [])]
    if ALREADY_RECORDED in refusals:
        # SETTLED, not held. The orchestrator holds a record of an act against this pull request,
        # which is the one refusal that will never clear and that nobody should be asked to act
        # on. Whether the change record should also leave `approved` once its rollout has been
        # observed is an open lifecycle question, named in the increment's report rather than
        # decided here.
        return Outcome(repository, number, "already-landed", ", ".join(refusals))
    if not answer.get("satisfied"):
        return Outcome(repository, number, "held", ", ".join(refusals))

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


def _pass(records: RecordSource, client: OrchestratorClient, submit: bool) -> list[Outcome]:
    """Ask about every routed change, in a stable order.

    Sorted, so a pass that lands one of several is reproducible rather than dependent on whatever
    order the listing happened to answer in -- which matters because the orchestrator permits one
    landing per repository per window, so WHICH one lands is decided here.
    """
    outcomes: list[Outcome] = []
    rows = sorted(
        (row for row in records.records() if isinstance(row, dict)),
        key=lambda row: (str(row.get("target_repository") or ""), row.get("id") or 0),
    )
    for row in rows:
        repository = row.get("target_repository")
        number = row.get("pull_request_number")
        if not isinstance(repository, str) or not isinstance(number, int):
            continue
        if row.get("status") in _UNAPPROVED:
            continue
        outcomes.append(_consider(client, repository, number, submit))
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
    except (ChangeManagerError, OrchestratorError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_TOOL_FAILURE

    return report(outcomes)


def report(outcomes: list[Outcome]) -> int:
    for outcome in outcomes:
        subject = f"{outcome.repository}#{outcome.number}"
        print(f"{subject}  {outcome.status:<11} {outcome.detail}")
    held = [o for o in outcomes if o.status == "held"]
    landed = [o for o in outcomes if o.status == "landed"]
    findings = [o for o in outcomes if o.status in {"held", "unreadable", "error"}]
    print(f"\n{len(outcomes)} considered, {len(landed)} landed, {len(held)} held")
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
