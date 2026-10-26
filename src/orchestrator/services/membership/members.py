"""Reading the SDS member record. Writes live in `proposals.py` and nowhere else."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from orchestrator.persistence.models import MEMBER_SERVICES, SdsMember


def list_members(session: Session) -> list[SdsMember]:
    """Every member, retired included, with grants and apps loaded, by case-folded repository."""
    return list(
        session.scalars(
            select(SdsMember)
            .options(selectinload(SdsMember.grants), selectinload(SdsMember.apps))
            .order_by(func.lower(SdsMember.repository))
        )
    )


def member_record(member: SdsMember) -> dict[str, Any]:
    """One member as plain data, with a grant for each of the seven services.

    A service with no stored grant reads `not_assessed`; confirming a new member writes all seven,
    so the fallback only covers rows written some other way.
    """
    held = {grant.service: grant for grant in member.grants}
    grants: dict[str, dict[str, Any]] = {}
    for service in MEMBER_SERVICES:
        grant = held.get(service)
        grants[service] = (
            {
                "state": grant.state,
                "seeded": grant.seeded,
                "set_by": grant.set_by,
                "set_at": grant.set_at,
                "reason": grant.reason,
            }
            if grant is not None
            else {
                "state": "not_assessed",
                "seeded": False,
                "set_by": member.state_changed_by,
                "set_at": member.created_at,
                "reason": "no grant recorded",
            }
        )
    return {
        "repository": member.repository,
        "owner": member.owner,
        "state": member.state,
        "working_copy": member.working_copy,
        "machine_local": member.machine_local,
        "apps": [
            {"name": a.name, "health_url": a.health_url, "branch": a.branch}
            for a in sorted(member.apps, key=lambda a: a.name)
        ],
        "grants": grants,
    }
