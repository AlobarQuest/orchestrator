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
    # WS-P2.28. Where the estate's record of what a landing on a default branch changes is read
    # from, and the READ-ONLY credential that reads it -- App Brain scopes that credential to two
    # read paths and refuses it everywhere else, so it cannot write to the estate's knowledge.
    # Both default to empty, and empty is a REFUSAL at admission rather than a permission: an
    # unconfigured deployment must not be able to conclude that nothing already serving changes.
    # Setting them is therefore a prerequisite of enabling routing, not an optional extra.
    app_brain_url: str = ""
    app_brain_read_key: SecretStr | None = None
    app_brain_timeout_seconds: float = 10.0
    # Where the estate's change records live, and the bearer that reads them (ADR-0019). Both
    # default to empty, and empty is a REFUSAL for every repository the estate says landing
    # changes something already serving -- which is exactly the answer that repository already
    # gets today, so a release carrying this needs no environment write to be safe and stays inert
    # until one is made. The timeout is deliberately shorter than App Brain's: both are consulted
    # inside a transaction holding a row lock on the work unit, and this is the second of the two.
    change_record_url: str = ""
    change_record_token: SecretStr | None = None
    change_record_timeout_seconds: float = 5.0
    dispatch_failure_signature_threshold: int = 3
    dispatch_orchestrator_url: str = "https://sds.alobar.net"
    # How long a human approval gate may go unanswered before the dead-letter view reports it as
    # a stalled approval. A plain int with NO "off" value, on purpose: its predecessor
    # (dispatch_human_gate_age_out_seconds: int | None = None) defaulted to None, and that None
    # is why the age-out it configured sat unwired and invisible for an entire workstream. A
    # reporting obligation that can be switched off is one that will be. Reporting only --
    # nothing here transitions a unit, because silence is never approval.
    # BOUNDED, not merely non-nullable. Without an upper bound the type says "cannot be switched
    # off" while the reachable config space says otherwise: 999_999_999 silences the report as
    # effectively as `None` ever did. The verifier who adjudicated this package made exactly that
    # point. A cap of 30 days makes the claim true of the values an operator can actually set --
    # silencing is now impossible rather than merely unfashionable.
    # The cap is the point; the floor is not. A LARGE value silences the report (that is the
    # failure mode), while 0 merely reports everything -- maximally on, and what drill 5 uses so
    # it needs no sleep. So: 0 <= x <= 30 days.
    dead_letter_stalled_approval_seconds: int = Field(
        default=604_800, ge=0, le=2_592_000
    )  # 7 days; capped at 30
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
    # How long after a package revision's work settles before its declared follow-up review
    # becomes due. A plain int with NO "off" value and BOUNDED at both ends, following
    # dead_letter_stalled_approval_seconds: a large value silences the mechanism as effectively
    # as None ever did, so the cap is what makes "cannot be switched off" true of the values an
    # operator can actually set. The floor is not the risk -- 0 means "due as soon as the work
    # settles", which is maximally on and is what the production demonstration uses so it needs
    # no waiting.
    follow_up_due_after_days: int = Field(default=30, ge=0, le=365)
    # How long after a claim's hold has ended before the review queue reports the unit as stalled.
    # This is a MARGIN on top of the lease, never a duration of its own: the lease already says how
    # long this unit's work may take, per what its package says that work reaches, so the only
    # question left is how long after that we stop expecting the worker back. Keying it on the
    # lease is what stops a second, disagreeing copy of "how long may this take" coming into
    # existence next to the one that already answers it.
    # A plain int with NO "off" value and BOUNDED at both ends, following
    # dead_letter_stalled_approval_seconds and follow_up_due_after_days. The cap is what makes
    # "cannot be switched off" true of the values an operator can actually set: the longest hold
    # this build will ever grant is two hours (kernel.leases.LEASE_CEILING), so a day is already
    # twelve times the largest thing this is a margin on, and anything past that silences the
    # report as completely as a None ever did. The floor is not the risk -- 0 reports at the lapse
    # itself, which is maximally on, and is what the tests use so they need no sleep.
    execution_stall_grace_seconds: int = Field(
        default=900, ge=0, le=86_400
    )  # 15 minutes; capped at a day


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate({})
