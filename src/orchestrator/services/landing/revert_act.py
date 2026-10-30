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
`flaky_retry` on one reruns as on an inert landing. A `bad` one, for either reason, is not reverted
straight away: `act_on_landing` refuses it `landing_revert_needs_repoint` and writes nothing,
because production must first serve the build before the bump. Three more entry points, each one
step the estate lander drives, one per pass:

1. `record_repoint` -- the lander names the images it is about to pin each application to. The
   landing is judged again here and must still be `bad`; every image must be the bump's parent's
   tag with a digest and the moving tag to restore. The record moves to `repointed` with
   `pinned_images` BEFORE the lander touches production, so every pin it makes has a record (and
   the lander's `landing-production-pinned` alert) behind it; a pin that then fails part-way is
   repeated by the next pass, and the record already says production may be pinned.
2. `act_on_landing` on a `repointed` record -- the lander has confirmed every application serves
   the parent -- opens the revert from the record, without judging again: production serves the
   parent by the SDS's own act now, so a fresh judgment would read `clean`. The revert pull
   request's number is written in the same transaction that opens it.
3. `record_restored` -- the revert landed (the estate merge path records `revert_landed` and the
   hold), the lander put the moving tag back and confirmed production serves the revert's merge
   commit: the record moves to `restored`. A restored revert has landed in every other read.

A record that pinned production stays in the watch until it is restored, whatever its status and
age, so the lander's pinned alert stays open until then.

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

import re
import uuid
from collections.abc import Mapping
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
from orchestrator.services.landing.revert_record import (
    REPOINTED,
    REVERT_LANDED,
    REVERT_OPENED,
    is_landed_revert,
    landed_revert,
)
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
# Landing Part 3b. A re-point asked of a landing that does not redeploy, of one not judged bad now,
# or naming images other than the bump's parent's; and a restore asked before the revert landed.
LANDING_REPOINT_NOT_SERVING: Final = "landing_repoint_not_serving"
LANDING_REPOINT_NOT_BAD: Final = "landing_repoint_not_bad"
LANDING_REPOINT_IMAGES_INVALID: Final = "landing_repoint_images_invalid"
LANDING_RESTORE_NOT_LANDED: Final = "landing_restore_not_landed"
# A pin confirmed on a record that is not `repointed`.
LANDING_PIN_NOT_REPOINTED: Final = "landing_pin_not_repointed"
# A revert opened, or a take-over recorded, from a `repointed` record whose pin is not confirmed:
# its tags may never have moved, so nothing is read from them yet.
LANDING_PIN_UNCONFIRMED: Final = "landing_pin_unconfirmed"
# The mark a confirmed pin puts on each image: the lander set every application's tag. Until it is
# there, a tag equal to its restore tag is a pin that never happened, never a person's restore.
PIN_CONFIRMED: Final = {"pinned": "yes"}

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
RESTORED: Final = "restored"
# The statuses production may be recorded restored from: the revert landed, or it never could and
# a person put the moving tags back by hand (the lander confirms the tags before it asks), or a
# person did so before the revert opened and so took the landing over: no revert is opened then.
_RESTORABLE: Final = frozenset(  # not-a-vocabulary: record statuses
    {REVERT_LANDED, REVERT_FAILED, REFUSED_OVERLAP, REPOINTED}
)
_TAKEN_OVER: Final = "restored by hand before the revert opened"
# The statuses a re-point may be recorded from: no record yet, or a rerun's.
_REPOINTABLE: Final = (None, RERUN_REQUESTED)
# An image a re-point names: the bump's parent's full commit as its tag, and the digest it resolved.
_FULL_SHA: Final = re.compile(r"[0-9a-f]{40}")
_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")
# The lanes, as the watch names them: the landing lane each watched landing came through.
ESTATE_LANE: Final = "estate"
INERT_LANE: Final = "inert"

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
    # Landing Part 3b. Which lane landed it (`estate` redeploys, `inert` does not), the bump's
    # parent (the record's, else a served `bad` verdict's), the images a re-point pinned, and the
    # revert's own merge commit once landed: what the estate lander needs to pin and restore.
    lane: str = INERT_LANE
    parent_sha: str | None = None
    pinned_images: dict[str, dict[str, str]] | None = None
    revert_merge_sha: str | None = None


@dataclass(frozen=True)
class RepointCommand:
    """The estate lander's statement that it is pointing each application at the bump's parent's
    image: `pinned_images` is `{application uuid: {tag, digest, restore_tag}}`."""

    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    expected_status: str | None
    pinned_images: Mapping[str, object]


@dataclass(frozen=True)
class PinnedCommand:
    """The estate lander set every pinned application's tag to the parent's."""

    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    expected_status: str


@dataclass(frozen=True)
class RestoreCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    # The record's status the caller read: only `revert_landed` is restored.
    expected_status: str


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
        elif redeploys and not lane.enabled:
            # Before the past-the-rerun case: a record of the estate lane drives writes to
            # production through its status, so a lane switched off serves none to act on.
            verdict = Verdict(UNREADABLE, (), None, LANDING_REVERT_LANE_OFF)
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
        watched.append(_entry(landing, record, verdict, redeploys))
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
    landing, record, repository = _locked_landing(
        session, command, (inert.enabled or estate.enabled) and credentials_configured
    )
    if isinstance(landing, RevertActOutcome):
        return landing
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
    if record is not None and record.status == REPOINTED and redeploys:
        # Production serves the parent by the SDS's own act now, so a fresh judgment would read
        # clean: the revert opens from the record that judged it bad -- once the pin is confirmed.
        _refuse_unless_pin_confirmed(record, repository, command.pr_number)
        return _revert(session, command, gateway, landing, record, _stored_verdict(record))
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


def record_repoint(
    session: Session,
    command: RepointCommand,
    *,
    inert: RevertLane,
    estate: RevertLane,
    credentials_configured: bool,
) -> RevertActOutcome:
    """Record that production is being pointed at the bump's parent's image (Landing Part 3b).

    The landing must redeploy, be judged `bad` again here, and the record must be absent or a
    rerun's, as the caller read it. Each named image must be the parent's tag with its digest and
    the moving tag to restore. Owns its transaction.
    """
    try:
        outcome = _repoint(session, command, estate, inert, credentials_configured)
        session.commit()
        return outcome
    except Exception:
        session.rollback()
        raise


def _repoint(
    session: Session,
    command: RepointCommand,
    estate: RevertLane,
    inert: RevertLane,
    credentials_configured: bool,
) -> RevertActOutcome:
    landing, record, repository = _locked_landing(
        session, command, (inert.enabled or estate.enabled) and credentials_configured
    )
    if isinstance(landing, RevertActOutcome):
        return landing
    if not _redeploys(session, landing):
        raise DomainError(
            LANDING_REPOINT_NOT_SERVING,
            f"{repository}#{command.pr_number} landed where landing redeploys nothing",
            "only a landing that redeploys is pointed back; an inert one is reverted directly",
        )
    _refuse_unless_lane_on(estate.enabled)
    status = None if record is None else record.status
    if status != command.expected_status or status not in _REPOINTABLE:
        raise DomainError(
            LANDING_REVERT_RECORD_MOVED,
            f"the record is {status or 'absent'}, not {command.expected_status or 'absent'} "
            "awaiting a re-point",
            "re-read the landing watch and ask again",
        )
    verdict = _judgment(session, estate.gateway, landing, record, True)
    parent = verdict.parent_sha or (None if record is None else record.parent_sha)
    if verdict.kind != BAD or parent is None:
        raise DomainError(
            LANDING_REPOINT_NOT_BAD,
            f"{repository}#{command.pr_number} is judged {verdict.kind} now: {verdict.detail}",
            "nothing is pointed back; the next pass judges it again",
        )
    images = _valid_images(command.pinned_images, parent)
    now = datetime.now(UTC)
    if record is None:
        record = LandingRevert(
            repository=landing.repository,
            pr_number=landing.pr_number,
            merge_commit_sha=landing.merge_commit_sha or "",
            parent_sha=parent,
            failed_job_names=[],
            # Not null in the table; a served `bad` names no run.
            failed_run_id=verdict.run_id or 0,
            status=REPOINTED,
            bumps=[],
            created_at=now,
        )
        session.add(record)
    record.status = REPOINTED
    record.pinned_images = images
    record.failed_job_names = list(verdict.failed_job_names) or list(record.failed_job_names)
    record.failed_run_id = verdict.run_id or record.failed_run_id
    record.detail = f"bad by what it {verdict.reason}: {verdict.detail}"
    record.updated_at = now
    return _recorded(session, _as_revert_command(command), landing, record, verdict)


def _valid_images(images: Mapping[str, object], parent: str) -> dict[str, dict[str, str]]:
    """The images a re-point names, each the parent's full commit as its tag, a digest, and the
    moving tag to put back; else refused, naming nothing it was given."""
    valid: dict[str, dict[str, str]] = {}
    for application, image in images.items():
        if not isinstance(application, str) or not application or not isinstance(image, Mapping):
            break
        tag, digest, restore = (image.get(key) for key in ("tag", "digest", "restore_tag"))
        if not (isinstance(tag, str) and isinstance(digest, str) and isinstance(restore, str)):
            break
        if tag.lower() != parent.lower() or not _FULL_SHA.fullmatch(tag.lower()):
            break
        if not _DIGEST.fullmatch(digest) or not restore or _FULL_SHA.fullmatch(restore.lower()):
            break
        valid[application] = {"tag": tag.lower(), "digest": digest, "restore_tag": restore}
    else:
        if valid:
            return valid
    raise DomainError(
        LANDING_REPOINT_IMAGES_INVALID,
        f"each application must be pinned to the parent {parent[:12]} by its full commit, with "
        "a sha256 digest and a moving tag to restore",
        "resolve the parent's image again and name it",
    )


def record_pinned(
    session: Session,
    command: PinnedCommand,
    *,
    inert: RevertLane,
    estate: RevertLane,
    credentials_configured: bool,
) -> RevertActOutcome:
    """Record that every pinned application's tag was set to the parent's (Landing Part 3b).

    Only a `repointed` record, as the caller read it; each image gains the confirmation mark. A
    re-point never carries the mark (`_valid_images` keeps three fields), so a tag on its restore
    tag before this is a pin that never happened. Owns its transaction.
    """
    try:
        outcome = _pinned(session, command, estate, inert, credentials_configured)
        session.commit()
        return outcome
    except Exception:
        session.rollback()
        raise


def _pinned(
    session: Session,
    command: PinnedCommand,
    estate: RevertLane,
    inert: RevertLane,
    credentials_configured: bool,
) -> RevertActOutcome:
    landing, record, repository = _locked_landing(
        session, command, (inert.enabled or estate.enabled) and credentials_configured
    )
    if isinstance(landing, RevertActOutcome):
        return landing
    _refuse_unless_lane_on(estate.enabled)
    status = None if record is None else record.status
    if status != command.expected_status:
        raise DomainError(
            LANDING_REVERT_RECORD_MOVED,
            f"the record is {status or 'absent'}, not {command.expected_status} as the caller "
            "read it",
            "re-read the landing watch and ask again",
        )
    if record is None or record.status != REPOINTED or not record.pinned_images:
        raise DomainError(
            LANDING_PIN_NOT_REPOINTED,
            f"{repository}#{command.pr_number} is not pointed back (the record is {status})",
            "only a re-pointed record's pin is confirmed",
        )
    record.pinned_images = {
        uuid: {**image, **PIN_CONFIRMED} for uuid, image in record.pinned_images.items()
    }
    record.updated_at = datetime.now(UTC)
    return _recorded(session, _as_revert_command(command), landing, record, _stored_verdict(record))


def record_restored(
    session: Session,
    command: RestoreCommand,
    *,
    inert: RevertLane,
    estate: RevertLane,
    credentials_configured: bool,
) -> RevertActOutcome:
    """Record that the moving tag is back and production serves the revert (Landing Part 3b).

    Only a landed revert that pinned production may be restored. The lander confirms what
    production serves before it asks; this side has no way to read it. Owns its transaction.
    """
    try:
        outcome = _restore(session, command, estate, inert, credentials_configured)
        session.commit()
        return outcome
    except Exception:
        session.rollback()
        raise


def _restore(
    session: Session,
    command: RestoreCommand,
    estate: RevertLane,
    inert: RevertLane,
    credentials_configured: bool,
) -> RevertActOutcome:
    landing, record, repository = _locked_landing(
        session, command, (inert.enabled or estate.enabled) and credentials_configured
    )
    if isinstance(landing, RevertActOutcome):
        return landing
    _refuse_unless_lane_on(estate.enabled)
    status = None if record is None else record.status
    if status != command.expected_status:
        raise DomainError(
            LANDING_REVERT_RECORD_MOVED,
            f"the record is {status or 'absent'}, not {command.expected_status} as the caller "
            "read it",
            "re-read the landing watch and ask again",
        )
    if record is None or record.status not in _RESTORABLE or record.pinned_images is None:
        raise DomainError(
            LANDING_RESTORE_NOT_LANDED,
            f"{repository}#{command.pr_number} has no pinned record that may be restored "
            f"(the record is {'absent' if record is None else record.status})",
            "production stays pinned until the revert lands",
        )
    now = datetime.now(UTC)
    if record.status == REPOINTED:
        _refuse_unless_pin_confirmed(record, repository, command.pr_number)
        record.detail = _TAKEN_OVER
    record.status = RESTORED
    record.restored_at = now
    record.updated_at = now
    verdict = _stored_verdict(record)
    return _recorded(session, _as_revert_command(command), landing, record, verdict)


def _locked_landing(
    session: Session,
    command: RevertCommand | RepointCommand | RestoreCommand | PinnedCommand,
    lane_on: bool,
) -> tuple[EstatePrMerge | RevertActOutcome, LandingRevert | None, str]:
    """The act's common start: the actor, the switch and grant, the repository's lock, a replay of
    a spent key, and the watched landing with its record read under `FOR UPDATE`."""
    authorize_actor(command.actor, _VERB)
    repository = command.repository.lower()
    _refuse_unless_permitted(session, repository, lane_on)
    lock_repository(session, _LOCK, repository)
    spent = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if spent is not None:
        return _replay(spent, _as_revert_command(command), repository), None, repository
    landing = next(iter(_watched_landings(session, repository, command.pr_number)), None)
    record = _record(session, repository, command.pr_number, lock=True)
    if landing is None:
        raise _not_watched(repository, command.pr_number)
    return landing, record, repository


def _as_revert_command(
    command: RevertCommand | RepointCommand | RestoreCommand | PinnedCommand,
) -> RevertCommand:
    """The fields the shared recording and replay read, from any step's command."""
    if isinstance(command, RevertCommand):
        return command
    return RevertCommand(
        repository=command.repository,
        pr_number=command.pr_number,
        actor=command.actor,
        idempotency_key=command.idempotency_key,
        expected_status=command.expected_status,
    )


def _stored_verdict(record: LandingRevert) -> Verdict:
    """The `bad` a record past the judgment was recorded on: its failed jobs and run."""
    return Verdict(
        kind=BAD,
        failed_job_names=tuple(record.failed_job_names),
        run_id=record.failed_run_id or None,
        detail=record.detail or "judged bad before production was pointed back",
    )


def _refuse_unless_pin_confirmed(record: LandingRevert, repository: str, pr_number: int) -> None:
    """Every pinned image carries the lander's confirmation that its tag was set."""
    images = record.pinned_images or {}
    confirmed = bool(images) and all(
        all(image.get(key) == value for key, value in PIN_CONFIRMED.items())
        for image in images.values()
    )
    if not confirmed:
        raise DomainError(
            LANDING_PIN_UNCONFIRMED,
            f"{repository}#{pr_number} is pointed back but its pin is not confirmed",
            "the lander completes and confirms the pin first",
        )


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
                    RevertOf(
                        landing.pr_number,
                        pull.title,
                        verdict.failed_job_names,
                        production_reason=(
                            None
                            if verdict.failed_job_names
                            else "production never served it and was pointed back at "
                            f"{record.parent_sha[:12]}"
                        ),
                    ),
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
            or_(
                LandingRevert.status.in_(_ALWAYS_WATCHED),
                LandingRevert.updated_at >= window,
                # Landing Part 3b: production pinned and not yet restored.
                and_(LandingRevert.pinned_images.is_not(None), LandingRevert.status != RESTORED),
            ),
        ),
    )
    # A landed revert past its window is a candidate, kept only while its dependency is held.
    landed = landed_revert()
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
    landing time is the merge commit's committer date, read from the remote: it times the settle,
    and bounds the query (a reading taken after the landing was received after the commit
    existed). Which readings were taken after the landing is the served rule's to decide, from the
    tip each was measured against. Every reading of those applications received since then, and
    the watcher's heartbeat for each: when the ingest last accepted a post about it, a repeat of a
    stored reading included.
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
            ServedReading(
                application=name,
                serving=_fact(facts, "serving"),
                received_at=received_at,
                measured_against=_fact(facts, "branch_names"),
            )
            for name, facts, received_at in rows
        ),
        heard={name: last_seen_at for name, last_seen_at in heard},
        landed_at=landed_at,
        now=session.scalar(select(func.now())) or datetime.now(UTC),
    )


def _fact(facts: object, key: str) -> str | None:
    """A commit a reading names under `key` (`serving`, `branch_names`); None when it names none,
    as an `unstamped` reading's `serving` does."""
    value = facts.get(key) if isinstance(facts, dict) else None
    return value if isinstance(value, str) and value else None


def _still_held(session: Session, landing: EstatePrMerge) -> bool:
    """Is any dependency its landed revert took back out still held in the member record?"""
    record = _record(session, landing.repository, landing.pr_number, lock=False)
    if record is None or not is_landed_revert(record):
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
    landing: EstatePrMerge, record: LandingRevert | None, verdict: Verdict | None, redeploys: bool
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
        lane=ESTATE_LANE if redeploys else INERT_LANE,
        parent_sha=(
            record.parent_sha
            if record is not None
            else (None if verdict is None else verdict.parent_sha)
        ),
        pinned_images=None if record is None else record.pinned_images,
        revert_merge_sha=None if record is None else record.revert_merge_sha,
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
