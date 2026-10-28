"""The estate's one alert-hub client: a keyed set of open alerts, kept in step with a state file.

A lane that tells a person something needs them hands `KeyedAlerts.sync` the alerts that are
firing now, by key. alert-hub dedups on (sender, key) while an alert is open, and opens a NEW alert
when a key fires after a person closed the old one, so a lane must not re-fire a key every pass:
by default `sync` fires a key only when its state file does not already remember it. A caller whose
alert-hub schedule depends on hearing the key again each pass (the rotation worker) sets `refire`.
Either way a remembered key that stops firing is sent `resolved` and forgotten, and a key named in
`unread` (its condition couldn't be read this pass) is neither fired nor resolved.

* **The state file is the only memory**, written aside and renamed in the same directory. A client
  that fires once refuses to send anything if it cannot write the file, because an unrecorded
  send is a send repeated next pass.
* **A failed send is not remembered as sent** (a fire is retried next pass; a resolve stays
  remembered), and `sync` raises `AlertHubError` after saving the rest.
* **A 429 is retried once** after `Retry-After` seconds (alert-hub sends none today, so a bounded
  default stands in), then counts as a failure. A `refire` caller does not wait: it sends again
  next pass anyway, so a 429 is an immediate failure.
* **The token never leaves the request header.** Errors name the exception type and the status
  code, nothing the token store or `httpx` said.
* **Keys follow alert-hub's grammar** (1-200 of `[A-Za-z0-9._:/-]`); `alert_key` builds one from
  free text deterministically, and `sync` refuses a key outside it before sending anything.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure

LOG = logging.getLogger("alert_hub_client")
BASE_URL: Final = "https://alerts.alobar.net"
ALERTS: Final = "/v1/alerts"
TIMEOUT_SECONDS: Final = 10.0
ACCEPTED: Final = 202
RATE_LIMITED: Final = 429
DEFAULT_RETRY_WAIT: Final = 15.0
MAX_RETRY_WAIT: Final = 60.0
MAX_KEY = 200
KEY_GRAMMAR: Final = re.compile(r"[A-Za-z0-9._:/-]{1,200}")
# Left as they are inside a key part. `_` is the escape character and `:` the part separator, so
# neither is left alone.
_PLAIN: Final = re.compile(r"[A-Za-z0-9.\-/]")
_DIGEST_CHARS = 16


class AlertHubError(Exception):
    """An alert that could not be sent, or a state file that could not be kept."""


class TokenUnreadable(AlertHubError):
    """The sender token's store failed. Only the exception's type is kept: a store's own message
    may hold the token."""


class StateUnwritable(AlertHubError):
    """The state file could not be written. `reason` is the operating system's account of it."""

    def __init__(self, error: OSError) -> None:
        super().__init__(f"state file unwritable: {type(error).__name__}")
        self.reason = str(error)


@dataclass(frozen=True)
class Alert:
    title: str
    message: str = ""
    link: str | None = None


def _encode(part: str) -> str:
    out: list[str] = []
    for char in part:
        if char == "_":
            out.append("__")
        elif _PLAIN.fullmatch(char):
            out.append(char)
        else:
            out.extend(f"_{byte:02X}" for byte in char.encode("utf-8", errors="surrogatepass"))
    return "".join(out)


def alert_key(*parts: str) -> str:
    """Join free-text parts into one alert-hub key, `:`-separated.

    Within a part, `_` becomes `__` and any other character outside `[A-Za-z0-9.-/]` becomes `_`
    plus the two uppercase hex digits of each of its UTF-8 bytes (`@types/node` is
    `_40types/node`), so two different parts never share a key. A key over 200 characters keeps
    its head and ends in a SHA-256 digest of the whole, so it stays deterministic and distinct.
    """
    key = ":".join(_encode(part) for part in parts)
    if len(key) <= MAX_KEY:
        return key
    digest = hashlib.sha256(key.encode("ascii")).hexdigest()[:_DIGEST_CHARS]
    return f"{key[: MAX_KEY - _DIGEST_CHARS - 1]}-{digest}"


# Ruling 5 of bridge Part 3: one key grammar for a dependency bump, shared by both lanes that alert
# on one (the bump proposer and the work watcher). It lives here, beside `alert_key`, because both
# import this module and neither may import the other.
DEPENDENCY_BUMP: Final = "dependency-bump"


def dependency_bump_key(repository: str, ecosystem: str, dependency: str) -> str:
    """`dependency-bump:<repository lowercased>:<ecosystem>:<dependency>`, through `alert_key`.

    `ecosystem` is the tooling name (`npm`, `pip`, `uv`) for an ecosystem the factory has a profile
    for, and Dependabot's own branch segment (`docker`, `github_actions`) for one it has not.
    """
    return alert_key(DEPENDENCY_BUMP, repository.lower(), ecosystem, dependency)


def dependency_bump_prefix(repository: str) -> str:
    """The start every `dependency_bump_key` for this repository shares."""
    return alert_key(DEPENDENCY_BUMP, repository.lower()) + ":"


class KeyedAlerts:
    def __init__(
        self,
        token: Callable[[], str],
        state_path: Path,
        *,
        user_agent: str,
        refire: bool = False,
        severity: str = "warning",
        resolved_title: str = "Resolved",
        refuse: Callable[[str, str], Exception] | None = None,
        logger: logging.Logger = LOG,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._state = state_path
        self._user_agent = user_agent
        self._refire = refire
        self._severity = severity
        self._resolved_title = resolved_title
        self._refuse = refuse or (lambda method, path: AlertHubError(f"may not {method} {path}"))
        self._transport = transport
        self._sleep = sleep
        self._log = logger

    def sync(self, firing: Mapping[str, Alert], unread: Set[str] = frozenset()) -> None:
        """Bring alert-hub in line with `firing`. Raises `AlertHubError` for any send or state
        failure, after doing everything else it could."""
        for key in firing:
            if not KEY_GRAMMAR.fullmatch(key):
                raise ValueError(f"not an alert-hub key: {key[:40]!r}")
        remembered = self._remembered()
        if not self._refire:
            self._save(remembered)
        keep = set(remembered & unread)
        failures = 0
        client = self.open_client()
        try:
            for key, alert in firing.items():
                if key in remembered and not self._refire:
                    keep.add(key)
                    continue
                sent = self._send(client, key, "firing", alert)
                failures += not sent
                if sent or key in remembered:
                    keep.add(key)
            for key in remembered - firing.keys() - unread:
                sent = self._send(client, key, "resolved", Alert(self._resolved_title))
                failures += not sent
                if not sent:
                    keep.add(key)
        finally:
            client.close()
        self._save(keep)
        if failures:
            raise AlertHubError(f"{failures} alert(s) not sent")

    def remembered(self) -> set[str]:
        """The keys the state file holds as firing, which is what a caller needs to name `unread`
        keys it can't otherwise list (a source it couldn't read this pass)."""
        return self._remembered()

    def open_client(self) -> ConfinedClient:
        try:
            token = self._token()
        except Exception as error:  # a token store's failure; only its type is reported
            raise TokenUnreadable(f"sender token unreadable: {type(error).__name__}") from None
        try:
            return ConfinedClient(
                base_url=BASE_URL,
                user_agent=self._user_agent,
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT_SECONDS,
                permits=lambda method, path: (method, path) == ("POST", ALERTS),
                refuse=self._refuse,
                transport=self._transport,
            )
        except (TransportFailure, ValueError) as error:
            raise AlertHubError(f"alert-hub client unusable: {type(error).__name__}") from None

    def _send(self, client: ConfinedClient, key: str, status: str, alert: Alert) -> bool:
        payload = {
            "key": key,
            "severity": self._severity,
            "kind": "alert",
            "status": status,
            "title": alert.title,
            **({"message": alert.message} if alert.message else {}),
            **({"link": alert.link} if alert.link else {}),
        }
        try:
            response = client.request("POST", ALERTS, json=payload)
            if response.status_code == RATE_LIMITED and not self._refire:
                self._sleep(_retry_wait(response))
                response = client.request("POST", ALERTS, json=payload)
        except TransportFailure as failure:
            self._log.warning("notify_failed", extra={"key": key, "reason": failure.error_type})
            return False
        if response.status_code != ACCEPTED:
            self._log.warning("notify_failed", extra={"key": key, "status": response.status_code})
            return False
        self._log.info("notified", extra={"key": key, "status": status})
        return True

    def _remembered(self) -> set[str]:
        try:
            keys = json.loads(self._state.read_text())
        except OSError, ValueError:
            return set()
        return {key for key in keys if isinstance(key, str)} if isinstance(keys, list) else set()

    def _save(self, keys: set[str]) -> None:
        staged = self._state.with_name(self._state.name + ".tmp")
        try:
            staged.write_text(json.dumps(sorted(keys)))
            staged.replace(self._state)
        except OSError as error:
            raise StateUnwritable(error) from None


def _retry_wait(response: httpx.Response) -> float:
    header = response.headers.get("Retry-After", "")
    if header.isdecimal():
        return min(float(header), MAX_RETRY_WAIT)
    return DEFAULT_RETRY_WAIT
