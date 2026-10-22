"""The Keychain instance of the executor (ADR-0055 amendment 12): its configuration, the person's
stage step, which units it takes, and where it finds a keeper."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from rotation_worker import cli, worker
from rotation_worker.checks import _keeper_id
from rotation_worker.config import ConfigError, load
from rotation_worker.contract import ContractError
from rotation_worker.registry import RegistryEntry, RegistryError
from rotation_worker.secret_store import KeychainStore, StoreError
from rotation_worker.values import Value
from rotation_worker.worker import Setup
from tests.rotation_worker.stores import MemoryStore

TOKEN = "0.11111111-2222-4333-8444-555555555555.Abc123:ZGVmZ2hp=="
KEYCHAIN_CONFIG = (
    'store = "keychain"\norchestrator_url = "https://sds.example"\n'
    'credential_key_id = "rotation-executor-keychain-1"\nwork_project_id = "rotation-executor"\n'
    'bearer_secret_id = "worker-bearer"\nstate_dir = "/s"\nsource_revision = "0123456789ab"\n'
)


def _config(tmp_path: Path, text: str = KEYCHAIN_CONFIG) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


# ---- configuration --------------------------------------------------------------------------


def test_a_keychain_configuration_names_keychain_accounts_and_no_root_token(
    tmp_path: Path,
) -> None:
    config = load(_config(tmp_path))
    assert (config.store, config.root_token_file, config.organization_id) == (
        "keychain",
        None,
        None,
    )
    assert cli.secret_id(config, "worker-bearer") == (
        'keychain service="rotation-executor" account="worker-bearer"'
    )
    assert isinstance(cli.open_store(config), KeychainStore)


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ('organization_id = "11111111-1111-4111-8111-111111111111"\n', "names no organization_id"),
        ('root_token_file = "/t"\n', "names no root_token_file"),
        ('coolify_url = "https://c"\n', "names no coolify_url"),
        ("[admin_secrets]\n", "names no admin_secrets"),
    ],
)
def test_a_keychain_configuration_names_nothing_only_the_vm_uses(
    tmp_path: Path, extra: str, match: str
) -> None:
    with pytest.raises(ConfigError, match=match):
        load(_config(tmp_path, KEYCHAIN_CONFIG + extra))


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ('store = "keychain"', 'store = "vault"', "store is one of"),
        ('bearer_secret_id = "worker-bearer"', 'bearer_secret_id = "a b"', "Keychain name"),
        ('work_project_id = "rotation-executor"', "", "Keychain name"),
    ],
)
def test_an_unknown_store_or_a_malformed_name_is_refused(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    with pytest.raises(ConfigError, match=match):
        load(_config(tmp_path, KEYCHAIN_CONFIG.replace(old, new)))


# ---- the person's stage step ----------------------------------------------------------------


class Recording(MemoryStore):
    read_back_kind = "keychain"


def test_stage_takes_the_token_from_a_hidden_prompt_and_prints_only_its_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Recording()
    monkeypatch.setattr(cli, "open_store", lambda config: store)
    config = load(_config(tmp_path))
    assert cli.stage(config, "bws-tok-brain-ops", "2026-10-22-requested", lambda _p: TOKEN) == 0
    printed = capsys.readouterr().out
    assert TOKEN not in printed
    assert json.loads(printed) == {
        "sha256_prefix": Value(TOKEN).fingerprint.sha256_prefix,
        "length": len(TOKEN),
    }
    [(service, key, value)] = store.secrets.values()
    assert (service, key) == (
        "rotation-executor",
        "rotation/bws-tok-brain-ops/2026-10-22-requested/staging",
    )
    assert value.reveal() == TOKEN


@pytest.mark.parametrize("typed", ["not a token", TOKEN + "!", "0.x.y:z", TOKEN[1:]])
def test_stage_refuses_anything_not_shaped_like_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    store = Recording()
    monkeypatch.setattr(cli, "open_store", lambda config: store)
    result = cli.stage(load(_config(tmp_path)), "c", "o", lambda _p: typed)
    assert result == cli.EXIT_UNREADABLE and store.secrets == {}


def test_stage_reports_a_keychain_refusal_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Refusing(Recording):
        def create(self, project_id: str, key: str, value: Value) -> Any:
            raise StoreError("the Keychain didn't answer: it may be asking a person")

    monkeypatch.setattr(cli, "open_store", lambda config: Refusing())
    assert cli.stage(load(_config(tmp_path)), "c", "o", lambda _p: TOKEN) == cli.EXIT_UNREADABLE
    assert "asking a person" in capsys.readouterr().err


def test_stage_refuses_a_second_staging_and_a_vm_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Recording()
    monkeypatch.setattr(cli, "open_store", lambda config: store)
    config = load(_config(tmp_path))
    assert cli.stage(config, "c", "o", lambda _p: TOKEN) == 0
    assert cli.stage(config, "c", "o", lambda _p: TOKEN) == cli.EXIT_UNREADABLE
    from dataclasses import replace

    assert (
        cli.stage(replace(config, store="bws"), "c", "o", lambda _p: TOKEN) == cli.EXIT_UNREADABLE
    )


# ---- which units an instance takes ----------------------------------------------------------


def _unit(adapter: str) -> dict[str, Any]:
    return {"id": "u1", "authority": {"constraints": {"rotation_adapter": adapter}}}


@pytest.mark.parametrize(
    ("store", "adapter", "ours"),
    [
        (MemoryStore(), "bitbucket", True),
        (MemoryStore(), "bws-machine-token", False),
        (Recording(), "bws-machine-token", True),
        (Recording(), "bitbucket", False),
        (Recording(), "no-such-adapter", True),
    ],
)
def test_each_instance_takes_only_the_units_its_store_keeps(
    store: Any, adapter: str, ours: bool
) -> None:
    setup = cast(Setup, SimpleNamespace(store=store))
    assert worker._ours(setup, _unit(adapter)) is ours


def test_the_other_instance_s_unit_is_left_unclaimed_without_reading_its_package() -> None:
    class Client:
        def unit(self, unit_id: str) -> dict[str, Any]:
            return _unit("bws-machine-token")

        def package_intake(self, revision: str) -> dict[str, Any]:
            raise AssertionError("the other instance's unit was read further")

        def claim(self, *arguments: Any) -> None:
            raise AssertionError("the other instance's unit was claimed")

    setup = cast(Setup, SimpleNamespace(store=MemoryStore(), client=Client()))
    assert worker._take(setup, "u1") == 0


# ---- where the keeper is --------------------------------------------------------------------

KEEPER = 'keychain service="Claude" account="BWS_ACCESS_TOKEN_BRAIN_OPS"'


def _entry(**fields: Any) -> RegistryEntry:
    return RegistryEntry("AlobarQuest/infraops-mcp-server", "c" * 40, {"id": "x", **fields})


def test_a_machine_token_s_keeper_is_a_keychain_item_the_keychain_instance_reads() -> None:
    entry = _entry(keeper_service="Claude", keeper_account="BWS_ACCESS_TOKEN_BRAIN_OPS")
    assert entry.keeper == KEEPER
    assert _keeper_id(entry, [KEEPER], KeychainStore()) == KEEPER
    with pytest.raises(ContractError, match="isn't kept where this executor reads"):
        _keeper_id(entry, [KEEPER], MemoryStore())
    with pytest.raises(ContractError, match="isn't one of its destinations"):
        _keeper_id(entry, [], KeychainStore())


def test_a_bws_keeper_is_still_its_uuid() -> None:
    entry = _entry(bws_uuid="00000000-0000-4000-8000-000000000000")
    destination = 'bws-secret uuid="00000000-0000-4000-8000-000000000000"'
    assert _keeper_id(entry, [destination], MemoryStore()) == "00000000-0000-4000-8000-000000000000"


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"keeper_service": "Claude"},
        {"bws_uuid": "u", "keeper_service": "Claude", "keeper_account": "A"},
        {"keeper_service": 1, "keeper_account": "A"},
    ],
)
def test_a_keeper_is_exactly_one_of_the_two(fields: dict[str, Any]) -> None:
    with pytest.raises(RegistryError, match="keeper is bws_uuid, or keeper_service"):
        _ = _entry(**fields).keeper
