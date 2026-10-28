"""The failed units this lane tells a person about, through alert-hub (bridge Part 3, Ruling 8).

A record in the approved queue whose work has a `failed` unit holds one alert open, keyed exactly
as the bump proposer keys the same bump (`dependency_bump_key`, Ruling 5). A failed unit stays
failed until a person retries or cancels it, so the alert is true while it lasts. The key fires
once and resolves on the first acting pass that no longer raises it: the record was retired, or a
newer revision superseded it.

A key is never resolved by a pass that could not read it: a record whose work the orchestrator
would not answer about holds its key (`Raised.held`), and a package this pass could not read holds
every remembered key (`Raised.hold_all`), because the key it would have fired is unknown. A bare
pass never reaches this module, so it sends nothing and writes no state file.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

import httpx

from alert_hub_client import Alert, AlertHubError, KeyedAlerts, dependency_bump_key
from standing_identity import BumpIdentity

USER_AGENT: Final = "work-watcher/1 (+AlobarQuest/orchestrator)"
STATE_FILE: Final = "alerts.json"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "WORK_WATCHER_STATE_DIR"
DEFAULT_STATE_DIR: Final = Path.home() / "Library" / "Application Support" / "sds" / "work-watcher"


@dataclass
class Raised:
    """What one pass raised: the keys firing, the keys `held`, and whether every key is held."""

    firing: dict[str, Alert] = field(default_factory=dict)
    held: set[str] = field(default_factory=set)
    hold_all: bool = False


class AlertSink(Protocol):
    def sync(self, firing: Mapping[str, Alert], held: set[str], hold_all: bool) -> str | None:
        """Bring alert-hub in line with `firing`. Answers why it could not, or None."""
        ...


def key_for(identity: BumpIdentity) -> str:
    return dependency_bump_key(identity.repository, identity.ecosystem, identity.dependency)


def failed_unit_alert(change_record_id: int, package_id: str, states: tuple[str, ...]) -> Alert:
    return Alert(
        f"Dependency bump failed to build: {package_id}",
        f"Change record {change_record_id}: its work is {', '.join(states)}. A failed unit stays "
        "failed until a person retries or cancels it.",
    )


class HubAlerts:
    def __init__(
        self,
        token: str,
        state_dir: Path,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._dir = state_dir
        self._alerts = KeyedAlerts(
            lambda: token,
            state_dir / STATE_FILE,
            user_agent=USER_AGENT,
            resolved_title="Dependency bump build recovered",
            transport=transport,
        )

    def sync(self, firing: Mapping[str, Alert], held: set[str], hold_all: bool) -> str | None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            unread = self._alerts.remembered() if hold_all else held
            self._alerts.sync(firing, unread)
        except AlertHubError as error:
            return f"alert-hub: {error}"
        except OSError as error:
            return f"alert-hub: the state directory is unusable: {type(error).__name__}"
        return None


def from_environment() -> HubAlerts | None:
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        return None
    return HubAlerts(token, Path(os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR))
