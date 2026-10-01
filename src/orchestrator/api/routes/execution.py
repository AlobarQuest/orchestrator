from uuid import UUID

from fastapi import APIRouter

from orchestrator.api.dependencies import ActorDep, SessionDep, SettingsDep
from orchestrator.api.routes.common import (
    ERROR_RESPONSES,
    LandingSourceDep,
    github_app_credentials_for,
    to_change_window_override,
)
from orchestrator.api.schemas.execution import (
    DispatchCommandModel,
    DispatchResponse,
    FactoryPolicyResponse,
)
from orchestrator.factory_policy import load_factory_policy
from orchestrator.services.execution.dispatch import (
    DispatchCommand,
    DispatchSettings,
    GitHubActionsDispatcher,
    dispatch_work_unit,
)
from orchestrator.services.execution.factory_target import GitHubFactoryTargetSource
from orchestrator.services.github_app import token_provider_for
from orchestrator.services.lifecycle.lifecycle import require_operator_actor

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.post("/work-units/{unit_id}/dispatch", response_model=DispatchResponse)
def dispatch_route(
    unit_id: UUID,
    body: DispatchCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
) -> object:
    # One resolution feeds both the admission gate and the minter, so the gate can never
    # attest to credentials the dispatcher does not actually use.
    credentials = github_app_credentials_for(settings)
    dispatch_settings = DispatchSettings(
        enabled=settings.dispatch_enabled,
        allowed_change_classes=settings.dispatch_allowed_change_classes,
        enabled_capabilities=settings.dispatch_enabled_capabilities,
        workflow_id=settings.dispatch_workflow_id,
        workflow_ref=settings.dispatch_workflow_ref,
        github_app_configured=credentials is not None,
        failure_signature_threshold=settings.dispatch_failure_signature_threshold,
        orchestrator_url=settings.dispatch_orchestrator_url,
    )
    token_provider = token_provider_for(credentials)
    dispatcher = GitHubActionsDispatcher(token_provider)
    return dispatch_work_unit(
        session,
        DispatchCommand(
            unit_id=unit_id,
            runner_attempt=body.runner_attempt,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
            change_window_override=to_change_window_override(body.change_window_override),
        ),
        dispatch_settings,
        dispatcher,
        landing_source,
        target_source=GitHubFactoryTargetSource(token_provider),
    )


@router.get("/factory-policy", response_model=FactoryPolicyResponse)
def factory_policy_route(actor: ActorDep) -> object:
    """WS-P2.18. The policy this running process is enforcing, read from the artifact per request.

    Merged is not deployed: a policy that is correct on `main` says nothing about the image serving
    traffic, and this is the surface that answers what that image actually holds. The artifact is
    re-read on every call, so an operator sees the bytes in force now rather than the ones loaded
    at start-up.
    """
    require_operator_actor(actor)
    return load_factory_policy().report()
