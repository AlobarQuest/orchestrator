"""The service's commands: one pass, the canary's keeper, and the executor's own bearer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from rotation_worker import cli
from rotation_worker.adapters import AdapterContext
from rotation_worker.config import Config, ConfigError, load
from rotation_worker.registry import RegistryEntry
from rotation_worker.values import Value
from rotation_worker.worker import Setup
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
    context = AdapterContext(tmp_path, store, admin_secret_id=None)
    assert cli.ADAPTERS["fake"].build(context).probe(keeper) == 200


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


def test_read_consumers_prints_each_reader_s_answer_and_exits_2_on_one_unreadable(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
    credential = {
        "bws_uuid": "00000000-0000-4000-8000-000000000000",
        "consumer": [
            {
                "kind": "runtime-fetch",
                "repo": "v",
                "ref": "a.sh",
                "observation_source": "s",
                "observation_subject": "after",
            },
            {
                "kind": "runtime-fetch",
                "repo": "v",
                "ref": "b.sh",
                "observation_source": "s",
                "observation_subject": "before",
            },
            {
                "kind": "runtime-fetch",
                "repo": "v",
                "ref": "c.sh",
                "observation_source": "s",
                "observation_subject": "silent",
            },
        ],
    }
    monkeypatch.setattr(
        cli, "find_credential", lambda mirror, cid: RegistryEntry("r", "c" * 40, credential)
    )

    class Client:
        def observations(self, source: str, subject: str, since: datetime) -> list[dict[str, str]]:
            at = {"after": stored + timedelta(hours=1), "before": stored - timedelta(hours=1)}
            return [{"observed_at": at[subject].isoformat()}] if subject in at else []

    setup = cast(
        Setup,
        SimpleNamespace(
            mirror=None, healthchecks=None, client=Client(), github_app=None, store=MemoryStore()
        ),
    )
    assert cli.read_consumers(setup, "x", stored) == cli.EXIT_UNREADABLE
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(
        'switched    runtime-fetch repo="v" ref="a.sh"  last run 2026-10-15T07'
    )
    assert lines[1].startswith(
        'not yet     runtime-fetch repo="v" ref="b.sh"  last run 2026-10-15T05'
    )
    assert lines[2] == (
        'unreadable  runtime-fetch repo="v" ref="c.sh": silent posted nothing in the last 15 days'
    )


def test_read_consumers_needs_a_zoned_instant() -> None:
    with pytest.raises(SystemExit):
        cli.main(["read-consumers", "x", "--stored-at", "2026-10-15T06:00:00"])


def test_the_rotation_app_is_built_only_when_configured(tmp_path: Path) -> None:
    from dataclasses import replace

    from rotation_worker.cli import open_github_app
    from rotation_worker.config import Config
    from rotation_worker.github_actions import RotationApp
    from tests.rotation_worker.stores import MemoryStore

    config = Config(
        "https://sds.test", "k", "o", "w", None, tmp_path / "t", tmp_path, "0123456789ab"
    )
    store = MemoryStore()
    assert open_github_app(config, store) is None
    configured = replace(config, github_app_id="5383095", github_app_key_id="id")
    assert isinstance(open_github_app(configured, store), RotationApp)


# ---- adopt-keeper and pg-restore, through the entry point ------------------------------------

KEEPERS = "33333333-3333-4333-8333-333333333333"
PASSWORD = "5ab2e4d8eec1" + "0" * 52


def _pg_config(tmp_path: Path) -> Config:
    from dataclasses import replace

    return replace(_config(tmp_path), keepers_project_id=KEEPERS)


def _entry(**changes: object) -> RegistryEntry:
    credential = {
        "id": "alobar-id-postgres",
        "class": "postgres-role-password",
        "provider": "postgres-role",
        "bws_uuid": "00000000-0000-4000-8000-000000000000",
        "consumer": [],
    }
    return RegistryEntry("r", "c" * 40, {**credential, **changes})


@pytest.fixture
def entry_point(tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    """`cli.main` with the configuration, the registry and the clones faked, and stdin to set."""
    monkeypatch.setattr(cli, "load", lambda path: _pg_config(tmp_path))
    monkeypatch.setattr(cli, "configure", lambda: None)
    monkeypatch.setattr(cli, "find_credential", lambda mirror, cid: _entry())
    monkeypatch.setattr(cli, "RepoMirror", lambda root: None)
    return store


def _stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def test_adopt_keeper_reads_stdin_not_argv(
    entry_point: MemoryStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stdin(monkeypatch, PASSWORD + "\n")
    assert cli.main(["adopt-keeper", "alobar-id-postgres"]) == 0
    printed = capsys.readouterr()
    project, key, value = next(iter(entry_point.secrets.values()))
    assert (project, key, value.reveal()) == (KEEPERS, "alobar-id-postgres", PASSWORD)
    assert json.loads(printed.out)["sha256_prefix"] == value.fingerprint.sha256_prefix
    assert PASSWORD not in printed.out + printed.err
    with pytest.raises(SystemExit):
        cli.main(["adopt-keeper", "alobar-id-postgres", PASSWORD])


@pytest.mark.parametrize(
    "text", ["", "short", PASSWORD.upper(), PASSWORD + "0", "g" * 64, PASSWORD[:-1] + "\n0"]
)
def test_adopt_keeper_refuses_a_wrong_shape(
    entry_point: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str,
) -> None:
    _stdin(monkeypatch, text)
    assert cli.main(["adopt-keeper", "alobar-id-postgres"]) == cli.EXIT_UNREADABLE
    assert entry_point.secrets == {}
    assert text.strip() not in capsys.readouterr().err or not text.strip()


def test_adopt_keeper_refuses_another_class_and_an_existing_secret(
    entry_point: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "find_credential", lambda mirror, cid: _entry(**{"class": "x"}))
    _stdin(monkeypatch, PASSWORD)
    assert cli.main(["adopt-keeper", "alobar-id-postgres"]) == cli.EXIT_UNREADABLE
    monkeypatch.setattr(cli, "find_credential", lambda mirror, cid: _entry())
    entry_point.put(KEEPERS, "alobar-id-postgres", Value("earlier"))
    _stdin(monkeypatch, PASSWORD)
    assert cli.main(["adopt-keeper", "alobar-id-postgres"]) == cli.EXIT_UNREADABLE
    assert [v.reveal() for _, _, v in entry_point.secrets.values()] == ["earlier"]


def test_pg_restore_uses_the_keepers_value(
    entry_point: MemoryStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    keeper = entry_point.put(KEEPERS, "alobar-id-postgres", Value(PASSWORD))
    monkeypatch.setattr(cli, "find_credential", lambda mirror, cid: _entry(bws_uuid=keeper))
    restored: list[Value] = []

    class Provider(cli.postgres_role.PostgresRoleProvider):
        def __init__(self) -> None:
            pass

        def restore(self, value: Value, replaced: Value | None = None) -> str:
            restored.append(value)
            return "put it back"

    seen: list[AdapterContext] = []

    def build(context: AdapterContext) -> Provider:
        seen.append(context)
        return Provider()

    monkeypatch.setitem(
        cli.ADAPTERS,
        "postgres-role",
        SimpleNamespace(build=build),  # type: ignore[arg-type]
    )
    setup = SimpleNamespace(
        mirror=None,
        store=entry_point,
        work_project_id=WORK,
        state_dir=tmp_path,
        admin_secrets={"postgres-role": "tok"},
        coolify_url="https://coolify.test",
        pg_helper=None,
    )
    monkeypatch.setattr(cli, "build_setup", lambda config: setup)
    assert cli.main(["pg-restore", "alobar-id-postgres"]) == 0
    assert [v.reveal() for v in restored] == [PASSWORD]
    assert seen[0].rotation is not None and seen[0].rotation.keeper_id == keeper
    assert seen[0].admin_secret_id == "tok"
    out = capsys.readouterr()
    assert out.out == "pg-restore: put it back\n"
    assert PASSWORD not in out.out + out.err
