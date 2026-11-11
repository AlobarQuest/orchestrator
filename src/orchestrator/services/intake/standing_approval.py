"""Whether the decomposition gate may admit a standing approval's policy approver (ADR-0055 a22).

Amendment 22 lets the policy approver `policy:non-software-operational@v<N>` approve a lane
credential's revision 1 in place of a person, once the work carrier has run the standing
approval's checks on it. A revision reaches intake by more doors than the carrier, and every door
converges on the decomposition gate, so the exception lives there (`decomposition.py`,
`_rotation_policy_outcome`), and this module is all of it.

**THE GATE RUNS NO CHECKS OF ITS OWN.** It can't read the registry: nothing under
`src/orchestrator/` reads it, and adding a read would add an outbound path. What it requires
instead is the carrier's record of its checks: an observation recorded by the configured carrier
identity (`Settings.work_carrier_agent_id`) that binds this revision's package id, revision number
and `content_hash`, and states that the carry form refused nothing. The carrier records it before
it posts the intake, so it exists when the proposal's own transaction reads it.

**ANYTHING ELSE REFUSES, AND A PERSON DECIDES.** A wrong approver prefix, a revision that isn't a
lane credential's rotation revision 1, an unset identity, an absent or mismatched record, or a read
that fails: each is a `PolicyRefusal`, never an exception and never a pass.
"""

from collections.abc import Mapping
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Observation, WorkPackageRevision
from orchestrator.services.intake.bump_policy import PolicyRefusal

# The approver the standing approval records. Keyed on the prefix, as `bump_policy` keys on
# `policy:dependency-update@`, so a later schema version doesn't stop every standing approval;
# intent-packages refuses any version but its loaded policy's.
STANDING_APPROVER: Final = "policy:non-software-operational@"
ROTATION_PROFILE: Final = "non-software-operational"
UNRECORDED: Final = "standing_approval_unrecorded"
# The carrier's row, as `work_carrier/standing.py` composes it. Spelled here rather than imported:
# the orchestrator imports no out-of-process program.
# `tests/services/test_standing_approval_gate.py` holds the two sides equal.
CARRIER_SOURCE: Final = "work_carrier"
CARRIER_CHECK: Final = "standing_approval_check"
# The two lane types' provider destinations, in the executor's destination format
# (`rotation_worker.destinations.render`); the same test holds them to it.
# not-a-vocabulary: whole destination strings a package lists, held to the executor's by test
LANE_PROVIDERS: Final = (
    'provider name="bws-lane-token"',
    'provider name="healthchecks-ping-url"',
)


def is_standing_approver(approver: str) -> bool:
    return approver.startswith(STANDING_APPROVER)


def subject_for(package_id: str, revision: int) -> str:
    """The revision as the carrier names it: it has no orchestrator id when the carrier records."""
    return f"{package_id}@r{revision}"


def standing_approval_refusal(
    session: Session, revision: WorkPackageRevision, carrier_agent_id: str | None
) -> PolicyRefusal | None:
    """Why the standing approval can't stand for a person on this revision; ``None`` when the
    carrier's record of its checks is present and bound to it. The caller has already seen the
    `policy:non-software-operational@` prefix."""
    if revision.profile != ROTATION_PROFILE:
        return PolicyRefusal(UNRECORDED, ("not_a_rotation_revision",))
    if revision.revision != 1:
        return PolicyRefusal(UNRECORDED, ("not_revision_1",))
    if not _names_one_lane_type(revision.enforcement_snapshot):
        return PolicyRefusal(UNRECORDED, ("not_a_lane_type",))
    if carrier_agent_id is None:
        return PolicyRefusal(UNRECORDED, ("carrier_identity_unset",))
    package_id = revision.work_package.package_id
    try:
        with session.begin_nested():
            rows = session.scalars(
                select(Observation).where(
                    Observation.source_system == CARRIER_SOURCE,
                    Observation.observation_type == CARRIER_CHECK,
                    Observation.subject_type == "package_revision",
                    Observation.subject_reference == subject_for(package_id, revision.revision),
                    Observation.recorded_by == carrier_agent_id,
                    Observation.status == "passed",
                )
            ).all()
    except SQLAlchemyError as error:
        return PolicyRefusal(UNRECORDED, ("carrier_record_unreadable", type(error).__name__))
    if not any(_binds(row.facts, package_id, revision) for row in rows):
        return PolicyRefusal(UNRECORDED, ("no_carrier_record",))
    return None


def _names_one_lane_type(snapshot: object) -> bool:
    fields = snapshot.get("profile_fields") if isinstance(snapshot, Mapping) else None
    destinations = fields.get("destinations") if isinstance(fields, Mapping) else None
    if not isinstance(destinations, list):
        return False
    return sum(1 for item in destinations if item in LANE_PROVIDERS) == 1


def _binds(facts: object, package_id: str, revision: WorkPackageRevision) -> bool:
    """The record names this revision by package id, number and content hash, and states that the
    carry form refused nothing. A record of any other shape binds nothing."""
    if not isinstance(facts, Mapping):
        return False
    number = facts.get("revision")
    return (
        facts.get("package_id") == package_id
        and isinstance(number, int)
        and not isinstance(number, bool)
        and number == revision.revision
        and facts.get("content_hash") == revision.content_hash
        and facts.get("refused") == []
    )
