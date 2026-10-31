"""What this lane tells a person, through alert-hub (sender `sds-bridge`).

`rotation-finding:<credential>:<status>` for each outcome in `FINDING_STATUSES`, which fires on the
pass that reports it. `rotation-approval:<credential>` for a revision a person has not decided
(`awaiting-approval`) or rejected (`declined`), which fires once the credential has stayed in
either for `APPROVAL_ALERT_HOURS`: waiting on a named human is the lane working, but not for days.

Each fires once, stays while its subject stays, and resolves on the first pass that no longer
reports it. A pass that could not use its inputs (exit 2) never reaches `sync_alerts`, so every
remembered key holds. Two files live in the state directory: `alerts.json`, `KeyedAlerts`' memory
of what it fired, and `approval-since.json`, when each approval wait was first seen. The sightings
are written before alert-hub is asked, and kept when it refuses, so an outage never restarts a
credential's clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import httpx

from alert_hub_client import (
    Alert,
    AlertHubError,
    FirstSightings,
    KeyedAlerts,
    StateUnwritable,
    alert_key,
)
from rotation_proposer.outcome import FINDING_STATUSES, Outcome

USER_AGENT: Final = "rotation-proposer/1 (+AlobarQuest/orchestrator)"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "ROTATION_PROPOSER_STATE_DIR"
ALERTS_FILE: Final = "alerts.json"
APPROVAL_SINCE_FILE: Final = "approval-since.json"
APPROVAL_ALERT_HOURS: Final = 48
APPROVAL_STATUSES: Final = frozenset({"awaiting-approval", "declined"})
_FINDING: Final = "rotation-finding"
_APPROVAL: Final = "rotation-approval"


def finding_key(credential_id: str, status: str) -> str:
    return alert_key(_FINDING, credential_id, status)


def approval_key(credential_id: str) -> str:
    return alert_key(_APPROVAL, credential_id)


def _findings(outcomes: Iterable[Outcome]) -> dict[str, Alert]:
    return {
        finding_key(outcome.credential_id, outcome.status): Alert(
            f"Rotation {outcome.status}: {outcome.credential_id}", outcome.detail
        )
        for outcome in outcomes
        if outcome.status in FINDING_STATUSES
    }


def _waiting(outcomes: Iterable[Outcome]) -> dict[str, Outcome]:
    return {
        approval_key(outcome.credential_id): outcome
        for outcome in outcomes
        if outcome.status in APPROVAL_STATUSES
    }


def sync_alerts(
    outcomes: Sequence[Outcome],
    *,
    token: str,
    state_dir: Path,
    now: datetime,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Bring alert-hub in line with this pass's outcomes. Answers why it could not, or None."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"the state directory is unusable: {type(error).__name__}"
    waiting = _waiting(outcomes)
    reasons: list[str] = []
    try:
        since = FirstSightings(state_dir / APPROVAL_SINCE_FILE).update(waiting.keys(), now)
    except StateUnwritable as error:
        reasons.append(str(error))
        since = {key: now for key in waiting}
    firing = _findings(outcomes)
    for key, outcome in waiting.items():
        waited = now - since[key]
        if waited >= timedelta(hours=APPROVAL_ALERT_HOURS):
            firing[key] = Alert(
                f"Rotation waiting on a person: {outcome.credential_id}",
                f"{outcome.status} for {int(waited.total_seconds() // 3600)} hours: "
                f"{outcome.detail}",
            )
    hub = KeyedAlerts(
        lambda: token,
        state_dir / ALERTS_FILE,
        user_agent=USER_AGENT,
        resolved_title="Rotation no longer needs a person",
        transport=transport,
    )
    try:
        hub.sync(firing)
    except AlertHubError as error:
        reasons.append(str(error))
    return "; ".join(reasons) or None
