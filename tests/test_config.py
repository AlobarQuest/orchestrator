import pytest
from pydantic import ValidationError

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


def test_follow_up_due_after_days_defaults_and_is_env_overridable(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_FOLLOW_UP_DUE_AFTER_DAYS", raising=False)
    assert Settings(database_url=DB_URL).follow_up_due_after_days == 30

    monkeypatch.setenv("ORCHESTRATOR_FOLLOW_UP_DUE_AFTER_DAYS", "0")
    assert Settings(database_url=DB_URL).follow_up_due_after_days == 0


def test_follow_up_due_after_days_cannot_be_set_high_enough_to_silence_it(monkeypatch) -> None:
    """The cap is the point. A large value would silence the mechanism as effectively as an
    off switch, which is the WS-P2.15 failure mode this bound exists to make unreachable."""
    monkeypatch.setenv("ORCHESTRATOR_FOLLOW_UP_DUE_AFTER_DAYS", "100000")
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL)


LANE_SWITCHES = {
    "dispatch_enabled": "ORCHESTRATOR_DISPATCH_ENABLED",
    "estate_landing_enabled": "ORCHESTRATOR_ESTATE_LANDING_ENABLED",
    "inert_landing_enabled": "ORCHESTRATOR_INERT_LANDING_ENABLED",
}


@pytest.mark.parametrize(("field", "variable"), sorted(LANE_SWITCHES.items()))
def test_a_lane_switch_is_on_when_its_variable_is_absent(monkeypatch, field, variable) -> None:
    """ADR-0046. Absence means ON: a deployment that loses the variable must not silently halt a
    lane its standing decision says is running."""
    monkeypatch.delenv(variable, raising=False)
    assert getattr(Settings(database_url=DB_URL), field) is True


@pytest.mark.parametrize(("field", "variable"), sorted(LANE_SWITCHES.items()))
def test_a_lane_switch_is_off_when_its_variable_says_false(monkeypatch, field, variable) -> None:
    """The variable survives as the off switch, and it is the only way to stop a lane."""
    monkeypatch.setenv(variable, "false")
    assert getattr(Settings(database_url=DB_URL), field) is False


@pytest.mark.parametrize(
    "variable",
    ["ORCHESTRATOR_DISPATCH_ENABLED_CAPABILITIES", "ORCHESTRATOR_DISPATCH_ALLOWED_CHANGE_CLASSES"],
)
def test_the_retired_posture_variables_are_read_by_nothing(monkeypatch, variable) -> None:
    """ADR-0053. The lists live in the policy artifact; a leftover variable is not a field, does not
    fail boot, and so cannot widen anything -- which is why deleting it from a deployment can happen
    in either order with the release."""
    monkeypatch.setenv(variable, '["command.run", "anything"]')
    settings = Settings(database_url=DB_URL)

    assert not any(
        name in Settings.model_fields
        for name in ("dispatch_enabled_capabilities", "dispatch_allowed_change_classes")
    )
    assert "anything" not in settings.model_dump_json()
