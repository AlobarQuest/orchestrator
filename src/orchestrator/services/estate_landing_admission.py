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

# The platform's word for "a required check has not passed" -- which covers a check that FAILED, a
# check that was abandoned, a check still running, and a required context that never reported at
# all. One word, four causes; see `_checks_term` for the second read that separates them.
MERGEABLE_BLOCKED: Final = "blocked"

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
# A required check REPORTED SOMETHING THIS LANE MAY NOT LAND ON. Kept for exactly that, and
# narrowed: it used to be raised for every `mergeable_state` that was not `clean`, which collapsed
# "a check said no" into "a check said nothing yet" and named the first as the cause of the second.
LANDING_CHECKS_NOT_CLEAN: Final = "landing_checks_not_clean"

# NO CHECK AT THIS HEAD HAS REACHED A VERDICT -- every run that could hold the landing was
# abandoned, was passed over, or never happened. Its own refusal because its remedy is its own:
# a failing check is answered by a person changing something, and a missing one is answered by
# running it, which is what bringing the branch up to date does.
#
# **The platform's composite answer CANNOT tell these apart, and that was measured rather than
# assumed.** One repository, one required check, four head states: a genuinely failing gate, a
# gate abandoned mid-run, a gate still running, and a green gate. The first three all answer
# `blocked` and only the last answers `clean` -- so the composite is a single string covering
# three causes with three different remedies, and reading it alone reports the wrong one for two
# of them. Hence the second read below.
LANDING_CHECKS_AWAITING_VERDICT: Final = "landing_checks_awaiting_verdict"

# A check at this head is STILL RUNNING. Deliberately not the refusal above, because the remedy is
# opposite: bringing the branch up to date would abandon the very run whose verdict is awaited, and
# the next pass gets the answer for free by waiting.
LANDING_CHECKS_IN_FLIGHT: Final = "landing_checks_in_flight"

# The runs at this head could not be read, so which of the three above holds is unknown. A question
# that was not asked is not an answer, and it is certainly not permission -- same polarity as every
# other unreadable in this module.
LANDING_CHECKS_VERDICT_UNREADABLE: Final = "landing_checks_verdict_unreadable"

# The remote has not finished computing mergeability. GitHub answers `unknown` while it works, and
# reporting that as "the checks are not clean" names the wrong cause to whoever reads the report --
# a pull request whose checks are green. Its own refusal, because its remedy is to ask again and
# every other one's is not. Both refuse; only the name differs, which is the whole point.
LANDING_MERGEABILITY_UNKNOWN: Final = "landing_mergeability_unknown"
MERGEABLE_UNKNOWN: Final = "unknown"

# The platform's own words for a workflow run that has finished, and for the finishing states that
# are NOT a verdict about the change. Both are read from the workflow-run listing, which is the
# only check-shaped surface this estate's App may read at all: it holds no `checks` permission, so
# the check-runs API answers 403 and the runs listing is what remains.
#
# **`success` is deliberately absent, and every other string is deliberately absent.** A run that
# passed cannot be what holds a landing, so it is neither a verdict to refuse on nor a missing one
# to wait for. Anything else -- `failure`, `timed_out`, `action_required`, and any word the
# platform has not yet invented -- is read as a verdict this lane may not land on. That polarity is
# the whole safety of the split: a conclusion nobody enumerated fails toward refusing, never toward
# calling itself absent and inviting the branch to be freshened.
RUN_COMPLETED: Final = "completed"
RUN_SUCCEEDED: Final = "success"
NO_VERDICT_CONCLUSIONS: Final = frozenset({"cancelled", "skipped", "stale"})

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
#
# **RAISED ONLY UNDER A POLICY VERSION THAT DECIDES BY UPDATE TYPE**, which is every version before
# the fifth (ADR-0036). They are not dead under the fifth: the two repositories in this contract
# ship separately and the served conditions say which rule applies, so a version predating the
# outcome rule is what this reader meets whenever it is deployed ahead of the server -- and after
# any rollback of it.
LANDING_UPDATE_TYPE_UNPARSEABLE: Final = "landing_update_type_unparseable"
LANDING_UPDATE_TYPE_NOT_PERMITTED: Final = "landing_update_type_not_permitted"

# ADR-0036, and raised only under a version that decides on the OUTCOME. The exclusion is not a
# statement about how large a change is; it names the ecosystems whose changes the required checks
# on a pull request do not exercise. On a repository where landing changes something already
# serving, the rollout job is gated on a push to the default branch and runs on no pull request at
# all, so a bump reaching it would be exercised for the first time by the very rollout it gates.
#
# UNREADABLE IS ITS OWN ANSWER AND REFUSES. The ecosystem is the second segment of the update
# bot's branch name, which every pull request it opens carries -- so a name this cannot read is
# never "the bot named no ecosystem". It is this program failing to read what the exclusion is
# about, and permitting on that would land a change whose exclusion nobody can re-check. The
# estate's landing ledger reaches the same conclusion about the same fact, for the same reason.
LANDING_ECOSYSTEM_EXCLUDED: Final = "landing_ecosystem_excluded"
LANDING_ECOSYSTEM_UNREADABLE: Final = "landing_ecosystem_unreadable"

# The update bot's branch naming, from which the ecosystem is read: `dependabot/<ecosystem>/<rest>`.
_BRANCH_PREFIX: Final = "dependabot/"

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
class HeadCheckRun:
    """One workflow run at a head, as the classification below needs it.

    Run-level rather than job-level, and that is the right grain HERE rather than a simplification.
    The question is *what does this head currently report*, and a re-run supersedes its
    predecessor: the run carries the latest attempt's answer, which is the answer branch protection
    is reading too. Job-level granularity matters where the question is what a PARTICULAR attempt
    did, and this is not that question.
    """

    status: str
    conclusion: str | None


@dataclass(frozen=True)
class EstatePullRequest:
    """What the remote says about the pull request, as this module needs it."""

    number: int
    title: str
    head_sha: str
    base_ref: str
    # The branch this pull request would be squashed FROM, which is where the update bot states
    # the ecosystem. Carried as the raw ref rather than as a parsed ecosystem so that the one
    # place that reads it is the one place that decides what an unreadable name means.
    head_ref: str
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

    def head_check_runs(self, *, repository: str, head_sha: str) -> tuple[HeadCheckRun, ...]: ...


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
    # ADR-0024. The fact the freshness-derived criterion below takes as an argument, served so the
    # OTHER consumer -- the out-of-process reporting agent, which cannot import this module -- can
    # ask the same question this process asks. It is a fact rather than a verdict: what to do with
    # it differs between the two, and only the term that read the blobs knows it.
    rollout_base_matches_pin: bool


def freshness_derived_refusals(
    refusals: tuple[str, ...] | frozenset[str] | set[str],
    *,
    rollout_base_matches_pin: bool,
) -> frozenset[str]:
    """Which of these refusals are produced by the head's POSITION relative to its base, and say
    nothing about the change itself? ADR-0024.

    **ONE CONCEPT, TWO CONSUMERS, AND THEY ASK DIFFERENT QUESTIONS OF IT.**
    `qualifies_for_branch_update` below asks *may the lane act on this* -- yes when every obstacle
    is either freshness-derived or deliberate. The reporting agent asks *is this a finding* --
    no, beside a refusal current policy can never clear, when what remains is freshness-derived.
    Expressed once so a fifth member is answered in both places by construction, which is the
    whole reason ADR-0024 rules on the class rather than on the case.

    ## The criterion, and the discriminator that keeps it narrow

    Being behind IS the position, so it is derived whenever it is present. A rollout pin that
    differs is derived only when the BASE carries the pinned bytes: under that condition the head
    simply predates a workflow change and bringing the base's commits in carries the pinned bytes
    with it, while a base that does not carry them means the workflow genuinely moved and no
    amount of freshening puts that right.

    **A failing check is deliberately NOT a member, and it is the case that keeps this honest.**
    Freshening re-runs checks and might turn one green, so *would freshening clear it?* is too
    loose a test and would silence a red build. The discriminator is *does this say anything about
    the change?* -- and a failing check does.

    ## The head-behind conjunct, which is not decoration

    A refusal cannot be caused by a position the head is not in. `qualifies_for_branch_update`
    supplies that fact through its own return condition, so adding it here changes nothing for
    that caller -- but the reporting consumer has no such guard, and without it a pull request
    whose OWN DIFF edits the pinned rollout workflow (base carrying the pinned bytes, head current
    with its base, head blob differing) would be classed as merely stale. That is `_rollout_term`'s
    founding case and it must always report.

    Returned members are intersected with what was actually raised, so the answer describes THESE
    refusals rather than a vocabulary.
    """
    present = set(refusals)
    if LANDING_HEAD_NOT_CURRENT_WITH_BASE not in present:
        return frozenset()
    derived = {LANDING_HEAD_NOT_CURRENT_WITH_BASE}
    if rollout_base_matches_pin:
        derived.add(LANDING_ROLLOUT_MOVED)
    return frozenset(derived & present)


def qualifies_for_branch_update(
    refusals: tuple[str, ...], *, rollout_base_matches_pin: bool
) -> bool:
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
    save the single carve-out below, which is keyed on two FACTS rather than on membership of a
    set. That polarity is what this lane argues for everywhere else: an unclassified code must fail
    toward refusing rather than toward acting.

    ## The carve-out: a rollout pin that differs BECAUSE the head is stale

    `_rollout_term` compares the pinned workflow's bytes at the base and at the head, so a head
    opened before that file last changed reports `landing_rollout_moved` -- a refusal CAUSED by
    being behind, which is the very condition this rule exists to clear. Read as an obstacle it is
    a deadlock, and it was one: the five `alobarquest/brain` pull requests open on 2026-08-16 were
    each refused for being behind their base and disqualified from the one mechanism that would
    bring them up to date.

    **That carve-out is no longer stated here.** ADR-0024 found the same question being asked by a
    second consumer and made it a criterion -- `freshness_derived_refusals` above -- of which this
    is now one reader. What that function excuses, and why a genuinely moved workflow is not
    excused, is stated there.

    Note what this DID cost, since an earlier version of this docstring argued the opposite:
    the criterion carries the "head is behind" conjunct itself, where this function had left it to
    the return below on the grounds that restating it would give one fact two sources. That
    reasoning held only while this was the sole reader. The two are equivalent HERE -- the return
    requires the same thing -- so nothing about this answer moved.

    **The carve-out is self-limiting rather than trusted.** After an update the term re-evaluates
    against the NEW head: a pull request that does not touch the workflow then carries the pinned
    bytes and proceeds, while one whose own diff edits that file still differs and is still refused
    -- `_rollout_term`'s founding case, untouched. The cost of that ambiguity is one build on a
    pull request that will not land; nothing in the cascade can see a pull request's changed files,
    so no narrower reading is available here.

    ## The third subtraction: a head whose checks reached no verdict

    `landing_checks_awaiting_verdict` does not disqualify, and it is the only refusal here that is
    excused because bringing the branch up to date is what ANSWERS it rather than what tolerates
    it. The two above clear on their own and this one does not: nothing else in the estate re-runs
    a check that was abandoned, so a pull request holding one waits forever while its own report
    says the checks are not clean.

    **It is not folded into the freshness criterion**, though it would qualify at a glance. That
    criterion asks *is this refusal produced by the head's POSITION?* and this one is not -- it is
    produced by what happened to the runs. Folding it in would also excuse it for the reporting
    consumer, which reads the same criterion to decide what is a finding, and there the answer is
    different: an unanswered check beside a permanent exception is still worth saying.

    **A failing check remains disqualifying, and that boundary is the whole value of the split.**
    Freshening cannot turn a red verdict green, so offering it one spends a build to re-learn the
    same answer -- and a build running is indistinguishable from progress to whoever reads the
    report. `_checks_term` is where the two are told apart, and it does so by reading the runs
    rather than by trusting a word that covers both.

    ## The shape, because it will recur

    **When a refusal can be CAUSED by the condition another rule exists to clear, the two rules
    deadlock.** This test must be keyed on refusals that are genuinely independent of freshness --
    not merely on the ones that happened to be live when it was written.
    """
    # `False` withholds the carve-out, so every path that did not positively observe a matching
    # base leaves the refusal standing.
    remainder = (
        set(refusals)
        - freshness_derived_refusals(refusals, rollout_base_matches_pin=rollout_base_matches_pin)
        - DELIBERATE_REFUSALS
        - {LANDING_CHECKS_AWAITING_VERDICT}
    )
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
        branch_update_qualifies=qualifies_for_branch_update(
            tuple(refusals), rollout_base_matches_pin=remote.rollout_base_matches_pin
        ),
        rollout_base_matches_pin=remote.rollout_base_matches_pin,
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
    # Carried up rather than recomputed: the blobs were read once, by the term that owns them.
    rollout_base_matches_pin: bool


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
        return _RemoteTerms(_Term(False, (LANDING_PULL_REQUEST_UNREADABLE,)), None, False)

    refusals: list[str] = []
    if pull.landed or not pull.open:
        refusals.append(LANDING_PULL_REQUEST_NOT_OPEN)
    if pull.base_ref != pull.default_branch:
        refusals.append(LANDING_BASE_NOT_DEFAULT_BRANCH)
    if pull.author_login != UPDATE_BOT_LOGIN or not pull.author_is_bot:
        refusals.append(LANDING_AUTHOR_NOT_THE_UPDATE_BOT)
    if pull.mergeable_state == MERGEABLE_UNKNOWN:
        refusals.append(LANDING_MERGEABILITY_UNKNOWN)
        checks = _Term(False, ())
    else:
        checks = _checks_term(repository, pull, gateway)
        refusals.extend(checks.refusals)

    if conditions is None:
        # Already reported by the record term. Everything below is a condition this process was
        # not told, so it cannot be met and there is nothing further to say about it.
        return _RemoteTerms(_Term(False, tuple(refusals)), pull.head_sha, False)

    fresh = _freshness_term(repository, pull, conditions, gateway)
    kind = _bump_term(pull, conditions)
    rollout = _rollout_term(repository, pull, conditions, gateway)
    refusals.extend(fresh.refusals)
    refusals.extend(kind.refusals)
    refusals.extend(rollout.term.refusals)

    met = (
        pull.open
        and not pull.landed
        and pull.base_ref == pull.default_branch
        and pull.author_login == UPDATE_BOT_LOGIN
        and pull.author_is_bot
        and pull.mergeable_state != MERGEABLE_UNKNOWN
        and checks.met
        and fresh.met
        and kind.met
        and rollout.term.met
    )
    return _RemoteTerms(_Term(met, tuple(refusals)), pull.head_sha, rollout.base_matches_pin)


def _checks_term(
    repository: str,
    pull: EstatePullRequest,
    gateway: EstateReadGateway,
) -> _Term:
    """Do the checks at this head say NO, say NOTHING YET, or say nothing AT ALL?

    Three answers where the platform's composite offers one word. `clean` is the only value that
    permits, and every other value used to raise a single refusal naming a failing check -- which
    is true of one cause and false of the other two, and false in the direction that matters: the
    remedy for a check that never reported is to run it, and this lane owns the act that does so.

    ## The second read, and why it is not optional

    `mergeable_state` is a scalar. Measured against one repository with one required check, a
    genuinely failing gate, a gate abandoned mid-run and a gate still running ALL answer `blocked`,
    and three live pull requests in this estate's own ledger repositories answer `blocked` with
    every run at their head abandoned. No amount of care with the composite recovers the
    difference, so the runs at the head are read.

    ## Only `blocked` is inquired into

    Every other unpermitted value is a statement about the branch rather than about a verdict --
    a conflict, a draft, a check that failed without being required -- and none is made right by
    a fresher base. They keep the original refusal untouched, and a conflicted branch is thereby
    never offered to an update that would fail at the remote anyway.

    ## The order of the three questions is the safety

    A failing run outranks one still going, which outranks the absence of any verdict: a head
    carrying one red run and one still running has said no, whatever else is pending. Reading
    those in the other order would let an in-flight sibling excuse a failure.

    ## Residual, named rather than implied

    This reads every run at the head, not the REQUIRED ones -- the required-context list needs a
    permission this estate's App does not hold. So an unrelated failing workflow holds a pull
    request that branch protection would have let through. That is the conservative direction and
    it is the same residual the composite's own note already carries.
    """
    if pull.mergeable_state == MERGEABLE_CLEAN:
        return _Term(True, ())
    if pull.mergeable_state != MERGEABLE_BLOCKED:
        return _Term(False, (LANDING_CHECKS_NOT_CLEAN,))
    try:
        runs = gateway.head_check_runs(repository=repository, head_sha=pull.head_sha)
    except EstateGatewayError:
        return _Term(False, (LANDING_CHECKS_VERDICT_UNREADABLE,))
    if any(
        run.status == RUN_COMPLETED
        and run.conclusion != RUN_SUCCEEDED
        and run.conclusion not in NO_VERDICT_CONCLUSIONS
        for run in runs
    ):
        return _Term(False, (LANDING_CHECKS_NOT_CLEAN,))
    if any(run.status != RUN_COMPLETED for run in runs):
        return _Term(False, (LANDING_CHECKS_IN_FLIGHT,))
    return _Term(False, (LANDING_CHECKS_AWAITING_VERDICT,))


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


def ecosystem_of(head_ref: str) -> str | None:
    """Which package ecosystem the update bot says this branch belongs to, or None.

    The second segment of `dependabot/<ecosystem>/<rest>`, which is the same fact the estate's
    landing ledger reads and the same one the update bot's own metadata action derives. Read from
    the BRANCH rather than from the title, unlike the version delta above, and the two are not in
    tension: the branch goes stale about the VERSION when the bot rewrites a pull request in place,
    and it cannot go stale about the ecosystem, because an update never moves between them.

    None for any name that is not that shape. What that means is the caller's to decide, and it
    decides refuse.
    """
    if not head_ref.startswith(_BRANCH_PREFIX):
        return None
    rest = head_ref[len(_BRANCH_PREFIX) :]
    ecosystem, separator, remainder = rest.partition("/")
    if not separator or not ecosystem or not remainder:
        return None
    return ecosystem


def _bump_term(pull: EstatePullRequest, conditions: LandingConditions) -> _Term:
    """May this bump land unattended -- and by WHICH RULE is that asked?

    **THE SERVED CONDITIONS SAY WHICH RULE APPLIES, and nothing here chooses.** A version that
    names the excluded ecosystems decides on the OUTCOME (ADR-0036); one that names none decides
    on the version delta, which is every version before the fifth. That is not a transitional
    courtesy: the party holding the policy and the party evaluating it are different processes
    shipped separately, so this reader meets both shapes in production and must answer about the
    version actually in force rather than about the one it was written alongside.

    ## Why the delta stopped deciding

    It never said whether the bump WORKS. Both this lane and the cascade governing the inert half
    of the estate already gate on the required checks passing; the update-type condition sat on top
    of that gate and asked a question about the version NUMBER. A requirement range states no delta
    at all, so no rule about deltas could ever reach one -- and five green pull requests sat
    unlandable for that reason alone, while a `semver-patch` that broke at runtime would have
    passed.

    ## Why an exclusion survives, and what it is about

    The outcome rule rests on the required checks having exercised what changed. Where they have
    not, the outcome says nothing. On these repositories the rollout job is gated on a push to the
    default branch and runs on no pull request -- visible on every subject as a skipped job beside
    the passing ones -- so a change reaching it is first exercised by the rollout it is supposed to
    gate. The excluded set names that, by ecosystem, exactly as the cascade's own exclusion does.

    **It is not the whole of the protection and is not meant to be.** `_rollout_term` compares the
    pinned workflow's bytes at the head, so a change to that file is refused whatever ecosystem it
    came from and whoever wrote it. The exclusion reaches what the pin cannot: a workflow this
    estate runs that no required check executes, whose bytes are not pinned by any record.
    """
    if True:
        return _update_type_term(pull, conditions)
    ecosystem = ecosystem_of(pull.head_ref)
    if ecosystem is None:
        return _Term(False, (LANDING_ECOSYSTEM_UNREADABLE,))
    if ecosystem in conditions.excluded_ecosystems:
        return _Term(False, (LANDING_ECOSYSTEM_EXCLUDED,))
    return _Term(True, ())


def _update_type_term(pull: EstatePullRequest, conditions: LandingConditions) -> _Term:
    """Is the version delta one the policy permits landing unattended?

    The rule every policy version before the fifth declares, retained because those versions are
    retained: a record approved under one is judged by what it actually said, and this reader is
    served that shape whenever it runs ahead of the party holding the policy.

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


@dataclass(frozen=True)
class _RolloutTerm:
    term: _Term
    # DID THE BASE CARRY THE PINNED BYTES? Reported beside the refusal because the refusal itself
    # cannot tell "this head is stale" from "this workflow moved", and only the comparison below
    # can. False wherever the question was not reached or not answered -- an unpinned repository
    # and an unreadable blob are both merely *not known* to match, and a reader that treats
    # not-known as matching would waive a condition on the strength of a failed read.
    base_matches_pin: bool


def _blob_matches(observed: str | None, expected: str) -> bool:
    """One comparison, so the refusal and the fact beside it can never disagree.

    Case-folded, for the reason the pin's own test gives: GitHub serves object names lower-cased
    and a human transcribing one may not. Computing the fact with a second, raw comparison would
    withhold the carve-out for an upper-cased pin alone -- refusing, but for a reason nobody could
    read off either value.
    """
    return observed is not None and observed.lower() == expected.lower()


def _rollout_term(
    repository: str,
    pull: EstatePullRequest,
    conditions: LandingConditions,
    gateway: EstateReadGateway,
) -> _RolloutTerm:
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

    **The two sides answer DIFFERENT questions once the answer leaves here**, which is why the base
    comparison is carried out as well as the refusal. A head that differs while the base matches is
    a head that predates the file's last change, and that is curable by bringing the base's commits
    into it; a base that differs is a workflow this record was not written about, which nothing
    about the branch can put right. `qualifies_for_branch_update` is the only reader, and this term
    is the only party that can tell them apart -- the refusal is one string for both.

    A `None` is the pinned path naming no file at that ref. A renamed or removed rollout is a moved
    rollout; reading it as "nothing to compare" would waive the condition exactly when it matters
    most.
    """
    pin = conditions.pin_for(repository)
    if pin is None:
        return _RolloutTerm(_Term(False, (LANDING_ROLLOUT_UNPINNED,)), False)
    try:
        at_base = gateway.blob_sha(repository=repository, path=pin.path, ref=pull.base_ref)
        if not _blob_matches(at_base, pin.blob_sha):
            return _RolloutTerm(_Term(False, (LANDING_ROLLOUT_MOVED,)), False)
        at_head = gateway.blob_sha(repository=repository, path=pin.path, ref=pull.head_sha)
    except EstateGatewayError:
        # False even when the BASE read succeeded and matched: a term that could not finish
        # answering has not established the pair the carve-out rests on. Unobservable today --
        # `landing_rollout_unreadable` disqualifies on its own -- so it is stated rather than
        # left to whichever value happened to be in hand.
        return _RolloutTerm(_Term(False, (LANDING_ROLLOUT_UNREADABLE,)), False)
    if not _blob_matches(at_head, pin.blob_sha):
        return _RolloutTerm(_Term(False, (LANDING_ROLLOUT_MOVED,)), True)
    return _RolloutTerm(_Term(True, ()), True)
