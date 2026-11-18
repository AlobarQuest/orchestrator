"""What this lane tells a person, through alert-hub (sender `sds-bridge`).

`bump-unit-failed:<repo>:<unit>` for a unit in `revision_required`,
`bump-decompose-refused:<repo>:<revision>` for a revision the generator refused or policy didn't
approve, `bump-act-refused:<repo>:<unit>` for a refused dispatch or merge, and
`bump-run-unclaimed:<repo>:<unit>` for a dispatched run no runner claimed. A `failed` unit raises
none here: `work_watcher` already holds `dependency-unit-failed` for it.

In the lane's container two more hold a bump before its decomposition (ADR-0059 Amendment 13):
`bump-conformance-facts`, one key for the whole lane, while the Mac's conformance facts in the
factory-events chain are missing, unusable or stale and a bump that reads them is held; and
`bump-conformance-wiring:<repo>` while a member declares a LaunchAgent or hook check the event
doesn't list.

Each fires once, stays while its subject stays, and resolves on the first pass that no longer
raises it. A repository whose records this pass couldn't read says nothing about its subjects, so
its remembered keys are held, never resolved, and an `unread` with no repository holds every key.
`bump-conformance-facts` is the exception: it resolves only on a pass whose facts are usable, so a
pass that happens to hold no bump while the facts are still unusable keeps it firing.
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
_ACT_REFUSED: Final = "bump-act-refused"
_RUN_UNCLAIMED: Final = "bump-run-unclaimed"
_KEY_PREFIXES: Final = (_UNIT_FAILED, _DECOMPOSE_REFUSED, _ACT_REFUSED, _RUN_UNCLAIMED)
# A repository's own key, with no subject after it.
_CONFORMANCE_WIRING: Final = "bump-conformance-wiring"
CONFORMANCE_FACTS_KEY: Final = alert_key("bump-conformance-facts")


@dataclass(frozen=True)
class Outcome:
    subject: str
    # approved, would-propose, awaiting-approval, superseded, declined, refused, dispatched,
    # would-dispatch, running, unclaimed, busy, waiting, merged, would-merge, failed or unread
    result: str
    detail: str = ""
    repository: str | None = None
    # The alert this outcome raises, when it raises one.
    key: str | None = None


def bump_failed_key(repository: str, unit_id: str) -> str:
    return alert_key(_UNIT_FAILED, repository.lower(), unit_id)


def decompose_refused_key(repository: str, revision_id: str) -> str:
    return alert_key(_DECOMPOSE_REFUSED, repository.lower(), revision_id)


def act_refused_key(repository: str, unit_id: str) -> str:
    return alert_key(_ACT_REFUSED, repository.lower(), unit_id)


def run_unclaimed_key(repository: str, unit_id: str) -> str:
    return alert_key(_RUN_UNCLAIMED, repository.lower(), unit_id)


def conformance_wiring_key(repository: str) -> str:
    return alert_key(_CONFORMANCE_WIRING, repository.lower())


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
    repositories = {str(outcome.repository).lower() for outcome in unread}
    prefixes = tuple(
        alert_key(prefix, repository) + ":"
        for repository in repositories
        for prefix in _KEY_PREFIXES
    )
    own = {alert_key(_CONFORMANCE_WIRING, repository) for repository in repositories}
    return {key for key in remembered if key in own or key.startswith(prefixes)}


def sync_alerts(
    outcomes: Sequence[Outcome],
    *,
    token: str,
    state_dir: Path,
    kept: frozenset[str] = frozenset(),
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Bring alert-hub in line with this pass's outcomes. Answers why it could not, or None.
    A remembered key in `kept` is held whatever this pass raised."""
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
        remembered = hub.remembered()
        hub.sync(_firing(outcomes), _held(outcomes, remembered) | (kept & remembered))
    except AlertHubError as error:
        return str(error)
    return None
