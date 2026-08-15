"""May the orchestrator land this pull request into a repository where landing changes
something already serving, and if not, why? ADR-0019 Increment 5b.

**This module answers; it does not act.** Nothing it returns causes anything to happen, and the
one thing it holds -- a read-only view of GitHub -- it uses to ask questions rather than to change
anything. The separation is the same one the work-unit landing answer keeps, and for the same
reason: the composed answer can be read against the real waiting population before anything obeys
it.

## Why this is a sibling path and not the work-unit one

The work-unit landing is built around a unit: an envelope with a human's fingerprint-bound
approval, verifier-decided criteria, a binding row. A pull request the update bot opened has none
of them, and the row shape that records such a landing has a non-nullable unit id.

**What authorises this instead is a change record and the policy behind it**, and the substitution
is bounded rather than equivalent. What a conformant record attests is precise and narrow: *a
human pinned this repository, these criteria and this remedy.* Not one policy term is a function
of the change -- the class and the risk are literals the producer writes about every pull request
it sees, and the rest are functions of the repository. **So every change-specific question is a
term HERE**, evaluated against GitHub at the moment of the act, because that is the only party
that can read it and that moment is the only one at which the answer is true.

## `satisfied` is a positive conjunction, never "no refusal was raised"

Every term computes its own affirmative answer and the answers are ANDed; the refusal list is
built alongside for the reader. An answer whose affirmative case is an empty objection list is the
fail-open shape this repository keeps finding.

## Every term is reported, and none short-circuits

An operator asking why nothing landed tonight wants the whole list, not the first thing that went
wrong -- and the terms are fixed by different people at different times. A held pull request that
names its condition is the mechanism working.

## Two facts that are read from the record's own service, and one that is not

The conditions on the act (which update types may land, whether the head must be current with its
base, which bytes the rollout must still be) are DECLARED by the party holding the policy and
EVALUATED here. This process keeps no copy of them: they arrive with the record they qualify.

The one thing read from neither is whether landing on this repository changes something already
serving. That is the estate's own answer, and it is asked of the estate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import Clock, TransactionClock
from orchestrator.errors import DomainError
from orchestrator.factory_policy import load_factory_policy
from orchestrator.persistence.models import EstatePrMerge
from orchestrator.reach_vocabulary import LIVE_ESTATE
from orchestrator.services.change_record import (
    RECORD_AMBIGUOUS,
    ChangeRecordSource,
    LandingConditions,
)
from orchestrator.services.change_record import (
    SOURCE_UNCONFIGURED as RECORD_SOURCE_UNCONFIGURED,
)
from orchestrator.services.estate_landing import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    SOURCE_UNCONFIGURED,
    EstateLandingSource,
)

# The identity of the account whose pull requests this lane exists for, exactly. NOT "any account
# of type Bot": that admits every GitHub App, including this estate's own, which holds a write on
# every repository in the account. The type is checked as well as the login, so a user account
# that renamed itself into this string is still refused.
UPDATE_BOT_LOGIN: Final = "dependabot[bot]"

# GitHub's own composite answer about whether a pull request can be landed. It is the closest
# thing available to "every required check is green" -- reading the required-context list needs a
# permission this estate's App does not have, and the checks a repository publishes are not the
# same set as the checks its branch protection requires.
#
# **AND ITS VALUE RESTS ON A SETTING THIS PROCESS CANNOT READ.** `clean` means "no required check
# is failing" only while branch protection requires one; strip the required context, and `clean`
# degrades to "no merge conflict" while every term in this cascade still passes. The App has no
# `administration` permission, so this side can neither read that list nor pin it -- the estate has
# measured both that these settings drift and that they are unreadable from here. It is a real
# residual and it is named rather than implied.
#
# **It is stale-tolerant, so it does NOT discharge the freshness term.** A required check can be
# green against a head that is behind its base, and this answers `clean` for exactly that case;
# the four pull requests waiting when this was written were all `clean` and all two commits
# behind.
MERGEABLE_CLEAN: Final = "clean"

# This deployment has not been told it may land anything. Default false, unconfigured refusing.
LANDING_NOT_ENABLED: Final = "landing_not_enabled"

# No App credentials, so nothing can be minted and no call can be made. Asked before the remote is
# touched, so the gate and the actor read one answer about which credentials are in play.
LANDING_APP_CREDENTIALS_MISSING: Final = "landing_app_credentials_missing"

# What the estate says about landing on this repository's default branch. This lane exists ONLY
# for repositories where that changes something already serving -- the opposite direction from the
# work-unit landing's term, which exists only for the ones where it does not.
LANDING_ESTATE_SOURCE_UNCONFIGURED: Final = "landing_estate_source_unconfigured"
LANDING_ESTATE_SOURCE_UNREADABLE: Final = "landing_estate_source_unreadable"
LANDING_ESTATE_UNKNOWN: Final = "landing_estate_unknown"
LANDING_TARGET_NOT_ROUTED: Final = "landing_target_not_routed"

# Whether a change was routed through the estate's record, and what the record says.
LANDING_RECORD_SOURCE_UNCONFIGURED: Final = "landing_record_source_unconfigured"
LANDING_RECORD_SOURCE_UNREADABLE: Final = "landing_record_source_unreadable"
LANDING_RECORD_AMBIGUOUS: Final = "landing_record_ambiguous"
LANDING_RECORD_ABSENT: Final = "landing_record_absent"
LANDING_RECORD_NOT_APPROVED: Final = "landing_record_not_approved"

# The record carries no readable identifier, so the landing could not NAME the permission it acted
# on. The act writes that identifier into the squash body, where the estate's ledger reads it back
# to classify the landing -- so without one the landing records as having no accountable basis at
# all, which is the class the ledger keeps and no detector reads. Refused here rather than
# discovered there: a landing whose basis cannot be written down is one that should not happen.
LANDING_RECORD_UNIDENTIFIED: Final = "landing_record_unidentified"

# Stored `approved`, and the policy now says it does not conform. The record's own service
# recomputes its objections on every read, so a stored decision the policy has since overtaken is
# visible -- and this term is why reading `status` alone is not enough.
LANDING_RECORD_HAS_LIVE_OBJECTIONS: Final = "landing_record_has_live_objections"

# The record carries no version, so no policy approved it -- a human did, before any policy
# existed. Its basis is that person, and this lane does not land on it: an unattended act binds
# itself to a standing rule, and "somebody approved this once" is not one.
LANDING_RECORD_NOT_POLICY_APPROVED: Final = "landing_record_not_policy_approved"

# The record was approved under a version that is no longer in force.
#
# BE PRECISE ABOUT WHAT THIS BUYS, because an earlier version of this comment overstated it. It is
# the only thing that binds an existing approval AT THE ACT -- but the record's own service
# re-approves a still-conforming record under the newer version on the producer's next pass, so
# for a pull request that is still open the binding lasts about an hour and is then lifted without
# anyone looking. What it genuinely covers is the window before that pass, and every record the
# producer will never propose again: one whose pull request has closed or merged is re-evaluated
# by nothing, and would otherwise carry a superseded approval forever.
#
# A NARROWING that a record no longer conforms to is a different matter and is fully bound: the
# record's service revokes it rather than re-approving.
LANDING_POLICY_VERSION_SUPERSEDED: Final = "landing_policy_version_superseded"

# The conditions on the act did not read as any. A deployment whose record service predates them,
# or a shape this build does not recognise. Refuses rather than proceeding under conditions
# nobody stated.
LANDING_CONDITIONS_UNREADABLE: Final = "landing_conditions_unreadable"

# The hours policy declares for changing something already serving.
LANDING_CHANGE_WINDOW_NOT_DECLARED: Final = "landing_change_window_not_declared"
LANDING_OUTSIDE_CHANGE_WINDOW: Final = "landing_outside_change_window"
LANDING_POLICY_UNREADABLE: Final = "landing_policy_unreadable"

# What GitHub says about the pull request itself.
LANDING_PULL_REQUEST_UNREADABLE: Final = "landing_pull_request_unreadable"
LANDING_PULL_REQUEST_NOT_OPEN: Final = "landing_pull_request_not_open"
LANDING_BASE_NOT_DEFAULT_BRANCH: Final = "landing_base_not_default_branch"
LANDING_AUTHOR_NOT_THE_UPDATE_BOT: Final = "landing_author_not_the_update_bot"
LANDING_CHECKS_NOT_CLEAN: Final = "landing_checks_not_clean"

# The remote has not finished computing mergeability. GitHub answers `unknown` while it works, and
# reporting that as "the checks are not clean" names the wrong cause to whoever reads the report --
# a pull request whose checks are green. Its own refusal, because its remedy is to ask again and
# every other one's is not. Both refuse; only the name differs, which is the whole point.
LANDING_MERGEABILITY_UNKNOWN: Final = "landing_mergeability_unknown"
MERGEABLE_UNKNOWN: Final = "unknown"

# The head is behind the base it would be squashed onto, so the tree that would land is one no
# check has ever run against -- and on a repository where landing changes something already
# serving, that tree is what starts serving.
LANDING_HEAD_NOT_CURRENT_WITH_BASE: Final = "landing_head_not_current_with_base"
LANDING_FRESHNESS_UNREADABLE: Final = "landing_freshness_unreadable"

# The version delta, parsed from the title at the moment of the act rather than frozen into the
# record. The update bot rewrites a pull request IN PLACE when a newer version appears, so the
# title is the only place that tracks it: measured, the branch ref goes stale, and so does the
# machine-readable block in the bot's own commit message.
#
# A requirement-RANGE bump carries no parseable delta and is refused for want of one. That is the
# intended answer and not a parser defect.
LANDING_UPDATE_TYPE_UNPARSEABLE: Final = "landing_update_type_unparseable"
LANDING_UPDATE_TYPE_NOT_PERMITTED: Final = "landing_update_type_not_permitted"

# Whether the rollout this landing would cause is still the one the record's criteria describe.
LANDING_ROLLOUT_UNPINNED: Final = "landing_rollout_unpinned"
LANDING_ROLLOUT_UNREADABLE: Final = "landing_rollout_unreadable"
LANDING_ROLLOUT_MOVED: Final = "landing_rollout_moved"

# Something already landed into this repository during the hours now open. One per repository per
# occurrence, so a night's blast radius is bounded by a rule rather than by a side effect.
LANDING_PACE_EXHAUSTED: Final = "landing_pace_exhausted"

# A row already records an act against this pull request. Terminal: the row is unique per pull
# request and there is no delete path, so a second act is never attempted.
LANDING_ALREADY_RECORDED: Final = "landing_already_recorded"

# Refusals the system raises ON PURPOSE, each of which clears itself when the window next opens.
# Neither names a condition anybody can act on: the day's pace for this repository is spent, or the
# clock is outside the hours policy declares for changing something already serving.
#
# MIRRORED in the lander's own `_DELIBERATE`, which cannot import this module -- that program is
# isolated from `orchestrator.*` on purpose. The two are pinned equal by a test that imports both,
# because this estate's standing lesson is that wherever two vocabularies must agree they do not,
# until something checks.
DELIBERATE_REFUSALS: Final = frozenset({LANDING_PACE_EXHAUSTED, LANDING_OUTSIDE_CHANGE_WINDOW})

# `bump <name> from <a> to <b>`, anchored at the end so a grouped bump -- whose title carries
# trailing text naming the group -- refuses rather than being classified on whichever dependency
# happens to be named. A requirement range (`from >=0.51.0 to >=0.52.1`) does not match at all,
# because the character after `to ` is not a digit.
_BUMP: Final = re.compile(r"\bfrom v?(\d[\d.]*) to v?(\d[\d.]*)$")

SEMVER_MAJOR: Final = "semver-major"
SEMVER_MINOR: Final = "semver-minor"
SEMVER_PATCH: Final = "semver-patch"


@dataclass(frozen=True)
class EstatePullRequest:
    """What the remote says about the pull request, as this module needs it."""

    number: int
    title: str
    head_sha: str
    base_ref: str
    default_branch: str
    open: bool
    landed: bool
    author_login: str
    author_is_bot: bool
    mergeable_state: str


class EstateGatewayError(Exception):
    """A failure to reach or read the remote. Carries a code, never a token."""

    def __init__(self, code: str, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class EstateReadGateway(Protocol):
    """The reads every term below needs. Injected, so the whole cascade runs with no network."""

    def read_pull_request(self, *, repository: str, number: int) -> EstatePullRequest: ...

    def commits_behind_base(self, *, repository: str, base_ref: str, head_sha: str) -> int: ...

    def blob_sha(self, *, repository: str, path: str, ref: str) -> str | None: ...


@dataclass(frozen=True)
class EstateLandingAdmission:
    """The composed answer, plus what the act needs in order to name what it acted on."""

    satisfied: bool
    refusals: tuple[str, ...]
    repository: str
    pr_number: int
    head_sha: str | None
    change_record_id: int | None
    policy_version: int | None
    # A SECOND, much smaller permission composed from the same terms: not "may this land" but "may
    # the lane bring this branch up to date with its base". Served on the read surface so a dry run
    # can report what a live pass would do without anything acting -- the acting path recomposes
    # this from scratch and never trusts a caller's copy of it.
    branch_update_qualifies: bool


def qualifies_for_branch_update(refusals: tuple[str, ...]) -> bool:
    """May the lane bring this pull request's head up to date with its base?

    **ONLY WHEN FRESHNESS IS THE SOLE REMAINING OBSTACLE**, and that rule is the whole design
    rather than a precaution. The lane creates this condition itself: a landing moves the base, so
    every sibling pull request in that repository becomes behind it, and the freshness term then
    refuses them all. Nothing else resolves it -- measured, one pull request sat 29 hours behind
    while three windows passed over it.

    So the lane clears what the lane staled. What it must NOT do is bring up to date a pull request
    that could not land anyway: a requirement-range bump states no single version delta and can
    never be classified, a red check is not made green by a fresher base. Each would spend a real
    build on a branch whose answer does not change, and a build running is indistinguishable from
    progress to whoever reads the report.

    The remainder is tested against a CATEGORY and never against a count. A pull request refused on
    freshness alone qualifies; so does one also refused because the day's pace is spent or the hour
    is outside the window, because each of those clears itself and neither says anything about the
    branch. Any other refusal -- present or future, named or not yet invented -- disqualifies,
    which is the polarity this lane argues for everywhere else: an unclassified code must fail
    toward refusing rather than toward acting.
    """
    remainder = set(refusals) - {LANDING_HEAD_NOT_CURRENT_WITH_BASE} - DELIBERATE_REFUSALS
    return LANDING_HEAD_NOT_CURRENT_WITH_BASE in refusals and not remainder


@dataclass(frozen=True)
class _Term:
    met: bool
    refusals: tuple[str, ...]


def update_type_of(title: str) -> str | None:
    """Which kind of version change this title declares, or None when it declares none.

    **Read from the TITLE, and that was measured rather than chosen.** The update bot rewrites a
    pull request in place when a newer version appears. On one such pull request the branch still
    read `ruff-0.16.0` while the title read `0.16.1` -- and so, remarkably, did the bot's own
    machine-readable `dependency-version` trailer in the head commit, whose diff installs 0.16.1.
    The title is the only one of the three that tracked the change, so the two identifiers that
    look more structured are the two that were wrong.

    None is a refusal, never a default. A requirement-range bump and a grouped bump both land
    here, and both are correctly unlandable by this lane: neither states a single delta that any
    rule about update types could be applied to.
    """
    match = _BUMP.search(title.strip())
    if match is None:
        return None
    before, after = _version(match.group(1)), _version(match.group(2))
    if before is None or after is None or after <= before:
        return None
    if after[0] != before[0]:
        return SEMVER_MAJOR
    if after[1] != before[1]:
        return SEMVER_MINOR
    return SEMVER_PATCH


def _version(text: str) -> tuple[int, int, int] | None:
    """A dotted version as three components, padding a short one with zeros.

    Padding is what makes `from 4 to 7` -- how the workflow-automation ecosystem is versioned --
    read as the major change it is, rather than as unparseable.
    """
    parts = text.split(".")
    if len(parts) > 3 or any(not part.isdigit() for part in parts):
        return None
    padded = [*parts, "0", "0"][:3]
    return int(padded[0]), int(padded[1]), int(padded[2])


def estate_landing_admission(
    session: Session,
    repository: str,
    pr_number: int,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    gateway: EstateReadGateway,
    *,
    enabled: bool,
    credentials_configured: bool,
    clock: Clock | None = None,
) -> EstateLandingAdmission:
    """Compose the answer for one pull request. Reads only; writes nothing, and never acts.

    THE REPOSITORY IS FOLDED HERE, so the report and the act cannot normalize it differently. The
    acting path lowercased before calling in and the reporting route did not, which is two surfaces
    asking the estate a different question about one repository -- and the record's own identity
    key folds case, so the fold has to happen somewhere both reach.
    """
    repository = repository.lower()
    now = (clock or TransactionClock()).now(session)

    prior = session.scalar(
        select(EstatePrMerge).where(
            EstatePrMerge.repository == repository.lower(),
            EstatePrMerge.pr_number == pr_number,
        )
    )

    refusals: list[str] = []
    if not enabled:
        refusals.append(LANDING_NOT_ENABLED)
    if not credentials_configured:
        refusals.append(LANDING_APP_CREDENTIALS_MISSING)
    if prior is not None:
        refusals.append(LANDING_ALREADY_RECORDED)

    estate = _estate_term(repository, landing_source)
    record = _record_term(repository, pr_number, record_source)
    window = _window_term(now)
    pace = _pace_term(session, repository, now)

    conditions = record.conditions
    remote = _remote_terms(repository, pr_number, conditions, gateway)

    refusals.extend(estate.refusals)
    refusals.extend(record.term.refusals)
    refusals.extend(window.refusals)
    refusals.extend(pace.refusals)
    refusals.extend(remote.term.refusals)

    satisfied = (
        enabled
        and credentials_configured
        and prior is None
        and estate.met
        and record.term.met
        and window.met
        and pace.met
        and remote.term.met
    )
    return EstateLandingAdmission(
        satisfied=satisfied,
        refusals=tuple(refusals),
        repository=repository,
        pr_number=pr_number,
        head_sha=remote.head_sha,
        change_record_id=record.record_id,
        policy_version=record.policy_version,
        branch_update_qualifies=qualifies_for_branch_update(tuple(refusals)),
    )


def _estate_term(repository: str, landing_source: EstateLandingSource) -> _Term:
    """Does landing on this repository's default branch change something already serving?

    Only an explicit yes passes, and the direction is the opposite of the work-unit landing's.
    That one exists for repositories where a landed pull request is inert; this one exists ONLY
    for the other kind, because a change record and a rollout to observe are what make an
    unattended landing accountable, and an inert repository has neither.
    """
    answer = landing_source.landing_for(repository)
    if answer.landing == LANDING_REDEPLOYS:
        return _Term(True, ())
    if answer.landing is not None:
        # `inert` and `unknown` are different facts with different next steps: one says this lane
        # is the wrong one, the other says the estate has not looked.
        if answer.landing == LANDING_INERT:
            return _Term(False, (LANDING_TARGET_NOT_ROUTED,))
        return _Term(False, (LANDING_ESTATE_UNKNOWN,))
    if answer.reason == SOURCE_UNCONFIGURED:
        return _Term(False, (LANDING_ESTATE_SOURCE_UNCONFIGURED,))
    return _Term(False, (LANDING_ESTATE_SOURCE_UNREADABLE,))


@dataclass(frozen=True)
class _RecordTerms:
    term: _Term
    record_id: int | None
    policy_version: int | None
    conditions: LandingConditions | None


def _record_term(
    repository: str, pr_number: int, record_source: ChangeRecordSource
) -> _RecordTerms:
    """Was this change routed, approved, and approved under the rule that is in force NOW?

    **`status` alone decides nothing here**, which is the correction this increment carries. Three
    materially different rows read `approved`: one a policy approved and still conforms, one a
    person approved before any policy existed, and one whose stored decision the policy has since
    overtaken. They are told apart by the version and the live objections, and only the first is a
    basis for an unattended act.
    """
    answer = record_source.record_for(repository, pr_number)
    if not answer.answered:
        return _RecordTerms(_Term(False, (_unread_reason(answer.reason),)), None, None, None)
    record = answer.record
    if record is None:
        return _RecordTerms(_Term(False, (LANDING_RECORD_ABSENT,)), None, None, None)

    # Each clause is a fact about the record and its own refusal, evaluated together so the answer
    # names every one that is unmet rather than the first. `met` is the conjunction of the same
    # clauses -- a positive answer, never "the list came back empty".
    clauses = (
        (record.approved, LANDING_RECORD_NOT_APPROVED),
        (record.record_id is not None, LANDING_RECORD_UNIDENTIFIED),
        (not record.policy_objections, LANDING_RECORD_HAS_LIVE_OBJECTIONS),
        (record.policy_version is not None, LANDING_RECORD_NOT_POLICY_APPROVED),
        (record.conditions is not None, LANDING_CONDITIONS_UNREADABLE),
        # Reported only when both halves are readable: with no version or no conditions there is
        # nothing to compare, and a second name for one absent fact is redundancy this repository
        # has rejected before.
        (
            record.conditions is None
            or record.policy_version is None
            or record.policy_version == record.conditions.version,
            LANDING_POLICY_VERSION_SUPERSEDED,
        ),
    )
    refusals = tuple(refusal for held, refusal in clauses if not held)
    return _RecordTerms(
        _Term(all(held for held, _ in clauses), refusals),
        record.record_id,
        record.policy_version,
        record.conditions,
    )


def _unread_reason(reason: str | None) -> str:
    """Why the record service gave no answer. Three causes, three different people.

    An unconfigured deployment is a missing setting here; ambiguity is a foreign constraint this
    process may not resolve; anything else is a service refusing or unreachable.
    """
    if reason == RECORD_SOURCE_UNCONFIGURED:
        return LANDING_RECORD_SOURCE_UNCONFIGURED
    if reason == RECORD_AMBIGUOUS:
        return LANDING_RECORD_AMBIGUOUS
    return LANDING_RECORD_SOURCE_UNREADABLE


def _window_term(now) -> _Term:
    """Is this an hour in which policy raises no objection to changing something already serving?

    The affirmative case is "a window is declared and now is inside it", never "nothing objected":
    the artifact treats a row with no window as raising no objection, which is right for a policy
    report and would admit at any hour here.
    """
    try:
        policy = load_factory_policy()
        row = policy.rows.get(LIVE_ESTATE)
        if row is None or row.change_window is None:
            return _Term(False, (LANDING_CHANGE_WINDOW_NOT_DECLARED,))
        refusal = policy.window_refusal((LIVE_ESTATE,), now)
    except DomainError:
        return _Term(False, (LANDING_POLICY_UNREADABLE,))
    if refusal is not None:
        return _Term(False, (LANDING_OUTSIDE_CHANGE_WINDOW,))
    return _Term(True, ())


def _pace_term(session: Session, repository: str, now) -> _Term:
    """One landing per repository per occurrence of the window.

    A RULE rather than a side effect, and the difference matters. Freshness makes it very nearly
    emergent -- a landing moves the base, so every sibling becomes behind it -- but "very nearly"
    is what a previous version of this design relied on, and two requests that read the same
    absence before either acted would both proceed. Stating it bounds a night to one change per
    repository, which is what a person reading the rollout the next morning needs to be true.
    """
    try:
        policy = load_factory_policy()
        opened = policy.window_opened_at(LIVE_ESTATE, now)
    except DomainError:
        return _Term(False, (LANDING_POLICY_UNREADABLE,))
    if opened is None:
        # Outside the window, or no window declared. The window term reports both; a second name
        # for one fact is redundancy, so this one is simply unmet with nothing to say.
        return _Term(False, ())
    already = session.scalar(
        select(EstatePrMerge).where(
            EstatePrMerge.repository == repository.lower(),
            EstatePrMerge.created_at >= opened,
        )
    )
    if already is not None:
        return _Term(False, (LANDING_PACE_EXHAUSTED,))
    return _Term(True, ())


@dataclass(frozen=True)
class _RemoteTerms:
    term: _Term
    head_sha: str | None


def _remote_terms(
    repository: str,
    pr_number: int,
    conditions: LandingConditions | None,
    gateway: EstateReadGateway,
) -> _RemoteTerms:
    """Every question only GitHub can answer, asked at the moment the answer has to be true.

    A read that fails is a refusal and never a pass: an unreachable remote is a question that was
    not asked, which is a different thing from a question that was answered no, and neither is
    permission.
    """
    try:
        pull = gateway.read_pull_request(repository=repository, number=pr_number)
    except EstateGatewayError:
        return _RemoteTerms(_Term(False, (LANDING_PULL_REQUEST_UNREADABLE,)), None)

    refusals: list[str] = []
    if pull.landed or not pull.open:
        refusals.append(LANDING_PULL_REQUEST_NOT_OPEN)
    if pull.base_ref != pull.default_branch:
        refusals.append(LANDING_BASE_NOT_DEFAULT_BRANCH)
    if pull.author_login != UPDATE_BOT_LOGIN or not pull.author_is_bot:
        refusals.append(LANDING_AUTHOR_NOT_THE_UPDATE_BOT)
    if pull.mergeable_state == MERGEABLE_UNKNOWN:
        refusals.append(LANDING_MERGEABILITY_UNKNOWN)
    elif pull.mergeable_state != MERGEABLE_CLEAN:
        refusals.append(LANDING_CHECKS_NOT_CLEAN)

    if conditions is None:
        # Already reported by the record term. Everything below is a condition this process was
        # not told, so it cannot be met and there is nothing further to say about it.
        return _RemoteTerms(_Term(False, tuple(refusals)), pull.head_sha)

    fresh = _freshness_term(repository, pull, conditions, gateway)
    kind = _update_type_term(pull, conditions)
    rollout = _rollout_term(repository, pull, conditions, gateway)
    refusals.extend(fresh.refusals)
    refusals.extend(kind.refusals)
    refusals.extend(rollout.refusals)

    met = (
        pull.open
        and not pull.landed
        and pull.base_ref == pull.default_branch
        and pull.author_login == UPDATE_BOT_LOGIN
        and pull.author_is_bot
        and pull.mergeable_state == MERGEABLE_CLEAN
        and fresh.met
        and kind.met
        and rollout.met
    )
    return _RemoteTerms(_Term(met, tuple(refusals)), pull.head_sha)


def _freshness_term(
    repository: str,
    pull: EstatePullRequest,
    conditions: LandingConditions,
    gateway: EstateReadGateway,
) -> _Term:
    """Is the head current with the base it would be squashed onto?

    The condition exists because required checks are not required to be up to date on these
    repositories -- a deliberate estate-wide choice -- so a check can be green against a head that
    is behind, and a squash of that head produces a tree nothing has executed. Where landing
    changes something already serving, that tree is what starts serving.

    It is a POLICY condition rather than a branch setting because a branch setting serialises
    what a person lands too, applies estate-wide behaviour nobody versions, and blocks silently
    where
    this produces a named refusal.
    """
    if not conditions.require_head_current_with_base:
        return _Term(True, ())
    try:
        behind = gateway.commits_behind_base(
            repository=repository, base_ref=pull.base_ref, head_sha=pull.head_sha
        )
    except EstateGatewayError:
        return _Term(False, (LANDING_FRESHNESS_UNREADABLE,))
    if behind > 0:
        return _Term(False, (LANDING_HEAD_NOT_CURRENT_WITH_BASE,))
    return _Term(True, ())


def _update_type_term(pull: EstatePullRequest, conditions: LandingConditions) -> _Term:
    """Is the version delta one the policy permits landing unattended?

    Stricter than the rule governing the repositories where landing is inert, on that rule's own
    reasoning: its premise is that the check gating a bump IS the thing being bumped, so passing
    it exercises the new version exactly as it will be used. Here the rollout job does not run on
    a pull request at all, so a bump to it would first be exercised during the very rollout it is
    supposed to gate.
    """
    kind = update_type_of(pull.title)
    if kind is None:
        return _Term(False, (LANDING_UPDATE_TYPE_UNPARSEABLE,))
    if kind not in conditions.update_types:
        return _Term(False, (LANDING_UPDATE_TYPE_NOT_PERMITTED,))
    return _Term(True, ())


def _rollout_term(
    repository: str,
    pull: EstatePullRequest,
    conditions: LandingConditions,
    gateway: EstateReadGateway,
) -> _Term:
    """Is the rollout this landing would cause still the one the record's criteria describe?

    The record says what a green rollout attests. Nothing else in the estate checks that the
    workflow producing it is still the bytes that statement was made about -- the producer notices
    on its next pass and revokes, but that is a scheduled job rather than a condition on the act.
    Between a workflow landing and that pass, a change could otherwise land under criteria
    describing bytes that no longer exist.

    A repository with NO PIN refuses. A version that declared none is a version that predates the
    condition, and "nobody said which bytes" is not "these bytes are fine".

    **BOTH SIDES ARE READ, and a first version read only the base.** The base is what the rollout
    runs from today, so it is the obvious one -- and it is unchanged until the instant the landing
    happens, which is exactly the hole. A pull request whose own diff edits the rollout workflow
    passes a base-only check by construction: base blob equals the pin, the squash lands the edit,
    and `on: push` then fires bytes nobody transcribed, under criteria written for bytes that no
    longer exist. That is the state this condition was added to prevent, reachable through the
    condition itself. Reading the HEAD as well refuses it: a pull request that changes the file
    cannot have the pinned blob at its head.

    Nothing in the cascade can see a pull request's changed files -- the gateway has no method for
    it, deliberately -- so this is the whole of the protection, and it is why the head read is not
    an optimisation to be skipped when the base already matches.
    """
    pin = conditions.pin_for(repository)
    if pin is None:
        return _Term(False, (LANDING_ROLLOUT_UNPINNED,))
    for ref in (pull.base_ref, pull.head_sha):
        try:
            observed = gateway.blob_sha(repository=repository, path=pin.path, ref=ref)
        except EstateGatewayError:
            return _Term(False, (LANDING_ROLLOUT_UNREADABLE,))
        # `None` is the pinned path naming no file at that ref. A renamed or deleted rollout is a
        # moved rollout; reading it as "nothing to compare" would waive the condition exactly when
        # it matters most.
        if observed is None or observed.lower() != pin.blob_sha.lower():
            return _Term(False, (LANDING_ROLLOUT_MOVED,))
    return _Term(True, ())
