"""Tell a person that a caller workflow is not at the recommended runner pin: one keyed alert each.

Before this, exit 2 reached only the launcher's log and the observation each pass files; the
dead-man check pings success on a finding, so nobody heard. The observation stays the finding's
durable home; this is the page.

* **The key is the repository**, the same spelling for a firing caller and an unreadable one, so a
  repository the pass could not read keeps its remembered key instead of resolving it.
* **Only the acting pass tells** (any pass without `--dry-run`); a measuring pass needs no token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from alert_hub_client import Alert, KeyedAlerts, alert_key
from pin_watcher.compare import Caller
from pin_watcher.measure import Pass

USER_AGENT: Final = "pin-watcher/1 (+AlobarQuest/orchestrator)"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "PIN_WATCHER_STATE_DIR"
ALERTS_FILE: Final = "alerts.json"


def state_dir(home: Path, configured: str | None) -> Path:
    if configured:
        return Path(configured)
    return home / "Library" / "Application Support" / "sds" / "pin-watcher"


def repository_key(repository: str) -> str:
    return alert_key("pin-behind", repository)


def _alert(caller: Caller, recommended: str) -> Alert:
    counts = f", {caller.behind_by} behind, {caller.ahead_by} ahead" if caller.behind_by else ""
    return Alert(
        f"{caller.repository} runs a runner pin that is not the recommendation",
        f"{caller.state}: pin {caller.pin[:12]}, recommended {recommended[:12]}{counts}",
    )


def classify(result: Pass) -> tuple[dict[str, Alert], set[str]]:
    """The alerts that fire this pass, and the keys of repositories the pass couldn't read."""
    firing = {
        repository_key(caller.repository): _alert(caller, result.recommended)
        for caller in result.findings
    }
    unread = {repository_key(repository) for repository in result.unreadable}
    return firing, unread


def open_hub(token: str, directory: Path) -> KeyedAlerts:
    return KeyedAlerts(
        lambda: token,
        directory / ALERTS_FILE,
        user_agent=USER_AGENT,
        resolved_title="Caller pin is at the recommendation",
    )
