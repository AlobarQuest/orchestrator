"""Package-declared follow-up scheduling (WS-P2.8).

The intent package declares WHETHER an outcome should be revisited; this module owns the
orchestrator's side of that contract. `revisit_when` and `signals` are prose written for a human
and are never parsed -- the timing comes from configuration, not from the text.

The four field names mirror the intent-packages schema exactly. A fifth key is a validation
error rather than an ignored extra, because a silently-dropped key is how a declaration and its
reader drift apart.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import authority_fingerprint, normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkPackageRevision, WorkUnit
from orchestrator.services.lifecycle import FOLLOW_UP_CAPABILITY, ActorContext

# The intent-packages `follow_up` block, mirrored field for field. Every key is mandatory-present;
# `revisit_when` and `owner` may be null. Registered in the cross-boundary vocabulary registry.
FOLLOW_UP_FIELDS = ("required", "revisit_when", "signals", "owner")


def _invalid(detail: str) -> DomainError:
    return DomainError(
        "follow_up_invalid",
        f"package follow_up declaration is invalid: {detail}",
        "correct the package follow_up block and re-emit the intake payload",
    )


def validate_follow_up(value: object) -> dict[str, Any] | None:
    """Return the normalized declaration, or None when the package carried none."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("it must be a mapping")
    unknown = sorted(set(value) - set(FOLLOW_UP_FIELDS))
    if unknown:
        raise _invalid(f"unknown key {unknown[0]!r}")
    missing = [field for field in FOLLOW_UP_FIELDS if field not in value]
    if missing:
        raise _invalid(f"missing required key {missing[0]!r}")
    if not isinstance(value["required"], bool):
        raise _invalid("`required` must be a boolean")
    for field in ("revisit_when", "owner"):
        if value[field] is not None and not isinstance(value[field], str):
            raise _invalid(f"`{field}` must be a string or null")
    signals = value["signals"]
    if not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
        raise _invalid("`signals` must be a list of strings")
    return {
        "required": value["required"],
        "revisit_when": value["revisit_when"],
        "signals": list(signals),
        "owner": value["owner"],
    }


# The capability a follow-up review unit requires -- imported from `services.lifecycle` (the
# single source of truth also consulted by `lifecycle`'s own authorization predicates and by
# `services.verifier_criteria` / `services.evidence`) rather than defined here a second time.
# Re-exported under this name because external code (tests included) already imports it as
# `orchestrator.services.follow_ups.FOLLOW_UP_CAPABILITY`. Registered in
# ORCHESTRATOR_ONLY_CAPABILITIES, never in the runner vocabulary: no runner works one of these, and
# the byte-pinned cross-repo envelope fixture stays untouched.

# Why a revision was passed over. Individual constants rather than a collection: a module-level
# tuple of strings used in a membership test becomes a discovered subject of the cross-boundary
# vocabulary detector, and these are internal policy, not a contract with another repo.
SKIP_NOT_REQUIRED = "not_required"
SKIP_NO_COMPLETED_UNIT = "no_completed_unit"
SKIP_UNITS_IN_FLIGHT = "units_in_flight"
SKIP_UNSETTLED_FAILED_UNIT = "unsettled_failed_unit"
SKIP_NOT_YET_DUE = "not_yet_due"
SKIP_ALREADY_MINTED = "already_minted"
SKIP_DECLARATION_MALFORMED = "declaration_malformed"

_COMPLETED = "completed"
_CANCELLED = "cancelled"
_FAILED = "failed"


@dataclass(frozen=True)
class UnitFacts:
    """One work unit, reduced to what due-ness depends on.

    `settled_at` is when the unit ENTERED its settled state, read from the event ledger -- never
    from `work_units.updated_at`, which a trigger rewrites on every write and which therefore
    cannot be back-dated or trusted as a state-entry time.
    """

    required_capability: str
    state: str
    settled_at: datetime | None


@dataclass(frozen=True)
class RevisionFacts:
    revision_id: uuid.UUID
    follow_up: dict[str, object] | None
    units: tuple[UnitFacts, ...]


@dataclass(frozen=True)
class DueDecision:
    revision_id: uuid.UUID
    due_at: datetime | None
    skip_reason: str | None


def evaluate_due(facts: RevisionFacts, *, now: datetime, due_after_days: int) -> DueDecision:
    """Decide whether a revision's declared follow-up review is due. Pure: no I/O, no clock.

    A revision qualifies when its declaration asks for one, its work actually shipped (at least
    one unit completed) and has stopped moving, the window has elapsed since the last unit
    settled, and no review unit exists yet.

    FAILED deliberately blocks. It is not a terminal state -- a failed unit can return to READY
    or be retired -- so the package's outcome is not yet knowable and there is nothing to
    schedule a revisit of. It gets its OWN skip reason rather than being folded into
    `units_in_flight`, because "still working" and "stopped, and nobody decided" call for
    different operator actions.
    """
    # One review unit per revision, forever. DERIVED from the units rather than taken as a
    # separate flag: a flag plus a filter is two mechanisms for one rule, and the filter is
    # unreachable whenever the flag is computed from these same units -- untestable protection,
    # which this repo treats as a defect rather than as depth.
    if any(unit.required_capability == FOLLOW_UP_CAPABILITY for unit in facts.units):
        return DueDecision(facts.revision_id, None, SKIP_ALREADY_MINTED)
    declaration = facts.follow_up
    if not isinstance(declaration, dict) or declaration.get("required") is not True:
        return DueDecision(facts.revision_id, None, SKIP_NOT_REQUIRED)

    subjects = facts.units
    if any(unit.state == _FAILED for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_UNSETTLED_FAILED_UNIT)
    if any(unit.state not in (_COMPLETED, _CANCELLED) for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_UNITS_IN_FLIGHT)
    if not any(unit.state == _COMPLETED for unit in subjects):
        return DueDecision(facts.revision_id, None, SKIP_NO_COMPLETED_UNIT)

    settled = [unit.settled_at for unit in subjects if unit.settled_at is not None]
    if not settled:
        return DueDecision(facts.revision_id, None, SKIP_UNITS_IN_FLIGHT)
    due_at = max(settled) + timedelta(days=due_after_days)
    if now < due_at:
        return DueDecision(facts.revision_id, due_at, SKIP_NOT_YET_DUE)
    return DueDecision(facts.revision_id, due_at, None)


_SETTLED_STATES = (WorkUnitState.COMPLETED, WorkUnitState.CANCELLED)
_MINT_ACTION = "follow_up_unit.created"
_DEFAULT_REVISIT = "No revisit condition was declared; confirm whether this outcome still holds."


@dataclass(frozen=True)
class MintedFollowUp:
    work_unit_id: uuid.UUID
    work_package_revision_id: uuid.UUID
    due_at: datetime


@dataclass(frozen=True)
class SkippedRevision:
    work_package_revision_id: uuid.UUID
    reason: str


@dataclass(frozen=True)
class MintResult:
    minted: tuple[MintedFollowUp, ...]
    skipped: tuple[SkippedRevision, ...]
    considered: int


def _authorize_actor(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may mint follow-up reviews",
            None,
        )


def follow_up_unit_id(revision_id: uuid.UUID) -> uuid.UUID:
    """Content-addressed, so a second pass cannot create a second row.

    This is the structural half of the idempotency story; the already-minted skip is the
    reporting half. The unique constraint on (work_package_revision_id, unit_key) is the backstop
    if both are somehow bypassed.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"sds:follow-up:{revision_id}")


def _describe(declaration: dict[str, Any]) -> str:
    revisit = declaration.get("revisit_when") or _DEFAULT_REVISIT
    lines = [f"Revisit: {revisit}"]
    signals = declaration.get("signals") or []
    if signals:
        lines.append("Signals:")
        lines.extend(f"- {signal}" for signal in signals)
    owner = declaration.get("owner")
    if owner:
        lines.append(f"Owner: {owner}")
    return "\n".join(lines)


def _revision_facts(session: Session, revision: WorkPackageRevision) -> RevisionFacts:
    rows = session.execute(
        select(WorkUnit.id, WorkUnit.required_capability, WorkUnit.state).where(
            WorkUnit.work_package_revision_id == revision.id
        )
    ).all()
    units = []
    for unit_id, capability, state in rows:
        settled_at = None
        if state in _SETTLED_STATES:
            settled_at = session.scalar(
                select(func.max(Event.occurred_at)).where(
                    Event.subject_type == "work_unit",
                    Event.subject_id == unit_id,
                    Event.to_state == state,
                )
            )
        units.append(UnitFacts(capability, state, settled_at))
    # No already-minted flag: evaluate_due derives that from the units themselves, so there is
    # exactly one mechanism and this function cannot contradict it.
    return RevisionFacts(revision.id, revision.follow_up, tuple(units))


def _mint(
    session: Session,
    revision: WorkPackageRevision,
    declaration: dict[str, Any],
    actor: ActorContext,
    now: datetime,
) -> WorkUnit:
    authority = normalize_authority(
        {
            "capabilities": {FOLLOW_UP_CAPABILITY: "allowed"},
            "budgets": {"max_attempts": 1},
        }
    )
    unit = WorkUnit(
        id=follow_up_unit_id(revision.id),
        work_package_revision_id=revision.id,
        unit_key=f"follow-up:{revision.id}",
        title="Follow-up review",
        outcome=_describe(declaration),
        state=WorkUnitState.AWAITING_REVIEW,
        # The system self-attests: ck_work_units_approved_beyond_draft requires both approval
        # columns for any state other than draft, and this unit had no decomposition.
        decomposition_approved_by=actor.actor_id,
        decomposition_approved_at=now,
        required_capability=FOLLOW_UP_CAPABILITY,
        authority=authority.normalized(),
        authority_fingerprint=authority_fingerprint(authority),
        max_attempts=1,
    )
    session.add(unit)
    session.flush()
    session.add(
        Event(
            subject_type="work_unit",
            subject_id=unit.id,
            action=_MINT_ACTION,
            to_state=WorkUnitState.AWAITING_REVIEW,
            actor_id=actor.actor_id,
            correlation_id=uuid.uuid4(),
            idempotency_key=f"{_MINT_ACTION}:{unit.id}",
            payload={"command": {"work_package_revision_id": str(revision.id)}},
        )
    )
    session.flush()
    return unit


def _process_revision(
    session: Session,
    revision: WorkPackageRevision,
    actor: ActorContext,
    now: datetime,
    due_after_days: int,
) -> MintedFollowUp | SkippedRevision | None:
    """One revision, isolated in its own SAVEPOINT.

    `evaluate_due` owns the not-required rule (a null declaration and a `required: false` one are
    both `SKIP_NOT_REQUIRED`) -- there is no second check of `declaration["required"]` here. Two
    checks of one rule is the same defect Task 3 removed from `evaluate_due` itself (a flag plus a
    filter), just one function over: the second check would make `evaluate_due`'s own
    `SKIP_NOT_REQUIRED` branch unreachable from this, its only production caller.

    The two exceptions caught here are the two failure modes this module's own code anticipates,
    named deliberately and no wider:
      - `DomainError` -- `validate_follow_up` rejecting a malformed declaration.
      - `IntegrityError` -- the `work_units (work_package_revision_id, unit_key)` unique
        constraint, the backstop `follow_up_unit_id`'s docstring names for the case where the
        deterministic id and the already-minted check are somehow both bypassed.
    A SAVEPOINT is required to recover from the second case: once PostgreSQL aborts a transaction
    on a constraint violation, no further statement succeeds until something rolls back to a
    savepoint taken before the failing statement. Anything else -- a genuine programming error --
    is deliberately left uncaught: swallowing it as "this revision was unusable" would hide a bug
    behind a skip reason instead of failing the pass loudly.
    """
    try:
        with session.begin_nested():
            declaration = validate_follow_up(revision.follow_up)
            decision = evaluate_due(
                _revision_facts(session, revision), now=now, due_after_days=due_after_days
            )
            if decision.skip_reason == SKIP_NOT_REQUIRED:
                # Flooding the response with every revision that never asked for a follow-up
                # would swamp the genuinely actionable skips.
                return None
            if decision.skip_reason is not None:
                return SkippedRevision(revision.id, decision.skip_reason)
            assert declaration is not None and decision.due_at is not None
            unit = _mint(session, revision, declaration, actor, now)
            return MintedFollowUp(unit.id, revision.id, decision.due_at)
    except DomainError:
        return SkippedRevision(revision.id, SKIP_DECLARATION_MALFORMED)
    except IntegrityError:
        return SkippedRevision(revision.id, SKIP_ALREADY_MINTED)


def mint_due_follow_ups(
    session: Session,
    *,
    actor: ActorContext,
    due_after_days: int,
) -> MintResult:
    """One pass over approved revisions, minting whatever is due. Externally invoked; nothing
    loops and nothing schedules itself.

    Per-item fail-open with a counted skip: each revision runs inside its own SAVEPOINT
    (`_process_revision`), so a `DomainError` or `IntegrityError` on one revision rolls back only
    that revision's own attempt -- never the units already minted earlier in the pass, and never
    the ones still to come. This function still issues exactly one `session.commit()`, at the very
    end, for the whole pass; the savepoints are what make surviving a mid-pass failure safe.
    """
    _authorize_actor(actor)
    now = TransactionClock().now(session)
    revisions = session.scalars(
        select(WorkPackageRevision).order_by(
            WorkPackageRevision.registered_at, WorkPackageRevision.id
        )
    ).all()
    minted: list[MintedFollowUp] = []
    skipped: list[SkippedRevision] = []
    for revision in revisions:
        outcome = _process_revision(session, revision, actor, now, due_after_days)
        if isinstance(outcome, MintedFollowUp):
            minted.append(outcome)
        elif isinstance(outcome, SkippedRevision):
            skipped.append(outcome)
    session.commit()
    return MintResult(tuple(minted), tuple(skipped), len(revisions))
