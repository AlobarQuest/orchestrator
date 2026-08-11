"""Propose a change record for every deploying merge that is waiting to happen.

ADR-0019 increment 4. **This program proposes and reports. It approves nothing, merges nothing,
and moves no record's status** -- and after this increment's other half it *cannot*, because the
credential it holds is refused those routes by change-manager itself.

WHAT IT IS FOR. Increment 3 taught the factory lane to require an approved change record before
landing a pull request into a repository where landing redeploys. Nothing created those records,
so `change_record_absent` was the only answer the term could ever give. This is the producer.

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


EXIT_OK = 0
EXIT_FINDINGS = 3
EXIT_UNUSABLE = 2


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
    return {
        "target_repository": repository,
        "pull_request_number": pull["number"],
        "change_class": "dependency-update",
        "risk": "caution",
        "reasoning": (
            f"{pull['title']} -- landing this on the default branch of {repository} redeploys "
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
        return None, f"REFUSED: {error}"
    return _proposal(repository, pull, criteria, rollback), "eligible"


def _pass(
    reader: GitHubReader,
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
            outcomes.append(_consider_one(reader, repository, pull, client))
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
        return Outcome(repository, number, "skipped", why)
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
    findings = [o for o in outcomes if o.status in {"refused", "error", "unreadable"}]
    proposed = [o for o in outcomes if o.status in {"proposed", "would-propose"}]
    print(f"\n{len(outcomes)} considered, {len(proposed)} to propose, {len(findings)} findings")
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
