"""Bidirectional traceability query (WS-P2.6).

Resolves any node on the intent -> work unit -> PR -> commit -> artifact -> deployment ->
observation chain to the full ordered chain, answering "why is this code in production?". It
reads canonical rows only and composes the WS-P2.5 projections and the release-artifact /
deployment-observation fetchers; it never writes, never transitions, and never touches git.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    CONTAINER_IMAGE_KIND,
    DeploymentObservation,
    Observation,
    ReconciliationCondition,
    ReconciliationResolution,
    ReleaseArtifactBinding,
    UnitPrBinding,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle.pr_bindings import get_pr_binding
from orchestrator.services.release.deployment_observations import list_deployment_observations
from orchestrator.services.release.machine_activation import (
    LANDING_COMMIT,
    LANDING_FACTS_KEY,
    LANDING_HEAD_COMMIT,
    LANDING_OBSERVATION_TYPE,
    LANDING_PULL_REQUEST,
    LANDING_SOURCE_SYSTEM,
    LANDING_SUBJECT_TYPE,
)
from orchestrator.services.release.observations import ObservationFilters, list_observations
from orchestrator.services.release.release_artifacts import list_release_artifacts
from orchestrator.services.reporting.evidence_pack import evidence_pack_projection

# The revision watcher's row shape, as `revision_watcher/record.py` writes it. Transcribed because
# `src/orchestrator` cannot import that program; `tests/contract/test_served_revision_contract.py`
# holds the two equal, since a renamed key would empty this hop in silence.
SERVED_SOURCE_SYSTEM = "revision_watcher"
SERVED_SUBJECT_TYPE = "service"
SERVED_OBSERVATION_TYPE = "production_revision"
SERVED_REPOSITORY = "repository"
SERVED_COMMIT = "serving"


class TraceabilityAnchorResponse(BaseModel):
    """WS-P2.6: identifies which entity the caller anchored the traceability query on."""

    matched_on: str
    value: str


class TraceabilityIntentHop(BaseModel):
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    registered_by: str
    # ADR-0026. The chain could already answer what a work unit caused; this is the half that
    # says what caused the work. It belongs on the intent hop because the revision is where the
    # link is stored -- an observation would not do, because the observation hop reads only
    # unit-scoped observations and the landings of the unit's release commits, so a
    # revision-scoped observation never reaches any chain.
    change_record_id: int | None = None
    # ADR-0026 amendment 1. The other half of the same join, and it rides the SAME hop for the
    # same reason: the observation hop reads only unit-scoped observations and release-commit
    # landings, so the fact that caused this work could never arrive through it. Declared here
    # because a FastAPI `response_model` silently drops any key it does not declare -- the
    # service could set it and the consumer read nothing.
    originating_observation_id: uuid.UUID | None = None


class TraceabilityUnitHop(BaseModel):
    id: uuid.UUID
    unit_key: str
    title: str
    state: str
    authority_fingerprint: str
    authority_approved_by: str | None = None
    authority_decision: str | None = None


class TraceabilityPrHop(BaseModel):
    pr_number: int
    head_sha: str
    # Where the pull request was read from. `unit_pr_binding` is a pull request the factory's
    # worker opened and bound to this unit. `landing_ledger` is one made outside the factory (by a
    # build session), read from the landing record of the commit this unit's release binding
    # names, and only when the binding's own `implementation_pr_number` agrees with it.
    source: str = "unit_pr_binding"


class TraceabilityCommitHop(BaseModel):
    source_repository: str
    source_commit: str
    merge_commit: str
    implementation_pr_number: int | None = None


class TraceabilityArtifactHop(BaseModel):
    artifact_digest: str
    # The ONE field that separates the estate's two activation models. A reader who does not know
    # which repository is hosted and which is machine-local reads this and knows anyway.
    kind: str
    artifact_registry: str | None = None
    artifact_repository: str | None = None
    artifact_name: str | None = None
    artifact_tag: str | None = None
    workflow_run_url: str | None = None
    builder_id: str | None = None
    provenance_digest: str | None = None
    sbom_digest: str | None = None


class TraceabilityDeploymentHop(BaseModel):
    """One observation of an artifact being live, in whichever of the two activation models.

    `kind` is the single field that separates them: a hosted deployment carries the URL and the
    probe summary, a machine-local activation carries neither and reports the activation summary
    instead. A reader can tell which without knowing anything about the repository.
    """

    environment: str
    kind: str
    observed_artifact_digest: str
    digest_matches: bool
    deployment_ref: str
    deployment_url: str | None
    deployer: str | None
    observed_at: datetime
    status_summary: dict[str, Any]
    probe_summary: dict[str, Any]
    activation_summary: dict[str, Any]


class TraceabilityConditionHop(BaseModel):
    observation_kind: str
    condition_type: str
    detail: str
    resolution_generation: int
    detected_at: datetime
    open: bool
    resolution_decision: str | None = None


class TraceabilityObservationHop(BaseModel):
    # What the observation is about: this work unit, or (for a landing record) the repository the
    # unit's release binding names, joined on the exact commit that landed.
    subject_type: str
    subject_reference: str
    source_system: str
    observation_type: str
    status: str
    severity: str
    summary: str
    observed_at: datetime


class TraceabilityChainResponse(BaseModel):
    intent: TraceabilityIntentHop
    unit: TraceabilityUnitHop
    pr: TraceabilityPrHop | None = None
    commit: list[TraceabilityCommitHop]
    artifact: list[TraceabilityArtifactHop]
    deployment: list[TraceabilityDeploymentHop]
    conditions: list[TraceabilityConditionHop]
    observations: list[TraceabilityObservationHop]


class TraceabilityResponse(BaseModel):
    anchor: TraceabilityAnchorResponse
    chains: list[TraceabilityChainResponse]


@dataclass(frozen=True)
class TraceabilityAnchor:
    kind: str
    work_unit_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    artifact_digest: str | None = None
    commit: str | None = None
    pr_number: int | None = None
    source_repository: str | None = None
    environment: str | None = None
    observation_id: uuid.UUID | None = None

    @property
    def display_value(self) -> str:
        value = {
            "work_unit": self.work_unit_id,
            "revision": self.revision_id,
            "artifact_digest": self.artifact_digest,
            "commit": self.commit,
            "pr": self.pr_number,
            "environment": self.environment,
            "observation": self.observation_id,
        }[self.kind]
        return str(value)


def resolve_anchors(session: Session, anchor: TraceabilityAnchor) -> tuple[uuid.UUID, ...]:
    if anchor.kind == "work_unit":
        if session.get(WorkUnit, anchor.work_unit_id) is None:
            raise DomainError("work_unit_not_found", "work unit does not exist", None)
        return (anchor.work_unit_id,)  # type: ignore[return-value]
    if anchor.kind == "revision":
        if session.get(WorkPackageRevision, anchor.revision_id) is None:
            raise DomainError("revision_not_found", "package revision does not exist", None)
        return tuple(
            session.scalars(
                select(WorkUnit.id)
                .where(WorkUnit.work_package_revision_id == anchor.revision_id)
                .order_by(WorkUnit.unit_key)
            )
        )
    if anchor.kind == "observation":
        return _resolve_observation(session, anchor.observation_id)
    if anchor.kind == "artifact_digest":
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id)
            .where(ReleaseArtifactBinding.artifact_digest == anchor.artifact_digest)
            .order_by(ReleaseArtifactBinding.work_unit_id),
        )
    if anchor.kind == "commit":
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id)
            .where(
                or_(
                    ReleaseArtifactBinding.source_commit == anchor.commit,
                    ReleaseArtifactBinding.merge_commit == anchor.commit,
                )
            )
            .order_by(ReleaseArtifactBinding.work_unit_id),
        )
    if anchor.kind == "pr":
        return _resolve_pr(session, anchor)
    if anchor.kind == "environment":
        return _resolve_environment(session, anchor.environment)
    raise DomainError("traceability_anchor_invalid", f"unknown anchor kind {anchor.kind}", None)


def _distinct_units(session: Session, stmt: Select[tuple[uuid.UUID]]) -> tuple[uuid.UUID, ...]:
    # De-duplicate a digest/commit/PR shared by multiple bindings of the same unit, preserving
    # first-seen order. The caller is responsible for making that pre-dedup stream deterministic
    # (an `order_by` on the select), or "first-seen" just means DB-physical order.
    seen: dict[uuid.UUID, None] = {}
    for unit_id in session.scalars(stmt):
        seen.setdefault(unit_id, None)
    return tuple(seen)


def _resolve_observation(
    session: Session, observation_id: uuid.UUID | None
) -> tuple[uuid.UUID, ...]:
    """What work did this signal cause? ADR-0026 amendment 1.

    It resolves through the revisions that NAME the observation rather than through the
    observation hop, which reads only unit-scoped observations and the landings of the unit's
    release commits, and so can never carry an arbitrary repository-scoped signal -- the shape
    every signal producer in this estate emits.

    THE EXISTENCE CHECK IS WHAT MAKES THE EMPTY ANSWER MEAN SOMETHING. "This signal caused
    nothing yet" is ordinary -- it is the state of every observation nobody has acted on -- and
    "that is not an observation" is a caller error. Without the check both answer with an empty
    chain list and a reader cannot tell them apart.
    """
    if session.get(Observation, observation_id) is None:
        raise DomainError("observation_not_found", "observation does not exist", None)
    return tuple(
        session.scalars(
            select(WorkUnit.id)
            .join(
                WorkPackageRevision,
                WorkUnit.work_package_revision_id == WorkPackageRevision.id,
            )
            .where(WorkPackageRevision.originating_observation_id == observation_id)
            # One observation can cause more than one revision -- a package is revised, and each
            # revision carries the originating reference forward explicitly -- so the order is
            # across revisions first and by unit key within one.
            #
            # REVISION, NEVER THE PRIMARY KEY. `UUIDPrimaryKey.id` defaults to `uuid4`, so an
            # id-first order is arbitrary and two databases holding the same logical rows answer
            # differently -- which is the order this clause used to have, under this same
            # comment. `id` survives only as a total-order tiebreak across DIFFERENT packages,
            # which share no revision sequence; within one package `revision` is unique.
            .order_by(
                WorkPackageRevision.revision,
                WorkPackageRevision.id,
                WorkUnit.unit_key,
            )
        )
    )


def _resolve_pr(session: Session, anchor: TraceabilityAnchor) -> tuple[uuid.UUID, ...]:
    if anchor.source_repository is not None:
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id)
            .where(
                ReleaseArtifactBinding.source_repository == anchor.source_repository,
                ReleaseArtifactBinding.implementation_pr_number == anchor.pr_number,
            )
            .order_by(ReleaseArtifactBinding.work_unit_id),
        )
    return _distinct_units(
        session,
        select(UnitPrBinding.work_unit_id)
        .where(UnitPrBinding.pr_number == anchor.pr_number)
        .order_by(UnitPrBinding.work_unit_id),
    )


def _resolve_environment(session: Session, environment: str | None) -> tuple[uuid.UUID, ...]:
    # "What is in this environment now" = the latest observation per unit for that environment.
    rows = session.scalars(
        select(DeploymentObservation)
        .where(DeploymentObservation.environment == environment)
        .order_by(
            DeploymentObservation.observed_at.desc(),
            DeploymentObservation.recorded_at.desc(),
            DeploymentObservation.id.desc(),
        )
    )
    seen: dict[uuid.UUID, None] = {}
    for row in rows:
        seen.setdefault(row.implementation_work_unit_id, None)
    return tuple(seen)


def build_chain(session: Session, unit_id: uuid.UUID) -> TraceabilityChainResponse:
    projection = evidence_pack_projection(session, unit_id)  # raises work_unit_not_found if absent
    unit = projection["unit"]
    revision = projection["revision"]
    # The canonical authority approval is the one bound to the unit via
    # `unit.authority_approval_id` (see `persistence/repositories.py::exact_authority_approval`),
    # not merely the first `subject_type == "authority"` row: a unit can carry more than one
    # authority-type Approval (e.g. a standing-context expansion approval alongside the per-unit
    # envelope approval), and `projection["approvals"]` is ordered by `created_at` ascending.
    authority_approval = next(
        (a for a in projection["approvals"] if a.id == unit.authority_approval_id), None
    )

    artifacts = _unwrap(list_release_artifacts(session, unit_id))
    pr_binding = get_pr_binding(session, unit_id)

    deployment_hops: list[TraceabilityDeploymentHop] = []
    for binding in artifacts:
        for obs in _unwrap(list_deployment_observations(session, binding.id)):
            deployment_hops.append(
                TraceabilityDeploymentHop(
                    environment=obs.environment,
                    kind=obs.kind,
                    observed_artifact_digest=obs.observed_artifact_digest,
                    # The deployment-observation writer enforces this equality at write time
                    # (`deployment_observation_digest_mismatch`), so through any public writer
                    # this is always True; it confirms the build-to-deployment invariant for
                    # audit completeness and would only read False for a divergent row that
                    # reached this table by some non-writer path (e.g. reconciliation).
                    digest_matches=obs.observed_artifact_digest == binding.artifact_digest,
                    deployment_ref=obs.deployment_ref,
                    deployment_url=obs.deployment_url,
                    deployer=obs.deployer,
                    observed_at=obs.observed_at,
                    status_summary=obs.status_summary,
                    probe_summary=obs.probe_summary,
                    activation_summary=obs.activation_summary,
                )
            )

    conditions = tuple(
        session.scalars(
            select(ReconciliationCondition)
            .where(ReconciliationCondition.work_unit_id == unit_id)
            .order_by(ReconciliationCondition.detected_at, ReconciliationCondition.id)
        )
    )
    resolutions = (
        {
            row.condition_id: row
            for row in session.scalars(
                select(ReconciliationResolution).where(
                    ReconciliationResolution.condition_id.in_([c.id for c in conditions])
                )
            )
        }
        if conditions
        else {}
    )

    landings = [
        (binding, landing)
        for binding in artifacts
        if (landing := _landing_of_commit(session, binding.source_repository, binding.merge_commit))
        is not None
    ]
    # Several bindings can name one commit (an image and a machine-local activation of the same
    # merge), so one landing is listed once.
    landed = {landing.id: landing for _, landing in landings}
    # What production said it served once the release was deployed (SDS 1.1 item 5c). Keyed on the
    # image's built commit, so several bindings of one image list each reading once.
    served = {row.id: row for binding in artifacts for row in _served_revisions(session, binding)}
    observations = (
        list(
            list_observations(
                session,
                ObservationFilters(subject_type="work_unit", subject_reference=str(unit_id)),
            )
        )
        + list(landed.values())
        + list(served.values())
    )

    return TraceabilityChainResponse(
        intent=TraceabilityIntentHop(
            revision=revision.revision,
            content_hash=revision.content_hash,
            source_path=revision.source_path,
            source_commit=revision.source_commit,
            registered_by=revision.registered_by,
            change_record_id=revision.change_record_id,
            originating_observation_id=revision.originating_observation_id,
        ),
        unit=TraceabilityUnitHop(
            id=unit.id,
            unit_key=unit.unit_key,
            title=unit.title,
            state=unit.state,
            authority_fingerprint=unit.authority_fingerprint,
            authority_approved_by=authority_approval.approved_by if authority_approval else None,
            authority_decision=authority_approval.decision if authority_approval else None,
        ),
        pr=(
            TraceabilityPrHop(pr_number=pr_binding.pr_number, head_sha=pr_binding.head_sha)
            if pr_binding is not None
            else _landed_pull_request(landings)
        ),
        commit=[
            TraceabilityCommitHop(
                source_repository=b.source_repository,
                source_commit=b.source_commit,
                merge_commit=b.merge_commit,
                implementation_pr_number=b.implementation_pr_number,
            )
            for b in artifacts
        ],
        artifact=[
            TraceabilityArtifactHop(
                artifact_digest=b.artifact_digest,
                kind=b.kind,
                artifact_registry=b.artifact_registry,
                artifact_repository=b.artifact_repository,
                artifact_name=b.artifact_name,
                artifact_tag=b.artifact_tag,
                workflow_run_url=b.workflow_run_url,
                builder_id=b.builder_id,
                provenance_digest=b.provenance_digest,
                sbom_digest=b.sbom_digest,
            )
            for b in artifacts
        ],
        deployment=deployment_hops,
        conditions=[
            TraceabilityConditionHop(
                observation_kind=c.observation_kind,
                condition_type=c.condition_type,
                detail=c.detail,
                resolution_generation=c.resolution_generation,
                detected_at=c.detected_at,
                open=c.id not in resolutions,
                resolution_decision=(resolutions[c.id].decision if c.id in resolutions else None),
            )
            for c in conditions
        ],
        observations=[
            TraceabilityObservationHop(
                subject_type=o.subject_type,
                subject_reference=o.subject_reference,
                source_system=o.source_system,
                observation_type=o.observation_type,
                status=o.status,
                severity=o.severity,
                summary=o.summary,
                observed_at=o.observed_at,
            )
            for o in observations
        ],
    )


def _served_revisions(session: Session, binding: ReleaseArtifactBinding) -> list[Observation]:
    """The revision watcher's readings of production serving this binding's image.

    Only a container image has a served commit to match: a machine-local activation is a working
    copy, which the watcher's `orchestrator` subject -- the hosted container -- never describes, and
    joining across the two is the mix-up ADR-0030 forbids. The commit is the image's BUILT commit,
    which the binder records, not the unit's merge commit: an image usually carries several merges.
    """
    if binding.kind != CONTAINER_IMAGE_KIND or not isinstance(binding.summary, dict):
        return []
    image = binding.summary.get("image")
    built = image.get("built_commit") if isinstance(image, dict) else None
    if not isinstance(built, str) or not built:
        return []
    return list(
        session.scalars(
            select(Observation)
            .where(
                Observation.source_system == SERVED_SOURCE_SYSTEM,
                Observation.subject_type == SERVED_SUBJECT_TYPE,
                Observation.observation_type == SERVED_OBSERVATION_TYPE,
                func.lower(Observation.facts[SERVED_REPOSITORY].astext)
                == binding.source_repository.lower(),
                Observation.facts[SERVED_COMMIT].astext == built,
            )
            .order_by(Observation.observed_at, Observation.id)
        )
    )


def _landing_of_commit(session: Session, repository: str, commit: str) -> Observation | None:
    """The landing ledger's record of `commit` landing in `repository`, if it recorded one.

    Landing records are content-addressed per (repository, landed commit), so this is exact: at
    most one row, and only a record of that very commit landing.
    """
    return session.scalars(
        select(Observation)
        .where(
            Observation.source_system == LANDING_SOURCE_SYSTEM,
            Observation.subject_type == LANDING_SUBJECT_TYPE,
            Observation.observation_type == LANDING_OBSERVATION_TYPE,
            func.lower(Observation.subject_reference) == repository.lower(),
            Observation.facts[LANDING_FACTS_KEY][LANDING_COMMIT].astext == commit,
        )
        .order_by(Observation.observed_at, Observation.id)
        .limit(1)
    ).first()


def _landed_pull_request(
    landings: list[tuple[ReleaseArtifactBinding, Observation]],
) -> TraceabilityPrHop | None:
    """A pull request made outside the factory, when two independent writers agree on it.

    The release binding names the pull request and the commit it merged; the landing ledger,
    which reads GitHub, recorded that commit landing through a pull request. Only when both name
    the same number is the pull request this unit's.
    """
    for binding, landing in landings:
        changed = landing.facts.get(LANDING_FACTS_KEY)
        if not isinstance(changed, dict):
            continue
        number, head = changed.get(LANDING_PULL_REQUEST), changed.get(LANDING_HEAD_COMMIT)
        if (
            isinstance(number, int)
            and not isinstance(number, bool)
            and number == binding.implementation_pr_number
            and isinstance(head, str)
            and head
        ):
            return TraceabilityPrHop(pr_number=number, head_sha=head, source="landing_ledger")
    return None


def _unwrap[T](result: tuple[T, ...] | DomainError) -> tuple[T, ...]:
    # list_* fetchers return `tuple | DomainError`; inside build_chain the unit is known to exist
    # (evidence_pack_projection already validated it), so a DomainError here is a real bug.
    if isinstance(result, DomainError):
        raise result
    return result


def traceability_response(session: Session, anchor: TraceabilityAnchor) -> TraceabilityResponse:
    unit_ids = resolve_anchors(session, anchor)
    return TraceabilityResponse(
        anchor=TraceabilityAnchorResponse(matched_on=anchor.kind, value=anchor.display_value),
        chains=[build_chain(session, unit_id) for unit_id in unit_ids],
    )
