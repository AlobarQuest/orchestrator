"""The service's commands: one pass, the canary's keeper, and the executor's own bearer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rotation_worker import cli
from rotation_worker.config import Config, ConfigError, load
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore

WORK = "22222222-2222-4222-8222-222222222222"


def _config(tmp_path: Path, bearer: str | None = None) -> Config:
    return Config(
        orchestrator_url="https://sds.example",
        credential_key_id="rotation-executor-1",
        organization_id="11111111-1111-4111-8111-111111111111",
        work_project_id=WORK,
        bearer_secret_id=bearer,
        root_token_file=tmp_path / "root-token",
        state_dir=tmp_path,
        source_revision="0123456789ab",
    )


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    memory = MemoryStore()
    monkeypatch.setattr(cli, "open_store", lambda config: memory)
    return memory


def test_mint_bearer_stores_the_value_and_prints_only_its_id_and_hash(
    tmp_path: Path, store: MemoryStore, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.mint_bearer(_config(tmp_path)) == 0
    printed = json.loads(capsys.readouterr().out)
    project, key, value = store.secrets[printed["secret_id"]]
    assert (project, key) == (WORK, cli.BEARER_KEY)
    assert printed["token_hash"] == hashlib.sha256(value.reveal().encode()).hexdigest()
    assert printed["sha256_prefix"] == value.fingerprint.sha256_prefix
    assert value.reveal() not in json.dumps(printed)
    assert len(value.reveal()) >= 64


def test_mint_bearer_refuses_while_one_is_waiting(
    tmp_path: Path, store: MemoryStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.put(WORK, cli.BEARER_KEY, Value("an-earlier-bearer"))
    assert cli.mint_bearer(_config(tmp_path)) == cli.EXIT_UNREADABLE
    assert len(store.secrets) == 1
    assert "already waiting" in capsys.readouterr().err


def test_seed_canary_creates_the_keeper_once(
    tmp_path: Path, store: MemoryStore, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.seed_canary(_config(tmp_path)) == 0
    first = json.loads(capsys.readouterr().out)["secret_id"]
    assert cli.seed_canary(_config(tmp_path)) == 0
    assert json.loads(capsys.readouterr().out)["secret_id"] == first
    assert len(store.secrets) == 1
    keeper = store.read(first)
    assert cli.ADAPTERS["fake"].build(tmp_path).probe(keeper) == 200


def test_run_needs_the_bearer(tmp_path: Path, store: MemoryStore) -> None:
    with pytest.raises(ConfigError, match="mint the bearer first"):
        cli.run(_config(tmp_path))


def test_a_config_without_a_bearer_loads_and_a_malformed_one_does_not(tmp_path: Path) -> None:
    text = (
        'orchestrator_url = "https://sds.example"\ncredential_key_id = "k"\n'
        'organization_id = "11111111-1111-4111-8111-111111111111"\n'
        f'work_project_id = "{WORK}"\nroot_token_file = "/t"\nstate_dir = "/s"\n'
        'source_revision = "0123456789ab"\n'
    )
    path = tmp_path / "config.toml"
    path.write_text(text)
    assert load(path).bearer_secret_id is None
    path.write_text(text + 'bearer_secret_id = "nope"\n')
    with pytest.raises(ConfigError, match="bearer_secret_id"):
        load(path)
