"""A pull request a lander has held for a day raises one alert-hub alert (Landing Part 1, task 1).

A pull request whose outcome is in `ALERTING_STATUSES` is remembered from the first pass that saw
it there. Once it has stayed there for `HELD_ALERT_HOURS`, one alert fires under `held_key`; it
is not re-fired while it stays (`KeyedAlerts` remembers it), and it is sent `resolved` on the first
pass in which the pull request is no longer in the set -- landed, waiting, deliberate, an
exception, settled, or no longer reported at all. A change of status INSIDE the set (held, then
unreadable) keeps the first sighting: the pull request has been stuck the whole time.

One pull request can be reported twice in a pass (the landing pass and the branch-update pass
both report every subject), so it is in the set when ANY of its outcomes is, and the first such
outcome supplies the alert's words.

Two files live in the lane's state directory (`LANDER_STATE_DIR`, else
`~/Library/Application Support/sds/<lane>`): `held-since.json`, the first sightings, and
`alerts.json`, `KeyedAlerts`' memory of what it fired. The launchers set neither variable, so the
two lanes' state stays apart. The first sightings are kept even when alert-hub refuses, so an
outage never restarts a pull request's clock.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import httpx

from alert_hub_client import Alert, AlertHubError, KeyedAlerts, alert_key
from lander.core import Outcome

# not-a-vocabulary: lander report statuses, read here and never exchanged with another system.
ALERTING_STATUSES: Final = frozenset({"held", "unreadable", "error"})
HELD_ALERT_HOURS: Final = 24
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "LANDER_STATE_DIR"
HELD_SINCE_FILE: Final = "held-since.json"
ALERTS_FILE: Final = "alerts.json"


def held_key(repository: str, number: int) -> str:
    return alert_key("landing-held", repository.lower(), str(number))


def state_dir(lane: str, environ: Mapping[str, str]) -> Path:
    configured = environ.get(STATE_DIR_ENV)
    if configured:
        return Path(configured)
    return Path.home() / "Library" / "Application Support" / "sds" / lane


def _parse(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def firing(
    outcomes: Iterable[Outcome], first_seen: Mapping[str, str], now: datetime
) -> tuple[dict[str, Alert], dict[str, str]]:
    """The alerts to fire now, and the first sightings to keep. Pure."""
    alerts: dict[str, Alert] = {}
    kept: dict[str, str] = {}
    for outcome in outcomes:
        if outcome.status not in ALERTING_STATUSES:
            continue
        key = held_key(outcome.repository, outcome.number)
        if key in kept:
            continue
        since = _parse(first_seen.get(key, ""))
        if since is None:
            since = now
        kept[key] = since.isoformat()
        if now - since >= timedelta(hours=HELD_ALERT_HOURS):
            hours = int((now - since).total_seconds() // 3600)
            alerts[key] = Alert(
                f"Pull request held: {outcome.repository}#{outcome.number}",
                f"{outcome.status} for {hours} hours: {outcome.detail}",
                f"https://github.com/{outcome.repository}/pull/{outcome.number}",
            )
    return alerts, kept


def _read_first_seen(path: Path) -> dict[str, str]:
    try:
        stored = json.loads(path.read_text())
    except OSError, ValueError:
        return {}
    if not isinstance(stored, dict):
        return {}
    return {k: v for k, v in stored.items() if isinstance(k, str) and isinstance(v, str)}


def _write_first_seen(path: Path, first_seen: Mapping[str, str]) -> None:
    staged = path.with_name(path.name + ".tmp")
    staged.write_text(json.dumps(dict(sorted(first_seen.items()))))
    staged.replace(path)


def sync_held_alerts(
    lane: str,
    outcomes: Sequence[Outcome],
    *,
    now: datetime,
    environ: Mapping[str, str] = os.environ,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Bring alert-hub in line with this pass's outcomes. Answers why it could not, or None."""
    token = environ.get(TOKEN_ENV, "")
    if not token:
        return f"{TOKEN_ENV} is unset"
    directory = state_dir(lane, environ)
    held_since = directory / HELD_SINCE_FILE
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"the state directory is unusable: {type(error).__name__}"
    alerts, kept = firing(outcomes, _read_first_seen(held_since), now)
    hub = KeyedAlerts(
        lambda: token,
        directory / ALERTS_FILE,
        user_agent=f"{lane}/1 (+AlobarQuest/orchestrator)",
        resolved_title="Pull request no longer held",
        transport=transport,
    )
    reason: str | None = None
    try:
        hub.sync(alerts)
    except AlertHubError as error:
        reason = str(error)
    try:
        _write_first_seen(held_since, kept)
    except OSError as error:
        return reason or f"the state directory is unusable: {type(error).__name__}"
    return reason
