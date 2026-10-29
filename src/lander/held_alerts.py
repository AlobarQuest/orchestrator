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

A repository the lane could not list is reported as one row numbered 0, and none of its pull
requests appear. That row says nothing about them, so their remembered alerts and first sightings
are HELD (passed to `KeyedAlerts` as `unread`, kept by `firing`), never resolved or forgotten.
The row itself runs the same 24-hour clock under the repository-level key (`held_key` with number
0) and fires once (`Repository could not be listed: <repo>`): that pass exits 3, which the
dead-man check pings as success, so nothing else reports it. Its key shares the repository's
prefix, so it too is held while the repository stays unlisted, and is resolved on the first pass
that lists the repository again. Which keys belong to a repository is read from `held_key`'s own
prefix; every key carries the lane, like `lander.core.landing_key`.

Two files live in the lane's state directory (`LANDER_STATE_DIR`, else
`~/Library/Application Support/sds/<lane>`): `held-since.json`, the first sightings, and
`alerts.json`, `KeyedAlerts`' memory of what it fired. The launchers set neither variable, so the
two lanes' state stays apart. The first sightings are written before alert-hub is asked, and kept
even when it refuses, so an outage never restarts a pull request's clock and a pass killed between
the two writes never leaves `alerts.json` ahead of the sightings.

## Reverts (Landing Part 3a, the inert lane)

`sync_revert_alerts` raises one alert per revert record in the orchestrator's landing watch:
`landing-reverted:<repo>:<pr>` once the revert has landed (naming the failed jobs, the revert pull
request, the hold it placed, how to lift one, and that the reverted version stays refused after
that), and `landing-revert-refused:<repo>:<pr>` or `landing-revert-failed:<repo>:<pr>` when no
revert could be opened. `landing-revert-stranded:<repo>:<pr>` fires for a revert pull request the
lander found no longer open while its record still says it is (closed without landing); it is held
while that repository could not be listed. Each fires once and follows its record in the watch:
`landing-reverted` stays open while the dependency is held (the watch keeps the record that long)
and resolves when the hold is lifted; the refused and failed records never leave the watch, so
those alerts stay open until a person closes them, as does a stranded one while its revert stays
closed. A pass that could not read the watch holds every remembered key. Its memory is its own
file, `revert-alerts.json`: `KeyedAlerts` resolves every remembered key a sync does not name, so
sharing `alerts.json` would resolve the other's alerts.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence, Set
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
REVERT_ALERTS_FILE: Final = "revert-alerts.json"

# The record statuses that raise an alert, and the key prefix each raises it under.
_REVERT_ALERTS: Final = {
    "revert_landed": "landing-reverted",
    "refused_overlap": "landing-revert-refused",
    "revert_failed": "landing-revert-failed",
}
_STRANDED: Final = "landing-revert-stranded"
LIFT_A_HOLD: Final = (
    "To lift a hold: stage a lift with `sds-members stage <file.toml>`, then confirm it on /review."
)
NEVER_AGAIN: Final = (
    "The reverted version is refused for good (landing_version_reverted), even after the hold is "
    "lifted; to take it anyway, land that version by hand, and it is not watched by the SDS."
)


def held_key(lane: str, repository: str, number: int) -> str:
    """Number 0 is the repository-level key: the row of a repository the lane could not list."""
    return alert_key("landing-held", lane, repository.lower(), str(number))


def _repository_prefix(lane: str, repository: str) -> str:
    """The start every `held_key` for this lane and repository shares, ending at the separator."""
    return alert_key("landing-held", lane, repository.lower()) + ":"


def unlisted_prefixes(lane: str, outcomes: Iterable[Outcome]) -> tuple[str, ...]:
    """The key prefixes of the repositories this pass could not list (a row numbered 0)."""
    return tuple(
        sorted({_repository_prefix(lane, o.repository) for o in outcomes if o.number == 0})
    )


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


def _alert(outcome: Outcome, hours: int) -> Alert:
    message = f"{outcome.status} for {hours} hours: {outcome.detail}"
    if outcome.number == 0:
        return Alert(
            f"Repository could not be listed: {outcome.repository}",
            message,
            f"https://github.com/{outcome.repository}",
        )
    return Alert(
        f"Pull request held: {outcome.repository}#{outcome.number}",
        message,
        f"https://github.com/{outcome.repository}/pull/{outcome.number}",
    )


def firing(
    lane: str, outcomes: Iterable[Outcome], first_seen: Mapping[str, str], now: datetime
) -> tuple[dict[str, Alert], dict[str, str]]:
    """The alerts to fire now, and the first sightings to keep. Pure."""
    outcomes = list(outcomes)
    alerts: dict[str, Alert] = {}
    kept: dict[str, str] = {}
    for outcome in outcomes:
        if outcome.status not in ALERTING_STATUSES:
            continue
        key = held_key(lane, outcome.repository, outcome.number)
        if key in kept:
            continue
        since = _parse(first_seen.get(key, ""))
        if since is None:
            since = now
        kept[key] = since.isoformat()
        if now - since >= timedelta(hours=HELD_ALERT_HOURS):
            alerts[key] = _alert(outcome, int((now - since).total_seconds() // 3600))
    unlisted = unlisted_prefixes(lane, outcomes)
    if unlisted:
        for key, since in first_seen.items():
            if key not in kept and key.startswith(unlisted):
                kept[key] = since
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
    alerts, kept = firing(lane, outcomes, _read_first_seen(held_since), now)
    unlisted = unlisted_prefixes(lane, outcomes)
    reasons: list[str] = []
    try:
        _write_first_seen(held_since, kept)
    except OSError as error:
        reasons.append(f"the state directory is unusable: {type(error).__name__}")
    hub = KeyedAlerts(
        lambda: token,
        directory / ALERTS_FILE,
        user_agent=f"{lane}/1 (+AlobarQuest/orchestrator)",
        resolved_title="Pull request no longer held",
        transport=transport,
    )
    try:
        unread = (
            {key for key in hub.remembered() if key.startswith(unlisted)} if unlisted else set()
        )
        hub.sync(alerts, unread)
    except AlertHubError as error:
        reasons.append(str(error))
    return "; ".join(reasons) or None


def revert_key(status: str, repository: str, number: int) -> str | None:
    """The alert key a revert record of this status raises, or None when it raises none."""
    prefix = _REVERT_ALERTS.get(status)
    return None if prefix is None else alert_key(prefix, repository.lower(), str(number))


def stranded_key(repository: str, number: int) -> str:
    """The alert key of a landing whose revert pull request closed without landing."""
    return alert_key(_STRANDED, repository.lower(), str(number))


def _stranded_alert(row: Mapping[str, object]) -> Alert:
    repository, number, revert = (
        row.get("repository"),
        row.get("pr_number"),
        row.get("revert_pr_number"),
    )
    return Alert(
        f"Revert not landed: {repository}#{number}",
        f"The revert #{revert} of {repository}#{number} is no longer open and did not land, so the "
        "default branch may still carry the bad bump. This repository's Dependabot bumps wait "
        "until the revert pull request is reopened and lands. Reverting by hand does not release "
        "them yet (no act clears the record).",
        f"https://github.com/{repository}/pull/{revert}",
    )


def _revert_alert(row: Mapping[str, object]) -> Alert:
    repository, number = str(row.get("repository")), row.get("pr_number")
    jobs = row.get("failed_job_names")
    failed = ", ".join(str(job) for job in jobs) if isinstance(jobs, list) and jobs else "a job"
    subject = f"{repository}#{number}"
    landing = f"https://github.com/{repository}/pull/{number}"
    if row.get("status") == "revert_landed":
        revert = row.get("revert_pr_number")
        hold = row.get("hold_outcome") or "nothing held"
        detail = row.get("detail")
        return Alert(
            f"Landing reverted: {subject}",
            f"{failed} failed on the default branch after {subject} landed; the revert "
            f"#{revert} has landed. Hold: {hold}."
            + (f" {detail}." if detail else "")
            + f" {LIFT_A_HOLD} {NEVER_AGAIN}",
            f"https://github.com/{repository}/pull/{revert}",
        )
    return Alert(
        f"Landing not reverted: {subject}",
        f"{failed} failed on the default branch after {subject} landed, and no revert was "
        f"opened: {row.get('detail') or row.get('status')}. A person reverts it by hand.",
        landing,
    )


def revert_alerts(watch: Iterable[Mapping[str, object]]) -> dict[str, Alert]:
    """The alerts the watch's revert records raise now, by key. Pure."""
    alerts: dict[str, Alert] = {}
    for row in watch:
        repository, number, status = row.get("repository"), row.get("pr_number"), row.get("status")
        if not isinstance(repository, str) or not isinstance(number, int):
            continue
        key = revert_key(status, repository, number) if isinstance(status, str) else None
        if key is not None:
            alerts[key] = _revert_alert(row)
    return alerts


def sync_revert_alerts(
    lane: str,
    watch: Sequence[Mapping[str, object]] | None,
    *,
    stranded: Set[tuple[str, int]] = frozenset(),
    unknown: Set[tuple[str, int]] = frozenset(),
    environ: Mapping[str, str] = os.environ,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Bring alert-hub in line with the watch's revert records; `None` for a watch that could not
    be read, which holds every remembered key. `stranded` names the landings whose revert pull
    request the lander found closed without landing, and `unknown` those whose repository it could
    not list (their stranded alert is held). Answers why it could not, or None."""
    token = environ.get(TOKEN_ENV, "")
    if not token:
        return f"{TOKEN_ENV} is unset"
    directory = state_dir(lane, environ)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"the state directory is unusable: {type(error).__name__}"
    hub = KeyedAlerts(
        lambda: token,
        directory / REVERT_ALERTS_FILE,
        user_agent=f"{lane}/1 (+AlobarQuest/orchestrator)",
        resolved_title="Landing no longer watched",
        transport=transport,
    )
    try:
        if watch is None:
            hub.sync({}, hub.remembered())
        else:
            alerts = revert_alerts(watch)
            for row in watch:
                repository, number = row.get("repository"), row.get("pr_number")
                if isinstance(repository, str) and isinstance(number, int):
                    if (repository, number) in stranded:
                        alerts[stranded_key(repository, number)] = _stranded_alert(row)
            hub.sync(alerts, {stranded_key(r, n) for r, n in unknown})
    except AlertHubError as error:
        return str(error)
    return None
