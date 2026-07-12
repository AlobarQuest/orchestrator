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
    # The workflow-dispatch endpoint takes a workflow file NAME or numeric id, not a path.
    # A path adds URL segments and GitHub answers 404, which is indistinguishable from a
    # missing workflow and opens the failure-signature circuit breaker after three tries.
    dispatch_workflow_id: str = "factory-runner-pilot.yml"
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
    # How long a post-deploy verification unit may sit in SUBMITTED before the reconciliation
    # detect-pass calls it a deploy_split_brain. AC-003 is time-elapsed by nature -- the unit is
    # minted SUBMITTED inside the deployment-ingest transaction, so "verification stalled" cannot
    # be true at ingest under any design, and this threshold is the only thing that distinguishes
    # a normal in-progress verification from a split brain. Production must sit above a normal
    # verification's worst case; the AC-010 drill sets it low via the env var so it needs no sleep.
    reconcile_split_brain_stall_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate({})
