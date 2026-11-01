"""What this lane tells a person, through alert-hub (sender `sds-bridge`).

`verify-refused:<unit>` for a unit the lane owns and the orchestrator refused,
`verify-checks-unreadable:<unit>` for a repository whose required checks could not be read, and
`verify-no-required-check:<unit>` for a bump whose repository requires none. A withdrawn grant is a
refusal, with the reason in its detail. A unit that goes to `awaiting_review` raises nothing here:
`execution-watch` holds that wait.

Each fires once, stays while its unit is refused, and resolves on the first pass that no longer
reports it. A pass that couldn't read its queue never reaches here, so every key holds; so does
the key of a unit it listed but couldn't read (`UNREAD`). This runs
after every verify, and answers a failure rather than raising one: telling a person never stops a
rotation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from alert_hub_client import Alert, AlertHubError, KeyedAlerts, alert_key

USER_AGENT: Final = "rotation-verifier/1 (+AlobarQuest/orchestrator)"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "VERIFIER_STATE_DIR"
ALERTS_FILE: Final = "alerts.json"
REFUSED: Final = "refused"
# A unit, or the member record it needs, the lane could not read: nothing is known, so the unit
# keeps its alert state and isn't verified.
UNREAD: Final = "unread"
NO_REQUIRED_CHECK: Final = "bump_unit_no_required_check"
CHECKS_UNREADABLE: Final = "required_checks_unreadable"
KINDS: Final = {
    REFUSED: "verify-refused",
    CHECKS_UNREADABLE: "verify-checks-unreadable",
    NO_REQUIRED_CHECK: "verify-no-required-check",
}
_NEXT_STEPS: Final = {
    REFUSED: (
        "The lane tries again every ten minutes, so a moved version clears by itself. Otherwise "
        "open the unit on /review and read ~/Library/Logs/rotation-verifier.log for the cause"
    ),
    CHECKS_UNREADABLE: (
        "Check that the operator's gh login still reads the repository's branch protection. The "
        "unit stays unverified until it does"
    ),
    NO_REQUIRED_CHECK: (
        "Add a required check to the repository's main branch protection, or cancel the unit. "
        "It is not verified until one exists"
    ),
}


@dataclass(frozen=True)
class Outcome:
    unit_key: str
    # completed, awaiting_review, would-verify, waiting, or one of `KINDS`
    result: str
    detail: str = ""
    unit_id: str = ""


def state_directory(environ: Mapping[str, str], home: Path) -> Path:
    configured = environ.get(STATE_DIR_ENV)
    if configured:
        return Path(configured)
    return home / "Library" / "Application Support" / "sds" / "verifier"


def _firing(outcomes: Iterable[Outcome]) -> dict[str, Alert]:
    return {
        alert_key(KINDS[outcome.result], outcome.unit_id): Alert(
            f"Verification needs a person: {outcome.unit_key}",
            f"{outcome.result}: {outcome.detail}. {_NEXT_STEPS[outcome.result]}.",
        )
        for outcome in outcomes
        if outcome.result in KINDS
    }


def sync_alerts(
    outcomes: Sequence[Outcome],
    *,
    token: str,
    state_dir: Path,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Bring alert-hub in line with this pass's outcomes. Answers why it could not, or None."""
    if not token:
        return f"{TOKEN_ENV} is empty, so no alert was sent or resolved"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"the state directory is unusable: {type(error).__name__}"
    hub = KeyedAlerts(
        lambda: token,
        state_dir / ALERTS_FILE,
        user_agent=USER_AGENT,
        resolved_title="Verification no longer needs a person",
        transport=transport,
    )
    try:
        unread = {
            alert_key(kind, outcome.unit_id)
            for outcome in outcomes
            if outcome.result == UNREAD
            for kind in KINDS.values()
        }
        hub.sync(_firing(outcomes), unread)
    except AlertHubError as error:
        return str(error)
    return None
