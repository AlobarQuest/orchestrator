"""Propose a change record for every deploying merge that is waiting to happen.

ADR-0019 increment 4. **This program proposes and reports. It approves nothing, merges nothing,
and moves no record's status** -- and after this increment's other half it *cannot*, because the
credential it holds is refused those routes by change-manager itself.

WHAT IT IS FOR. Two populations, and they were one until 2026-08-16.

**The DEPENDABOT population** — the pull requests ADR-0019's auto-merge lane concerns. Those
records are read by increment 2's rollout watcher, which observes what a landing actually caused
and appends an observation to the record this creates, and by the estate lander, which asks about
the APPROVED ones.

**The FACTORY population.** `FACTORY_PR_TOKEN` is a fine-grained PAT on a USER account, so GitHub
reports `type: "User"` for every pull request factory-runner opens and the bot filter below
refused all of them — which left the ADR-0020 factory lane into a redeploying repository dying at
`change_record_absent`, and ADR-0022's unit-scoped observation with no subject to watch. A factory
pull request is a THIRD case beside a human's and an update bot's, recognised by the marking
factory-runner stamps on its own title (`change_proposer.factory_marking`). Nothing about the
filter is loosened.

A factory record is proposed with a change class that is deliberately OUTSIDE change-manager's
deploy policy, so it stays `pending`: whether a machine-written change may land unattended is a
decision for a person, and this program does not take it by choosing a conforming shape. The
rollout watcher reads every deploy record whatever its status, so ADR-0022's observation is
reached without waiting on that decision; the ADR-0020 landing lane refuses at
`change_record_not_approved` until it is taken.

WHY IT PROPOSES RATHER THAN AUTHORS. A rule requiring three hand-written fields is incompatible
with anything unattended, so the acceptance criteria are read from the transcribed statement of
what each rollout workflow actually attests, and the rollback plan from a transcribed per-repository
plan. Both fail closed: bytes nobody classified, or a repository nobody wrote a plan for, produce
a refusal and a finding rather than a guessed record.

**SCOPE IS THE TRANSCRIBED SET, and that is a deliberate choice worth stating.** A repository is in
scope when somebody has transcribed both its rollout workflow and its rollback plan. The estate's
authority on *does landing redeploy* is App Brain, and the orchestrator's admission term reads it
there -- but a transcription is a stronger, human-made statement than a classification, and asking
App Brain here would put a third credential in this program to re-derive a fact the transcription
already implies. If the two ever disagree, the admission term is the one that gates.

**PROPOSING IS IDEMPOTENT SERVER-SIDE.** change-manager answers 201 for a new record and 200 for an
identical proposal that already exists, and 409 for a different one. So a re-run is a replay, which
is what makes this safe to schedule -- and a 409 is a real finding, because it means somebody
proposed different facts for the same pull request and whoever proposed FIRST fixed them.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from change_proposer.change_manager import (
    DEFAULT_BASE_URL,
    ChangeManagerClient,
    ChangeManagerError,
    ProposalRefused,
)
from change_proposer.criteria import CriteriaUnavailable, acceptance_criteria, rollback_for
from change_proposer.factory_marking import FACTORY_TITLE_PREFIX, factory_unit_id
from deploy_watcher.github import GitHubReader, ReadError
from deploy_watcher.workflows import ROLLOUT_WORKFLOWS, attestation_for


class BlobSource(Protocol):
    """The ONE thing eligibility needs from GitHub: which bytes the rollout workflow is at.

    A protocol rather than `GitHubReader` because that is the honest signature -- `_consider`
    reads one file and nothing else -- and because typing it as the concrete reader would force a
    test to fake an HTTP client to answer a question about a workflow revision.
    """

    def blob_revision(self, repository: str, path: str, ref: str) -> str | None: ...


class DispositionSource(Protocol):
    """Where a pull request ended up. The one read the retirement sweep needs.

    Its own protocol rather than a method on the two above, because the sweep is a different pass
    over a different population -- records already made rather than pull requests waiting -- and a
    signature that named the concrete reader would carry nine methods neither pass calls.
    """

    def pull_request_disposition(self, repository: str, number: int) -> str | None: ...


class RetirementTarget(Protocol):
    """What the sweep needs from change-manager: the records it has made, and one retirement.

    A protocol rather than the concrete client, because that is the honest signature -- and
    because typing it as the client would make every test of the sweep stand up an HTTP transport
    to answer a question about which records exist.
    """

    def records(self) -> list[dict[str, Any]]: ...

    def retire(self, item_id: int, *, pull_request_number: int) -> dict[str, Any]: ...


class PullSource(BlobSource, Protocol):
    """What the whole pass needs from GitHub: the open pull requests, and one file's bytes.

    Two methods, named here, so a test can drive the pass without standing up an HTTP client — and
    so the signature says what is actually used rather than naming a concrete reader that carries
    nine other methods this program never calls.
    """

    def open_pull_requests(self, repository: str) -> list[dict[str, Any]]: ...


EXIT_OK = 0
EXIT_FINDINGS = 3
EXIT_UNUSABLE = 2

# `_consider` cannot honestly derive a record, so it refuses rather than guessing. ONE
# definition, used to write the reason and to recognise it again, because the alternative is
# a magic string in two places and the recogniser silently ceasing to match.
REFUSAL_PREFIX = "REFUSED: "

# What makes a pass a FINDING rather than a clean run.
#
# `underivable` is here and that is the whole point of this constant. It used to be reported
# as an ordinary `skipped` — the same status a draft or a human's pull request gets — and
# `skipped` is not a finding, so an untranscribed rollout workflow produced a pass that exited
# 0 with nothing to look at. That is the silent case: the workflow deciding what a deploy
# PROVES had changed, no record could be derived for any pull request on that repository, and
# the scheduled job reported success. A skip means "this pull request is not our business"; a
# refusal means "it is, and nobody can say what its deploy would attest".
FINDING_STATUSES = frozenset({"refused", "error", "unreadable", "underivable"})

# What kind of change a FACTORY pull request lands, as distinct from an update bot's.
#
# DELIBERATELY OUTSIDE change-manager's deploy policy, which pins `change_classes` to
# `{"dependency-update"}` in every version to date. Two reasons, and the first is the decisive
# one. Reusing `dependency-update` would make each factory record conform on shape and be
# APPROVED by `_apply_policy` the instant it is proposed -- so this program would have decided,
# silently, that a machine-written change may land unattended, which is a decision for a person
# and not for this increment. It would also be untrue: the work unit behind a factory pull
# request may be a dependency update, a maintenance remediation or a software delivery, and
# nothing readable from a pull request title says which. A name that claims none of them is the
# honest one, and it is the name a human would add to a policy version if he decided these
# records should be pre-approved.
FACTORY_CHANGE_CLASS = "factory-delivery"
BOT_CHANGE_CLASS = "dependency-update"

# What the retirement sweep must observe before it retires anything, mirrored from
# `deploy_watcher.github.pull_request_disposition`.
CLOSED_UNMERGED_DISPOSITION = "closed_unmerged"

# Statuses a record can hold that this sweep leaves alone. `resolved` and `wontfix` are terminal --
# a record already retired, or one a human decided against -- and re-asserting either would be the
# machine re-deciding rather than reconciling. Everything else is swept, including `approved`,
# because an approved record for a pull request that can never land is the case this exists for.
_TERMINAL_STATUSES = frozenset({"resolved", "wontfix"})


@dataclass(frozen=True)
class Outcome:
    repository: str
    number: int
    status: str
    detail: str


def _in_scope() -> list[str]:
    """Repositories with BOTH a transcribed rollout workflow and a transcribed rollback plan."""
    scope = []
    for repository in sorted(ROLLOUT_WORKFLOWS):
        try:
            rollback_for(repository)
        except CriteriaUnavailable:
            continue
        scope.append(repository)
    return scope


def _reasoning(repository: str, work_unit_id: str | None) -> str:
    """Why this pull request carries a change record -- and, for a factory one, what opened it.

    **A FROZEN REPLAY CONTRACT.** `reasoning` is one of change-manager's asserted fields, so this
    string is compared against the stored one on every later pass and a reword turns every
    existing record into a permanent 409. It must therefore say only what stays true forever: no
    date, no count, no version, and no title.

    THE WORK UNIT IDENTIFIER LIVES HERE, and the alternative was measured rather than assumed.
    `note` is the only other asserted free-text field, and change-manager's own item page renders
    it under the label *"Auto-fix held:"* -- a drift-lane caption that would misdescribe it in the
    one place a person reads it. Neither field is machine-read for the unit today: the rollout
    watcher takes the unit from the merge commit's `SDS-Unit:` trailer, not from this record, so a
    named slot here would be a slot with no reader. What the record actually needs is for a person
    reading it to see that a work unit is behind it, and `reasoning` is the field they read.
    """
    why = (
        f"landing this pull request on the default branch of {repository} redeploys "
        "production, so it is a deploying merge and carries a change record (ADR-0019)."
    )
    if work_unit_id is None:
        return why
    return (
        f"{why} The factory opened it for work unit {work_unit_id}: the change was produced by a "
        "runner acting under an authority envelope a human approved, rather than by a person or "
        "by an update bot."
    )


def _proposal(
    repository: str,
    pull: dict[str, Any],
    criteria: tuple[str, ...],
    rollback: Any,
    *,
    work_unit_id: str | None,
) -> dict[str, Any]:
    """The record's facts, and EVERY ONE OF THEM MUST STAY TRUE.

    **The pull request's TITLE is deliberately absent, and that is the whole point of this
    docstring.** `propose_deploy_change` compares the proposed fields against the stored ones and
    raises a terminal 409 on any difference — there is no update path and no supersede route. So a
    field that drifts turns every later pass into a permanent refusal for that pull request, and
    freezes the record on the older value forever.

    **Dependabot rewrites a pull request IN PLACE when a newer version appears**, changing its
    title, which makes the title the most volatile string available and the worst possible thing to
    interpolate. A first version of this function did exactly that; measured against a live
    change-manager, a drifted title answered
    `409 … asserting different target_repository, reasoning, acceptance_criteria, rollback_plan`.
    This repository already records the rule under the landing ledger's `facts`: prose in a frozen
    record must say only what stays true — never a title, never a count, never anything dated.

    The acceptance criteria CAN still drift, when the rollout workflow's bytes change, and that
    conflict is left in place on purpose: if what a green rollout attests has changed, the stored
    criteria really are stale, and a refusal is the honest way for a person to find out.

    **The work unit is derived from the title and the title is still absent from the payload.**
    That is not a contradiction: a work unit identifier is fixed for the life of the pull request
    that carries it, where the rest of a title is the most volatile string available. What is
    frozen here is the identifier, never the prose around it.

    THE CRITERIA AND THE ROLLBACK ARE THE SAME FOR BOTH SHAPES, deliberately. They state what a
    green rollout of this REPOSITORY attests and how its production is put back; who opened the
    pull request changes neither.
    """
    return {
        "target_repository": repository,
        "pull_request_number": pull["number"],
        "change_class": BOT_CHANGE_CLASS if work_unit_id is None else FACTORY_CHANGE_CLASS,
        "risk": "caution",
        "reasoning": _reasoning(repository, work_unit_id),
        "acceptance_criteria": list(criteria),
        "rollback_plan": {"steps": list(rollback.steps), "target": rollback.target},
        "actor": "change-proposer",
    }


def _consider(
    reader: BlobSource, repository: str, pull: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    """Build the proposal for one pull request, or say why there is none."""
    workflow = ROLLOUT_WORKFLOWS[repository.lower()]
    if pull.get("draft"):
        return None, "draft"
    work_unit_id = factory_unit_id(pull.get("title"))
    if work_unit_id is None and not pull.get("is_bot"):
        # A human's pull request is a human's to merge, which ADR-0019 puts out of scope by
        # construction. Keyed on the account TYPE, never on a `[bot]` login suffix -- and NOT
        # loosened: what admits a factory pull request is the marking beside this filter, not a
        # weaker reading of it.
        return None, _unmarked_author_reason(pull)
    if pull.get("base_ref") != workflow.trigger_branch:
        # Merging into some other base fires no rollout, so there is no deploy to record.
        return None, f"base is {pull.get('base_ref')!r}, not {workflow.trigger_branch!r}"
    revision = reader.blob_revision(repository, workflow.path, workflow.trigger_branch)
    try:
        criteria = acceptance_criteria(repository, attestation_for(revision))
        rollback = rollback_for(repository)
    except CriteriaUnavailable as error:
        return None, f"{REFUSAL_PREFIX}{error}"
    return _proposal(repository, pull, criteria, rollback, work_unit_id=work_unit_id), "eligible"


def _unmarked_author_reason(pull: dict[str, Any]) -> str:
    """Why a user account's pull request was passed over -- and a breadcrumb when it looks
    like drift.

    A skip, never a finding, in both branches. A person is entitled to title a pull request
    however they like, and turning that into a nightly page would be the permanently-red control
    this estate keeps rebuilding. But a user-authored title that carries the factory PREFIX and
    no readable unit is what one class of format drift looks like from here, so the line says so
    rather than reading as an ordinary human's pull request. It is a breadcrumb beside the pin,
    not a substitute for it: a drift that changed the prefix would not reach this branch at all,
    and that is precisely what the consumer-compatibility check is for.
    """
    if str(pull.get("title") or "").startswith(FACTORY_TITLE_PREFIX):
        return "human-authored (the title carries the factory prefix but names no work unit)"
    return "human-authored"


def _pass(
    reader: PullSource,
    scope: list[str],
    client: ChangeManagerClient | None,
) -> list[Outcome]:
    """Consider every open pull request in scope, and say what happened to each.

    `client is None` IS the dry run: there is one loop, so the reported reasoning is the reasoning
    a submitting pass would act on, rather than a second code path that could disagree with it.
    """
    outcomes: list[Outcome] = []
    for repository in scope:
        try:
            pulls = reader.open_pull_requests(repository)
        except ReadError as error:
            outcomes.append(Outcome(repository, 0, "unreadable", str(error)))
            continue
        for pull in sorted(pulls, key=lambda p: p.get("number") or 0):
            try:
                outcomes.append(_consider_one(reader, repository, pull, client))
            except ReadError as error:
                # `blob_revision` is called INSIDE the per-pull path, so a 403 rate-limit, a 502 or
                # a timeout here used to escape `_pass` entirely and kill the scheduled run with a
                # traceback — dropping every repository still to be considered. One unreadable pull
                # request is a finding about that pull request, not the end of the pass.
                outcomes.append(
                    Outcome(repository, pull.get("number") or 0, "unreadable", str(error))
                )
    return outcomes


def _consider_one(
    reader: BlobSource,
    repository: str,
    pull: dict[str, Any],
    client: ChangeManagerClient | None,
) -> Outcome:
    number = pull.get("number") or 0
    proposal, why = _consider(reader, repository, pull)
    if proposal is None:
        underivable = why.startswith(REFUSAL_PREFIX)
        return Outcome(repository, number, "underivable" if underivable else "skipped", why)
    if client is None:
        return Outcome(repository, number, "would-propose", proposal["reasoning"])
    try:
        record, created = client.propose(proposal)
    except ProposalRefused as error:
        return Outcome(repository, number, "refused", str(error))
    except ChangeManagerError as error:
        return Outcome(repository, number, "error", str(error))
    return Outcome(
        repository,
        number,
        "proposed" if created else "replayed",
        f"item {record.get('id')} status={record.get('status')}",
    )


def _retire_pass(
    reader: DispositionSource,
    scope: list[str],
    client: RetirementTarget | None,
) -> list[Outcome]:
    """Retire every record whose pull request was closed WITHOUT merging.

    THE PRODUCER IS ALSO A RECONCILER, and this is the half that was missing. The pass above
    enumerates pull requests waiting to happen; this one enumerates records already made and
    retires the ones whose subject is gone. Production item 44 is the case that forced it: a
    record for a pull request closed and superseded, standing approved, authorising a landing that
    can never occur, with nothing in the estate positioned to notice.

    **RETIRE ON A FACT, NEVER ON ABSENCE.** "GitHub says this pull request is closed and was not
    merged" is a fact; "I could not read it", "GitHub has no such pull request" and "the record
    names no pull request" are not, and each is reported rather than acted on. That distinction is
    the whole safety of the sweep, because a retirement is a status change to somebody else's
    record.

    Scoped to the same repositories the proposing pass considers. A record for a repository this
    program does not transcribe is one it has no business deciding anything about.
    """
    if client is None:
        # A dry run reads change-manager for nothing else, and reading the records would need the
        # credential a dry run must not touch. Reported as a skip so a bare invocation says
        # plainly that it examined no records rather than that it found none.
        return [Outcome("-", 0, "skipped", "retirement sweep needs --submit")]
    try:
        records = client.records()
    except ChangeManagerError as error:
        return [Outcome("-", 0, "error", str(error))]

    wanted = set(scope)
    outcomes: list[Outcome] = []
    for record in records:
        repository = record.get("target_repository")
        number = record.get("pull_request_number")
        item_id = record.get("id")
        if not isinstance(repository, str) or repository.lower() not in wanted:
            continue
        if not isinstance(number, int) or isinstance(number, bool) or not isinstance(item_id, int):
            outcomes.append(Outcome(str(repository), 0, "unreadable", "record names no subject"))
            continue
        if record.get("status") in _TERMINAL_STATUSES:
            continue
        outcomes.append(_retire_one(reader, client, repository, number, item_id))
    return outcomes


def _retire_one(
    reader: DispositionSource,
    client: RetirementTarget,
    repository: str,
    number: int,
    item_id: int,
) -> Outcome:
    try:
        disposition = reader.pull_request_disposition(repository, number)
    except ReadError as error:
        return Outcome(repository, number, "unreadable", str(error))
    if disposition is None:
        # A record naming a pull request GitHub does not have. Reported, never retired: the fact
        # this sweep acts on is a closure it observed, and an absent subject is a question about
        # the record rather than an answer about the change.
        return Outcome(repository, number, "unreadable", "github has no such pull request")
    if disposition != CLOSED_UNMERGED_DISPOSITION:
        return Outcome(repository, number, "skipped", disposition)
    try:
        retired = client.retire(item_id, pull_request_number=number)
    except ChangeManagerError as error:
        return Outcome(repository, number, "error", str(error))
    return Outcome(repository, number, "retired", f"item {item_id} status={retired.get('status')}")


def _resolve_scope(requested: list[str] | None) -> list[str] | None:
    """The repositories to consider, or None when one was named that is out of scope."""
    scope = _in_scope()
    if not requested:
        return scope
    wanted = {r.lower() for r in requested}
    unknown = wanted - set(scope)
    if unknown:
        print(f"out of scope (no transcribed workflow or plan): {sorted(unknown)}")
        return None
    return [r for r in scope if r in wanted]


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submit",
        action="store_true",
        help="actually propose. Without it the pass is a dry run and writes nothing.",
    )
    parser.add_argument(
        "--repository", action="append", help="limit to one repository (repeatable)"
    )
    args = parser.parse_args(argv)

    github_token = os.environ.get("CHANGE_PROPOSER_GITHUB_TOKEN", "")
    cm_token = os.environ.get("CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN", "")
    cm_url = os.environ.get("CHANGE_PROPOSER_CHANGE_MANAGER_URL", "")
    if not github_token:
        print("CHANGE_PROPOSER_GITHUB_TOKEN is unset", file=sys.stderr)
        return EXIT_UNUSABLE
    if args.submit and not cm_token:
        print("CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN is unset; --submit needs it", file=sys.stderr)
        return EXIT_UNUSABLE

    scope = _resolve_scope(args.repository)
    if scope is None:
        return EXIT_UNUSABLE

    client = None
    try:
        if args.submit:
            client = ChangeManagerClient(cm_token, base_url=cm_url or DEFAULT_BASE_URL)
        with GitHubReader(github_token) as reader:
            outcomes = _pass(reader, scope, client)
            outcomes.extend(_retire_pass(reader, scope, client))
    except ChangeManagerError as error:
        print(str(error), file=sys.stderr)
        return EXIT_UNUSABLE
    finally:
        if client is not None:
            client.close()

    for outcome in outcomes:
        subject = f"{outcome.repository}#{outcome.number or '-'}"
        print(f"{subject}  {outcome.status:<13} {outcome.detail}")
    findings = [o for o in outcomes if o.status in FINDING_STATUSES]
    proposed = [o for o in outcomes if o.status in {"proposed", "would-propose"}]
    retired = [o for o in outcomes if o.status == "retired"]
    print(
        f"\n{len(outcomes)} considered, {len(proposed)} to propose, "
        f"{len(retired)} retired, {len(findings)} findings"
    )
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
