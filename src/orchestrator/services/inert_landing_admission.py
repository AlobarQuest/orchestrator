"""May the orchestrator land this pull request into a repository where landing on the default
branch changes nothing already serving, and if not, why? ADR-0038 part 2.

**This module answers; it does not act.** Nothing it returns causes anything to happen, and the
one thing it holds -- a read-only view of GitHub -- it uses to ask questions rather than to change
anything. Same separation as both sibling lanes, and for the same reason: the composed answer can
be read against the real waiting population before anything obeys it.

## Why this is a third path and not a branch inside the second

The lane that lands into repositories where landing DOES change something already serving is built
around a change record: a human pinned that repository, its acceptance criteria and its remedy, and
half its terms are functions of that record. **There is no record here, and there cannot be one** --
a record exists to carry acceptance criteria and a rollback plan for a rollout, and a repository
where landing deploys nothing has no subject for any of the three.

So the record terms, the change window and the pace rule are all absent, and their absence is
decided rather than inherited. What authorises this instead is a block of the same policy document,
naming a population a person pinned and the conditions on landing into it -- and the conditions
that remain are the ones that are about the CHANGE rather than about the rollout it would cause.

**The estate term is inverted, and that inversion is the whole reason this is a separate function.**
Its sibling passes only on an explicit `redeploys`; this one passes only on an explicit `inert`.
Branching inside that one would have dragged the record terms onto a lane that has no record.

## The population is declared by a person and confirmed by the estate

The policy names which repositories a human admitted; App Brain says whether landing there is still
inert; a disagreement refuses. Fail-closed in both directions: a repository that quietly starts
redeploying stops being landable by this lane rather than being landed wrongly, and one the estate
calls inert but nobody declared is not landable either.

## Freshness is required and there is no pace

Requiring the head to be current with its base is a TIGHTENING over the workflow this replaces,
which required nothing -- branch protection is not up-to-date-gated anywhere in this estate,
deliberately. It is warranted for a reason about the default branch rather than about production:
a squash of a behind head produces a tree nothing executed, and that branch is what every build
session branches from and what default-branch CI now runs on.

Given freshness, a landing stales every sibling, so at most one pull request per repository is
landable per pass and the rest are freshened for the next one. **A pace rule would be a second
mechanism producing an effect the first already produces.** The deploying lane does carry one, and
the difference is not an oversight: there, pace bounds how often something already serving may
change, which is a fact about production rather than about staleness.

## `satisfied` is a positive conjunction, never "no refusal was raised"

Every term computes its own affirmative answer and the answers are ANDed; the refusal list is built
alongside for the reader. An answer whose affirmative case is an empty objection list is the
fail-open shape this repository keeps finding. And every term is reported: an operator asking why
nothing landed wants the whole list, not the first thing that went wrong.

## The refusal vocabulary is SHARED with the deploying lane wherever the condition is the same

A head behind its base, a check that reported nothing, an unreadable pull request: these are one
condition asked by two lanes, and giving each lane its own spelling would be two vocabularies that
must agree -- which, in this estate, means two that do not until something checks. The codes below
that are new are new because the CONDITION is new. Which lane refused is answered by which surface
was asked, not by the spelling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import EstatePrMerge
from orchestrator.services.estate_landing import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    EstateLandingSource,
)
from orchestrator.services.estate_landing import (
    SOURCE_UNCONFIGURED as ESTATE_SOURCE_UNCONFIGURED,
)
from orchestrator.services.estate_landing_admission import (
    LANDING_ALREADY_RECORDED,
    LANDING_APP_CREDENTIALS_MISSING,
    LANDING_BASE_NOT_DEFAULT_BRANCH,
    LANDING_ESTATE_SOURCE_UNCONFIGURED,
    LANDING_ESTATE_SOURCE_UNREADABLE,
    LANDING_ESTATE_UNKNOWN,
    LANDING_MERGEABILITY_UNKNOWN,
    LANDING_NOT_ENABLED,
    LANDING_PULL_REQUEST_NOT_OPEN,
    LANDING_PULL_REQUEST_UNREADABLE,
    MERGEABLE_UNKNOWN,
    EstateGatewayError,
    EstatePullRequest,
    EstateReadGateway,
    Term,
    checks_term,
    ecosystem_exclusion_term,
    freshness_term,
    qualifies_for_branch_update,
)
from orchestrator.services.inert_landing_policy import (
    RULES_UNDECLARED,
    InertLandingPolicySource,
    InertLandingRules,
)
from orchestrator.services.inert_landing_policy import (
    SOURCE_UNCONFIGURED as POLICY_SOURCE_UNCONFIGURED,
)

# The estate says landing on this repository's default branch DOES change something already
# serving -- so this is the wrong lane for it, and the right one exists. Its own refusal rather
# than the sibling's `landing_target_not_routed`, because that name says "nobody routed this
# change" and here the answer is the opposite fact about the repository.
INERT_LANDING_TARGET_NOT_INERT: Final = "inert_landing_target_not_inert"

# Where the policy declaring this population is read from, and what the read said. Three causes,
# three different people: an environment variable nobody set, a service refusing or unreachable,
# and a document that loaded and declared no such population at all.
INERT_LANDING_POLICY_SOURCE_UNCONFIGURED: Final = "inert_landing_policy_source_unconfigured"
INERT_LANDING_POLICY_SOURCE_UNREADABLE: Final = "inert_landing_policy_source_unreadable"
INERT_LANDING_RULES_UNDECLARED: Final = "inert_landing_rules_undeclared"

# The policy loaded and this repository is not in the population a person pinned. The estate may
# well agree that landing here is inert; that is not the same as somebody having decided this
# lane may land into it, and this refusal is the difference between the two.
INERT_LANDING_REPOSITORY_NOT_DECLARED: Final = "inert_landing_repository_not_declared"

# The pull request was not opened by an account the policy names.
#
# **THE ONLY THING BOUNDING WHICH PULL REQUESTS THIS LANE SEES AT ALL.** The deploying lane gets
# an upstream filter for free -- its producer refuses a pull request no bot opened, so a
# machine-authored one never becomes a change record and never becomes a subject there. There is
# no record here and therefore no upstream filter, and four of the declared repositories carry a
# factory caller workflow: a factory-opened pull request with green checks is a real subject
# rather than a hypothetical one, and this lane asks none of the questions a factory landing
# rests on -- whether the unit completed, whether the verifier decided its criteria from observed
# evidence, whether a human's authority approval is bound to the envelope's fingerprint.
INERT_LANDING_AUTHOR_NOT_PERMITTED: Final = "inert_landing_author_not_permitted"


@dataclass(frozen=True)
class InertLandingAdmission:
    """The composed answer, plus what the act needs in order to name what it acted on."""

    satisfied: bool
    refusals: tuple[str, ...]
    repository: str
    pr_number: int
    head_sha: str | None
    # The DOCUMENT's version, which is what the landing commit's trailer carries and what the
    # estate's ledger reads back out of it. `None` whenever the policy could not be read, which
    # is also whenever the answer cannot be satisfied -- so the act never has to ask whether a
    # permission it is about to write down actually exists.
    policy_version: int | None
    # The same second, much smaller permission the deploying lane serves: not "may this land" but
    # "may the lane bring this branch up to date with its base". Served on the read surface so a
    # caller can report what a live pass would do without anything acting; the acting path
    # recomposes it from scratch and never trusts a caller's copy.
    branch_update_qualifies: bool


def inert_landing_admission(
    session: Session,
    repository: str,
    pr_number: int,
    landing_source: EstateLandingSource,
    policy_source: InertLandingPolicySource,
    gateway: EstateReadGateway,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> InertLandingAdmission:
    """Compose the answer for one pull request. Reads only; writes nothing, and never acts.

    THE REPOSITORY IS FOLDED HERE, so the report and the act cannot normalize it differently --
    the same fold, in the same place, as the deploying lane's, and for the same reason: two
    surfaces asking the estate a different question about one repository is how that lane's
    equivalent went wrong.

    **No clock.** There is no term here that reads one, which is what the absence of a change
    window and of a pace rule amounts to in code. Accepting one and ignoring it would suggest an
    hour matters to this answer, and it does not.
    """
    repository = repository.lower()

    # The SAME table the deploying lane records into, read for the same reason: one row per
    # (repository, pull request) ever, with no delete path, so "did we already act on this?" is
    # answered here rather than by asking GitHub afterwards, where a lost success and a refusal
    # answer alike. The two lanes share it because the two populations cannot overlap -- each
    # requires the opposite answer from the estate, and the estate gives one answer per
    # repository -- so a pull request is a subject of exactly one of them.
    prior = session.scalar(
        select(EstatePrMerge).where(
            EstatePrMerge.repository == repository,
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

    estate = _inert_estate_term(repository, landing_source)
    policy = _policy_term(repository, policy_source)
    remote = _remote_terms(repository, pr_number, policy.rules, gateway)

    refusals.extend(estate.refusals)
    refusals.extend(policy.term.refusals)
    refusals.extend(remote.term.refusals)

    satisfied = (
        enabled
        and credentials_configured
        and prior is None
        and estate.met
        and policy.term.met
        and remote.term.met
    )
    return InertLandingAdmission(
        satisfied=satisfied,
        refusals=tuple(refusals),
        repository=repository,
        pr_number=pr_number,
        head_sha=remote.head_sha,
        policy_version=policy.rules.version if policy.rules is not None else None,
        # **REUSED VERBATIM, and the reuse is the point rather than a convenience.** That rule is
        # "freshness is the sole remaining obstacle", and it subtracts three things: refusals the
        # head's POSITION produced, the two deliberate refusals, and a head whose checks reached
        # no verdict. This lane raises neither deliberate refusal and never raises
        # `landing_rollout_moved`, so the subtraction it performs here is exactly the subtraction
        # this lane wants -- and expressing it a second time would be a second definition of one
        # rule, which is what makes two consumers of a criterion drift apart.
        #
        # `rollout_base_matches_pin=False` WITHHOLDS the carve-out, which is correct rather than
        # merely safe: the carve-out excuses a refusal this lane cannot raise, so withholding it
        # changes no answer here and cannot silently excuse something later.
        branch_update_qualifies=qualifies_for_branch_update(
            tuple(refusals), rollout_base_matches_pin=False
        ),
    )


def _inert_estate_term(repository: str, landing_source: EstateLandingSource) -> Term:
    """Does landing on this repository's default branch leave everything already serving alone?

    Only an explicit `inert` passes, and the direction is the opposite of the deploying lane's.
    That is structural rather than enumerated: an answer this build does not recognise, an absent
    one, and one that says the estate has not looked all fall through to a refusal, so a fourth
    value shipped on the authoring side cannot arrive here as permission.

    `redeploys` gets its own refusal because it is not a failure -- it says the other lane is the
    right one -- and reporting it as "the estate has not looked" would send somebody to assess a
    repository that has been assessed.
    """
    answer = landing_source.landing_for(repository)
    if answer.landing == LANDING_INERT:
        return Term(True, ())
    if answer.landing is not None:
        if answer.landing == LANDING_REDEPLOYS:
            return Term(False, (INERT_LANDING_TARGET_NOT_INERT,))
        return Term(False, (LANDING_ESTATE_UNKNOWN,))
    if answer.reason == ESTATE_SOURCE_UNCONFIGURED:
        return Term(False, (LANDING_ESTATE_SOURCE_UNCONFIGURED,))
    return Term(False, (LANDING_ESTATE_SOURCE_UNREADABLE,))


@dataclass(frozen=True)
class _PolicyTerms:
    term: Term
    rules: InertLandingRules | None


def _policy_term(repository: str, policy_source: InertLandingPolicySource) -> _PolicyTerms:
    """Did the policy read, and did a person pin this repository into the population?

    Two questions rather than one, because they have different next steps: a document that could
    not be read needs somebody to look at a service, and a document that read fine and does not
    name this repository needs somebody to decide whether it should.

    The rules are carried up whether or not the repository is declared -- the remote terms below
    still need the conditions in order to say anything useful, and reporting "this repository is
    not declared" while silently declining to evaluate everything else would leave an operator
    fixing one thing at a time.
    """
    answer = policy_source.inert_landing_rules()
    rules = answer.rules
    if rules is None:
        if answer.reason == POLICY_SOURCE_UNCONFIGURED:
            return _PolicyTerms(Term(False, (INERT_LANDING_POLICY_SOURCE_UNCONFIGURED,)), None)
        if answer.reason == RULES_UNDECLARED:
            return _PolicyTerms(Term(False, (INERT_LANDING_RULES_UNDECLARED,)), None)
        return _PolicyTerms(Term(False, (INERT_LANDING_POLICY_SOURCE_UNREADABLE,)), None)
    if not rules.declares(repository):
        return _PolicyTerms(Term(False, (INERT_LANDING_REPOSITORY_NOT_DECLARED,)), rules)
    return _PolicyTerms(Term(True, ()), rules)


@dataclass(frozen=True)
class _RemoteTerms:
    term: Term
    head_sha: str | None


def _remote_terms(
    repository: str,
    pr_number: int,
    rules: InertLandingRules | None,
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
        return _RemoteTerms(Term(False, (LANDING_PULL_REQUEST_UNREADABLE,)), None)

    refusals: list[str] = []
    if pull.landed or not pull.open:
        refusals.append(LANDING_PULL_REQUEST_NOT_OPEN)
    if pull.base_ref != pull.default_branch:
        refusals.append(LANDING_BASE_NOT_DEFAULT_BRANCH)
    if pull.mergeable_state == MERGEABLE_UNKNOWN:
        refusals.append(LANDING_MERGEABILITY_UNKNOWN)
        checks = Term(False, ())
    else:
        checks = checks_term(repository, pull, gateway)
        refusals.extend(checks.refusals)

    if rules is None:
        # Already reported by the policy term. Everything below is a condition this process was
        # not told, so it cannot be met and there is nothing further to say about it.
        return _RemoteTerms(Term(False, tuple(refusals)), pull.head_sha)

    author = _author_term(pull, rules)
    fresh = freshness_term(repository, pull, gateway, required=rules.require_head_current_with_base)
    ecosystem = ecosystem_exclusion_term(pull, rules.excluded_ecosystems)
    refusals.extend(author.refusals)
    refusals.extend(fresh.refusals)
    refusals.extend(ecosystem.refusals)

    met = (
        pull.open
        and not pull.landed
        and pull.base_ref == pull.default_branch
        and pull.mergeable_state != MERGEABLE_UNKNOWN
        and checks.met
        and author.met
        and fresh.met
        and ecosystem.met
    )
    return _RemoteTerms(Term(met, tuple(refusals)), pull.head_sha)


def _author_term(pull: EstatePullRequest, rules: InertLandingRules) -> Term:
    """Was this pull request opened by an account the policy names, and is it that account?

    **TWO CHECKS, AND NEITHER IS REDUNDANT.** The login is what the document declares, so it is
    read from the document rather than written here -- the workflow this lane replaces keyed on
    exactly that string, and a value hardcoded on this side would be a second copy of a rule the
    document now holds. The TYPE is not in the document and is checked anyway: a person may rename
    a user account into any spelling at all, and the platform's own answer about what kind of
    account it is cannot be taken by renaming.

    So a login the document permits, carried by an account that is not a machine, refuses.
    """
    if rules.permits_author(pull.author_login) and pull.author_is_bot:
        return Term(True, ())
    return Term(False, (INERT_LANDING_AUTHOR_NOT_PERMITTED,))
