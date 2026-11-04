"""Tell a person what the sweep's exit 2 means: one keyed alert-hub alert per member per condition.

Before this, "somebody has to act" reached only the launcher's log; three checkouts sat behind on
2026-11-04 and nobody heard. The observation each pass files is still the finding's durable home;
this is the page.

* **Two subcommands, two state files.** `KeyedAlerts.sync` resolves every remembered key that is
  neither firing nor unread, and `sweep` and `bind` run one after the other in one launcher, so a
  shared file would let each resolve the other's keys. Each keeps its own.
* **`<member>` is the directory name `sds_scope` prints**, which is the last component of the
  checkout path the launcher builds from it. Firing and unread keys both come from that one
  spelling (never the remote URL a measured checkout also reports), so an unavailable checkout's
  remembered keys are found and left alone.
* **An answer the pass lacks is unread, not clean.** An unavailable checkout, or a bind member
  with any unit it could not answer, leaves its remembered keys neither fired nor resolved.
* **Only the acting pass tells** (any pass without `--dry-run`); the caller never builds a hub
  otherwise, so a measuring pass needs no alert token.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from activation_sweep.bind import CONDITION_OUTCOMES, FINDING_OUTCOMES
from activation_sweep.checkout import CONDITIONS
from alert_hub_client import Alert, KeyedAlerts, alert_key

USER_AGENT: Final = "activation-sweep/1 (+AlobarQuest/orchestrator)"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "ACTIVATION_SWEEP_STATE_DIR"
SWEEP_FILE: Final = "sweep-alerts.json"
BIND_FILE: Final = "bind-alerts.json"
ACTIVATION_INCOMPLETE: Final = "activation-incomplete"


def state_dir(home: Path, configured: str | None) -> Path:
    if configured:
        return Path(configured)
    return home / "Library" / "Application Support" / "sds" / "activation-sweep"


def member_of(summary: dict[str, Any]) -> str:
    return Path(summary["checkout"]).name


def checkout_key(condition: str, member: str) -> str:
    return alert_key(f"checkout-{condition}", member)


def classify_sweep(summaries: Iterable[dict[str, Any]]) -> tuple[dict[str, Alert], set[str]]:
    """The alerts that fire this pass, and the keys of checkouts the pass couldn't measure."""
    firing: dict[str, Alert] = {}
    unread: set[str] = set()
    for summary in summaries:
        member = member_of(summary)
        if summary["unavailable"]:
            unread.update(checkout_key(condition, member) for condition in CONDITIONS)
            continue
        for condition in summary.get("conditions", []):
            firing[checkout_key(condition, member)] = Alert(
                f"{member} checkout is {condition}", _sweep_message(summary, condition)
            )
    return firing, unread


def _sweep_message(summary: dict[str, Any], condition: str) -> str:
    branch = summary.get("branch")
    return (
        f"{condition}: on {branch}, default branch {summary.get('default_branch')}, "
        f"behind by {summary.get('behind_by')}, "
        f"{summary.get('tracked_modifications')} modified tracked file(s)"
    )


def classify_bind(summaries: Iterable[dict[str, Any]]) -> tuple[dict[str, Alert], set[str]]:
    """Per member: fires when any unit is bound and not fully activated; unread when any unit
    (or the whole checkout) went unanswered."""
    firing: dict[str, Alert] = {}
    unread: set[str] = set()
    for summary in summaries:
        member = member_of(summary)
        key = alert_key(ACTIVATION_INCOMPLETE, member)
        units = summary.get("units", [])
        unsatisfied = [unit for unit in units if _activation(unit) in CONDITION_OUTCOMES]
        if unsatisfied:
            firing[key] = Alert(
                f"{member} holds a bound artifact that is not fully activated",
                _bind_message(unsatisfied),
            )
        if summary["unavailable"] or any(_unanswered(unit) for unit in units):
            unread.add(key)
    return firing, unread


def _activation(unit: dict[str, Any]) -> str | None:
    activation = unit.get("activation")
    return activation.get("outcome") if isinstance(activation, dict) else None


def _unanswered(unit: dict[str, Any]) -> bool:
    return unit.get("outcome") in FINDING_OUTCOMES or _activation(unit) in FINDING_OUTCOMES


def _bind_message(units: list[dict[str, Any]]) -> str:
    details = [
        f"{unit.get('unit_key')}: {', '.join(unit['activation'].get('unsatisfied', []))}"
        for unit in units
    ]
    return "Unsatisfied: " + "; ".join(details)


def open_hub(token: str, directory: Path, filename: str) -> KeyedAlerts:
    return KeyedAlerts(
        lambda: token,
        directory / filename,
        user_agent=USER_AGENT,
        resolved_title="Activation sweep clear",
    )
