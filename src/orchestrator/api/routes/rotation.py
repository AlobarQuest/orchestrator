from fastapi import APIRouter, Request

from orchestrator.api.dependencies import ActorDep, AuthConfig, SettingsDep
from orchestrator.api.routes.common import ERROR_RESPONSES
from orchestrator.api.schemas.rotation import M2MIdentityResponse
from orchestrator.errors import DomainError

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.get(
    "/m2m-identities",
    response_model=dict[str, M2MIdentityResponse],
    response_model_exclude_none=True,
)
def m2m_identities(request: Request, actor: ActorDep, settings: SettingsDep) -> object:
    """The machine identities this process loaded: key ids, agent ids and bearer hashes.

    ADR-0055 amendment 9. The rotation executor rewrites the whole credentials setting when it
    rotates a self-issued bearer, and its hosting token can't read the stored setting, so it reads
    what the running process loaded instead. Reading the live process means a hand edit made since
    the last rotation is never reverted, and after a restart the executor confirms its write took
    effect. Only the configured executor may read it. A hash is the sha256 of a random bearer, so
    it is not a secret, but nothing else needs the list.
    """
    # An actor id is never None, so an unset executor refuses everyone.
    if actor.actor_id != settings.rotation_executor_agent_id:
        raise DomainError(
            "role_forbidden", "only the rotation executor may read this surface", None
        )
    # An authenticated actor implies `get_actor` found this configuration.
    config: AuthConfig = request.app.state.auth_config
    return {
        key: M2MIdentityResponse.model_validate(credential)
        for key, credential in config.m2m_credentials.items()
    }
