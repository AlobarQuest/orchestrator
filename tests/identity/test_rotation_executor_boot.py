"""Boot refuses unless the rotation-executor setting and the credentials agree (ADR-0055).

Claim confinement is keyed on `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID`. If the executor's bearer
were configured without the setting, the executor could claim any unit that is not a rotation
unit, so that direction is closed at boot rather than left to a missing environment variable.
"""

import json
from pathlib import Path

import pytest

from orchestrator.main import load_auth_config
from tests.identity.test_boot_auth_config import AUTH_VARIABLES, MESSAGE, VALID

SETTING = "ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID"


def _bundle(tmp_path: Path) -> str:
    path = tmp_path / "registry-bundle.json"
    bundle = json.loads(Path("tests/fixtures/registry-bundle.json").read_text())
    bundle["actors"].append(
        {
            "agent_id": "rotation-executor",
            "version": 1,
            "status": "active",
            "runtime": "node-executor",
            "authority_profile": "rotation-executor-v1",
        }
    )
    path.write_text(json.dumps(bundle))
    return str(path)


def _boot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    executor_credential: bool,
    setting: str | None,
) -> None:
    for name in (*AUTH_VARIABLES, SETTING):
        monkeypatch.delenv(name, raising=False)
    credentials = {"worker-key": {"agent_id": "worker", "token_hash": "a" * 64}}
    if executor_credential:
        credentials["executor-key"] = {"agent_id": "rotation-executor", "token_hash": "b" * 64}
    environment = {
        **VALID,
        "ORCHESTRATOR_REGISTRY_BUNDLE": _bundle(tmp_path),
        "ORCHESTRATOR_M2M_CREDENTIALS": json.dumps(credentials),
    }
    if setting is not None:
        environment[SETTING] = setting
    for name, value in environment.items():
        if value is not None:
            monkeypatch.setenv(name, value)
    load_auth_config()


@pytest.mark.parametrize(
    ("executor_credential", "setting"),
    [
        pytest.param(False, None, id="neither"),
        pytest.param(False, "  ", id="blank_setting_is_unset"),
        pytest.param(False, "rotation-executor", id="setting_before_credential"),
        pytest.param(True, "rotation-executor", id="both_agree"),
    ],
)
def test_boot_accepts_an_agreeing_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    executor_credential: bool,
    setting: str | None,
) -> None:
    _boot(monkeypatch, tmp_path, executor_credential=executor_credential, setting=setting)


@pytest.mark.parametrize(
    ("executor_credential", "setting"),
    [
        pytest.param(True, None, id="credential_without_setting"),
        pytest.param(True, "worker", id="credential_with_another_setting"),
        pytest.param(False, "worker", id="setting_without_the_executor_profile"),
        pytest.param(False, "devon", id="setting_names_a_human"),
        pytest.param(False, "nobody", id="setting_names_no_identity"),
    ],
)
def test_boot_refuses_a_disagreeing_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    executor_credential: bool,
    setting: str | None,
) -> None:
    with pytest.raises(RuntimeError, match=MESSAGE):
        _boot(monkeypatch, tmp_path, executor_credential=executor_credential, setting=setting)
