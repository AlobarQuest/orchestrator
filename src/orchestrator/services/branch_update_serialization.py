"""The lane edits one Dependabot branch per repository at a time. ADR-0045.

Bringing a Dependabot pull request up to date with its base writes a commit under this estate's
identity, and from then on Dependabot refuses to rebase that branch. Two such branches in one
repository, and a landing of either, is the whole precondition of the deadlock this rule exists to
prevent: the landing conflicts the other, Dependabot will not rebase it, and the lane will not
freshen a conflicted head.

This module owns the rule so that both branch-update acts and both admission reads ask one
question in one place.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from orchestrator.clock import Clock, TransactionClock
from orchestrator.persistence.models import Event
from orchestrator.services.estate_landing_admission import (
    DELIBERATE_REFUSALS,
    LANDING_APP_CREDENTIALS_MISSING,
    LANDING_CHECKS_AWAITING_VERDICT,
    LANDING_CHECKS_IN_FLIGHT,
    LANDING_CHECKS_VERDICT_UNREADABLE,
    LANDING_CONDITIONS_UNREADABLE,
    LANDING_ECOSYSTEM_UNREADABLE,
    LANDING_ESTATE_SOURCE_UNCONFIGURED,
    LANDING_ESTATE_SOURCE_UNREADABLE,
    LANDING_ESTATE_UNKNOWN,
    LANDING_FRESHNESS_UNREADABLE,
    LANDING_MERGEABILITY_UNKNOWN,
    LANDING_MERGEABILITY_UNRECOGNISED,
    LANDING_POLICY_UNREADABLE,
    LANDING_PULL_REQUEST_UNREADABLE,
    LANDING_RECORD_AMBIGUOUS,
    LANDING_RECORD_SOURCE_UNCONFIGURED,
    LANDING_RECORD_SOURCE_UNREADABLE,
    LANDING_RECORD_UNIDENTIFIED,
    LANDING_ROLLOUT_UNREADABLE,
    UPDATE_BOT_LOGIN,
    EstateGatewayError,
    OpenPullRequest,
    PullRequestCommit,
    SiblingReadGateway,
    freshness_derived_refusals,
)
from orchestrator.services.inert_landing_admission import (
    INERT_LANDING_POLICY_SOURCE_UNCONFIGURED,
    INERT_LANDING_POLICY_SOURCE_UNREADABLE,
)

# The event actions the two branch-update acts record, one per lane. They live HERE rather than in
# the act modules because the sibling rule reads them back out of the event log, and the act
# modules import this one -- so this one cannot import them. Each act re-imports its own under the
# same name, so every existing reader keeps working.
BRANCH_UPDATE_ACTION: Final = "estate_pr_branch_update.updated"
INERT_BRANCH_UPDATE_ACTION: Final = "inert_pr_branch_update.updated"

# The platform's own identity as COMMITTER of a commit it wrote on someone's behalf. Every commit
# the update bot writes carries it, measured on 63 of 63 across six repositories (2026-09-25). So
# does this estate's App's update-branch commit -- which is why the committer is not what separates
# the two (the author does). What it adds is the case the author cannot see: an update-bot commit a
# person rewrote in a local checkout keeps its author and takes that person as committer, and
# Dependabot disowns the branch all the same.
DEPENDABOT_COMMITTER: Final = "web-flow"

# How long a recorded branch update counts as an edit the platform may not have delivered yet.
#
# The update call answers 202 and does the work afterwards, so a commit read taken seconds after
# one sibling was updated can still show only the bot's commits, and the next sibling in the same
# pass would be updated too -- which is how two of the four stuck pairs were made. The event closes
# that window: it is written in the same transaction as the call, under the same repository lock,
# so the next act in the pass sees it.
#
# The bound exists because a 202 the platform accepts and never delivers would otherwise leave the
# pre-update head current forever, and the branch "edited" forever with it. Delivery took about 12
# seconds when measured, and sibling acts inside one pass are 3-4 seconds apart; ten minutes is
# more than forty times the measured delivery, longer than a whole lander pass, and shorter than
# the hourly cadence of both landers -- so a never-delivered update has lapsed before the next pass
# reads it. The trade, stated: a delivery slower than ten minutes reopens the window for one pass.
EDIT_EVENT_BOUND: Final = timedelta(minutes=10)


class Ownership(Enum):
    """Whose branch this is, as far as can be positively established."""

    # Every commit is the update bot's, the list is complete, and it ends at the listed head.
    OWNED = "owned"
    # Something other than the update bot has written to it, or this estate just did.
    EDITED = "edited"
    # Neither could be established. Which way that fails is decided by the side it is on.
    UNESTABLISHED = "unestablished"


def _commit_class(commit: PullRequestCommit) -> Literal["dependabot", "foreign", "unclassified"]:
    """Exactly one of three classes, from the fields the commits listing already carries.

    **The update bot's own:** linked author `dependabot[bot]`, committer `web-flow`, and the
    platform's signature. The signature is what closes the cheap spoof: a committer address set to
    the platform's noreply links to web-flow but cannot carry the platform's signature.

    **Foreign:** a linked author that is anyone else, or a linked committer that is anyone but
    web-flow. Dependabot disowns a branch whoever pushed to it, so a person's commit counts exactly
    as this estate's App's does -- nothing here consults identities the orchestrator knows.

    **Unclassified:** everything else -- no linked author, no linked committer, or an unsigned
    web-flow commit. Not treated as either, because each side of the rule must fail in a
    different direction on an unknown.
    """
    author = commit.author_login
    committer = commit.committer_login
    if (author is not None and author != UPDATE_BOT_LOGIN) or (
        committer is not None and committer != DEPENDABOT_COMMITTER
    ):
        return "foreign"
    if author == UPDATE_BOT_LOGIN and committer == DEPENDABOT_COMMITTER and commit.verified:
        return "dependabot"
    return "unclassified"


def _ownership(
    commits: tuple[PullRequestCommit, ...], *, listed_head: str, edited_by_event: bool
) -> Ownership:
    """Positively edited, positively owned, or neither.

    **Edited** when any commit is foreign, or this estate updated the branch at its current head
    moments ago -- a foreign commit is a fact about the branch whatever head it was read at.

    **Owned** only when every commit is the bot's, there is at least one, and the LAST one is the
    head the list call named. The list and the commits are two reads: if the head moved between
    them, the commits describe a different head from the one being reasoned about, and a moved
    head is an unestablished answer rather than a wrong one.
    """
    classes = [_commit_class(c) for c in commits]
    if edited_by_event or "foreign" in classes:
        return Ownership.EDITED
    if commits and all(c == "dependabot" for c in classes) and commits[-1].sha == listed_head:
        return Ownership.OWNED
    return Ownership.UNESTABLISHED


def _recently_updated_heads(
    session: Session, repository: str, numbers: Iterable[int], now: datetime
) -> dict[int, set[str]]:
    """For each named pull request, the heads this estate updated it FROM inside the bound.

    One query over both lanes' update events, because both edit update-bot branches and share the
    repository lock. The recorded head is the head BEFORE the update, so it matches the pull
    request's current head exactly while the platform has not delivered, and retires the moment
    the head moves -- whether by delivery (the App's commit then reads as foreign) or by a user's
    recreate (the branch is the bot's again).

    A failing query propagates. The caller's transaction is then broken, and a broken transaction
    must never be converted into an answer about ownership.
    """
    wanted = set(numbers)
    rows = session.scalars(
        select(Event.payload).where(
            Event.action.in_((BRANCH_UPDATE_ACTION, INERT_BRANCH_UPDATE_ACTION)),
            Event.occurred_at > now - EDIT_EVENT_BOUND,
            Event.payload["repository"].astext == repository,
        )
    )
    heads: dict[int, set[str]] = {}
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        number = payload.get("pr_number")
        head = payload.get("head_sha")
        # `True == 1`: a boolean is not a pull request number, however it compares.
        if not isinstance(number, int) or isinstance(number, bool) or number not in wanted:
            continue
        if isinstance(head, str) and head:
            heads.setdefault(number, set()).add(head)
    return heads


# HOLDING, beside the two sets reused rather than restated. What an edited branch reports in the
# minutes after an update: checks re-running, runs cancelled or not yet started, or the platform
# still computing mergeability. Without these the branch the lane has just freshened would stop
# counting as holding in the very pass that freshened it, and the sibling behind it would be edited
# too -- which is the defect.
#
# EACH OF THE THREE CAN OUTLIVE THOSE MINUTES, and that is a stall residual rather than an
# oversight: abandoned runs nothing re-runs, a run with no runner, mergeability that never
# resolves. It is reported once, as `held` on the holding branch's own line, because none of them
# is in any lander's non-finding set. Nothing here asserts that every member clears on its own.
#
# Built from the imported names, like every refusal set in this estate, so no code is spelled
# twice. What keeps the three sets complete is the test that enumerates every refusal constant.
_HOLDING_BESIDE_THE_CRITERION: Final = frozenset(
    {LANDING_CHECKS_IN_FLIGHT, LANDING_CHECKS_AWAITING_VERDICT, LANDING_MERGEABILITY_UNKNOWN}
)

# READ FAILURE: the orchestrator could not establish the sibling's state. Any one of these in a
# sibling's composed answer withholds the target -- as a finding, never quietly -- because a sibling
# whose reads failed is exactly a sibling whose state was not established. One set for both lanes:
# which lane can raise which code is not derivable mechanically, and a member a lane never raises
# is harmless in it.
#
# The repository-level members (the two unconfigured sources, the missing credentials) would
# refuse the target's own answer too, so the rule never runs beside them; they are listed so the
# classification is complete. `landing_mergeability_unknown` is deliberately NOT here: it is the
# ordinary transient after an update. `..._unrecognised` is a word the platform has invented since
# the code was written, and nothing can be concluded from it.
READ_FAILURE_REFUSALS: Final = frozenset(
    {
        LANDING_PULL_REQUEST_UNREADABLE,
        LANDING_CHECKS_VERDICT_UNREADABLE,
        LANDING_FRESHNESS_UNREADABLE,
        LANDING_ROLLOUT_UNREADABLE,
        LANDING_ECOSYSTEM_UNREADABLE,
        LANDING_POLICY_UNREADABLE,
        LANDING_CONDITIONS_UNREADABLE,
        LANDING_RECORD_SOURCE_UNREADABLE,
        LANDING_RECORD_SOURCE_UNCONFIGURED,
        LANDING_RECORD_AMBIGUOUS,
        LANDING_RECORD_UNIDENTIFIED,
        LANDING_ESTATE_SOURCE_UNREADABLE,
        LANDING_ESTATE_SOURCE_UNCONFIGURED,
        LANDING_ESTATE_UNKNOWN,
        LANDING_MERGEABILITY_UNRECOGNISED,
        LANDING_APP_CREDENTIALS_MISSING,
        INERT_LANDING_POLICY_SOURCE_UNREADABLE,
        INERT_LANDING_POLICY_SOURCE_UNCONFIGURED,
    }
)


@dataclass(frozen=True)
class SiblingAnswer:
    """What a sibling's own composed admission says, as the holding test needs it."""

    refusals: tuple[str, ...]
    # The freshness criterion's one fact. The inert lane pins no rollout, so its composer always
    # passes False, under which a moved rollout can never be excused as staleness.
    rollout_base_matches_pin: bool


def _answer_class(answer: SiblingAnswer) -> Literal["holding", "releasing", "unreadable"]:
    """Is this sibling still queued to land, not, or not known?

    **Unreadable is checked first**, so a read failure beside a releasing code is still a read
    failure: the releasing code was read, the failed one was not, and the sibling's state as a whole
    was not established.

    **Holding** when every refusal is one that clears without anyone acting: a deliberate one, a
    freshness-derived one (which the lane itself clears, since the holding branch is already edited
    and may always be freshened again), or one of the three post-update transients. An empty list
    holds too -- that sibling's own answer is satisfied, so it is the branch queued next.

    **Releasing** otherwise. At runtime releasing is the complement, the pre-change behaviour: on a
    condition the orchestrator positively read and named, the rule never withholds more than the
    lane did before it existed.
    """
    present = set(answer.refusals)
    if present & READ_FAILURE_REFUSALS:
        return "unreadable"
    derived = freshness_derived_refusals(
        present, rollout_base_matches_pin=answer.rollout_base_matches_pin
    )
    if present <= DELIBERATE_REFUSALS | derived | _HOLDING_BESIDE_THE_CRITERION:
        return "holding"
    return "releasing"


class SiblingOutcome(Enum):
    """The four answers the act reaches, in the order they are checked."""

    # 1. The target is already edited: it has lost Dependabot's ownership, so freshening it again
    #    costs nothing. This is what keeps a repository with several edited branches workable.
    RELEASE_TARGET_EDITED = "release_target_edited"
    # 2. Another Dependabot pull request is positively edited and holding, and the target is
    #    positively owned. A deliberate withhold, and it clears when the branch ahead lands.
    WITHHOLD_SIBLING_HOLDING = "withhold_sibling_holding"
    # 3. The act could not establish that no other edited branch is queued to land. A withhold
    #    that is a finding: nothing about not knowing clears on its own.
    WITHHOLD_SIBLINGS_UNREADABLE = "withhold_siblings_unreadable"
    # 4. Every other Dependabot pull request is owned, or edited and not holding.
    RELEASE = "release"


def _is_update_bot(pull: OpenPullRequest) -> bool:
    """The author test the admission cascade already applies: the login AND the account type."""
    return pull.author_login == UPDATE_BOT_LOGIN and pull.author_is_bot


def branch_update_sibling_outcome(
    session: Session,
    *,
    repository: str,
    target_number: int,
    gateway: SiblingReadGateway,
    compose: Callable[[int], SiblingAnswer],
    clock: Clock | None = None,
) -> SiblingOutcome:
    """May the lane make this Dependabot branch edited, given its siblings? ADR-0045.

    **Why this lives outside both admission modules.** Testing whether a sibling holds means
    composing THAT sibling's admission. If this rule lived inside the admission, composing a
    sibling would compose the sibling's siblings, without end. Out here, `compose` answers one
    question -- what does this sibling's own admission say -- and that answer never asks this one.
    It is also how "which lane" is parameterized: each caller hands in its own lane's composer, and
    everything else is one definition for both.

    **The order is the spec's, and every sibling is evaluated before deciding.** A positively
    observed holding sibling beside an owned target is the deliberate withhold even when some other
    read failed; the served fact is therefore true exactly on outcome 2.

    **Unknowns fail in opposite directions on the two sides.** An unestablished target is never
    treated as edited (that would make it a second edited branch) and an unestablished sibling is
    never treated as owned (that would let a first edit go ahead beside one). Where either matters,
    the answer is outcome 3 -- a withhold that is reported, never a silent one.

    A database failure propagates from anywhere in here, including out of `compose`: the caller's
    transaction is broken, and a broken transaction is not an answer about siblings.
    """
    repository = repository.lower()
    now = (clock or TransactionClock()).now(session)

    try:
        open_pulls = gateway.open_pull_requests(repository=repository)
    except EstateGatewayError:
        return SiblingOutcome.WITHHOLD_SIBLINGS_UNREADABLE
    target = next((p for p in open_pulls if p.number == target_number), None)
    if target is None:
        # A race, or the platform lagging. The target's author and head are unknown, and releasing
        # would decide a Dependabot branch's ownership by default.
        return SiblingOutcome.WITHHOLD_SIBLINGS_UNREADABLE
    if not _is_update_bot(target):
        # A sync bot rebuilds its branch daily with a force-push, and anyone else's pull request is
        # not one Dependabot maintains: an edit disowns nothing, so nothing is withheld.
        return SiblingOutcome.RELEASE

    siblings = [p for p in open_pulls if _is_update_bot(p) and p.number != target_number]
    events = _recently_updated_heads(
        session, repository, [target_number, *(p.number for p in siblings)], now
    )

    target_ownership = _read_ownership(
        target, repository=repository, gateway=gateway, events=events
    )
    if target_ownership is Ownership.EDITED:
        return SiblingOutcome.RELEASE_TARGET_EDITED

    verdicts = {
        _sibling_verdict(
            sibling, repository=repository, gateway=gateway, events=events, compose=compose
        )
        for sibling in siblings
    }
    read_failed = target_ownership is None or "unknown" in verdicts
    if "holding" in verdicts and target_ownership is Ownership.OWNED:
        return SiblingOutcome.WITHHOLD_SIBLING_HOLDING
    if "holding" in verdicts or read_failed:
        return SiblingOutcome.WITHHOLD_SIBLINGS_UNREADABLE
    return SiblingOutcome.RELEASE


def _read_ownership(
    pull: OpenPullRequest,
    *,
    repository: str,
    gateway: SiblingReadGateway,
    events: dict[int, set[str]],
) -> Ownership | None:
    """One pull request's ownership, or None when its commits could not be read in full.

    None is distinct from UNESTABLISHED on purpose: an unread list is a read failure wherever it
    occurs, where an unclassified commit makes only this pull request's ownership unknown.
    """
    try:
        commits = gateway.pull_request_commits(repository=repository, number=pull.number)
    except EstateGatewayError:
        return None
    return _ownership(
        commits,
        listed_head=pull.head_sha,
        edited_by_event=pull.head_sha in events.get(pull.number, set()),
    )


def _sibling_verdict(
    sibling: OpenPullRequest,
    *,
    repository: str,
    gateway: SiblingReadGateway,
    events: dict[int, set[str]],
    compose: Callable[[int], SiblingAnswer],
) -> Literal["clear", "holding", "unknown"]:
    """Does this sibling hold the repository, not hold it, or is that unknown?

    An owned sibling is clear without composing anything: Dependabot will rebase it if a landing
    conflicts it. Otherwise its own admission is composed, and an unestablished sibling whose answer
    would hold is unknown rather than holding -- whether it is edited was never established.
    """
    ownership = _read_ownership(sibling, repository=repository, gateway=gateway, events=events)
    if ownership is None:
        return "unknown"
    if ownership is Ownership.OWNED:
        return "clear"
    try:
        answer = compose(sibling.number)
    except SQLAlchemyError:
        raise
    except Exception:
        # Composing the sibling's answer failed rather than answered: its state is unknown.
        return "unknown"
    verdict = _answer_class(answer)
    if verdict == "unreadable":
        return "unknown"
    if verdict == "holding":
        return "holding" if ownership is Ownership.EDITED else "unknown"
    return "clear"


def withheld_for_sibling(*, qualifies: bool, outcome: Callable[[], SiblingOutcome]) -> bool:
    """The fact both admission answers serve: is this branch update withheld for a sibling?

    True ONLY for a positively observed holding sibling (outcome 2). False on outcome 3, so a scan
    whose reads failed never produces a quiet line -- the act meets the same failure and refuses
    with its own code, which the landers keep a finding. It is a fact about an observed sibling,
    never a record of the lane declining.

    The scan runs only when the branch qualifies, because a branch that does not qualify is not
    being freshened whatever its siblings say. A scan that fails in any way but a database error
    leaves the admission read answering, with this false; a database error propagates, because the
    read's transaction is then broken.
    """
    if not qualifies:
        return False
    try:
        return outcome() is SiblingOutcome.WITHHOLD_SIBLING_HOLDING
    except SQLAlchemyError:
        raise
    except Exception:
        return False
