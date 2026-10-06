from datetime import datetime
from uuid import UUID

from fastapi import APIRouter

from orchestrator.api.dependencies import ActorDep, SessionDep
from orchestrator.api.routes.common import ERROR_RESPONSES, raise_error
from orchestrator.api.schemas.release import (
    DeploymentObservationCommandModel,
    MachineActivationCandidateResponse,
    ObservationCommandModel,
    ObservationResponse,
    ReleaseArtifactCommandModel,
)
from orchestrator.errors import DomainError
from orchestrator.persistence.models import MACHINE_LOCAL_KIND, RELEASE_ARTIFACT_KINDS, Observation
from orchestrator.services.reconciliation.reconciliation_detection import (
    detect_observation_conditions,
    record_digest_divergence,
)
from orchestrator.services.release.deployment_observations import (
    DeploymentObservationCommand,
    DeploymentObservationResponse,
    list_deployment_observations,
    record_deployment_observation,
)
from orchestrator.services.release.machine_activation import machine_activation_candidates
from orchestrator.services.release.observations import (
    ObservationCommand,
    ObservationFilters,
    list_observations,
    record_observation,
)
from orchestrator.services.release.release_artifacts import (
    ReleaseArtifactCommand,
    ReleaseArtifactResponse,
    list_release_artifacts,
    record_release_artifact,
)
from orchestrator.services.release.release_evidence_pack import (
    ReleaseEvidencePackResponse,
    release_evidence_pack_response,
)

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


def _parse_datetime_filter(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DomainError("observation_invalid", f"{field} is invalid", None) from error


@router.get(
    "/revisions/{revision_id}/evidence-pack",
    response_model=ReleaseEvidencePackResponse,
)
def release_evidence_pack_route(
    revision_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    """WS-P2.5 Increment 2: the per-release evidence pack -- every unit's pack in a package
    revision, plus that revision's release artifact bindings and deployment observations.

    Authentication-only, no role gate, matching the per-unit evidence-pack route. This JSON is
    FULL-FIDELITY (approver identity + rationale are present) and must never be relayed onto a
    possibly-public surface: the redaction boundary lives in the per-unit markdown relay, which
    this increment deliberately does not build.
    """
    return release_evidence_pack_response(session, revision_id)


@router.post(
    "/work-units/{unit_id}/release-artifacts",
    response_model=ReleaseArtifactResponse,
    status_code=201,
)
def create_release_artifact(
    unit_id: UUID,
    body: ReleaseArtifactCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return raise_error(
        record_release_artifact(
            session,
            ReleaseArtifactCommand(
                work_unit_id=unit_id,
                actor=actor,
                package_revision_id=body.package_revision_id,
                package_revision_hash=body.package_revision_hash,
                source_repository=body.source_repository,
                implementation_pr_number=body.implementation_pr_number,
                source_commit=body.source_commit,
                merge_commit=body.merge_commit,
                kind=body.kind,
                artifact_registry=body.artifact_registry,
                artifact_repository=body.artifact_repository,
                artifact_name=body.artifact_name,
                artifact_digest=body.artifact_digest,
                artifact_tag=body.artifact_tag,
                workflow_run_id=body.workflow_run_id,
                workflow_run_attempt=body.workflow_run_attempt,
                workflow_path=body.workflow_path,
                workflow_ref=body.workflow_ref,
                workflow_run_url=body.workflow_run_url,
                builder_id=body.builder_id,
                builder_class=body.builder_class,
                provenance_ref=body.provenance_ref,
                provenance_digest=body.provenance_digest,
                sbom_ref=body.sbom_ref,
                sbom_digest=body.sbom_digest,
                summary=body.summary,
                idempotency_key=body.idempotency_key,
                expected_version=body.expected_version,
            ),
        )
    )


@router.get(
    "/work-units/{unit_id}/release-artifacts",
    response_model=list[ReleaseArtifactResponse],
)
def release_artifacts(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return raise_error(list_release_artifacts(session, unit_id))


@router.get(
    "/machine-activation-candidates",
    response_model=list[MachineActivationCandidateResponse],
)
def machine_activation_candidates_route(
    repository: str,
    _actor: ActorDep,
    session: SessionDep,
    kind: str = MACHINE_LOCAL_KIND,
) -> object:
    """ADR-0030: which completed units a machine-local working copy could bind an artifact for.

    Authentication-only, matching the other read surfaces. Read-only: it writes nothing and
    asserts nothing about the machine. A repository with no confirmed landings answers with an
    empty list rather than a 404 -- the ordinary state of a repository the factory has not landed
    into yet, which a 404 would make indistinguishable from a misspelled name.

    `kind` names which bindings count as already made: `machine_local` for a working copy (the
    default, and the activation sweep's), `container_image` for a hosted image's deploy.
    """
    if kind not in RELEASE_ARTIFACT_KINDS:
        raise DomainError(
            "release_artifact_kind_invalid", "kind is not a release artifact kind", None
        )
    return list(machine_activation_candidates(session, repository, kind))


@router.post(
    "/release-artifacts/{binding_id}/deployment-observations",
    response_model=DeploymentObservationResponse,
    status_code=201,
)
def create_deployment_observation(
    binding_id: UUID,
    body: DeploymentObservationCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_deployment_observation(
        session,
        DeploymentObservationCommand(
            release_artifact_binding_id=binding_id,
            actor=actor,
            environment=body.environment,
            base_url=body.base_url,
            observed_artifact_digest=body.observed_artifact_digest,
            deployment_ref=body.deployment_ref,
            deployment_url=body.deployment_url,
            deployer=body.deployer,
            observed_at=body.observed_at,
            kind=body.kind,
            **body.stored_summaries(),
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        ),
    )
    # The digest guard RAISES and the ingest service rolls back, so a condition written in there
    # would be erased with the rejected observation. Record it here instead, in its own
    # transaction -- and the ingest stays rejected.
    if isinstance(result, DomainError) and result.code == "deployment_observation_digest_mismatch":
        record_digest_divergence(
            session,
            actor=actor,
            release_artifact_binding_id=binding_id,
            observed_artifact_digest=body.observed_artifact_digest,
            environment=body.environment,
        )
    return raise_error(result)


@router.get(
    "/release-artifacts/{binding_id}/deployment-observations",
    response_model=list[DeploymentObservationResponse],
)
def deployment_observations(
    binding_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return raise_error(list_deployment_observations(session, binding_id))


@router.post("/observations", response_model=ObservationResponse, status_code=201)
def create_observation(
    body: ObservationCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_observation(
        session,
        ObservationCommand(
            actor=actor,
            source_system=body.source_system,
            source_reference=body.source_reference,
            source_url=body.source_url,
            trust_classification=body.trust_classification,
            subject_type=body.subject_type,
            subject_reference=body.subject_reference,
            environment=body.environment,
            observation_type=body.observation_type,
            status=body.status,
            severity=body.severity,
            observed_at=body.observed_at,
            summary=body.summary,
            facts=body.facts,
            payload_digest=body.payload_digest,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        ),
    )
    # Post-commit, in the NEXT transaction: record_observation has already committed, so a
    # rejected ingest never reaches detection and a detection failure cannot roll the
    # observation back. Detection never raises, so a forged correlation cannot turn a valid
    # observation into a rejected ingest.
    if isinstance(result, Observation):
        detect_observation_conditions(session, result, actor)
    return raise_error(result)


@router.get("/observations", response_model=list[ObservationResponse])
def observations(
    _actor: ActorDep,
    session: SessionDep,
    source_system: str | None = None,
    subject_type: str | None = None,
    subject_reference: str | None = None,
    observation_type: str | None = None,
    environment: str | None = None,
    observed_from: str | None = None,
    observed_to: str | None = None,
) -> object:
    return raise_error(
        list_observations(
            session,
            ObservationFilters(
                source_system=source_system,
                subject_type=subject_type,
                subject_reference=subject_reference,
                observation_type=observation_type,
                environment=environment,
                observed_from=_parse_datetime_filter(observed_from, "observed_from"),
                observed_to=_parse_datetime_filter(observed_to, "observed_to"),
            ),
        )
    )
