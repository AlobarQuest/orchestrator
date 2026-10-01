from dataclasses import replace
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from orchestrator.api.dependencies import ActorDep, SessionDep, SettingsDep
from orchestrator.api.routes.common import (
    ERROR_RESPONSES,
    LandingSourceDep,
    github_app_credentials_for,
    to_change_window_override,
)
from orchestrator.api.schemas.landing import (
    EstateBranchUpdateCommandModel,
    EstateBranchUpdateResponse,
    EstateLandingAdmissionResponse,
    EstatePrMergeCommandModel,
    EstatePrMergeResponse,
    InertBranchUpdateCommandModel,
    InertBranchUpdateResponse,
    InertLandingAdmissionResponse,
    InertPrMergeCommandModel,
    InertPrMergeResponse,
    PrMergeAdmissionResponse,
    PrMergeCommandModel,
    PrMergeResponse,
)
from orchestrator.services.github_app import token_provider_for
from orchestrator.services.landing.branch_update_serialization import (
    branch_update_sibling_outcome,
    estate_sibling_composer,
    inert_sibling_composer,
    withheld_for_sibling,
)
from orchestrator.services.landing.change_record import ChangeRecordSource, HttpChangeRecordSource
from orchestrator.services.landing.estate_landing_admission import estate_landing_admission
from orchestrator.services.landing.estate_pr_branch_update import (
    EstateBranchUpdateCommand,
    update_estate_pull_request_branch,
)
from orchestrator.services.landing.estate_pr_merge import (
    EstateMergeCommand,
    GitHubEstatePullRequests,
    land_estate_pull_request,
)
from orchestrator.services.landing.inert_landing_admission import inert_landing_admission
from orchestrator.services.landing.inert_landing_policy import (
    HttpInertLandingPolicySource,
    InertLandingPolicySource,
)
from orchestrator.services.landing.inert_pr_branch_update import (
    InertBranchUpdateCommand,
    update_inert_pull_request_branch,
)
from orchestrator.services.landing.inert_pr_merge import (
    GitHubInertPullRequests,
    InertMergeCommand,
    land_inert_pull_request,
)
from orchestrator.services.landing.pr_merge import (
    GitHubPullRequests,
    MergeCommand,
    land_unit_pull_request,
)
from orchestrator.services.landing.pr_merge_admission import pr_merge_admission

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


# change-manager's own name for the pipeline that proposed changes arrive on -- the source of
# truth is `DEPLOY_SOURCE` in AlobarQuest/change-manager `app/sources.py`, and its listing route
# returns nothing from that pipeline unless it is named. It is resolved HERE rather than in the
# service, alongside the base URL and the credential, because this is where this deployment's
# cross-boundary configuration is read. It is a constant rather than a setting: an operator who
# could change it could only ever make the question unanswerable.
CHANGE_RECORD_PIPELINE: Final = "deploy"


def get_change_record_source(settings: SettingsDep) -> ChangeRecordSource:
    """Build the thing that asks the estate whether this change was routed and approved.

    A dependency for the reason the estate answer's reader is one: a test can substitute
    change-manager, and an unset URL or credential arrives as the empty string the source itself
    refuses on -- never as a source that silently answers.
    """
    return HttpChangeRecordSource(
        base_url=settings.change_record_url,
        token=(
            settings.change_record_token.get_secret_value() if settings.change_record_token else ""
        ),
        pipeline=CHANGE_RECORD_PIPELINE,
        timeout_seconds=settings.change_record_timeout_seconds,
    )


ChangeRecordSourceDep = Annotated[ChangeRecordSource, Depends(get_change_record_source)]


def get_inert_landing_policy_source(settings: SettingsDep) -> InertLandingPolicySource:
    """Build the thing that reads which repositories a person declared landable unattended.

    **The SAME base URL and the SAME credential as the change-record reader above**, because it is
    the same service and the same bearer: every narrow scope change-manager grants is its read
    routes plus whatever that scope may write, and this route is one of those read routes -- so a
    credential that can read a change record can read this. Sharing them means activating the lane
    costs one environment variable rather than a second secret whose absence would be
    indistinguishable from the service being down.

    A dependency for the reason its two siblings are: a test can substitute the service, and an
    unset URL or credential arrives as the empty string the source itself refuses on, never as a
    source that silently answers.
    """
    return HttpInertLandingPolicySource(
        base_url=settings.change_record_url,
        token=(
            settings.change_record_token.get_secret_value() if settings.change_record_token else ""
        ),
        timeout_seconds=settings.change_record_timeout_seconds,
    )


InertLandingPolicySourceDep = Annotated[
    InertLandingPolicySource, Depends(get_inert_landing_policy_source)
]


@router.get(
    "/work-units/{unit_id}/pr-merge-admission",
    response_model=PrMergeAdmissionResponse,
)
def pr_merge_admission_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0020 Increment 4a: may the factory land this unit's pull request, and if not, why?

    **Report-only. Nothing here acts, and nothing that acts exists yet.** It is served so the
    composed answer can be read against real completed units before anything obeys it.

    Authentication-only, no role gate, matching the evidence-pack routes: it is a read over
    canonical rows plus one read-only question to the estate, and every actor that can read a
    unit's evidentiary record can read this.
    """
    return pr_merge_admission(session, unit_id, landing_source, record_source)


@router.get("/estate-pr-merge-admission", response_model=EstateLandingAdmissionResponse)
def estate_landing_admission_route(
    repository: Annotated[str, Query(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", max_length=300)],
    pr_number: Annotated[int, Query(gt=0)],
    _actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0019 Increment 5b: may this pull request be landed, and if not, why?

    **Report-only.** It is what makes a night that lands nothing legible: every held pull request
    names the condition it misses, and a first pass that reports four held for freshness is the
    condition working rather than the lane failing.

    The credentials are resolved once and fed to both this answer and the act, so the gate can
    never attest to credentials the actor does not hold.
    """
    credentials = github_app_credentials_for(settings)
    gateway = GitHubEstatePullRequests(token_provider_for(credentials))
    enabled = settings.estate_landing_enabled
    credentials_configured = credentials is not None
    admission = estate_landing_admission(
        session,
        repository,
        pr_number,
        landing_source,
        record_source,
        gateway,
        enabled=enabled,
        credentials_configured=credentials_configured,
    )
    # ADR-0045. Filled in HERE, because the admission itself must never scan siblings: finding out
    # composes each sibling's own answer, which would otherwise ask the same of its siblings.
    withheld = withheld_for_sibling(
        qualifies=admission.branch_update_qualifies,
        outcome=lambda: branch_update_sibling_outcome(
            session,
            repository=admission.repository,
            target_number=admission.pr_number,
            gateway=gateway,
            compose=estate_sibling_composer(
                session,
                admission.repository,
                landing_source,
                record_source,
                gateway,
                enabled=enabled,
                credentials_configured=credentials_configured,
            ),
        ),
    )
    return replace(admission, branch_update_withheld_for_sibling=withheld)


@router.post("/estate-pr-merge", response_model=EstatePrMergeResponse)
def estate_pr_merge_route(
    body: EstatePrMergeCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0019 Increment 5b: the orchestrator lands a pull request that has no work unit.

    Its caller is a scheduled one, which is why this path has an off-switch where its unit-bound
    sibling deliberately has none. Unconfigured refuses.
    """
    credentials = github_app_credentials_for(settings)
    gateway = GitHubEstatePullRequests(token_provider_for(credentials))
    record = land_estate_pull_request(
        session,
        EstateMergeCommand(
            repository=body.repository,
            pr_number=body.pr_number,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_head_sha=body.expected_head_sha,
        ),
        gateway,
        landing_source,
        record_source,
        enabled=settings.estate_landing_enabled,
        credentials_configured=credentials is not None,
    )
    return record


@router.post("/estate-pr-branch-update", response_model=EstateBranchUpdateResponse)
def estate_pr_branch_update_route(
    body: EstateBranchUpdateCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0019 Increment 6: the lane brings up to date a branch it has itself put behind.

    The same off-switch and the same credentials as the landing, resolved the same way and read
    off the same composed answer -- so a deployment that may not land may not touch a branch
    either, by the term that already says so rather than by a second one.
    """
    credentials = github_app_credentials_for(settings)
    gateway = GitHubEstatePullRequests(token_provider_for(credentials))
    return update_estate_pull_request_branch(
        session,
        EstateBranchUpdateCommand(
            repository=body.repository,
            pr_number=body.pr_number,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_head_sha=body.expected_head_sha,
        ),
        gateway,
        landing_source,
        record_source,
        enabled=settings.estate_landing_enabled,
        credentials_configured=credentials is not None,
    )


@router.get("/inert-pr-merge-admission", response_model=InertLandingAdmissionResponse)
def inert_landing_admission_route(
    repository: Annotated[str, Query(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", max_length=300)],
    pr_number: Annotated[int, Query(gt=0)],
    _actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    policy_source: InertLandingPolicySourceDep,
) -> object:
    """ADR-0038 part 2: may this update-bot pull request be landed where landing changes nothing
    already serving, and if not, why?

    **Report-only.** It is what makes a pass that lands nothing legible: every held pull request
    names the condition it misses, and a pass that reports several held for freshness is the
    condition working rather than the lane failing.

    The credentials are resolved once and fed to both this answer and the act, so the gate can
    never attest to credentials the actor does not hold.
    """
    credentials = github_app_credentials_for(settings)
    gateway = GitHubInertPullRequests(token_provider_for(credentials))
    enabled = settings.inert_landing_enabled
    credentials_configured = credentials is not None
    admission = inert_landing_admission(
        session,
        repository,
        pr_number,
        landing_source,
        policy_source,
        gateway,
        enabled=enabled,
        credentials_configured=credentials_configured,
    )
    # ADR-0045, filled in here for the reason the sibling route gives.
    withheld = withheld_for_sibling(
        qualifies=admission.branch_update_qualifies,
        outcome=lambda: branch_update_sibling_outcome(
            session,
            repository=admission.repository,
            target_number=admission.pr_number,
            gateway=gateway,
            compose=inert_sibling_composer(
                session,
                admission.repository,
                landing_source,
                policy_source,
                gateway,
                enabled=enabled,
                credentials_configured=credentials_configured,
            ),
        ),
    )
    return replace(admission, branch_update_withheld_for_sibling=withheld)


@router.post("/inert-pr-merge", response_model=InertPrMergeResponse)
def inert_pr_merge_route(
    body: InertPrMergeCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    policy_source: InertLandingPolicySourceDep,
) -> object:
    """ADR-0038 part 2: the orchestrator lands a pull request the update bot opened.

    Its caller is a scheduled one, which is why this path has an off-switch. Unconfigured refuses,
    and the switch is its own rather than the deploying lane's: the two were activated by different
    decisions and neither implies the other.
    """
    credentials = github_app_credentials_for(settings)
    gateway = GitHubInertPullRequests(token_provider_for(credentials))
    return land_inert_pull_request(
        session,
        InertMergeCommand(
            repository=body.repository,
            pr_number=body.pr_number,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_head_sha=body.expected_head_sha,
        ),
        gateway,
        landing_source,
        policy_source,
        enabled=settings.inert_landing_enabled,
        credentials_configured=credentials is not None,
    )


@router.post("/inert-pr-branch-update", response_model=InertBranchUpdateResponse)
def inert_pr_branch_update_route(
    body: InertBranchUpdateCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    policy_source: InertLandingPolicySourceDep,
) -> object:
    """ADR-0038 part 2: the lane brings up to date a branch it has itself put behind.

    The same off-switch and the same credentials as the landing, resolved the same way and read off
    the same composed answer -- so a deployment that may not land may not touch a branch either, by
    the term that already says so rather than by a second one.
    """
    credentials = github_app_credentials_for(settings)
    gateway = GitHubInertPullRequests(token_provider_for(credentials))
    return update_inert_pull_request_branch(
        session,
        InertBranchUpdateCommand(
            repository=body.repository,
            pr_number=body.pr_number,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_head_sha=body.expected_head_sha,
        ),
        gateway,
        landing_source,
        policy_source,
        enabled=settings.inert_landing_enabled,
        credentials_configured=credentials is not None,
    )


@router.post("/work-units/{unit_id}/pr-merge", response_model=PrMergeResponse)
def pr_merge_route(
    unit_id: UUID,
    body: PrMergeCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0020 Increment 4b: the factory lands its own pull request.

    **Its caller is whoever drives verification, immediately afterwards.** Nothing in this
    repository is scheduled except the landing ledger, and this is deliberately not the exception:
    a scheduled closer is its own increment with its own decision, and the no-off-switch ruling is
    explicitly void if one is ever proposed.

    One credential resolution feeds the gateway, exactly as the workflow trigger does — so the
    thing that acts and the thing that reports what it may do can never disagree about which
    credentials are in play.
    """
    # One resolution feeds both the gate and the actor, so the gate can never attest to
    # credentials the gateway does not actually hold — the rule the workflow trigger states.
    credentials = github_app_credentials_for(settings)
    return land_unit_pull_request(
        session,
        MergeCommand(
            unit_id=unit_id,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
            change_window_override=to_change_window_override(body.change_window_override),
        ),
        GitHubPullRequests(token_provider_for(credentials)),
        landing_source,
        record_source,
        credentials is not None,
    )
