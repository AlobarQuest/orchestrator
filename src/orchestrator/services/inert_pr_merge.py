"""The orchestrator lands a pull request into a repository where landing changes nothing already
serving. ADR-0038 part 2.

**This is the third place in this repository that changes a repository nobody asked it to change
at the moment it acts, and it is the least consequential of the three** -- the landed commit sits
on a default branch until something separately acts on it, which for these six repositories is
nothing. What it costs is not a running service; it is that `main` is what every build session
branches from and what default-branch CI now runs on.

The shape is copied from the sibling that lands where landing DOES change something already
serving, deliberately and almost exactly: a cascade of named refusals re-evaluated here, a row
with a unique constraint so a repeat is detectable, an injected gateway so the whole path runs
with no network, and credentials resolved once so the gate can never attest to credentials the
actor does not use.

## Why the orchestrator, rather than the platform's own arming

This lane replaces a GitHub Actions workflow that armed the platform's own automatic landing with
the workflow-scoped token. Measured across the estate's merge history: 38 landings armed that way
fired **zero** default-branch workflow runs, against 18 by this estate's App which fired 18. So
those six repositories have been skipping default-branch CI on every unattended landing, and
`main` could be red there with nothing reporting it. Performing the act directly is what switches
that CI back on -- which is the whole of what ADR-0038 gains.

## What it writes into the landing commit, and why

One trailer, naming the policy version that permitted the landing. The estate's ledger observes
landings independently and classifies each by the permission it can find; with the workflow gone
there is no gate run at the head to attribute one to, so without something in the artifact itself
every landing here would record as having no accountable basis at all -- a class the ledger keeps
and, until ADR-0038 part 3, no detector reads.

**The trailer's NAME is what identifies the lane**, so a reader needs no second marker and no
inference from which other trailers are absent.

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

## The switch defaults to refusing

Its caller is a scheduled one, so it has a switch for the reason its sibling has one: a switch
against a loop is a real control where a switch against one operator is ceremony. Its own switch
rather than the sibling's, because the two lanes are activated by different decisions and turning
one on must not turn the other on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import EstatePrMerge, Event
from orchestrator.services.estate_landing import EstateLandingSource
from orchestrator.services.estate_landing_admission import (
    EstateGatewayError,
    EstateReadGateway,
    gateway_failure_detail,
)
from orchestrator.services.estate_pr_merge import (
    NEVER_SENT,
    GitHubEstatePullRequests,
    MergeOutcome,
)
from orchestrator.services.inert_landing_admission import (
    InertLandingAdmission,
    inert_landing_admission,
)
from orchestrator.services.inert_landing_policy import InertLandingPolicySource
from orchestrator.services.lifecycle import ActorContext

# The trailer the landing writes into the squash body, and the estate's ledger reads back out of
# it. Named here because this is the only writer; the reader pins the same spelling on its own
# side, and a disagreement between the two is a landing recorded with no basis rather than a
# crash -- which is why both sides carry a test naming the literal rather than deriving it from
# the other.
#
# ONE TRAILER, AND ITS NAME CARRIES THE LANE. The sibling stamps two, because a landing there is
# permitted by a change record AND the policy version that approved it. Here there is no record,
# so a bare version number would be indistinguishable from the sibling's second trailer; naming
# the population in the key is what makes the basis readable without a second marker.
INERT_LANDING_POLICY_TRAILER: Final = "SDS-Inert-Landing-Policy"

# Refusals that leave NO RECORD, because nothing happened and each can be tried again.
INERT_MERGE_NOT_ADMISSIBLE: Final = "inert_merge_not_admissible"
INERT_MERGE_REFUSED_BY_REMOTE: Final = "inert_merge_refused_by_remote"
INERT_MERGE_HEAD_MOVED: Final = "inert_merge_head_moved"

# Recorded on the one ambiguous outcome: the remote refused and the confirming read also failed,
# so a landing cannot be ruled out. The same reason code the sibling writes, because the row it
# is written into is the same row and one column may not carry two vocabularies.
MERGE_REFUSED_BY_REMOTE: Final = "merge_refused_by_remote"


class InertPullRequestGateway(EstateReadGateway, Protocol):
    """Everything the cascade reads, plus the two calls that change anything.

    **THE LANDING IS NAMED `merge`, DELIBERATELY, AND THAT IS NOT A DETAIL.** This repository's
    merge guard finds a landing by the REST path spelled in a file or by an attribute call named
    `merge`, and a landing performed through an injected gateway spells neither -- so a module in
    this shape can land pull requests and be invisible to the one control that lists every file
    that does. Naming the act with the spelling the guard reads is what makes this file's entry in
    that list real rather than nominal, and it is why the entry can be taken openly, which is the
    only way ADR-0020 permits the prohibition to be lifted at all.
    """

    def merge(
        self, *, repository: str, number: int, head_sha: str, commit_message: str
    ) -> MergeOutcome: ...


class GitHubInertPullRequests(GitHubEstatePullRequests):
    """The real gateway: the sibling's client, with the landing under the name above.

    Composition would have meant five delegating methods and a second place for the read surface
    to drift; there is exactly one behavioural difference between the two lanes' use of GitHub,
    and it is the name. Nothing is overridden.
    """

    def merge(
        self, *, repository: str, number: int, head_sha: str, commit_message: str
    ) -> MergeOutcome:
        return self.submit_merge(
            repository=repository,
            number=number,
            head_sha=head_sha,
            commit_message=commit_message,
        )


@dataclass(frozen=True)
class InertMergeCommand:
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


def land_inert_pull_request(
    session: Session,
    command: InertMergeCommand,
    gateway: InertPullRequestGateway,
    landing_source: EstateLandingSource,
    policy_source: InertLandingPolicySource,
    *,
    enabled: bool,
    credentials_configured: bool,
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
            policy_source,
            enabled=enabled,
            credentials_configured=credentials_configured,
        )
        session.commit()
        return record
    except Exception:
        session.rollback()
        raise


def _land(
    session: Session,
    command: InertMergeCommand,
    gateway: InertPullRequestGateway,
    landing_source: EstateLandingSource,
    policy_source: InertLandingPolicySource,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> EstatePrMerge:
    _authorize_actor(command.actor)
    repository = command.repository.lower()

    # SERIALISE ON THE REPOSITORY before anything is read, under the SAME key the sibling uses.
    # One table, one lock discipline: the rows both lanes reason about are ones that may not exist
    # yet, which `FOR UPDATE` cannot lock, and two lock namespaces over one table would leave two
    # requests each reading an absence and each acting on it. The two populations cannot overlap,
    # so the shared key costs no contention.
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

    admission = inert_landing_admission(
        session,
        repository,
        command.pr_number,
        landing_source,
        policy_source,
        gateway,
        enabled=enabled,
        credentials_configured=credentials_configured,
    )
    if not admission.satisfied:
        # No record: nothing was acted on, and consuming this pull request's one row here would
        # refuse every later legitimate attempt. The reasons are already served by the read
        # surface; they are named in the message rather than in a structured field, because
        # `DomainError` carries a closed set of attributes.
        raise DomainError(
            INERT_MERGE_NOT_ADMISSIBLE,
            "this pull request may not be landed: " + ", ".join(admission.refusals),
            "read the landing-admission answer for every term that is unmet",
        )
    if admission.head_sha != command.expected_head_sha:
        # The pull request moved between the answer the caller read and this call. Nothing is
        # recorded, because nothing happened and the caller can simply re-read: the update bot
        # rebasing its own branch is the ordinary cause, and the freshness term will then pass on
        # a head somebody has actually evaluated.
        raise DomainError(
            INERT_MERGE_HEAD_MOVED,
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
    command: InertMergeCommand,
    gateway: InertPullRequestGateway,
    admission: InertLandingAdmission,
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
        raise DomainError(INERT_MERGE_NOT_ADMISSIBLE, "the head is not identified", None)

    try:
        outcome = gateway.merge(
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
            # finding.
            raise DomainError(
                INERT_MERGE_REFUSED_BY_REMOTE,
                f"the landing was not attempted: {gateway_failure_detail(error)}",
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
                INERT_MERGE_REFUSED_BY_REMOTE,
                f"the remote refused to land the pull request: {gateway_failure_detail(error)}",
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


def _trailers(admission: InertLandingAdmission) -> str:
    """The permission, written into the artifact the estate's ledger will read.

    Only values that stay true, and nothing dated: the ledger freezes every string a landing
    carries at the first observation of it, so a body that named a count or a moment would make a
    later pass over an unchanged landing conflict with itself.

    The version is the POLICY DOCUMENT's, not the block's -- one number covers both populations,
    so a revision moving only the deploying half re-stamps what a landing here is attributed to.
    That follows from there being one holder of the rule; ADR-0038 records it so a ledger reader
    does not assume the number tracks the rule it names.
    """
    return f"{INERT_LANDING_POLICY_TRAILER}: {admission.policy_version}"


def _landed_after_all(
    gateway: InertPullRequestGateway, admission: InertLandingAdmission
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

    Not the worker, for the reason both sibling paths give: a runner asking for its own work to be
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
    command: InertMergeCommand,
    admission: InertLandingAdmission,
    head_sha: str,
    *,
    status: str,
    reason_code: str | None,
    merge_commit_sha: str | None = None,
    github_status: int | None = None,
) -> EstatePrMerge:
    """Write the outcome into the table both lanes share.

    `change_record_id` IS LEFT NULL, and that is what a row from this lane looks like: there is no
    record here and there cannot be one. It is not a discriminator anything reads -- the estate's
    ledger classifies a landing from the commit's trailer, not from this table -- so a column
    added to carry that distinction would have no reader, which is the dead-knob defect this
    repository has paid for before. What discriminates in the event stream is the ACTION, which
    names this lane.
    """
    record = EstatePrMerge(
        repository=admission.repository,
        pr_number=admission.pr_number,
        head_sha=head_sha,
        status=status,
        reason_code=reason_code,
        merge_commit_sha=merge_commit_sha,
        github_status=github_status,
        change_record_id=None,
        policy_version=admission.policy_version,
        idempotency_key=command.idempotency_key,
    )
    session.add(record)
    session.flush()
    event = Event(
        actor_id=command.actor.actor_id,
        action=f"inert_pr_merge.{status}",
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
