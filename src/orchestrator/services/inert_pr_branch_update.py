"""The lane brings a pull request's head up to date with the base it has itself moved, in a
repository where landing on the default branch changes nothing already serving. ADR-0038 part 2.

**Why this exists at all, and why it is not optional.** The landing lane it accompanies requires
the head to be current with its base -- a TIGHTENING over the workflow it replaces, which required
nothing. But that condition, once required, is one the lane CREATES: a landing moves the base, so
every sibling pull request in the repository becomes behind it at that instant, and the next pass
refuses them all for a reason the previous pass caused. Nothing else in the estate resolves it. The
update bot rebases on its own schedule, which is weekly across most of these repositories, and the
one measured wait was 29 hours. So requiring freshness without this act is a strict degradation
over what it replaces, and the two ship together.

## What it writes down: an event, and NO row of its own

The landing keeps a row because its act cannot be retried: that row is unique per pull request with
no delete path, so recording an outcome bars the subject forever, which is right for *it landed* and
would be badly wrong here. This act is the opposite kind -- repeatable by design, because whenever
the base moves again it is right to do again.

So the durable trace is an event and nothing else, and the difference that makes it safe is WHAT
THE KEY NAMES. The caller's key is content-addressed over the head, and a successful update changes
the head, so the next legitimate update carries a different key and is never barred by this one. A
key bars only a repeat of the same request against the same head, which is exactly what a replay is.

## It serialises on the repository, and the reason is NOT the one the branch suggests

What must not be raced is not the branch -- the platform holds that itself, since the head is named
in the request and a head that moved is refused there. It is the KEY. Two concurrent requests
carrying one idempotency key both read no spent event, both act, and the loser's commit violates
the globally unique index -- an `IntegrityError`, which has no registered handler and so reaches the
caller as a bare HTTP 500 over an act that in fact happened twice.

The lock key is the one the sibling lane already uses, because it is per repository and the two
populations cannot overlap: each lane requires the opposite answer from the estate about the same
repository, and the estate gives one answer per repository. One repository, one branch-update lock,
whichever lane is asking.

## Every fact about this act is read from the composed answer

The permission, the head, the off-switch and the credential check all come off one cascade, so a
deployment that has not been told it may land anything cannot be made to touch a branch either --
by the same term, not by a second one somebody has to remember to write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services.estate_landing import EstateLandingSource
from orchestrator.services.estate_landing_admission import (
    EstateGatewayError,
    EstateReadGateway,
)
from orchestrator.services.inert_landing_admission import inert_landing_admission
from orchestrator.services.inert_landing_policy import InertLandingPolicySource
from orchestrator.services.lifecycle import ActorContext

INERT_BRANCH_UPDATE_ACTION: Final = "inert_pr_branch_update.updated"
INERT_BRANCH_UPDATE_SUBJECT: Final = "inert_pull_request"

# The composed answer does not name freshness as this pull request's sole remaining obstacle. The
# refusals it does name are carried in the message, because they are the answer to the only
# question the caller can act on.
INERT_BRANCH_UPDATE_NOT_QUALIFIED: Final = "inert_branch_update_not_qualified"

# The platform declined, or could not be reached. Nothing is recorded and nothing is barred: the
# next pass composes the answer again and asks again, which is the right behaviour for an act whose
# whole nature is that repeating it is harmless.
INERT_BRANCH_UPDATE_REFUSED_BY_REMOTE: Final = "inert_branch_update_refused_by_remote"

# The pull request moved between the answer the caller read and this call.
INERT_BRANCH_UPDATE_HEAD_MOVED: Final = "inert_branch_update_head_moved"


@dataclass(frozen=True)
class InertBranchUpdateOutcome:
    """What was done, named so the caller can print it and the response can carry it."""

    repository: str
    pr_number: int
    head_sha: str
    # WAS THIS ANSWERED FROM A SPENT KEY RATHER THAN ACTED ON? Reported because of what a replay
    # here actually means. The caller's key is content-addressed over the head, and a successful
    # update CHANGES the head -- so a second request carrying the same key is a request about a
    # branch that did not move. The platform answers 202 and does the work afterwards, so the one
    # realistic way to reach this path is that it accepted and did not deliver.
    #
    # Left unreported, that failure describes itself as success FOREVER: still behind, still
    # qualifying, same head, same key, replayed, printed as "updated", and never a finding.
    replayed: bool


class InertBranchUpdateGateway(EstateReadGateway, Protocol):
    """Everything the composed answer reads, plus the one call that changes anything."""

    def update_branch(self, *, repository: str, number: int, expected_head_sha: str) -> None: ...


@dataclass(frozen=True)
class InertBranchUpdateCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    # The head the caller read when it read the composed answer: the subject is a pull request in a
    # foreign system and has no version of ours, so its head is the value that moves and naming it
    # is the same claim every other mutation makes with `expected_version`.
    expected_head_sha: str


def update_inert_pull_request_branch(
    session: Session,
    command: InertBranchUpdateCommand,
    gateway: InertBranchUpdateGateway,
    landing_source: EstateLandingSource,
    policy_source: InertLandingPolicySource,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> InertBranchUpdateOutcome:
    """Own the transaction, because it writes the event that records the act.

    A flush alone returns a correct-looking answer while the row is discarded when the session
    closes, which would leave an act that really happened with no trace of it.
    """
    try:
        outcome = _update(
            session,
            command,
            gateway,
            landing_source,
            policy_source,
            enabled=enabled,
            credentials_configured=credentials_configured,
        )
        session.commit()
        return outcome
    except Exception:
        session.rollback()
        raise


def _update(
    session: Session,
    command: InertBranchUpdateCommand,
    gateway: InertBranchUpdateGateway,
    landing_source: EstateLandingSource,
    policy_source: InertLandingPolicySource,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> InertBranchUpdateOutcome:
    _authorize_actor(command.actor)
    repository = command.repository.lower()

    # BEFORE the spent-key lookup, or the lookup and the write straddle the window two concurrent
    # requests would both pass through. The subject is a row that may not exist yet, which is
    # exactly what a row lock cannot hold.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"estate_pr_branch_update:{repository}"},
    )

    spent = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if spent is not None:
        return _replay(command, spent)

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
    if not admission.branch_update_qualifies:
        raise DomainError(
            INERT_BRANCH_UPDATE_NOT_QUALIFIED,
            "this pull request's branch may not be brought up to date: "
            + ", ".join(admission.refusals),
            "read the landing-admission answer for every term that is unmet",
        )

    head_sha = admission.head_sha
    if not head_sha:
        # Unreachable through the cascade: a pull request that cannot be read refuses with a code
        # that is not one of the self-clearing ones, so the answer above has already declined.
        # Stated rather than assumed, because naming the head is the whole of the concurrency
        # control and a call without one would act on whatever has been pushed since.
        raise DomainError(INERT_BRANCH_UPDATE_NOT_QUALIFIED, "the head is not identified", None)

    if head_sha != command.expected_head_sha:
        raise DomainError(
            INERT_BRANCH_UPDATE_HEAD_MOVED,
            "the pull request's head is not the one the caller read",
            "re-read the landing-admission answer and ask again",
        )

    try:
        gateway.update_branch(
            repository=admission.repository,
            number=admission.pr_number,
            expected_head_sha=head_sha,
        )
    except EstateGatewayError as error:
        raise DomainError(
            INERT_BRANCH_UPDATE_REFUSED_BY_REMOTE,
            f"the branch was not brought up to date: {error.code}",
            "nothing was recorded; the next pass composes the answer again and may ask again",
        ) from error
    _record(session, command, admission.repository, admission.pr_number, head_sha)
    return InertBranchUpdateOutcome(
        repository=admission.repository,
        pr_number=admission.pr_number,
        head_sha=head_sha,
        replayed=False,
    )


def _subject_id(repository: str, pr_number: int) -> uuid.UUID:
    """The pull request's own identity, since there is no row of ours to point at.

    Derived from its URL rather than allocated, so the same pull request is the same subject on
    every pass without anything having to store the mapping.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{repository}/pull/{pr_number}")


def _replay(command: InertBranchUpdateCommand, spent: Event) -> InertBranchUpdateOutcome:
    """This exact request, already performed. Answer from the record and touch nothing.

    A KEY SPENT ON A DIFFERENT SUBJECT IS REFUSED rather than replayed, and the ACTION is part of
    what makes a subject different. The event key space is global and both lanes write into it, so
    without that clause a key spent by the other lane's branch update -- or by any other act in the
    system -- would be answered here as though this pull request had been brought up to date.
    """
    payload = spent.payload if isinstance(spent.payload, dict) else {}
    if (
        spent.action != INERT_BRANCH_UPDATE_ACTION
        or payload.get("repository") != command.repository.lower()
        or payload.get("pr_number") != command.pr_number
    ):
        raise DomainError(
            "idempotency_conflict",
            "this idempotency key belongs to a different act",
            "use a new idempotency key",
        )
    return InertBranchUpdateOutcome(
        repository=str(payload.get("repository")),
        pr_number=command.pr_number,
        head_sha=str(payload.get("head_sha")),
        replayed=True,
    )


def _record(
    session: Session,
    command: InertBranchUpdateCommand,
    repository: str,
    pr_number: int,
    head_sha: str,
) -> None:
    """The act, written down. AFTER the call, never before.

    A record written before a call that then fails is a lie, and this one is recoverable in the
    direction it can fail: an act whose event is lost leaves the branch up to date and the next
    pass simply finding nothing to do.
    """
    session.add(
        Event(
            actor_id=command.actor.actor_id,
            action=INERT_BRANCH_UPDATE_ACTION,
            subject_type=INERT_BRANCH_UPDATE_SUBJECT,
            subject_id=_subject_id(repository, pr_number),
            payload={
                "repository": repository,
                "pr_number": pr_number,
                "head_sha": head_sha,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    session.flush()


def _authorize_actor(actor: ActorContext) -> None:
    """SYSTEM only, for the reason both landing paths give.

    Not the worker, because a runner asking for its own work to be made landable is the runner
    attesting to its own compliance. And not a human either -- a person can bring a branch up to
    date themselves, and this exists for the case where nobody had to.
    """
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may bring a pull request's branch up to date",
            None,
        )
