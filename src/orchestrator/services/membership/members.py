"""Reading the SDS member record. Writes live in `proposals.py` and nowhere else.

Reads return frozen records, never ORM objects: a caller holding a mapped row could change a
member without passing the writers guard (`tests/architecture/test_membership_writers.py`).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from orchestrator.persistence.models import MEMBER_SERVICES, SdsMember, SdsMemberProposal


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class AppView(_Record):
    name: str
    health_url: str
    branch: str


class GrantView(_Record):
    state: str
    seeded: bool
    set_by: str
    set_at: datetime
    reason: str


class HoldView(_Record):
    ecosystem: str
    directory: str
    dependency: str
    ceiling: str
    reason: str
    set_by: str
    set_at: datetime


class MemberView(_Record):
    """One member with a grant for each of the seven services, keyed by wire value, and its holds
    (empty when it has none)."""

    repository: str
    owner: str
    state: str
    working_copy: str | None
    machine_local: bool
    apps: tuple[AppView, ...]
    grants: dict[str, GrantView]
    holds: tuple[HoldView, ...]


class StagedProposalView(_Record):
    id: uuid.UUID
    summary: str
    staged_by: str
    staged_at: datetime


def list_members(session: Session) -> list[MemberView]:
    """Every member, retired included, by case-folded repository, with a grant for each of the
    seven services.

    A service with no stored grant reads `not_assessed`; confirming a new member writes all seven,
    so the fallback only covers rows written some other way.
    """
    members = session.scalars(
        select(SdsMember)
        .options(
            selectinload(SdsMember.grants),
            selectinload(SdsMember.apps),
            selectinload(SdsMember.holds),
        )
        .order_by(func.lower(SdsMember.repository))
    )
    views = []
    for member in members:
        held = {grant.service: grant for grant in member.grants}
        grants = {
            service: GrantView(
                state=grant.state,
                seeded=grant.seeded,
                set_by=grant.set_by,
                set_at=grant.set_at,
                reason=grant.reason,
            )
            if (grant := held.get(service)) is not None
            else GrantView(
                state="not_assessed",
                seeded=False,
                set_by=member.state_changed_by,
                set_at=member.created_at,
                reason="no grant recorded",
            )
            for service in MEMBER_SERVICES
        }
        views.append(
            MemberView(
                repository=member.repository,
                owner=member.owner,
                state=member.state,
                working_copy=member.working_copy,
                machine_local=member.machine_local,
                apps=tuple(
                    AppView(name=a.name, health_url=a.health_url, branch=a.branch)
                    for a in sorted(member.apps, key=lambda a: a.name)
                ),
                grants=grants,
                holds=tuple(
                    HoldView(
                        ecosystem=h.ecosystem,
                        directory=h.directory,
                        dependency=h.dependency,
                        ceiling=h.ceiling,
                        reason=h.reason,
                        set_by=h.set_by,
                        set_at=h.set_at,
                    )
                    for h in sorted(
                        member.holds, key=lambda h: (h.ecosystem, h.directory, h.dependency)
                    )
                ),
            )
        )
    return views


def holds_for(session: Session, repository: str) -> tuple[HoldView, ...]:
    """The holds on one repository, compared case-folded, whatever the member's state: a hold is
    a person's statement about a dependency, and a retired member's still applies to anything that
    would land there."""
    wanted = repository.lower()
    return tuple(
        hold
        for member in list_members(session)
        if member.repository.lower() == wanted
        for hold in member.holds
    )


def apps_for(session: Session, repository: str, branch: str) -> tuple[str, ...]:
    """The names of the apps a live member serves for this repository from `branch`, compared
    case-folded: the subjects `revision_watcher` reads, which it names by app name alone. A
    retired member's apps are not read, so they have none."""
    wanted = repository.lower()
    return tuple(
        app.name
        for member in list_members(session)
        if member.repository.lower() == wanted and member.state != "retired"
        for app in member.apps
        if app.branch == branch
    )


def staged_member_proposals(session: Session) -> tuple[StagedProposalView, ...]:
    """Every member proposal a machine staged and no person has decided, oldest first."""
    rows = session.scalars(
        select(SdsMemberProposal)
        .where(SdsMemberProposal.state == "staged")
        .order_by(SdsMemberProposal.staged_at, SdsMemberProposal.id)
    )
    return tuple(
        StagedProposalView(
            id=row.id,
            summary=str(row.payload.get("summary", "")),
            staged_by=row.staged_by,
            staged_at=row.staged_at,
        )
        for row in rows
    )
