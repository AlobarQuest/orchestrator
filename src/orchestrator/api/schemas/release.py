from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from orchestrator.api.schemas.common import CommandBase
from orchestrator.services.release.deployment_observations import (
    ActivationSummary,
    AuthSummary,
    DispatchSummary,
    ProbeSummary,
    RouteSummary,
    StatusSummary,
)


class ReleaseArtifactCommandModel(CommandBase):
    package_revision_id: UUID
    package_revision_hash: str = Field(min_length=1)
    source_repository: str = Field(min_length=1)
    implementation_pr_number: int | None = Field(default=None, gt=0)
    source_commit: str = Field(min_length=1)
    merge_commit: str = Field(min_length=1)
    # Defaulted so every existing caller keeps its meaning. The registry three below are
    # OPTIONAL here and conditional in the service, which is the authority: a container image
    # requires them and a machine-local activation refuses them. Loosening the wire while the
    # service still refuses keeps one rule in one place.
    kind: str = "container_image"
    artifact_registry: str | None = None
    artifact_repository: str | None = None
    artifact_name: str | None = None
    artifact_digest: str = Field(min_length=1)
    artifact_tag: str | None = None
    workflow_run_id: str | None = None
    workflow_run_attempt: int | None = Field(default=None, gt=0)
    workflow_path: str | None = None
    workflow_ref: str | None = None
    workflow_run_url: str | None = None
    builder_id: str | None = None
    builder_class: str | None = None
    provenance_ref: str | None = None
    provenance_digest: str | None = None
    sbom_ref: str | None = None
    sbom_digest: str | None = None
    summary: dict[str, Any] | None = None


class DeploymentObservationCommandModel(CommandBase):
    """The wire shape of both activation models, LOOSER than either one on its own.

    Each summary is its declared model, so the served OpenAPI document publishes its shape; the
    models belong to the service, which validates through them too. Every summary is OPTIONAL
    here and conditional in the service, which is the authority: a hosted observation requires
    the URLs and the four probe-shaped summaries (dispatch posture optional), a machine-local one
    refuses them and requires the activation summary. Loosening the wire while the service still
    refuses keeps one rule in one place -- and this model is a SECOND rule set the service's own
    tests never traverse, so each producer's composed payload is validated against it directly by
    `tests/contract`.
    """

    environment: str = Field(min_length=1)
    base_url: str | None = None
    observed_artifact_digest: str = Field(min_length=1)
    deployment_ref: str = Field(min_length=1)
    deployment_url: str | None = None
    deployer: str | None = None
    observed_at: datetime
    kind: str = "container_image"
    probe_summary: ProbeSummary | None = None
    route_summary: RouteSummary | None = None
    auth_summary: AuthSummary | None = None
    dispatch_summary: DispatchSummary | None = None
    status_summary: StatusSummary | None = None
    activation_summary: ActivationSummary | None = None

    @field_validator(
        "probe_summary",
        "route_summary",
        "auth_summary",
        "dispatch_summary",
        "status_summary",
        "activation_summary",
        mode="before",
    )
    @classmethod
    def _empty_is_absent(cls, value: object) -> object:
        """An explicit `{}` is the absent summary, as it was when these fields were dicts: the
        service stores and compares `{}` for a summary nobody sent, so the two must not differ."""
        return None if isinstance(value, dict) and not value else value

    def stored_summaries(self) -> dict[str, dict[str, Any]]:
        """Each summary as the service stores it: exactly the keys the caller sent, `{}` for none.

        `exclude_unset`, never `exclude_none` or a full dump: a default filled in here would make
        a retry that omits an optional key look different from a first write that sent it (or the
        reverse), and the idempotent replay compares the two payloads key for key.
        """
        summaries = {
            "probe_summary": self.probe_summary,
            "route_summary": self.route_summary,
            "auth_summary": self.auth_summary,
            "dispatch_summary": self.dispatch_summary,
            "status_summary": self.status_summary,
            "activation_summary": self.activation_summary,
        }
        return {
            name: {} if summary is None else summary.model_dump(mode="json", exclude_unset=True)
            for name, summary in summaries.items()
        }


class ObservationCommandModel(CommandBase):
    source_system: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    source_url: str | None = None
    trust_classification: str = Field(min_length=1)
    subject_type: str = Field(min_length=1)
    subject_reference: str = Field(min_length=1)
    environment: str | None = None
    observation_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    observed_at: datetime
    summary: str = Field(min_length=1)
    facts: dict[str, Any]
    payload_digest: str | None = None


class MachineActivationCandidateResponse(BaseModel):
    """One completed unit a machine-local working copy could bind a release artifact for.

    Everything here is the ORCHESTRATOR's half of the answer. Whether the working copy actually
    holds `merge_commit`, and what its content digest is, are facts only the machine has.
    """

    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    work_package_revision_id: UUID
    package_revision_hash: str
    unit_key: str
    work_unit_version: int
    source_repository: str
    pr_number: int
    source_commit: str
    merge_commit: str
    binding_id: UUID | None
    binding_artifact_digest: str | None
    observation_id: UUID | None


class ObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_system: str
    source_reference: str
    source_url: str | None
    trust_classification: str
    subject_type: str
    subject_reference: str
    environment: str | None
    observation_type: str
    status: str
    severity: str
    observed_at: datetime
    received_at: datetime
    summary: str
    facts: dict[str, Any]
    normalized_fact_hash: str
    payload_digest: str | None
    recorded_by: str
    event_id: UUID
    idempotency_key: str
