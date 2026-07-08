from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORCHESTRATOR_")
    database_url: str
    dispatch_enabled: bool = False
    dispatch_allowed_change_classes: frozenset[str] = Field(default_factory=frozenset)
    dispatch_enabled_capabilities: frozenset[str] = Field(default_factory=frozenset)
    dispatch_target_repository: str = "AlobarQuest/orchestrator"
    dispatch_workflow_id: str = ".github/workflows/factory-runner-pilot.yml"
    dispatch_workflow_ref: str = "main"
    github_dispatch_token: str | None = None
    dispatch_failure_signature_threshold: int = 3
    dispatch_orchestrator_url: str = "https://sds.alobar.net"
    dispatch_human_gate_age_out_seconds: int | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate({})
