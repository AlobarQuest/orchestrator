"""The lane brings a pull request's head up to date with the base it has itself moved.
ADR-0019 Increment 6.

**This is the first thing the lane changes outside a landing, and saying so plainly is part of the
increment.** It is strictly smaller than the act it accompanies -- the landing rewrites a default
branch and starts a rollout on a running service, while this one brings the base branch's commits
into a topic branch that nothing serves and nobody reads -- but it is a write to a repository
nobody asked this process to write to, performed unattended, and that class has exactly one other
member.

## Why it exists at all

The freshness condition on landing is correct and is not in question: required checks on these
repositories are deliberately not gated on being up to date, so a check can be green against a
head that is behind its base, and squashing that head produces a tree nothing has executed. Where
landing changes something already serving, that tree is what starts serving.

But the lane CREATES that condition. A landing moves the base, so every sibling pull request in
the repository becomes behind it at that instant, and the next pass refuses them all for a reason
the previous pass caused. Nothing resolved it: measured, one pull request sat 29 hours behind
while three windows passed over it, and a night's four passes could only re-report the same two.

So the lane clears what the lane staled. That is the whole of the change -- the condition stands
untouched, and what moves is the branch rather than the rule.

## What it writes down: an event, and NO row of its own

The landing keeps a row because its act cannot be retried: that row is unique per pull request
with no delete path, so recording an outcome bars the subject forever, which is right for *it
landed* and would be badly wrong here. This act is the opposite kind -- repeatable by design,
because whenever the base moves again it is right to do again.

So the durable trace is an event and nothing else, and the difference that makes it safe is WHAT
THE KEY NAMES. The caller's key is content-addressed over the head, and a successful update
changes the head, so the next legitimate update carries a different key and is never barred by
this one. A key bars only a repeat of the same request against the same head, which is exactly
what a replay is.

The event's subject is the pull request itself, named by a `uuid5` over its URL, because there is
no row of ours to point at and inventing one would be inventing the permanence this act must not
have. That construction is used elsewhere in this repository for the same reason.

## It serialises on the repository, and the reason is NOT the one the branch suggests

A first version of this module argued that no lock was needed: what must not be raced is the
branch, and the platform holds that itself, since the head is named in the request and a head that
moved is refused there. **That is true of the branch and false of the KEY**, which is the race that
matters. Two concurrent requests carrying one idempotency key both read no spent event, both act,
and the loser's commit violates the unique index -- an `IntegrityError`, which has no registered
handler and so reaches the caller as a bare HTTP 500 over an act that in fact happened twice.

So it takes the same advisory lock its sibling does, for a different reason and over a row that
may not exist yet, which is what a row lock cannot cover. It is released with the transaction
whichever way that ends.

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
    BRANCH_UPDATE_ACTION,
    estate_sibling_composer,
)
from orchestrator.services.landing.change_record import ChangeRecordSource
from orchestrator.services.landing.estate_landing import EstateLandingSource
from orchestrator.services.landing.estate_landing_admission import (
    estate_landing_admission,
)
from orchestrator.services.landing.lane_act import (
    BranchUpdateGateway,
    BranchUpdateLane,
    BranchUpdateOutcome,
    update_pull_request_branch,
)

BRANCH_UPDATE_SUBJECT: Final = "estate_pull_request"

# The composed answer does not name freshness as this pull request's sole remaining obstacle. The
# refusals it does name are carried in the message, because they are the answer to the only
# question the caller can act on.
BRANCH_UPDATE_NOT_QUALIFIED: Final = "estate_branch_update_not_qualified"

# The platform declined, or could not be reached. Nothing is recorded and nothing is barred: the
# next pass composes the answer again and asks again, which is the right behaviour for an act
# whose whole nature is that repeating it is harmless.
BRANCH_UPDATE_REFUSED_BY_REMOTE: Final = "estate_branch_update_refused_by_remote"

# The pull request moved between the answer the caller read and this call.
BRANCH_UPDATE_HEAD_MOVED: Final = "estate_branch_update_head_moved"

# ADR-0045. Another Dependabot pull request this lane has already edited is queued to land, and this
# one is still Dependabot's. Editing it too would make two branches Dependabot will no longer
# rebase, and a landing of either conflicts the other with nobody able to act. A deliberate
# withhold: it clears when the branch ahead lands, so the caller treats it as self-clearing.
BRANCH_UPDATE_SIBLING_HOLDING: Final = "estate_branch_update_sibling_holding"

# ADR-0045. The open pull requests, their commits, or a sibling's own answer could not be read, so
# it is not established that no other edited branch is queued. NOT self-clearing, and spelled so
# that neither sibling code contains the other: not knowing clears on nothing, and a repository
# whose reads keep failing must stay a finding rather than look like one waiting its turn.
BRANCH_UPDATE_SIBLINGS_UNREADABLE: Final = "estate_branch_update_siblings_unreadable"

_LANE: Final = BranchUpdateLane(
    action=BRANCH_UPDATE_ACTION,
    subject_type=BRANCH_UPDATE_SUBJECT,
    not_qualified=BRANCH_UPDATE_NOT_QUALIFIED,
    head_moved=BRANCH_UPDATE_HEAD_MOVED,
    sibling_holding=BRANCH_UPDATE_SIBLING_HOLDING,
    siblings_unreadable=BRANCH_UPDATE_SIBLINGS_UNREADABLE,
    refused_by_remote=BRANCH_UPDATE_REFUSED_BY_REMOTE,
)


@dataclass(frozen=True)
class EstateBranchUpdateCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    idempotency_key: str
    # The head the caller read when it read the composed answer, for the reason its sibling states:
    # the subject is a pull request in a foreign system and has no version of ours, so its head is
    # the value that moves and naming it is the same claim every other mutation makes.
    expected_head_sha: str


def update_estate_pull_request_branch(
    session: Session,
    command: EstateBranchUpdateCommand,
    gateway: BranchUpdateGateway,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    *,
    enabled: bool,
    credentials_configured: bool,
    clock: Clock | None = None,
) -> BranchUpdateOutcome:
    """Compose this lane's answer, and act only on what it says. The steps are `lane_act`'s.

    The caller's clock reaches this pull request's answer, every sibling's answer, and the
    sibling rule's ten-minute bound alike, so all three are judged at the same moment.
    """
    return update_pull_request_branch(
        session,
        command,
        _LANE,
        gateway,
        admit=lambda repository: estate_landing_admission(
            session,
            repository,
            command.pr_number,
            landing_source,
            record_source,
            gateway,
            enabled=enabled,
            credentials_configured=credentials_configured,
            clock=clock,
        ),
        sibling_composer=lambda repository: estate_sibling_composer(
            session,
            repository,
            landing_source,
            record_source,
            gateway,
            enabled=enabled,
            credentials_configured=credentials_configured,
            clock=clock,
        ),
        clock=clock,
    )
