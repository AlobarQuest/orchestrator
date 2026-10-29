"""Watch the inert lane's recent landings, and act on one that went bad. Landing Part 3a.

Two entry points, one population. `landing_watch` reads: it judges each watched landing and reports
each record. `act_on_landing` acts on one landing, once per judgment: a `flaky_retry` reruns the
failing runs and records `rerun_requested`; a `bad` plans the revert and opens it (`revert_opened`),
or records why it could not (`refused_overlap`, `revert_failed`). Every other verdict acts on
nothing. The judgment is made again inside the act's transaction, never taken from the caller.

## Which landings are watched

A landing is this lane's when its `estate_pr_merge` row's event is `inert_pr_merge.merged` or
`inert_pr_merge.already_merged` (the action names the lane; the table is shared). A revert landing
is never watched: a row whose pull request some record names as its revert is left out, so a revert
is never judged and never reverted. A landing without a record must be the update bot's (its author
and a `dependabot/<ecosystem>/` branch, read from the pull request), so a fork's upstream sync is
never watched either.

A landing stays in the watch for `WATCH_HOURS` after it landed, and for as long after that as its
record keeps it there: `revert_opened` until it moves, and every other status for `WATCH_HOURS`
after its record last changed. So a `clean` landing leaves when its window closes, and so does one
whose rerun passed (its record stays `rerun_requested`, and nothing moves it) or never reported;
a revert stays until it has landed and been seen landed; and a refusal is reported for two days.

## Every call is bounded by the inert lane's grant term

Both entry points read through the Dispatch App, and the act writes through it. Each asks the member
record's `dependency_updates` grant for the repository first, so a repository the record does not
grant is never read or written: the watch reports it unreadable by that refusal's name, and the act
refuses it.

## Once per judgment

The act takes the repository's lock, then reads the record under `FOR UPDATE`, so two requests take
turns. What it may do follows from the record: a rerun moves the judgment to `pending` until the
rerun reports, and a revert moves the record past the judgment. So a repeated request acts on
nothing. Each act also writes one `Event` under the caller's key; a repeated key answers from it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext
from orchestrator.persistence.models import EstatePrMerge, Event, LandingRevert
from orchestrator.services.execution.target_grant import (
    DEPENDENCY_UPDATES_SERVICE,
    MemberRecordGrants,
)
from orchestrator.services.landing.inert_pr_merge import INERT_LANDING_EVENT_PREFIX
from orchestrator.services.landing.interfaces import (
    UPDATE_BOT_LOGIN,
    EstateGatewayError,
    EstatePullRequest,
    RevertActGateway,
    gateway_failure_detail,
)
from orchestrator.services.landing.lane_act import authorize_actor, lock_repository
from orchestrator.services.landing.revert_build import (
    REVERT_TOUCHED_NOTHING,
    Overlap,
    RevertOf,
    open_revert,
    plan_revert,
)
from orchestrator.services.landing.revert_record import REVERT_OPENED
from orchestrator.services.landing.revert_watch import (
    BAD,
    FLAKY_RETRY,
    UNREADABLE,
    WATCH_HOURS,
    Verdict,
    judge,
)
from orchestrator.services.landing.terms import (
    LANDING_REPOSITORY_NOT_GRANTED,
    UpdateBumps,
    ecosystem_of,
    grant_term,
)
from orchestrator.services.membership.holds import Bump, from_version_of

# Refusals. Nothing is read, written or recorded under any of them.
LANDING_REVERT_LANE_OFF: Final = "landing_revert_lane_off"
LANDING_REVERT_NOT_WATCHED: Final = "landing_revert_not_watched"
# Every rerun the act asked for was refused, so nothing was recorded and the next pass asks again.
LANDING_REVERT_RERUN_REFUSED: Final = "landing_revert_rerun_refused"
# The record is not in the status the caller read: someone acted between the read and the request.
LANDING_REVERT_RECORD_MOVED: Final = "landing_revert_record_moved"

# The record statuses, as the act writes them.
RERUN_REQUESTED: Final = "rerun_requested"
REFUSED_OVERLAP: Final = "refused_overlap"
REVERT_FAILED: Final = "revert_failed"

# The record status that keeps a landing in the watch however old it is: the revert pull request
# the lander must still land. Never `rerun_requested`: a rerun that passed leaves its record there
# for good, and it would be re-judged through the App on every pass forever.
_ALWAYS_WATCHED: Final = frozenset({REVERT_OPENED})  # not-a-vocabulary: statuses

# The events a landing of this lane's writes, one per landed status.
_LANDED_ACTIONS: Final = (
    f"{INERT_LANDING_EVENT_PREFIX}.merged",
    f"{INERT_LANDING_EVENT_PREFIX}.already_merged",
)
_EVENT_PREFIX: Final = "landing_revert."
_LOCK: Final = "landing_revert"
_VERB: Final = "act on a landing that went bad"
_DEPENDABOT_BRANCH: Final = "dependabot/"

# Planning failures that will fail the same way next pass, so the record says so rather than the
# act asking again for two days: the merge changed nothing, or a tree too large to read whole.
_PLAN_FAILS_FOR_GOOD: Final = frozenset({REVERT_TOUCHED_NOTHING, "tree_response_invalid"})

_LIST_UNREADABLE: Final = (
    "the update bot's dependency list could not be read: nothing will be held, and the version "
    "is not barred from landing again"
)


@dataclass(frozen=True)
class WatchedLanding:
    """One watched landing: its verdict when it was judged, else its record's status.

    `verdict` is None when the record is past the rerun, which is then reported by `status` alone.
    `failed_runs` is the verdict's (run id, attempt) pairs, which a caller's key names.
    """

    repository: str
    pr_number: int
    merge_commit_sha: str | None
    landed_at: datetime
    verdict: str | None
    detail: str
    failed_job_names: tuple[str, ...]
    failed_runs: tuple[tuple[int, int], ...]
    status: str | None
    revert_pr_number: int | None
    hold_outcome: str | None


@dataclass(frozen=True)
class RevertCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    # The record's status the caller read (None: no record), as every act names what it read: the
    # record has no version of its own, and its status is what an act moves.
    expected_status: str | None


@dataclass(frozen=True)
class RevertActOutcome:
    """What the act judged and did. `acted` is False when the verdict asked for nothing."""

    repository: str
    pr_number: int
    verdict: str
    detail: str
    acted: bool
    status: str | None
    revert_pr_number: int | None
    replayed: bool


def landing_watch(session: Session, gateway: RevertActGateway) -> tuple[WatchedLanding, ...]:
    """Every watched landing, judged or reported, oldest first. Reads only; writes nothing."""
    grants = MemberRecordGrants(session)
    watched: list[WatchedLanding] = []
    for landing in _watched_landings(session):
        record = _record(session, landing.repository, landing.pr_number, lock=False)
        verdict: Verdict | None = None
        if not grant_term(grants, landing.repository, (DEPENDENCY_UPDATES_SERVICE,)).met:
            verdict = Verdict(UNREADABLE, (), None, LANDING_REPOSITORY_NOT_GRANTED)
        elif record is None:
            pull = _bump_pull(gateway, landing)
            if pull is None:
                continue
            verdict = pull if isinstance(pull, Verdict) else judge(gateway, landing, None)
        elif record.status == RERUN_REQUESTED:
            verdict = judge(gateway, landing, record)
        watched.append(_entry(landing, record, verdict))
    return tuple(watched)


def act_on_landing(
    session: Session,
    command: RevertCommand,
    gateway: RevertActGateway,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> RevertActOutcome:
    """Judge one watched landing again and act once on what the judgment asks for.

    Owns its transaction: commits what it recorded, or rolls back everything it did not finish.
    """
    try:
        outcome = _act(
            session,
            command,
            gateway,
            enabled=enabled,
            credentials_configured=credentials_configured,
        )
        session.commit()
        return outcome
    except Exception:
        session.rollback()
        raise


def _act(
    session: Session,
    command: RevertCommand,
    gateway: RevertActGateway,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> RevertActOutcome:
    authorize_actor(command.actor, _VERB)
    repository = command.repository.lower()
    _refuse_unless_permitted(session, repository, enabled and credentials_configured)
    lock_repository(session, _LOCK, repository)
    spent = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if spent is not None:
        return _replay(spent, command.idempotency_key)
    landing = next(iter(_watched_landings(session, repository, command.pr_number)), None)
    record = _record(session, repository, command.pr_number, lock=True)
    if landing is None:
        raise _not_watched(repository, command.pr_number)
    if (None if record is None else record.status) != command.expected_status:
        raise DomainError(
            LANDING_REVERT_RECORD_MOVED,
            f"the record is not {command.expected_status or 'absent'} as the caller read it",
            "re-read the landing watch and ask again",
        )
    if record is None:
        pull = _bump_pull(gateway, landing)
        if pull is None:
            raise _not_watched(repository, command.pr_number)
        if isinstance(pull, Verdict):
            return _outcome(landing, pull, None, acted=False)
    verdict = judge(gateway, landing, record)
    if verdict.kind == FLAKY_RETRY:
        return _rerun(session, command, gateway, landing, record, verdict)
    if verdict.kind == BAD and record is not None:
        return _revert(session, command, gateway, landing, record, verdict)
    return _outcome(landing, verdict, record, acted=False)


def _refuse_unless_permitted(session: Session, repository: str, lane_on: bool) -> None:
    """The lane's switch and App credentials, then its grant term: before anything is read."""
    if not lane_on:
        raise DomainError(
            LANDING_REVERT_LANE_OFF,
            "the inert lane may not act: it is switched off or has no App credentials",
            "switch the lane on and configure the App",
        )
    if not grant_term(MemberRecordGrants(session), repository, (DEPENDENCY_UPDATES_SERVICE,)).met:
        raise DomainError(
            LANDING_REPOSITORY_NOT_GRANTED,
            f"the member record does not grant dependency updates on {repository}",
            "a person grants the service on /review",
        )


def _rerun(
    session: Session,
    command: RevertCommand,
    gateway: RevertActGateway,
    landing: EstatePrMerge,
    record: LandingRevert | None,
    verdict: Verdict,
) -> RevertActOutcome:
    """Rerun each failing run once, then record the attempts each rerun is measured against.

    The parent is read before anything is rerun, so a rerun is never left without the record that
    makes its result count. A run whose rerun was refused is left out of the record, so the next
    pass judges it new and asks again.
    """
    merge_sha = landing.merge_commit_sha or ""
    if record is None:
        try:
            parent_sha = gateway.parent_of(repository=landing.repository, sha=merge_sha)
        except EstateGatewayError as error:
            return _outcome(landing, _unreadable(error), None, acted=False)
    asked: dict[str, int] = {}
    refused: list[str] = []
    for run_id, attempt in verdict.failed_runs:
        try:
            gateway.rerun_failed_jobs(repository=landing.repository, run_id=run_id)
        except EstateGatewayError as error:
            refused.append(f"run {run_id}: {gateway_failure_detail(error)}")
            continue
        asked[str(run_id)] = attempt
    if not asked:
        raise DomainError(
            LANDING_REVERT_RERUN_REFUSED,
            "no failing run could be rerun: " + "; ".join(refused),
            "nothing was recorded; the next pass asks again",
        )
    now = datetime.now(UTC)
    if record is None:
        record = LandingRevert(
            repository=landing.repository,
            pr_number=landing.pr_number,
            merge_commit_sha=merge_sha,
            parent_sha=parent_sha,
            failed_job_names=list(verdict.failed_job_names),
            failed_run_id=verdict.run_id,
            # Every write of this status names its runs: a record with none would read every
            # failure as a run not yet rerun, and rerun it every pass.
            rerun_attempts=asked,
            status=RERUN_REQUESTED,
            bumps=[],
            created_at=now,
            updated_at=now,
        )
        session.add(record)
    else:
        # A new dict, merged rather than replaced: an earlier run's attempt still decides whether
        # its failure survived the rerun asked for it.
        record.rerun_attempts = {**(record.rerun_attempts or {}), **asked}
        record.failed_job_names = sorted({*record.failed_job_names, *verdict.failed_job_names})
        record.updated_at = now
    record.detail = "; ".join(f"rerun refused for {reason}" for reason in refused) or None
    return _recorded(session, command, landing, record, verdict)


def _revert(
    session: Session,
    command: RevertCommand,
    gateway: RevertActGateway,
    landing: EstatePrMerge,
    record: LandingRevert,
    verdict: Verdict,
) -> RevertActOutcome:
    """Plan the revert and open it, or record why not.

    A read that fails before anything is written records nothing, and the next pass asks again;
    so does a plan read that fails for a reason that can clear. Once opening has begun, any failure
    is recorded `revert_failed` with its reason, never retried: opening is four writes, and a branch
    left by a failed one refuses the next attempt (422).
    """
    repository = landing.repository
    try:
        pull = gateway.read_pull_request(repository=repository, number=landing.pr_number)
    except EstateGatewayError as error:
        return _outcome(landing, _unreadable(error), record, acted=False)
    bumps = _bump_rows(UpdateBumps(repository, pull, gateway).bumps, pull.title)
    notes = [] if bumps is not None else [_LIST_UNREADABLE]
    try:
        plan = plan_revert(gateway, repository, record.merge_commit_sha, record.parent_sha)
    except EstateGatewayError as error:
        if error.code not in _PLAN_FAILS_FOR_GOOD:
            return _outcome(landing, _unreadable(error), record, acted=False)
        record.status = REVERT_FAILED
        notes.insert(0, f"the revert could not be planned: {gateway_failure_detail(error)}")
    else:
        if isinstance(plan, Overlap):
            record.status = REFUSED_OVERLAP
            notes.insert(
                0, "changed on the default branch since the landing: " + ", ".join(plan.paths)
            )
        else:
            try:
                number, head = open_revert(
                    gateway,
                    repository,
                    plan,
                    RevertOf(landing.pr_number, pull.title, verdict.failed_job_names),
                )
            except EstateGatewayError as error:
                record.status = REVERT_FAILED
                notes.insert(0, f"the revert was not opened: {gateway_failure_detail(error)}")
            else:
                record.status = REVERT_OPENED
                record.revert_pr_number = number
                record.revert_head_sha = head
    record.failed_job_names = list(verdict.failed_job_names)
    record.failed_run_id = verdict.run_id or record.failed_run_id
    record.bumps = bumps or []
    record.detail = "; ".join(notes) or None
    record.updated_at = datetime.now(UTC)
    return _recorded(session, command, landing, record, verdict)


def _bump_rows(bumps: tuple[Bump, ...] | None, title: str) -> list[dict[str, Any]] | None:
    """The record's `bumps`: each dependency the update moved, with the version it moved from.

    The from-version is read from a single update's title; a grouped update's title names none, so
    its entries carry None and `revert_hold` holds nothing for them (the never-again term still
    refuses their versions).
    """
    if bumps is None:
        return None
    from_version = from_version_of(title) if len(bumps) == 1 else None
    return [
        {
            "ecosystem": bump.ecosystem,
            "directory": bump.directory,
            "dependency": bump.dependency,
            "from_version": from_version,
            "to_version": bump.to_version,
        }
        for bump in bumps
    ]


def _watched_landings(
    session: Session, repository: str | None = None, pr_number: int | None = None
) -> list[EstatePrMerge]:
    """This lane's landings in the watch, oldest first; see the module docstring."""
    window = func.now() - timedelta(hours=WATCH_HOURS)
    is_record = and_(
        LandingRevert.repository == EstatePrMerge.repository,
        LandingRevert.pr_number == EstatePrMerge.pr_number,
    )
    conditions: list[ColumnElement[bool]] = [
        Event.action.in_(_LANDED_ACTIONS),
        ~exists().where(
            LandingRevert.repository == EstatePrMerge.repository,
            LandingRevert.revert_pr_number == EstatePrMerge.pr_number,
        ),
        or_(
            EstatePrMerge.created_at >= window,
            exists().where(
                is_record,
                or_(LandingRevert.status.in_(_ALWAYS_WATCHED), LandingRevert.updated_at >= window),
            ),
        ),
    ]
    if repository is not None:
        conditions.append(EstatePrMerge.repository == repository)
    if pr_number is not None:
        conditions.append(EstatePrMerge.pr_number == pr_number)
    return list(
        session.scalars(
            select(EstatePrMerge)
            .join(Event, Event.id == EstatePrMerge.event_id)
            .where(*conditions)
            .order_by(EstatePrMerge.created_at, EstatePrMerge.repository, EstatePrMerge.pr_number)
        )
    )


def _record(
    session: Session, repository: str, pr_number: int, *, lock: bool
) -> LandingRevert | None:
    query = select(LandingRevert).where(
        LandingRevert.repository == repository, LandingRevert.pr_number == pr_number
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.scalar(query)


def _bump_pull(
    gateway: RevertActGateway, landing: EstatePrMerge
) -> EstatePullRequest | Verdict | None:
    """The landed pull request when the update bot opened it as a bump; None when it is some other
    subject (a fork's sync), and an `unreadable` verdict when it could not be read."""
    try:
        pull = gateway.read_pull_request(repository=landing.repository, number=landing.pr_number)
    except EstateGatewayError as error:
        return _unreadable(error)
    if pull.author_login != UPDATE_BOT_LOGIN or not pull.head_ref.startswith(_DEPENDABOT_BRANCH):
        return None
    if ecosystem_of(pull.head_ref) is None:
        return None
    return pull


def _unreadable(error: EstateGatewayError) -> Verdict:
    return Verdict(UNREADABLE, (), None, gateway_failure_detail(error))


def _not_watched(repository: str, pr_number: int) -> DomainError:
    return DomainError(
        LANDING_REVERT_NOT_WATCHED,
        f"{repository}#{pr_number} is not a watched landing of the inert lane",
        "read GET /api/v1/landing-watch for the landings that are",
    )


def _entry(
    landing: EstatePrMerge, record: LandingRevert | None, verdict: Verdict | None
) -> WatchedLanding:
    """A judged landing reports its verdict; a record past the rerun reports itself."""
    names = verdict.failed_job_names if verdict is not None else ()
    if not names and record is not None:
        names = tuple(record.failed_job_names)
    if verdict is not None:
        detail = verdict.detail
    else:
        detail = (record.detail if record is not None else None) or ""
    return WatchedLanding(
        repository=landing.repository,
        pr_number=landing.pr_number,
        merge_commit_sha=landing.merge_commit_sha,
        landed_at=landing.created_at,
        verdict=None if verdict is None else verdict.kind,
        detail=detail,
        failed_job_names=names,
        failed_runs=() if verdict is None else verdict.failed_runs,
        status=None if record is None else record.status,
        revert_pr_number=None if record is None else record.revert_pr_number,
        hold_outcome=None if record is None else record.hold_outcome,
    )


def _outcome(
    landing: EstatePrMerge, verdict: Verdict, record: LandingRevert | None, *, acted: bool
) -> RevertActOutcome:
    return RevertActOutcome(
        repository=landing.repository,
        pr_number=landing.pr_number,
        verdict=verdict.kind,
        detail=verdict.detail if record is None or not acted else (record.detail or verdict.detail),
        acted=acted,
        status=None if record is None else record.status,
        revert_pr_number=None if record is None else record.revert_pr_number,
        replayed=False,
    )


def _recorded(
    session: Session,
    command: RevertCommand,
    landing: EstatePrMerge,
    record: LandingRevert,
    verdict: Verdict,
) -> RevertActOutcome:
    """Flush the record and write the act's event under the caller's key."""
    session.flush()
    outcome = _outcome(landing, verdict, record, acted=True)
    session.add(
        Event(
            actor_id=command.actor.actor_id,
            action=f"{_EVENT_PREFIX}{record.status}",
            subject_type="landing_revert",
            subject_id=record.id,
            payload={
                "repository": outcome.repository,
                "pr_number": outcome.pr_number,
                "verdict": outcome.verdict,
                "detail": outcome.detail,
                "status": outcome.status,
                "revert_pr_number": outcome.revert_pr_number,
                "merge_commit_sha": record.merge_commit_sha,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    session.flush()
    return outcome


def _replay(spent: Event, key: str) -> RevertActOutcome:
    """This request, already acted on: answer from its event. A key another act spent is refused."""
    if not spent.action.startswith(_EVENT_PREFIX) or not isinstance(spent.payload, dict):
        raise DomainError(
            "idempotency_conflict",
            "this idempotency key belongs to a different act",
            f"nothing uses {key} but an act on a landing that went bad",
        )
    payload: dict[str, Any] = spent.payload
    number, status = payload.get("revert_pr_number"), payload.get("status")
    return RevertActOutcome(
        repository=str(payload.get("repository") or ""),
        pr_number=int(payload.get("pr_number") or 0),
        verdict=str(payload.get("verdict") or ""),
        detail=str(payload.get("detail") or ""),
        acted=True,
        status=status if isinstance(status, str) else None,
        revert_pr_number=number if isinstance(number, int) else None,
        replayed=True,
    )
