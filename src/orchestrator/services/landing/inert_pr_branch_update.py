"""The lane brings a pull request's head up to date with the base it has itself moved, in a
repository where landing on the default branch changes nothing already serving. ADR-0038 part 2.

**Why this exists at all, and why it is not optional.** The landing lane it accompanies requires
the head to be current with its base -- a TIGHTENING over the workflow it replaces, which required
nothing. But that condition, once required, is one the lane CREATES: a landing moves the base, so
every sibling pull request in the repository becomes behind it at that instant, and the next pass
refuses them all for a reason the previous pass caused. Nothing else in the estate resolves it. The
update bot rebases on its own schedule, which is weekly across most of these repositories, and the
one measured wait was 29 hours. So requiring freshness without this act is a strict degradation
over what it replaces, and the two ship together.

## What it writes down: an event, and NO row of its own

The landing keeps a row because its act cannot be retried: that row is unique per pull request with
no delete path, so recording an outcome bars the subject forever, which is right for *it landed* and
would be badly wrong here. This act is the opposite kind -- repeatable by design, because whenever
the base moves again it is right to do again.

So the durable trace is an event and nothing else, and the difference that makes it safe is WHAT
THE KEY NAMES. The caller's key is content-addressed over the head, and a successful update changes
the head, so the next legitimate update carries a different key and is never barred by this one. A
key bars only a repeat of the same request against the same head, which is exactly what a replay is.

## It serialises on the repository, and the reason is NOT the one the branch suggests

What must not be raced is not the branch -- the platform holds that itself, since the head is named
in the request and a head that moved is refused there. It is the KEY. Two concurrent requests
carrying one idempotency key both read no spent event, both act, and the loser's commit violates
the globally unique index -- an `IntegrityError`, which has no registered handler and so reaches the
caller as a bare HTTP 500 over an act that in fact happened twice.

The lock key is the one the sibling lane already uses, because it is per repository and the two
populations cannot overlap: each lane requires the opposite answer from the estate about the same
repository, and the estate gives one answer per repository. One repository, one branch-update lock,
whichever lane is asking.

## Every fact about this act is read from the composed answer

The permission, the head, the off-switch and the credential check all come off one cascade, so a
deployment that has not been told it may land anything cannot be made to touch a branch either --
by the same term, not by a second one somebody has to remember to write.

## It edits one Dependabot branch per repository at a time. ADR-0045.

Bringing a Dependabot pull request up to date writes a commit under this estate's identity, and
from then on Dependabot refuses to rebase that branch. Two such branches in one repository, and a
landing of either, is the whole precondition of a deadlock: the landing conflicts the other,
Dependabot will not rebase it because it was edited, and this lane will not freshen a conflicted
head. Nobody can act.

So before the remote is asked, the act asks whether another Dependabot pull request in the
repository is already edited and still queued to land. If it is and this branch is still
Dependabot's, the branch is left alone -- and staying Dependabot's is exactly what keeps it safe,
because a sibling Dependabot still owns is one it rebases when a landing conflicts it. A branch
that is already edited is always freshened again: it has nothing left to lose. When the siblings
cannot be read the act refuses with a code of its own, because not knowing is a finding and
waiting one's turn is not.

The act works this out for itself, inside its own transaction and under the repository lock it
already holds. It never reads the served answer's copy of the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

from orchestrator.clock import Clock
from orchestrator.kernel.states import ActorContext
from orchestrator.services.landing.branch_update_serialization import (
    INERT_BRANCH_UPDATE_ACTION,
    inert_sibling_composer,
)
from orchestrator.services.landing.estate_landing import EstateLandingSource
from orchestrator.services.landing.inert_landing_admission import inert_landing_admission
from orchestrator.services.landing.inert_landing_policy import InertLandingPolicySource
from orchestrator.services.landing.lane_act import (
    BranchUpdateGateway,
    BranchUpdateLane,
    BranchUpdateOutcome,
    update_pull_request_branch,
)

INERT_BRANCH_UPDATE_SUBJECT: Final = "inert_pull_request"

# The composed answer does not name freshness as this pull request's sole remaining obstacle. The
# refusals it does name are carried in the message, because they are the answer to the only
# question the caller can act on.
INERT_BRANCH_UPDATE_NOT_QUALIFIED: Final = "inert_branch_update_not_qualified"

# The platform declined, or could not be reached. Nothing is recorded and nothing is barred: the
# next pass composes the answer again and asks again, which is the right behaviour for an act whose
# whole nature is that repeating it is harmless.
INERT_BRANCH_UPDATE_REFUSED_BY_REMOTE: Final = "inert_branch_update_refused_by_remote"

# The pull request moved between the answer the caller read and this call.
INERT_BRANCH_UPDATE_HEAD_MOVED: Final = "inert_branch_update_head_moved"

# ADR-0045. Another Dependabot pull request this lane has already edited is queued to land, and this
# one is still Dependabot's. A deliberate withhold that clears when the branch ahead lands.
INERT_BRANCH_UPDATE_SIBLING_HOLDING: Final = "inert_branch_update_sibling_holding"

# ADR-0045. It could not be established that no other edited branch is queued to land. Not
# self-clearing: not knowing clears on nothing.
INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE: Final = "inert_branch_update_siblings_unreadable"

_LANE: Final = BranchUpdateLane(
    action=INERT_BRANCH_UPDATE_ACTION,
    subject_type=INERT_BRANCH_UPDATE_SUBJECT,
    not_qualified=INERT_BRANCH_UPDATE_NOT_QUALIFIED,
    head_moved=INERT_BRANCH_UPDATE_HEAD_MOVED,
    sibling_holding=INERT_BRANCH_UPDATE_SIBLING_HOLDING,
    siblings_unreadable=INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE,
    refused_by_remote=INERT_BRANCH_UPDATE_REFUSED_BY_REMOTE,
)


@dataclass(frozen=True)
class InertBranchUpdateCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    # The head the caller read when it read the composed answer: the subject is a pull request in a
    # foreign system and has no version of ours, so its head is the value that moves and naming it
    # is the same claim every other mutation makes with `expected_version`.
    expected_head_sha: str


def update_inert_pull_request_branch(
    session: Session,
    command: InertBranchUpdateCommand,
    gateway: BranchUpdateGateway,
    landing_source: EstateLandingSource,
    policy_source: InertLandingPolicySource,
    *,
    enabled: bool,
    credentials_configured: bool,
    clock: Clock | None = None,
) -> BranchUpdateOutcome:
    """Compose this lane's answer, and act only on what it says. The steps are `lane_act`'s.

    The caller's clock reaches only the sibling rule's ten-minute bound: this lane's answer reads
    no clock of its own, for this pull request or for a sibling.
    """
    return update_pull_request_branch(
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
        sibling_composer=lambda repository: inert_sibling_composer(
            session,
            repository,
            landing_source,
            policy_source,
            gateway,
            enabled=enabled,
            credentials_configured=credentials_configured,
        ),
        clock=clock,
    )
