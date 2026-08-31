from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class CommandBase(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0)


class ChangeWindowOverrideModel(BaseModel):
    """A supervised act's statement that it may start outside the hours policy declares.

    `reason` is deliberately UNCONSTRAINED here, against this module's habit of `min_length=1`.
    A constrained field would answer `{}` and `{"reason": null}` with a 422 listing a field
    location, where the requirement is a named refusal a caller can act on -- and the requirement
    itself belongs to the type that carries the override, so it holds for a caller reaching the
    services directly as well as for this one. Presence is what declares the override; the reason
    is what makes the record worth reading, and the two are separable only if this model lets an
    override arrive without one.
    """

    reason: str | None = None


class ClaimCommand(CommandBase):
    standing_context: dict[str, Any] | None = None


class RenewCommand(CommandBase):
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)


class ReclaimCommand(CommandBase):
    next_owner_id: str = Field(min_length=1)
    standing_context: dict[str, Any] | None = None


class RecoverExpiredClaimCommand(CommandBase):
    pass


class LifecycleCommand(CommandBase):
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)
    reason: str | None = None
    standing_context: dict[str, Any] | None = None
    context_snapshot_id: UUID | None = None


class ApprovalCommand(CommandBase):
    subject_type: str = Field(pattern="^(authority|action)$")
    reason: str = Field(min_length=1)
    standing_context: dict[str, Any] | None = None


class RetryCommand(CommandBase):
    new_max_attempts: int = Field(gt=0)
    reason: str = Field(min_length=1)


class AdjudicationCommand(CommandBase):
    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    outcome: str = Field(pattern="^(passed|failed|waived|not_applicable)$")
    rationale: str = Field(min_length=1)
    evidence_id: UUID | None = None
    failed_evidence_id: UUID | None = None
    risk: str | None = None
    follow_up: str | None = None
    scope: str | None = None
    expires_at: datetime | None = None


class DependencyCommand(CommandBase):
    kind: str
    required_state_or_condition: str
    depends_on_work_unit_id: UUID | None = None
    external_ref: str | None = None


class DependencyResolutionCommand(CommandBase):
    status: str = Field(pattern="^(satisfied|failed)$")
    detail: dict[str, Any] = Field(default_factory=dict)


class EvidenceCommand(CommandBase):
    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    source_revision: str = Field(min_length=1)
    context_snapshot_id: UUID | None = None
    # A later attempt supersedes the current evidence for its AC rather than first-writing
    # over it. The service resolves which row to supersede from current_evidence, so the
    # caller signals only intent. Default False preserves first-write behavior.
    supersede: bool = False


class PreflightCommandModel(CommandBase):
    standing_context: dict[str, Any]
    purpose: str = Field(min_length=1)
    previous_context_snapshot_id: UUID | None = None
    approval_id: UUID | None = None
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)


class DispatchCommandModel(CommandBase):
    runner_attempt: int = Field(gt=0)
    # ADR-0032. Suppresses `outside_change_window` and nothing else, and grants nothing to the
    # act that lands the pull request the run produces -- that act carries its own.
    change_window_override: ChangeWindowOverrideModel | None = None


class InfraLaneLinkCommandModel(CommandBase):
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)
    status: str = Field(
        pattern="^(requested|approved|executing|verification_pending|completed|failed|cancelled)$"
    )
    change_manager_ref: str = Field(min_length=1)
    change_manager_url: str | None = None
    infraops_ref: str | None = None
    approval_ref: str | None = None
    rollback_ref: str | None = None
    verify_ref: str | None = None
    final_evidence_ref: str | None = None
    payload: dict[str, Any] | None = None


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

    Every field a machine-local observation cannot carry is optional here and conditional in the
    service, which is the authority: a hosted observation requires the URLs and the five
    probe-shaped summaries, a machine-local one refuses them and requires the activation summary.
    Loosening the wire while the service still refuses keeps one rule in one place -- and this
    model is a SECOND rule set the service's own tests never traverse, so a producer's composed
    payload is validated against it directly by `tests/contract`.
    """

    environment: str = Field(min_length=1)
    base_url: str | None = None
    observed_artifact_digest: str = Field(min_length=1)
    deployment_ref: str = Field(min_length=1)
    deployment_url: str | None = None
    deployer: str | None = None
    observed_at: datetime
    kind: str = "container_image"
    probe_summary: dict[str, Any] = Field(default_factory=dict)
    route_summary: dict[str, Any] = Field(default_factory=dict)
    auth_summary: dict[str, Any] = Field(default_factory=dict)
    dispatch_summary: dict[str, Any] = Field(default_factory=dict)
    status_summary: dict[str, Any] = Field(default_factory=dict)
    activation_summary: dict[str, Any] = Field(default_factory=dict)


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


class KnowledgePromotionProposalCommandModel(CommandBase):
    correlation_identity: str = Field(min_length=1, max_length=200)
    source_observation_ids: list[UUID] = Field(min_length=1)
    release_artifact_binding_id: UUID | None = None
    deployment_observation_id: UUID | None = None
    work_unit_id: UUID | None = None
    package_revision_id: UUID | None = None
    correlation_summary: str = Field(min_length=1, max_length=700)
    target_brain: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    applicability: dict[str, Any] = Field(default_factory=dict)
    proposed_payload: dict[str, Any]
    provenance: dict[str, Any] = Field(default_factory=dict)


class KnowledgePromotionSubmitCommandModel(CommandBase):
    pass


class VerifyCommandModel(CommandBase):
    pass


class VerifierNamedCheckEvidenceCommandModel(CommandBase):
    """WS-P2.20: the caller names a check and claims a conclusion; it does not report one.

    There is no field for what the check actually concluded, nor for the run that produced it.
    The orchestrator reads those from GitHub at ingestion, so a caller cannot supply both halves
    of a comparison and have the criterion resolve on its own arithmetic.
    """

    model_config = ConfigDict(extra="forbid")

    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1, max_length=100)
    dispatch_id: UUID
    repository: str = Field(min_length=1, max_length=300)
    pr_number: int = Field(gt=0)
    pr_url: str = Field(min_length=1, max_length=2000)
    head_sha: str = Field(min_length=7, max_length=64)
    check_name: str = Field(min_length=1, max_length=200)
    expected_conclusion: Literal[
        "success",
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "neutral",
        "skipped",
    ]


class AcceptanceCriterionDeclaration(BaseModel):
    """What one of the revision's required acceptance criteria actually IS.

    The bootstrap registration lane could always declare WHICH ac_ids a revision requires (the
    enforcement snapshot's list of strings) and never what any of them meant. A required ac_id
    with no criterion behind it is decidable by no actor: `human_may_adjudicate` refuses an absent
    criterion, and the verify command refuses the whole revision. Such a unit used to be
    completable only by a verifier asserting an outcome it had not evaluated (WS-P2.32).
    """

    ac_id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    approver: str = Field(min_length=1)


class RevisionRegistration(CommandBase):
    package_id: str
    source_repository: str
    revision: int = Field(gt=0)
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    approved_at: datetime
    approval_event_id: str = Field(min_length=1)
    enforcement_snapshot: dict[str, Any]
    authority: dict[str, Any]
    registry_version: int = Field(ge=0)
    acceptance_criteria: list[AcceptanceCriterionDeclaration] | None = None


class UnitRegistration(CommandBase):
    unit_key: str
    title: str
    outcome: str
    required_capability: str
    authority: dict[str, Any]
    max_attempts: int = Field(ge=0, default=3)
    approved_by: str
    approved_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    recovery: str | None = None
    current_state: str | None = None
    current_version: int | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class RevisionResponse(BaseModel):
    id: UUID
    revision: int


class UnitResponse(BaseModel):
    id: UUID
    state: str
    version: int


class ReadinessReasonResponse(BaseModel):
    code: str
    subject_id: UUID | None
    detail: str


class ReadinessResponse(BaseModel):
    status: str
    reasons: list[ReadinessReasonResponse]


class RunnerBriefWorkUnitResponse(BaseModel):
    id: UUID
    state: str
    version: int
    title: str
    outcome: str
    required_capability: str
    max_attempts: int


class RunnerBriefPackageResponse(BaseModel):
    id: str
    revision_id: UUID
    revision: int
    content_hash: str
    source_repository: str
    source_path: str
    source_commit: str


class RunnerBriefAuthorityResponse(BaseModel):
    fingerprint: str
    envelope: dict[str, Any]


class RunnerBriefReadinessResponse(BaseModel):
    status: str
    reasons: list[ReadinessReasonResponse]


class RunnerBriefTargetResponse(BaseModel):
    repository: str


class RunnerBriefResponse(BaseModel):
    work_unit: RunnerBriefWorkUnitResponse
    package: RunnerBriefPackageResponse
    authority: RunnerBriefAuthorityResponse
    acceptance_criteria: list["PackageAcceptanceCriterionResponse"]
    readiness: RunnerBriefReadinessResponse
    target: RunnerBriefTargetResponse
    standing_context: dict[str, Any]
    # Undeclared keys are dropped here silently, so the service returning a field
    # is not the same as a worker receiving one. factory-runner parses the HTTP
    # body, not the service dict.
    enrichment: dict[str, Any] | None = None


class LeaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: UUID
    attempt: int
    lease_token: str
    expires_at: datetime
    context_snapshot_id: UUID | None = None


class TransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_id: UUID
    state: str
    version: int
    event_id: UUID


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_revision_id: UUID
    work_unit_id: UUID
    ac_id: str
    attempt: int
    evidence_type: str
    stable_ref: str | None
    payload: dict[str, Any] | None
    source_revision: str
    recorded_by: str
    recorded_at: datetime
    event_id: UUID
    idempotency_key: str
    supersedes_evidence_id: UUID | None
    context_snapshot_id: UUID | None = None


class ContextSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_revision_id: UUID
    work_unit_id: UUID
    claim_id: UUID | None
    attempt: int
    actor_id: str
    actor_role: str
    context: dict[str, Any] | list[Any]
    context_fingerprint: str
    classification: str
    decision: str
    approval_id: UUID | None
    event_id: UUID
    idempotency_key: str
    created_at: datetime


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    occurred_at: datetime
    actor_id: str
    action: str
    subject_type: str
    subject_id: UUID
    from_state: str | None
    to_state: str | None
    payload: dict[str, Any]
    correlation_id: UUID
    idempotency_key: str


class EventPublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_system: str
    source_kind: str
    source_id: UUID
    source_action: str | None
    event_id: str
    mapping_version: str
    status: str
    skip_reason: str | None
    export_ref: str | None
    attempt_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    last_attempted_at: datetime | None
    published_at: datetime | None


class DispatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    work_package_revision_id: UUID
    runner_attempt: int
    status: str
    reason_code: str | None
    target_repository: str
    workflow_id: str
    workflow_ref: str
    github_run_id: str | None
    github_run_url: str | None
    failure_signature: str | None
    event_id: UUID | None
    created_at: datetime
    updated_at: datetime


class PrMergeAdmissionResponse(BaseModel):
    """Whether the factory may land this unit's pull request itself, and every reason it may not.

    Report-only (ADR-0020, Increment 4a). Reading this causes nothing to happen; it exists so the
    composed answer can be inspected against real completed units before anything obeys it.

    `refusals` carries EVERY term that was not met, not the first, because the question is asked
    about a unit that has already finished and the useful answer is the whole list.
    `verified_head_sha` is the head that was adjudicated -- the armed head, not the latest -- and
    is reported because it is what an act would have to name.
    """

    model_config = ConfigDict(from_attributes=True)

    satisfied: bool
    refusals: list[str]
    target_repository: str
    pr_number: int | None
    verified_head_sha: str | None
    # Always false on THIS route, and declared rather than hidden. The read surface carries no
    # override of its own (ADR-0032), so what it reports is the true statement that nothing
    # suppressed a term in the answer being read -- which is what a reader of a report needs to
    # know. A field a response model does not declare is dropped in silence, and the repo-wide
    # guard that this model answers with every field the service does is what caught the omission.
    change_window_override_applied: bool


class PrMergeCommandModel(CommandBase):
    """Ask the factory to land a unit's pull request.

    `expected_version` is REQUIRED, like every other mutation on this API -- a repo-wide invariant
    asserts it over the whole OpenAPI document, and it caught this model when it first shipped the
    field as optional. The rule earns itself here: the caller has just read an admission answer,
    and stating the version it read is what makes "nothing moved in between" the caller's claim
    rather than an assumption. The act re-evaluates every term regardless, so this is a second
    guard rather than the only one.

    `change_window_override` (ADR-0032) suppresses `merge_outside_change_window` and nothing else.
    It is this act's own: an override supplied when the run was started grants nothing here, and
    the reverse holds too. The asymmetry is the point -- the run produced a pull request that
    changed nothing outside a repository, and landing it changes what is already serving.
    """

    change_window_override: ChangeWindowOverrideModel | None = None


class PrMergeResponse(BaseModel):
    """The orchestrator's record of its own act.

    `status` is `merged` when this call landed it, `already_merged` when the pull request was
    found landed (either by somebody else, or by a previous call of ours whose response was lost),
    and `refused` otherwise. The three are distinct because a lost response and a refusal are
    indistinguishable at the remote and must not be indistinguishable here.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    repository: str
    pr_number: int
    head_sha: str
    status: str
    reason_code: str | None
    merge_commit_sha: str | None
    github_status: int | None
    event_id: UUID | None
    created_at: datetime
    updated_at: datetime


class EstatePrMergeCommandModel(BaseModel):
    """Ask the orchestrator to land a pull request that has no work unit (ADR-0019 5b).

    **It carries `expected_head_sha` where every other mutation carries `expected_version`**, and
    that is a deliberate, named exception rather than an omission. The repo-wide rule exists so a
    caller states what it read before it asks for an act; here the subject is a pull request in a
    foreign system, which has no version of ours to state. Its head is the value that moves, and
    naming it is the same claim: *nothing changed between the answer I read and the act I am
    asking for*. A version field would be a field that means nothing, which is worse than an
    exception that says why.
    """

    idempotency_key: str = Field(min_length=1, max_length=200)
    # BOUNDED IN SHAPE, because it is interpolated into GitHub API paths that are called with the
    # App installation token. An unbounded string can address paths nobody intended -- not a
    # disclosure, since only refusal codes come back, but unbounded use of a production credential
    # from a caller-supplied value, which is not a thing to leave to the good behaviour of the one
    # caller that exists.
    repository: str = Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", max_length=300)
    pr_number: int = Field(gt=0)
    # A FULL object name, not a prefix. The service compares it for equality against the head the
    # admission answer named, and GitHub serves that in full -- so a prefix could never match, and
    # admitting one would only let a caller send something that is guaranteed to be refused.
    expected_head_sha: str = Field(min_length=40, max_length=40)


class EstatePrMergeResponse(BaseModel):
    """The orchestrator's record of its own act, for a landing with no unit behind it.

    `status` carries the same three values, and for the same reason: a lost response and a refusal
    are indistinguishable at the remote and must not be indistinguishable here.

    `change_record_id` and `policy_version` are the permission, written down at the moment it was
    exercised. The standing condition behind them is re-derivable and will move.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository: str
    pr_number: int
    head_sha: str
    status: str
    reason_code: str | None
    merge_commit_sha: str | None
    github_status: int | None
    change_record_id: int | None
    policy_version: int | None
    event_id: UUID | None
    created_at: datetime
    updated_at: datetime


class EstateLandingAdmissionResponse(BaseModel):
    """Whether this pull request may be landed, and every term that is unmet.

    Every term is reported rather than the first that failed: the terms are fixed by different
    people at different times, and an operator asking why nothing landed wants the list.
    """

    repository: str
    pr_number: int
    satisfied: bool
    refusals: list[str]
    head_sha: str | None
    change_record_id: int | None
    policy_version: int | None
    # ADR-0019 Increment 6. DECLARED HERE OR IT DOES NOT EXIST ON THE WIRE: a response model drops
    # every key the service returns and the model does not name, silently and with no error, so a
    # field added to the service alone would pass every service-level assertion and reach no
    # caller. This estate has already shipped that exact defect once, on the runner brief.
    branch_update_qualifies: bool
    # ADR-0024, and it is here under the same hazard as the line above. The reporting agent
    # classifies a rollout-pin refusal by whether the BASE carries the pinned bytes -- a fact it
    # cannot observe for itself, because it reads no repository and this is the only surface that
    # could tell it. Undeclared, the answer carries the field on the service object and nothing on
    # the wire, and the agent falls back to its fail-toward-a-finding default forever.
    rollout_base_matches_pin: bool


class EstateBranchUpdateCommandModel(BaseModel):
    """Ask the orchestrator to bring a pull request's head up to date with its base (ADR-0019 6).

    **It names `expected_head_sha` for exactly the reason its sibling above does**, and the two
    exceptions to the repo-wide `expected_version` rule are one judgment rather than two: both
    subjects are pull requests in a foreign system, which have no version of ours to state, and
    for both the head is the value that moves.

    The idempotency key is load-bearing here and not decoration, which is worth saying because a
    key on an act that keeps no record of its own would be. It is content-addressed over the head
    by its caller, and a successful update CHANGES the head -- so a key can only ever bar a repeat
    of this same request against this same head, and never the next legitimate update after the
    base moves again.
    """

    idempotency_key: str = Field(min_length=1, max_length=200)
    # Bounded in shape for the reason its sibling states: it is interpolated into API paths called
    # with the App installation token, and unbounded use of a production credential from a
    # caller-supplied value is not a thing to leave to the good behaviour of one caller.
    repository: str = Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", max_length=300)
    pr_number: int = Field(gt=0)
    expected_head_sha: str = Field(min_length=40, max_length=40)


class EstateBranchUpdateResponse(BaseModel):
    """What was brought up to date, and the head it was brought up to date from.

    There is no id, no status and no row, because the act is repeatable by design: what is kept is
    an event. The head named here is the one the platform was told to expect, which is what makes
    the answer checkable against the pull request afterwards -- and it is the OLD head, since the
    platform performs the work after answering and never names the resulting one.

    `replayed` is the one field that is not decoration. Because the key is content-addressed over
    the head and a success moves the head, a replay means the branch did NOT move -- so it is the
    signal that the platform accepted the work and did not do it, which without this field would
    print as a success on every pass forever.
    """

    repository: str
    pr_number: int
    head_sha: str
    replayed: bool


class InertPrMergeCommandModel(BaseModel):
    """Ask the orchestrator to land a pull request into a repository where landing on the default
    branch changes nothing already serving (ADR-0038 part 2).

    Field for field the same shape as its sibling above, and for the same reasons: the subject is a
    pull request in a foreign system with no version of ours, so `expected_head_sha` is what a
    caller states before asking for an act; and the repository is bounded in SHAPE because it is
    interpolated into GitHub API paths called with the App installation token.
    """

    idempotency_key: str = Field(min_length=1, max_length=200)
    repository: str = Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", max_length=300)
    pr_number: int = Field(gt=0)
    # A FULL object name, not a prefix: the service compares it for equality against the head the
    # admission answer named, so a prefix could never match.
    expected_head_sha: str = Field(min_length=40, max_length=40)


class InertPrMergeResponse(BaseModel):
    """The orchestrator's record of its own act, for a landing with no unit and no change record.

    It reads the SAME table its sibling writes -- the two populations cannot overlap, because each
    lane requires the opposite answer from the estate about a repository and the estate gives one
    answer per repository. So `change_record_id` is declared and is always null here: withholding
    the field would make one table answer two shapes, and a reader comparing rows would have to
    know which route produced each.

    `policy_version` is the whole of the permission, written down at the moment it was exercised.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository: str
    pr_number: int
    head_sha: str
    status: str
    reason_code: str | None
    merge_commit_sha: str | None
    github_status: int | None
    change_record_id: int | None
    policy_version: int | None
    event_id: UUID | None
    created_at: datetime
    updated_at: datetime


class InertLandingAdmissionResponse(BaseModel):
    """Whether this pull request may be landed into the declared inert population, and every term
    that is unmet.

    Every term is reported rather than the first that failed: the terms are fixed by different
    people at different times, and an operator asking why nothing landed wants the list.

    **`branch_update_qualifies` MUST BE DECLARED HERE OR IT DOES NOT EXIST ON THE WIRE.** A
    response model drops every key the service returns and the model does not name, silently and
    with no error -- so a field added to the service alone passes every service-level assertion and
    reaches no caller. This estate has shipped that exact defect twice: once on the runner brief,
    and once on this field's own sibling, where the enumerating agent read a key that was not there
    and skipped every record for two days while reporting zero.

    There is no `change_record_id` and no `rollout_base_matches_pin`: this lane has no record, and
    it evaluates no rollout pin, so a field for either would be a column of nulls that a reader
    would reasonably take to mean something.
    """

    repository: str
    pr_number: int
    satisfied: bool
    refusals: list[str]
    head_sha: str | None
    policy_version: int | None
    branch_update_qualifies: bool


class InertBranchUpdateCommandModel(BaseModel):
    """Ask the orchestrator to bring an inert-population pull request's head up to date with its
    base (ADR-0038 part 2).

    The idempotency key is load-bearing and not decoration, which is worth saying because a key on
    an act that keeps no record of its own would be. It is content-addressed over the head by its
    caller, and a successful update CHANGES the head -- so a key can only ever bar a repeat of this
    same request against this same head, and never the next legitimate update after the base moves
    again.
    """

    idempotency_key: str = Field(min_length=1, max_length=200)
    repository: str = Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", max_length=300)
    pr_number: int = Field(gt=0)
    expected_head_sha: str = Field(min_length=40, max_length=40)


class InertBranchUpdateResponse(BaseModel):
    """What was brought up to date, and the head it was brought up to date from.

    There is no id, no status and no row, because the act is repeatable by design: what is kept is
    an event. The head named here is the OLD one, since the platform performs the work after
    answering and never names the resulting one.

    `replayed` is the one field that is not decoration. Because the key is content-addressed over
    the head and a success moves the head, a replay means the branch did NOT move -- so it is the
    signal that the platform accepted the work and did not do it, which without this field would
    print as a success on every pass forever.
    """

    repository: str
    pr_number: int
    head_sha: str
    replayed: bool


class InfraLaneLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    work_package_revision_id: UUID
    attempt: int
    status: str
    change_manager_ref: str
    change_manager_url: str | None
    infraops_ref: str | None
    approval_ref: str | None
    rollback_ref: str | None
    verify_ref: str | None
    final_evidence_ref: str | None
    payload: dict[str, Any]
    recorded_by: str
    recorded_at: datetime
    event_id: UUID
    idempotency_key: str


class ReleaseArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    work_package_revision_id: UUID
    package_revision_hash: str
    source_repository: str
    implementation_pr_number: int | None
    source_commit: str
    merge_commit: str
    kind: str
    artifact_registry: str | None
    artifact_repository: str | None
    artifact_name: str | None
    artifact_digest: str
    artifact_tag: str | None
    workflow_run_id: str | None
    workflow_run_attempt: int | None
    workflow_path: str | None
    workflow_ref: str | None
    workflow_run_url: str | None
    builder_id: str | None
    builder_class: str | None
    provenance_ref: str | None
    provenance_digest: str | None
    sbom_ref: str | None
    sbom_digest: str | None
    summary: dict[str, Any]
    recorded_by: str
    recorded_at: datetime
    event_id: UUID
    evidence_id: UUID
    idempotency_key: str


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


class DeploymentObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    release_artifact_binding_id: UUID
    implementation_work_unit_id: UUID
    work_package_revision_id: UUID
    package_revision_hash: str
    kind: str
    post_deploy_work_unit_id: UUID | None
    environment: str
    base_url: str | None
    observed_artifact_digest: str
    deployment_ref: str
    deployment_url: str | None
    deployer: str | None
    observed_at: datetime
    probe_summary: dict[str, Any]
    route_summary: dict[str, Any]
    auth_summary: dict[str, Any]
    dispatch_summary: dict[str, Any]
    status_summary: dict[str, Any]
    activation_summary: dict[str, Any]
    recorded_by: str
    recorded_at: datetime
    event_id: UUID
    post_deploy_event_id: UUID | None
    evidence_ids: list[str]
    idempotency_key: str


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


class KnowledgePromotionProposalActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    proposal_id: UUID
    action: str
    brain_record_id: str | None
    brain_status: str | None
    brain_response: dict[str, Any] | None
    reason: str | None
    action_by: str
    action_at: datetime
    event_id: UUID
    idempotency_key: str


class KnowledgePromotionProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    correlation_identity: str
    source_observation_ids: list[str]
    source_observation_hashes: list[str]
    release_artifact_binding_id: UUID | None
    deployment_observation_id: UUID | None
    work_unit_id: UUID | None
    package_revision_id: UUID | None
    correlation_summary: str
    target_brain: str
    target_type: str
    authority: str
    applicability: dict[str, Any]
    proposed_payload: dict[str, Any]
    provenance: dict[str, Any]
    proposal_hash: str
    proposed_by: str
    proposed_at: datetime
    event_id: UUID
    idempotency_key: str
    state: str | None = None
    actions: list[KnowledgePromotionProposalActionResponse] = Field(default_factory=list)


class EventPublicationQueueCommand(CommandBase):
    source_kind: str | None = None
    source_id: UUID | None = None


class EventPublicationExportCommand(CommandBase):
    output_path: str = Field(min_length=1)


class EventPublicationRetryCommand(CommandBase):
    pass


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_type: str
    subject_id: UUID
    subject_revision_or_fingerprint: str
    approved_by: str
    reason: str


class AdjudicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_revision_id: UUID
    work_unit_id: UUID
    ac_id: str
    outcome: str
    decided_by: str
    rationale: str


class VerifyEvaluationResponse(BaseModel):
    ac_id: str
    evidence_type: str
    status: str
    outcome: str | None
    evidence_id: UUID | None
    finding_evidence_id: UUID | None
    adjudication_id: UUID | None
    reason: str


class VerifyResponse(BaseModel):
    unit_id: UUID
    state: str
    version: int
    result: str
    evaluations: tuple[VerifyEvaluationResponse, ...]


class StatusLedgerEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ac_id: str
    attempt: int
    evidence_type: str
    stable_ref: str | None
    source_revision: str
    recorded_by: str
    recorded_at: datetime
    context_snapshot_id: UUID | None


class StatusLedgerAdjudicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ac_id: str
    outcome: str
    decided_by: str
    decided_at: datetime
    evidence_id: UUID | None
    rationale: str


class StatusLedgerFailureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    actor_id: str
    occurred_at: datetime
    from_state: str | None
    reason: str | None


class MetricValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    value: float | None
    basis: str


class SloReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    since: datetime
    until: datetime
    intake_to_first_work: MetricValueResponse
    queue_age: MetricValueResponse
    claim_expiry_rate: MetricValueResponse
    waiver_frequency: MetricValueResponse
    revert_rate: MetricValueResponse
    evidence_completeness: MetricValueResponse
    cost_per_unit: MetricValueResponse
    token_consumption: MetricValueResponse
    improvisation: MetricValueResponse
    budget_breach: MetricValueResponse


class StatusLedgerRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor_id: str | None
    unit_id: UUID
    unit_key: str
    unit_title: str
    unit_state: str
    claim_id: UUID | None
    claim_attempt: int | None
    claim_lease_expires_at: datetime | None
    claim_released_at: datetime | None
    claim_terminal_reason: str | None
    last_heartbeat_at: datetime | None
    last_event_at: datetime | None
    blockers: list[dict[str, Any | None]]
    pending_human_approvals: list[dict[str, Any]]
    latest_evidence: StatusLedgerEvidenceResponse | None
    latest_adjudication: StatusLedgerAdjudicationResponse | None
    last_failure: StatusLedgerFailureResponse | None
    context_snapshot_id: UUID | None
    context_classification: str | None
    context_decision: str | None


class DependencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    kind: str
    required_state_or_condition: str
    status: str


class PackageAcceptanceCriterionCommand(BaseModel):
    ac_id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    approver: str = Field(min_length=1)


class PackageIntakeRegistration(CommandBase):
    package_id: str = Field(min_length=1)
    source_repository: str = Field(min_length=1)
    revision: int = Field(gt=0)
    content_hash: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_commit: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_at: datetime
    approval_event_id: str = Field(min_length=1)
    approval_ledger_commit: str = Field(min_length=1)
    profile: str | None = None
    status_at_intake: str = Field(min_length=1)
    verification_mode: str = Field(min_length=1)
    verification_limitations: dict[str, Any] | list[Any] | None = None
    enforcement_snapshot: dict[str, Any]
    authority: dict[str, Any]
    registry_version: int = Field(ge=0)
    acceptance_criteria: list[PackageAcceptanceCriterionCommand] = Field(min_length=1)
    intake_purpose: Literal["executable", "protocol_fixture"] = "executable"
    follow_up: dict[str, Any] | None = None
    # ADR-0026: the change-manager record a human approved to cause this work. Bounded by int4
    # because the column is an Integer, and `strict` because pydantic's lax mode reads `true`
    # as 1 -- which would attribute a revision to change record 1 rather than refusing.
    change_record_id: int | None = Field(default=None, gt=0, le=2_147_483_647, strict=True)


class PackageAcceptanceCriterionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ac_id: str
    condition: str
    evidence_type: str
    evidence: str
    approver: str


class PackageIntakeResponse(BaseModel):
    id: UUID
    change_record_id: int | None = None
    package_id: str
    source_repository: str
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    approved_at: datetime
    approval_event_id: str
    approval_ledger_commit: str | None
    profile: str | None
    status_at_intake: str | None
    intake_source: str
    verification_mode: str | None
    verification_limitations: dict[str, Any] | list[Any] | None
    enforcement_snapshot: dict[str, Any]
    authority_fingerprint: str
    authority: dict[str, Any] | None
    follow_up: dict[str, Any] | None
    registry_version: int
    registered_by: str
    registered_at: datetime
    acceptance_criteria: list[PackageAcceptanceCriterionResponse]


class ProposedUnitCommand(BaseModel):
    unit_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    required_capability: str = Field(min_length=1)
    authority: dict[str, Any]
    context_enrichment: dict[str, Any] | None = None
    max_attempts: int = Field(ge=0, default=3)


class ProposedDependencyCommand(BaseModel):
    source_unit_key: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    required_state_or_condition: str = Field(min_length=1)
    target_unit_key: str | None = None
    external_ref: str | None = None


class AcMappingCommandModel(BaseModel):
    ac_id: str = Field(min_length=1)
    unit_key: str = Field(min_length=1)


class RetainedAcCommandModel(BaseModel):
    ac_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class DecompositionProposalRegistration(CommandBase):
    rationale: str = Field(min_length=1)
    proposed_units: list[ProposedUnitCommand] = Field(min_length=1)
    dependencies: list[ProposedDependencyCommand] = Field(default_factory=list)
    ac_mappings: list[AcMappingCommandModel] = Field(default_factory=list)
    retained_acs: list[RetainedAcCommandModel] = Field(default_factory=list)


class DecompositionDecisionCommand(CommandBase):
    reason: str = Field(min_length=1)


class DecompositionProposalUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_key: str
    title: str
    outcome: str
    required_capability: str
    authority: dict[str, Any]
    authority_fingerprint: str
    context_enrichment: dict[str, Any] | None
    max_attempts: int


class DecompositionProposalDependencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_unit_key: str
    kind: str
    target_unit_key: str | None
    external_ref: str | None
    required_state_or_condition: str


class DecompositionProposalAcMappingResponse(BaseModel):
    unit_key: str
    package_acceptance_criterion: PackageAcceptanceCriterionResponse


class DecompositionProposalRetainedAcResponse(BaseModel):
    rationale: str
    package_acceptance_criterion: PackageAcceptanceCriterionResponse


class DecompositionProposalResponse(BaseModel):
    id: UUID
    work_package_revision_id: UUID
    proposal_number: int
    state: str
    rationale: str
    proposed_by: str
    proposed_actor_role: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    created_work_unit_ids: dict[str, str] | None
    proposed_units: list[DecompositionProposalUnitResponse]
    dependencies: list[DecompositionProposalDependencyResponse]
    ac_mappings: list[DecompositionProposalAcMappingResponse]
    retained_acs: list[DecompositionProposalRetainedAcResponse]


class RecoverEvidenceCommand(CommandBase):
    """Note there is NO lease_token: the whole scenario is that the lease is gone."""

    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    source_revision: str = Field(min_length=1)


class ReconciliationDetectCommand(CommandBase):
    """The detect-pass carries the same idempotency contract as every other /api/v1 mutation.

    Its conditions dedup on the divergence hash regardless of this key, so a duplicate delivery
    surfaces as `suppressed_duplicates` rather than a second row -- but the uniform contract is
    not something a write path gets to opt out of.
    """


class ReconciliationDetectResponse(BaseModel):
    """Counters, not just a status. Fail-open is counted, so a miss is observable."""

    conditions_recorded: int
    skipped_correlations: int
    suppressed_duplicates: int


class TrackerReconciliationDetectItem(BaseModel):
    """One bound tracker item's observed completion state, reported by the adapter.

    Normalized state only -- never card text. The orchestrator owns the divergence rule; this
    carries no interpretation.
    """

    tracker_system: str = Field(min_length=1)
    external_item_id: str = Field(min_length=1)
    observed_completed: bool


class TrackerReconciliationDetectCommand(CommandBase):
    """Inbound tracker reconciliation: a batch of observed item states. Conditions dedup on the
    divergence hash regardless of the idempotency key, so a duplicate delivery surfaces as
    suppressed_duplicates rather than a second row."""

    observed_states: list[TrackerReconciliationDetectItem]


class FollowUpMintCommand(CommandBase):
    """One minting pass. It has no single subject, so `expected_version` carries no meaning here
    and only 0 is accepted -- the same contract the observation ingress uses. Per-unit
    idempotency is structural: the unit id is content-addressed from the revision id, so a
    re-run under a fresh key still mints nothing new."""


class MintedFollowUpResponse(BaseModel):
    work_unit_id: UUID
    work_package_revision_id: UUID
    due_at: datetime


class SkippedRevisionResponse(BaseModel):
    work_package_revision_id: UUID
    # A second copy of the service's skip-reason strings, because `Literal` needs literals and
    # cannot be built from constants. Kept honest by a sync test rather than by hope --
    # see test_the_response_vocabulary_matches_the_services_skip_reasons.
    reason: Literal[
        "not_required",
        "no_completed_unit",
        "units_in_flight",
        "unsettled_failed_unit",
        "not_yet_due",
        "already_minted",
        "declaration_malformed",
        "reach_undeclared",
    ]


class FollowUpMintResponse(BaseModel):
    """Counters and reasons, not just a status. A skip is counted so a miss is observable."""

    minted: list[MintedFollowUpResponse]
    skipped: list[SkippedRevisionResponse]
    considered: int


class DeadLetterEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    work_unit_id: UUID
    unit_key: str
    unit_state: str
    reason_code: str | None
    detail: str | None
    attempt_count: int
    max_attempts: int
    requeue_eligible: bool
    occurred_at: datetime | None


class RequeueCommand(CommandBase):
    reason: str = Field(min_length=1)


class PrBindingCommand(CommandBase):
    """The worker reporting the pull request it opened, and its current head.

    `attempt` and `lease_token` are how the worker proves it holds this unit's claim -- the same
    proof recording evidence demands. Without it, any worker could rewrite any unit's expected
    head, and the expected head is the only thing divergence is measured against.
    """

    pr_number: int = Field(gt=0)
    head_sha: str = Field(min_length=1)
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)


class PrBindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    pr_number: int
    head_sha: str
    verification_read_head_sha: str | None
    verification_read_attempt: int | None


class TrackerBindingCommand(CommandBase):
    """A tracker-projection adapter recording the external item a unit is mirrored onto.

    Projection only, like pr-binding: it never derives from tracker content and never changes
    the unit's lifecycle state, so it carries no claim proof -- only the SYSTEM actor may write.
    """

    tracker_system: str
    external_item_id: str = Field(min_length=1)
    external_url: str | None = None
    projected_state: str = Field(min_length=1)


class TrackerBindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    tracker_system: str
    external_item_id: str
    external_url: str | None
    projected_state: str
    updated_at: datetime


class CostActualsCommand(CommandBase):
    """A runner reporting the actual LLM cost of one work-unit attempt.

    Carries `expected_version` (required 0, like pr-binding): cost-actuals appends an
    attempt.cost_recorded event and never targets the unit's version, so the route asserts
    expected_version == 0 as the same uniformity marker every worker write uses rather than
    exempting this path from the repo-wide "every mutation carries expected_version" invariant.
    `attempt` + `lease_token` prove the caller holds this unit's live claim, exactly as evidence
    and pr-binding demand. When `cost_known` is False (a failed attempt left no usable
    transcript) every numeric is null -- the cost is honestly absent, never a fabricated zero.
    """

    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)
    cost_known: bool
    llm_calls: int | None = Field(default=None, ge=0)
    num_turns: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _numerics_match_cost_known(self) -> "CostActualsCommand":
        numerics = (
            self.llm_calls,
            self.num_turns,
            self.input_tokens,
            self.output_tokens,
            self.cost_usd,
        )
        if self.cost_known and any(value is None for value in numerics):
            raise ValueError("cost_known is true but a numeric field is null")
        if not self.cost_known and any(value is not None for value in numerics):
            raise ValueError("cost_known is false but a numeric field is non-null")
        return self


class CostActualsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    attempt: int
    event_id: UUID
    cost_known: bool


class FactoryPolicyKnownGoodResponse(BaseModel):
    """One declared known-good pattern, in full.

    Everything the matcher reads is served, because an operator asking what this process enforces
    needs to be able to answer "would it recognise THIS envelope" without reading the image. Every
    field NARROWS what is recognised, so none of them reads as a permission.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str
    rationale: str
    decided: date
    change_class: str
    capabilities: dict[str, str]
    max_attempts: int
    max_llm_calls: int
    conformance_status: str
    target_repositories: list[str]
    command_prefixes: list[str]


class FactoryPolicyChangeWindowResponse(BaseModel):
    """The hours in which policy raises no objection to work of this reach starting.

    ``null`` for a row that declares none, which is this policy having no objection on those
    grounds -- never a window of zero length and never a default. Served in the local terms it was
    written in, zone included: an offset would be true for only half the year, and the reason the
    zone is in the artifact at all is that the question is about somebody's day.
    """

    model_config = ConfigDict(from_attributes=True)

    rationale: str
    decided: date
    timezone: str
    start: str
    end: str


class FactoryPolicyLeaseResponse(BaseModel):
    """How much longer than the default this orchestrator refuses to reassign work of this reach.

    ``null`` for a row that declares none, which means the build's default hold applies -- never a
    row with no lease, because every claim has one. The default and the ceiling that bounds what a
    row may declare are served at the top level, so ``null`` can be read without the image.
    """

    model_config = ConfigDict(from_attributes=True)

    rationale: str
    decided: date
    minutes: int


class FactoryPolicyLeaseBoundsResponse(BaseModel):
    """The two numbers the build owns, between which a declared lease must fall.

    Served because they are what makes a row's ``lease: null`` legible, and because they are the
    whole of why a duration in this document cannot widen anything: no value between them shortens
    a hold, and none of them switches reassignment off.
    """

    model_config = ConfigDict(from_attributes=True)

    default_minutes: int
    ceiling_minutes: int


class FactoryPolicyReachResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    member: str
    rationale: str
    decided: date
    known_good: list[FactoryPolicyKnownGoodResponse]
    change_window: FactoryPolicyChangeWindowResponse | None
    lease: FactoryPolicyLeaseResponse | None


class FactoryPolicyGrandfatheringResponse(BaseModel):
    """The revisions exempt from having to declare reach, in full.

    Not a count. This is a temporary exemption from a rule everything else is held to, and the
    operator question is which records it still covers and whether it can be deleted yet.
    """

    model_config = ConfigDict(from_attributes=True)

    rationale: str
    decided: date
    revisions: list[str]


class FactoryPolicyResponse(BaseModel):
    """What policy the running process is enforcing.

    Deliberately carries no permission of any kind: the artifact answers only in refusals, so a
    field here that read as "allowed" would be the one shape this schema must never grow.
    """

    model_config = ConfigDict(from_attributes=True)

    version: int
    source: str
    # A response model silently DROPS every key the service returns and the model does not declare,
    # which is how WS-P2.12 served an empty enrichment while every service assertion passed.
    lease_bounds: FactoryPolicyLeaseBoundsResponse
    grandfathered: FactoryPolicyGrandfatheringResponse | None
    reach: list[FactoryPolicyReachResponse]


class ConsistencyFindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    check: str
    work_unit_id: UUID | None
    subject: str
    detail: str
    observed: str
    expected: str


class ConsistencyReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    checked_at: datetime
    divergent: bool
    findings: list[ConsistencyFindingResponse]


class InFlightUnitModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    unit_key: str
    state: str
    version: int
    attempt_count: int
    work_package_revision_id: UUID
    pr_number: int | None
    head_sha: str | None
    verification_read_head_sha: str | None


class ReleaseBindingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    binding_id: UUID
    work_unit_id: UUID
    work_unit_state: str
    source_repository: str
    artifact_digest: str
    has_post_deploy_unit: bool
    post_deploy_unit_state: str | None
    post_deploy_unit_created_at: datetime | None


class InFlightUnitsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    units: list[InFlightUnitModel]
    release_bindings: list[ReleaseBindingModel]


class EvidencePackWorkUnitResponse(BaseModel):
    """WS-P2.5: the subset of a work unit the evidence pack keys everything else against."""

    id: UUID
    title: str
    state: str
    authority_fingerprint: str


class EvidencePackProvenanceResponse(BaseModel):
    """The canonical package-revision facts a reviewer checks first: what was actually built."""

    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    registered_by: str


class EvidencePackAuthorityViolationResponse(BaseModel):
    code: str
    message: str
    remediation: str | None = None


class EvidencePackAuthorityResponse(BaseModel):
    authority_fingerprint: str
    envelope: dict[str, Any]
    authority_violation: EvidencePackAuthorityViolationResponse | None = None


class EvidencePackDependencyResponse(BaseModel):
    kind: str
    required_state_or_condition: str
    status: str


class EvidencePackClaimResponse(BaseModel):
    attempt: int
    claimed_by: str
    lease_expires_at: datetime
    terminal_reason: str | None = None


class EvidencePackEvidenceResponse(BaseModel):
    """One AC-keyed evidence record. `supersedes` chains to a prior entry's `id`."""

    id: UUID
    ac_id: str
    current: bool
    evidence_type: str
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    supersedes: UUID | None = None


class EvidencePackAdjudicationResponse(BaseModel):
    """One AC-keyed adjudication. Waiver fields are populated only when `outcome == "waived"`."""

    id: UUID
    ac_id: str
    outcome: str
    current: bool
    decided_by: str
    # WS-P3.7. The KIND of actor that decided, as a stored fact. NULL on every row written before
    # the column existed, and NULL means *unknown* -- a consumer must never read it as "not human".
    decided_by_role: str | None = None
    # The evidence the decision was recorded against. `failed_evidence_id` below is the waiver
    # field and answers a different question; only it was projected before.
    evidence_id: UUID | None = None
    rationale: str
    risk: str | None = None
    follow_up: str | None = None
    scope: str | None = None
    expires_at: datetime | None = None
    failed_evidence_id: UUID | None = None


class EvidencePackCriterionRefusalResponse(BaseModel):
    """One reason the unit does not qualify. `ac_id` is null when the reason is unit-wide."""

    ac_id: str | None = None
    code: str


class EvidencePackVerifierDecidedResponse(BaseModel):
    """Whether every required acceptance criterion of this unit reached a current terminal
    adjudication that the verifier recorded from its own evaluation of evidence.

    Computed once, in `services/lifecycle.py`, and served here so an off-process consumer can read
    the answer without parsing `/history` for an opaque event payload. Fails closed in every
    direction: an unrecorded decider kind, a criterion with no single current adjudication, a
    waiver, or a revision that declares no usable criteria all make `satisfied` false and name
    themselves in `refusals`.
    """

    satisfied: bool
    # ADR-0020's sentence, as its two clauses. `decided_by_verifier` is "with no human
    # adjudication"; `evidence_observed` is "from observed evidence". Served separately because a
    # criterion can fail either one alone, and an off-process consumer that can only read the AND
    # cannot tell which -- which is the whole reason Increment 1 made the condition readable.
    decided_by_verifier: bool
    evidence_observed: bool
    refusals: list[EvidencePackCriterionRefusalResponse]


class EvidencePackApprovalResponse(BaseModel):
    subject_type: str
    decision: str
    approved_by: str
    reason: str


class EvidencePackEventPublicationResponse(BaseModel):
    source_ref: str
    status: str
    event_id: str
    export_ref: str | None = None
    last_error: str | None = None


class EvidencePackEventResponse(BaseModel):
    """One event, projected. A key this model does not declare is silently dropped, so a payload
    field a reader needs has to be named here as well as written there."""

    occurred_at: datetime
    action: str
    actor_id: str
    from_state: str | None = None
    to_state: str | None = None
    reason: str | None = None
    # ADR-0032, on the two acts that can carry one. Full fidelity in this JSON, which is
    # authenticated; the markdown renderer relays onto a possibly-public pull request comment and
    # deliberately does not interpolate the operator's words.
    change_window_override: dict[str, Any] | None = None


class EvidencePackResponse(BaseModel):
    """A single work unit's full evidentiary record, structured for programmatic consumption.

    Mirrors the field set of the `/review` evidence-pack HTML page (`templates/evidence_pack.html`)
    exactly, but as JSON any authenticated caller can read -- including the runner's WORKER
    credential, which has no role gate on this route. Field names are chosen so a per-release pack
    (WS-P2.5 Increment 2) can nest a `list[EvidencePackResponse]` without renaming anything here.
    """

    work_unit: EvidencePackWorkUnitResponse
    provenance: EvidencePackProvenanceResponse
    authority: EvidencePackAuthorityResponse
    dependencies: list[EvidencePackDependencyResponse]
    claims: list[EvidencePackClaimResponse]
    evidence: list[EvidencePackEvidenceResponse]
    adjudications: list[EvidencePackAdjudicationResponse]
    verifier_decided_completion: EvidencePackVerifierDecidedResponse
    approvals: list[EvidencePackApprovalResponse]
    event_publications: list[EvidencePackEventPublicationResponse]
    events: list[EvidencePackEventResponse]


class ReleaseEvidencePackRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_id: UUID
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    registered_by: str


class ReleaseEvidencePackResponse(BaseModel):
    revision: ReleaseEvidencePackRevisionResponse
    units: list[EvidencePackResponse]
    release_artifacts: list[ReleaseArtifactResponse]
    deployments: list[DeploymentObservationResponse]


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
    # link is stored -- an observation would not do, because the observation hop filters on
    # `subject_type="work_unit"`, so a revision-scoped observation never reaches any chain.
    change_record_id: int | None = None


class TraceabilityUnitHop(BaseModel):
    id: UUID
    unit_key: str
    title: str
    state: str
    authority_fingerprint: str
    authority_approved_by: str | None = None
    authority_decision: str | None = None


class TraceabilityPrHop(BaseModel):
    pr_number: int
    head_sha: str


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


class ChangeRecordUnitResponse(BaseModel):
    """One unit the change record caused, and the state the verdict below was computed from."""

    unit_id: UUID
    unit_key: str
    revision_id: UUID
    state: str


class ChangeRecordWorkResponse(BaseModel):
    """What a change record caused, and whether it is done (ADR-0029).

    `all_units_completed` is named for the narrow rule rather than for anything that reads as a
    synonym for "settled": it is true when there is at least one unit and every one of them is
    `completed`, and false for every other shape including a record nothing has carried yet.

    The units are served ALONGSIDE the verdict rather than instead of it. A response model drops
    every key it does not declare, so a consumer reading a field this model omits gets silence --
    which is why the evidence for the verdict has to be declared here to travel at all.
    """

    change_record_id: int
    revision_ids: list[UUID]
    units: list[ChangeRecordUnitResponse]
    all_units_completed: bool
