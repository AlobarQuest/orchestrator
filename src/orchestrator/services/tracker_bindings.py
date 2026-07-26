"""Canonical unit -> external tracker-item bindings (projection only).

Writing a binding records only THAT a unit is mirrored onto an external tracker item. It is
never a lifecycle action: it does not transition the unit and carries no authority. Only the
SYSTEM actor (the projection adapter, or an operator repair) may write.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import TRACKER_SYSTEMS, UnitTrackerBinding, WorkUnit
from orchestrator.services.lifecycle import ActorContext


def _authorize_write(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the system actor may write a tracker binding",
            None,
        )


def _validate(tracker_system: str, external_item_id: str) -> None:
    if tracker_system not in TRACKER_SYSTEMS:
        raise DomainError(
            "tracker_system_unsupported",
            f"tracker_system must be one of {TRACKER_SYSTEMS!r}",
            None,
        )
    if not external_item_id:
        raise DomainError(
            "tracker_item_id_required",
            "external_item_id must be non-empty",
            None,
        )


def _require_unit(session: Session, work_unit_id: uuid.UUID) -> WorkUnit:
    unit = session.get(WorkUnit, work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return unit


def upsert_tracker_binding(
    session: Session,
    *,
    actor: ActorContext,
    work_unit_id: uuid.UUID,
    tracker_system: str,
    external_item_id: str,
    external_url: str | None,
    projected_state: str,
) -> UnitTrackerBinding:
    """Record (or update) the unit's current tracker-item projection. Never touches unit state.

    Every validation here fails closed with a named `DomainError` before touching the row, so an
    unsupported `tracker_system` or an empty `external_item_id` never reaches the DB CHECK
    constraints on `unit_tracker_bindings` -- those exist as a second line of defence, not the
    first.
    """
    _authorize_write(actor)
    _validate(tracker_system, external_item_id)
    _require_unit(session, work_unit_id)
    now = TransactionClock().now(session)
    binding = get_tracker_binding(session, work_unit_id)  # taken FOR UPDATE, see below
    if binding is None:
        binding = UnitTrackerBinding(
            work_unit_id=work_unit_id,
            tracker_system=tracker_system,
            external_item_id=external_item_id,
            external_url=external_url,
            projected_state=projected_state,
            updated_at=now,
        )
        session.add(binding)
    else:
        binding.tracker_system = tracker_system
        binding.external_item_id = external_item_id
        binding.external_url = external_url
        binding.projected_state = projected_state
        binding.updated_at = now
    # This is a request entry point, so it owns its transaction and must COMMIT -- a flush alone
    # makes the response look right (the ORM hands back the instance it holds) while the row is
    # discarded when the session closes.
    session.commit()
    return binding


def get_tracker_binding(session: Session, work_unit_id: uuid.UUID) -> UnitTrackerBinding | None:
    """The unit's current binding, taken FOR UPDATE.

    Mirrors `pr_bindings._locked_binding`: there is exactly one row per unit by construction
    (PK on `work_unit_id`), so a concurrent duplicate write is serialized by this lock rather
    than by an idempotency key -- the second writer blocks, then observes the first writer's row
    and updates it instead of racing it on INSERT.
    """
    return session.scalar(
        select(UnitTrackerBinding)
        .where(UnitTrackerBinding.work_unit_id == work_unit_id)
        .with_for_update()
    )


def list_tracker_bindings(
    session: Session, *, tracker_system: str | None = None
) -> list[UnitTrackerBinding]:
    stmt = select(UnitTrackerBinding)
    if tracker_system is not None:
        stmt = stmt.where(UnitTrackerBinding.tracker_system == tracker_system)
    return list(session.scalars(stmt))
