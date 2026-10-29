"""Turn a cascade-refused dependency bump into proposed work (ADR-0028).

**WHAT THIS IS THE HEAD OF.** ADR-0016 gave routine dependency updates to GitHub's own
auto-merge and assigned the remainder -- the majors the cascade correctly refuses -- to the
factory. ADR-0026 built the record and the carry. ADR-0028 made the package a standing one,
revised per bump. Nothing produced the record, so the lane held zero of them. This is that
producer:

    Dependabot opens a bump and its required checks fail
      -> the declared landing rule does not land it
      -> HERE: FILE THE OBSERVATION stating the bump, then revise the standing package (or,
         since bridge Part 3, create it), approve the revision by policy, publish the commit
         that carries it (ADR-0033), propose a record naming that observation as its cause
      -> change-manager's work policy approves a conforming record (ADR-0025 amendment 1);
         a person approves any other
      -> the carry registers an intake -> decomposition -> envelope -> dispatch

A bump it cannot route -- no tooling for the ecosystem, a range its tooling cannot carry, a
dependency a package in another ecosystem already covers, every docker bump -- holds one alert-hub
alert open instead (`bump_proposer.alerts`).

**WHY THIS PRODUCER IS MECHANICAL AND OTHERS ARE NOT.** A cascade-refused bump IS its own work
statement: the title states the delta, and ADR-0016 already decided that such bumps go to the
factory. There is no diagnosis step, which is exactly what ADR-0026 says no producer of the
other kind has. Do not generalise this to signals that need interpreting; a producer that
guessed would be a worse thing than no producer.

**IT READS THE RULE RATHER THAN RE-DECIDING IT, AND SINCE ADR-0038 IT READS IT AT THE SOURCE.**
Whether a bump lands unattended is declared by change-manager's landing policy -- the one holder
of that rule, asked by all three of its readers rather than transcribed into any of them. It was
read until now from `landing_ledger.rules`, a hand transcription of the GitHub Actions workflow
that used to enforce it, keyed by that workflow's blob sha; the workflow is being removed and the
orchestrator becomes the merger, which would have left this producer reading a registry with no
subject. The fail-closed property is carried over rather than lost: a policy version that declares
no inert population, or no deploying one, is a refusal, not a waiver, exactly as an untranscribed
blob was a finding rather than a guess.

**IT APPROVES A PACKAGE REVISION AND NOTHING ELSE.** It cannot approve the change record it
writes: the record is created `pending` by construction, change-manager's `propose` scope
refuses every status-moving route, and this program's own change-manager client asserts a
two-path surface. Its SECOND client is narrower still: the orchestrator one names exactly one
route, the observation ingest, and the credential behind it can neither transition a unit nor
create work.
Whoever approves the record -- change-manager's work policy or a person -- this producer is on
the other side of that decision.

**ITS POPULATION IS THE MEMBER RECORD'S GRANT.** A repository is considered when the member record
grants it `dependency_updates` and the landing policy declares it (inert or deploying) or a
standing package targets it. The record is read once, before anything else, as the observer; an
unreadable one is exit 2, never a smaller population. A package created for a deploying repository
has reach `[live_estate]`, one for an inert repository `[source_repository]` (ADR-0028 amendment
3), and each repository's bumps are judged against its own block of the policy.

**A REPEAT PASS IS A REPLAY, in both halves.** A standing package whose tip revision already
carries this bump is not revised again; change-manager answers 200 for an identical proposal.
So the pass is safe to schedule, and a second run over an unchanged estate writes nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NamedTuple

from bump_proposer import alerts
from bump_proposer.change_manager import (
    DEFAULT_BASE_URL,
    ChangeManagerClient,
    ChangeManagerError,
    ProposalRefused,
)
from bump_proposer.landing_policy import (
    LandingPolicy,
    LandingPolicyError,
    read_landing_policy,
)
from bump_proposer.observation import ObservationUncomposable, bump_observation
from bump_proposer.orchestrator_client import (
    ObservationCredentialError,
    ObservationWriteError,
    OrchestratorClient,
    UnusableEndpointError,
    open_client,
)
from bump_proposer.standing import (
    PACKAGES_REPOSITORY,
    TOOLING,
    StandingError,
    StandingPackage,
    advance,
    checkout_root,
    commit,
    create,
    discover,
    other_ecosystem,
    remove_if_untracked,
    require_clean,
    require_publishable,
    snapshot_hash,
)
from bump_proposer.titles import range_bump_of
from landing_ledger.audit import REFUSING_CONCLUSIONS, is_green
from landing_ledger.github import (
    UPSTREAM_AUTHOR,
    GitHubReader,
    LedgerError,
    default_branch,
    read_pending_updates,
)
from landing_ledger.model import PendingUpdate
from landing_ledger.titles import Bump, bump_of
from sds_members import holds as hold_matcher
from sds_members.client import Hold, Member, MembersUnreadable, read_members_from_environment
from sds_members.populations import granted as granted_repositories

EXIT_OK: Final = 0
EXIT_UNUSABLE: Final = 2
EXIT_FINDINGS: Final = 3

ACTOR: Final = "bump-proposer"
RISK: Final = "caution"

# EXACTLY the keys `_proposal` emits, declared rather than left implicit in a dict literal.
#
# IT EXISTS SO THE CROSS-REPO FIELD CHECK HAS ONE LOCAL DECLARATION TO COMPARE AGAINST. The
# alternative is a list of field names retyped into the check script, which would be a second copy
# of this vocabulary -- the defect this repository has now re-learned for BWS UUIDs, capability
# names and the dependency-update budget, where only the copies that run get corrected. The brief
# check pins its parser against `RunnerBriefResponse.model_fields` for the same reason; a bare
# dict literal offers nothing to pin.
#
# `tests/bump_proposer/test_pass.py` holds it to what `_proposal` actually returns, so the
# declaration cannot drift from the payload it describes.
#
# Two parts. `WORK_RECORD_FIELDS` is what every standing-package producer sends (the rotation
# proposer sends exactly these). The bump proposer adds the two change-manager's work policy reads
# to approve a standing dependency-update record (bridge Part 3, Rulings 1-2) -- neither is an
# asserted field there, so adding them to a pending record's replay changes no stored payload.
WORK_RECORD_FIELDS: Final = (
    "package_id",
    "package_revision",
    "package_source_repository",
    "risk",
    "reasoning",
    "actor",
    "originating_observation_id",
)
PROPOSAL_FIELDS: Final = (*WORK_RECORD_FIELDS, "target_repository", "change_class")
CHANGE_CLASS: Final = "dependency-update"

# The reach a created standing package declares (ADR-0028 amendment 3, Ruling 1): `live_estate`
# where landing redeploys, so the factory's `live_estate` patterns, change window and lease apply;
# `source_repository` where it is inert. Dispatch checks the choice against App Brain.
DEPLOYING_REACH: Final = "live_estate"
INERT_REACH: Final = "source_repository"

# How long a concluded failure must stand before this producer reads it as the cascade's answer.
#
# IT GUARDS AN IRREVERSIBLE ACT, which is why it exists at all and why it is not borrowed from
# `landing_ledger.audit.SETTLE_SECONDS` despite the two sharing a magnitude. That one bounds how
# long an armed-and-green pull request may sit before the fact is worth REPORTING, where a wrong
# answer is a noisy line. This one decides whether to mint a package revision, and a package
# revision cannot be unminted: take a failure that a re-run then clears and the record is
# stranded as superseded, having spent a human approval. Two questions with two costs, and one
# name would let either be tuned for the other.
#
# `PendingUpdate.checks` holds only jobs that have CONCLUDED, so a pull request whose checks are
# still running is indistinguishable from one whose checks are finished except by how recently
# the last of them concluded. That makes this interval the only instrument available for "has
# the gate finished deciding?", rather than a nicety on top of one.
FAILURE_SETTLE_SECONDS: Final = 3600

# What makes a pass a FINDING rather than a clean run.
#
# `unclassifiable` is deliberately ABSENT. A title with no single delta (a multi-clause range, a
# grouped bump) is a refusal nothing can ever clear, which the estate rules an exception rather
# than a finding: the control that reports it forever stops being read. So are `would-create`,
# `created`, `would-alert` and `alerted` (bridge Part 3): a created package and an alert held open
# are this lane's ordinary output, and the alert is what tells a person.
FINDING_STATUSES: Final = frozenset(
    {
        "error",
        "unreadable",
        "ambiguous",
        # ADR-0038, and the SUCCESSOR to `no-cascade` rather than a new kind of thing. It used to
        # mean "this repository carries no auto-merge workflow, so which bumps it refuses is not a
        # question with an answer"; it now means the landing policy declares the repository in
        # neither population, inert nor deploying, which is the same fact asked of the holder
        # instead of of a file. `gate-not-transcribed` retires with no successor: there is no
        # longer a transcription to be missing an entry, and the shape it guarded -- a rule this
        # program cannot read -- is now a whole-pass refusal rather than a per-repository one,
        # because one declaration covers every repository at once.
        "not-declared-landing",
        "superseded",
        "refused",
        # G1+G2. The cause could not be filed, so nothing was proposed -- and a bump this lane
        # would otherwise have taken is now sitting unproposed with only this line to say so.
        # It is a finding for the same reason `refused` is: the pass reached its subject, could
        # not complete the act, and no later pass repairs it on its own.
        "unobserved",
    }
)


@dataclass(frozen=True)
class Outcome:
    repository: str
    number: int
    status: str
    detail: str


def _refused_by_the_checks(pending: PendingUpdate, now: datetime) -> bool:
    """Whether the required checks have finished deciding, and decided against this update.

    THREE STATES ARRIVE HERE AND ONLY ONE OF THEM IS THIS LANE'S SUBJECT. Nothing concluded yet,
    or something concluded a moment ago with more still to come: the gate has not answered, so
    neither lane acts. Everything concluded and none of it failed: the cascade lands it. Settled,
    with at least one failure: the cascade will not land it, and that remainder is what ADR-0016
    assigns to the factory.

    FAIL-CLOSED MEANS `False` HERE, and the asymmetry is the point. Answering "refused" wrongly
    mints a package revision that cannot be unminted and spends a human approval on a bump that
    was about to land by itself; answering "not refused" wrongly costs a pass, and the next pass
    picks it up. So an unreadable or unsettled state waits.

    THAT IS WHY IT ASKS `REFUSING_CONCLUSIONS` AND NOT `FAILING_CONCLUSIONS`. The wider set exists
    for `is_green`, where calling a cancelled run not-green is conservative. Here it would be
    fail-OPEN: a required job cancelled by hand, or gone `stale` because the Actions quota ran
    out, is no verdict about the change at all -- and reading it as one mints a revision while the
    gate's arming stays live, so the bump lands the moment somebody re-runs the job green.

    IT DOES NOT KNOW WHICH JOBS WERE REQUIRED, and `Check`'s own docstring says as much. Every
    `pull_request` job in all six lane repositories is a required context today, measured, so a
    non-required failure cannot reach this yet; adding one advisory job anywhere makes it
    reachable, with no signal. Answering it needs the branch's protection settings, which the
    ledger deliberately does not reconstruct.
    """
    if pending.last_concluded_at is None:
        return False
    if (now - pending.last_concluded_at).total_seconds() < FAILURE_SETTLE_SECONDS:
        return False
    return any(check.conclusion in REFUSING_CONCLUSIONS for check in pending.checks)


@dataclass(frozen=True)
class Route:
    """What this pass does with one open pull request.

    `act` revises `package` (or, when it is None, creates one for `tooling`) to carry `bump`;
    `alert` holds `key` open in alert-hub; `skip` and `ambiguous` only report `detail`, except that
    a `skip` naming a `key` holds that key as it is.
    """

    action: str
    detail: str
    bump: Bump | None = None
    package: StandingPackage | None = None
    tooling: str = ""
    key: str = ""


def _alert(pending: PendingUpdate, ecosystem: str, dependency: str, why: str) -> Route:
    return Route(
        "alert",
        f"{why}; a person decides it",
        key=alerts.key_for(pending.repository, ecosystem, dependency),
    )


# The requirement-range operators each tooling profile's mutation can carry (Ruling 7, Review
# focus 5). pip's mutation rewrites `==` lines only, so no range; uv's discovery reads `==` and
# `>=` pins; npm installs with `--save-exact`, which turns any range into an exact pin, so none.
# A range with any other operator alerts rather than becoming a package whose mutation can't apply.
RANGE_OPERATORS: Final = {"pip": frozenset(), "uv": frozenset({">=", "=="}), "npm": frozenset()}


def _consider(
    pending: PendingUpdate,
    rule: LandingPolicy,
    packages: dict[tuple[str, str], StandingPackage],
    now: datetime,
    *,
    holds: tuple[Hold, ...],
) -> Route:
    """Decide what this open pull request is, or say why it is nothing to do here.

    It routes the bump first (act, create or alert), then asks whether the cascade still has it.
    Asking in that order is what lets one rule hold an alert while a re-run's failure settles,
    whichever route raised it. `holds` are the member record's holds on this repository.
    """
    update = pending.update
    if update is not None and update.ecosystem is not None and update.ecosystem not in TOOLING:
        # NO TOOLING (bridge Part 3). The factory has no profile for this ecosystem, so a bump the
        # cascade will not land has no route but a person -- asked before the title is read,
        # because an alert needs no versions and a docker tag rarely parses as one. Which bumps
        # with no tooling reach the alert even when they pass is the repository's own block's
        # exclusion list (ADR-0028 amendment 3): docker in an inert repository, github_actions
        # in a deploying one. A permitted ecosystem's passing bump is the landing party's. A hold
        # is asked first: it is a person's decision about this dependency already, so the alert
        # would only ask them again.
        on_hold = _held_without_tooling(holds, update.ecosystem, update.dependency)
        if on_hold is not None:
            return on_hold
        route = _alert(
            pending,
            update.ecosystem,
            update.dependency,
            f"the factory has no tooling for {update.ecosystem} bumps of {update.dependency}",
        )
        return _gated(route, pending, rule, update.ecosystem, None, now)

    bump = bump_of(pending.title)
    declared_by_title = bump.declared if bump is not None else None
    declared_by_bot = pending.update.update_type if pending.update is not None else None

    if declared_by_title != declared_by_bot:
        # THE TWO SOURCES DISAGREE, and they answer different halves of one question: the
        # cascade evaluates the bot's own trailer, while the versions a revision must carry are
        # only reliable in the title (the bot rewrites a pull request in place, and the branch
        # and the trailer go stale). When they disagree, the pull request either states a delta
        # the gate did not see or the reverse, and one of those two directions means the gate
        # is about to merge something this producer is proposing as work.
        return Route(
            "ambiguous",
            (
                f"ambiguous: the title declares {declared_by_title or 'no delta'} and the "
                f"update trailer declares {declared_by_bot or 'no delta'}"
            ),
        )
    operator = None
    if bump is None:
        # A single-clause requirement range (Ruling 7), parsed only once title and trailer have
        # agreed there is no plain delta: the bot declares no update type for a range.
        ranged = range_bump_of(pending.title)
        if ranged is not None:
            operator, bump = ranged
    if bump is None:
        return Route("skip", "unclassifiable: the title states no single version delta")

    assert pending.update is not None  # a parsed title with no trailer is ambiguous above
    ecosystem = pending.update.ecosystem
    if ecosystem is None:
        return Route("skip", "unroutable: the branch names no ecosystem")
    held = _held(holds, pending.repository, ecosystem, pending.update.dependency, bump.to_version)
    if held is not None:
        return held
    route = _route(pending, bump, TOOLING[ecosystem], operator=operator, packages=packages)
    return _gated(route, pending, rule, ecosystem, bump, now)


def _held(
    holds: tuple[Hold, ...], repository: str, ecosystem: str, dependency: str, to_version: str
) -> Route | None:
    """A skip when the member record holds this bump back, else None (bridge Part 4).

    Asked before the route and before the cascade, so a held dependency is neither created,
    proposed nor alerted, and it names no alert key: `alerts.sync` resolves a `dependency-bump:`
    alert this lane raised for it before the hold. It asks the factory's question
    (`factory_breaches`), since what it proposes is a bump the factory installs: a uv version is a
    `>=` floor, so any hold on a uv dependency skips it, as the factory merge would refuse it. The
    directory is unknown (`None`), so a hold on any directory matches.
    """
    bump = hold_matcher.Bump(ecosystem, None, dependency, to_version)
    breached = hold_matcher.factory_breaches(holds, repository, bump)
    if not breached:
        return None
    ceilings = ", ".join(sorted({hold.ceiling for hold in breached}))
    return Route(
        "skip",
        f"ceiling-exceeded: the member record holds {ecosystem} {dependency} at {ceilings}, and "
        f"this bump moves it to {to_version}; it waits for the hold to be lifted",
    )


def _held_without_tooling(holds: tuple[Hold, ...], ecosystem: str, dependency: str) -> Route | None:
    """A skip when the member record holds a dependency the factory has no tooling for, else None.

    No version is read here (a docker tag rarely parses as one), so any hold on the dependency
    skips it (`holding`, matched as `exceeded` matches). Like `_held`, it names no alert key, so
    `alerts.sync` resolves a `dependency-bump:` alert raised before the hold.
    """
    bump = hold_matcher.Bump(ecosystem, None, dependency, "")
    on_hold = hold_matcher.holding(holds, bump)
    if not on_hold:
        return None
    ceilings = ", ".join(sorted({hold.ceiling for hold in on_hold}))
    return Route(
        "skip",
        f"ceiling-exceeded: the member record holds {ecosystem} {dependency} at {ceilings}, and "
        "the factory has no tooling for it; it waits for the hold to be lifted",
    )


def _gated(
    route: Route,
    pending: PendingUpdate,
    rule: LandingPolicy,
    ecosystem: str,
    bump: Bump | None,
    now: datetime,
) -> Route:
    """`route`, unless the cascade still has this bump.

    PERMITTED IS NOT THE SAME QUESTION AS LANDED, and it stopped being a usable proxy for it on
    2026-08-28. ADR-0034 moved the split onto the OUTCOME -- anything not excluded is permitted and
    the required checks decide the rest -- so a bump whose checks FAIL is permitted and unlandable
    at once, and this lane's subject is the second half. Reading `permits` alone would EMPTY the
    factory's queue. The declaration ADR-0038 moved to change-manager is that same rule, asked of
    the repository's own population (ADR-0028 amendment 3).

    A failure that hasn't settled (a re-run failing again) can't be decided yet, so the key of an
    alert the route would raise is HELD: neither resolved now nor fired again once it settles.
    """
    if not rule.permits(pending.repository, ecosystem) or _refused_by_the_checks(pending, now):
        return route
    unsettled = any(check.conclusion in REFUSING_CONCLUSIONS for check in pending.checks)
    held = route.key if route.action == "alert" and unsettled else ""
    return Route("skip", _landing_detail(pending, ecosystem, bump), key=held)


def _route(
    pending: PendingUpdate,
    bump: Bump,
    tooling: str,
    *,
    operator: str | None,
    packages: dict[tuple[str, str], StandingPackage],
) -> Route:
    """Where a failed bump with tooling goes: its package, a new package, or an alert."""
    assert pending.update is not None
    dependency = pending.update.dependency
    if operator is not None and operator not in RANGE_OPERATORS[tooling]:
        # Review focus 5: a package for this range would carry a mutation that cannot apply.
        return _alert(
            pending,
            tooling,
            dependency,
            f"a {tooling} requirement range ({operator}) of {dependency}, which the {tooling} "
            "mutation cannot carry",
        )
    package = packages.get((pending.repository.lower(), dependency))
    if package is not None:
        other = other_ecosystem(package, tooling)
        if other is not None:
            # Review focus 2: discovery keys on (repository, dependency) and refuses a duplicate,
            # so a second package for this pair would stop every later pass.
            return _alert(
                pending,
                tooling,
                dependency,
                f"{dependency} is already covered by {package.package_id} ({other}), and one "
                f"standing package per dependency is all discovery can hold",
            )
    return Route(
        "act",
        f"{dependency} {bump.from_version} to {bump.to_version}",
        bump=bump,
        package=package,
        tooling=tooling,
    )


def _landing_detail(pending: PendingUpdate, ecosystem: str | None, bump: Bump | None = None) -> str:
    answer = "have passed" if is_green(pending) else "have not concluded against it"
    what = f"a {bump.kind}" if bump is not None else "a bump"
    return f"the landing policy permits {what} of a {ecosystem} dependency and its checks {answer}"


def _reasoning(package: StandingPackage, bump: Bump, pending: PendingUpdate) -> str:
    """Why this work exists -- and a FROZEN string, like every asserted field on a record.

    change-manager compares the asserted fields against the stored ones and refuses a
    difference with a terminal 409, so this must say only what stays true for the life of the
    record. That is safe here in a way it was not for the deploy producer, whose subject was a
    pull request whose title drifts: this record's identity is the package REVISION, one
    revision carries exactly one bump, and a bump that moves produces a new revision and a new
    record rather than rewriting this one. So the two versions may be named. Nothing dated,
    nothing counted, and never the pull request's title.
    """
    return (
        f"{package.target_repository} carries an open dependency update of "
        f"{package.dependency} from {bump.from_version} to {bump.to_version} "
        f"(pull request {pending.number}), and the auto-merge cascade installed on that "
        f"repository does not permit a {bump.kind} of this kind. ADR-0016 assigns that "
        f"remainder to the factory. Revision {package.revision} of the standing package "
        f"{package.package_id} carries this bump, approved by conformance to "
        f"approval-policy.toml rather than by a named human (ADR-0028). Approving this record "
        f"is the decision to have the factory make the change; decomposition and the authority "
        f"envelope are each approved separately after it."
    )


def _proposal(
    package: StandingPackage,
    bump: Bump,
    pending: PendingUpdate,
    observation_id: str,
) -> dict[str, Any]:
    """The record's facts, including the observation that caused it (G1+G2 clause C1).

    `originating_observation_id` is NOT one of change-manager's asserted fields, deliberately, and
    that decision belongs to the other side of this boundary rather than to this payload. It is
    written once at construction and never compared, so a replay onto a record proposed before the
    column existed answers 200 rather than 409 -- which is what stops this scheduled producer
    wedging on every pre-contract record, permanently, with no repair route. The cost is the other
    half of the same trade: a producer that named the wrong cause cannot correct it.
    """
    return {
        "package_id": package.package_id,
        "package_revision": package.revision,
        "package_source_repository": PACKAGES_REPOSITORY,
        "risk": RISK,
        "reasoning": _reasoning(package, bump, pending),
        "actor": ACTOR,
        "originating_observation_id": observation_id,
        "target_repository": package.target_repository,
        "change_class": CHANGE_CLASS,
    }


def _superseded(records: list[dict[str, Any]], package: StandingPackage) -> list[Outcome]:
    """Records for an EARLIER revision of this package that nothing can carry any more.

    Only the tip revision is on disk, and the carry refuses a record whose revision does not
    match the checkout -- so advancing a standing package strands any record still standing for
    the revision before it. That happens for one reason: the update bot rewrote the pull request
    in place and the bump this lane is carrying changed under a stable pull request number.
    Reported HERE rather than left for the carry to discover the next morning, because this pass
    is what caused it and is the only thing that knows why.
    """
    stranded = []
    for row in records:
        if row.get("package_id") != package.package_id:
            continue
        revision = row.get("package_revision")
        if not isinstance(revision, int) or revision >= package.revision:
            continue
        if row.get("status") in {"resolved", "wontfix"}:
            continue
        stranded.append(
            Outcome(
                package.target_repository,
                0,
                "superseded",
                f"record {row.get('id')} names {package.package_id} revision {revision}, which "
                f"is no longer the revision on disk; the bump it describes was replaced",
            )
        )
    return stranded


def _act(
    route: Route,
    pending: PendingUpdate,
    packages: dict[tuple[str, str], StandingPackage],
    client: ChangeManagerClient | None,
    observer: OrchestratorClient | None,
    records: list[dict[str, Any]],
    root: Path,
    reach: str,
) -> list[Outcome]:
    subject = pending.repository
    bump, package = route.bump, route.package
    assert bump is not None and pending.update is not None  # an `act` route carries its bump
    dependency = pending.update.dependency
    if client is None:
        if package is None:
            return [
                Outcome(
                    subject,
                    pending.number,
                    "would-create",
                    f"a {route.tooling} standing package (reach {reach}) for {dependency}: "
                    f"{bump.from_version} to {bump.to_version}",
                )
            ]
        state = "would-replay" if package.carries(bump) and package.approved else "would-advance"
        return [
            Outcome(
                subject,
                pending.number,
                state,
                f"{package.package_id}: {bump.from_version} to {bump.to_version}",
            )
        ]

    # OBSERVE, THEN PROPOSE (G1+G2 clause C2), and the order is load-bearing rather than tidy.
    # The observation is the durable fact and the record is a decision about it, so a record
    # naming an observation that does not exist is a dangling cause with no repair: observations
    # are append-only, with no supersession model and no delete route.
    #
    # BEFORE THE ADVANCE, NOT MERELY BEFORE THE PROPOSAL, which is the stronger placement and the
    # one that costs nothing. A package revision cannot be unminted and a published commit cannot
    # be unpublished, so filing first means a pass that cannot state the fact has spent neither --
    # where observing after the mint would leave a revision standing for work nothing proposed.
    if observer is None:  # pragma: no cover - run() refuses --submit without the credential
        raise ObservationWriteError(
            "no orchestrator credential, so the cause cannot be filed and nothing may be proposed"
        )
    observation_id = observer.record_observation(bump_observation(pending, bump))

    outcomes: list[Outcome] = []
    key = (subject.lower(), dependency)
    created_here = package is None
    try:
        if package is None:
            # Bridge Part 3. No standing package covers this dependency, so this pass creates
            # one carrying the bump -- after the observation, like every other mint, and behind
            # the same publish guard. Remembered at once, so a second pull request for the same
            # dependency in this pass finds it rather than creating it again.
            require_publishable(root)
            package = create(root, subject, route.tooling, dependency, bump, reach=reach)
            packages[key] = package
            outcomes.append(Outcome(subject, pending.number, "created", package.package_id))
        published = None
        if not (package.carries(bump) and package.approved):
            # ASKED AGAIN, PER UNIT, and not only once at the top of the pass. A refused publish
            # is caught per pull request and the pass goes on, so the residue this pass just
            # created would otherwise be invisible to the guard that exists to stop a revision
            # being stacked on it -- and every later unit would mint another revision number,
            # which cannot be unminted, and commit another local commit refused the same way.
            require_publishable(root)
            package = advance(package, bump, root)
            snapshot_hash(package, root)
            published = commit(package, bump, root)
    except Exception:
        # A package this pass created and could not commit (an approval refused, say) would be
        # an untracked directory that `require_clean` refuses on every later pass. It is this
        # program's own and nothing else holds it, so it goes; a committed one is left alone.
        if created_here and key in packages:
            remove_if_untracked(packages.pop(key).path, root)
        raise

    record, created = client.propose(_proposal(package, bump, pending, observation_id))
    # THE SHA IS REPORTED BECAUSE THE PASS IS THE ONLY THING THAT KNOWS IT. `source_commit` on
    # the intake this record eventually causes is that commit, and until ADR-0033 the pass
    # discarded it -- so a reader wanting to know what had been written to the authoring
    # repository had to go and look. Only on a pass that wrote one: a replay names no sha
    # because it published nothing, and printing the checkout's HEAD would be a different
    # claim wearing the same words.
    detail = (
        f"item {record.get('id')} {package.package_id} rev {package.revision} "
        f"status={record.get('status')}"
    )
    if published is not None:
        detail = f"{detail} published {published[:12]}"
    outcomes.append(Outcome(subject, pending.number, "proposed" if created else "replayed", detail))
    outcomes.extend(_superseded(records, package))
    return outcomes


def _repository_pass(
    reader: GitHubReader,
    repository: str,
    rule: LandingPolicy,
    packages: dict[tuple[str, str], StandingPackage],
    client: ChangeManagerClient | None,
    observer: OrchestratorClient | None,
    records: list[dict[str, Any]],
    root: Path,
    now: datetime,
    raised: alerts.Raised,
    holds: tuple[Hold, ...],
) -> list[Outcome]:
    if not rule.declares(repository):
        # THE SUCCESSOR TO `no-cascade`, and the same fact asked of the holder rather than of a
        # file. A repository the landing policy declares in neither population is one where
        # nobody has said what landing on its default branch does -- so "what lands here
        # unattended" has no answer, and every bump against it is outside a lane whose subject is
        # defined by what that declaration declines. Still a finding, because a standing package
        # targeting such a repository was authored for a lane that does not cover it.
        return [
            Outcome(
                repository,
                0,
                "not-declared-landing",
                f"landing policy version {rule.version} declares this repository neither inert "
                "nor deploying, so which bumps land there unattended is not a question with an "
                "answer; a standing package targeting it needs a different producer",
            )
        ]
    reach = DEPLOYING_REACH if rule.deploys(repository) else INERT_REACH
    try:
        base = default_branch(reader, repository)
        pending_updates = read_pending_updates(reader, repository, base)
    except LedgerError as error:
        return [Outcome(repository, 0, "unreadable", str(error))]

    outcomes: list[Outcome] = []
    for pending in sorted(pending_updates, key=lambda p: p.number):
        route = _consider(pending, rule, packages, now, holds=holds)
        if route.action != "act":
            outcomes.append(_unacted(route, pending, raised, submitting=client is not None))
            continue
        try:
            outcomes.extend(_act(route, pending, packages, client, observer, records, root, reach))
        except (ObservationUncomposable, ObservationWriteError) as error:
            # ONE STATUS FOR BOTH, because the consequence is identical and it is the consequence
            # a reader acts on: this bump has no cause on record, so nothing was proposed for it.
            # They differ in whose fault it is -- a bump this producer cannot state, against an
            # orchestrator that would not take it -- and the detail line carries that.
            outcomes.append(Outcome(repository, pending.number, "unobserved", str(error)))
        except StandingError as error:
            outcomes.append(Outcome(repository, pending.number, "error", str(error)))
        except ProposalRefused as error:
            outcomes.append(Outcome(repository, pending.number, "refused", str(error)))
        except ChangeManagerError as error:
            outcomes.append(Outcome(repository, pending.number, "error", str(error)))
    return outcomes


def _unacted(
    route: Route, pending: PendingUpdate, raised: alerts.Raised, *, submitting: bool
) -> Outcome:
    """Report a route this pass does not act on, recording any alert key it raises or holds.

    Alerts are collected, and sent once the whole pass is read (`alerts.sync`): which keys stop
    firing is only known then. A dry run sends nothing.
    """
    if route.action == "alert":
        raised.firing[route.key] = alerts.alert_for(
            pending.repository, pending.number, pending.title, route.detail
        )
        status = "alerted" if submitting else "would-alert"
        return Outcome(pending.repository, pending.number, status, f"{route.key}: {route.detail}")
    if route.key:
        raised.held.add(route.key)
    status = "ambiguous" if route.action == "ambiguous" else "skipped"
    return Outcome(pending.repository, pending.number, status, route.detail)


def _lane(
    root: Path,
    *,
    submit: bool,
    requested: list[str] | None,
    members: list[Member],
    rule: LandingPolicy,
) -> tuple[dict[tuple[str, str], StandingPackage], list[str]] | None:
    """The standing packages and the repositories this pass reads, or None when there is no lane.

    **SCOPE IS THE MEMBER RECORD'S GRANT.** A repository is in this lane when the record grants it
    `dependency_updates` and either the landing policy declares it, inert or deploying (a failed
    bump there gets a standing package created: bridge Part 3, widened by ADR-0028 amendment 3),
    or a standing package already targets it (which `_repository_pass` reports
    `not-declared-landing` when the declaration names it in neither population). Repositories are
    compared case-folded, the record holding GitHub's spelling and a package whatever its author
    typed.

    The two ways to fall short are told apart and neither is a finding. A granted repository that
    is neither is `[NO PACKAGE]`: packages are created only for repositories the landing policy
    declares, and the grant is not a defect. A package for an ungranted repository is
    `[NOT GRANTED]`: it is not acted on, and `membership_check` already reports it. Naming either
    with `--repository` is a refusal, not a smaller run.
    """
    try:
        packages = discover(root)
        if submit:
            require_clean(root)
            # ADR-0033. Both refusals answer "is this checkout one this program may write to?",
            # and they are asked together for that reason: an uncommitted change is somebody
            # else's work this pass would sweep up, and an unpublished commit is this program's
            # own work it failed to finish. Neither is a state a further revision may be built
            # on, and both need a person.
            require_publishable(root)
    except StandingError as error:
        print(str(error), file=sys.stderr)
        return None

    authored = {package.target_repository.lower() for package in packages.values()}
    spelling = {name.lower(): name for name in granted_repositories(members, "dependency_updates")}
    in_lane = {name for name in spelling if name in authored or rule.declares(name)}
    for name in sorted(set(spelling) - in_lane):
        print(
            f"[NO PACKAGE] {spelling[name]} is granted dependency_updates; no standing package "
            "targets it and the landing policy declares it neither inert nor deploying"
        )
    targeted = {package.target_repository for package in packages.values()}
    for name in sorted(targeted, key=str.lower):
        if name.lower() not in spelling:
            print(f"[NOT GRANTED] {name} has a standing package; the record does not grant it")
    # A package's spelling wins where there is one, as before Part 3: it is what the package's
    # records and commits name.
    named = spelling | {
        package.target_repository.lower(): package.target_repository
        for package in packages.values()
    }
    scope = sorted(named[name] for name in in_lane)
    if requested:
        wanted = {name.lower() for name in requested}
        ungranted = wanted - set(spelling)
        if ungranted:
            print(
                f"the member record does not grant dependency_updates to {sorted(ungranted)}",
                file=sys.stderr,
            )
            return None
        unknown = wanted - in_lane
        if unknown:
            print(
                f"no standing package targets {sorted(unknown)} and the landing policy declares it "
                "neither inert nor deploying",
                file=sys.stderr,
            )
            return None
        scope = [name for name in scope if name.lower() in wanted]
    if not scope:
        print("no granted repository is in this lane; nothing to do", file=sys.stderr)
        return None
    return packages, scope


def _declared_rule(read_token: str, *, base_url: str) -> LandingPolicy | None:
    """What change-manager declares lands unattended, or None -- which stops the pass.

    ADR-0038. Every failure here is "this pass could not use its inputs" rather than "this pass
    found something", and that is the same reading an untranscribed gate blob used to get,
    arrived at from the other side: a rule this program cannot read is a rule it will not guess
    at. It stops the whole pass rather than one repository because ONE declaration covers every
    repository at once -- reporting it per repository would be N copies of one fact.
    """
    try:
        rule = read_landing_policy(read_token, base_url=base_url)
    except LandingPolicyError as error:
        print(str(error), file=sys.stderr)
        return None
    # THE DEPLOYING HALF IS REQUIRED HERE AND NOWHERE ELSE (ADR-0028 amendment 3). This pass is
    # the one reader that widens its lane on it, so a document that stopped declaring it must stop
    # the pass rather than shrink the lane to inert in silence. `parse` reads it permissively so
    # the inert lander, which never asks, is not stopped by its absence.
    if not rule.deploying:
        print(
            f"landing policy version {rule.version} declares no deploying population, or an "
            "empty one; an absent declaration is a refusal rather than a waiver",
            file=sys.stderr,
        )
        return None
    if rule.deploying_excluded_ecosystems is None:
        print(
            f"landing policy version {rule.version} decides deploying landings by update type, "
            "which this producer does not evaluate",
            file=sys.stderr,
        )
        return None
    if not rule.covers_author(UPSTREAM_AUTHOR):
        # THE ONE THING THIS PASS CHECKS ABOUT THE DECLARATION ITSELF, and it fails closed in the
        # expensive direction. It asks the inert block only: the deploying `landing` block names
        # no authors, since the orchestrator's admission decides who may land there.
        # `read_pending_updates` returns pull requests by `UPSTREAM_AUTHOR` and no others, so a
        # declaration that stopped permitting that author would make every open update in the
        # estate this lane's subject at once -- a flood of package revisions that cannot be
        # unminted, on a decision nobody took. Refusing costs a pass. The opposite direction, a
        # declaration naming an author this program's reader cannot see, needs no check here:
        # the lane simply goes quiet, and writes nothing.
        print(
            f"landing policy version {rule.version} does not permit {UPSTREAM_AUTHOR} to land, "
            "so every open update would be this lane's subject at once; refusing",
            file=sys.stderr,
        )
        return None
    return rule


def _report(outcomes: list[Outcome], rule: LandingPolicy) -> int:
    """Print the pass and answer its exit code.

    Extracted from `run` rather than inlined, because `run` now carries the observer credential's
    own refusal as well as change-manager's and reads past what a reader can hold at once. The
    exit code is composed HERE, beside the lines it summarises, so the count a reader sees and the
    code a scheduled run reports cannot come apart.
    """
    for outcome in outcomes:
        subject = f"{outcome.repository}#{outcome.number or '-'}"
        print(f"{subject}  {outcome.status:<19} {outcome.detail}")
    findings = [o for o in outcomes if o.status in FINDING_STATUSES]
    proposed = [o for o in outcomes if o.status in {"proposed", "would-advance", "would-create"}]
    print(
        f"\n{len(outcomes)} considered, {len(proposed)} to propose, {len(findings)} findings "
        f"(landing policy version {rule.version})"
    )
    return EXIT_FINDINGS if findings else EXIT_OK


class _Credentials(NamedTuple):
    """Everything this pass reads from the environment, in one shape.

    THE TWO SERVICES ARE NOT SYMMETRIC and the field names are where that shows. change-manager is
    reached with two bearers of different scope -- the READ one on every pass, the PROPOSE one only
    for a writing one -- while the orchestrator is reached with a single OBSERVER bearer, also only
    for a writing one.
    """

    github: str
    change_manager: str
    change_manager_read: str
    change_manager_url: str
    orchestrator_url: str
    orchestrator_key_id: str
    orchestrator_token: str
    alert_token: str


def _credentials(*, submit: bool) -> _Credentials | None:
    """Read and validate every credential this pass needs, or None -- which stops the pass.

    ONE FUNCTION BECAUSE IT IS ONE CONCERN, and because `run` reads past what a reader can hold at
    once when three services' worth of guards sit inline in it.

    THE OBSERVER BEARER IS READ FROM THE ESTATE-WIDE VARIABLES, not from `BUMP_PROPOSER_` ones.
    `orchestrator-observer` is the single credential every observe-and-report lane holds by design,
    and the three lanes that already hold it read it from exactly those three names; a fourth
    spelling would be a second name for one credential.

    TWO OF THE THREE ARE WITHHELD FROM A DRY RUN, and for one reason: a dry run proposes nothing,
    so it neither writes to change-manager nor has a cause to file -- and this lane's standing
    property is that it therefore touches no credential that could write. Refusing HERE rather than
    per pull request matters for the observer bearer in particular, because the alternative spends
    a package revision, which cannot be unminted, discovering it.
    """
    github = os.environ.get("BUMP_PROPOSER_GITHUB_TOKEN", "")
    change_manager = os.environ.get("BUMP_PROPOSER_CHANGE_MANAGER_TOKEN", "")
    # ADR-0038. TWO change-manager credentials, and which is fetched when is the property the
    # launcher's header states. This one is READ-scoped and is needed on EVERY pass, because the
    # rule a dry run reports against is now read rather than transcribed; the one above can
    # WRITE and is still needed only for `--submit`. Sharing one would have surrendered the
    # property that a dry run cannot touch the credential that could write.
    read = os.environ.get("BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN", "")
    url = os.environ.get("BUMP_PROPOSER_CHANGE_MANAGER_URL", "")
    observer_url = os.environ.get("ORCHESTRATOR_API_URL", "")
    observer_key_id = os.environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "")
    # PINNED, because the route admits SYSTEM as well as OBSERVER and `recorded_by` is permanent.
    # The three variables above are the estate-wide names every orchestrator client reads, so a
    # hand-run `--submit` from a shell already carrying the SYSTEM bearer -- which is how the
    # factory is driven by hand -- would file observations that SUCCEED, attributed to
    # `orchestrator-system`, into an append-only table with no delete route. The launcher always
    # sets this; the guard is for the shell that did not.
    OBSERVER_KEY_ID: Final = "orchestrator-observer"
    observer_token = os.environ.get("ORCHESTRATOR_API_TOKEN", "")
    # Bridge Part 3: the `sds-bridge` sender's alert-hub token. Only a writing pass sends.
    alert_token = os.environ.get("SDS_BRIDGE_ALERT_TOKEN", "")

    missing = ""
    if not github:
        missing = "BUMP_PROPOSER_GITHUB_TOKEN is unset"
    elif submit and not change_manager:
        missing = "BUMP_PROPOSER_CHANGE_MANAGER_TOKEN is unset; --submit needs it"
    elif submit and not (observer_url and observer_key_id and observer_token):
        # G1+G2, and fail closed BEFORE anything is minted: the contract requires every proposed
        # record to name the observation that caused it, so a pass that cannot file one cannot
        # propose at all.
        missing = (
            "the orchestrator observer credential is unset; --submit needs it "
            "(ORCHESTRATOR_API_URL, ORCHESTRATOR_API_CREDENTIAL_KEY_ID, ORCHESTRATOR_API_TOKEN)"
        )
    elif submit and observer_key_id != OBSERVER_KEY_ID:
        missing = (
            f"ORCHESTRATOR_API_CREDENTIAL_KEY_ID is {observer_key_id!r}, not "
            f"{OBSERVER_KEY_ID!r}; this producer files observations as the observer and "
            "attribution cannot be corrected once written"
        )
    elif submit and not alert_token:
        # Refused before anything is minted, like the observer: a pass that could not tell a
        # person about a bump it can't route would leave that bump silent.
        missing = "SDS_BRIDGE_ALERT_TOKEN is unset; --submit needs it to send alerts"
    if missing:
        print(missing, file=sys.stderr)
        return None
    return _Credentials(
        github,
        change_manager,
        read,
        url,
        observer_url,
        observer_key_id,
        observer_token,
        alert_token,
    )


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submit",
        action="store_true",
        help=(
            "actually create or revise, approve, propose and send alerts. Without it the pass "
            "is a dry run: it commits, proposes and sends nothing and needs no credential that "
            "writes to change-manager or the orchestrator (the launcher still fetches the alert "
            "sender token, which a dry run never uses)."
        ),
    )
    parser.add_argument(
        "--repository", action="append", help="limit to one target repository (repeatable)"
    )
    args = parser.parse_args(argv)

    resolved_credentials = _credentials(submit=args.submit)
    if resolved_credentials is None:
        return EXIT_UNUSABLE
    # READ ONCE, BEFORE ANYTHING IS JUDGED OR WRITTEN. An unreadable record is unusable input, never
    # a smaller population: with no grants to read, every package would look ungranted.
    try:
        members = read_members_from_environment()
    except MembersUnreadable as error:
        print(
            f"[UNUSABLE] the member record could not be read, so nothing is proposed: {error}",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE
    (
        github_token,
        cm_token,
        read_token,
        cm_url,
        orchestrator_url,
        orchestrator_key_id,
        orchestrator_token,
        alert_token,
    ) = resolved_credentials

    rule = _declared_rule(read_token, base_url=cm_url or DEFAULT_BASE_URL)
    if rule is None:
        return EXIT_UNUSABLE

    root = checkout_root()
    resolved = _lane(
        root, submit=args.submit, requested=args.repository, members=members, rule=rule
    )
    if resolved is None:
        return EXIT_UNUSABLE
    packages, scope = resolved
    held = {member.repository.casefold(): member.holds for member in members}

    client = None
    observer = None
    outcomes: list[Outcome] = []
    raised = alerts.Raised()
    now = datetime.now(UTC)
    try:
        if args.submit:
            client = ChangeManagerClient(cm_token, base_url=cm_url or DEFAULT_BASE_URL)
            observer = open_client(
                base_url=orchestrator_url,
                credential_key_id=orchestrator_key_id,
                token=orchestrator_token,
            )
        records = client.work_records() if client is not None else []
        with GitHubReader(token=github_token) as reader:
            for repository in scope:
                outcomes.extend(
                    _repository_pass(
                        reader,
                        repository,
                        rule,
                        packages,
                        client,
                        observer,
                        records,
                        root,
                        now,
                        raised,
                        held.get(repository.casefold(), ()),
                    )
                )
    except (UnusableEndpointError, ObservationCredentialError, ChangeManagerError) as error:
        # BOTH ARE "this pass could not use its inputs" rather than "this pass found something",
        # which is what makes one handler honest rather than a convenience. A typo in the
        # orchestrator URL is the tool being unusable for every bump at once, deliberately not the
        # per-bump failure that would report as a finding; change-manager being unreachable is the
        # same fact about the other service.
        print(str(error), file=sys.stderr)
        return EXIT_UNUSABLE
    finally:
        if client is not None:
            client.close()
        if observer is not None:
            observer.close()

    outcomes.extend(
        _send_alerts(alert_token, raised, outcomes, args.repository, submit=args.submit)
    )
    return _report(outcomes, rule)


def _send_alerts(
    token: str,
    raised: alerts.Raised,
    outcomes: list[Outcome],
    requested: list[str] | None,
    *,
    submit: bool,
) -> list[Outcome]:
    """Send the pass's alerts once every repository is read; a failure is a finding. A dry run
    sends nothing and writes no state file."""
    if not submit:
        return []
    # Repositories in the lane this pass did not read: GitHub could not answer, or the landing
    # policy declares them in neither population. Their remembered keys are neither resolved nor
    # fired again; a repository that left the lane has no outcome, so its keys are resolved.
    unread_statuses = {"unreadable", "not-declared-landing"}  # not-a-vocabulary: two local statuses
    unreadable = {o.repository for o in outcomes if o.status in unread_statuses}
    failure = alerts.sync(token, raised, unreadable=unreadable, requested=requested)
    return [] if failure is None else [Outcome("alert-hub", 0, "error", failure)]


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
