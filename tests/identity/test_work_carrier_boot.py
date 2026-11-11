"""Boot refuses unless the work-carrier setting and its observer credential agree exactly.

ADR-0055 amendment 22 item 3: the decomposition gate trusts a standing-approval record only when
`recorded_by` is `ORCHESTRATOR_WORK_CARRIER_AGENT_ID`, so the setting must name the one identity
whose credential holds `work-carrier-observer-v1`, and a credential holding that profile must be
the one the setting names.
"""

import json
from pathlib import Path

import pytest

from orchestrator.main import load_auth_config
from tests.identity.test_boot_auth_config import AUTH_VARIABLES, MESSAGE, VALID

SETTING = "ORCHESTRATOR_WORK_CARRIER_AGENT_ID"
CARRIER = "work-carrier"
SECOND_CARRIER = "work-carrier-2"


def _bundle(tmp_path: Path) -> str:
    path = tmp_path / "registry-bundle.json"
    bundle = json.loads(Path("tests/fixtures/registry-bundle.json").read_text())
    for agent_id in (CARRIER, SECOND_CARRIER):
        bundle["actors"].append(
            {
                "agent_id": agent_id,
                "version": 1,
                "status": "active",
                "runtime": "node-executor",
                "authority_profile": "work-carrier-observer-v1",
            }
        )
    path.write_text(json.dumps(bundle))
    return str(path)


def _boot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    holders: tuple[str, ...],
    setting: str | None,
) -> None:
    for name in (*AUTH_VARIABLES, SETTING):
        monkeypatch.delenv(name, raising=False)
    credentials = {"worker-key": {"agent_id": "worker", "token_hash": "a" * 64}}
    for index, agent_id in enumerate(holders):
        credentials[f"carrier-key-{index}"] = {
            "agent_id": agent_id,
            "token_hash": f"{index + 1:x}" * 64,
        }
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
    ("holders", "setting"),
    [
        pytest.param((), None, id="neither"),
        pytest.param((), "  ", id="blank_setting_is_unset"),
        pytest.param((CARRIER,), CARRIER, id="both_agree"),
        pytest.param((CARRIER, CARRIER), CARRIER, id="two_keys_one_identity"),
    ],
)
def test_boot_accepts_an_agreeing_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    holders: tuple[str, ...],
    setting: str | None,
) -> None:
    _boot(monkeypatch, tmp_path, holders=holders, setting=setting)


@pytest.mark.parametrize(
    ("holders", "setting"),
    [
        pytest.param((CARRIER,), None, id="credential_without_setting"),
        pytest.param((CARRIER,), "worker", id="credential_with_another_setting"),
        pytest.param((CARRIER, SECOND_CARRIER), CARRIER, id="a_second_holder"),
        pytest.param((), CARRIER, id="setting_without_credential"),
        pytest.param((), "worker", id="setting_names_another_profile"),
        pytest.param((), "nobody", id="setting_names_no_identity"),
    ],
)
def test_boot_refuses_a_disagreeing_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    holders: tuple[str, ...],
    setting: str | None,
) -> None:
    with pytest.raises(RuntimeError, match=MESSAGE):
        _boot(monkeypatch, tmp_path, holders=holders, setting=setting)
