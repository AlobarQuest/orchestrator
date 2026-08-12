"""The orchestrator lands a pull request into a repository where landing changes something
already serving. ADR-0019 Increment 5b.

**This is the second place in this repository that changes a repository nobody asked it to change
at the moment it acts, and it is the more consequential of the two** -- the other lands into
repositories the estate calls inert, where the landed commit sits until something separately acts
on it. Here the landing IS the change to a running service.

The shape is copied from that sibling, deliberately and almost exactly: an admission cascade of
named refusals re-evaluated here, a record row with a unique constraint so a repeat is detectable,
an injected client so the whole path runs with no network, and credentials resolved once so the
gate can never attest to credentials the actor does not use.

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

import uuid
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import quote

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.clock import Clock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import EstatePrMerge, Event
from orchestrator.services.change_record import ChangeRecordSource
from orchestrator.services.estate_landing import EstateLandingSource
from orchestrator.services.estate_landing_admission import (
    EstateGatewayError,
    EstateLandingAdmission,
    EstatePullRequest,
    EstateReadGateway,
    estate_landing_admission,
)
from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.lifecycle import ActorContext

GITHUB_API_URL: Final = "https://api.github.com"

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

# Recorded on the one ambiguous outcome: the remote refused and the confirming read also failed,
# so a landing cannot be ruled out.
MERGE_REFUSED_BY_REMOTE: Final = "merge_refused_by_remote"

# The prefix of every gateway code raised BEFORE anything is sent. `_headers()` mints the App
# token first, so a mint failure means the request provably did not leave this process -- and a
# landing that cannot have happened must never be recorded, because the row is permanent and
# would bar the pull request forever on one transient outage. Everything else in `submit_merge`
# happens at or after the send, where a lost response and a refusal are indistinguishable and the
# conservative record is the right answer.
NEVER_SENT: Final = "app_token_mint:"


@dataclass(frozen=True)
class MergeOutcome:
    landed: bool
    commit_sha: str | None
    status_code: int | None


class EstatePullRequestGateway(EstateReadGateway, Protocol):
    """Everything the admission cascade reads, plus the one call that changes anything."""

    def submit_merge(
        self, *, repository: str, number: int, head_sha: str, commit_message: str
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
    """Own the transaction, the way every request entry point in this repository does.

    A flush alone would return a correct-looking response while the row is discarded when the
    session closes -- leaving the record of an act that really happened absent, which is the one
    state the whole idempotency story depends on not reaching.
    """
    try:
        record = _land(
            session,
            command,
            gateway,
            landing_source,
            record_source,
            enabled=enabled,
            credentials_configured=credentials_configured,
            clock=clock,
        )
        session.commit()
        return record
    except Exception:
        session.rollback()
        raise


def _land(
    session: Session,
    command: EstateMergeCommand,
    gateway: EstatePullRequestGateway,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    *,
    enabled: bool,
    credentials_configured: bool,
    clock: Clock | None,
) -> EstatePrMerge:
    _authorize_actor(command.actor)
    repository = command.repository.lower()

    # SERIALISE ON THE REPOSITORY before anything is read. There is no unit row to lock here, and
    # the two rules that must not be raced -- one row per pull request, one landing per repository
    # per window -- are both stated over rows that may not exist yet, which `FOR UPDATE` cannot
    # lock. Two requests would otherwise each read an absence and each act on it.
    _lock_repository(session, repository)

    existing = session.scalar(
        select(EstatePrMerge).where(
            EstatePrMerge.repository == repository,
            EstatePrMerge.pr_number == command.pr_number,
        )
    )
    if existing is not None:
        return existing

    # A key already spent on a DIFFERENT subject, refused here rather than at the flush. Both
    # unique keys are global, so an operator who copies one request and changes only the number
    # would otherwise reach the remote, LAND THE PULL REQUEST, and lose the whole transaction to
    # an integrity error with no registered handler -- a bare 500 that reads as "nothing
    # happened" over a landing that did.
    spent = session.scalar(
        select(EstatePrMerge).where(EstatePrMerge.idempotency_key == command.idempotency_key)
    )
    if spent is not None:
        raise DomainError(
            "idempotency_conflict",
            "this idempotency key belongs to a different pull request",
            "use a new idempotency key",
        )

    admission = estate_landing_admission(
        session,
        repository,
        command.pr_number,
        landing_source,
        record_source,
        gateway,
        enabled=enabled,
        credentials_configured=credentials_configured,
        clock=clock,
    )
    if not admission.satisfied:
        # No record: nothing was acted on, and consuming this pull request's one row here would
        # refuse every later legitimate attempt. The reasons are already served by the read
        # surface; they are named in the message rather than in a structured field, because
        # `DomainError` carries a closed set of attributes.
        raise DomainError(
            ESTATE_MERGE_NOT_ADMISSIBLE,
            "this pull request may not be landed: " + ", ".join(admission.refusals),
            "read the landing-admission answer for every term that is unmet",
        )
    if admission.head_sha != command.expected_head_sha:
        # The pull request moved between the answer the caller read and this call. Nothing is
        # recorded, because nothing happened and the caller can simply re-read: the update bot
        # rebasing its own branch is the ordinary cause, and the freshness term will then pass on
        # a head somebody has actually evaluated.
        raise DomainError(
            ESTATE_MERGE_HEAD_MOVED,
            "the pull request's head is not the one the caller read",
            "re-read the landing-admission answer and ask again",
        )
    return _act(session, command, gateway, admission)


def _lock_repository(session: Session, repository: str) -> None:
    """Hold every other landing for this repository until this transaction settles.

    An advisory lock rather than a row lock, because the rows this decision is about are the ones
    that do not exist yet. It is released with the transaction whichever way that ends.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"estate_pr_merge:{repository}"},
    )


def _act(
    session: Session,
    command: EstateMergeCommand,
    gateway: EstatePullRequestGateway,
    admission: EstateLandingAdmission,
) -> EstatePrMerge:
    """The call and the reconciling re-read.

    **A RECORD IS WRITTEN ONLY FOR AN OUTCOME THAT CANNOT BE RETRIED.** The row is unique per pull
    request with no delete path, so every recorded outcome is permanent -- correct for *it landed*
    and for *we cannot rule out that it landed*, and badly wrong for *the remote answered 502
    once*, which would bar the pull request forever on one bad response.
    """
    head_sha = admission.head_sha
    if not head_sha:
        # Unreachable through the cascade, which refuses an unreadable pull request. Stated rather
        # than assumed, because this is the last point at which the call could name no head, and
        # naming the head is what makes the remote refuse a tree the terms were not evaluated on.
        raise DomainError(ESTATE_MERGE_NOT_ADMISSIBLE, "the head is not identified", None)

    try:
        outcome = gateway.submit_merge(
            repository=admission.repository,
            number=admission.pr_number,
            head_sha=head_sha,
            commit_message=_trailers(admission),
        )
    except EstateGatewayError as error:
        if error.code.startswith(NEVER_SENT):
            # NOTHING WAS SENT, so nothing can have landed. The reconciling read below would fail
            # the same way under the same outage and answer "we do not know", which would write a
            # permanent `refused` row -- silently barring an admissible pull request forever on a
            # transient credential failure, and reported by the caller as settled rather than as a
            # finding. The error code already carried the distinction and nothing read it.
            raise DomainError(
                ESTATE_MERGE_REFUSED_BY_REMOTE,
                f"the landing was not attempted: {error.code}",
                "retry once the credential can be minted",
            ) from error
        landed = _landed_after_all(gateway, admission)
        if landed is True:
            return _record(
                session,
                command,
                admission,
                head_sha,
                status="already_merged",
                reason_code=None,
                github_status=error.status_code,
            )
        if landed is False:
            # CONFIRMED not landed, so nothing happened and this is retryable -- a required check
            # that is red today can be green tomorrow. No record.
            raise DomainError(
                ESTATE_MERGE_REFUSED_BY_REMOTE,
                f"the remote refused to land the pull request: {error.code}",
                "resolve what the remote objected to, then ask again",
            ) from error
        # The reconciling read ITSELF failed, so a landing cannot be ruled out. Terminal and
        # conservative: a retry would meet the same refusal no better informed, and the ledger
        # observes the landing independently and can settle it.
        return _record(
            session,
            command,
            admission,
            head_sha,
            status="refused",
            reason_code=f"{MERGE_REFUSED_BY_REMOTE}:{error.code}",
            github_status=error.status_code,
        )

    return _record(
        session,
        command,
        admission,
        head_sha,
        status="merged" if outcome.landed else "refused",
        reason_code=None if outcome.landed else MERGE_REFUSED_BY_REMOTE,
        merge_commit_sha=outcome.commit_sha,
        github_status=outcome.status_code,
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


def _landed_after_all(
    gateway: EstatePullRequestGateway, admission: EstateLandingAdmission
) -> bool | None:
    """Did the pull request land despite the refusal? `None` means WE DO NOT KNOW.

    Three answers, not two, and the third is the one the caller must treat differently: a second
    failure to read cannot be collapsed into "it did not land", because that reads a lost success
    as a clean refusal.
    """
    try:
        return gateway.read_pull_request(
            repository=admission.repository, number=admission.pr_number
        ).landed
    except EstateGatewayError:
        return None


def _authorize_actor(actor: ActorContext) -> None:
    """SYSTEM only.

    Not the worker, for the reason the sibling path gives: a runner asking for its own work to be
    landed is the runner attesting to its own compliance. And not a human either -- a person can
    land a pull request themselves, and this exists for the case where nobody had to.
    """
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may land a pull request",
            None,
        )


def _record(
    session: Session,
    command: EstateMergeCommand,
    admission: EstateLandingAdmission,
    head_sha: str,
    *,
    status: str,
    reason_code: str | None,
    merge_commit_sha: str | None = None,
    github_status: int | None = None,
) -> EstatePrMerge:
    record = EstatePrMerge(
        repository=admission.repository,
        pr_number=admission.pr_number,
        head_sha=head_sha,
        status=status,
        reason_code=reason_code,
        merge_commit_sha=merge_commit_sha,
        github_status=github_status,
        change_record_id=admission.change_record_id,
        policy_version=admission.policy_version,
        idempotency_key=command.idempotency_key,
    )
    session.add(record)
    session.flush()
    event = Event(
        actor_id=command.actor.actor_id,
        action=f"estate_pr_merge.{status}",
        subject_type="estate_pr_merge",
        subject_id=record.id,
        payload={
            "estate_pr_merge_record_id": str(record.id),
            "repository": admission.repository,
            "pr_number": admission.pr_number,
            "head_sha": head_sha,
            "status": status,
            "reason_code": reason_code,
            "merge_commit_sha": merge_commit_sha,
            "change_record_id": admission.change_record_id,
            "policy_version": admission.policy_version,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=f"{command.idempotency_key}:event",
    )
    session.add(event)
    session.flush()
    record.event_id = event.id
    session.flush()
    return record


class GitHubEstatePullRequests:
    """The real gateway. Four calls, and only one of them changes anything.

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
        except (httpx.RequestError, httpx.InvalidURL, ValueError) as error:
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

    def submit_merge(
        self, *, repository: str, number: int, head_sha: str, commit_message: str
    ) -> MergeOutcome:
        """Ask for the landing, NAMING the head the terms were evaluated against.

        `sha` is the load-bearing parameter: the remote refuses when the pull request has moved,
        which closes the window between deciding and doing atomically and without this side having
        to have observed the move.
        """
        url = f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}/merge"
        try:
            response = httpx.put(
                url,
                headers=self._headers(),
                json={
                    "sha": head_sha,
                    "merge_method": "squash",
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
    title = body.get("title")
    if not all(isinstance(v, str) and v for v in (base_ref, default_branch, head_sha, title)):
        raise EstateGatewayError("read_response_invalid")
    login = user.get("login")
    return EstatePullRequest(
        number=number,
        title=str(title),
        head_sha=str(head_sha),
        base_ref=str(base_ref),
        default_branch=str(default_branch),
        open=body.get("state") == "open",
        landed=bool(body.get("merged")),
        author_login=login if isinstance(login, str) else "",
        author_is_bot=str(user.get("type", "")).lower() == "bot",
        mergeable_state=str(body.get("mergeable_state") or ""),
    )
