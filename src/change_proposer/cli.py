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

**The SDS's own REVERTS (Landing Part 3b).** When the orchestrator judges a bump it landed into a
deploying repository bad, it opens the revert through its Dispatch App. That pull request is filed
as `dependency-revert`, with the revert's own remedy, and only when the orchestrator recorded
opening exactly it (`GET /api/v1/opened-reverts`, read as the observer). A pull request the
Dispatch App opened is NEVER filed as `dependency-update`: deploy policy would approve it under the
bump's terms. One on a revert branch that no record names is a finding (`unrecorded`).

WHY IT PROPOSES RATHER THAN AUTHORS. A rule requiring three hand-written fields is incompatible
with anything unattended, so the acceptance criteria are read from the transcribed statement of
what each rollout workflow actually attests, and the rollback plan from a transcribed per-repository
plan. Both fail closed: bytes nobody classified, or a repository nobody wrote a plan for, produce
a refusal and a finding rather than a guessed record.

**SCOPE IS THE MEMBER RECORD'S `rollouts` GRANT, NARROWED TO THE TRANSCRIBED SET.** A repository
is in scope when the SDS member record grants it `rollouts` AND somebody has transcribed both its
rollout workflow and its rollback plan. The grant is the authority on which repositories this lane
works; the transcription is what makes a granted repository derivable at all, since a proposal's
criteria and rollback plan are read from it and a guess is refused. A grant with no transcription
is out of scope here, and `_scope_sweep` reports a transcription that has gone stale. The record
is read once at the start of a pass; an unreadable record stops the pass (exit 2), because no
grants to read is not an empty population. The estate's authority on *does landing redeploy* is
App Brain, and the orchestrator's admission term reads it there; if the two ever disagree, the
admission term is the one that gates.

**AND IT IS A SET THAT CAN SHRINK WITHOUT ANYONE SAYING SO**, which is why every pass now begins
by asking whether it still holds. The transcription keys on the BYTES of another repository's
rollout workflow, so a merge over there expires it and this program cannot see that merge happen.
`_scope_sweep` reports any rollout workflow whose current revision is no longer transcribed, or
that could not be read at all, and either answer is an INPUT failure rather than a finding: a pass
that has lost a repository from its own scope reports `0 findings` truthfully while covering less
than it claims to. The refusal in `_consider` is the same fact seen per pull request, and it fires
only when there is a pull request to refuse -- which is the wrong clock for a failure caused by
somebody else's merge.

**PROPOSING IS IDEMPOTENT SERVER-SIDE.** change-manager answers 201 for a new record and 200 for an
identical proposal that already exists, and 409 for a different one. So a re-run is a replay, which
is what makes this safe to schedule -- and a 409 is a real finding, because it means somebody
proposed different facts for the same pull request and whoever proposed FIRST fixed them.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from change_proposer.change_manager import (
    DEFAULT_BASE_URL,
    ChangeManagerClient,
    ChangeManagerError,
    ProposalRefused,
)
from change_proposer.criteria import (
    REVERT_CHANGE_CLASS,
    CriteriaUnavailable,
    acceptance_criteria,
    rollback_for,
)
from change_proposer.factory_marking import FACTORY_TITLE_PREFIX, factory_unit_id
from change_proposer.reverts import (
    DISPATCH_APP_LOGIN,
    REVERT_BRANCH_PREFIX,
    RecordedRevert,
    RevertsUnreadable,
    read_recorded_reverts_from_environment,
)
from deploy_watcher.github import GitHubReader, ReadError
from deploy_watcher.transcription_currency import Row, audit
from deploy_watcher.workflows import REGISTRY, ROLLOUT_WORKFLOWS, attestation_for
from sds_members.client import Member, MembersUnreadable, read_members_from_environment
from sds_members.populations import granted as granted_repositories


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
# Landing Part 3b, the same device for two more answers about a Dispatch App pull request: the
# recorded reverts could not be read, or no record names this revert-branch pull request.
UNREADABLE_PREFIX = "UNREADABLE: "
UNRECORDED_PREFIX = "UNRECORDED: "
_STATUS_BY_PREFIX = (
    (REFUSAL_PREFIX, "underivable"),
    (UNREADABLE_PREFIX, "unreadable"),
    (UNRECORDED_PREFIX, "unrecorded"),
)

# EXACTLY the keys `_proposal` emits, declared rather than left implicit in a dict literal.
#
# IT EXISTS SO THE CROSS-REPO FIELD CHECK HAS BOTH SIDES TO COMPARE. That check vets both of
# change-manager's proposal schemas, so a declaration for the work lane alone would leave half of
# it with nothing on this side to hold the other to. Retyping the names into the check script
# instead would be a second copy of this vocabulary, which is the defect this repository has now
# re-learned in three other vocabularies.
#
# THIS LANE IS DELIBERATELY NOT MADE TO CONFORM to the signal→work contract in this increment:
# `originating_observation_id` is absent because this producer posts no observation, and the
# contract's own document names that non-conformance rather than leaving it to be discovered. The
# check will see it from the day it ships, which is the point of writing this declaration now.
PROPOSAL_FIELDS = (
    "target_repository",
    "pull_request_number",
    "change_class",
    "risk",
    "reasoning",
    "acceptance_criteria",
    "rollback_plan",
    "actor",
)

# What makes a pass a FINDING rather than a clean run.
#
# `underivable` is here and that is the whole point of this constant. It used to be reported
# as an ordinary `skipped` — the same status a draft or a human's pull request gets — and
# `skipped` is not a finding, so an untranscribed rollout workflow produced a pass that exited
# 0 with nothing to look at. That is the silent case: the workflow deciding what a deploy
# PROVES had changed, no record could be derived for any pull request on that repository, and
# the scheduled job reported success. A skip means "this pull request is not our business"; a
# refusal means "it is, and nobody can say what its deploy would attest".
#
# `unrecorded` (Landing Part 3b) is a pull request the SDS's Dispatch App opened on a revert branch
# that no revert record names: the SDS's App doing something the SDS did not record. Nothing files
# it, and nothing would ever land it, so a person must hear of it.
FINDING_STATUSES = frozenset({"refused", "error", "unreadable", "underivable", "unrecorded"})

# What kind of change a FACTORY pull request lands, as distinct from an update bot's.
#
# A NAME OF ITS OWN, which is what makes it possible to decide the two classes separately. The
# work unit behind a factory pull request may be a dependency update, a maintenance remediation
# or a software delivery, and nothing readable from a pull request title says which -- so a name
# claiming none of them is the honest one. Reusing `dependency-update` would additionally have
# meant this program deciding, silently and by choice of string, whether a machine-written change
# may land unattended. That is a decision for a person, and a person has since made it.
#
# ADMITTED BY DEPLOY POLICY VERSION 4 SINCE 2026-08-25, so a record carrying this class for a
# repository that policy names is APPROVED at proposal, with no per-record human approval.
# ADR-0025, decided by Devon 2026-08-17. This comment used to end by calling that "the name a
# human would add to a policy version if he decided these records should be pre-approved" -- it
# was written 2026-08-16, one day before he decided exactly that, and it read as an open question
# for the eight days the mechanism went unbuilt.
#
# WHAT THAT DOES NOT MEAN, because approval here is the weakest thing the estate grants: policy
# approval is NO OBJECTION, never GO AHEAD. A conformant record attests that a human pinned this
# repository, these acceptance criteria and this remedy, and nothing about the change -- which is
# why the per-record approval was rejected rather than merely skipped. change-manager has no
# GitHub egress, so a human approving a record there could not be shown what changed. The
# judgment that reads a machine-authored diff is the human-judgment acceptance criterion on the
# intent package, which disqualifies its unit from landing unattended -- except for a standing
# dependency-update package, which carries none (ADR-0025 amendment 2). A package with the
# `live_estate` reach keeps two human gates: the decomposition approval and `pr_merge_admission`'s
# authority term, an approval bound to the unit's exact fingerprint. One with the source-repository
# reach has neither (ADR-0006 amendment 2, ADR-0025 amendment 4).
FACTORY_CHANGE_CLASS = "factory-delivery"
BOT_CHANGE_CLASS = "dependency-update"
# Landing Part 3b. The SDS's own revert of a bump it judged bad, opened through its Dispatch App.
# Deploy policy 14 approves it on its own terms (its own remedy, no pace, no window, the Dispatch
# App as author). A Dispatch App pull request is NEVER filed as `BOT_CHANGE_CLASS`: the policy
# would approve it under the bump's terms. `REVERT_CHANGE_CLASS` itself is defined in `criteria`,
# beside the remedy that class carries, and imported here.

# No recorded revert: the default for a caller that does not ask, which files nothing as a revert.
NO_REVERTS: frozenset[RecordedRevert] = frozenset()

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


def _in_scope(members: list[Member]) -> list[str]:
    """Repositories granted `rollouts` that ALSO have a transcribed rollout workflow and rollback.

    The grant is the authority; the transcription is what makes a granted repository derivable.
    A grant with nothing transcribed is not proposable, and `_scope_sweep` audits the
    transcriptions whoever is granted.
    """
    granted = {name.lower() for name in granted_repositories(members, "rollouts")}
    scope = []
    for repository in sorted(ROLLOUT_WORKFLOWS):
        if repository.lower() not in granted:
            continue
        try:
            rollback_for(repository)
        except CriteriaUnavailable:
            continue
        scope.append(repository)
    return scope


def _blob_read(reader: BlobSource) -> Callable[[str, str, str], str]:
    """`blob_revision` as the sweep's reader needs it: a revision, or a raise.

    `GitHubReader.blob_revision` answers `None` for a 404, for a directory and for anything whose
    `sha` is not one -- three ways of saying "there is no file there". Handed to the audit
    unchanged, `None` is not in the registry either, so all three would be reported as a rollout
    workflow whose bytes MOVED, when what happened is that nobody could find the file. The exit
    code would be the same and the sentence would be wrong, and the sentence is the whole value of
    a report nobody reads until it is red.
    """

    def read(repository: str, path: str, ref: str) -> str:
        revision = reader.blob_revision(repository, path, ref)
        if revision is None:
            raise ReadError(f"github answers no file at {path} for {repository}@{ref}")
        return revision

    return read


def _scope_sweep(reader: BlobSource) -> list[Row]:
    """Ask, up front, whether this lane still covers what it says it covers.

    SCOPE IS THE TRANSCRIBED SET -- the module docstring says so, and that makes the set a thing
    that can quietly shrink. `REGISTRY` is keyed by the BYTES of another repository's rollout
    workflow, so a merge over there expires the transcription, and nothing here can see that merge
    happen. The refusal in `_consider` catches it per PULL REQUEST, which means it fires only when
    something arrives to be refused: `brain#62` moved the bytes on 2026-09-08, no new brain pull
    request arrived for 27 hours, and every hourly pass reported `0 findings` truthfully while
    covering one fewer repository than it claimed to. A rule that fires only when a third party
    happens to open a pull request is not a check.

    THE POPULATION IS EVERY TRANSCRIBED ROLLOUT, NOT THE INVOCATION'S `--repository`. The question
    is what the LANE covers, not which subjects this run was asked about, and narrowing it would
    let a targeted run report a coverage the lane does not have. It also keeps this caller's
    population identical to `scripts/check_rollout_transcription_currency.py`'s, which is the
    point of the two of them sharing one predicate rather than one each.

    NO NEW CREDENTIAL. This is the GitHub token the program already holds for the pass below, and
    two more GETs an hour -- the same file, at the same ref, that `_consider` reads for a pull
    request. Reading it twice is not duplication: one read answers a question about a subject, and
    this one answers whether there are subjects to ask about at all.
    """
    return audit(ROLLOUT_WORKFLOWS, REGISTRY, _blob_read(reader))


def _reasoning(repository: str, work_unit_id: str | None, reverted: int | None = None) -> str:
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
    if reverted is not None:
        return (
            f"{why} The SDS opened it through its App to revert pull request #{reverted}, a "
            "dependency update it landed here and judged bad."
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
    reverted: int | None = None,
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
    if reverted is not None:
        change_class = REVERT_CHANGE_CLASS
    elif work_unit_id is not None:
        change_class = FACTORY_CHANGE_CLASS
    else:
        change_class = BOT_CHANGE_CLASS
    return {
        "target_repository": repository,
        "pull_request_number": pull["number"],
        "change_class": change_class,
        "risk": "caution",
        "reasoning": _reasoning(repository, work_unit_id, reverted),
        "acceptance_criteria": list(criteria),
        "rollback_plan": {"steps": list(rollback.steps), "target": rollback.target},
        "actor": "change-proposer",
    }


def _consider(
    reader: BlobSource,
    repository: str,
    pull: dict[str, Any],
    reverts: frozenset[RecordedRevert] | None = NO_REVERTS,
) -> tuple[dict[str, Any] | None, str]:
    """Build the proposal for one pull request, or say why there is none.

    `reverts` is what the orchestrator recorded opening, or None when it could not be read.
    """
    workflow = ROLLOUT_WORKFLOWS[repository.lower()]
    if pull.get("draft"):
        return None, "draft"
    reverted: int | None = None
    work_unit_id: str | None = None
    if pull.get("author") == DISPATCH_APP_LOGIN:
        # FIRST, before the factory marking and the bot filter: whatever else is true of it, a
        # pull request the Dispatch App opened is filed as the SDS's revert or not at all.
        reverted, why = _recorded_revert(repository, pull, reverts)
        if reverted is None:
            return None, why
    elif str(pull.get("head_ref") or "").startswith(REVERT_BRANCH_PREFIX):
        # Whoever else opened it -- a renamed or differently-cased App login included -- a revert
        # branch is never filed as a bump, which policy would approve under the bump's terms.
        return None, (
            f"{UNRECORDED_PREFIX}{pull.get('author')!r} opened a revert branch, and only the "
            "SDS's App files one"
        )
    else:
        work_unit_id = factory_unit_id(pull.get("title"))
    if reverted is None and work_unit_id is None and not pull.get("is_bot"):
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
        rollback = rollback_for(repository, None if reverted is None else REVERT_CHANGE_CLASS)
    except CriteriaUnavailable as error:
        return None, f"{REFUSAL_PREFIX}{error}"
    proposal = _proposal(
        repository, pull, criteria, rollback, work_unit_id=work_unit_id, reverted=reverted
    )
    return proposal, "eligible"


def _recorded_revert(
    repository: str, pull: dict[str, Any], reverts: frozenset[RecordedRevert] | None
) -> tuple[int | None, str]:
    """The bump this Dispatch App pull request reverts, or why it is filed as nothing.

    It is a revert only if it is typed a bot, sits on a revert branch, and a record the
    orchestrator wrote when it opened the revert names exactly this pull request on exactly that
    branch. Off a revert branch it is a skip -- the App opens other pull requests, none of them a
    deploying merge this lane files. On one, with no record naming it, it is a finding.
    """
    head = pull.get("head_ref")
    if not pull.get("is_bot") or not isinstance(head, str):
        return None, "opened by the SDS's App but not as a revert"
    if not head.startswith(REVERT_BRANCH_PREFIX):
        return None, f"opened by the SDS's App on {head!r}, which is not a revert branch"
    if reverts is None:
        return None, f"{UNREADABLE_PREFIX}the reverts the orchestrator recorded could not be read"
    for revert in reverts:
        if revert.names(repository, pull):
            return revert.pr_number, "revert"
    return None, f"{UNRECORDED_PREFIX}no revert the orchestrator recorded names {head!r}"


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
    reverts: frozenset[RecordedRevert] | None = NO_REVERTS,
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
                outcomes.append(_consider_one(reader, repository, pull, client, reverts))
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
    reverts: frozenset[RecordedRevert] | None = NO_REVERTS,
) -> Outcome:
    number = pull.get("number") or 0
    proposal, why = _consider(reader, repository, pull, reverts)
    if proposal is None:
        status = next((s for prefix, s in _STATUS_BY_PREFIX if why.startswith(prefix)), "skipped")
        return Outcome(repository, number, status, why)
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


def _resolve_scope(requested: list[str] | None, members: list[Member]) -> list[str] | None:
    """The repositories to consider, or None when one was named that is out of scope."""
    scope = _in_scope(members)
    if not requested:
        return scope
    wanted = {r.lower() for r in requested}
    unknown = wanted - set(scope)
    if unknown:
        print(
            "out of scope (needs a `rollouts` grant in the member record, a transcribed "
            f"rollout workflow and a transcribed rollback plan): {sorted(unknown)}"
        )
        return None
    return [r for r in scope if r in wanted]


def _read_members() -> list[Member] | None:
    """The member record, read once before anything is judged or written, or None when unreadable.

    An unreadable record is unusable input, never a smaller population: with no grants to read,
    every repository would look ungranted.
    """
    try:
        return read_members_from_environment()
    except MembersUnreadable as error:
        print(
            f"[UNUSABLE] the member record could not be read, so nothing is proposed: {error}",
            file=sys.stderr,
        )
        return None


def _read_reverts() -> tuple[frozenset[RecordedRevert] | None, list[Outcome]]:
    """The reverts the orchestrator recorded, read once per pass, or None and a finding.

    A finding on EVERY pass that cannot read them, whether or not a Dispatch App pull request is
    open: a read that broke must be heard before the pass that needs it, not at it.
    """
    try:
        return read_recorded_reverts_from_environment(), []
    except RevertsUnreadable as error:
        return None, [Outcome("-", 0, "unreadable", f"recorded reverts: {error}")]


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

    members = _read_members()
    scope = None if members is None else _resolve_scope(args.repository, members)
    if scope is None:
        return EXIT_UNUSABLE

    reverts, unread = _read_reverts()

    client = None
    try:
        if args.submit:
            client = ChangeManagerClient(cm_token, base_url=cm_url or DEFAULT_BASE_URL)
        with GitHubReader(github_token) as reader:
            # Kept OUT of `outcomes`, deliberately. Those rows are subjects this pass considered
            # and these are repositories it may no longer be able to consider anything in, so
            # folding them together would put a statement about the lane into a count of pull
            # requests -- and, since the two carry different exit codes, would need pulling apart
            # again immediately.
            scope_rows = _scope_sweep(reader)
            outcomes = unread + _pass(reader, scope, client, reverts)
            outcomes.extend(_retire_pass(reader, scope, client))
    except ChangeManagerError as error:
        print(str(error), file=sys.stderr)
        return EXIT_UNUSABLE
    finally:
        if client is not None:
            client.close()

    for row in scope_rows:
        seen = row.revision or "unread"
        print(
            f"scope  {row.repository} {row.path}@{row.ref} -> {seen} :: "
            f"{row.problem or 'transcribed'}"
        )
    for outcome in outcomes:
        subject = f"{outcome.repository}#{outcome.number or '-'}"
        print(f"{subject}  {outcome.status:<13} {outcome.detail}")
    findings = [o for o in outcomes if o.status in FINDING_STATUSES]
    proposed = [o for o in outcomes if o.status in {"proposed", "would-propose"}]
    retired = [o for o in outcomes if o.status == "retired"]
    lost = [row for row in scope_rows if row.problem is not None]
    print(
        f"\n{len(outcomes)} considered, {len(proposed)} to propose, "
        f"{len(retired)} retired, {len(findings)} findings, "
        f"{len(lost)} of {len(scope_rows)} rollout transcriptions unusable"
    )
    if lost:
        # 2 OUTRANKS 3, and the sibling lane states the same rule in the opposite direction: a
        # pass that could not use some of its inputs cannot claim it found everything there was
        # to find. `EXIT_UNUSABLE` used to be reachable only from an early return, so it never
        # competed with a finding; the sweep can hold both at once, so the order is written down
        # here rather than left to whichever branch a later refactor happens to put first.
        #
        # WHY 2 AND NOT 3, since something WAS found. Scope is the transcribed set, so a
        # repository falling out of it is this program losing an input rather than observing a
        # fact about a subject -- true whether or not the pass found anything, and true when
        # there is not a single pull request anywhere to find anything about. The launcher, an
        # outcome alarm, pings `/fail` on both codes (ADR-0021 amendment 2), so the code is what
        # tells a reader a finding about a subject from a lane that no longer knows what it
        # covers.
        #
        # AN UNREADABLE READ IS HERE TOO, and it differs from `_pass`'s `unreadable`, which is a
        # finding at 3. That one is a subject this pass could not consider; this one is the lane
        # unable to say what its subjects are. `check_rollout_transcription_currency.py` makes the
        # same call for the same reason -- nothing that was not compared may be reported as
        # agreeing. The cost is named rather than hidden: a transient GitHub failure will ping
        # `/fail` where it used to be silent, and a lane that goes quiet exactly when it cannot
        # tell what it covers is the failure this sweep exists to end.
        return EXIT_UNUSABLE
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
