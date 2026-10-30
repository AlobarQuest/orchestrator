"""What this lane tells a person, through alert-hub (sender `sds-bridge`).

`bump-unit-failed:<repo>:<unit>` for a unit that ended `failed` or `revision_required`, and
`bump-decompose-refused:<repo>:<revision>` for a revision the generator refused or policy didn't
approve. Each fires once, stays while its subject stays, and resolves on the first pass that no
longer raises it. A repository whose records this pass couldn't read says nothing about its
subjects, so its remembered keys are held, never resolved, and an `unread` with no repository holds
every key.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from alert_hub_client import Alert, AlertHubError, KeyedAlerts, alert_key

USER_AGENT: Final = "bump-driver/1 (+AlobarQuest/orchestrator)"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
ALERTS_FILE: Final = "alerts.json"
UNREAD: Final = "unread"
_UNIT_FAILED: Final = "bump-unit-failed"
_DECOMPOSE_REFUSED: Final = "bump-decompose-refused"


@dataclass(frozen=True)
class Outcome:
    subject: str
    # approved, would-propose, awaiting-approval, superseded, declined, refused, dispatched,
    # would-dispatch, running, busy, waiting, merged, would-merge, failed or unread
    result: str
    detail: str = ""
    repository: str | None = None
    # The alert this outcome raises, when it raises one.
    key: str | None = None


def bump_failed_key(repository: str, unit_id: str) -> str:
    return alert_key(_UNIT_FAILED, repository.lower(), unit_id)


def decompose_refused_key(repository: str, revision_id: str) -> str:
    return alert_key(_DECOMPOSE_REFUSED, repository.lower(), revision_id)


def _firing(outcomes: Iterable[Outcome]) -> dict[str, Alert]:
    return {
        outcome.key: Alert(
            f"Dependency bump needs a person: {outcome.subject}",
            f"{outcome.result}: {outcome.detail}",
            f"https://github.com/{outcome.repository}" if outcome.repository else None,
        )
        for outcome in outcomes
        if outcome.key is not None
    }


def _held(outcomes: Iterable[Outcome], remembered: set[str]) -> set[str]:
    unread = [outcome for outcome in outcomes if outcome.result == UNREAD]
    if any(outcome.repository is None for outcome in unread):
        return remembered
    prefixes = tuple(
        alert_key(prefix, str(outcome.repository).lower()) + ":"
        for outcome in unread
        for prefix in (_UNIT_FAILED, _DECOMPOSE_REFUSED)
    )
    return {key for key in remembered if key.startswith(prefixes)} if prefixes else set()


def sync_alerts(
    outcomes: Sequence[Outcome],
    *,
    token: str,
    state_dir: Path,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Bring alert-hub in line with this pass's outcomes. Answers why it could not, or None."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"the state directory is unusable: {type(error).__name__}"
    hub = KeyedAlerts(
        lambda: token,
        state_dir / ALERTS_FILE,
        user_agent=USER_AGENT,
        resolved_title="Dependency bump no longer needs a person",
        transport=transport,
    )
    try:
        hub.sync(_firing(outcomes), _held(outcomes, hub.remembered()))
    except AlertHubError as error:
        return str(error)
    return None
