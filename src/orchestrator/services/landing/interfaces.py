"""The vocabulary both landing lanes speak: read shapes, gateway protocols and refusal codes.

The estate lane (a Dependabot pull request into a repository where landing changes something
already serving, ADR-0019) and the inert lane (the opposite population) ask GitHub the same
questions and refuse in the same words. What they share lives here, so neither lane imports the
other to reach it, and no admission module imports an act module for a constant.

Types, protocols and constants only. This module imports nothing from `services.landing` and no HTTP
client, which `tests/architecture/test_landing_direction.py` holds it to; the terms composed from
this vocabulary are in `terms`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

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
# all. One word, four causes; see `checks_term` for the second read that separates them.
MERGEABLE_BLOCKED: Final = "blocked"

# The platform's word for "a NON-required check is not passing". Same second read as `blocked`
# below, deliberately: both mean some run at this head has not said yes, and which of the three
# causes holds is a question about the RUNS rather than about which composite word arrived. Giving
# it a cruder answer of its own would rebuild, one state over, the collapse `checks_term` already
# paid to take apart.
MERGEABLE_UNSTABLE: Final = "unstable"

# THE BRANCH CANNOT BE MERGED AT ALL -- git cannot compute the result. Nothing to do with checks,
# which on a conflicted branch are commonly green: one of this estate's own repositories carried
# a pull request on 2026-09-05 with TWO `Quality` runs at `success`, diverged two ahead and three
# behind its base, and this lane reported `landing_checks_not_clean` about it. A reader following
# that report goes and stares at CI that is fine.
#
# Its own refusal because its remedy is its own, and is nobody's here: a conflict is answered by
# rebasing the branch, which for a Dependabot pull request means Dependabot's own next cycle. It is
# emphatically NOT answered by bringing the head up to date -- that call fails at the remote -- and
# `qualifies_for_branch_update` withholds the update by construction, since it subtracts a named
# few and disqualifies everything else.
MERGEABLE_DIRTY: Final = "dirty"

# This deployment has not been told it may land anything. Default false, unconfigured refusing.
LANDING_NOT_ENABLED: Final = "landing_not_enabled"

# No App credentials, so nothing can be minted and no call can be made. Asked before the remote is
# touched, so the gate and the actor read one answer about which credentials are in play.
LANDING_APP_CREDENTIALS_MISSING: Final = "landing_app_credentials_missing"

# What the estate says about landing on this repository's default branch. Each lane exists for
# one answer only: the estate lane for repositories where landing changes something already
# serving, the inert lane for the ones where it does not.
LANDING_ESTATE_SOURCE_UNCONFIGURED: Final = "landing_estate_source_unconfigured"
LANDING_ESTATE_SOURCE_UNREADABLE: Final = "landing_estate_source_unreadable"
LANDING_ESTATE_UNKNOWN: Final = "landing_estate_unknown"

# The hours policy declares for changing something already serving.
LANDING_OUTSIDE_CHANGE_WINDOW: Final = "landing_outside_change_window"

# What GitHub says about the pull request itself.
LANDING_PULL_REQUEST_UNREADABLE: Final = "landing_pull_request_unreadable"
LANDING_PULL_REQUEST_NOT_OPEN: Final = "landing_pull_request_not_open"
LANDING_BASE_NOT_DEFAULT_BRANCH: Final = "landing_base_not_default_branch"
# The head conflicts with its base. See `MERGEABLE_DIRTY` for why this is not a statement about
# any check, and for the live case that showed it being reported as one.
LANDING_PULL_REQUEST_CONFLICTED: Final = "landing_pull_request_conflicted"

# THE PLATFORM SAID SOMETHING THIS LANE CANNOT NAME. `draft`, `behind`, `has_hooks`, and whatever
# GitHub invents next all reach here. Refusing is right; asserting a CAUSE is not, and until
# 2026-09-05 every one of them was reported as `landing_checks_not_clean` -- an assertion about a
# check that may never have run.
#
# This is the general half of that fix rather than a second patch for one state: the defect was
# not that `dirty` lacked a name, it was that an unrecognised word was given somebody else's. A
# state named later gets a name; until then it gets an honest absence of one, and it refuses
# either way.
LANDING_MERGEABILITY_UNRECOGNISED: Final = "landing_mergeability_unrecognised"
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
# other unreadable in either admission.
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

# ADR-0036, and raised only under a version that decides on the OUTCOME. The exclusion is not a
# statement about how large a change is; it names the ecosystems whose changes the required checks
# on a pull request do not exercise. On a repository where landing changes something already
# serving, the rollout job is gated on a push to the default branch and runs on no pull request at
# all, so a bump reaching it would be exercised for the first time by the very rollout it gates.
#
# UNREADABLE IS ITS OWN ANSWER AND REFUSES. The ecosystem is the second segment of the update
# bot's branch name, which every pull request it opens carries UNDER THE DEFAULT NAMING -- so a
# name this cannot read is never "the bot named no ecosystem". It is this program failing to read
# what the exclusion is about, and permitting on that would land a change whose exclusion nobody
# can re-check. The estate's landing ledger reaches the same conclusion about the same fact.
#
# The dependency it rests on, named rather than assumed: a repository setting
# `pull-request-branch-name.separator` changes that shape, and every pull request there would then
# refuse here forever. Neither repository sets it and nothing pins that they do not, so the failure
# would be a lane that goes quiet for a reason no refusal names.
LANDING_ECOSYSTEM_EXCLUDED: Final = "landing_ecosystem_excluded"
LANDING_ECOSYSTEM_UNREADABLE: Final = "landing_ecosystem_unreadable"

# Whether the rollout this landing would cause is still the one the record's criteria describe.
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

# Recorded on the one ambiguous outcome: the remote refused and the confirming read also failed,
# so a landing cannot be ruled out. Both acts write it, because the row it is written into is the
# same row and one column may not carry two vocabularies.
MERGE_REFUSED_BY_REMOTE: Final = "merge_refused_by_remote"

# The prefix of every gateway code raised BEFORE anything is sent. The gateway's `_headers()`
# (`GitHubEstatePullRequests`, in `estate_pr_merge`) mints the App token first, so a mint failure
# means the request provably did not leave this process -- and a landing that cannot have happened
# must never be recorded, because the row is permanent and would bar the pull request forever on
# one transient outage. Everything else in `submit_merge`
# happens at or after the send, where a lost response and a refusal are indistinguishable and the
# conservative record is the right answer.
NEVER_SENT: Final = "app_token_mint:"

# How the remote is asked to bring a branch onto the default branch. GitHub's vocabulary, spelled
# here because this is the one place either value crosses to it, and named rather than written
# inline so a caller states which it means instead of repeating a literal.
#
# SQUASH discards the branch's own commits and lands one new commit for its content. That is right
# for a branch whose commits nobody will merge from again, which is every subject either lane has
# had until now.
#
# MERGE_COMMIT keeps them, and is right for exactly one thing: a branch replaying commits from a
# source this estate does not own and WILL merge from again. Git resolves a later merge against the
# most recent commit both sides share, so a squash leaves that shared point where it was -- the
# content arrives without the commits carrying it, and the next sync compares against the stale
# point and finds both sides having rewritten the same lines. It cannot tell the two rewrites are
# one edit, so it conflicts, on files nobody here ever touched. Measured 2026-09-13 on
# `claude-octopus#14`: 18 conflicting paths, and all 17 content files' fork blobs byte-identical to
# some upstream commit's blob.
MERGE_COMMIT: Final = "merge"
SQUASH: Final = "squash"


@dataclass(frozen=True)
class MergeOutcome:
    landed: bool
    commit_sha: str | None
    status_code: int | None


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


def gateway_failure_detail(error: EstateGatewayError) -> str:
    """What the remote said, for a person reading a refusal -- the code AND the status.

    THE STATUS WAS CAPTURED AND THEN DISCARDED, which is the defect this closes. Three raise
    sites carry `response.status_code`, every one of them the case where the remote answered and
    said no; and every message rendered from them named only the code, so an operator's whole
    answer was `branch_update_status` -- a refusal that does not say what was refused. Found
    2026-09-01 when the inert lane's branch update began failing: separating "the App may not
    write this" from "the head moved under us" took six probes and a controlled differential, and
    a three-digit number would have taken none.

    A status of `None` renders as the bare code, deliberately. Most raise sites never reach the
    remote at all -- a timeout, an unparseable body -- and inventing a number for them would say
    the remote answered when it did not.

    IDENTIFIERS ARE NOT MESSAGES, and this is for messages only. The two `reason_code` values
    composed from the same errors are stored vocabulary that other readers key on; widening them
    with a number that varies per occurrence would make one value into many.
    """
    if error.status_code is None:
        return error.code
    return f"{error.code} (HTTP {error.status_code})"


class EstateReadGateway(Protocol):
    """The reads every term below needs. Injected, so the whole cascade runs with no network."""

    def read_pull_request(self, *, repository: str, number: int) -> EstatePullRequest: ...

    def commits_behind_base(self, *, repository: str, base_ref: str, head_sha: str) -> int: ...

    def blob_sha(self, *, repository: str, path: str, ref: str) -> str | None: ...

    def head_check_runs(self, *, repository: str, head_sha: str) -> tuple[HeadCheckRun, ...]: ...


@dataclass(frozen=True)
class OpenPullRequest:
    """One row of a repository's open pull requests, as the sibling rule needs it (ADR-0045).

    Only what the list answer carries, so nothing here costs a read per pull request: the number
    to address it by, the head the rest of the rule compares against, and the author test the
    admission cascade already applies to the target.
    """

    number: int
    head_sha: str
    author_login: str
    author_is_bot: bool


@dataclass(frozen=True)
class PullRequestCommit:
    """One commit on a pull request's branch, as the sibling rule classifies it (ADR-0045).

    The logins are the LINKED accounts GitHub resolved from the commit's author and committer, and
    `None` when an address links to no account. None is carried as None rather than defaulted to an
    empty string, because "no linked account" is its own answer to the ownership question and must
    not read as "an account with an empty name".
    """

    sha: str
    author_login: str | None
    committer_login: str | None
    verified: bool


class SiblingReadGateway(EstateReadGateway, Protocol):
    """The composed answer's reads plus the two the sibling rule adds. ADR-0045.

    A narrower protocol than a widened `EstateReadGateway`, deliberately: the unit-bound landing
    path shares that base and nothing on it asks about siblings, so its fakes grow nothing. The two
    branch-update acts take this one. It lives here, beside the shared read shapes, because this
    module imports nothing from either lane, so every party can reach it without an import cycle.
    """

    def open_pull_requests(self, *, repository: str) -> tuple[OpenPullRequest, ...]: ...

    def pull_request_commits(
        self, *, repository: str, number: int
    ) -> tuple[PullRequestCommit, ...]: ...


@dataclass(frozen=True)
class Term:
    met: bool
    refusals: tuple[str, ...]
