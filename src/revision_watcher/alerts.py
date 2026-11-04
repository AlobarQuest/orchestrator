"""Tell a person that an application is not serving what its branch names: one keyed alert each.

Before this, exit 2 reached only the launcher's log and the observation each pass files; the
dead-man check pings success on a finding, so nobody heard. The observation stays the finding's
durable home; this is the page.

* **The key is the subject's name**, the same spelling for a firing reading and an unreadable one,
  so an application the pass could not read keeps its remembered key instead of resolving it.
* **An undeclared application raises nothing.** It is reported and is not a finding, because it
  may not be the SDS's.
* **Only the acting pass tells** (any pass without `--dry-run`); a measuring pass needs no token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from alert_hub_client import Alert, KeyedAlerts, alert_key
from revision_watcher.census import Pass, Reading

USER_AGENT: Final = "revision-watcher/1 (+AlobarQuest/orchestrator)"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "REVISION_WATCHER_STATE_DIR"
ALERTS_FILE: Final = "alerts.json"


def state_dir(home: Path, configured: str | None) -> Path:
    if configured:
        return Path(configured)
    return home / "Library" / "Application Support" / "sds" / "revision-watcher"


def application_key(name: str) -> str:
    return alert_key("revision-stale", name)


def _alert(reading: Reading) -> Alert:
    name = reading.subject.name
    served = (reading.served or "<none>")[:12]
    expected = (reading.expected or "<unknown>")[:12]
    return Alert(
        f"{name} is not serving what its branch names",
        f"{reading.state}: serving {served}, branch names {expected} [{reading.landing}]"
        + (f" - {reading.detail}" if reading.detail else ""),
    )


def classify(result: Pass) -> tuple[dict[str, Alert], set[str]]:
    """The alerts that fire this pass, and the keys of applications the pass couldn't read."""
    firing = {application_key(r.subject.name): _alert(r) for r in result.findings}
    unread = {application_key(r.subject.name) for r in result.unreadable}
    return firing, unread


def open_hub(token: str, directory: Path) -> KeyedAlerts:
    return KeyedAlerts(
        lambda: token,
        directory / ALERTS_FILE,
        user_agent=USER_AGENT,
        resolved_title="Application serves its branch again",
    )
