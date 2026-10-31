"""Telling a person that a rotation waits on them (ADR-0055 amendment 15, increment 3).

A rotation unit blocked on a human act (a console mint, a revoke) waits silently unless someone
looks. Each pass, the executor sends one alert-hub alert per act its own units wait on, keyed by
the act's dependency, and resolves it once the act is no longer pending. alert-hub dedups by key,
so resending an open act every pass costs nothing and lets alert-hub repeat it on its own schedule
(`refire`: the shared client, `alert_hub_client`, would otherwise fire a key once).

A send never fails a unit or a pass: an alert that can't be sent is logged `notify_failed` and
tried again next pass. The keys sent as firing are kept in the state directory, so a later pass
knows what to resolve; losing that file only leaves an alert open until a person closes it.

The same sync carries what a person should know about a unit that isn't an act:
`rotation-unit-failed`, `-review`, `-stuck` and `-waiting`, keyed by unit (`worker.unit_alerts`,
`worker._overdue`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import httpx

from alert_hub_client import (
    ALERTS,
    Alert,
    AlertHubError,
    FirstSightings,
    KeyedAlerts,
    StateUnwritable,
    TokenUnreadable,
    alert_key,
)
from rotation_worker.contract import ContractError
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value, scrub

LOG = logging.getLogger("rotation_worker")
USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
STATE_FILE: Final = "announced-acts.json"

WAIT_CLOCK_FILE: Final = "wait-since.json"

__all__ = [
    "ALERTS",
    "STATE_FILE",
    "USER_AGENT",
    "Act",
    "Notifier",
    "first_sightings",
    "unit_failed_key",
    "unit_reader_clock_key",
    "unit_review_key",
    "unit_stuck_key",
    "unit_waiting_key",
]


def unit_failed_key(unit_id: str) -> str:
    return alert_key("rotation-unit-failed", unit_id)


def unit_review_key(unit_id: str) -> str:
    return alert_key("rotation-unit-review", unit_id)


def unit_reader_clock_key(unit_id: str) -> str:
    """A reader wait's entry in the wait clock. It names no alert: an overdue one raises
    `unit_waiting_key`."""
    return alert_key("rotation-unit-reader-wait", unit_id)


def unit_stuck_key(unit_id: str) -> str:
    return alert_key("rotation-unit-stuck", unit_id)


def unit_waiting_key(unit_id: str) -> str:
    return alert_key("rotation-unit-waiting", unit_id)


@dataclass(frozen=True)
class Act:
    """One human act a unit waits on: its alert-hub key, a title, and what to do."""

    key: str
    title: str
    message: str
    link: str | None = None


def first_sightings(
    state_dir: Path, current: Set[str], now: datetime, keep: Set[str]
) -> dict[str, datetime] | None:
    """When each key in `current` was first seen, or `None` if the clock file can't be saved."""
    try:
        return FirstSightings(state_dir / WAIT_CLOCK_FILE).update(current, now, keep)
    except StateUnwritable as error:
        LOG.warning("notify_state_unsaved", extra={"reason": scrub(error.reason)})
        return None


class Notifier:
    def __init__(
        self,
        token: Callable[[], Value],
        state_dir: Path,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._alerts = KeyedAlerts(
            lambda: self._reveal(token),
            state_dir / STATE_FILE,
            user_agent=USER_AGENT,
            refire=True,
            resolved_title="Rotation act done",
            refuse=lambda method, path: ContractError(f"alert-hub may not {method} {path}"),
            logger=LOG,
            transport=transport,
        )

    def announce(self, waiting: Mapping[str, Act], unread: Set[str] = frozenset()) -> None:
        """Fire every act in `waiting`, and resolve every act announced before that isn't,
        except those in `unread`, whose units couldn't be read: they stay as they were."""
        firing = {key: Alert(act.title, act.message, act.link) for key, act in waiting.items()}
        try:
            self._alerts.sync(firing, unread)
        except TokenUnreadable:
            pass  # `_reveal` logged the store's reason
        except StateUnwritable as error:
            LOG.warning("notify_state_unsaved", extra={"reason": scrub(error.reason)})
        except AlertHubError as error:
            LOG.warning("notify_failed", extra={"reason": scrub(str(error))})

    @staticmethod
    def _reveal(token: Callable[[], Value]) -> str:
        try:
            return token().reveal()
        except StoreError as error:
            LOG.warning("notify_failed", extra={"reason": scrub(str(error))})
            raise
