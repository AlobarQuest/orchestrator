import hashlib

from fastapi import APIRouter, Request

from orchestrator.api.dependencies import ActorDep, AuthConfig, SettingsDep
from orchestrator.api.routes.common import ERROR_RESPONSES
from orchestrator.api.schemas.rotation import HeldCredentialResponse, M2MIdentityResponse
from orchestrator.config import Settings
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


# The settings that hold another service's credential, by the variable a rotation's
# `coolify-env-held` destination names (ADR-0055 amendment 13). The read bearer is change-manager's.
HELD_SETTINGS = {"ORCHESTRATOR_CHANGE_RECORD_TOKEN": "change_record_token"}
# As long as the executor's fingerprints, and no longer.
HELD_PREFIX_LENGTH = 16


@router.get("/held-credentials", response_model=dict[str, HeldCredentialResponse])
def held_credentials(actor: ActorDep, settings: SettingsDep) -> object:
    """The fingerprint of each credential this process loaded to call another service.

    ADR-0055 amendment 13. A rotation writes the orchestrator's copy of another service's
    credential and restarts it; this is how the executor confirms the running process loaded the
    new value, which probing that service can't show. Only the configured executor may read it,
    and a setting that isn't set is left out.
    """
    if actor.actor_id != settings.rotation_executor_agent_id:
        raise DomainError(
            "role_forbidden", "only the rotation executor may read this surface", None
        )
    return {
        variable: _fingerprint(value)
        for variable, field in HELD_SETTINGS.items()
        if (value := _loaded(settings, field))
    }


def _loaded(settings: Settings, field: str) -> str:
    secret = getattr(settings, field)
    return secret.get_secret_value() if secret is not None else ""


def _fingerprint(value: str) -> HeldCredentialResponse:
    digest = hashlib.sha256(value.encode()).hexdigest()
    return HeldCredentialResponse(sha256_prefix=digest[:HELD_PREFIX_LENGTH], length=len(value))
