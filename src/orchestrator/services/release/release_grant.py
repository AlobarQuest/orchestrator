"""Whether the member record grants release work on a unit's own repository (Release Part 2).

Binding an image, recording a rollout observation and reading machine-activation candidates are
release work, and the member record grants it per repository as the `rollouts` service. The
repository asked about is the unit's envelope `target_repository`, never the request body's
`source_repository`: a body naming a granted repository for a unit aimed at another is a mismatch,
not a grant.

The grant is read as every other grant is (`target_grant.MemberRecordGrants`): case-folded, state
`granted` only, on a member that is not retired.
"""

from typing import Final

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.execution.target_grant import MemberRecordGrants

RELEASE_GRANT_SERVICE: Final = "rollouts"
RELEASE_REPOSITORY_NOT_GRANTED: Final = "release_repository_not_granted"
RELEASE_REPOSITORY_MISMATCH: Final = "release_repository_mismatch"


def repository_release_refusal(session: Session, repository: str) -> DomainError | None:
    """Why release work on ``repository`` is refused; ``None`` when it holds `rollouts`."""
    if MemberRecordGrants(session).is_granted(repository, RELEASE_GRANT_SERVICE):
        return None
    return DomainError(
        RELEASE_REPOSITORY_NOT_GRANTED,
        "the member record does not grant release work on this repository",
        "restore_grant",
    )


def unit_release_refusal(
    session: Session, unit: WorkUnit, source_repository: str
) -> DomainError | None:
    """Why release work for this unit is refused; ``None`` when its own repository is granted.

    ``source_repository`` is what the request body names. It must be the unit's target, compared
    case-folded and stripped, and a unit naming no target has nothing to match.
    """
    target = normalize_authority(unit.authority or {}).target_repository
    if not target or source_repository.strip().lower() != target.strip().lower():
        return DomainError(
            RELEASE_REPOSITORY_MISMATCH,
            "the request names a repository other than the unit's own target repository",
            "name_the_units_target_repository",
        )
    return repository_release_refusal(session, target)
