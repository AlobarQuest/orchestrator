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

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityEnvelope, normalize_authority
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.services.lifecycle.rotation_claims import UnitTerms, is_rotation_unit
from orchestrator.services.membership.members import MemberView, list_members

# Why a target repository was refused: the member record does not grant the service that matches
# the unit's change class on it.
TARGET_REPOSITORY_NOT_GRANTED = "target_repository_not_granted"
DEPENDENCY_UPDATE = "dependency-update"
DEPENDENCY_UPDATES_SERVICE = "dependency_updates"
APP_CHANGES_SERVICE = "app_changes"


def service_for_change_class(change_class: str) -> str:
    """The member-record service whose grant admits work of this change class."""
    return DEPENDENCY_UPDATES_SERVICE if change_class == DEPENDENCY_UPDATE else APP_CHANGES_SERVICE


def change_class_of(envelope: AuthorityEnvelope, required_capability: str) -> str:
    """The unit's change class; an envelope that declares none falls back to its capability."""
    return envelope.change_class or required_capability


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


def target_not_granted_error() -> DomainError:
    return DomainError(
        TARGET_REPOSITORY_NOT_GRANTED,
        "the member record does not grant this work on the unit's target repository",
        "restore_grant",
    )


def unit_grant_refusal(
    session: Session,
    unit: WorkUnit | UnitTerms,
    revision: WorkPackageRevision,
    grants: TargetGrantSource | None = None,
) -> DomainError | None:
    """Why the member record stops this unit running; ``None`` means nothing here does.

    A caller asking about many units passes ``grants`` built over members it read once.

    A rotation unit answers to its own `credential_rotation` check, and a unit naming no target
    repository has no grant to read.
    """
    if is_rotation_unit(unit, revision):
        return None
    envelope = normalize_authority(unit.authority)
    if not envelope.target_repository:
        return None
    service = service_for_change_class(change_class_of(envelope, unit.required_capability))
    if (grants or MemberRecordGrants(session)).is_granted(envelope.target_repository, service):
        return None
    return target_not_granted_error()
