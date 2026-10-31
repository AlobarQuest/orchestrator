"""What this lane tells a person, through alert-hub (sender `sds-bridge`).

`rotation-decomposition:<subject>` for a proposal that has waited on a person for
`APPROVAL_ALERT_HOURS` (an `awaiting-approval` outcome): a rotation's decomposition is normally
approved by policy as it is proposed, so one that waits is one a person must look at on /review.
Declined, refused and superseded outcomes already fail the outcome alarm and raise nothing here.

It fires once, stays while the proposal waits, and resolves on the first pass that no longer
reports it. A pass with an `unread` outcome could not see every record, so it syncs nothing and
every remembered key holds, clocks included. Two files live in the state directory: `alerts.json`,
`KeyedAlerts`' memory of what it fired, and `approval-since.json`, when each wait was first seen,
written before alert-hub is asked and kept when it refuses.
"""

from __future__ import annotations

from collections.abc import Sequence
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
from rotation_decomposer.outcome import Outcome

USER_AGENT: Final = "rotation-decomposer/1 (+AlobarQuest/orchestrator)"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "ROTATION_DECOMPOSER_STATE_DIR"
ALERTS_FILE: Final = "alerts.json"
APPROVAL_SINCE_FILE: Final = "approval-since.json"
APPROVAL_ALERT_HOURS: Final = 48
UNREAD: Final = "unread"
AWAITING: Final = "awaiting-approval"


def decomposition_key(subject: str) -> str:
    return alert_key("rotation-decomposition", subject)


def sync_alerts(
    outcomes: Sequence[Outcome],
    *,
    token: str,
    state_dir: Path,
    now: datetime,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Bring alert-hub in line with this pass's outcomes. Answers why it could not, or None."""
    if any(outcome.result == UNREAD for outcome in outcomes):
        return None
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"the state directory is unusable: {type(error).__name__}"
    waiting = {
        decomposition_key(outcome.subject): outcome
        for outcome in outcomes
        if outcome.result == AWAITING
    }
    reasons: list[str] = []
    try:
        since = FirstSightings(state_dir / APPROVAL_SINCE_FILE).update(waiting.keys(), now)
    except StateUnwritable as error:
        reasons.append(str(error))
        since = {key: now for key in waiting}
    firing: dict[str, Alert] = {}
    for key, outcome in waiting.items():
        waited = now - since[key]
        if waited >= timedelta(hours=APPROVAL_ALERT_HOURS):
            firing[key] = Alert(
                f"Rotation decomposition waiting on a person: {outcome.subject}",
                f"{AWAITING} for {int(waited.total_seconds() // 3600)} hours: {outcome.detail}",
            )
    hub = KeyedAlerts(
        lambda: token,
        state_dir / ALERTS_FILE,
        user_agent=USER_AGENT,
        resolved_title="Rotation decomposition no longer waits on a person",
        transport=transport,
    )
    try:
        hub.sync(firing)
    except AlertHubError as error:
        reasons.append(str(error))
    return "; ".join(reasons) or None
