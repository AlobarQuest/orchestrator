"""The orchestrator lands a pull request into a repository where landing changes something
already serving. ADR-0019 Increment 5b.

**This is the second place in this repository that changes a repository nobody asked it to change
at the moment it acts, and it is the more consequential of the two** -- the other lands into
repositories the estate calls inert, where the landed commit sits until something separately acts
on it. Here the landing IS the change to a running service.

The shape is shared with that sibling, and its steps live once in `lane_act`: an admission
cascade of named refusals re-evaluated here, a record row with a unique constraint so a repeat is
detectable, an injected client so the whole path runs with no network, and credentials resolved
once so the gate can never attest to credentials the actor does not use.

## Why the orchestrator, rather than the platform's own arming

Measured: an automatic landing armed with the workflow-scoped token fires no `on: push` workflow,
so it would land WITHOUT the rollout ever running -- the default branch and the running service
diverging silently, and the per-commit image a rollback plan names never built. The App's own
landings do fire them. So the act belongs to a party that performs it directly.

## What it writes into the landing commit, and why

The squash body carries two trailers naming the change record and the pinned policy version. The
estate's ledger observes landings independently and classifies each by the permission it can find;
without something in the artifact itself, a landing by this path is indistinguishable from a
machine landing with no accountable basis at all, which is a class the ledger records and no
detector reads.

Passing an explicit body replaces the one the repository setting would have composed. That is
accepted rather than overlooked: the bot's own dependency metadata stays on the pull request and
its branch commits, and the ledger already falls back to the head commit for exactly that reason.

## Idempotency: check, act, reconcile, record

A landing is not idempotent and its failure is asymmetric -- a lost success answers the same way a
refusal does. So the row is read before the call, the call is made, a refusal is followed by a
confirming read before anything is recorded, and only then is the outcome written.

Act-then-record, because the two failure modes are not equal. A record written before a call that
then fails is a lie. A landing whose record is lost is recoverable: the ledger observes it
independently and would report it as basis-less rather than not at all.

## There IS an off-switch here, and that reverses a recorded ruling on purpose

The sibling path records that it needs none, because it has exactly one human-invoked caller --
and it ends by saying that if a SCHEDULED caller is ever proposed, the decision is void. This is
that caller. The switch defaults to refusing, so a release carrying this code changes nothing
until somebody writes an environment variable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol, TypeGuard
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from orchestrator.clock import Clock
from orchestrator.kernel.states import ActorContext
from orchestrator.persistence.models import EstatePrMerge
from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.landing.change_record import ChangeRecordSource
from orchestrator.services.landing.estate_landing import EstateLandingSource
from orchestrator.services.landing.estate_landing_admission import (
    EstateLandingAdmission,
    estate_landing_admission,
)
from orchestrator.services.landing.interfaces import (
    ESTATE_LANDING_EVENT_PREFIX,
    SQUASH,
    BranchHead,
    EstateGatewayError,
    EstatePullRequest,
    EstateReadGateway,
    HeadCheckRun,
    Job,
    MergeOutcome,
    OpenPullRequest,
    PullRequestCommit,
    Tree,
    TreeChange,
    TreeEntry,
)
from orchestrator.services.landing.lane_act import LandingLane, land_pull_request
from orchestrator.services.landing.revert_landed import record_revert_landed

GITHUB_API_URL: Final = "https://api.github.com"

# List reads (ADR-0045). The page size is the platform's maximum, so the ordinary answer is one
# call; the page bound is a backstop against a listing that never ends, not an expected limit.
_LIST_PAGE_SIZE: Final = 100
_MAX_LIST_PAGES: Final = 20
# The platform's own ceiling on the pull request commits listing, whatever the paging.
_COMMITS_LIST_CAP: Final = 250

# A git tree row's fields, in the order `tree` unpacks them, and the type of a directory row.
_TREE_FIELDS: Final = ("path", "mode", "type", "sha")
_TREE_KIND_DIRECTORY: Final = "tree"

# The trailers the landing writes into the squash body, and the ledger reads back out of it. Named
# here because this is the only writer; the reader pins the same spellings on its own side, and a
# disagreement between the two is a landing recorded with no basis rather than a crash -- which is
# why both sides carry a test naming the literal rather than deriving it from the other.
CHANGE_RECORD_TRAILER: Final = "SDS-Change-Record"
POLICY_VERSION_TRAILER: Final = "SDS-Policy-Version"

# Refusals that leave NO RECORD, because nothing happened and each can be tried again.
ESTATE_MERGE_NOT_ADMISSIBLE: Final = "estate_merge_not_admissible"
ESTATE_MERGE_REFUSED_BY_REMOTE: Final = "estate_merge_refused_by_remote"
ESTATE_MERGE_HEAD_MOVED: Final = "estate_merge_head_moved"

_LANE: Final = LandingLane(
    event_prefix=ESTATE_LANDING_EVENT_PREFIX,
    not_admissible=ESTATE_MERGE_NOT_ADMISSIBLE,
    head_moved=ESTATE_MERGE_HEAD_MOVED,
    refused_by_remote=ESTATE_MERGE_REFUSED_BY_REMOTE,
)


class EstatePullRequestGateway(EstateReadGateway, Protocol):
    """Everything the admission cascade reads, plus the one call that changes anything."""

    def submit_merge(
        self,
        *,
        repository: str,
        number: int,
        head_sha: str,
        commit_message: str,
        merge_method: str,
    ) -> MergeOutcome: ...


@dataclass(frozen=True)
class EstateMergeCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    # The head the caller read when it read the admission answer. REQUIRED, with no default: a
    # default meaning "skip the check" is a precondition that holds by the good behaviour of the
    # one caller that exists. The subject is a pull request in a foreign system and has no version
    # of ours; its head is what moves, and naming it is the same claim every other mutation makes
    # with `expected_version`.
    expected_head_sha: str


def land_estate_pull_request(
    session: Session,
    command: EstateMergeCommand,
    gateway: EstatePullRequestGateway,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    *,
    enabled: bool,
    credentials_configured: bool,
    clock: Clock | None = None,
) -> EstatePrMerge:
    """Land the pull request if this lane's answer admits it. The steps are `lane_act`'s.

    The answer is read with the caller's clock, so the change window is judged on the acting path
    exactly as on the read surface; the row names the change record the answer was admitted on.
    """
    return land_pull_request(
        session,
        command,
        _LANE,
        gateway,
        admit=lambda repository: estate_landing_admission(
            session,
            repository,
            command.pr_number,
            landing_source,
            record_source,
            gateway,
            enabled=enabled,
            credentials_configured=credentials_configured,
            clock=clock,
        ),
        remote_call=lambda admission, head_sha: _act(gateway, admission, head_sha),
        change_record_id=lambda admission: admission.change_record_id,
        # Landing Part 3b: a revert the SDS opened lands here too, and is recorded and held as
        # the inert lane's are.
        on_landed=lambda record: record_revert_landed(session, record, command.actor),
    )


def _act(
    gateway: EstatePullRequestGateway, admission: EstateLandingAdmission, head_sha: str
) -> MergeOutcome:
    """The remote call itself. What happens around it -- the reconciling re-read and the record --
    is `lane_act`'s."""
    return gateway.submit_merge(
        repository=admission.repository,
        number=admission.pr_number,
        head_sha=head_sha,
        commit_message=_trailers(admission),
        # STATED, never defaulted. Every subject of this lane is an update bot's branch against
        # a repository whose commits no other lineage is merged from, so discarding the
        # branch's own commits costs nothing and is what the estate's history reads as. The
        # sibling lane decides per pull request because its population is not uniform; this one
        # is, and saying so here is what makes that a claim rather than an omission.
        merge_method=SQUASH,
    )


def _trailers(admission: EstateLandingAdmission) -> str:
    """The permission, written into the artifact the estate's ledger will read.

    Only values that stay true, and nothing dated: the ledger freezes every string a landing
    carries at the first observation of it, so a body that named a count or a moment would make a
    later pass over an unchanged landing conflict with itself.
    """
    return (
        f"{CHANGE_RECORD_TRAILER}: {admission.change_record_id}\n"
        f"{POLICY_VERSION_TRAILER}: {admission.policy_version}"
    )


class GitHubEstatePullRequests:
    """The real gateway. Twenty-one calls, and nine of them change anything.

    Holds a token PROVIDER rather than a token, because an installation token expires within the
    hour and a long-lived process would otherwise carry a dead one. Nothing here logs, formats or
    re-raises key material.
    """

    def __init__(self, token_provider, *, timeout: float = 15.0) -> None:
        self._token_provider = token_provider
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token_provider()
        except GitHubAppTokenError as error:
            raise EstateGatewayError(f"app_token_mint:{error.code}") from error
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, url: str) -> Any:
        try:
            response = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        # `InvalidURL` is not an `httpx.RequestError`, and IDNA encoding of a malformed host
        # raises `UnicodeError`, which is a `ValueError` and neither of the other two. The
        # repository interpolated into these paths is operator-authored, so all three are ordinary
        # ways for one to escape as a bare 500.
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise EstateGatewayError(f"request_error:{error.__class__.__name__}") from error
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise EstateGatewayError("read_status", response.status_code)
        try:
            return response.json()
        except ValueError as error:
            raise EstateGatewayError("read_response_invalid") from error

    def read_pull_request(self, *, repository: str, number: int) -> EstatePullRequest:
        body = self._get(f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}")
        if not isinstance(body, dict):
            raise EstateGatewayError("read_response_invalid")
        return _pull_from_body(body, number)

    def commits_behind_base(self, *, repository: str, base_ref: str, head_sha: str) -> int:
        """How many commits the base has that the head does not.

        GitHub's own comparison, rather than a walk of our own: the question is whether a squash
        of this head would produce a tree the base has moved past, and the platform computing the
        merge base is the same one that will perform the squash.
        """
        body = self._get(f"{GITHUB_API_URL}/repos/{repository}/compare/{base_ref}...{head_sha}")
        if not isinstance(body, dict):
            raise EstateGatewayError("compare_response_invalid")
        behind = body.get("behind_by")
        if not isinstance(behind, int) or isinstance(behind, bool):
            raise EstateGatewayError("compare_response_invalid")
        return behind

    def blob_sha(self, *, repository: str, path: str, ref: str) -> str | None:
        """The object name of a file AS IT IS at a ref, or None if it is not there.

        Read from the remote rather than computed, so the pin cannot drift from the thing it pins
        by a hashing disagreement: this is the name the platform itself gives those bytes.

        BOTH VALUES ARE ENCODED. `repository` is shape-bounded at the schema; these two are not --
        the path comes from the served policy pin and the ref from the remote. A `#` in either
        truncates the query, which drops the ref and reads the DEFAULT branch instead: the base
        and the head would then answer identically, collapsing the distinction the rollout term
        was extended to make. The path keeps its slashes, which are meaningful in it.
        """
        quoted = quote(path, safe="/")
        body = self._get(
            f"{GITHUB_API_URL}/repos/{repository}/contents/{quoted}?ref={quote(ref, safe='')}"
        )
        if body is None:
            return None
        if not isinstance(body, dict):
            # A directory answers with a list. Whatever this is, it is not the file.
            return None
        sha = body.get("sha")
        return sha if isinstance(sha, str) and sha else None

    def head_check_runs(self, *, repository: str, head_sha: str) -> tuple[HeadCheckRun, ...]:
        """Every workflow run at this head, so the cascade can tell a verdict from its absence.

        **The workflow-run listing rather than the check-runs one, and that is forced.** This
        estate's App holds no `checks` permission, so the check-runs API answers 403 while this
        listing answers 200 -- the same constraint, and the same substitution, that the named-check
        observer already lives with. Every check this estate publishes is a workflow job, so the
        two sets coincide here; a check published by any other application would be invisible, and
        the cascade's conservative reading of an empty answer is what covers that.

        `per_page=100` because one head legitimately carries several: a workflow fires on both the
        push and the pull request, more than one workflow may watch a branch, and a re-run adds
        another. A default page would silently truncate, and truncation drops runs -- which is the
        direction that loses a failing one.
        """
        body = self._get(
            f"{GITHUB_API_URL}/repos/{repository}/actions/runs"
            f"?head_sha={quote(head_sha, safe='')}&per_page=100"
        )
        if not isinstance(body, dict):
            raise EstateGatewayError("runs_response_invalid")
        rows = body.get("workflow_runs")
        if not isinstance(rows, list):
            raise EstateGatewayError("runs_response_invalid")
        runs: list[HeadCheckRun] = []
        for row in rows:
            if not isinstance(row, dict):
                raise EstateGatewayError("runs_response_invalid")
            status = row.get("status")
            conclusion = row.get("conclusion")
            if not isinstance(status, str):
                # A run whose state cannot be read is not a run that reported nothing. Refusing
                # the whole answer keeps the caller from reading a partial list as a complete one.
                raise EstateGatewayError("runs_response_invalid")
            runs.append(
                HeadCheckRun(
                    status=status,
                    conclusion=conclusion if isinstance(conclusion, str) else None,
                )
            )
        return tuple(runs)

    def _get_pages(self, url: str, *, cap: int | None = None) -> list[Any]:
        """Every row of a paginated list answer, or a raise. ADR-0045.

        **Separate from `_get`, which turns a 404 into `None`.** That is right for a single object
        and wrong for a list: a list answer of 404 read as absent would read as EMPTY, which for
        the sibling rule means "this repository has no other pull requests" -- the most permissive
        answer there is. Here any status but 200, on any page, raises.

        A page shorter than the page size ends the list, so the common case costs one call. A
        failed page after the first raises rather than ending the list: a shorter list that looks
        complete is the failure pagination exists to prevent. `cap` stops paging once that many
        rows are held, for a listing the platform itself truncates; what a capped list means is the
        caller's to decide.
        """
        joiner = "&" if "?" in url else "?"
        rows: list[Any] = []
        for page in range(1, _MAX_LIST_PAGES + 1):
            paged = f"{url}{joiner}per_page={_LIST_PAGE_SIZE}&page={page}"
            try:
                response = httpx.get(paged, headers=self._headers(), timeout=self._timeout)
            except (httpx.RequestError, httpx.InvalidURL, ValueError) as error:
                raise EstateGatewayError(f"request_error:{error.__class__.__name__}") from error
            if response.status_code != 200:
                raise EstateGatewayError("list_status", response.status_code)
            try:
                body = response.json()
            except ValueError as error:
                raise EstateGatewayError("list_response_invalid") from error
            if not isinstance(body, list):
                raise EstateGatewayError("list_response_invalid")
            rows.extend(body)
            if len(body) < _LIST_PAGE_SIZE or (cap is not None and len(rows) >= cap):
                return rows
        raise EstateGatewayError("list_pagination_exceeded")

    def open_pull_requests(self, *, repository: str) -> tuple[OpenPullRequest, ...]:
        """Every open pull request in the repository, with its author and head. ADR-0045.

        Each row is read strictly: a row with no number cannot be matched to anything and a row
        with no head cannot be compared with its commits, and dropping either would shorten the
        list without saying so.
        """
        rows = self._get_pages(f"{GITHUB_API_URL}/repos/{repository}/pulls?state=open")
        pulls: list[OpenPullRequest] = []
        for row in rows:
            if not isinstance(row, dict):
                raise EstateGatewayError("open_pulls_response_invalid")
            number = row.get("number")
            head = row.get("head")
            head_sha = head.get("sha") if isinstance(head, dict) else None
            if not isinstance(number, int) or isinstance(number, bool):
                raise EstateGatewayError("open_pulls_response_invalid")
            if not isinstance(head_sha, str) or not head_sha:
                raise EstateGatewayError("open_pulls_response_invalid")
            user = row.get("user")
            user = user if isinstance(user, dict) else {}
            login = user.get("login")
            pulls.append(
                OpenPullRequest(
                    number=number,
                    head_sha=head_sha,
                    author_login=login if isinstance(login, str) else "",
                    author_is_bot=str(user.get("type", "")).lower() == "bot",
                )
            )
        return tuple(pulls)

    def pull_request_commits(
        self, *, repository: str, number: int
    ) -> tuple[PullRequestCommit, ...]:
        """Every commit on the pull request's branch, oldest first. ADR-0045.

        **The platform returns at most 250 commits here however it is paged**, so a list that
        reaches that many is not known to be complete and raises: a truncated read is an unread
        one. Update-bot branches carry one to three commits, so this never fires in practice; it
        exists so that the day it would, the answer is "unread" rather than "these are all".

        The logins are the top-level `author` / `committer` objects -- the accounts GitHub LINKED
        the commit to -- not the free-text names inside the commit, which anyone can set. Verified
        is true only when the platform says exactly `true`.
        """
        rows = self._get_pages(
            f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}/commits",
            cap=_COMMITS_LIST_CAP,
        )
        if len(rows) >= _COMMITS_LIST_CAP:
            raise EstateGatewayError("commits_list_truncated")
        commits: list[PullRequestCommit] = []
        for row in rows:
            if not isinstance(row, dict):
                raise EstateGatewayError("commits_response_invalid")
            sha = row.get("sha")
            if not isinstance(sha, str) or not sha:
                raise EstateGatewayError("commits_response_invalid")
            commit = row.get("commit")
            commit = commit if isinstance(commit, dict) else {}
            verification = commit.get("verification")
            verified = verification.get("verified") if isinstance(verification, dict) else None
            message = commit.get("message")
            commits.append(
                PullRequestCommit(
                    sha=sha,
                    author_login=_linked_login(row.get("author")),
                    committer_login=_linked_login(row.get("committer")),
                    verified=verified is True,
                    message=message if isinstance(message, str) else None,
                )
            )
        return tuple(commits)

    def submit_merge(
        self,
        *,
        repository: str,
        number: int,
        head_sha: str,
        commit_message: str,
        merge_method: str,
    ) -> MergeOutcome:
        """Ask for the landing, NAMING the head the terms were evaluated against.

        `sha` is the load-bearing parameter: the remote refuses when the pull request has moved,
        which closes the window between deciding and doing atomically and without this side having
        to have observed the move.

        `merge_method` is REQUIRED, with no default. A default would be the one value most
        subjects want, which is the shape that lets a caller needing the other one reach the
        remote without ever naming it -- and the failure is silent, because the wrong method still
        lands the pull request. Both callers state it.

        The body reaches the artifact under either method, measured rather than assumed
        (2026-09-14, a disposable public repository): with `commit_message` and no `commit_title`,
        the landing commit's subject is the platform's own default -- which names this pull
        request and the branch it came from -- followed by a blank line and then the body, and it
        carries two parents. Under `squash` the subject is the pull request's own title. So no
        title is sent: the default already identifies the subject, and a second thing to compose
        would be a second thing to keep true.

        Both values additionally depend on the target repository ALLOWING that method
        (`allow_merge_commit`, `allow_squash_merge`), which nothing here checks. Measured
        2026-09-14: all ten repositories of this estate permit both. A repository that stopped
        would answer 405, which reaches the confirming re-read, records nothing permanent, and
        stays retryable with the platform's own detail naming what it refused.
        """
        url = f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}/merge"
        try:
            response = httpx.put(
                url,
                headers=self._headers(),
                json={
                    "sha": head_sha,
                    "merge_method": merge_method,
                    "commit_message": commit_message,
                },
                timeout=self._timeout,
            )
        except (httpx.RequestError, httpx.InvalidURL, ValueError) as error:
            raise EstateGatewayError(f"request_error:{error.__class__.__name__}") from error
        if response.status_code != 200:
            raise EstateGatewayError("merge_status", response.status_code)
        try:
            body = response.json()
        except ValueError as error:
            raise EstateGatewayError("merge_response_invalid") from error
        landed = bool(body.get("merged")) if isinstance(body, dict) else False
        commit = body.get("sha") if isinstance(body, dict) else None
        return MergeOutcome(
            landed=landed,
            commit_sha=commit if isinstance(commit, str) else None,
            status_code=response.status_code,
        )

    def update_branch(self, *, repository: str, number: int, expected_head_sha: str) -> None:
        """Bring the base branch's commits into this pull request's head. ADR-0019 Increment 6.

        **THE SUCCESS CODE IS 202, NOT 200**, and copying the sibling call above would therefore
        report every success as a refusal. The platform accepts the request and performs the work
        afterwards; the body names a message and a URL and does NOT name the resulting head, which
        is why nothing here returns one and why this side must not re-read to confirm -- a read
        taken immediately can still answer with the old head and would read as a failure.

        `expected_head_sha` is the whole of the concurrency control, exactly as `sha` is for the
        landing: the remote answers 422 when the head has moved since it was read, rather than
        acting on a branch this side never evaluated. Sent always, never defaulted -- omitting it
        makes the platform substitute whatever the head is now, which is the one behaviour it is
        here to prevent.
        """
        url = f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}/update-branch"
        try:
            response = httpx.put(
                url,
                headers=self._headers(),
                json={"expected_head_sha": expected_head_sha},
                timeout=self._timeout,
            )
        except (httpx.RequestError, httpx.InvalidURL, ValueError) as error:
            raise EstateGatewayError(f"request_error:{error.__class__.__name__}") from error
        if response.status_code != 202:
            raise EstateGatewayError("branch_update_status", response.status_code)

    def comment_bodies(self, *, repository: str, number: int) -> tuple[str, ...]:
        """Every comment's text on the pull request's conversation. Bridge layer Part 4.

        Read before the held-close act comments, so a close that failed after its comment posted
        does not comment twice on the next pass. Paged strictly: a short list that looked complete
        would post the comment again.
        """
        rows = self._get_pages(f"{GITHUB_API_URL}/repos/{repository}/issues/{number}/comments")
        bodies: list[str] = []
        for row in rows:
            body = row.get("body") if isinstance(row, dict) else None
            if not isinstance(body, str):
                raise EstateGatewayError("comments_response_invalid")
            bodies.append(body)
        return tuple(bodies)

    def comment(self, *, repository: str, number: int, body: str) -> None:
        """Post one comment on the pull request's conversation. Bridge layer Part 4.

        A pull request's conversation is its issue's, so the path is the issue's; 201 is success.
        """
        self._send(
            httpx.post,
            f"{GITHUB_API_URL}/repos/{repository}/issues/{number}/comments",
            {"body": body},
            expected=201,
            status_code="comment_status",
        )

    def close(self, *, repository: str, number: int) -> None:
        """Close the pull request without landing it. Bridge layer Part 4.

        Believed only when the answer says the pull request is now closed: a 200 naming any other
        state is not the act having happened.
        """
        body = self._send(
            httpx.patch,
            f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}",
            {"state": "closed"},
            expected=200,
            status_code="close_status",
        )
        if not isinstance(body, dict) or body.get("state") != "closed":
            raise EstateGatewayError("close_response_invalid")

    def jobs_at(self, *, repository: str, sha: str) -> tuple[Job, ...]:
        """Every workflow job at this commit, each at its latest attempt. Landing Part 3a.

        The runs at the commit, then the jobs of each run -- the Actions listings, because this
        estate's App holds no `checks` permission (the same substitution `head_check_runs` makes).

        **`filter=all`, reduced here to each job's highest `run_attempt`**, rather than the
        platform's `latest`: what `latest` lists after a rerun of failed jobs only has not been
        measured from this side, and the reduction is right under either answer.

        Strict like every list read here: any status but 200, a row that cannot be read, or fewer
        rows than the listing's own `total_count` raises. A short list of jobs is how a failing one
        goes unseen.
        """
        quoted = quote(sha, safe="")
        runs = self._listing(
            f"{GITHUB_API_URL}/repos/{repository}/actions/runs?head_sha={quoted}", "workflow_runs"
        )
        latest: dict[tuple[int, str], Job] = {}
        for run in runs:
            run_id, workflow, event = _run_from_row(run)
            rows = self._listing(
                f"{GITHUB_API_URL}/repos/{repository}/actions/runs/{run_id}/jobs?filter=all",
                "jobs",
            )
            for row in rows:
                job = _job_from_row(row, run_id, workflow, event)
                held = latest.get((run_id, job.name))
                if held is None or job.attempt > held.attempt:
                    latest[(run_id, job.name)] = job
        return tuple(latest.values())

    def compare_status(self, *, repository: str, base: str, head: str) -> str:
        """GitHub's compare status of `head` against `base`. Landing Part 3b.

        A commit the repository does not have is a 404, and raises: a served commit that cannot be
        placed against the merge commit is unreadable, never fine.
        """
        body = self._get(
            f"{GITHUB_API_URL}/repos/{repository}/compare/"
            f"{quote(base, safe='')}...{quote(head, safe='')}"
        )
        if body is None:
            raise EstateGatewayError("commit_not_found", 404)
        status = body.get("status") if isinstance(body, dict) else None
        if not isinstance(status, str) or not status:
            raise EstateGatewayError("compare_response_invalid")
        return status

    def committed_at(self, *, repository: str, sha: str) -> datetime:
        """The commit's committer date: for a squash merge, the moment it landed. Landing Part 3b.

        A commit the repository does not have, or a date that cannot be read, raises: a landing
        with no time cannot be told how long production has stood.
        """
        body = self._get(f"{GITHUB_API_URL}/repos/{repository}/commits/{quote(sha, safe='')}")
        if body is None:
            raise EstateGatewayError("commit_not_found", 404)
        commit = body.get("commit") if isinstance(body, dict) else None
        committer = commit.get("committer") if isinstance(commit, dict) else None
        date = committer.get("date") if isinstance(committer, dict) else None
        try:
            when = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
        except ValueError as error:
            raise EstateGatewayError("commit_response_invalid") from error
        if when.tzinfo is None:
            raise EstateGatewayError("commit_response_invalid")
        return when

    def parent_of(self, *, repository: str, sha: str) -> str:
        """The single parent of a squash merge. Landing Part 3a.

        Exactly one, or a raise: a commit with none or with two is not a squash merge, and the
        default branch before the bump is only well defined for one.
        """
        body = self._get(f"{GITHUB_API_URL}/repos/{repository}/commits/{quote(sha, safe='')}")
        if body is None:
            raise EstateGatewayError("commit_not_found", 404)
        parents = body.get("parents") if isinstance(body, dict) else None
        if not isinstance(parents, list) or len(parents) != 1:
            raise EstateGatewayError("commit_parents_not_single")
        parent = parents[0].get("sha") if isinstance(parents[0], dict) else None
        if not isinstance(parent, str) or not parent:
            raise EstateGatewayError("commit_response_invalid")
        return parent

    def rerun_job(self, *, repository: str, job_id: int) -> None:
        """Ask the platform to rerun one job, once. Landing Part 3 (one job since Part 3b).

        One job by its id, never a run's failed jobs: those include failed jobs the watch never
        judged, and in a repository that redeploys one of them is the step that redeploys. The
        platform reruns the jobs that need this one too, as it would for any rerun of it.

        **201 Created is success**, not 200. The body names nothing this side needs, and nothing
        re-reads to confirm: the new attempt can take a while to appear, which the judgment reads
        from the attempt number rather than from this answer.
        """
        self._send(
            httpx.post,
            f"{GITHUB_API_URL}/repos/{repository}/actions/jobs/{job_id}/rerun",
            {},
            expected=201,
            status_code="rerun_status",
        )

    def default_branch_head(self, *, repository: str) -> BranchHead:
        """The default branch's name and the commit it points at now. Landing Part 3a."""
        body = self._get(f"{GITHUB_API_URL}/repos/{repository}")
        name = body.get("default_branch") if isinstance(body, dict) else None
        if not isinstance(name, str) or not name:
            raise EstateGatewayError("repository_response_invalid")
        ref = self._get(
            f"{GITHUB_API_URL}/repos/{repository}/git/ref/heads/{quote(name, safe='/')}"
        )
        target = ref.get("object") if isinstance(ref, dict) else None
        sha = target.get("sha") if isinstance(target, dict) else None
        if not isinstance(sha, str) or not sha:
            raise EstateGatewayError("ref_response_invalid")
        return BranchHead(name=name, sha=sha)

    def tree(self, *, repository: str, commit_sha: str) -> Tree:
        """A commit's tree sha and every leaf of it by path. Landing Part 3a.

        The commit is read first because the trees endpoint, given a commit, echoes the COMMIT's sha
        rather than the tree's (measured 2026-10-29), and a new tree's `base_tree` must be a tree.
        Directory rows are dropped: a directory's sha changes with any file under it, so only the
        leaves say what changed.

        Strict: a `truncated` answer raises, because a tree missing entries would read a path the
        bump touched as untouched.
        """
        repo = f"{GITHUB_API_URL}/repos/{repository}"
        commit = self._get(f"{repo}/git/commits/{quote(commit_sha, safe='')}")
        tree = commit.get("tree") if isinstance(commit, dict) else None
        tree_sha = tree.get("sha") if isinstance(tree, dict) else None
        if not isinstance(tree_sha, str) or not tree_sha:
            raise EstateGatewayError("commit_response_invalid")
        body = self._get(f"{repo}/git/trees/{quote(tree_sha, safe='')}?recursive=1")
        if not isinstance(body, dict) or body.get("truncated") is not False:
            raise EstateGatewayError("tree_response_invalid")
        rows = body.get("tree")
        if not isinstance(rows, list):
            raise EstateGatewayError("tree_response_invalid")
        entries: dict[str, TreeEntry] = {}
        for row in rows:
            path, entry = _tree_row(row)
            if entry.type != _TREE_KIND_DIRECTORY:
                entries[path] = entry
        return Tree(sha=tree_sha, entries=entries)

    def create_tree(self, *, repository: str, base_tree: str, changes: Sequence[TreeChange]) -> str:
        """A new tree: `base_tree` with each change applied, a null sha deleting its path."""
        body = self._send(
            httpx.post,
            f"{GITHUB_API_URL}/repos/{repository}/git/trees",
            {
                "base_tree": base_tree,
                "tree": [
                    {
                        "path": change.path,
                        "mode": change.mode,
                        "type": change.type,
                        "sha": change.sha,
                    }
                    for change in changes
                ],
            },
            expected=201,
            status_code="tree_create_status",
        )
        return _created_sha(body, "tree_create_response_invalid")

    def create_commit(self, *, repository: str, message: str, tree: str, parent: str) -> str:
        """A new commit of `tree` with the one parent `parent`. Moves no branch."""
        body = self._send(
            httpx.post,
            f"{GITHUB_API_URL}/repos/{repository}/git/commits",
            {"message": message, "tree": tree, "parents": [parent]},
            expected=201,
            status_code="commit_create_status",
        )
        return _created_sha(body, "commit_create_response_invalid")

    def create_branch(self, *, repository: str, branch: str, sha: str) -> None:
        """A new branch at `sha`. Refused (422) when the branch exists already: never moves one."""
        self._send(
            httpx.post,
            f"{GITHUB_API_URL}/repos/{repository}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
            expected=201,
            status_code="branch_create_status",
        )

    def open_pull_request(
        self, *, repository: str, title: str, body: str, head: str, base: str
    ) -> int:
        """Open a pull request from `head` into `base`; its number."""
        answer = self._send(
            httpx.post,
            f"{GITHUB_API_URL}/repos/{repository}/pulls",
            {"title": title, "body": body, "head": head, "base": base},
            expected=201,
            status_code="pull_create_status",
        )
        number = answer.get("number") if isinstance(answer, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise EstateGatewayError("pull_create_response_invalid")
        return number

    def _listing(self, url: str, key: str) -> list[Any]:
        """Every row of an Actions listing, whose answer wraps its rows as `{total_count, <key>}`.

        Separate from `_get_pages`, whose answers are bare lists, and from `_get`, which reads a
        404 as absent -- for a listing, that would read as EMPTY. Any status but 200 on any page
        raises, and so does ending with fewer rows than `total_count` names.
        """
        joiner = "&" if "?" in url else "?"
        rows: list[Any] = []
        for page in range(1, _MAX_LIST_PAGES + 1):
            paged = f"{url}{joiner}per_page={_LIST_PAGE_SIZE}&page={page}"
            try:
                response = httpx.get(paged, headers=self._headers(), timeout=self._timeout)
            except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
                raise EstateGatewayError(f"request_error:{error.__class__.__name__}") from error
            if response.status_code != 200:
                raise EstateGatewayError("listing_status", response.status_code)
            try:
                body = response.json()
            except ValueError as error:
                raise EstateGatewayError("listing_response_invalid") from error
            batch = body.get(key) if isinstance(body, dict) else None
            total = body.get("total_count") if isinstance(body, dict) else None
            if not isinstance(batch, list):
                raise EstateGatewayError("listing_response_invalid")
            if not isinstance(total, int) or isinstance(total, bool):
                raise EstateGatewayError("listing_response_invalid")
            rows.extend(batch)
            if len(batch) < _LIST_PAGE_SIZE:
                if len(rows) < total:
                    raise EstateGatewayError("listing_truncated")
                return rows
        raise EstateGatewayError("list_pagination_exceeded")

    def _send(
        self,
        method: Callable[..., httpx.Response],
        url: str,
        payload: dict[str, Any],
        *,
        expected: int,
        status_code: str,
    ) -> Any:
        """One write, refusing any status but `expected`; the parsed body, or None without one."""
        try:
            response = method(url, headers=self._headers(), json=payload, timeout=self._timeout)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise EstateGatewayError(f"request_error:{error.__class__.__name__}") from error
        if response.status_code != expected:
            raise EstateGatewayError(status_code, response.status_code)
        try:
            return response.json()
        except ValueError:
            return None


def _tree_row(row: Any) -> tuple[str, TreeEntry]:
    """One tree row, strictly: a leaf with no path, mode, type or sha cannot be compared."""
    if not isinstance(row, dict):
        raise EstateGatewayError("tree_response_invalid")
    path, mode, kind, sha = (row.get(key) for key in _TREE_FIELDS)
    if not (_text(path) and _text(mode) and _text(kind) and _text(sha)):
        raise EstateGatewayError("tree_response_invalid")
    return path, TreeEntry(mode=mode, type=kind, sha=sha)


def _text(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def _created_sha(body: Any, code: str) -> str:
    """The sha of an object a write created, or a raise."""
    sha = body.get("sha") if isinstance(body, dict) else None
    if not isinstance(sha, str) or not sha:
        raise EstateGatewayError(code)
    return sha


def _run_from_row(row: Any) -> tuple[int, str, str]:
    """A run's id, workflow name and triggering event, strictly: the judgment keys on all three."""
    if not isinstance(row, dict):
        raise EstateGatewayError("runs_response_invalid")
    run_id = row.get("id")
    workflow = row.get("name")
    event = row.get("event")
    if not isinstance(run_id, int) or isinstance(run_id, bool):
        raise EstateGatewayError("runs_response_invalid")
    if not isinstance(workflow, str) or not workflow or not isinstance(event, str) or not event:
        raise EstateGatewayError("runs_response_invalid")
    return run_id, workflow, event


def _job_from_row(row: Any, run_id: int, workflow: str, event: str) -> Job:
    """One job row, strictly: a job with no name, attempt or id cannot be matched or rerun."""
    if not isinstance(row, dict):
        raise EstateGatewayError("jobs_response_invalid")
    name = row.get("name")
    attempt = row.get("run_attempt")
    job_id = row.get("id")
    conclusion = row.get("conclusion")
    if not isinstance(name, str) or not name:
        raise EstateGatewayError("jobs_response_invalid")
    if not isinstance(attempt, int) or isinstance(attempt, bool):
        raise EstateGatewayError("jobs_response_invalid")
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise EstateGatewayError("jobs_response_invalid")
    return Job(
        name=name,
        conclusion=conclusion if isinstance(conclusion, str) else None,
        run_id=run_id,
        attempt=attempt,
        workflow=workflow,
        event=event,
        job_id=job_id,
    )


def _linked_login(account: Any) -> str | None:
    """The login of a linked account object, or None when the platform linked no account."""
    if not isinstance(account, dict):
        return None
    login = account.get("login")
    return login if isinstance(login, str) and login else None


def _pull_from_body(body: dict[str, Any], number: int) -> EstatePullRequest:
    """The remote's answer, read defensively about a shape this repository does not own.

    Every unrecognised shape raises rather than falling to a plausible default: this module's
    caller treats an unreadable pull request as a refusal, and a default that read as "open,
    authored by the update bot, clean" would be a fabricated permission.
    """
    if body.get("number") != number:
        # Believe an object only when it says it is the one that was asked for.
        raise EstateGatewayError("read_response_invalid")
    base = body.get("base")
    base = base if isinstance(base, dict) else {}
    repo = base.get("repo")
    repo = repo if isinstance(repo, dict) else {}
    head = body.get("head")
    head = head if isinstance(head, dict) else {}
    user = body.get("user")
    user = user if isinstance(user, dict) else {}

    base_ref = base.get("ref")
    default_branch = repo.get("default_branch")
    head_sha = head.get("sha")
    # ADR-0036. The branch name, which is where the update bot states the ecosystem an exclusion is
    # keyed on. REQUIRED like the others rather than defaulted: a body with no head ref is a body
    # this reader does not recognise, and defaulting it to an empty string would hand the exclusion
    # a name it can never read, which the caller then has to treat as a refusal anyway -- one
    # refusal naming the wrong cause instead of the one that says the answer was unreadable.
    head_ref = head.get("ref")
    title = body.get("title")
    if not all(
        isinstance(v, str) and v for v in (base_ref, default_branch, head_sha, head_ref, title)
    ):
        raise EstateGatewayError("read_response_invalid")
    login = user.get("login")
    landed = bool(body.get("merged"))
    merge_sha = body.get("merge_commit_sha")
    return EstatePullRequest(
        number=number,
        title=str(title),
        head_sha=str(head_sha),
        base_ref=str(base_ref),
        head_ref=str(head_ref),
        default_branch=str(default_branch),
        open=body.get("state") == "open",
        landed=landed,
        author_login=login if isinstance(login, str) else "",
        author_is_bot=str(user.get("type", "")).lower() == "bot",
        mergeable_state=str(body.get("mergeable_state") or ""),
        # GitHub fills this for an open pull request too (a test merge); only a landed one's is
        # the landing commit.
        merge_commit_sha=merge_sha if landed and isinstance(merge_sha, str) and merge_sha else None,
    )
