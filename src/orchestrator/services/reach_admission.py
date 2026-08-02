"""Whether a work unit may be admitted at all, given what its package says it touches.

WS-P2.18 Increment 4. Reach became a declarable key in Increment 1 and a policy key in Increment
2, and until now nothing required it: a package that declared nothing was admitted exactly like one
that declared everything, so the field was documentation. This module is what binds it. From here
on an undeclared reach is a refusal, and the refusal is the whole point -- a factory that decides
what work may run by what that work touches cannot be told "unknown" and proceed.

**Grandfathering is bounded by a named list, never by the absence it exempts.** Revisions approved
before the key existed carry no declaration and never can, because the lineage approval is hashed
over the package file. The artifact names those revisions individually
(``FactoryPolicy.admission_refusals``), so a revision created tomorrow cannot fall into the
exemption however little it declares -- and the list is required to still have a subject, so it
deletes itself rather than becoming permanent.

**This is a different question from whether a human must read the envelope**, which
``services.authority_gate`` answers and which grandfathering does not touch. A grandfathered
revision's units are admissible and still need that approval. Separating them is what keeps the
exemption from widening into one: reach is missing on those units, so no known-good pattern can
recognise them, so the person still reads it.

**Every failure here resolves to refusing.** A policy that will not load, a grandfathering with
nothing left to grandfather, and a declaration naming something this build does not know all return
a refusal rather than an absence of one, because the alternative reading of "policy could not
answer" is that it had nothing to object to.

**The change window (Increment 5) is a SECOND term here, not part of the first.** ``may this work
be admitted at all`` and ``may it be admitted now`` fail differently and are fixed differently: the
first is a property of the package that only editing it can change, the second fixes itself when
the clock moves. Folding them together would report the self-clearing one in place of the standing
one and send an operator away to wait for a moment that changes nothing. Each term consults the
artifact for itself, which costs one extra read of a small file and no state -- the artifact is
deliberately never cached (ADR-0010), so there is no shared parse to reuse and nothing to
invalidate.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.clock import Clock, TransactionClock
from orchestrator.errors import DomainError
from orchestrator.factory_policy import FactoryPolicy, load_factory_policy
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.reach_vocabulary import LIVE_ESTATE, reach_from_snapshot
from orchestrator.services.estate_landing import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    SOURCE_UNCONFIGURED,
    EstateLandingSource,
)

# Policy could not be consulted or could not be evaluated, so it recognised nothing and objected to
# nothing -- which is the one state where those two are not the same. Named separately from the
# artifact's own refusals because it is a fault in this process rather than a statement about this
# unit. Shared by both terms below: whichever notices first reports the same fault.
REACH_POLICY_UNREADABLE = "reach_policy_unreadable"

# The declaration says nothing already serving changes; App Brain says landing on this
# repository's default branch changes something already serving. Fixing it means editing the
# package, which is why this reads as a defect rather than as a wait.
REACH_CONTRADICTS_ESTATE = "reach_contradicts_estate"

# App Brain has no assessment for this repository -- either no app record at all, or a record it
# has not assessed. Distinct from the two below because the fix is neither an environment variable
# nor a service: somebody has to determine the answer and record it.
REACH_ESTATE_UNKNOWN = "reach_estate_unknown"

# This process could not obtain an answer. Two codes, not one, because they send different people
# somewhere different: the first is a missing setting on this deployment and affects every unit
# equally; the second is App Brain refusing or unreachable and may clear on its own.
REACH_ESTATE_SOURCE_UNCONFIGURED = "reach_estate_source_unconfigured"
REACH_ESTATE_SOURCE_UNREADABLE = "reach_estate_source_unreadable"

# A unit in one of these states has stopped for good: neither has an outgoing edge in
# `kernel.states.LEGAL_EDGES`, so nothing reached through it will be admitted again. FAILED is
# deliberately absent, because `FAILED -> READY` is legal and a failed unit can still run.
_SPENT_STATE_NAMES = tuple(
    str(state) for state in (WorkUnitState.COMPLETED, WorkUnitState.CANCELLED)
)


def reach_admission_refusal(session: Session, revision: WorkPackageRevision) -> str | None:
    """Why this revision's work may not be admitted on reach grounds; ``None`` means it may.

    The declaration lives on the revision rather than the unit because reach is what the PACKAGE
    says its work touches -- decomposition splits that work up, it does not change where it lands.
    Read through ``reach_from_snapshot``, which is the single reader and which returns ``None`` for
    any declaration it cannot read whole, so a snapshot naming a member this build predates is
    undeclared here rather than the recognisable part of itself.
    """
    try:
        policy = load_factory_policy()
        policy.require_live_subject(_live_grandfathered_revisions(session, policy))
    except DomainError:
        return REACH_POLICY_UNREADABLE
    refusals = policy.admission_refusals(
        reach_from_snapshot(revision.enforcement_snapshot), revision.id
    )
    # First by the artifact's own sort order. There is one term per reason and admission reports
    # one reason, so reporting the rest would need a refusal list on every record for a case that
    # only arises when an author declares two broken things at once.
    return refusals[0] if refusals else None


def estate_refusal(
    revision: WorkPackageRevision,
    target_repository: str,
    source: EstateLandingSource,
) -> str | None:
    """Why this revision's declared reach is contradicted by the estate; ``None`` means it is not.

    **Declared-and-verified, never derived.** The author still says what the work reaches (R8);
    this only checks that saying so did not contradict what App Brain records about the repository
    the work lands in. Nothing here writes a reach, infers one, or repairs one.

    **The claim under test is made by OMITTING ``live_estate``, not by naming anything.** So the
    term is keyed on that omission rather than on ``source_repository`` being present: a package
    declaring only ``external_system`` makes the same implicit claim about this estate, and keying
    on the positive member would have let it through while catching the honest case.

    **It refuses a landing that redeploys even when the unit's own act is inert, and that is the
    conservative direction on purpose.** Opening a pull request genuinely changes nothing running;
    the work that started this -- a dependency bump against a repository that redeploys itself --
    was correctly declared for its own execution and wrongly declared for its own definition of
    done, whose success signal was a landed pull request. This function cannot read a success
    signal, so it asks the narrower question it can answer and errs toward the restraint that
    declaring ``live_estate`` buys: a change window and a longer hold. Being wrong that way costs
    an author some hours; being wrong the other way is an unplanned change to something serving.

    **Every path that is not an explicit ``inert`` refuses.** That is structural rather than
    enumerated: an answer this build does not recognise, an absent one, and one that says the
    estate has not looked all fall through to a refusal, so a fourth value shipped on the
    authoring side cannot arrive here as permission.
    """
    declared = reach_from_snapshot(revision.enforcement_snapshot)
    if declared is None:
        # An undeclared reach is `reach_admission_refusal`'s term and it already refuses. Claiming
        # it here too would report a second reason for one defect, and -- because this term sits
        # below that one -- would only ever be the reason nobody sees.
        return None
    if LIVE_ESTATE in declared:
        return None
    if not target_repository:
        # `target_repository_missing` is dispatch's own term, ordered below this one. Asking App
        # Brain about the empty string would answer `unknown` and report a missing assessment in
        # place of a missing constraint.
        return None
    answer = source.landing_for(target_repository)
    if answer.landing == LANDING_INERT:
        return None
    if answer.landing == LANDING_REDEPLOYS:
        return REACH_CONTRADICTS_ESTATE
    if answer.landing is not None:
        return REACH_ESTATE_UNKNOWN
    if answer.reason == SOURCE_UNCONFIGURED:
        return REACH_ESTATE_SOURCE_UNCONFIGURED
    return REACH_ESTATE_SOURCE_UNREADABLE


def change_window_refusal(
    session: Session,
    revision: WorkPackageRevision,
    clock: Clock | None = None,
) -> str | None:
    """Why this revision's work may not START now; ``None`` means policy raises no objection.

    Asked once, at admission, and nowhere else. Nothing consults this again after work has been
    sent: a window closing over a run in progress must not touch it, because the worker reports
    back at the END of its run and interrupting it there strands the unit with its attempt spent.

    The clock is a parameter so that behaviour depending on the time can be exercised without
    depending on the time. It defaults to the transaction's own timestamp, which is an aware
    instant read from the database rather than from this process -- and the related trap is already
    documented here: a row's ``updated_at`` cannot be back-dated, because a trigger rewrites it, so
    ageing data is not a way to test anything that reads a clock.
    """
    try:
        policy = load_factory_policy()
        return policy.window_refusal(
            reach_from_snapshot(revision.enforcement_snapshot),
            (clock or TransactionClock()).now(session),
        )
    except DomainError:
        return REACH_POLICY_UNREADABLE


def _live_grandfathered_revisions(session: Session, policy: FactoryPolicy) -> Sequence[UUID]:
    """Which grandfathered revisions can still produce work that admission would judge.

    **Being spent requires PROOF; being live is the answer to everything else.** A revision is
    spent when it has produced units and every one of them has stopped for good -- positive,
    observable evidence that nothing it owns will be admitted again. Anything short of that reads
    as live, including a listed id this database has never seen.

    That asymmetry is deliberate, and it is the one place this mechanism declines to be strict.
    Deleting the exemption is what makes the whole artifact stop answering, so inferring "finished"
    from "not found" would turn a restored database, a fresh environment, or a mistyped id into a
    factory-wide halt on missing data. The cost of the other direction is bounded and dull: a
    listed id naming nothing keeps a table alive that exempts nobody, which the report shows in
    full for exactly this reason.

    Having units is what "was broken down" means -- units are created only when a decomposition is
    approved -- so this reads one table rather than joining two to ask the same question.
    """
    if policy.grandfathered is None:
        return ()
    listed = sorted(policy.grandfathered.revisions)
    spent = set(
        session.scalars(
            select(WorkUnit.work_package_revision_id)
            .where(WorkUnit.work_package_revision_id.in_(listed))
            .group_by(WorkUnit.work_package_revision_id)
            .having(
                func.count() == func.count().filter(WorkUnit.state.in_(_SPENT_STATE_NAMES)),
            )
        )
    )
    return [revision for revision in listed if revision not in spent]
