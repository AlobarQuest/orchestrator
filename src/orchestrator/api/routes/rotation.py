from fastapi import APIRouter, Request

from orchestrator.api.dependencies import ActorDep, AuthConfig, SettingsDep
from orchestrator.api.routes.common import ERROR_RESPONSES
from orchestrator.api.schemas.rotation import M2MIdentityResponse, PreviousHashResponse
from orchestrator.errors import DomainError

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.get("/m2m-identities", response_model=dict[str, M2MIdentityResponse])
def m2m_identities(request: Request, actor: ActorDep, settings: SettingsDep) -> object:
    """The machine identities this process loaded: key ids, agent ids and bearer hashes.

    ADR-0055 amendment 9. The rotation executor rewrites the whole credentials setting when it
    rotates a self-issued bearer, and its hosting token can't read the stored setting, so it reads
    what the running process loaded instead. Reading the live process means a hand edit made since
    the last rotation is never reverted, and after a restart the executor confirms its write took
    effect. Only the configured executor may read it. A hash is the sha256 of a random bearer, so
    it is not a secret, but nothing else needs the list.
    """
    executor = settings.rotation_executor_agent_id
    if executor is None or actor.actor_id != executor:
        raise DomainError("role_forbidden", "only the rotation executor may read this surface", None)
    # An authenticated actor implies `get_actor` found this configuration.
    config: AuthConfig = request.app.state.auth_config
    return {
        key: M2MIdentityResponse(
            agent_id=credential.agent_id,
            token_hash=credential.token_hash,
            previous=(
                None
                if credential.previous is None
                else PreviousHashResponse(
                    token_hash=credential.previous.token_hash,
                    until=credential.previous.until,
                )
            ),
        )
        for key, credential in config.m2m_credentials.items()
    }
