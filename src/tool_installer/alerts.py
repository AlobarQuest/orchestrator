"""Tell a person when a tool fails to install: one keyed alert-hub alert per tool.

Before this, the finding (exit 3) reached only the launcher's log, and `rea` failed on every
logged pass with nobody the wiser. The observation each pass files is still the finding's durable
home; this is the page.

* **Fires** for a row whose action is in `FAILING_ACTIONS`; **resolves** when the tool's row is
  clean on a later acting pass.
* **A row the pass didn't act on is unread**, not clean: a closed window or a missing toolchain
  leaves `ACTION_NOT_PERMITTED`, which says nothing about whether the last install worked, so its
  remembered key neither fires nor resolves.
* **Only the acting pass tells** (`--install` without `--dry-run`); the caller never builds a hub
  otherwise, so a measuring pass needs no alert token.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Final

from alert_hub_client import Alert, KeyedAlerts, alert_key
from tool_installer.record import (
    ACTION_NOT_PERMITTED,
    FAILING_ACTIONS,
    Installation,
    summary_of,
)

USER_AGENT: Final = "tool-installer/1 (+AlobarQuest/orchestrator)"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "TOOL_INSTALLER_STATE_DIR"
ALERTS_FILE: Final = "alerts.json"


def state_dir(home: Path, configured: str | None) -> Path:
    if configured:
        return Path(configured)
    return home / "Library" / "Application Support" / "sds" / "tool-installer"


def tool_key(tool: str) -> str:
    return alert_key("tool-install", tool)


def classify(rows: Iterable[Installation]) -> tuple[dict[str, Alert], set[str]]:
    """The alerts that fire this pass, and the keys of tools the pass couldn't judge."""
    firing: dict[str, Alert] = {}
    unread: set[str] = set()
    for row in rows:
        key = tool_key(row.tool.name)
        if row.action in FAILING_ACTIONS:
            firing[key] = Alert(f"{row.tool.name} failed to install", summary_of(row))
        elif row.action == ACTION_NOT_PERMITTED:
            unread.add(key)
    return firing, unread


def open_hub(token: str, directory: Path) -> KeyedAlerts:
    return KeyedAlerts(
        lambda: token,
        directory / ALERTS_FILE,
        user_agent=USER_AGENT,
        resolved_title="Tool installs again",
    )
