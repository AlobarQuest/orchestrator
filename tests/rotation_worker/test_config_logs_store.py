"""Configuration, the root token file, the JSON log line, and the BWS store's use of the SDK."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rotation_worker.config import ConfigError, load, read_root_token
from rotation_worker.logs import JsonFormatter, configure
from rotation_worker.secret_store import BwsStore, StoreError
from rotation_worker.values import Value

ORG = "11111111-1111-4111-8111-111111111111"
PROJECT = "22222222-2222-4222-8222-222222222222"
CONFIG = f"""
orchestrator_url = "https://sds.alobar.net"
credential_key_id = "rotation-executor-1"
organization_id = "{ORG}"
work_project_id = "{PROJECT}"
root_token_file = "/etc/rotation-worker/root-token"
state_dir = "/var/lib/rotation-worker"
source_revision = "0123456789ab"
"""


def test_a_complete_config_loads(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    config = load(path)
    assert config.credential_key_id == "rotation-executor-1"
    assert config.mirror_root == Path("/var/lib/rotation-worker/mirrors")
    assert config.healthchecks_key_id is None
    path.write_text(CONFIG + f'healthchecks_key_id = "{PROJECT}"\n')
    assert load(path).healthchecks_key_id == PROJECT


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f'organization_id = "{ORG}"', 'organization_id = "not-a-uuid"'),
        (
            f'work_project_id = "{PROJECT}"',
            'work_project_id = "AAAAAAAA-2222-4222-8222-222222222222"',
        ),
        ('source_revision = "0123456789ab"', 'source_revision = "' + "0" * 40 + '"'),
        ('source_revision = "0123456789ab"', 'source_revision = "main"'),
        ('credential_key_id = "rotation-executor-1"', 'credential_key_id = " "'),
        ('state_dir = "/var/lib/rotation-worker"', ""),
        ('state_dir = "/var/lib/rotation-worker"', 'healthchecks_key_id = "nope"'),
    ],
)
def test_a_malformed_config_is_refused(tmp_path: Path, old: str, new: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.replace(old, new))
    with pytest.raises(ConfigError):
        load(path)


def test_admin_secrets_map_provider_names_to_uuids(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    assert load(path).admin_secrets == {}
    path.write_text(CONFIG + f'[admin_secrets]\nopenrouter = "{PROJECT}"\n')
    assert load(path).admin_secrets == {"openrouter": PROJECT}


@pytest.mark.parametrize(
    "table",
    [
        '[admin_secrets]\nopenrouter = "not-a-uuid"\n',
        '[admin_secrets]\nopenrouter = "AAAAAAAA-2222-4222-8222-222222222222"\n',
        "[admin_secrets]\nopenrouter = 7\n",
        'admin_secrets = "openrouter"\n',
    ],
)
def test_a_malformed_admin_secret_is_refused(tmp_path: Path, table: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG + table)
    with pytest.raises(ConfigError, match="admin_secrets"):
        load(path)


def test_an_unreadable_config_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load(tmp_path / "absent.toml")
    (tmp_path / "bad.toml").write_text("a = [")
    with pytest.raises(ConfigError):
        load(tmp_path / "bad.toml")


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_a_private_root_token_file_is_read(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "root-token"
    path.write_text("a-throwaway-token\n")
    os.chmod(path, mode)
    assert read_root_token(path).same_as(Value("a-throwaway-token"))


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660])
def test_a_root_token_file_others_could_read_is_refused(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "root-token"
    path.write_text("a-throwaway-token")
    os.chmod(path, mode)
    with pytest.raises(ConfigError, match="mode 0600 or 0400"):
        read_root_token(path)


def test_a_missing_or_empty_root_token_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="can't be read"):
        read_root_token(tmp_path / "absent")
    path = tmp_path / "empty"
    path.write_text("\n")
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError, match="empty"):
        read_root_token(path)


def test_a_log_line_is_json_with_its_extras_and_no_value() -> None:
    secret = Value("a-value-that-must-never-be-logged")
    record = logging.LogRecord("rotation_worker", logging.ERROR, "x", 1, "unit_failed", None, None)
    record.unit = "u1"
    record.reason = f"probe said {secret.reveal()}"
    line = json.loads(JsonFormatter().format(record))
    assert line["event"] == "unit_failed" and line["level"] == "error" and line["unit"] == "u1"
    assert secret.reveal() not in json.dumps(line)
    assert "[redacted]" in line["reason"]


class FakeSecrets:
    """The SDK's secrets client, answering as `bitwarden_sdk` does: success, data, error message."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.updated: list[tuple[Any, ...]] = []

    def get(self, secret_id: str) -> Any:
        row = self.rows.get(secret_id)
        if row is None:
            raise Exception(f"404 Resource not found: {secret_id}")
        return SimpleNamespace(success=True, data=SimpleNamespace(**row))

    def list(self, organization_id: str) -> Any:
        entries = [
            SimpleNamespace(
                id=uuid.UUID(sid), key=row["key"], project_ids=[uuid.UUID(row["project_id"])]
            )
            for sid, row in self.rows.items()
        ]
        return SimpleNamespace(success=True, data=SimpleNamespace(data=entries))

    def update(self, *arguments: Any) -> Any:
        self.updated.append(arguments)
        _, secret_id, key, value, note, projects = arguments
        self.rows[secret_id] = {**self.rows[secret_id], "key": key, "value": value, "note": note}
        return SimpleNamespace(success=True, data=SimpleNamespace(id=secret_id))

    def delete(self, ids: list[str]) -> Any:
        return SimpleNamespace(success=False, data=None, error_message="refused by policy")


def _store(secrets: FakeSecrets) -> BwsStore:
    store = BwsStore.__new__(BwsStore)
    store._organization_id = ORG
    setattr(store, "_client", SimpleNamespace(secrets=lambda: secrets))
    return store


def _row(key: str, value: str, project: str = PROJECT) -> dict[str, Any]:
    return {"key": key, "value": value, "note": "kept", "project_id": project}


def test_write_keeps_the_key_note_and_project() -> None:
    secrets = FakeSecrets()
    sid = str(uuid.uuid4())
    secrets.rows[sid] = _row("keeper", "old-value")
    _store(secrets).write(sid, Value("new-value"))
    assert secrets.updated == [(ORG, sid, "keeper", "new-value", "kept", [PROJECT])]


def test_find_matches_key_and_project_and_refuses_a_duplicate() -> None:
    secrets = FakeSecrets()
    first, other = str(uuid.uuid4()), str(uuid.uuid4())
    secrets.rows[first] = _row("rotation/x/staging", "v")
    secrets.rows[other] = _row("rotation/x/staging", "v", project=ORG)
    store = _store(secrets)
    found = store.find(PROJECT, "rotation/x/staging")
    assert found is not None and found.id == first
    assert store.find(PROJECT, "absent") is None
    secrets.rows[str(uuid.uuid4())] = _row("rotation/x/staging", "v")
    with pytest.raises(StoreError, match="2 secrets"):
        store.find(PROJECT, "rotation/x/staging")


def test_an_sdk_failure_is_a_store_error_with_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = FakeSecrets()
    leaked = Value("a-value-in-a-server-message")
    sid = str(uuid.uuid4())
    secrets.rows[sid] = _row("k", "v")
    store = _store(secrets)
    with pytest.raises(StoreError, match="404"):
        store.read(str(uuid.uuid4()))
    with pytest.raises(StoreError, match="refused by policy"):
        store.delete(sid)

    def echoes(secret_id: str) -> Any:
        raise Exception(f"echo {leaked.reveal()}")

    monkeypatch.setattr(secrets, "get", echoes)
    with pytest.raises(StoreError) as raised:
        store.read(sid)
    assert leaked.reveal() not in str(raised.value)


def test_a_response_marked_unsuccessful_fails_even_with_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = FakeSecrets()
    sid = str(uuid.uuid4())
    secrets.rows[sid] = _row("k", "v")

    def half_answer(secret_id: str) -> Any:
        return SimpleNamespace(success=False, data=SimpleNamespace(value="v"), error_message="no")

    monkeypatch.setattr(secrets, "get", half_answer)
    with pytest.raises(StoreError, match="no"):
        _store(secrets).read(sid)


def test_a_value_json_would_escape_is_still_scrubbed() -> None:
    secret = Value('a"value\\with-escapes-é')
    record = logging.LogRecord("rotation_worker", logging.ERROR, "x", 1, "unit_failed", None, None)
    record.reason = f"probe said {secret.reveal()}"
    line = JsonFormatter().format(record)
    assert json.loads(line)["reason"] == "probe said [redacted]"
    assert json.dumps(secret.reveal())[1:-1] not in line


def test_request_urls_are_not_logged() -> None:
    configure()
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_revised_is_the_secret_s_revision_date() -> None:
    secrets = FakeSecrets()
    sid = str(uuid.uuid4())
    when = datetime(2026, 10, 16, 6, 5, tzinfo=UTC)
    secrets.rows[sid] = {**_row("keeper", "v"), "revision_date": when}
    assert _store(secrets).revised(sid) == when


def test_a_revision_date_without_a_zone_is_refused() -> None:
    secrets = FakeSecrets()
    sid = str(uuid.uuid4())
    secrets.rows[sid] = {**_row("keeper", "v"), "revision_date": datetime(2026, 10, 16, 6, 5)}
    with pytest.raises(StoreError, match="without a zone"):
        _store(secrets).revised(sid)
