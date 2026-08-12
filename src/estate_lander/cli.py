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
    if _SETTLED & set(refusals):
        return Outcome(repository, number, "settled", ", ".join(refusals))
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
        if row.get("status") not in _ASK_ABOUT:
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
    settled = [o for o in outcomes if o.status == "settled"]
    findings = [o for o in outcomes if o.status in {"held", "unreadable", "error"}]
    print(
        f"\n{len(outcomes)} considered, {len(landed)} landed, "
        f"{len(held)} held, {len(settled)} settled"
    )
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
