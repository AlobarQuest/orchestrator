from orchestrator.config import Settings

# The test is about the stall setting. `database_url` has no default -- it is supplied from the
# environment at runtime -- so it is passed explicitly here rather than left to whatever the
# ambient environment happens to hold.
DB_URL = "postgresql+psycopg://postgres@127.0.0.1:5432/orchestrator_test"


def test_split_brain_stall_seconds_defaults_and_is_env_overridable(monkeypatch) -> None:
    # Construct Settings directly, never get_settings() -- that accessor is lru_cached and would
    # hand back a stale object across tests.
    monkeypatch.delenv("ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS", raising=False)
    assert Settings(database_url=DB_URL).reconcile_split_brain_stall_seconds == 900

    monkeypatch.setenv("ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS", "5")
    assert Settings(database_url=DB_URL).reconcile_split_brain_stall_seconds == 5
