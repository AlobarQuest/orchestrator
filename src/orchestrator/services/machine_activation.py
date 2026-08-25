"""Which completed units a machine-local working copy could bind a release artifact for.

ADR-0030 named a second activation model: a change becomes live on the operator machine when the
code is pulled into a working copy and the next process start picks it up. The producer that
records one of those lives on that machine, because only the machine can answer whether the code
is there. This module answers the half the machine cannot: WHICH units it should be asking about,
and what the answer would have to be bound to.

WHERE THE LANDING COMMIT COMES FROM, and it is the whole design.

`record_release_artifact` wants the commit that landed the change, and the obvious source --
`UnitPrMerge`, the orchestrator's own record of its own act -- does not have it for the population
this lane exists to serve. Measured 2026-08-24: the estate's first fully-automated signal-to-merge
(`infraops-mcp-server` #81, unit `eb7c36f7`) was landed by a person, so no such row exists and the
one unit the lane most needs would be unreachable.

So the commit is read from the LANDING LEDGER'S OWN OBSERVATION, which an independent program
derived from GitHub, and it is CONFIRMED against the orchestrator's own worker-written
`UnitPrBinding.head_sha`. Two parties have to agree before a candidate exists: GitHub says this
pull request landed at that head as that commit, and the orchestrator says that head is this
unit's. Neither alone would do -- a commit trailer naming a unit is the runner attesting to its
own compliance, one artifact over, and a binding with no confirmation would let a landing choose
its own subject.

A unit whose landing nobody has observed yet is simply NOT A CANDIDATE. That is the ordinary state
between a merge and the ledger's next pass, and it fails closed: no answer, no row.

THIS MODULE DECIDES NOTHING ABOUT THE MACHINE. Whether the working copy actually holds the commit,
and what its content digest is, are facts only the machine has. It reports what a binding would
have to say; the producer measures whether it is true.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    MACHINE_LOCAL_KIND,
    DeploymentObservation,
    Observation,
    ReleaseArtifactBinding,
    UnitPrBinding,
    WorkPackageRevision,
    WorkUnit,
)

# The landing ledger's own row shape, as `landing_ledger/record.py` writes it. Named here because
# `src/orchestrator` cannot import that package -- it is a separate program by design -- so this
# is a transcription, and `tests/contract/test_landing_fact_contract.py` is what keeps the two
# agreeing. A rename on the ledger side would otherwise empty this stream in silence.
LANDING_SOURCE_SYSTEM = "github"
LANDING_SUBJECT_TYPE = "repo"
LANDING_OBSERVATION_TYPE = "landing"
LANDING_FACTS_KEY = "what_changed"
LANDING_REPOSITORY = "repository"
LANDING_PULL_REQUEST = "pull_request"
LANDING_HEAD_COMMIT = "head_commit"
LANDING_COMMIT = "commit"


@dataclass(frozen=True)
class MachineActivationCandidate:
    """One completed unit whose landing is confirmed, and what a binding for it must carry.

    `binding_id` is the machine-local binding that already exists, when one does. It is reported
    rather than filtered out so the producer can say it skipped a unit and why -- and it is scoped
    to `machine_local` deliberately: a container-image binding on the same unit describes the other
    model entirely and must neither suppress this one nor stand in for it.

    `binding_artifact_digest` and `observation_id` exist for the activation check that follows the
    binding. The digest is what the producer compares its working copy against: the artifact is
    the tree the binding named, so once `HEAD` moves past it that tree is no longer what the next
    start executes and there is nothing left to observe -- which is a fact about time, not a
    fault. `observation_id` says the check has already been filed, so a later pass replays nothing
    and cannot present a moved clock as a changed fact.
    """

    work_unit_id: uuid.UUID
    work_package_revision_id: uuid.UUID
    package_revision_hash: str
    unit_key: str
    # The unit's version AT THIS READ, so the producer can send a real `expected_version` rather
    # than probing for one. `CommandBase.expected_version` is `int = Field(ge=0)` -- required and
    # non-nullable -- so a producer with no version cannot post at all, and the estate's
    # documented workaround (post 0, read `current_version` off the conflict, retry) is a retry
    # branch this route can simply remove. If the unit transitions between this read and the
    # write, the write correctly fails as `version_conflict`, which is what the check is for.
    work_unit_version: int
    source_repository: str
    pr_number: int
    source_commit: str
    merge_commit: str
    binding_id: uuid.UUID | None
    binding_artifact_digest: str | None
    observation_id: uuid.UUID | None


def machine_activation_candidates(
    session: Session, repository: str
) -> tuple[MachineActivationCandidate, ...]:
    """Every completed unit targeting `repository` whose landing two parties agree on.

    The repository match is CASE-FOLDED on both sides. The orchestrator lowercases what it records
    of its own landings while an authority envelope and a ledger observation each carry whatever
    was authored, and production holds both spellings today -- so an exact comparison would refuse
    a candidate that is real.
    """
    wanted = repository.strip().lower()
    if not wanted:
        return ()
    landings = _landings_by_pull_request(session, wanted)
    if not landings:
        return ()

    rows = session.execute(
        select(WorkUnit, UnitPrBinding, WorkPackageRevision)
        .join(UnitPrBinding, UnitPrBinding.work_unit_id == WorkUnit.id)
        .join(WorkPackageRevision, WorkPackageRevision.id == WorkUnit.work_package_revision_id)
        .where(WorkUnit.state == WorkUnitState.COMPLETED.value)
        .order_by(WorkUnit.unit_key, WorkUnit.id)
    ).all()

    bound = _existing_machine_local_bindings(session)
    observed = _existing_activation_observations(session)
    candidates = []
    for unit, binding, revision in rows:
        target = normalize_authority(unit.authority or {}).target_repository.strip().lower()
        if target != wanted:
            continue
        existing = bound.get(unit.id)
        landing = landings.get(binding.pr_number)
        # The confirmation. GitHub's observed head must be the head this unit's own binding names,
        # or the landing being read is some other change that shared a pull request number.
        if landing is None or landing.head_commit.lower() != binding.head_sha.lower():
            continue
        candidates.append(
            MachineActivationCandidate(
                work_unit_id=unit.id,
                work_package_revision_id=revision.id,
                package_revision_hash=revision.content_hash,
                unit_key=unit.unit_key,
                work_unit_version=unit.version,
                source_repository=repository.strip(),
                pr_number=binding.pr_number,
                source_commit=binding.head_sha,
                merge_commit=landing.commit,
                binding_id=None if existing is None else existing.binding_id,
                binding_artifact_digest=None if existing is None else existing.artifact_digest,
                observation_id=(None if existing is None else observed.get(existing.binding_id)),
            )
        )
    return tuple(candidates)


@dataclass(frozen=True)
class _ExistingBinding:
    binding_id: uuid.UUID
    artifact_digest: str


@dataclass(frozen=True)
class _Landing:
    head_commit: str
    commit: str


def _landings_by_pull_request(session: Session, repository: str) -> dict[int, _Landing]:
    """The ledger's landings for one repository, keyed by pull request.

    A pull request number appears at most once per repository, so the last row wins only in the
    impossible case; ordering by `observed_at` makes even that deterministic.
    """
    rows = session.scalars(
        select(Observation)
        .where(
            Observation.source_system == LANDING_SOURCE_SYSTEM,
            Observation.subject_type == LANDING_SUBJECT_TYPE,
            Observation.observation_type == LANDING_OBSERVATION_TYPE,
            func.lower(Observation.subject_reference) == repository,
        )
        .order_by(Observation.observed_at, Observation.received_at, Observation.id)
    )
    landings: dict[int, _Landing] = {}
    for row in rows:
        landing = _landing_of(row.facts, repository)
        if landing is not None:
            landings[landing[0]] = landing[1]
    return landings


def _landing_of(facts: Any, repository: str) -> tuple[int, _Landing] | None:
    """One ledger row, read defensively. Anything short of complete is not a landing.

    A push with no pull request carries `pull_request: null` and `head_commit: null`, and is the
    majority of the ledger's rows -- so an incomplete row is the ordinary case rather than a fault,
    and is skipped rather than raised on.
    """
    if not isinstance(facts, dict):
        return None
    changed = facts.get(LANDING_FACTS_KEY)
    if not isinstance(changed, dict):
        return None
    named = changed.get(LANDING_REPOSITORY)
    if not isinstance(named, str) or named.strip().lower() != repository:
        return None
    pull_request = changed.get(LANDING_PULL_REQUEST)
    head_commit = changed.get(LANDING_HEAD_COMMIT)
    commit = changed.get(LANDING_COMMIT)
    if not isinstance(pull_request, int) or isinstance(pull_request, bool) or pull_request <= 0:
        return None
    if not isinstance(head_commit, str) or not head_commit.strip():
        return None
    if not isinstance(commit, str) or not commit.strip():
        return None
    return pull_request, _Landing(head_commit=head_commit.strip(), commit=commit.strip())


def _existing_machine_local_bindings(session: Session) -> dict[uuid.UUID, _ExistingBinding]:
    """Machine-local bindings by unit, with the digest each one names. Kind-scoped, and that
    scoping is the point.

    A unit can legitimately carry both kinds -- the same change can reach a registry image and a
    working copy -- so keying this on "has any binding" would let a container image suppress the
    machine-local row, and would equally let a machine-local row be read as protecting a unit whose
    only binding describes the other model.
    """
    rows = session.execute(
        select(
            ReleaseArtifactBinding.work_unit_id,
            ReleaseArtifactBinding.id,
            ReleaseArtifactBinding.artifact_digest,
        )
        .where(ReleaseArtifactBinding.kind == MACHINE_LOCAL_KIND)
        .order_by(ReleaseArtifactBinding.recorded_at, ReleaseArtifactBinding.id)
    ).all()
    return {
        unit_id: _ExistingBinding(binding_id=binding_id, artifact_digest=digest)
        for unit_id, binding_id, digest in rows
    }


def _existing_activation_observations(session: Session) -> dict[uuid.UUID, uuid.UUID]:
    """The activation checks already filed, by binding. Kind-scoped for the same reason.

    A container-image observation on the same binding cannot exist -- the ingest refuses a kind
    that disagrees with its binding -- but scoping this read anyway keeps the two models from
    standing in for one another wherever a future kind is added.
    """
    rows = session.execute(
        select(
            DeploymentObservation.release_artifact_binding_id,
            DeploymentObservation.id,
        )
        .where(DeploymentObservation.kind == MACHINE_LOCAL_KIND)
        .order_by(DeploymentObservation.recorded_at, DeploymentObservation.id)
    ).all()
    return {binding_id: observation_id for binding_id, observation_id in rows}
