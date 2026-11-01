"""Whether the SDS member record grants a service on a repository (ADR-0058).

Admission asks this in place of reading the repository's own `factory-target.toml` from GitHub: the
member record, confirmed by a person on `/review`, is the authority for which repositories the
factory may send work into, and for which kind of work.

A grant counts only in state `granted` on a member that is not retired. Repositories compare
case-folded: the record stores GitHub's spelling and is unique on its lower-case form, while an
envelope names the repository however its author typed it.

The lanes' population module holds a copy of the mapping. The lanes run out of process and
cannot import this module, so `tests/services/test_target_grant.py` holds the two equal.
"""

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.orm import Session

from orchestrator.services.membership.members import MemberView, list_members

DEPENDENCY_UPDATE = "dependency-update"
DEPENDENCY_UPDATES_SERVICE = "dependency_updates"
APP_CHANGES_SERVICE = "app_changes"


def service_for_change_class(change_class: str) -> str:
    """The member-record service whose grant admits work of this change class."""
    return DEPENDENCY_UPDATES_SERVICE if change_class == DEPENDENCY_UPDATE else APP_CHANGES_SERVICE


class TargetGrantSource(Protocol):
    def is_granted(self, repository: str, service: str) -> bool: ...


class MemberRecordGrants:
    """Reads the grant from the member record through the caller's own session.

    A caller asking about many repositories passes ``members`` read once, so the record is
    not re-read per question.
    """

    def __init__(self, session: Session, members: Sequence[MemberView] | None = None) -> None:
        self._session = session
        self._members = members

    def is_granted(self, repository: str, service: str) -> bool:
        wanted = repository.lower()
        return any(
            member.repository.lower() == wanted
            and member.state != "retired"
            and (grant := member.grants.get(service)) is not None
            and grant.state == "granted"
            for member in (
                self._members if self._members is not None else list_members(self._session)
            )
        )
