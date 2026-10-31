"""The executor's configuration: ids and paths only. The configuration file holds no value.

ADR-0055 decision 2: on the VM (`store = "bws"`, the default) the root token lives in a file
readable only by the service's user, and the executor refuses one that anyone else could read.
Everything else it needs (its WORKER bearer) it reads from BWS with that token.

On the operator machine (`store = "keychain"`, amendment 12) there is no root token: the
executor's state is in the login Keychain. `work_project_id` is then the Keychain service its
staging, quarantine and bearer items live under, and `bearer_secret_id` and
`healthchecks_key_id` are account names under it. Its one BWS secret is its inbox, through an identity of its own whose token is the Keychain item
`inbox_token_id` names (ADR-0055 amendment 15, increment 3c).
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from rotation_worker.values import Value

DEFAULT_PATH: Final = Path("/etc/rotation-worker/config.toml")
UUID_PATTERN: Final = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
REVISION_PATTERN: Final = re.compile(r"^[0-9a-f]{7,12}$")
APP_ID_PATTERN: Final = re.compile(r"^[1-9][0-9]{0,11}$")
KEYCHAIN_NAME: Final = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SSH_DESTINATION: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$")
KNOWN_HOST_KEY: Final = re.compile(r"^(?:ssh|ecdsa|sk)-[a-z0-9@.-]+ [A-Za-z0-9+/]+={0,2}$")
STORES: Final = ("bws", "keychain")
# What only the VM instance's configuration may name.
VM_ONLY: Final = (
    "root_token_file",
    "admin_secrets",
    "coolify_url",
    "orchestrator_application",
    "change_manager_application",
    "github_app_id",
    "github_app_key_id",
    "pg_helper_key_id",
    "pg_helper_host",
    "pg_helper_known_host",
    "keepers_project_id",
)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    orchestrator_url: str
    # The key id the bearer's hash is registered under in `ORCHESTRATOR_M2M_CREDENTIALS`.
    credential_key_id: str
    organization_id: str | None
    work_project_id: str
    # Absent until the executor's WORKER bearer exists (`rotation-worker mint-bearer`).
    bearer_secret_id: str | None
    root_token_file: Path | None
    state_dir: Path
    source_revision: str
    store: str = "bws"
    # Each provider's admin secret, by provider name: the BWS UUID an adapter reads when a step
    # mints or revokes (decision 4). Holds ids only, never a value.
    admin_secrets: Mapping[str, str] = field(default_factory=dict)
    # Production Coolify's API, over the tailnet (ADR-0055 amendment 9). Absent until an adapter
    # writes a hosted service.
    coolify_url: str | None = None
    # The orchestrator's Coolify application uuid: the one application the orchestrator adapter
    # may write, whatever a package names.
    orchestrator_application: str | None = None
    # change-manager's Coolify application uuid: the one application the change-manager adapter
    # may write, whatever a package names.
    change_manager_application: str | None = None
    # The read-only Healthchecks key in `Rotation / Healthchecks`, read only when a scheduled
    # reader's check is (ADR-0055 amendment 9, decision 5).
    healthchecks_key_id: str | None = None
    # The rotation GitHub App (increment 7): its numeric id, and its private key's secret id in
    # `Rotation / Admin`. Both or neither; read only when a step writes or reads an Actions secret.
    github_app_id: str | None = None
    github_app_key_id: str | None = None
    # The VPS Postgres helper (`helper_client.py`): its SSH private key's secret id, the SSH
    # destination, and the pinned host-key line. All three or none; the key is read only when a
    # step needs the helper.
    pg_helper_key_id: str | None = None
    pg_helper_host: str | None = None
    pg_helper_known_host: str | None = None
    # `Rotation / Keepers`, where `adopt-keeper` creates a keeper. Read by no rotation step.
    keepers_project_id: str | None = None
    # This instance's alert-hub sender token: a BWS UUID, or a Keychain account name. Absent, no
    # act is announced (ADR-0055 amendment 15, increment 3).
    alert_hub_token_id: str | None = None
    # The BWS secret a person pastes a new value into (`inbox.py`), and on the Keychain instance
    # the Keychain account holding the token of the identity that reaches it. Absent, a person's
    # mint is staged by hand.
    inbox_secret_id: str | None = None
    inbox_token_id: str | None = None

    @property
    def mirror_root(self) -> Path:
        return self.state_dir / "mirrors"


def load(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error
    store = raw.get("store", "bws")
    if store not in STORES:
        raise ConfigError(f"store is one of {', '.join(STORES)}")
    if store == "keychain":
        return _keychain(raw)
    for key in ("organization_id", "work_project_id"):
        if not UUID_PATTERN.fullmatch(_string(raw, key)):
            raise ConfigError(f"{key} must be a lowercase UUID")
    bearer = _optional_uuid(raw, "bearer_secret_id")
    healthchecks_key = _optional_uuid(raw, "healthchecks_key_id")
    alert_hub_token = _optional_uuid(raw, "alert_hub_token_id")
    if "inbox_token_id" in raw:
        raise ConfigError("the VM's inbox is read with the root token: it names no inbox_token_id")
    app_id, app_key = _github_app(raw)
    helper_key, helper_host, helper_known_host = _pg_helper(raw)
    admin_secrets = raw.get("admin_secrets", {})
    if not isinstance(admin_secrets, dict) or not all(
        isinstance(name, str) and isinstance(uuid, str) and UUID_PATTERN.fullmatch(uuid)
        for name, uuid in admin_secrets.items()
    ):
        raise ConfigError("admin_secrets maps provider names to lowercase UUIDs")
    revision = _string(raw, "source_revision")
    if not REVISION_PATTERN.fullmatch(revision):
        raise ConfigError("source_revision must be 7 to 12 hex characters of the commit")
    return Config(
        orchestrator_url=_string(raw, "orchestrator_url"),
        credential_key_id=_string(raw, "credential_key_id"),
        organization_id=_string(raw, "organization_id"),
        work_project_id=_string(raw, "work_project_id"),
        bearer_secret_id=bearer,
        root_token_file=Path(_string(raw, "root_token_file")),
        state_dir=Path(_string(raw, "state_dir")),
        source_revision=revision,
        admin_secrets=dict(admin_secrets),
        coolify_url=_optional_string(raw, "coolify_url"),
        orchestrator_application=_optional_string(raw, "orchestrator_application"),
        change_manager_application=_optional_string(raw, "change_manager_application"),
        healthchecks_key_id=healthchecks_key,
        github_app_id=app_id,
        github_app_key_id=app_key,
        pg_helper_key_id=helper_key,
        pg_helper_host=helper_host,
        pg_helper_known_host=helper_known_host,
        keepers_project_id=_optional_uuid(raw, "keepers_project_id"),
        alert_hub_token_id=alert_hub_token,
        inbox_secret_id=_optional_uuid(raw, "inbox_secret_id"),
    )


def _optional_uuid(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and (not isinstance(value, str) or not UUID_PATTERN.fullmatch(value)):
        raise ConfigError(f"{key} must be a lowercase UUID")
    return value


def _keychain(raw: dict[str, Any]) -> Config:
    named = [key for key in VM_ONLY if key in raw]
    if named:
        raise ConfigError(f"a Keychain executor names no {', '.join(named)}")
    keychain_names = (
        "work_project_id",
        "bearer_secret_id",
        "healthchecks_key_id",
        "alert_hub_token_id",
        "inbox_token_id",
    )
    for key in keychain_names:
        value = raw.get(key)
        if (value is not None or key == "work_project_id") and (
            not isinstance(value, str) or not KEYCHAIN_NAME.fullmatch(value)
        ):
            raise ConfigError(f"{key} is a Keychain name: letters, digits, '.', '_' or '-'")
    inbox = [_optional_uuid(raw, key) for key in ("organization_id", "inbox_secret_id")]
    named = [*inbox, raw.get("inbox_token_id")]
    if any(value is None for value in named) and any(value is not None for value in named):
        raise ConfigError("an inbox needs organization_id, inbox_secret_id and inbox_token_id")
    revision = _string(raw, "source_revision")
    if not REVISION_PATTERN.fullmatch(revision):
        raise ConfigError("source_revision must be 7 to 12 hex characters of the commit")
    return Config(
        orchestrator_url=_string(raw, "orchestrator_url"),
        credential_key_id=_string(raw, "credential_key_id"),
        organization_id=inbox[0],
        work_project_id=_string(raw, "work_project_id"),
        bearer_secret_id=raw.get("bearer_secret_id"),
        root_token_file=None,
        state_dir=Path(_string(raw, "state_dir")),
        source_revision=revision,
        store="keychain",
        healthchecks_key_id=raw.get("healthchecks_key_id"),
        alert_hub_token_id=raw.get("alert_hub_token_id"),
        inbox_secret_id=inbox[1],
        inbox_token_id=raw.get("inbox_token_id"),
    )


def _github_app(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    app_id, app_key = raw.get("github_app_id"), raw.get("github_app_key_id")
    if app_id is None and app_key is None:
        return None, None
    if not isinstance(app_id, str) or not APP_ID_PATTERN.fullmatch(app_id):
        raise ConfigError("github_app_id is the App's numeric id, as a string, with its key id")
    if not isinstance(app_key, str) or not UUID_PATTERN.fullmatch(app_key):
        raise ConfigError("github_app_key_id must be a lowercase UUID, with the App's id")
    return app_id, app_key


def _pg_helper(raw: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    names = ("pg_helper_key_id", "pg_helper_host", "pg_helper_known_host")
    present = [raw.get(name) for name in names]
    if all(value is None for value in present):
        return None, None, None
    key, host, known_host = present
    if not isinstance(key, str) or not UUID_PATTERN.fullmatch(key):
        raise ConfigError("pg_helper_key_id must be a lowercase UUID, with the helper's host")
    if not isinstance(host, str) or not SSH_DESTINATION.fullmatch(host):
        raise ConfigError(
            "pg_helper_host is an ssh destination, with the helper's key and host key"
        )
    if not isinstance(known_host, str) or not _pins(host, known_host.strip()):
        raise ConfigError(
            "pg_helper_known_host is exactly '<host> <keytype> <base64>' for pg_helper_host"
        )
    return key, host, known_host.strip()


def _pins(destination: str, line: str) -> bool:
    """Whether `line` pins one key for exactly the destination's host: no marker, no pattern."""
    fields = line.split(" ")
    if len(fields) != 3 or not KNOWN_HOST_KEY.fullmatch(" ".join(fields[1:])):
        return False
    return fields[0] == destination.rsplit("@", 1)[-1]


def read_root_token(path: Path) -> Value:
    """The root token, from a file only this user may read or write."""
    try:
        status = path.stat()
    except OSError as error:
        raise ConfigError(f"the root token file can't be read: {type(error).__name__}") from None
    if status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) & 0o077:
        raise ConfigError("the root token file must belong to this user and be mode 0600 or 0400")
    text = path.read_text().strip()
    if not text:
        raise ConfigError("the root token file is empty")
    return Value(text)


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    return _string(raw, key) if key in raw else None


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} is required")
    return value.strip()
