"""The factory lands its own pull request. ADR-0020, Increment 4b.

**This is the one place in this repository that changes a repository nobody asked it to change at
the moment it acts**, and the shape is copied from the only other authorized outbound mutation
this estate has: an admission cascade of named refusals, a record row with a unique constraint so
a repeat is detectable, an injected client so the whole path can be exercised with no network, and
credentials resolved once at the route and handed to both the gate and the actor, so the gate can
never attest to credentials the actor does not use.

**Every term is re-evaluated HERE, over the row this transaction locked.** The reported answer
(`services/pr_merge_admission.py`) is a report; between reading it and acting, a unit can be
transitioned, an approval can be recorded, a divergence can be detected. Re-asking is what closes
that window, and it is why the act is an explicit command rather than a side effect of completion:
a failed landing must never roll back a recorded completion.

## Three terms only this module can evaluate, because only GitHub knows them

The estate's answer is about a repository's DEFAULT branch. Nothing on this side records what a
pull request targets, so a pull request opened against some other base would land somewhere that
answer does not describe. Asked here, refused here.

Whether the pull request is still open, likewise. And the head: every head this side holds is one
somebody REPORTED, so an unreported push is invisible to a local comparison. The call therefore
NAMES the adjudicated head, and the remote refuses anything else — a guarantee no comparison of
our own rows can offer.

## Idempotency, and why the order is check, act, reconcile, record

A landing is not idempotent and its failure is asymmetric: if it succeeds and the response is
lost, asking again answers 405, which is shaped exactly like a refusal. So:

1. **Check.** The unit's row is locked and its merge record read. A record means the call was
   already made; it is replayed, never repeated. This is the scar the workflow trigger left —
   there, a reused ordinal returns a success-shaped record having fired nothing, and a repeat here
   would be worse, because the second call reaches GitHub and comes back with a plausible refusal.
2. **Act.**
3. **Reconcile before recording a refusal.** A refusal from the remote is followed by re-reading
   the pull request: if it is in fact landed, the record says so. Without that, a retry after a
   lost response writes `refused` over something that happened, which is the one outcome nothing
   downstream could correct.
4. **Record.**

Act-then-record, deliberately, because the two failure modes are not equal. A record written
before a call that then fails is a lie in the ledger. A landing whose record is lost is
recoverable: the landing ledger observes GitHub independently and would report it as basis-less
rather than silently.

## There is no off-switch, and that is decided rather than overlooked

Devon, 2026-08-09, following the same reasoning that retired the workflow-trigger flag: a
non-per-unit switch stops nothing here, because this command has exactly one caller and no
scheduler, while toggling costs a release and a restart — and a restart during a live run is the
one thing that genuinely strands a unit. Four stops exist and none needs a release: withhold the
authority approval, withhold the capability in the envelope, branch protection, and revoking the
App installation's write. **If a SCHEDULED caller is ever proposed, that decision is void** — a
switch is ceremony against one human-invoked caller and a real control against a loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, UnitPrMerge, WorkPackageRevision, WorkUnit
from orchestrator.services.estate_landing import EstateLandingSource
from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_merge_admission import MergeAdmission, admission_for

GITHUB_API_URL: Final = "https://api.github.com"

# The pull request does not target the branch the estate's answer is about. The estate says what
# landing on a repository's DEFAULT branch does; a different base is a different question.
PR_BASE_NOT_DEFAULT_BRANCH: Final = "pr_base_not_default_branch"

# The pull request is closed, or already landed, or otherwise not in a state that can be landed.
PR_NOT_OPEN: Final = "pr_not_open"

# The remote refused. Branch protection is the expected reason and is the floor under this whole
# increment: the App cannot read protection settings, so a refusal is learned by attempting and
# reading the answer, never by asking permission first.
MERGE_REFUSED_BY_REMOTE: Final = "merge_refused_by_remote"

# The remote could not be reached, or answered something unreadable.
REMOTE_UNREADABLE: Final = "remote_unreadable"


@dataclass(frozen=True)
class PullRequestState:
    """What the remote says about the pull request, as this module needs it."""

    base_ref: str
    default_branch: str
    open: bool
    landed: bool


@dataclass(frozen=True)
class MergeOutcome:
    landed: bool
    commit_sha: str | None
    status_code: int | None


class PullRequestGateway(Protocol):
    """Read the pull request, and ask for it to be landed. Injected, so the whole admission
    cascade is exercisable with no network."""

    def read_pull_request(self, *, repository: str, number: int) -> PullRequestState: ...

    def submit_merge(self, *, repository: str, number: int, head_sha: str) -> MergeOutcome: ...


class GitHubGatewayError(Exception):
    """A failure to reach or read the remote. Carries a code, never a token."""

    def __init__(self, code: str, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class MergeCommand:
    unit_id: uuid.UUID
    actor: ActorContext
    idempotency_key: str
    expected_version: int | None = None


def land_unit_pull_request(
    session: Session,
    command: MergeCommand,
    gateway: PullRequestGateway,
    landing_source: EstateLandingSource,
) -> UnitPrMerge:
    """Own the transaction, the way every request entry point in this repository does.

    A flush alone would return a correct-looking response while the row is discarded when the
    session closes — leaving the record of an act that really happened absent, which is the one
    state the whole idempotency story depends on not reaching.
    """
    try:
        record = _land_unit_pull_request(session, command, gateway, landing_source)
        session.commit()
        return record
    except Exception:
        session.rollback()
        raise


def _land_unit_pull_request(
    session: Session,
    command: MergeCommand,
    gateway: PullRequestGateway,
    landing_source: EstateLandingSource,
) -> UnitPrMerge:
    _authorize_actor(command.actor)
    unit = session.scalar(select(WorkUnit).where(WorkUnit.id == command.unit_id).with_for_update())
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    if command.expected_version is not None and unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )

    # Read BEFORE the call, under the unit's row lock, so two requests cannot both find nothing.
    existing = session.scalar(
        select(UnitPrMerge).where(UnitPrMerge.work_unit_id == unit.id).with_for_update()
    )
    if existing is not None:
        return existing

    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)

    admission = admission_for(session, unit, revision, landing_source)
    if not admission.satisfied:
        # No record: this unit was never acted on, and consuming its one row here would refuse
        # every later legitimate attempt. The reasons are already served by the read surface.
        # The refusals are named in the message rather than in a structured field: `DomainError`
        # carries a closed set of attributes, and widening it for one caller would put a shape in
        # the error contract that every handler would then have to know about.
        raise DomainError(
            "pr_merge_not_admissible",
            "this unit may not have its pull request landed: " + ", ".join(admission.refusals),
            "read the merge-admission answer for every term that is unmet",
        )

    return _act(session, command, unit, revision, gateway, admission)


def _act(
    session: Session,
    command: MergeCommand,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    gateway: PullRequestGateway,
    admission: MergeAdmission,
) -> UnitPrMerge:
    """The three remote terms, the call, and the reconciling re-read."""
    number = admission.pr_number
    head_sha = admission.verified_head_sha
    if number is None or not head_sha:
        # Unreachable through `admission_for`, which refuses both. Stated rather than assumed,
        # because this is the last point at which either could be absent and the call would then
        # name no head — which is the whole guarantee.
        raise DomainError("pr_merge_not_admissible", "the pull request is not identified", None)

    try:
        state = gateway.read_pull_request(repository=admission.target_repository, number=number)
    except GitHubGatewayError as error:
        return _record(
            session,
            command,
            unit,
            revision,
            admission,
            number,
            head_sha,
            status="refused",
            reason_code=REMOTE_UNREADABLE,
            github_status=error.status_code,
        )

    if state.base_ref != state.default_branch:
        return _record(
            session,
            command,
            unit,
            revision,
            admission,
            number,
            head_sha,
            status="refused",
            reason_code=PR_BASE_NOT_DEFAULT_BRANCH,
        )
    if state.landed:
        # Somebody else landed it. Recorded truthfully rather than as our own act.
        return _record(
            session,
            command,
            unit,
            revision,
            admission,
            number,
            head_sha,
            status="already_merged",
            reason_code=None,
        )
    if not state.open:
        return _record(
            session,
            command,
            unit,
            revision,
            admission,
            number,
            head_sha,
            status="refused",
            reason_code=PR_NOT_OPEN,
        )

    try:
        outcome = gateway.submit_merge(
            repository=admission.target_repository, number=number, head_sha=head_sha
        )
    except GitHubGatewayError as error:
        # THE RECONCILING READ. A refusal here is indistinguishable from a lost response to a call
        # that succeeded, so ask what is true before writing what happened. Asked ONCE and bound to
        # a name: reading twice would be two answers to one question, and the record would be
        # composed from whichever each happened to give.
        landed = _landed_after_all(gateway, admission, number)
        return _record(
            session,
            command,
            unit,
            revision,
            admission,
            number,
            head_sha,
            status="already_merged" if landed else "refused",
            reason_code=None if landed else MERGE_REFUSED_BY_REMOTE,
            github_status=error.status_code,
        )

    return _record(
        session,
        command,
        unit,
        revision,
        admission,
        number,
        head_sha,
        status="merged" if outcome.landed else "refused",
        reason_code=None if outcome.landed else MERGE_REFUSED_BY_REMOTE,
        merge_commit_sha=outcome.commit_sha,
        github_status=outcome.status_code,
    )


def _landed_after_all(gateway: PullRequestGateway, admission: MergeAdmission, number: int) -> bool:
    """Did the pull request land despite the refusal? Never raises: a second failure to read
    leaves the honest answer, which is that we do not know it landed."""
    try:
        return gateway.read_pull_request(
            repository=admission.target_repository, number=number
        ).landed
    except GitHubGatewayError:
        return False


def _authorize_actor(actor: ActorContext) -> None:
    """SYSTEM only, and deliberately not the worker.

    A runner asking for its own pull request to be landed is the runner attesting to its own
    compliance, which is the shape WS-P2.32 spent a workstream closing. A human does not need this
    route: a person can land a pull request themselves, and this exists for the case where nobody
    had to.
    """
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden", "only the orchestrator system actor may land a pull request", None
        )


def _record(
    session: Session,
    command: MergeCommand,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    admission: MergeAdmission,
    number: int,
    head_sha: str,
    *,
    status: str,
    reason_code: str | None,
    merge_commit_sha: str | None = None,
    github_status: int | None = None,
) -> UnitPrMerge:
    record = UnitPrMerge(
        work_unit_id=unit.id,
        repository=admission.target_repository,
        pr_number=number,
        head_sha=head_sha,
        status=status,
        reason_code=reason_code,
        merge_commit_sha=merge_commit_sha,
        github_status=github_status,
        idempotency_key=command.idempotency_key,
    )
    session.add(record)
    session.flush()
    event = Event(
        actor_id=command.actor.actor_id,
        action=f"pr_merge.{status}",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload={
            "pr_merge_record_id": str(record.id),
            "work_package_revision_id": str(revision.id),
            "repository": admission.target_repository,
            "pr_number": number,
            "head_sha": head_sha,
            "status": status,
            "reason_code": reason_code,
            "merge_commit_sha": merge_commit_sha,
            "authority_fingerprint": unit.authority_fingerprint,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=f"{command.idempotency_key}:event",
    )
    session.add(event)
    session.flush()
    record.event_id = event.id
    session.flush()
    return record


class GitHubPullRequests:
    """The real gateway. Reads one pull request and asks for one landing; nothing else.

    Holds a token PROVIDER rather than a token, for the reason the workflow client does: an
    installation token expires within the hour and a long-lived process would otherwise carry a
    dead one. Nothing here logs, formats or re-raises key material.
    """

    def __init__(self, token_provider, *, timeout: float = 15.0) -> None:
        self._token_provider = token_provider
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token_provider()
        except GitHubAppTokenError as error:
            raise GitHubGatewayError(f"app_token_mint:{error.code}") from error
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def read_pull_request(self, *, repository: str, number: int) -> PullRequestState:
        url = f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}"
        try:
            response = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        except httpx.RequestError as error:
            raise GitHubGatewayError(f"request_error:{error.__class__.__name__}") from error
        if response.status_code != 200:
            raise GitHubGatewayError("read_status", response.status_code)
        try:
            body = response.json()
        except ValueError as error:
            raise GitHubGatewayError("read_response_invalid") from error
        return _state_from_body(body)

    def submit_merge(self, *, repository: str, number: int, head_sha: str) -> MergeOutcome:
        """PUT the landing, NAMING the head the criteria were adjudicated at.

        `sha` is the load-bearing parameter: the remote refuses when the pull request has moved,
        which closes the window between deciding and doing atomically and without this side having
        to have observed the move.
        """
        url = f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}/merge"
        try:
            response = httpx.put(
                url,
                headers=self._headers(),
                json={"sha": head_sha, "merge_method": "squash"},
                timeout=self._timeout,
            )
        except httpx.RequestError as error:
            raise GitHubGatewayError(f"request_error:{error.__class__.__name__}") from error
        if response.status_code != 200:
            raise GitHubGatewayError("merge_status", response.status_code)
        try:
            body = response.json()
        except ValueError as error:
            raise GitHubGatewayError("merge_response_invalid") from error
        landed = bool(body.get("merged")) if isinstance(body, dict) else False
        commit = body.get("sha") if isinstance(body, dict) else None
        return MergeOutcome(
            landed=landed,
            commit_sha=commit if isinstance(commit, str) else None,
            status_code=response.status_code,
        )


def _state_from_body(body: Any) -> PullRequestState:
    """The remote's answer, read defensively about a shape this repository does not own.

    Anything missing or of the wrong type reads as NOT open and NOT landed against a base that is
    not the default branch — every unrecognised shape falls to a refusal rather than to the
    nearest recognisable thing.
    """
    if not isinstance(body, dict):
        raise GitHubGatewayError("read_response_invalid")
    base = body.get("base")
    base = base if isinstance(base, dict) else {}
    repo = base.get("repo")
    repo = repo if isinstance(repo, dict) else {}
    base_ref = base.get("ref")
    default_branch = repo.get("default_branch")
    if not isinstance(base_ref, str) or not isinstance(default_branch, str):
        raise GitHubGatewayError("read_response_invalid")
    return PullRequestState(
        base_ref=base_ref,
        default_branch=default_branch,
        open=body.get("state") == "open",
        landed=bool(body.get("merged")),
    )
