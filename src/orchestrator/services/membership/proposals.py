"""Staging a membership proposal: a machine stages, a person confirms.

A staged row changes nothing: only a human-confirmed proposal writes the member tables. Staging
refuses what a confirm could not apply, with `validate_changes`, which the confirm runs again
against the state it finds then.
"""

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import (
    MEMBER_SERVICES,
    Event,
    SdsMember,
    SdsMemberApp,
    SdsMemberGrant,
    SdsMemberProposal,
)
from orchestrator.services.membership.changes import GrantChange, MemberChange, MemberProposalBody

# "MBR1": serializes concurrent stagings of one idempotency key, so the loser replays the winner
# instead of meeting the unique constraint as an unhandled IntegrityError.
STAGING_LOCK_NAMESPACE: Final = 0x4D425231

# "MBR2": one lock for the whole membership record. Decisions on different proposals read and write
# the same members, so they take turns: the second validates against what the first left.
RECORD_LOCK_NAMESPACE: Final = 0x4D425232

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


def confirm_member_proposal(
    session: Session,
    *,
    actor: ActorContext,
    proposal_id: uuid.UUID,
    expected_digest: str,
) -> SdsMemberProposal:
    """Apply a staged proposal as the confirming person: every change or none.

    The digest is the one the person read on the page. Validation runs again against the members
    as they stand now, so a proposal staged before another confirmation moved the record is
    refused rather than applied blind.
    """
    _require_human(actor, "confirm")
    _lock_record(session)
    proposal = _locked_staged(session, proposal_id)
    if read_digest(session, proposal) != expected_digest:
        raise DomainError(
            "member_proposal_changed",
            "the proposal is not the one that was read",
            "reload the page and read it again",
        )
    body = MemberProposalBody.model_validate(proposal.payload)
    validate_changes(session, body.changes)
    for change in body.changes:
        _apply_change(session, proposal, body, change, actor)
    proposal.state = "confirmed"
    proposal.decided_by = actor.actor_id
    proposal.decided_at = datetime.now(UTC)
    session.flush()
    return proposal


def withdraw_member_proposal(
    session: Session, *, actor: ActorContext, proposal_id: uuid.UUID, reason: str
) -> SdsMemberProposal:
    """Retire a staged proposal a person decided against. It changes nothing in the record."""
    _require_human(actor, "withdraw")
    _require_text(reason, "reason")
    _lock_record(session)
    proposal = _locked_staged(session, proposal_id)
    proposal.state = "withdrawn"
    proposal.decided_by = actor.actor_id
    proposal.decided_at = datetime.now(UTC)
    proposal.decision_reason = reason
    session.flush()
    return proposal


def member_proposal(session: Session, proposal_id: uuid.UUID) -> SdsMemberProposal:
    """The proposal row, or `member_proposal_not_found` (a 404)."""
    proposal = session.get(SdsMemberProposal, proposal_id)
    if proposal is None:
        raise DomainError("member_proposal_not_found", "member proposal does not exist", None)
    return proposal


def proposal_preview(session: Session, proposal: SdsMemberProposal) -> list[dict[str, Any]]:
    """Each change beside the member as it stands now, for the page a person reads before deciding.

    A cell is `{"old", "new", "reason"}`; `new` is None where the change leaves a field alone.
    """
    body = MemberProposalBody.model_validate(proposal.payload)
    rows = []
    for change in body.changes:
        member = session.scalar(
            select(SdsMember)
            .where(func.lower(SdsMember.repository) == change.repository.lower())
            .execution_options(populate_existing=True)
        )
        grants = (
            {
                g.service: g.state
                for g in session.scalars(
                    select(SdsMemberGrant).where(SdsMemberGrant.member_id == member.id)
                )
            }
            if member is not None
            else {}
        )
        apps = (
            sorted(
                session.scalars(
                    select(SdsMemberApp.name).where(SdsMemberApp.member_id == member.id)
                )
            )
            if member is not None
            else []
        )
        rows.append(
            {
                "repository": change.repository,
                "reason": change.reason,
                "creates": member is None,
                "fields": {
                    name: {"old": getattr(member, name, None), "new": getattr(change, name)}
                    for name in ("state", "owner", "working_copy", "machine_local")
                },
                "apps": {
                    "old": apps,
                    "new": None
                    if change.apps is None
                    else [f"{a.name} ({a.health_url}, {a.branch})" for a in change.apps],
                },
                "grants": {
                    service: {
                        "old": grants.get(service, "not_assessed" if member is None else None),
                        "new": named.state if named is not None else None,
                        "reason": named.reason if named is not None else None,
                    }
                    for service in MEMBER_SERVICES
                    for named in [dict(change.grants).get(service)]
                },
            }
        )
    return rows


def read_digest(session: Session, proposal: SdsMemberProposal) -> str:
    """What a person read: the staged payload and the current values of the members it touches.

    Confirmation compares it under the record lock, so a record that moved since the page was
    rendered is refused rather than overwritten.
    """
    canonical = json.dumps(
        {"payload": proposal.payload_digest, "preview": proposal_preview(session, proposal)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _lock_record(session: Session) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, 0)"),
        {"namespace": RECORD_LOCK_NAMESPACE},
    )


def _require_human(actor: ActorContext, verb: str) -> None:
    if actor.role is not ActorRole.HUMAN or not actor.actor_id:
        raise DomainError(
            "human_actor_required",
            f"a membership proposal is decided by a person, not to {verb} by a machine",
            "decide it from /review/member-proposals/{id}",
        )


def _locked_staged(session: Session, proposal_id: uuid.UUID) -> SdsMemberProposal:
    """The row under FOR UPDATE, re-read so a press that waited on the lock sees the new state."""
    proposal = session.scalar(
        select(SdsMemberProposal)
        .where(SdsMemberProposal.id == proposal_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if proposal is None:
        raise DomainError("member_proposal_not_found", "member proposal does not exist", None)
    if proposal.state != "staged":
        raise DomainError(
            "member_proposal_not_staged",
            f"a {proposal.state} member proposal cannot be decided again",
            None,
        )
    return proposal


def _apply_change(
    session: Session,
    proposal: SdsMemberProposal,
    body: MemberProposalBody,
    change: MemberChange,
    actor: ActorContext,
) -> None:
    member = session.scalar(
        select(SdsMember)
        .where(func.lower(SdsMember.repository) == change.repository.lower())
        .execution_options(populate_existing=True)
    )
    created = member is None
    fields: dict[str, Any] = {}
    now = datetime.now(UTC)
    if member is None:
        # validate_changes guarantees a creation names both.
        assert change.state is not None and change.owner is not None
        member = SdsMember(
            repository=change.repository,
            owner=change.owner,
            state=change.state,
            working_copy=change.working_copy,
            machine_local=bool(change.machine_local),
            state_changed_by=actor.actor_id,
            state_reason=change.reason,
        )
        session.add(member)
        session.flush()
        fields = {
            name: {"old": None, "new": getattr(member, name)}
            for name in ("state", "owner", "working_copy", "machine_local")
        }
    else:
        for name in ("state", "owner", "working_copy", "machine_local"):
            new = getattr(change, name)
            old = getattr(member, name)
            if new is not None and new != old:
                fields[name] = {"old": old, "new": new}
                setattr(member, name, new)
        if "state" in fields:
            member.state_changed_by = actor.actor_id
            member.state_reason = change.reason
        if fields:
            member.updated_at = now
    grants = _apply_grants(
        session, member, change, created=created, seeded=body.seeded, actor=actor, now=now
    )
    apps = _replace_apps(session, member, change)
    if apps is not None and not created:
        member.updated_at = now
    session.add(
        Event(
            actor_id=actor.actor_id,
            action="member.created" if created else "member.changed",
            subject_type="sds_member",
            subject_id=member.id,
            payload={
                "proposal_id": str(proposal.id),
                "repository": member.repository,
                "reason": change.reason,
                "fields": fields,
                "grants": grants,
                "apps": apps,
            },
            correlation_id=proposal.id,
            idempotency_key=f"member-proposal:{proposal.id}:{member.id}",
        )
    )
    session.flush()


def _apply_grants(
    session: Session,
    member: SdsMember,
    change: MemberChange,
    *,
    created: bool,
    seeded: bool,
    actor: ActorContext,
    now: datetime,
) -> dict[str, Any]:
    """Write the grants a change names; a new member also gets every other service `not_assessed`,
    so each member always has one row per service."""
    grants: dict[str, Any] = {}
    existing = {
        row.service: row
        for row in session.scalars(
            select(SdsMemberGrant).where(SdsMemberGrant.member_id == member.id)
        )
    }
    named_grants: dict[str, GrantChange] = {str(k): v for k, v in change.grants.items()}
    for service in MEMBER_SERVICES:
        named = named_grants.get(service)
        if named is None and not created:
            continue
        state = named.state if named is not None else "not_assessed"
        reason = named.reason if named is not None else "not assessed at creation"
        row = existing.get(service)
        if row is None:
            session.add(
                SdsMemberGrant(
                    member_id=member.id,
                    service=service,
                    state=state,
                    seeded=seeded,
                    set_by=actor.actor_id,
                    reason=reason,
                    set_at=now,
                )
            )
            grants[service] = {"old": None, "new": state, "reason": reason}
        elif row.state != state or row.reason != reason:
            grants[service] = {"old": row.state, "new": state, "reason": reason}
            row.state, row.reason = state, reason
            row.seeded, row.set_by, row.set_at = seeded, actor.actor_id, now
    return grants


def _replace_apps(
    session: Session, member: SdsMember, change: MemberChange
) -> dict[str, list[str]] | None:
    """Replace the member's app list with the change's; `None` when the change leaves it alone."""
    if change.apps is None:
        return None
    old = list(
        session.scalars(select(SdsMemberApp.name).where(SdsMemberApp.member_id == member.id))
    )
    session.execute(delete(SdsMemberApp).where(SdsMemberApp.member_id == member.id))
    session.flush()
    session.add_all(
        SdsMemberApp(
            member_id=member.id, name=app.name, health_url=str(app.health_url), branch=app.branch
        )
        for app in change.apps
    )
    return {"old": sorted(old), "new": sorted(app.name for app in change.apps)}


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
