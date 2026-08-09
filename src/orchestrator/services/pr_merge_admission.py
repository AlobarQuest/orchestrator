"""May the factory land this unit's pull request itself, and if not, why?

ADR-0020, Increment 4a. **This module answers; it does not act.** It holds no credential, imports
no client, and nothing it returns causes anything to happen. That separation is the point: the
answer can be read against the real production population, on real completed units, before
anything is wired to obey it -- and if it says yes to something surprising, that arrives while
nothing can act on it.

**`satisfied` is a positive conjunction, never "no refusal was raised."** Every term computes its
own affirmative answer and the answers are ANDed; the refusal list is built alongside for the
reader and is not what the verdict is derived from. An answer whose affirmative case is an empty
objection list is the fail-open shape this repository keeps finding, and this is the second module
to be written the other way round on purpose (the first is `verifier_decided_completion`).

**Every term is reported, not just the first.** Admission reports one reason because a blocked
unit needs one thing done next; this answers a question a person is asking about a unit that is
already finished, where the useful answer is the whole list. The one term that costs a network
round-trip is asked only when the unit names a repository to ask about, so a unit with no target
repository does not pay for an answer that could not mean anything.

**The estate term is asked of App Brain and nothing else.** A repository where landing on the
default branch changes something already serving is out of scope until ADR-0019's routing exists,
and the question is read from the estate, never from a list of repository names kept here.
It is deliberately NOT `reach_admission.estate_refusal`: that term answers *does the package's
declaration agree with the estate*, which is a different question off the same source, and reusing
it would be the correct-about-the-wrong-noun error this repository has made four times.

**Nothing here is a substitute for the human authority approval.** The approval is the gate that
remains, and one term below requires it to be bound to the unit's exact current fingerprint --
the same binding `exact_authority_approval` enforces for readiness, and unconditionally, because
the policy-pattern substitute that admission allows (ADR-0011) has no counterpart here: an
envelope granting this capability matches no known-good pattern and falls to the human gate by
totality (Increment 3).

**What this answer INHERITS FROM NOTHING, said out loud rather than left to be discovered.** It is
not admission and shares none of admission's environment gates: there is no target-repository
allowlist here, no hard off-switch, and no change window. Each absence is a position rather than
an oversight. The allowlist governs where work may be SENT, and a unit that was never sent can
still finish by hand. The change window belongs to a reach that names something already serving,
and the only landing this answer can approve is one the estate calls inert. **The off-switch is
the one that is genuinely open**: the flag admission reads is matched against
`unit.required_capability`, and a grant to land is a secondary entry in the capabilities map, so
no existing environment term could ever see it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import UnitPrBinding, WorkPackageRevision, WorkUnit
from orchestrator.persistence.repositories import PackageRepository
from orchestrator.services.estate_landing import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    SOURCE_UNCONFIGURED,
    EstateLandingSource,
)
from orchestrator.services.lifecycle import verifier_decided_completion
from orchestrator.services.reconciliation import open_conditions

# The capability a human approves per unit, in the envelope, the way every other capability is
# approved. Landing is never an ambient property of the factory (ADR-0020).
MERGE_CAPABILITY: Final = "github.pr.merge"

# The unit has not finished. Everything below is about a result, and there is no result yet.
WORK_UNIT_NOT_COMPLETED: Final = "work_unit_not_completed"

# Some required criterion was not settled by the verifier from its own evaluation of evidence.
# One code, because the per-criterion detail is already served in full by
# `verifier_decided_completion` on the evidence pack, and duplicating it here would be a second
# projection of one answer.
#
# **This is the "no human decided" half of ADR-0020's condition, and not the whole of it.** The
# decision says every criterion must have resolved deterministically from OBSERVED evidence; the
# predicate asks who decided and whether the outcome settles, never whether the evidence was
# observed. A criterion declared `automated_test` resolves off a row the WORKER recorded about its
# own run, so this term can be met on attested rather than observed evidence -- the known-open
# second half of WS-P2.32, surfacing here. Do not read a satisfied answer as "observed".
CRITERIA_NOT_VERIFIER_DECIDED: Final = "criteria_not_verifier_decided"

# An operator has an unresolved question about this unit. The reconciliation lane records what it
# observed of the pull request and its checks -- already landed elsewhere, a head that moved, a
# check that flipped red afterwards -- and a condition stays open until a person decides it. Those
# are the only observations of GitHub this repository holds about a unit, and the failure modes
# they name are exactly the ones the terms below can only approximate from rows written on this
# side. Landing past a pending human decision is the thing this whole increment must not do.
OPEN_RECONCILIATION_CONDITION: Final = "open_reconciliation_condition"

# The envelope does not grant landing at `allowed`. `level_for` answers `prohibited` for a name it
# does not carry, so an envelope that never mentioned the capability refuses here rather than
# falling through.
MERGE_CAPABILITY_NOT_AUTHORIZED: Final = "merge_capability_not_authorized"

# No human approval bound to this unit's exact current authority fingerprint.
AUTHORITY_APPROVAL_NOT_BOUND: Final = "authority_approval_not_bound"

# There is no pull request recorded for this unit, so there is nothing to land.
PR_BINDING_MISSING: Final = "pr_binding_missing"

# A binding exists but no head was ever armed for adjudication, so nothing states which tree the
# evidence was about.
VERIFICATION_HEAD_UNARMED: Final = "verification_head_unarmed"

# The armed head belongs to an earlier attempt than the one the unit finished on: the last cycle
# was never armed, so the armed value describes a tree an earlier adjudication was about.
VERIFICATION_HEAD_NOT_CURRENT_ATTEMPT: Final = "verification_head_not_current_attempt"

# A NEWER HEAD WAS REPORTED after the head that was adjudicated was handed over, so the tree that
# would land is not the tree the criteria were decided about.
#
# Reported, not observed, and the distinction is the point: both values are columns this side
# wrote, and the orchestrator never reads GitHub, so a push nobody reported leaves this term
# answering "unmoved". What actually closes that gap is naming the armed head at the moment of the
# act, which is why the answer reports it; the reconciliation term above is what sees a moved head
# that WAS observed.
PR_HEAD_MOVED_SINCE_VERIFICATION: Final = "pr_head_moved_since_verification"

# The envelope names no repository, so there is nothing to ask the estate about and nothing to
# land into.
MERGE_TARGET_REPOSITORY_MISSING: Final = "merge_target_repository_missing"

# App Brain records that landing on this repository's default branch changes something already
# serving. Out of scope until ADR-0019's routing exists.
MERGE_TARGET_REPOSITORY_REDEPLOYS: Final = "merge_target_repository_redeploys"

# The estate has no assessment for this repository -- no record at all, or a record nobody has
# assessed. The estate saying it has not looked is not permission.
MERGE_TARGET_ESTATE_UNKNOWN: Final = "merge_target_estate_unknown"

# THIS PROCESS has no answer, which is a different thing again from the estate not having one, and
# the two send different people somewhere different: a missing setting on this deployment, or App
# Brain refusing or unreachable.
MERGE_TARGET_ESTATE_SOURCE_UNCONFIGURED: Final = "merge_target_estate_source_unconfigured"
MERGE_TARGET_ESTATE_SOURCE_UNREADABLE: Final = "merge_target_estate_source_unreadable"


@dataclass(frozen=True)
class MergeAdmission:
    """The composed answer.

    `verified_head_sha` is the head that was adjudicated -- the armed head, not the latest one.
    It is reported because the act must name it, and for a sharper reason than tidiness: every
    head this side holds is one somebody REPORTED, so an unreported push is invisible to the terms
    below. Naming the armed head at the moment of the act is what makes the remote refuse a tree
    the criteria were not decided about, which is a guarantee no local comparison can offer.
    """

    satisfied: bool
    refusals: tuple[str, ...]
    target_repository: str
    pr_number: int | None
    verified_head_sha: str | None


def pr_merge_admission(
    session: Session,
    unit_id: uuid.UUID,
    landing_source: EstateLandingSource,
) -> MergeAdmission:
    """Compose the answer for one unit. Reads only; writes nothing, and never acts."""
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    return admission_for(session, unit, revision, landing_source)


def admission_for(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    landing_source: EstateLandingSource,
) -> MergeAdmission:
    """Every term, over a unit the caller already holds.

    Split from the loader above so that a caller which must hold the row -- one re-asking at the
    moment it is about to act -- evaluates the same terms over the row it locked, rather than
    re-reading a unit that could have moved in between.

    **That split is why the revision is CHECKED rather than trusted.** `required_ac_ids` derives
    the required criterion set entirely from the revision it is handed, so a caller passing the
    wrong one asks about a different set of criteria and can turn a refusal into an affirmative
    answer. Today's only caller loads the right revision; the split exists to invite a second one,
    and a precondition that holds by the good behaviour of a caller that does not exist yet is the
    correct-about-the-wrong-noun shape this module is written against.

    The envelope is normalized EXACTLY ONCE here, for the reason admission normalizes once: the
    envelope is what a human's authority approval attests, and a second normalization is a second
    reading of it.
    """
    if revision.id != unit.work_package_revision_id:
        raise DomainError(
            "revision_not_for_unit",
            "package revision does not belong to this work unit",
            "ask about the revision the unit was created from",
        )
    envelope = normalize_authority(unit.authority)
    target_repository = envelope.constraints.get("target_repository")
    target_repository = target_repository if isinstance(target_repository, str) else ""

    completed = unit.state == WorkUnitState.COMPLETED.value
    criteria_verifier_decided = verifier_decided_completion(session, revision, unit).satisfied
    capability_authorized = envelope.level_for(MERGE_CAPABILITY) == "allowed"
    authority_bound = PackageRepository(session).exact_authority_approval(unit) is not None

    nothing_open = not open_conditions(session, unit.id)

    binding = session.get(UnitPrBinding, unit.id)
    head = _head_terms(binding, unit)
    estate = _estate_terms(target_repository, landing_source)

    refusals: list[str] = []
    if not completed:
        refusals.append(WORK_UNIT_NOT_COMPLETED)
    if not criteria_verifier_decided:
        refusals.append(CRITERIA_NOT_VERIFIER_DECIDED)
    if not nothing_open:
        refusals.append(OPEN_RECONCILIATION_CONDITION)
    if not capability_authorized:
        refusals.append(MERGE_CAPABILITY_NOT_AUTHORIZED)
    if not authority_bound:
        refusals.append(AUTHORITY_APPROVAL_NOT_BOUND)
    refusals.extend(head.refusals)
    refusals.extend(estate.refusals)

    return MergeAdmission(
        satisfied=(
            completed
            and criteria_verifier_decided
            and nothing_open
            and capability_authorized
            and authority_bound
            and head.met
            and estate.met
        ),
        refusals=tuple(refusals),
        target_repository=target_repository,
        pr_number=binding.pr_number if binding is not None else None,
        verified_head_sha=binding.verification_read_head_sha if binding is not None else None,
    )


@dataclass(frozen=True)
class _Term:
    met: bool
    refusals: tuple[str, ...]


def _head_terms(binding: UnitPrBinding | None, unit: WorkUnit) -> _Term:
    """Is there a pull request, and is its head the head that was adjudicated?

    Three separate refusals rather than one, because each is a different situation with a
    different next step: no pull request at all, one whose head was never handed over for
    adjudication, and one whose head moved after it was. The last is the one that matters most --
    it is the difference between landing the tree the criteria were decided about and landing
    whatever has been pushed since.
    """
    if binding is None:
        return _Term(False, (PR_BINDING_MISSING,))
    armed = binding.verification_read_head_sha
    if armed is None:
        return _Term(False, (VERIFICATION_HEAD_UNARMED,))
    refusals: list[str] = []
    current_attempt = binding.verification_read_attempt == unit.attempt_count
    unmoved = binding.head_sha == armed
    if not current_attempt:
        refusals.append(VERIFICATION_HEAD_NOT_CURRENT_ATTEMPT)
    if not unmoved:
        refusals.append(PR_HEAD_MOVED_SINCE_VERIFICATION)
    return _Term(current_attempt and unmoved, tuple(refusals))


def _estate_terms(target_repository: str, landing_source: EstateLandingSource) -> _Term:
    """Does landing on this repository's default branch change something already serving?

    Only an explicit `inert` passes. That is structural rather than enumerated: an answer this
    build does not recognise, an absent one, and one that says the estate has not looked all fall
    through to a refusal, so a fourth value shipped on the authoring side cannot arrive here as
    permission.
    """
    if not target_repository:
        return _Term(False, (MERGE_TARGET_REPOSITORY_MISSING,))
    answer = landing_source.landing_for(target_repository)
    if answer.landing == LANDING_INERT:
        return _Term(True, ())
    if answer.landing == LANDING_REDEPLOYS:
        return _Term(False, (MERGE_TARGET_REPOSITORY_REDEPLOYS,))
    if answer.landing is not None:
        return _Term(False, (MERGE_TARGET_ESTATE_UNKNOWN,))
    if answer.reason == SOURCE_UNCONFIGURED:
        return _Term(False, (MERGE_TARGET_ESTATE_SOURCE_UNCONFIGURED,))
    return _Term(False, (MERGE_TARGET_ESTATE_SOURCE_UNREADABLE,))
