from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from orchestrator.api.schemas.common import ChangeWindowOverrideModel, CommandBase


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
    # ADR-0045, DECLARED HERE OR IT DOES NOT EXIST ON THE WIRE. True ONLY when the branch update
    # would be withheld for a sibling: this pull request qualifies, is the update bot's, is still
    # the update bot's own, and another of the update bot's pull requests here was positively seen
    # edited by this lane and still queued to land. FALSE when the siblings could not be read, so
    # a failed scan never produces a quiet line -- the act meets the same failure and refuses with a
    # code of its own, which stays a finding. It never co-occurs with a failing check, which
    # disqualifies the update outright. It is a fact about an observed sibling, NOT a record of the
    # lane declining. A scan that fails leaves this answer answering, with the fact false.
    branch_update_withheld_for_sibling: bool


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

    `merge_method` is served for the same reason every other field here is -- the answer is served
    WHOLE, and a field the service composes but the model omits is the silent drop this docstring
    opens by naming. It also gives a report-only pass the one thing it could not otherwise say
    about a subject it declines to act on: HOW the landing would be performed.
    """

    repository: str
    pr_number: int
    satisfied: bool
    refusals: list[str]
    head_sha: str | None
    policy_version: int | None
    branch_update_qualifies: bool
    merge_method: str
    # ADR-0045, DECLARED HERE OR IT DOES NOT EXIST ON THE WIRE. True ONLY when the branch update
    # would be withheld for a sibling: this pull request qualifies, is the update bot's, is still
    # the update bot's own, and another of the update bot's pull requests here was positively seen
    # edited by this lane and still queued to land. FALSE when the siblings could not be read, so
    # a failed scan never produces a quiet line -- the act meets the same failure and refuses with a
    # code of its own, which stays a finding. It never co-occurs with a failing check, which
    # disqualifies the update outright. It is a fact about an observed sibling, NOT a record of the
    # lane declining. A scan that fails leaves this answer answering, with the fact false.
    branch_update_withheld_for_sibling: bool


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
