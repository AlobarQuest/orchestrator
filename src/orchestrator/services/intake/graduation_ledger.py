"""What happened the last N times a comparable envelope cleared the human gate (WS-P2.18 Inc 8).

Increment 3 built the mechanism that lifts the human authority requirement for a **declared**
known-good pattern (ADR-0011). Nothing told Devon which patterns are worth declaring. This module
is that evidence, rendered where the decision is actually made: on the unit page, beside the
authority-approval form, at the moment he is being asked to clear one more envelope of a shape he
has cleared before.

**It reports. It does not graduate.** Nothing here writes a row, withholds a refusal or touches
the policy artifact -- this module does not even know its name, which
``test_no_second_copy_of_the_artifact_values_exists_in_the_source_tree`` enforces by insisting the
artifact have exactly one reader. Declaring a pattern stays a deliberate human edit to that
document, because that judgement is the one this whole workstream exists to preserve; a ledger
that auto-declared would move the trust root without anyone deciding to.

**It never reads who approved (ADR-0014).** `/review` reads the actor from the forward-auth
header, so a construction-era gate an agent drove is recorded as Devon. All 35 authority approvals
on record carry one identity and none is a rejection, so the column carries no information -- and
a cutoff date laid down while the hole is still open would certify future rows as attributable
when they are not. The standing ruling makes this cheap: an approval is the **index** (this
envelope shape was cleared, at this time), not the evidence. What follows the clearing is the
evidence, and that is a fact about the envelope rather than about the approver.

**An outcome is what this system recorded happening to the work, not whether it eventually got
done.** Reaching `completed` is the tempting definition and it is the wrong one: it scores 30 of
this estate's 43 units successful, including one that completed on its eighth of eight attempts
after seven failures. Adjudications are worse -- 67 of 68 on record are `passed`, which is uniform
and therefore measures nothing. So a clearing is scored on three adverse signals that are facts in
the database, and the recorded reasons are reported verbatim and **unclassified**: deciding which
of them the gate could plausibly have caught is a judgement, and it is Devon's, not this module's.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import AuthorityEnvelope, normalize_authority
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Event,
    ReconciliationCondition,
    WorkUnit,
)

# How many prior clearings are shown one by one. The counts above them are over the WHOLE
# comparable population, so the window shortens the reading rather than the evidence -- a recent
# run of clean outcomes cannot hide an older run of bad ones behind a limit.
LEDGER_WINDOW = 10

# The four verdicts. Separate constants rather than a collection: nothing tests membership in
# them, and a module-level string collection here would be a vocabulary claiming an agreement with
# a producer that does not exist.
CLEAN = "clean"
RECOVERED = "recovered"
ABANDONED = "abandoned"
UNFINISHED = "unfinished"

_TRANSITIONED = "work_unit.transitioned"
_WAIVED = "waived"

# Deliberately says nothing that a growing population would make false -- no size, no date range.
# The size and the repository spread are rendered beside it, computed; a caveat that quoted them
# would be a second copy going stale in the one place a reader trusts to be careful.
_CAVEAT = (
    "These are counts of what happened, not a rate. The outcomes are not independent -- they are "
    "one estate's work, over the repositories listed above. Who performed each approval is not "
    "recorded in a usable form (ADR-0014), so nothing here is evidence that anyone scrutinised "
    "anything; it is evidence about the envelope shape only."
)


@dataclass(frozen=True)
class ClearedEnvelope:
    """One prior clearing of a comparable envelope, and what followed it.

    `recorded_reasons` are the reasons this system stored on the unit's failing and cancelling
    transitions, verbatim and in order. They are the informative part -- one of them reads
    "obsolete allowed_commands", which names an envelope defect outright -- and they are
    deliberately not mapped onto a judgement about what the gate could have caught.
    """

    unit_key: str
    target_repository: str | None
    cleared_at: datetime
    verdict: str
    failed_attempts: int
    reconciliation_conditions: int
    waived_criteria: int
    recorded_reasons: tuple[str, ...]


@dataclass(frozen=True)
class GraduationLedger:
    """The evidence for declaring this envelope's shape known-good, or for not declaring it.

    `total` counts every comparable prior clearing; `recent` details the most recent
    `LEDGER_WINDOW` of them. `repositories` is the spread the counts are drawn from, because a
    perfect record confined to one repository is a different claim from the same record across
    six.
    """

    change_class: str | None
    capabilities: Mapping[str, str]
    total: int
    clean: int
    recovered: int
    abandoned: int
    unfinished: int
    repositories: tuple[str, ...]
    recent: tuple[ClearedEnvelope, ...]
    caveat: str


def graduation_ledger(session: Session, unit: WorkUnit) -> GraduationLedger:
    """How comparable envelopes have gone, for the human being asked to clear this one.

    Comparable means **one known-good pattern could cover both**: the same change class and the
    same capability map. Those are the two fields Increment 3's matcher decides identity by --
    budgets, repositories and command prefixes are bounds a pattern *widens* to, so splitting the
    population on them would hide exactly the spread Devon needs in order to choose those bounds.
    They are reported instead.
    """
    envelope = normalize_authority(unit.authority)
    cleared = _comparable_clearings(session, unit.id, envelope)
    # No early return for the empty case: `in_(())` is an empty result, not an error, so the one
    # path builds an honest empty report. A shape with no history is the novel case R2 wants
    # gated, and saying so is the answer rather than the absence of one.
    unit_ids = [candidate.id for candidate, _, _ in cleared]
    failures = _counted(session, _failure_counts(unit_ids))
    conditions = _counted(session, _condition_counts(unit_ids))
    waivers = _counted(session, _waiver_counts(unit_ids))
    reasons = _recorded_reasons(session, unit_ids)
    entries = tuple(
        ClearedEnvelope(
            unit_key=candidate.unit_key,
            target_repository=_target_repository(candidate_envelope),
            cleared_at=cleared_at,
            verdict=_verdict(
                candidate.state,
                failures.get(candidate.id, 0)
                + conditions.get(candidate.id, 0)
                + waivers.get(candidate.id, 0),
            ),
            failed_attempts=failures.get(candidate.id, 0),
            reconciliation_conditions=conditions.get(candidate.id, 0),
            waived_criteria=waivers.get(candidate.id, 0),
            recorded_reasons=reasons.get(candidate.id, ()),
        )
        for candidate, cleared_at, candidate_envelope in cleared
    )
    return GraduationLedger(
        change_class=envelope.change_class,
        capabilities=dict(sorted(envelope.capabilities.items())),
        total=len(entries),
        clean=sum(1 for entry in entries if entry.verdict == CLEAN),
        recovered=sum(1 for entry in entries if entry.verdict == RECOVERED),
        abandoned=sum(1 for entry in entries if entry.verdict == ABANDONED),
        unfinished=sum(1 for entry in entries if entry.verdict == UNFINISHED),
        repositories=tuple(
            sorted({entry.target_repository for entry in entries if entry.target_repository})
        ),
        recent=entries[:LEDGER_WINDOW],
        caveat=_CAVEAT,
    )


def _comparable_clearings(
    session: Session, unit_id: uuid.UUID, envelope: AuthorityEnvelope
) -> list[tuple[WorkUnit, datetime, AuthorityEnvelope]]:
    """Every other unit whose gate a human cleared for a comparable envelope, newest first.

    Every candidate is loaded and compared in Python through `normalize_authority`, which is the
    same reading Increment 3's matcher uses -- comparing raw JSONB would let a stored envelope and
    an authored one that normalize identically fall into different groups. The scan is bounded by
    the number of authority approvals ever recorded, which is 35 after four weeks of operation, so
    there is nothing here to cap; a cap would silently shrink the counts instead.
    """
    rows = session.execute(
        select(WorkUnit, Approval.created_at)
        .join(
            Approval,
            (Approval.subject_id == WorkUnit.id)
            & (Approval.subject_revision_or_fingerprint == WorkUnit.authority_fingerprint),
        )
        .where(
            Approval.subject_type == "authority",
            Approval.decision == "approved",
            WorkUnit.id != unit_id,
        )
        # Two clearings recorded in the same instant have no natural order, and a report whose
        # rows shuffle between reads is one a person cannot compare against what they saw last
        # time. The tiebreak is the unit key, then its id, so the ordering is total.
        .order_by(Approval.created_at.desc(), WorkUnit.unit_key, WorkUnit.id)
    ).all()
    seen: set[uuid.UUID] = set()
    comparable: list[tuple[WorkUnit, datetime, AuthorityEnvelope]] = []
    for candidate, cleared_at in rows:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        candidate_envelope = normalize_authority(candidate.authority)
        if _comparable(candidate_envelope, envelope):
            comparable.append((candidate, cleared_at, candidate_envelope))
    return comparable


def _comparable(candidate: AuthorityEnvelope, envelope: AuthorityEnvelope) -> bool:
    return (
        candidate.change_class == envelope.change_class
        and candidate.capabilities == envelope.capabilities
    )


def _verdict(state: str, adverse: int) -> str:
    """What became of the work this clearing let through.

    `unfinished` exists because a clearing whose work is still running has no outcome yet and must
    not be quietly dropped from the denominator -- every unit in the estate happens to be terminal
    today, and a ledger that silently excluded the in-flight ones would flatter itself the first
    time one was not.
    """
    if state == WorkUnitState.COMPLETED:
        return CLEAN if adverse == 0 else RECOVERED
    if state in (WorkUnitState.FAILED, WorkUnitState.CANCELLED):
        return ABANDONED
    return UNFINISHED


def _target_repository(envelope: AuthorityEnvelope) -> str | None:
    value = envelope.constraints.get("target_repository")
    return value if isinstance(value, str) and value else None


def _counted(session: Session, statement: Select[tuple[uuid.UUID, int]]) -> dict[uuid.UUID, int]:
    return {subject_id: count for subject_id, count in session.execute(statement).all()}


def _failure_counts(unit_ids: Sequence[uuid.UUID]) -> Select[tuple[uuid.UUID, int]]:
    return (
        select(Event.subject_id, func.count())
        .where(
            Event.action == _TRANSITIONED,
            Event.to_state == WorkUnitState.FAILED,
            Event.subject_id.in_(unit_ids),
        )
        .group_by(Event.subject_id)
    )


def _condition_counts(unit_ids: Sequence[uuid.UUID]) -> Select[tuple[uuid.UUID, int]]:
    return (
        select(ReconciliationCondition.work_unit_id, func.count())
        .where(ReconciliationCondition.work_unit_id.in_(unit_ids))
        .group_by(ReconciliationCondition.work_unit_id)
    )


def _waiver_counts(unit_ids: Sequence[uuid.UUID]) -> Select[tuple[uuid.UUID, int]]:
    return (
        select(Adjudication.work_unit_id, func.count())
        .where(
            Adjudication.outcome == _WAIVED,
            Adjudication.work_unit_id.in_(unit_ids),
        )
        .group_by(Adjudication.work_unit_id)
    )


def _recorded_reasons(
    session: Session, unit_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, ...]]:
    """The reasons stored on failing and cancelling transitions, in order, deduplicated.

    Cancellation is included because it is where the most informative reason in the whole
    population lives: four units were retired with "obsolete allowed_commands", which is an
    envelope defect stated in words, on a transition that carries no failure at all.
    """
    rows = session.execute(
        select(Event.subject_id, Event.payload)
        .where(
            Event.action == _TRANSITIONED,
            Event.to_state.in_((WorkUnitState.FAILED, WorkUnitState.CANCELLED)),
            Event.subject_id.in_(unit_ids),
        )
        .order_by(Event.occurred_at)
    ).all()
    collected: dict[uuid.UUID, list[str]] = {}
    for subject_id, payload in rows:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        if not isinstance(reason, str) or not reason:
            continue
        seen = collected.setdefault(subject_id, [])
        if reason not in seen:
            seen.append(reason)
    return {subject_id: tuple(reasons) for subject_id, reasons in collected.items()}
