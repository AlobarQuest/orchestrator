"""Helpers the route modules of more than one domain use.

Each `api/routes/<domain>.py` holds the routes of one service domain and the helpers only those
routes reach; a helper moves here only when a second domain needs it. The request-scoped
dependency aliases (`SessionDep`, `ActorDep`, `SettingsDep`) live in `api/dependencies.py`.
"""

from typing import Annotated, Any

from fastapi import Depends

from orchestrator.api.dependencies import SettingsDep
from orchestrator.api.schemas.common import ChangeWindowOverrideModel, ErrorResponse
from orchestrator.change_window_override import ChangeWindowOverride
from orchestrator.config import Settings
from orchestrator.errors import DomainError
from orchestrator.services.github_app import GitHubAppCredentials, github_app_credentials
from orchestrator.services.landing.estate_landing import (
    EstateLandingSource,
    HttpEstateLandingSource,
)


def github_app_credentials_for(settings: Settings) -> GitHubAppCredentials | None:
    return github_app_credentials(
        app_id=settings.github_app_id,
        installation_id=settings.github_app_installation_id,
        private_key_b64=settings.github_app_private_key_b64,
    )


def get_landing_source(settings: SettingsDep) -> EstateLandingSource:
    """Build the thing that asks the estate what landing on a repository's default branch does.

    A dependency rather than a call inside each route body so a test can substitute App Brain, and
    so an unset URL or credential arrives as the empty string the source itself refuses on — never
    as a source that silently answers. One definition, because two routes now ask the same
    question and a second copy is a second place for that empty-string property to be forgotten.
    """
    return HttpEstateLandingSource(
        base_url=settings.app_brain_url,
        read_key=(
            settings.app_brain_read_key.get_secret_value() if settings.app_brain_read_key else ""
        ),
        timeout_seconds=settings.app_brain_timeout_seconds,
    )


LandingSourceDep = Annotated[EstateLandingSource, Depends(get_landing_source)]


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required or rejected"},
    403: {"model": ErrorResponse, "description": "Authenticated actor is forbidden"},
    409: {"model": ErrorResponse, "description": "Domain conflict"},
}


def raise_error(value: object) -> object:
    if isinstance(value, DomainError):
        raise value
    return value


def require_zero_expected_version(value: int, operation: str) -> None:
    if value != 0:
        raise DomainError(
            "version_conflict",
            f"{operation} requires expected version 0",
            "reload",
            current_version=0,
        )


def to_change_window_override(
    body: ChangeWindowOverrideModel | None,
) -> ChangeWindowOverride | None:
    """Turn what a caller sent into the type the acts honour, or refuse it by name.

    Absent means no override, which is the default and stays fail-closed. Present with nothing to
    say is the shape this refuses: `ChangeWindowOverride` cannot be constructed without a reason,
    so the `DomainError` is raised by the type rather than here, and it is raised for every caller
    rather than for this one. The refusal reaches the caller as a named error because that is what
    has a registered handler; a constrained pydantic field would have answered the same request
    with a 422 naming a field location, which tells an operator less about what to do next.

    It happens HERE, before either service is entered, so a malformed request can never be
    answered by an idempotent replay of an earlier record.
    """
    if body is None:
        return None
    return ChangeWindowOverride(reason=body.reason or "")
