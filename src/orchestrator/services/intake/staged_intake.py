"""Staged package intakes: a machine stages, a person confirms (ADR-0006 amendment 1).

Intake used to be a paste. The CLI emitted a verified payload, Devon copied it into
`/review/intakes/new`, and the page that registered it showed the decision facts only AFTER the
registration they were meant to inform. Staging puts the decision first. The CLI posts the payload
here as SYSTEM, `/review/staged-intakes/{id}` renders what it does, what it affects and whether it
can be backed out, and one button registers it.

**A staged row does nothing.** It is not a revision, it is not on any lane's read path, and no
machine can move it on: `confirm_staged_intake` admits only a HUMAN actor, and no standing HUMAN
credential exists (ADR-0006), so registration still needs a live browser session. What changed is
who types, not who decides.

**The confirm is the same registration the paste performs.** The caller builds the
`PackageIntakeCommand` from the stored payload through the same `PackageIntakeRegistration` model
and projection the API route uses, and this module hands it to `register_package_intake`
unchanged. The staged idempotency key IS the registration's key, so a double-click replays one
registration rather than attempting two.
"""

import uuid
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import StagedPackageIntake, WorkPackageRevision
from orchestrator.services.intake.package_intake import (
    PackageIntakeCommand,
    preflight_package_intake,
    register_package_intake,
)

# "STG1": serializes concurrent stagings of one idempotency key, so the loser replays the winner
# instead of meeting the unique constraint as an unhandled IntegrityError (a bare 500).
STAGING_LOCK_NAMESPACE: Final = 0x53544731

STAGED: Final = "staged"
REGISTERED: Final = "registered"
WITHDRAWN: Final = "withdrawn"


def stage_package_intake(
    session: Session,
    payload: dict[str, Any],
    command: PackageIntakeCommand,
    actor: ActorContext,
) -> StagedPackageIntake:
    """Hold a validated intake payload for a person to confirm. SYSTEM only.

    The payload is the `PackageIntakeRegistration` the route already validated, dumped as JSON
    with unset fields left out, so a confirm rebuilds exactly the model the caller sent. Its
    `idempotency_key` keys the staging AND the eventual registration. Staging the same key again
    with the same payload replays the row; with a different payload it is a conflict.

    `command` is the same payload projected the way the confirm will project it. A new row is
    staged only if `preflight_package_intake` passes it, so a payload the registration would
    refuse is refused here, with the registration's own code, instead of after the click.

    HUMAN is refused too. A person has the paste form and the confirm button; letting a browser
    session stage would make a second, unattributed way in rather than a better one.
    """
    if actor.role is not ActorRole.SYSTEM or not actor.actor_id:
        raise DomainError(
            "role_forbidden",
            "only the system actor may stage an intake",
            "a person registers an intake from /review/intakes/new",
        )
    # `CommandBase` requires a non-empty key, so a validated payload always carries one.
    idempotency_key: str = payload["idempotency_key"]
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
        {"namespace": STAGING_LOCK_NAMESPACE, "key": idempotency_key},
    )
    existing = session.scalar(
        select(StagedPackageIntake).where(StagedPackageIntake.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.payload != payload:
            raise DomainError(
                "idempotency_conflict",
                "idempotency key belongs to a different staged intake",
                "use a new idempotency key",
            )
        return existing
    preflight_package_intake(session, command)
    staged = StagedPackageIntake(
        payload=payload,
        idempotency_key=idempotency_key,
        staged_by=actor.actor_id,
        state=STAGED,
    )
    session.add(staged)
    session.flush()
    return staged


def staged_intake(session: Session, staged_id: uuid.UUID) -> StagedPackageIntake:
    """The staged row, or `staged_intake_not_found` (a 404) when there is none."""
    staged = session.get(StagedPackageIntake, staged_id)
    if staged is None:
        raise DomainError("staged_intake_not_found", "staged intake does not exist", None)
    return staged


def confirm_staged_intake(
    session: Session,
    staged_id: uuid.UUID,
    command: PackageIntakeCommand,
    actor: ActorContext,
) -> WorkPackageRevision:
    """Register the staged intake as the confirming HUMAN, once.

    Locks the row first, then `register_package_intake` takes the package lock; this is the only
    path that holds both, so the order cannot invert. A row already registered returns its
    revision, which is what a second click or a reload-and-resubmit sees. `command` must carry the
    staged key: it is built by the caller from this row's payload, and a command keyed otherwise
    would register under a key the row never committed to.

    The staged id is recorded on the intake event beside the command, so the audit trail shows the
    registration came from staging and which row it was.
    """
    if actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "human_actor_required",
            "a staged intake is confirmed by a person",
            "confirm it from /review/staged-intakes/{id}",
        )
    staged = _locked(session, staged_id)
    if command.idempotency_key != staged.idempotency_key:
        raise DomainError(
            "idempotency_conflict",
            "a staged intake is registered under its own idempotency key",
            "reload the staged intake",
        )
    if staged.state == REGISTERED:
        revision = session.get(WorkPackageRevision, staged.registered_revision_id)
        if revision is None:
            raise DomainError("event_invalid", "staged intake names no revision", None)
        return revision
    if staged.state != STAGED:
        raise DomainError(
            "staged_intake_not_confirmable",
            f"a {staged.state} staged intake cannot be confirmed",
            None,
        )
    revision = register_package_intake(session, command, actor, staged_intake_id=staged.id)
    staged.state = REGISTERED
    staged.registered_revision_id = revision.id
    session.flush()
    return revision


def withdraw_staged_intake(
    session: Session, staged_id: uuid.UUID, reason: str, actor: ActorContext
) -> StagedPackageIntake:
    """Retire a staged row a person has decided not to register, with their reason.

    For a row that went stale between staging and the confirm, or should never have been staged.
    A withdrawn row leaves the review queue and refuses the confirm. Withdrawing a withdrawn row
    returns it unchanged, so a second press is harmless; a registered row cannot be withdrawn,
    since its revision exists.
    """
    if actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "human_actor_required",
            "a staged intake is withdrawn by a person",
            "withdraw it from /review/staged-intakes/{id}",
        )
    reason = reason.strip()
    if not reason:
        raise DomainError(
            "staged_intake_withdrawal_reason_required",
            "withdrawing a staged intake needs a reason",
            "say why it should not be registered",
        )
    staged = _locked(session, staged_id)
    if staged.state == WITHDRAWN:
        return staged
    if staged.state != STAGED:
        raise DomainError(
            "staged_intake_not_withdrawable",
            f"a {staged.state} staged intake cannot be withdrawn",
            None,
        )
    staged.state = WITHDRAWN
    staged.withdrawn_by = actor.actor_id
    staged.withdrawn_at = TransactionClock().now(session)
    staged.withdrawal_reason = reason
    session.flush()
    return staged


def _locked(session: Session, staged_id: uuid.UUID) -> StagedPackageIntake:
    """The row under FOR UPDATE, re-read even if the session already holds it.

    `populate_existing`: the caller has usually loaded this row already, and a FOR UPDATE select
    does not overwrite a loaded object, so without it a press that waited on the lock would still
    read `staged` and act a second time.
    """
    staged = session.scalar(
        select(StagedPackageIntake)
        .where(StagedPackageIntake.id == staged_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if staged is None:
        raise DomainError("staged_intake_not_found", "staged intake does not exist", None)
    return staged
