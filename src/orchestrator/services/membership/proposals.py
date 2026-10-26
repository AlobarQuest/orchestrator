"""Staging a membership proposal: a machine stages, a person confirms.

A staged row changes nothing: only a human-confirmed proposal writes the member tables. Staging
refuses what a confirm could not apply, with `validate_changes`, which the confirm runs again
against the state it finds then.
"""

import hashlib
import json
import re
import uuid
from typing import Final

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import (
    SdsMember,
    SdsMemberApp,
    SdsMemberGrant,
    SdsMemberProposal,
)
from orchestrator.services.membership.changes import MemberChange, MemberProposalBody

# "MBR1": serializes concurrent stagings of one idempotency key, so the loser replays the winner
# instead of meeting the unique constraint as an unhandled IntegrityError.
STAGING_LOCK_NAMESPACE: Final = 0x4D425231

_REPOSITORY: Final = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


def stage_member_proposal(
    session: Session,
    *,
    actor: ActorContext,
    body: MemberProposalBody,
    idempotency_key: str,
) -> SdsMemberProposal:
    """Hold a validated membership proposal for a person to confirm. SYSTEM only.

    The same key with the same body replays the row; with a different body it is a conflict.
    """
    if actor.role is not ActorRole.SYSTEM or not actor.actor_id:
        raise DomainError(
            "role_forbidden",
            "only the system actor may stage a membership proposal",
            "a person changes membership by confirming a staged proposal",
        )
    payload = body.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
        {"namespace": STAGING_LOCK_NAMESPACE, "key": idempotency_key},
    )
    existing = session.scalar(
        select(SdsMemberProposal).where(SdsMemberProposal.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.payload_digest != digest:
            raise DomainError(
                "idempotency_conflict",
                "idempotency key belongs to a different member proposal",
                "use a new idempotency key",
            )
        return existing
    _require_text(body.summary, "summary")
    validate_changes(session, body.changes)
    proposal = SdsMemberProposal(
        idempotency_key=idempotency_key,
        payload=payload,
        payload_digest=digest,
        state="staged",
        staged_by=actor.actor_id,
    )
    session.add(proposal)
    session.flush()
    return proposal


def validate_changes(session: Session, changes: list[MemberChange]) -> None:
    """Refuse a change list that could not be applied to the members as they stand now."""
    repositories = _distinct_repositories(changes)
    members = {
        member.repository.lower(): member
        for member in session.scalars(
            select(SdsMember).where(func.lower(SdsMember.repository).in_(repositories))
        )
    }
    for change in changes:
        member = members.get(change.repository.lower())
        _require_text(change.reason, "reason")
        if member is None and (change.state is None or change.owner is None):
            raise DomainError(
                "member_creation_incomplete",
                f"{change.repository} is not a member, so its change needs a state and an owner",
                "give both to enroll it",
            )
        if change.owner is not None:
            _require_text(change.owner, "owner")
        _check_working_copy(change, member)
        _check_grants(session, change, member)
    _check_app_names(session, changes, members)


def _distinct_repositories(changes: list[MemberChange]) -> set[str]:
    seen: set[str] = set()
    for change in changes:
        if not _REPOSITORY.fullmatch(change.repository):
            raise DomainError(
                "member_repository_invalid",
                f"{change.repository!r} is not an owner/name repository",
                "name it as GitHub spells it, e.g. AlobarQuest/brain",
            )
        key = change.repository.lower()
        if key in seen:
            raise DomainError(
                "member_change_duplicated",
                f"{change.repository} appears in more than one change",
                "fold the changes into one",
            )
        seen.add(key)
    return seen


def _check_app_names(
    session: Session, changes: list[MemberChange], members: dict[str, SdsMember]
) -> None:
    """An app name is unique across members. A member replacing its app list frees its old names."""
    names: set[str] = set()
    replacing: set[uuid.UUID] = set()
    for change in changes:
        if change.apps is None:
            continue
        member = members.get(change.repository.lower())
        if member is not None:
            replacing.add(member.id)
        for app in change.apps:
            if app.name in names:
                raise _app_taken(app.name)
            names.add(app.name)
    if not names:
        return
    rows = session.execute(
        select(SdsMemberApp.name, SdsMemberApp.member_id).where(SdsMemberApp.name.in_(names))
    )
    for name, member_id in rows:
        if member_id not in replacing:
            raise _app_taken(name)


def _check_working_copy(change: MemberChange, member: SdsMember | None) -> None:
    machine_local = change.machine_local
    if machine_local is None:
        machine_local = member.machine_local if member is not None else False
    working_copy = change.working_copy
    if working_copy is None and member is not None:
        working_copy = member.working_copy
    if machine_local and not (working_copy and working_copy.strip()):
        raise DomainError(
            "member_working_copy_required",
            f"{change.repository} is machine-local but would have no working copy",
            "name the working copy",
        )


def _check_grants(session: Session, change: MemberChange, member: SdsMember | None) -> None:
    for service, grant in change.grants.items():
        _require_text(grant.reason, f"{service} grant reason")
        if grant.state != "withdrawn":
            continue
        current = (
            session.scalar(
                select(SdsMemberGrant.state).where(
                    SdsMemberGrant.member_id == member.id, SdsMemberGrant.service == service
                )
            )
            if member is not None
            else None
        )
        if current != "granted":
            raise DomainError(
                "grant_withdrawal_invalid",
                f"{change.repository} has no granted {service} to withdraw",
                "only a granted service can be withdrawn",
            )


def _require_text(value: str, label: str) -> None:
    if not value.strip():
        raise DomainError("member_text_blank", f"{label} must not be blank", f"say what {label} is")


def _app_taken(name: str) -> DomainError:
    return DomainError(
        "member_app_name_taken",
        f"app name {name!r} is already used by another member",
        "app names are unique across members",
    )
