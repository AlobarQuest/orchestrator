"""The one automatic writer of the member tables: a hold after the SDS's own revert lands.
Landing Part 3a.

Every other change to the member record is a person confirming a proposal (`proposals.py`). This
one is Devon's decision of 2026-10-29: once a revert of a bump has landed, the SDS holds the
dependency itself, at the version before the bump, until a person lifts it.

It is narrow on purpose. It acts only on a revert record that has landed; it only inserts, a
ceiling at the record's from-version; and it never updates, replaces or lifts a hold, so a hold a
person placed, or one already there, stays as it is. Its one caller is the inert lane's merge path,
in the transaction that records the revert landing (`services/landing/inert_pr_merge.py`).

**It never raises for a bump it can't hold.** That transaction records a landing that already
happened on GitHub, and an exception would roll the record back -- a lost success. So a bump it
cannot hold is skipped, and the answer it returns says which and why; the caller stores it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import LandingRevert, SdsMember, SdsMemberHold
from orchestrator.services.membership.holds import Bump, HeldDependency, holding, release
from orchestrator.services.membership.members import holds_for
from orchestrator.services.membership.proposals import lock_record

# Who a hold placed here is set by. Not a person, and never one.
SET_BY: Final = "sds-revert"

_LANDED: Final = "revert_landed"


@dataclass(frozen=True)
class _Placed:
    """A hold this call placed, so a second bump of the same dependency finds it."""

    ecosystem: str
    directory: str
    dependency: str
    ceiling: str


def place(session: Session, revert: LandingRevert) -> str:
    """Hold each dependency the revert took back out, at its from-version; answer what was done.

    Takes the membership record's lock, as a person's confirmation does, so the two take turns.
    Flushes nothing and never commits: the caller owns the transaction.
    """
    if revert.status != _LANDED:
        return f"nothing held: the revert is {revert.status}, not landed"
    lock_record(session)
    member = session.scalar(
        select(SdsMember).where(func.lower(SdsMember.repository) == revert.repository.lower())
    )
    if member is None:
        return f"nothing held: {revert.repository} has no member record"
    held: list[HeldDependency] = list(holds_for(session, revert.repository))
    jobs = ", ".join(revert.failed_job_names)
    reason = f"Reverted {revert.repository}#{revert.pr_number}: {jobs} failed"
    answers: list[str] = []
    for entry in revert.bumps:
        answer, row = _hold(member, entry, held, reason)
        answers.append(answer)
        if row is not None:
            session.add(row)
            held.append(_Placed(row.ecosystem, row.directory, row.dependency, row.ceiling))
    return "; ".join(answers)


def _hold(
    member: SdsMember, entry: dict[str, Any], held: list[HeldDependency], reason: str
) -> tuple[str, SdsMemberHold | None]:
    """One bump's answer, and the hold to insert when there is one."""
    ecosystem = str(entry.get("ecosystem") or "")
    directory = entry.get("directory")
    dependency = str(entry.get("dependency") or "")
    from_version = entry.get("from_version")
    name = f"{ecosystem} {directory} {dependency}"
    if not ecosystem or not dependency:
        return f"not held {name}: the record does not name the dependency", None
    if not isinstance(directory, str) or not directory.startswith("/"):
        return f"not held {name}: the directory is not a path", None
    # An unreadable ceiling would hold every version of the dependency, with no sign it was wrong.
    if not isinstance(from_version, str) or release(from_version) is None:
        return f"not held {name}: the version before the bump is unknown", None
    existing = holding(held, Bump(ecosystem, directory, dependency, from_version))
    if existing:
        return f"not held {name}: already held at {existing[0].ceiling}", None
    row = SdsMemberHold(
        member_id=member.id,
        ecosystem=ecosystem,
        directory=directory,
        dependency=dependency,
        ceiling=from_version,
        reason=reason,
        set_by=SET_BY,
    )
    return f"held {name} at {from_version}", row
