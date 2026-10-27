"""Telling a person that a rotation waits on them (ADR-0055 amendment 15, increment 3).

A rotation unit blocked on a human act (a console mint, a revoke) waits silently unless someone
looks. Each pass, the executor sends one alert-hub alert per act its own units wait on, keyed by
the act's dependency, and resolves it once the act is no longer pending. alert-hub dedups by key,
so resending an open act every pass costs nothing and lets alert-hub repeat it on its own schedule.

A send never fails a unit or a pass: an alert that can't be sent is logged `notify_failed` and
tried again next pass. The keys sent as firing are kept in the state directory, so a later pass
knows what to resolve; losing that file only leaves an alert open until a person closes it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.contract import ContractError
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value, scrub

LOG = logging.getLogger("rotation_worker")
BASE_URL: Final = "https://alerts.alobar.net"
ALERTS: Final = "/v1/alerts"
USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 10.0
ACCEPTED: Final = 202
STATE_FILE: Final = "announced-acts.json"


@dataclass(frozen=True)
class Act:
    """One human act a unit waits on: its alert-hub key, a title, and what to do."""

    key: str
    title: str
    message: str


class Notifier:
    def __init__(
        self,
        token: Callable[[], Value],
        state_dir: Path,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token
        self._state = state_dir / STATE_FILE
        self._transport = transport

    def announce(self, waiting: Mapping[str, Act]) -> None:
        """Fire every act in `waiting`, and resolve every act announced before that isn't."""
        announced = self._announced()
        still_open = set()
        client = self._client()
        for key, act in waiting.items():
            if self._send(client, act, "firing"):
                still_open.add(key)
            elif key in announced:
                still_open.add(key)
        for key in announced - waiting.keys():
            if not self._send(client, Act(key, "Rotation act done", ""), "resolved"):
                still_open.add(key)
        self._save(still_open)

    def _client(self) -> ConfinedClient | None:
        try:
            token = self._token()
            return ConfinedClient(
                base_url=BASE_URL,
                user_agent=USER_AGENT,
                headers={"Authorization": f"Bearer {token.reveal()}"},
                timeout=TIMEOUT_SECONDS,
                permits=lambda method, path: (method, path) == ("POST", ALERTS),
                refuse=lambda method, path: ContractError(f"alert-hub may not {method} {path}"),
                transport=self._transport,
            )
        except StoreError as error:
            LOG.warning("notify_failed", extra={"reason": scrub(str(error))})
        except TransportFailure as failure:
            LOG.warning("notify_failed", extra={"reason": failure.error_type})
        return None

    def _send(self, client: ConfinedClient | None, act: Act, status: str) -> bool:
        if client is None:
            return False
        payload = {
            "key": act.key,
            "severity": "warning",
            "kind": "alert",
            "status": status,
            "title": act.title,
            **({"message": act.message} if act.message else {}),
        }
        try:
            response = client.request("POST", ALERTS, json=payload)
        except TransportFailure as failure:
            LOG.warning("notify_failed", extra={"key": act.key, "reason": failure.error_type})
            return False
        if response.status_code != ACCEPTED:
            LOG.warning("notify_failed", extra={"key": act.key, "status": response.status_code})
            return False
        LOG.info("notified", extra={"key": act.key, "status": status})
        return True

    def _announced(self) -> set[str]:
        try:
            keys = json.loads(self._state.read_text())
        except OSError, ValueError:
            return set()
        return {key for key in keys if isinstance(key, str)} if isinstance(keys, list) else set()

    def _save(self, keys: set[str]) -> None:
        self._state.write_text(json.dumps(sorted(keys)))
