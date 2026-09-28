"""What the two Dependabot lanes' acts share: the order they happen in, and nothing that differs.

The estate lane lands where landing changes something already serving, and the inert lane lands
where it does not. Each lands a pull request and each brings a branch up to date, and for both acts
the two lanes run the same steps in the same order: the actor check, the repository lock, the
spent-key check, the composed answer, the head check, the act, and the record written after it.
Those steps live here, once.

What stays in each lane's own module is everything that makes the lanes different, and this module
takes it as arguments rather than knowing it:

* the remote call that lands a pull request, which each lane makes itself -- under the name the
  repository's guard against unattended landings reads, so each lane's own module is the file that
  guard lists, for its own recorded reason. This module never names that call; it runs the one it
  is handed;
* which answer admits the act, and so which terms it applies and which clock it reads;
* how the landing is performed and what the landing commit carries;
* whether the row names a change record;
* the refusal codes, the event action and the event subject;
* how a sibling Dependabot pull request's own answer is composed.

## One lock per repository per act, whichever lane is asking

Both landings take one advisory lock key and both branch updates take another, per repository. The
two populations cannot overlap -- each lane requires the opposite answer from the estate about the
same repository -- so sharing costs no contention, and two lock namespaces over one table would let
two requests each read the same absence and each act on it. The keys are the ones the estate lane
has always used, so an in-flight request under the previous code waits on the same lock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.clock import Clock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import EstatePrMerge, Event
from orchestrator.services.landing.branch_update_serialization import (
    SiblingAnswer,
    SiblingOutcome,
    branch_update_sibling_outcome,
)
from orchestrator.services.landing.interfaces import (
    MERGE_REFUSED_BY_REMOTE,
    NEVER_SENT,
    EstateGatewayError,
    EstateReadGateway,
    MergeOutcome,
    SiblingReadGateway,
    gateway_failure_detail,
)

# The advisory lock namespaces, one per act and shared by both lanes.
_LANDING_LOCK: Final = "estate_pr_merge"
_BRANCH_UPDATE_LOCK: Final = "estate_pr_branch_update"

# What each act says when the actor is not the system actor.
_LANDING_VERB: Final = "land a pull request"
_BRANCH_UPDATE_VERB: Final = "bring a pull request's branch up to date"


class LaneCommand(Protocol):
    """The request either lane's act is given."""

    @property
    def repository(self) -> str: ...
    @property
    def pr_number(self) -> int: ...
    @property
    def actor(self) -> ActorContext: ...
    @property
    def idempotency_key(self) -> str: ...
    @property
    def expected_head_sha(self) -> str: ...


class LaneAdmission(Protocol):
    """The part of either lane's composed answer the shared steps read."""

    @property
    def satisfied(self) -> bool: ...
    @property
    def refusals(self) -> tuple[str, ...]: ...
    @property
    def repository(self) -> str: ...
    @property
    def pr_number(self) -> int: ...
    @property
    def head_sha(self) -> str | None: ...
    @property
    def policy_version(self) -> int | None: ...
    @property
    def branch_update_qualifies(self) -> bool: ...


@dataclass(frozen=True)
class LandingLane:
    """What a lane's landing names that the other lane's does not."""

    # The event action is `<event_prefix>.<status>`, and it is what tells the two lanes' rows
    # apart: both write the one table, under one subject type.
    event_prefix: str
    not_admissible: str
    head_moved: str
    refused_by_remote: str


@dataclass(frozen=True)
class BranchUpdateLane:
    """What a lane's branch update names that the other lane's does not."""

    action: str
    subject_type: str
    not_qualified: str
    head_moved: str
    sibling_holding: str
    siblings_unreadable: str
    refused_by_remote: str


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


class BranchUpdateGateway(SiblingReadGateway, Protocol):
    """Everything the composed answer reads, plus the one call that changes anything."""

    def update_branch(self, *, repository: str, number: int, expected_head_sha: str) -> None: ...


def land_pull_request[Admission: LaneAdmission](
    session: Session,
    command: LaneCommand,
    lane: LandingLane,
    gateway: EstateReadGateway,
    *,
    admit: Callable[[str], Admission],
    remote_call: Callable[[Admission, str], MergeOutcome],
    change_record_id: Callable[[Admission], int | None] | None,
) -> EstatePrMerge:
    """Own the transaction, the way every request entry point in this repository does.

    A flush alone would return a correct-looking response while the row is discarded when the
    session closes -- leaving the record of an act that really happened absent, which is the one
    state the whole idempotency story depends on not reaching.

    `admit` composes the lane's answer for the lower-cased repository. `remote_call` performs the
    lane's landing of the head that answer named. `change_record_id` reads the change record the
    row names; a lane that has no change record passes None, and its row and event name none.
    """
    try:
        record = _land(
            session,
            command,
            lane,
            gateway,
            admit=admit,
            remote_call=remote_call,
            change_record_id=change_record_id,
        )
        session.commit()
        return record
    except Exception:
        session.rollback()
        raise


def _land[Admission: LaneAdmission](
    session: Session,
    command: LaneCommand,
    lane: LandingLane,
    gateway: EstateReadGateway,
    *,
    admit: Callable[[str], Admission],
    remote_call: Callable[[Admission, str], MergeOutcome],
    change_record_id: Callable[[Admission], int | None] | None,
) -> EstatePrMerge:
    _authorize_actor(command.actor, _LANDING_VERB)
    repository = command.repository.lower()

    # SERIALISE ON THE REPOSITORY before anything is read. There is no unit row to lock here, and
    # the two rules that must not be raced -- one row per pull request, one landing per repository
    # per window -- are both stated over rows that may not exist yet, which `FOR UPDATE` cannot
    # lock. Two requests would otherwise each read an absence and each act on it.
    _lock_repository(session, _LANDING_LOCK, repository)

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

    admission = admit(repository)
    if not admission.satisfied:
        # No record: nothing was acted on, and consuming this pull request's one row here would
        # refuse every later legitimate attempt. The reasons are already served by the read
        # surface; they are named in the message rather than in a structured field, because
        # `DomainError` carries a closed set of attributes.
        raise DomainError(
            lane.not_admissible,
            "this pull request may not be landed: " + ", ".join(admission.refusals),
            "read the landing-admission answer for every term that is unmet",
        )
    if admission.head_sha != command.expected_head_sha:
        # The pull request moved between the answer the caller read and this call. Nothing is
        # recorded, because nothing happened and the caller can simply re-read: the update bot
        # rebasing its own branch is the ordinary cause, and the freshness term will then pass on
        # a head somebody has actually evaluated.
        raise DomainError(
            lane.head_moved,
            "the pull request's head is not the one the caller read",
            "re-read the landing-admission answer and ask again",
        )
    return _act(
        session,
        command,
        lane,
        gateway,
        admission,
        remote_call=remote_call,
        change_record_id=None if change_record_id is None else change_record_id(admission),
        names_change_record=change_record_id is not None,
    )


def _act[Admission: LaneAdmission](
    session: Session,
    command: LaneCommand,
    lane: LandingLane,
    gateway: EstateReadGateway,
    admission: Admission,
    *,
    remote_call: Callable[[Admission, str], MergeOutcome],
    change_record_id: int | None,
    names_change_record: bool,
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
        raise DomainError(lane.not_admissible, "the head is not identified", None)

    try:
        outcome = remote_call(admission, head_sha)
    except EstateGatewayError as error:
        if error.code.startswith(NEVER_SENT):
            # NOTHING WAS SENT, so nothing can have landed. The reconciling read below would fail
            # the same way under the same outage and answer "we do not know", which would write a
            # permanent `refused` row -- silently barring an admissible pull request forever on a
            # transient credential failure, and reported by the caller as settled rather than as a
            # finding. The error code already carried the distinction and nothing read it.
            raise DomainError(
                lane.refused_by_remote,
                f"the landing was not attempted: {gateway_failure_detail(error)}",
                "retry once the credential can be minted",
            ) from error
        landed = _landed_after_all(gateway, admission)
        if landed is True:
            return _record(
                session,
                command,
                lane,
                admission,
                head_sha,
                change_record_id,
                names_change_record=names_change_record,
                status="already_merged",
                reason_code=None,
                github_status=error.status_code,
            )
        if landed is False:
            # CONFIRMED not landed, so nothing happened and this is retryable -- a required check
            # that is red today can be green tomorrow. No record.
            raise DomainError(
                lane.refused_by_remote,
                f"the remote refused to land the pull request: {gateway_failure_detail(error)}",
                "resolve what the remote objected to, then ask again",
            ) from error
        # The reconciling read ITSELF failed, so a landing cannot be ruled out. Terminal and
        # conservative: a retry would meet the same refusal no better informed, and the ledger
        # observes the landing independently and can settle it.
        return _record(
            session,
            command,
            lane,
            admission,
            head_sha,
            change_record_id,
            names_change_record=names_change_record,
            status="refused",
            reason_code=f"{MERGE_REFUSED_BY_REMOTE}:{error.code}",
            github_status=error.status_code,
        )

    return _record(
        session,
        command,
        lane,
        admission,
        head_sha,
        change_record_id,
        names_change_record=names_change_record,
        status="merged" if outcome.landed else "refused",
        reason_code=None if outcome.landed else MERGE_REFUSED_BY_REMOTE,
        merge_commit_sha=outcome.commit_sha,
        github_status=outcome.status_code,
    )


def _landed_after_all(gateway: EstateReadGateway, admission: LaneAdmission) -> bool | None:
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


def _record(
    session: Session,
    command: LaneCommand,
    lane: LandingLane,
    admission: LaneAdmission,
    head_sha: str,
    change_record_id: int | None,
    *,
    names_change_record: bool,
    status: str,
    reason_code: str | None,
    merge_commit_sha: str | None = None,
    github_status: int | None = None,
) -> EstatePrMerge:
    """Write the outcome into the table both lanes share.

    A lane with no change record leaves the column NULL and its event names none -- that is what a
    row from such a lane looks like, and it is not a discriminator anything reads: the estate's
    ledger classifies a landing from the commit's trailer, not from this table. What discriminates
    in the event stream is the ACTION, which names the lane.
    """
    record = EstatePrMerge(
        repository=admission.repository,
        pr_number=admission.pr_number,
        head_sha=head_sha,
        status=status,
        reason_code=reason_code,
        merge_commit_sha=merge_commit_sha,
        github_status=github_status,
        change_record_id=change_record_id,
        policy_version=admission.policy_version,
        idempotency_key=command.idempotency_key,
    )
    session.add(record)
    session.flush()
    payload: dict[str, object] = {
        "estate_pr_merge_record_id": str(record.id),
        "repository": admission.repository,
        "pr_number": admission.pr_number,
        "head_sha": head_sha,
        "status": status,
        "reason_code": reason_code,
        "merge_commit_sha": merge_commit_sha,
    }
    if names_change_record:
        payload["change_record_id"] = change_record_id
    payload["policy_version"] = admission.policy_version
    event = Event(
        actor_id=command.actor.actor_id,
        action=f"{lane.event_prefix}.{status}",
        subject_type="estate_pr_merge",
        subject_id=record.id,
        payload=payload,
        correlation_id=uuid.uuid4(),
        idempotency_key=f"{command.idempotency_key}:event",
    )
    session.add(event)
    session.flush()
    record.event_id = event.id
    session.flush()
    return record


def update_pull_request_branch[Admission: LaneAdmission](
    session: Session,
    command: LaneCommand,
    lane: BranchUpdateLane,
    gateway: BranchUpdateGateway,
    *,
    admit: Callable[[str], Admission],
    sibling_composer: Callable[[str], Callable[[int], SiblingAnswer]],
    clock: Clock | None,
) -> BranchUpdateOutcome:
    """Compose the lane's answer, and act only on what it says.

    **The permission is READ OFF THE SAME CASCADE the landing uses**, so the off-switch and the
    credential check hold here for free: `landing_not_enabled` and `landing_app_credentials_missing`
    are refusals like any other and are not among the ones that clear themselves, so a deployment
    that has not been told it may land anything cannot be made to touch a branch either.

    It OWNS its transaction, because it writes the event that records the act. A flush alone
    returns a correct-looking answer while the row is discarded when the session closes, which
    would leave an act that really happened with no trace of it.

    `admit` composes the lane's answer for the lower-cased repository, and `sibling_composer`
    builds, for the repository that answer names, the lane's composer of a sibling's own answer.
    """
    try:
        outcome = _update(
            session,
            command,
            lane,
            gateway,
            admit=admit,
            sibling_composer=sibling_composer,
            clock=clock,
        )
        session.commit()
        return outcome
    except Exception:
        session.rollback()
        raise


def _update[Admission: LaneAdmission](
    session: Session,
    command: LaneCommand,
    lane: BranchUpdateLane,
    gateway: BranchUpdateGateway,
    *,
    admit: Callable[[str], Admission],
    sibling_composer: Callable[[str], Callable[[int], SiblingAnswer]],
    clock: Clock | None,
) -> BranchUpdateOutcome:
    _authorize_actor(command.actor, _BRANCH_UPDATE_VERB)
    repository = command.repository.lower()

    # BEFORE the spent-key lookup, or the lookup and the write straddle the window two concurrent
    # requests would both pass through. THE KEY IS THE RACE, NOT THE BRANCH: the platform refuses a
    # head that moved, but two requests carrying one idempotency key would both read no spent
    # event, both act, and the loser's commit would violate the unique index -- an unhandled 500
    # over an act that happened twice.
    _lock_repository(session, _BRANCH_UPDATE_LOCK, repository)

    spent = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if spent is not None:
        return _replay(command, lane, spent)

    admission = admit(repository)
    if not admission.branch_update_qualifies:
        raise DomainError(
            lane.not_qualified,
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
        raise DomainError(lane.not_qualified, "the head is not identified", None)

    if head_sha != command.expected_head_sha:
        # The pull request moved between the answer the caller read and this call. Nothing is
        # recorded and nothing is barred: the update bot rewriting its own branch is the ordinary
        # cause, and the next pass reads the new head and asks again about that one.
        raise DomainError(
            lane.head_moved,
            "the pull request's head is not the one the caller read",
            "re-read the landing-admission answer and ask again",
        )

    # ADR-0045. Worked out here, inside this transaction and under the lock already held, and
    # never read off the served answer's copy of the fact.
    outcome = branch_update_sibling_outcome(
        session,
        repository=admission.repository,
        target_number=admission.pr_number,
        gateway=gateway,
        compose=sibling_composer(admission.repository),
        clock=clock,
    )
    if outcome is SiblingOutcome.WITHHOLD_SIBLING_HOLDING:
        raise DomainError(
            lane.sibling_holding,
            "another Dependabot pull request this lane has already edited is queued to land, "
            "and this branch is still Dependabot's",
            "the pull request ahead of it lands first; the next pass asks again",
        )
    if outcome is SiblingOutcome.WITHHOLD_SIBLINGS_UNREADABLE:
        raise DomainError(
            lane.siblings_unreadable,
            "it could not be established that no other edited branch is queued to land",
            "read the open pull requests and their commits; nothing was changed",
        )

    try:
        gateway.update_branch(
            repository=admission.repository,
            number=admission.pr_number,
            expected_head_sha=head_sha,
        )
    except EstateGatewayError as error:
        raise DomainError(
            lane.refused_by_remote,
            f"the branch was not brought up to date: {gateway_failure_detail(error)}",
            "nothing was recorded; the next pass composes the answer again and may ask again",
        ) from error
    _record_branch_update(
        session, command, lane, admission.repository, admission.pr_number, head_sha
    )
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


def _replay(command: LaneCommand, lane: BranchUpdateLane, spent: Event) -> BranchUpdateOutcome:
    """This exact request, already performed. Answer from the record and touch nothing.

    A KEY SPENT ON A DIFFERENT SUBJECT IS REFUSED rather than replayed, and the ACTION is part of
    what makes a subject different. The event key space is global and both lanes write into it, so
    without that clause a key spent by the other lane's branch update -- or by any other act in the
    system -- would be answered here as though this pull request had been brought up to date.
    """
    payload = spent.payload if isinstance(spent.payload, dict) else {}
    if (
        spent.action != lane.action
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


def _record_branch_update(
    session: Session,
    command: LaneCommand,
    lane: BranchUpdateLane,
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
            action=lane.action,
            subject_type=lane.subject_type,
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


def _lock_repository(session: Session, namespace: str, repository: str) -> None:
    """Hold every other request for this act on this repository until this transaction settles.

    An advisory lock rather than a row lock, because the rows the decision is about are the ones
    that do not exist yet. It is released with the transaction whichever way that ends.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{namespace}:{repository}"},
    )


def _authorize_actor(actor: ActorContext, verb: str) -> None:
    """SYSTEM only, for either lane and either act.

    Not the worker, because a runner asking for its own work to be landed, or made landable, is the
    runner attesting to its own compliance. And not a human either -- a person can do either
    themselves, and this exists for the case where nobody had to.
    """
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            f"only the orchestrator system actor may {verb}",
            None,
        )
