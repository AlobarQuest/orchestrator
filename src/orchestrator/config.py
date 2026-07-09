from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORCHESTRATOR_")
    database_url: str
    dispatch_enabled: bool = False
    dispatch_allowed_change_classes: frozenset[str] = Field(default_factory=frozenset)
    dispatch_enabled_capabilities: frozenset[str] = Field(default_factory=frozenset)
    # Dispatch routes to each unit's own constraints.target_repository; this allowlist
    # bounds where it may route. Empty (the default) dispatches nowhere.
    dispatch_allowed_target_repositories: frozenset[str] = Field(default_factory=frozenset)
    dispatch_workflow_id: str = ".github/workflows/factory-runner-pilot.yml"
    dispatch_workflow_ref: str = "main"
    # Dispatch authenticates as a GitHub App: an installation token expires hourly, so the
    # orchestrator mints one rather than holding a static bearer token. The PEM is base64
    # encoded so it stays a single-line environment variable.
    github_app_id: str | None = None
    github_app_installation_id: str | None = None
    github_app_private_key_b64: SecretStr | None = None
    dispatch_failure_signature_threshold: int = 3
    dispatch_orchestrator_url: str = "https://sds.alobar.net"
    dispatch_human_gate_age_out_seconds: int | None = None
    brain_proposal_target_urls: dict[str, str] = Field(default_factory=dict)
    brain_proposal_credentials: dict[str, str] = Field(default_factory=dict)
    brain_proposal_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate({})
