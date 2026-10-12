"""The executor's configuration: ids and paths only. The one secret it starts from is a file.

ADR-0055 decision 2: the root token lives in a file readable only by the service's user, and the
executor refuses one that anyone else could read. Everything else it needs (its WORKER bearer)
it reads from BWS with that token. The configuration file itself holds no value.
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


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    orchestrator_url: str
    # The key id the bearer's hash is registered under in `ORCHESTRATOR_M2M_CREDENTIALS`.
    credential_key_id: str
    organization_id: str
    work_project_id: str
    # Absent until the executor's WORKER bearer exists (`rotation-worker mint-bearer`).
    bearer_secret_id: str | None
    root_token_file: Path
    state_dir: Path
    source_revision: str
    # Each provider's admin secret, by provider name: the BWS UUID an adapter reads when a step
    # mints or revokes (decision 4). Holds ids only, never a value.
    admin_secrets: Mapping[str, str] = field(default_factory=dict)

    @property
    def mirror_root(self) -> Path:
        return self.state_dir / "mirrors"


def load(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error
    for key in ("organization_id", "work_project_id"):
        if not UUID_PATTERN.fullmatch(_string(raw, key)):
            raise ConfigError(f"{key} must be a lowercase UUID")
    bearer = raw.get("bearer_secret_id")
    if bearer is not None and (not isinstance(bearer, str) or not UUID_PATTERN.fullmatch(bearer)):
        raise ConfigError("bearer_secret_id must be a lowercase UUID")
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
    )


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


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} is required")
    return value.strip()
