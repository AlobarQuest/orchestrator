from orchestrator.config import Settings


def test_split_brain_stall_seconds_defaults_and_is_env_overridable(monkeypatch) -> None:
    # Construct Settings directly, never get_settings() -- that accessor is lru_cached and would
    # hand back a stale object across tests.
    monkeypatch.delenv("ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS", raising=False)
    assert Settings().reconcile_split_brain_stall_seconds == 900

    monkeypatch.setenv("ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS", "5")
    assert Settings().reconcile_split_brain_stall_seconds == 5
