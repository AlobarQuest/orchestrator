"""Watch the landing lanes' recent landings, and act on one that went bad. Landing Part 3.

Two entry points, one population. `landing_watch` reads: it judges each watched landing and reports
each record. `act_on_landing` acts on one landing, once per judgment: a `flaky_retry` reruns each
failed watched job by its own id (never a run's failed jobs, which include jobs the watch never
judged) and records `rerun_requested`; a `bad` plans the revert and opens it (`revert_opened`),
or records why it could not (`refused_overlap`, `revert_failed`). Every other verdict acts on
nothing. The judgment is made again inside the act's transaction, never taken from the caller.

## Which landings are watched

A landing is watched when its `estate_pr_merge` row's event is `merged` or `already_merged` by the
inert lane (`inert_pr_merge.*`, Part 3a) or by the estate lane, whose landings redeploy
(`estate_pr_merge.*`, Part 3b): the action names the lane; the table is shared. A revert landing
is never watched: a row whose pull request some record names as its revert is left out, so a revert
is never judged and never reverted. A landing without a record must be the update bot's (its author
and a `dependabot/<ecosystem>/` branch, read from the pull request), so a fork's upstream sync is
never watched either.

A landing stays in the watch for `WATCH_HOURS` after it landed, and for as long after that as its
record keeps it there: `revert_opened`, `refused_overlap` and `revert_failed` until they move;
`revert_landed` while any of its bumps' dependencies is held in the member record (a hold it placed
or one a person placed, matched by the hold matcher's identity, `holds.holding`); and every other
status for `WATCH_HOURS` after its record last changed. So a `clean` landing leaves when its window
closes, and so does one whose rerun passed (its record stays `rerun_requested`, and nothing moves
it) or never reported; a revert stays until it has landed and been seen landed, and then while the
dependency is held, so its alert stays open as long as the hold; and a refused or failed revert
stays, since the default branch may still be broken and only a person clears it. None of these
reads anything through the App: only a landing with no record or a `rerun_requested` one is
judged.

## A landing that redeploys (Landing Part 3b)

An estate-lane landing is judged by what production serves as well as by its checks
(`revert_watch`): its applications are the member record's `apps` for the repository, and their
readings are `revision_watcher`'s `production_revision` observations, read here from the store. A
`flaky_retry` on one reruns as on an inert landing. A `bad` one, for either reason, is refused
`landing_revert_needs_repoint` and nothing is written: the revert of a repository that redeploys
waits until production has been pointed back at the build before the bump, which this act does not
do. The watch reports the verdict and its reason meanwhile.

## Each landing is under its own lane

An inert landing is read and acted on through the inert lane's gateway under its switch, a
landing that redeploys through the estate lane's under the estate switch. The watch is refused
only when neither lane may act; a landing whose own lane is off is reported `unreadable` by the
refusal's name and read through nothing, and the act refuses it.

## Every call is bounded by the grant term

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
from orchestrator.persistence.models import (
    EstatePrMerge,
    Event,
    LandingRevert,
    Observation,
    ObservationSource,
)
from orchestrator.services.execution.target_grant import (
    DEPENDENCY_UPDATES_SERVICE,
    MemberRecordGrants,
)
from orchestrator.services.landing.inert_pr_merge import INERT_LANDING_EVENT_PREFIX
from orchestrator.services.landing.interfaces import (
    ESTATE_LANDING_EVENT_PREFIX,
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
from orchestrator.services.landing.revert_record import REVERT_LANDED, REVERT_OPENED
from orchestrator.services.landing.revert_watch import (
    BAD,
    FLAKY_RETRY,
    UNREADABLE,
    WATCH_HOURS,
    Production,
    ServedReading,
    Verdict,
    judge,
)
from orchestrator.services.landing.terms import (
    LANDING_REPOSITORY_NOT_GRANTED,
    UpdateBumps,
    ecosystem_of,
    grant_term,
)
from orchestrator.services.membership.holds import Bump, directory_of, from_version_of, holding
from orchestrator.services.membership.members import apps_for, holds_for

# Refusals. Nothing is read, written or recorded under any of them.
LANDING_REVERT_LANE_OFF: Final = "landing_revert_lane_off"
LANDING_REVERT_NOT_WATCHED: Final = "landing_revert_not_watched"
# Every rerun the act asked for was refused, so nothing was recorded and the next pass asks again.
LANDING_REVERT_RERUN_REFUSED: Final = "landing_revert_rerun_refused"
# The record is not in the status the caller read: someone acted between the read and the request.
LANDING_REVERT_RECORD_MOVED: Final = "landing_revert_record_moved"
# A landing that redeploys was judged bad: production must be pointed back before it is reverted.
LANDING_REVERT_NEEDS_REPOINT: Final = "landing_revert_needs_repoint"

# The record statuses, as the act writes them.
RERUN_REQUESTED: Final = "rerun_requested"
REFUSED_OVERLAP: Final = "refused_overlap"
REVERT_FAILED: Final = "revert_failed"

# The record statuses that keep a landing in the watch however old it is, none of them read through
# the App: the revert pull request the lander must still land, and the two that leave the default
# branch possibly broken with no revert, which only a person clears. Never `rerun_requested`: a
# rerun that passed leaves its record there for good, and it would be re-judged on every pass.
_ALWAYS_WATCHED: Final = frozenset(  # not-a-vocabulary: record statuses
    {REVERT_OPENED, REFUSED_OVERLAP, REVERT_FAILED}
)

# The events a watched landing writes: each lane's, one per landed status. The estate lane's are
# the landings that redeploy, which are judged by what production serves too.
_SERVED_ACTIONS: Final = (
    f"{ESTATE_LANDING_EVENT_PREFIX}.merged",
    f"{ESTATE_LANDING_EVENT_PREFIX}.already_merged",
)
_LANDED_ACTIONS: Final = (
    f"{INERT_LANDING_EVENT_PREFIX}.merged",
    f"{INERT_LANDING_EVENT_PREFIX}.already_merged",
    *_SERVED_ACTIONS,
)
# `revision_watcher`'s readings of what each application serves.
_WATCHER: Final = "revision_watcher"
_PRODUCTION_REVISION: Final = "production_revision"
_APPLICATION: Final = "service"
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
    `failed_runs` is the verdict's (run id, attempt) pairs, which a caller's key names. `reason` is
    a `bad` verdict's rule, `checks` or `served`.
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
    reason: str | None


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


@dataclass(frozen=True)
class RevertLane:
    """One landing lane as the watch and the act use it: its switch, and the App gateway its
    landings are read and written through."""

    gateway: RevertActGateway
    enabled: bool


def landing_watch(
    session: Session,
    *,
    inert: RevertLane,
    estate: RevertLane,
    credentials_configured: bool,
) -> tuple[WatchedLanding, ...]:
    """Every watched landing, judged or reported, oldest first. Reads only; writes nothing.

    Refused, before any read, when no lane may act. A landing whose own lane is switched off is
    reported `unreadable` by the refusal's name and read through nothing; one reported by its
    record's status alone reads nothing either way.
    """
    _refuse_unless_lane_on((inert.enabled or estate.enabled) and credentials_configured)
    grants = MemberRecordGrants(session)
    watched: list[WatchedLanding] = []
    for landing in _watched_landings(session):
        record = _record(session, landing.repository, landing.pr_number, lock=False)
        redeploys = _redeploys(session, landing)
        lane = estate if redeploys else inert
        verdict: Verdict | None = None
        if not grant_term(grants, landing.repository, (DEPENDENCY_UPDATES_SERVICE,)).met:
            verdict = Verdict(UNREADABLE, (), None, LANDING_REPOSITORY_NOT_GRANTED)
        elif record is not None and record.status != RERUN_REQUESTED:
            pass  # past the rerun: reported by its record's status, reading nothing
        elif not lane.enabled:
            verdict = Verdict(UNREADABLE, (), None, LANDING_REVERT_LANE_OFF)
        elif record is None:
            pull = _bump_pull(lane.gateway, landing)
            if pull is None:
                continue
            verdict = (
                pull
                if isinstance(pull, Verdict)
                else _judgment(session, lane.gateway, landing, None, redeploys)
            )
        else:
            verdict = _judgment(session, lane.gateway, landing, record, redeploys)
        watched.append(_entry(landing, record, verdict))
    return tuple(watched)


def act_on_landing(
    session: Session,
    command: RevertCommand,
    *,
    inert: RevertLane,
    estate: RevertLane,
    credentials_configured: bool,
) -> RevertActOutcome:
    """Judge one watched landing again and act once on what the judgment asks for, through its
    own lane's gateway and under its own lane's switch.

    Owns its transaction: commits what it recorded, or rolls back everything it did not finish.
    """
    try:
        outcome = _act(
            session,
            command,
            inert=inert,
            estate=estate,
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
    *,
    inert: RevertLane,
    estate: RevertLane,
    credentials_configured: bool,
) -> RevertActOutcome:
    authorize_actor(command.actor, _VERB)
    repository = command.repository.lower()
    _refuse_unless_permitted(
        session, repository, (inert.enabled or estate.enabled) and credentials_configured
    )
    lock_repository(session, _LOCK, repository)
    spent = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if spent is not None:
        return _replay(spent, command, repository)
    landing = next(iter(_watched_landings(session, repository, command.pr_number)), None)
    record = _record(session, repository, command.pr_number, lock=True)
    if landing is None:
        raise _not_watched(repository, command.pr_number)
    redeploys = _redeploys(session, landing)
    lane = estate if redeploys else inert
    _refuse_unless_lane_on(lane.enabled)
    gateway = lane.gateway
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
    verdict = _judgment(session, gateway, landing, record, redeploys)
    if verdict.kind == FLAKY_RETRY:
        return _rerun(session, command, gateway, landing, record, verdict)
    if verdict.kind == BAD and redeploys:
        raise DomainError(
            LANDING_REVERT_NEEDS_REPOINT,
            f"{repository}#{command.pr_number} redeploys and was judged bad by its "
            f"{verdict.reason}: {verdict.detail}",
            "production must be pointed back at the build before the bump first; nothing is "
            "reverted here yet",
        )
    if verdict.kind == BAD and record is not None:
        return _revert(session, command, gateway, landing, record, verdict)
    return _outcome(landing, verdict, record, acted=False)


def _refuse_unless_lane_on(lane_on: bool) -> None:
    if not lane_on:
        raise DomainError(
            LANDING_REVERT_LANE_OFF,
            "the landing's lane may not act: it is switched off or has no App credentials",
            "switch the lane on and configure the App",
        )


def _refuse_unless_permitted(session: Session, repository: str, lane_on: bool) -> None:
    """The lane's switch and App credentials, then its grant term: before anything is read."""
    _refuse_unless_lane_on(lane_on)
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
    """Rerun each failed watched job once, by its own id, then record the attempts each rerun is
    measured against (per run) and the jobs rerun.

    Never a run's failed jobs: that would rerun a failed job the watch never judged too, and in a
    repository that redeploys one of those is the step that redeploys. The parent is read before
    anything is rerun, so a rerun is never left without the record that makes its result count. A
    job whose rerun was refused is left out of the record, so the next pass judges it new and asks
    again.
    """
    merge_sha = landing.merge_commit_sha or ""
    if record is None:
        try:
            parent_sha = gateway.parent_of(repository=landing.repository, sha=merge_sha)
        except EstateGatewayError as error:
            return _outcome(landing, _unreadable(error), None, acted=False)
    asked: dict[str, int] = {}
    rerun: set[str] = set()
    refused: list[str] = []
    for job in verdict.failed_jobs:
        try:
            gateway.rerun_job(repository=landing.repository, job_id=job.job_id)
        except EstateGatewayError as error:
            refused.append(f"{job.name} in run {job.run_id}: {gateway_failure_detail(error)}")
            continue
        asked[str(job.run_id)] = max(job.attempt, asked.get(str(job.run_id), 0))
        rerun.add(job.name)
    if not asked:
        raise DomainError(
            LANDING_REVERT_RERUN_REFUSED,
            "no failed job could be rerun: " + "; ".join(refused),
            "nothing was recorded; the next pass asks again",
        )
    now = datetime.now(UTC)
    if record is None:
        record = LandingRevert(
            repository=landing.repository,
            pr_number=landing.pr_number,
            merge_commit_sha=merge_sha,
            parent_sha=parent_sha,
            failed_job_names=sorted(rerun),
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
        stored = record.rerun_attempts or {}
        record.rerun_attempts = {
            **stored,
            **{run: max(attempt, stored.get(run, 0)) for run, attempt in asked.items()},
        }
        record.failed_job_names = sorted({*record.failed_job_names, *rerun})
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

    The from-version and the directory are read from a single update's title (the block names
    neither): the directory is its ` in /<path>` suffix, else the root, and is used only where the
    block named none. A grouped update's title names neither, so its entries carry None for both
    and `revert_hold` holds nothing for them (the never-again term still refuses their versions,
    on any directory).
    """
    if bumps is None:
        return None
    single = len(bumps) == 1
    from_version = from_version_of(title) if single else None
    directory = directory_of(title) if single else None
    return [
        {
            "ecosystem": bump.ecosystem,
            "directory": bump.directory or directory,
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
    ]
    recent = or_(
        EstatePrMerge.created_at >= window,
        exists().where(
            is_record,
            or_(LandingRevert.status.in_(_ALWAYS_WATCHED), LandingRevert.updated_at >= window),
        ),
    )
    # A landed revert past its window is a candidate, kept only while its dependency is held.
    landed = LandingRevert.status == REVERT_LANDED
    if repository is not None:
        conditions.append(EstatePrMerge.repository == repository)
    if pr_number is not None:
        conditions.append(EstatePrMerge.pr_number == pr_number)
    rows = session.execute(
        select(EstatePrMerge, recent.label("recent"))
        .join(Event, Event.id == EstatePrMerge.event_id)
        .where(*conditions, or_(recent, exists().where(is_record, landed)))
        .order_by(EstatePrMerge.created_at, EstatePrMerge.repository, EstatePrMerge.pr_number)
    ).all()
    return [landing for landing, is_recent in rows if is_recent or _still_held(session, landing)]


def _redeploys(session: Session, landing: EstatePrMerge) -> bool:
    """Is this the estate lane's landing, whose landing redeploys? Read from its event's action."""
    action = session.scalar(select(Event.action).where(Event.id == landing.event_id))
    return action in _SERVED_ACTIONS


def _judgment(
    session: Session,
    gateway: RevertActGateway,
    landing: EstatePrMerge,
    record: LandingRevert | None,
    redeploys: bool,
) -> Verdict:
    """The landing's verdict: by its checks, and by what production served when it redeploys."""
    if not redeploys:
        return judge(gateway, landing, record)
    try:
        production = _production(session, gateway, landing)
    except EstateGatewayError as error:
        return _unreadable(error)
    return judge(gateway, landing, record, production)


def _production(session: Session, gateway: RevertActGateway, landing: EstatePrMerge) -> Production:
    """What the store says production served since the landing redeployed it.

    The applications are the member record's apps for the repository on its default branch. The
    landing time is the merge commit's, read from the remote: an `already_merged` landing is
    recorded after the merge, and a reading between the two is one that counts. Every reading of
    those applications received since then, and the watcher's heartbeat for each: when the ingest
    last accepted a post about it, a repeat of a stored reading included.
    """
    branch = gateway.default_branch_head(repository=landing.repository).name
    landed_at = gateway.committed_at(
        repository=landing.repository, sha=landing.merge_commit_sha or ""
    )
    applications = apps_for(session, landing.repository, branch)
    rows = session.execute(
        select(Observation.subject_reference, Observation.facts, Observation.received_at).where(
            Observation.source_system == _WATCHER,
            Observation.observation_type == _PRODUCTION_REVISION,
            Observation.subject_type == _APPLICATION,
            Observation.subject_reference.in_(applications),
            Observation.received_at >= landed_at,
        )
    ).all()
    heard = session.execute(
        select(ObservationSource.subject_reference, ObservationSource.last_seen_at).where(
            ObservationSource.source_system == _WATCHER,
            ObservationSource.subject_reference.in_(applications),
        )
    ).all()
    return Production(
        applications=applications,
        readings=tuple(
            ServedReading(application=name, serving=_serving(facts), received_at=received_at)
            for name, facts, received_at in rows
        ),
        heard={name: last_seen_at for name, last_seen_at in heard},
        landed_at=landed_at,
        now=session.scalar(select(func.now())) or datetime.now(UTC),
    )


def _serving(facts: object) -> str | None:
    """The commit a reading names; None for an `unstamped` one, which names none."""
    serving = facts.get("serving") if isinstance(facts, dict) else None
    return serving if isinstance(serving, str) and serving else None


def _still_held(session: Session, landing: EstatePrMerge) -> bool:
    """Is any dependency its landed revert took back out still held in the member record?"""
    record = _record(session, landing.repository, landing.pr_number, lock=False)
    if record is None or record.status != REVERT_LANDED:
        return False
    holds = holds_for(session, landing.repository)
    return any(
        holding(
            holds,
            Bump(
                ecosystem=str(entry.get("ecosystem") or ""),
                directory=entry.get("directory"),
                dependency=str(entry.get("dependency") or ""),
                to_version="",
            ),
        )
        for entry in record.bumps
        if entry.get("ecosystem") and entry.get("dependency")
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
        f"{repository}#{pr_number} is not a watched landing",
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
        reason=None if verdict is None else verdict.reason,
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


def _replay(spent: Event, command: RevertCommand, repository: str) -> RevertActOutcome:
    """This request, already acted on: answer from its event. A key another act spent, or one spent
    on another landing, is refused."""
    key = command.idempotency_key
    payload = spent.payload if isinstance(spent.payload, dict) else None
    if (
        not spent.action.startswith(_EVENT_PREFIX)
        or payload is None
        or payload.get("repository") != repository
        or payload.get("pr_number") != command.pr_number
    ):
        raise DomainError(
            "idempotency_conflict",
            "this idempotency key belongs to a different act or landing",
            f"nothing uses {key} but this act on this landing",
        )
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
