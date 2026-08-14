"""The lane brings a pull request's head up to date with the base it has itself moved.
ADR-0019 Increment 6.

**This is the first thing the lane changes outside a landing, and saying so plainly is part of the
increment.** It is strictly smaller than the act it accompanies -- the landing rewrites a default
branch and starts a rollout on a running service, while this one brings the base branch's commits
into a topic branch that nothing serves and nobody reads -- but it is a write to a repository
nobody asked this process to write to, performed unattended, and that class has exactly one other
member.

## Why it exists at all

The freshness condition on landing is correct and is not in question: required checks on these
repositories are deliberately not gated on being up to date, so a check can be green against a
head that is behind its base, and squashing that head produces a tree nothing has executed. Where
landing changes something already serving, that tree is what starts serving.

But the lane CREATES that condition. A landing moves the base, so every sibling pull request in
the repository becomes behind it at that instant, and the next pass refuses them all for a reason
the previous pass caused. Nothing resolved it: measured, one pull request sat 29 hours behind
while three windows passed over it, and a night's four passes could only re-report the same two.

So the lane clears what the lane staled. That is the whole of the change -- the condition stands
untouched, and what moves is the branch rather than the rule.

## What it writes down: an event, and NO row of its own

The landing keeps a row because its act cannot be retried: that row is unique per pull request
with no delete path, so recording an outcome bars the subject forever, which is right for *it
landed* and would be badly wrong here. This act is the opposite kind -- repeatable by design,
because whenever the base moves again it is right to do again.

So the durable trace is an event and nothing else, and the difference that makes it safe is WHAT
THE KEY NAMES. The caller's key is content-addressed over the head, and a successful update
changes the head, so the next legitimate update carries a different key and is never barred by
this one. A key bars only a repeat of the same request against the same head, which is exactly
what a replay is.

The event's subject is the pull request itself, named by a `uuid5` over its URL, because there is
no row of ours to point at and inventing one would be inventing the permanence this act must not
have. That construction is used elsewhere in this repository for the same reason.

## It serialises on the repository, and the reason is NOT the one the branch suggests

A first version of this module argued that no lock was needed: what must not be raced is the
branch, and the platform holds that itself, since the head is named in the request and a head that
moved is refused there. **That is true of the branch and false of the KEY**, which is the race that
matters. Two concurrent requests carrying one idempotency key both read no spent event, both act,
and the loser's commit violates the unique index -- an `IntegrityError`, which has no registered
handler and so reaches the caller as a bare HTTP 500 over an act that in fact happened twice.

So it takes the same advisory lock its sibling does, for a different reason and over a row that
may not exist yet, which is what a row lock cannot cover. It is released with the transaction
whichever way that ends.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.clock import Clock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services.change_record import ChangeRecordSource
from orchestrator.services.estate_landing import EstateLandingSource
from orchestrator.services.estate_landing_admission import (
    EstateGatewayError,
    EstateReadGateway,
    estate_landing_admission,
)
from orchestrator.services.lifecycle import ActorContext

BRANCH_UPDATE_ACTION: Final = "estate_pr_branch_update.updated"
BRANCH_UPDATE_SUBJECT: Final = "estate_pull_request"

# The composed answer does not name freshness as this pull request's sole remaining obstacle. The
# refusals it does name are carried in the message, because they are the answer to the only
# question the caller can act on.
BRANCH_UPDATE_NOT_QUALIFIED: Final = "estate_branch_update_not_qualified"

# The platform declined, or could not be reached. Nothing is recorded and nothing is barred: the
# next pass composes the answer again and asks again, which is the right behaviour for an act
# whose whole nature is that repeating it is harmless.
BRANCH_UPDATE_REFUSED_BY_REMOTE: Final = "estate_branch_update_refused_by_remote"

# The pull request moved between the answer the caller read and this call.
BRANCH_UPDATE_HEAD_MOVED: Final = "estate_branch_update_head_moved"


@dataclass(frozen=True)
class BranchUpdateOutcome:
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
    # qualifying, same head, same key, replayed, printed as "updated", and never a finding. The
    # flag is what lets the caller say "asked before, still behind" instead.
    replayed: bool


class EstateBranchUpdateGateway(EstateReadGateway, Protocol):
    """Everything the composed answer reads, plus the one call that changes anything."""

    def update_branch(self, *, repository: str, number: int, expected_head_sha: str) -> None: ...


@dataclass(frozen=True)
class EstateBranchUpdateCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    # The head the caller read when it read the composed answer, for the reason its sibling states:
    # the subject is a pull request in a foreign system and has no version of ours, so its head is
    # the value that moves and naming it is the same claim every other mutation makes.
    expected_head_sha: str


def update_estate_pull_request_branch(
    session: Session,
    command: EstateBranchUpdateCommand,
    gateway: EstateBranchUpdateGateway,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    *,
    enabled: bool,
    credentials_configured: bool,
    clock: Clock | None = None,
) -> BranchUpdateOutcome:
    """Compose the landing answer, and act only on what it says.

    **The permission is READ OFF THE SAME CASCADE the landing uses, and that is what makes the
    off-switch and the credential check hold here for free.** `landing_not_enabled` and
    `landing_app_credentials_missing` are refusals like any other and are not among the ones that
    clear themselves, so a deployment that has not been told it may land anything cannot be made
    to touch a branch either -- by the same term, not by a second one somebody has to remember to
    write.

    It OWNS its transaction, because it writes the event that records the act. A flush alone
    returns a correct-looking answer while the row is discarded when the session closes, which
    would leave an act that really happened with no trace of it.
    """
    try:
        outcome = _update(
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
        return outcome
    except Exception:
        session.rollback()
        raise


def _update(
    session: Session,
    command: EstateBranchUpdateCommand,
    gateway: EstateBranchUpdateGateway,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    *,
    enabled: bool,
    credentials_configured: bool,
    clock: Clock | None,
) -> BranchUpdateOutcome:
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
    if not admission.branch_update_qualifies:
        raise DomainError(
            BRANCH_UPDATE_NOT_QUALIFIED,
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
        raise DomainError(BRANCH_UPDATE_NOT_QUALIFIED, "the head is not identified", None)

    if head_sha != command.expected_head_sha:
        # The pull request moved between the answer the caller read and this call. Nothing is
        # recorded and nothing is barred: the update bot rewriting its own branch is the ordinary
        # cause, and the next pass reads the new head and asks again about that one.
        raise DomainError(
            BRANCH_UPDATE_HEAD_MOVED,
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
            BRANCH_UPDATE_REFUSED_BY_REMOTE,
            f"the branch was not brought up to date: {error.code}",
            "nothing was recorded; the next pass composes the answer again and may ask again",
        ) from error
    _record(session, command, admission.repository, admission.pr_number, head_sha)
    return BranchUpdateOutcome(
        repository=admission.repository,
        pr_number=admission.pr_number,
        head_sha=head_sha,
        replayed=False,
    )


def _subject_id(repository: str, pr_number: int) -> uuid.UUID:
    """The pull request's own identity, since there is no row of ours to point at.

    Derived from its URL rather than allocated, so the same pull request is the same subject on
    every pass without anything having to store the mapping. Inventing a row to own a real id
    would be inventing the permanence this act is careful not to have.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{repository}/pull/{pr_number}")


def _replay(command: EstateBranchUpdateCommand, spent: Event) -> BranchUpdateOutcome:
    """This exact request, already performed. Answer from the record and touch nothing.

    A KEY SPENT ON A DIFFERENT SUBJECT IS REFUSED rather than replayed. Both are reached through
    one globally unique column, so an operator who copies a request and edits only the number
    would otherwise be told that the pull request they named had been brought up to date, when
    what happened was that a different one had.
    """
    payload = spent.payload if isinstance(spent.payload, dict) else {}
    if (
        spent.action != BRANCH_UPDATE_ACTION
        or payload.get("repository") != command.repository.lower()
        or payload.get("pr_number") != command.pr_number
    ):
        raise DomainError(
            "idempotency_conflict",
            "this idempotency key belongs to a different act",
            "use a new idempotency key",
        )
    return BranchUpdateOutcome(
        repository=str(payload.get("repository")),
        pr_number=command.pr_number,
        head_sha=str(payload.get("head_sha")),
        replayed=True,
    )


def _record(
    session: Session,
    command: EstateBranchUpdateCommand,
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
            action=BRANCH_UPDATE_ACTION,
            subject_type=BRANCH_UPDATE_SUBJECT,
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
    """SYSTEM only, for the reason the landing gives.

    Not the worker, because a runner asking for its own work to be made landable is the runner
    attesting to its own compliance. Not a human, because a person can press the button on the
    pull request themselves and this exists for the case where nobody had to.
    """
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may bring a pull request's branch up to date",
            None,
        )
