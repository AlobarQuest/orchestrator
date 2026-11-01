"""One execution-watch pass: every stuck unit and overdue admission wait becomes a keyed alert.

Three invocations:

* bare: reads the orchestrator and prints each raised key, marking the ones the lane already knew.
  It needs no alert token, writes nothing and sends nothing.
* `--seed`: writes `known.json` from one read and sends nothing. It refuses when the file exists,
  so there is no re-baseline command: delete the file by hand to start again.
* `--submit`: the scheduled pass. It refuses, and raises `execution-watch-unread`, when
  `known.json` is missing, so a lost file can never silently re-baseline.

A pass that cannot read the orchestrator holds every remembered key and every first sighting, and
raises `execution-watch-unread` once that has lasted `Thresholds.unread_hours`. Sightings and the
known set are saved before alert-hub is asked, as in `lander.held_alerts`.

Exit codes: 0 the pass ran; 1 the lane could not do its job (configuration, state files, alert-hub,
a refused seed, a missing `known.json`); 2 the orchestrator could not be read and every key was
held. The launcher declares 2 as this lane's finding code, so it pings the check as success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence, Set
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import httpx

from alert_hub_client import Alert, AlertHubError, FirstSightings, KeyedAlerts, StateUnwritable
from execution_watch.client import USER_AGENT, CallError, Orchestrator
from execution_watch.decide import (
    DEFAULT_THRESHOLDS,
    Condition,
    StatusRow,
    Thresholds,
    firing,
    raised_keys,
)

ORCHESTRATOR_URL: Final = "https://sds.alobar.net"
TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"
STATE_DIR_ENV: Final = "EXECUTION_WATCH_STATE_DIR"
ALERTS_FILE: Final = "alerts.json"
FIRST_SEEN_FILE: Final = "first-seen.json"
KNOWN_FILE: Final = "known.json"
UNREAD_KEY: Final = Condition.WATCH_UNREAD.value
UNREAD_EXIT: Final = 2
# While the admission rows reach production: a pass baselines every present admission key and
# sends none, so history can't fire before anything could seed it. The second pull request of
# Admission Part 1 deletes this and its branch in `_readable`.
ABSORB_ADMISSION: Final = True
_ADMISSION_CONDITIONS: Final = (Condition.ADMISSION_WAITING, Condition.ADMISSION_POLICY_NOT_APPLIED)


def state_directory(environ: Mapping[str, str], home: Path) -> Path:
    configured = environ.get(STATE_DIR_ENV)
    if configured:
        return Path(configured)
    return home / "Library" / "Application Support" / "sds" / "execution-watch"


def _read_keys(path: Path) -> set[str] | None:
    """The keys a state file holds, or None when it is missing or unreadable."""
    try:
        stored = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if isinstance(stored, list):
        return {key for key in stored if isinstance(key, str)}
    if isinstance(stored, dict):
        return {key for key in stored if isinstance(key, str)}
    return None


def _write_known(path: Path, keys: Set[str]) -> None:
    staged = path.with_name(path.name + ".tmp")
    staged.write_text(json.dumps(sorted(keys)))
    staged.replace(path)


def _unread_alert(detail: str) -> Alert:
    return Alert(
        "Execution watch cannot see work units",
        f"{detail} Every execution alert is held as it was until the lane can read again. "
        "Check https://sds.alobar.net/openapi.json for execution-status, then the lane's log "
        "~/Library/Logs/execution-watch.log.",
        "https://sds.alobar.net/openapi.json",
    )


def seed(rows: Sequence[StatusRow], state: Path, now: datetime) -> int:
    """Write the known subjects from one read. Sends nothing, and never overwrites."""
    path = state / KNOWN_FILE
    if path.exists():
        print(
            f"FATAL: {path} already exists; --seed never overwrites it. Delete it by hand to "
            "re-baseline.",
            file=sys.stderr,
        )
        return 1
    keys = raised_keys(rows, now, DEFAULT_THRESHOLDS)
    try:
        state.mkdir(parents=True, exist_ok=True)
        _write_known(path, keys)
    except OSError as error:
        print(f"FATAL: the state directory is unusable: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"seeded {len(keys)} known subject(s) into {path}; nothing was sent")
    for key in sorted(keys):
        print(f"  known  {key}")
    return 0


def dry_run(rows: Sequence[StatusRow], state: Path, now: datetime) -> int:
    known = _read_keys(state / KNOWN_FILE)
    raised = raised_keys(rows, now, DEFAULT_THRESHOLDS)
    if known is None:
        print(f"no {KNOWN_FILE} yet: a --submit pass would refuse; run --seed first")
        known = set()
    for key in sorted(raised):
        print(f"  {'known' if key in known else 'new  '}  {key}")
    print(f"{len(raised)} raised key(s); nothing was written or sent")
    return 0


def _unreadable(
    sightings: FirstSightings,
    state: Path,
    now: datetime,
    thresholds: Thresholds,
    failure: str,
    reasons: list[str],
) -> dict[str, Alert]:
    """An unreadable pass keeps every clock and runs the unread one."""
    kept = _read_keys(state / FIRST_SEEN_FILE) or set()
    try:
        since = sightings.update({UNREAD_KEY}, now, kept)[UNREAD_KEY]
    except StateUnwritable as error:
        reasons.append(str(error))
        return {}
    if now - since >= timedelta(hours=thresholds.unread_hours):
        return {UNREAD_KEY: _unread_alert(f"{failure}.")}
    return {}


def _readable(
    rows: Sequence[StatusRow],
    known: set[str],
    sightings: FirstSightings,
    state: Path,
    now: datetime,
    thresholds: Thresholds,
    reasons: list[str],
) -> dict[str, Alert]:
    """Save the known set and the sightings, then decide what fires."""
    raised = raised_keys(rows, now, thresholds)
    if ABSORB_ADMISSION:
        known = known | {key for key in raised if key.startswith(_ADMISSION_CONDITIONS)}
    try:
        _write_known(state / KNOWN_FILE, known & raised)
    except OSError as error:
        reasons.append(f"the state directory is unusable: {type(error).__name__}")
    since: dict[str, datetime] = {}
    try:
        since = sightings.update(raised, now)
    except StateUnwritable as error:
        reasons.append(str(error))
    to_fire = firing(rows, since, known, now, thresholds)
    print(f"{len(raised)} raised, {len(to_fire)} firing")
    return to_fire


def submit(
    orchestrator: Orchestrator,
    state: Path,
    *,
    token: str,
    now: datetime,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    hub_transport: httpx.BaseTransport | None = None,
) -> int:
    try:
        state.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"FATAL: the state directory is unusable: {type(error).__name__}", file=sys.stderr)
        return 1
    hub = KeyedAlerts(
        lambda: token,
        state / ALERTS_FILE,
        user_agent=USER_AGENT,
        resolved_title="Execution no longer needs a person",
        transport=hub_transport,
    )
    sightings = FirstSightings(state / FIRST_SEEN_FILE)
    known = _read_keys(state / KNOWN_FILE)
    reasons: list[str] = []
    code = 0
    try:
        rows: list[StatusRow] | None = orchestrator.execution_status()
        read_failure = ""
    except CallError as error:
        rows, read_failure = None, str(error)
    to_fire: dict[str, Alert]
    held: set[str]
    if known is None:
        to_fire = {UNREAD_KEY: _unread_alert(f"{KNOWN_FILE} is missing, so nothing is baselined.")}
        held = set(hub.remembered())
        reasons.append(f"{KNOWN_FILE} is missing; run --seed after reading what is raised")
    elif rows is None:
        to_fire = _unreadable(sightings, state, now, thresholds, read_failure, reasons)
        held = set(hub.remembered())
        code = UNREAD_EXIT
        print(f"unread: {read_failure}; every key held")
    else:
        to_fire = _readable(rows, known, sightings, state, now, thresholds, reasons)
        held = set()
    try:
        hub.sync(to_fire, held)
    except AlertHubError as error:
        reasons.append(str(error))
    for reason in reasons:
        print(f"FATAL: {reason}", file=sys.stderr)
    return 1 if reasons else code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="execution-watch",
        description="Alert on work stalled, held, failed, never started or waiting on a person.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--submit", action="store_true", help="send alerts (the scheduled pass)")
    mode.add_argument("--seed", action="store_true", help="write known.json; send nothing")
    args = parser.parse_args(argv)
    environ = os.environ
    if not environ.get("ORCHESTRATOR_API_TOKEN") or not environ.get(
        "ORCHESTRATOR_API_CREDENTIAL_KEY_ID"
    ):
        print("FATAL: ORCHESTRATOR_API_TOKEN and its key id are required", file=sys.stderr)
        return 1
    token = environ.get(TOKEN_ENV, "")
    if args.submit and not token:
        print(f"FATAL: {TOKEN_ENV} is required", file=sys.stderr)
        return 1
    state = state_directory(environ, Path(environ.get("HOME", str(Path.home()))))
    try:
        orchestrator = Orchestrator(
            base_url=environ.get("ORCHESTRATOR_API_URL", ORCHESTRATOR_URL),
            credential_key_id=environ["ORCHESTRATOR_API_CREDENTIAL_KEY_ID"],
            token=environ["ORCHESTRATOR_API_TOKEN"],
        )
    except CallError as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1
    now = datetime.now(UTC)
    try:
        if args.submit:
            return submit(orchestrator, state, token=token, now=now)
        try:
            rows = orchestrator.execution_status()
        except CallError as error:
            print(f"FATAL: {error}", file=sys.stderr)
            return 1
        return seed(rows, state, now) if args.seed else dry_run(rows, state, now)
    finally:
        orchestrator.close()


if __name__ == "__main__":
    sys.exit(main())
