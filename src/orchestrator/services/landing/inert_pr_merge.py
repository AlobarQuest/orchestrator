"""The orchestrator lands a pull request into a repository where landing changes nothing already
serving. ADR-0038 part 2.

**This is the third place in this repository that changes a repository nobody asked it to change
at the moment it acts, and it is the least consequential of the three** -- the landed commit sits
on a default branch until something separately acts on it, which for these six repositories is
nothing. What it costs is not a running service; it is that `main` is what every build session
branches from and what default-branch CI now runs on.

The shape is shared with the sibling that lands where landing DOES change something already
serving, and its steps live once in `lane_act`: a cascade of named refusals re-evaluated here, a row
with a unique constraint so a repeat is detectable, an injected gateway so the whole path runs
with no network, and credentials resolved once so the gate can never attest to credentials the
actor does not use.

## Why the orchestrator, rather than the platform's own arming

This lane replaces a GitHub Actions workflow that armed the platform's own automatic landing with
the workflow-scoped token. Measured across the estate's merge history: 38 landings armed that way
fired **zero** default-branch workflow runs, against 18 by this estate's App which fired 18. So
those six repositories have been skipping default-branch CI on every unattended landing, and
`main` could be red there with nothing reporting it. Performing the act directly is what switches
that CI back on -- which is the whole of what ADR-0038 gains.

## What it writes into the landing commit, and why

One trailer, naming the policy version that permitted the landing. The estate's ledger observes
landings independently and classifies each by the permission it can find; with the workflow gone
there is no gate run at the head to attribute one to, so without something in the artifact itself
every landing here would record as having no accountable basis at all -- a class the ledger keeps
and, until ADR-0038 part 3, no detector reads.

**The trailer's NAME is what identifies the lane**, so a reader needs no second marker and no
inference from which other trailers are absent.

Passing an explicit body replaces the one the repository setting would have composed. That is
accepted rather than overlooked: the bot's own dependency metadata stays on the pull request and
its branch commits, and the ledger already falls back to the head commit for exactly that reason.

## How it lands, which is not the same for every subject

Almost every pull request this lane sees is the end of its own lineage -- one bot's branch, whose
commits nothing will ever be merged from again -- and discarding them for a single commit carrying
the content is exactly what a squash is for.

One population is different: a fork's upstream sync replays commits from a repository this estate
does not own and WILL take from again. Squashing those hands the fork the content while leaving the
most recent commit the two sides share where it was, so the next sync compares against that stale
point, finds both sides carrying the same lines, and conflicts on files nobody here has touched.
Keeping the commits is what stops it. WHICH subjects those are is not decided here -- it follows
from a fact the policy already declares about the author; see `inert_landing_admission`.

## Idempotency: check, act, reconcile, record

A landing is not idempotent and its failure is asymmetric -- a lost success answers the same way a
refusal does. So the row is read before the call, the call is made, a refusal is followed by a
confirming read before anything is recorded, and only then is the outcome written.

Act-then-record, because the two failure modes are not equal. A record written before a call that
then fails is a lie. A landing whose record is lost is recoverable: the ledger observes it
independently and would report it as basis-less rather than not at all.

## The switch defaults to refusing

Its caller is a scheduled one, so it has a switch for the reason its sibling has one: a switch
against a loop is a real control where a switch against one operator is ceremony. Its own switch
rather than the sibling's, because the two lanes are activated by different decisions and turning
one on must not turn the other on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorContext
from orchestrator.persistence.models import EstatePrMerge
from orchestrator.services.landing.estate_landing import EstateLandingSource
from orchestrator.services.landing.estate_pr_merge import (
    GitHubEstatePullRequests,
)
from orchestrator.services.landing.inert_landing_admission import (
    InertLandingAdmission,
    inert_landing_admission,
)
from orchestrator.services.landing.inert_landing_policy import InertLandingPolicySource
from orchestrator.services.landing.interfaces import EstateReadGateway, MergeOutcome
from orchestrator.services.landing.lane_act import LandingLane, land_pull_request

# The trailer the landing writes into the landing commit's body, and the estate's ledger reads back
# out of it. It reaches the artifact under either landing method, measured rather than assumed --
# see `submit_merge`, which records what each produces.
# Named here because this is the only writer; the reader pins the same spelling on its own
# side, and a disagreement between the two is a landing recorded with no basis rather than a
# crash -- which is why both sides carry a test naming the literal rather than deriving it from
# the other.
#
# ONE TRAILER, AND ITS NAME CARRIES THE LANE. The sibling stamps two, because a landing there is
# permitted by a change record AND the policy version that approved it. Here there is no record,
# so a bare version number would be indistinguishable from the sibling's second trailer; naming
# the population in the key is what makes the basis readable without a second marker.
INERT_LANDING_POLICY_TRAILER: Final = "SDS-Inert-Landing-Policy"

# Refusals that leave NO RECORD, because nothing happened and each can be tried again.
INERT_MERGE_NOT_ADMISSIBLE: Final = "inert_merge_not_admissible"
INERT_MERGE_REFUSED_BY_REMOTE: Final = "inert_merge_refused_by_remote"
INERT_MERGE_HEAD_MOVED: Final = "inert_merge_head_moved"

_LANE: Final = LandingLane(
    event_prefix="inert_pr_merge",
    not_admissible=INERT_MERGE_NOT_ADMISSIBLE,
    head_moved=INERT_MERGE_HEAD_MOVED,
    refused_by_remote=INERT_MERGE_REFUSED_BY_REMOTE,
)


class InertPullRequestGateway(EstateReadGateway, Protocol):
    """Everything the cascade reads, plus the two calls that change anything.

    **THE LANDING IS NAMED `merge`, DELIBERATELY, AND THAT IS NOT A DETAIL.** This repository's
    merge guard finds a landing by the REST path spelled in a file or by an attribute call named
    `merge`, and a landing performed through an injected gateway spells neither -- so a module in
    this shape can land pull requests and be invisible to the one control that lists every file
    that does. Naming the act with the spelling the guard reads is what makes this file's entry in
    that list real rather than nominal, and it is why the entry can be taken openly, which is the
    only way ADR-0020 permits the prohibition to be lifted at all.
    """

    def merge(
        self,
        *,
        repository: str,
        number: int,
        head_sha: str,
        commit_message: str,
        merge_method: str,
    ) -> MergeOutcome: ...


class GitHubInertPullRequests(GitHubEstatePullRequests):
    """The real gateway: the sibling's client, with the landing under the name above.

    Composition would have meant five delegating methods and a second place for the read surface
    to drift; there is exactly one behavioural difference between the two lanes' use of GitHub,
    and it is the name. Nothing is overridden.
    """

    def merge(
        self,
        *,
        repository: str,
        number: int,
        head_sha: str,
        commit_message: str,
        merge_method: str,
    ) -> MergeOutcome:
        return self.submit_merge(
            repository=repository,
            number=number,
            head_sha=head_sha,
            commit_message=commit_message,
            merge_method=merge_method,
        )


@dataclass(frozen=True)
class InertMergeCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    # The head the caller read when it read the admission answer. REQUIRED, with no default: a
    # default meaning "skip the check" is a precondition that holds by the good behaviour of the
    # one caller that exists. The subject is a pull request in a foreign system and has no version
    # of ours; its head is what moves, and naming it is the same claim every other mutation makes
    # with `expected_version`.
    expected_head_sha: str


def land_inert_pull_request(
    session: Session,
    command: InertMergeCommand,
    gateway: InertPullRequestGateway,
    landing_source: EstateLandingSource,
    policy_source: InertLandingPolicySource,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> EstatePrMerge:
    """Land the pull request if this lane's answer admits it. The steps are `lane_act`'s.

    `change_record_id` IS LEFT NULL, and that is what a row from this lane looks like: there is no
    record here and there cannot be one. A column added to carry that distinction would have no
    reader, which is the dead-knob defect this repository has paid for before; what discriminates
    in the event stream is the ACTION, which names this lane.
    """
    return land_pull_request(
        session,
        command,
        _LANE,
        gateway,
        admit=lambda repository: inert_landing_admission(
            session,
            repository,
            command.pr_number,
            landing_source,
            policy_source,
            gateway,
            enabled=enabled,
            credentials_configured=credentials_configured,
        ),
        remote_call=lambda admission, head_sha: _act(gateway, admission, head_sha),
        change_record_id=None,
    )


def _act(
    gateway: InertPullRequestGateway, admission: InertLandingAdmission, head_sha: str
) -> MergeOutcome:
    """The remote call itself. What happens around it -- the reconciling re-read and the record --
    is `lane_act`'s."""
    return gateway.merge(
        repository=admission.repository,
        number=admission.pr_number,
        head_sha=head_sha,
        commit_message=_trailers(admission),
        # THREADED, never re-derived. The cascade read the pull request and the policy once and
        # decided from both; asking either of them again here would be a second reading of a
        # subject that can move between the two, and the act would then be performed under a
        # rule the answer it was admitted on never saw.
        merge_method=admission.merge_method,
    )


def _trailers(admission: InertLandingAdmission) -> str:
    """The permission, written into the artifact the estate's ledger will read.

    Only values that stay true, and nothing dated: the ledger freezes every string a landing
    carries at the first observation of it, so a body that named a count or a moment would make a
    later pass over an unchanged landing conflict with itself.

    The version is the POLICY DOCUMENT's, not the block's -- one number covers both populations,
    so a revision moving only the deploying half re-stamps what a landing here is attributed to.
    That follows from there being one holder of the rule; ADR-0038 records it so a ledger reader
    does not assume the number tracks the rule it names.
    """
    return f"{INERT_LANDING_POLICY_TRAILER}: {admission.policy_version}"
