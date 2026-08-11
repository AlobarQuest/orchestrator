"""Propose a change record for every deploying merge that is waiting to happen.

ADR-0019 increment 4. **This program proposes and reports. It approves nothing, merges nothing,
and moves no record's status** -- and after this increment's other half it *cannot*, because the
credential it holds is refused those routes by change-manager itself.

WHAT IT IS FOR — and be precise, because a first version of this docstring was WRONG in a way that
would have propagated. It said increment 3's factory-lane admission term was the consumer. It is
not, today: that term reads `UnitPrBinding.pr_number`, i.e. the pull request **factory-runner**
opened, and every one of those is authored by `AlobarQuest` — `FACTORY_PR_TOKEN` is a fine-grained
PAT on a USER account, so GitHub reports `type: "User"` and the bot filter below refuses it.

**This producer serves the DEPENDABOT population** — the pull requests ADR-0019's auto-merge lane
concerns, and the nine currently waiting on `change-manager` and `brain`. Those records are read
today by increment 2's rollout watcher, which observes what a landing actually caused and appends
an observation to the record this creates.

Whether increment 3's term should also be fed — and how, since a Dependabot pull request has no
work unit and therefore no binding — is the LANDING increment's question, not this one's. Do not
read a green pass here as "the factory lane now has records."

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
from deploy_watcher.github import GitHubReader, ReadError
from deploy_watcher.workflows import ROLLOUT_WORKFLOWS, attestation_for


class BlobSource(Protocol):
    """The ONE thing eligibility needs from GitHub: which bytes the rollout workflow is at.

    A protocol rather than `GitHubReader` because that is the honest signature -- `_consider`
    reads one file and nothing else -- and because typing it as the concrete reader would force a
    test to fake an HTTP client to answer a question about a workflow revision.
    """

    def blob_revision(self, repository: str, path: str, ref: str) -> str | None: ...


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


def _proposal(
    repository: str, pull: dict[str, Any], criteria: tuple[str, ...], rollback: Any
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
    """
    return {
        "target_repository": repository,
        "pull_request_number": pull["number"],
        "change_class": "dependency-update",
        "risk": "caution",
        "reasoning": (
            f"landing this pull request on the default branch of {repository} redeploys "
            "production, so it is a deploying merge and carries a change record (ADR-0019)."
        ),
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
    if not pull.get("is_bot"):
        # A human's pull request is a human's to merge, which ADR-0019 puts out of scope by
        # construction. Keyed on the account TYPE, never on a `[bot]` login suffix.
        return None, "human-authored"
    if pull.get("base_ref") != workflow.trigger_branch:
        # Merging into some other base fires no rollout, so there is no deploy to record.
        return None, f"base is {pull.get('base_ref')!r}, not {workflow.trigger_branch!r}"
    revision = reader.blob_revision(repository, workflow.path, workflow.trigger_branch)
    try:
        criteria = acceptance_criteria(repository, attestation_for(revision))
        rollback = rollback_for(repository)
    except CriteriaUnavailable as error:
        return None, f"{REFUSAL_PREFIX}{error}"
    return _proposal(repository, pull, criteria, rollback), "eligible"


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
    print(f"\n{len(outcomes)} considered, {len(proposed)} to propose, {len(findings)} findings")
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
