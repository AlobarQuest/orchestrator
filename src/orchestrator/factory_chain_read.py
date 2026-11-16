"""The factory-events database chain, walked and read in one snapshot (ADR-0055 am. 24 item 8).

`package_sources` runs this module as a subprocess (`python -m orchestrator.factory_chain_read`),
with security-standards' `src` on `PYTHONPATH`, when `FACTORY_EVENTS_READ_PASSWORD` is set. It
connects as the read role, from that password and the three settings
(`FACTORY_EVENTS_DB_HOST`, `_PORT`, `_NAME`), through `factory_events.chain.connection_params`,
which reads them and refuses a missing or retired one by name (ADR-0055 amendment 26). In one
read-only transaction at repeatable read it walks every row from `seq` 1 through FE-1's walker
(`factory_events.store.walk_records`, the one both chains verify with) and collects the
`package.approved` events as they pass, so the verify and the events the caller matches against
see one snapshot. The `SELECT` is the one FE-1's README gives readers in other repositories.

The same walk collects the `portfolio.scanned` events (ADR-0059 amendment 11, decision item 3), and
a verified record carries one `portfolio_scan` object: the newest usable scan, or the reason there
is none. `work_carrier` reads it on the lane's container pass; nothing here judges a repository.
`portfolio_scan` explains the selection.

The answer is one JSON record on stdout, which the caller captures: `outcome` `verified` with the
`package_approved` events (exit `VERIFIED`), or `broken` or `failure` (exit `UNAVAILABLE`). It is
this module's structured log record too, and it carries the `seq`, a prefix of the head hash and
an error's type, never the password or a driver's text, which can repeat connection details
(amendment 24 item 13). A refusal from `factory_events.chain` is reported the same way, as
`ChainError`: the caller never relays the child's stderr, and the record carries types only.

The module imports psycopg and `factory_events` lazily: `factory_events` lives in another
repository, and the caller's interpreter loads neither itself.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Final, Protocol

VERIFIED = 0
VERIFIED_OUTCOME = "verified"
UNAVAILABLE = 2
CONNECT_TIMEOUT_SECONDS = 10
HEAD_PREFIX = 12

WALK = "SELECT seq, prev_hash, hash, event_id, event FROM factory_events ORDER BY seq"

SCAN_ACTION = "portfolio.scanned"
SCAN_SOURCE = "portfolio-scan"
# The four check ids the scan's feed may carry, as `work_carrier.workability` maps them.
SCAN_CHECKS = frozenset(  # not-a-vocabulary: the feed contract's key set, checked here only
    {"runner.caller", "factory.secrets", "factory.landing_known", "factory.pat_access"}
)
# The scan's time is the Mac's clock and this pass's is the VPS's, both NTP-synced, so a real
# event is never more than seconds ahead. Five minutes absorbs that; an event dated further ahead
# is a wrong clock or a forgery, and since the chain is append-only it would never age past the
# carrier's 48 hours. It is skipped as a malformed event is. A plain int, with no off value.
SCAN_FUTURE_SKEW_SECONDS: Final = 300
_SCAN_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_REPOSITORY = re.compile(r"[^/\s]+/[^/\s]+")


def _emit(outcome: str, **fields: object) -> None:
    record = {
        "logger": "orchestrator.factory_chain_read",
        "op": "read-package-approvals",
        "outcome": outcome,
        **{key: value for key, value in fields.items() if value is not None},
    }
    print(json.dumps(record, sort_keys=True), flush=True)


def _records(
    rows: Iterable[tuple[Any, ...]],
    approvals: list[dict[str, Any]],
    scans: list[tuple[int, dict[str, Any]]],
) -> Iterator[dict]:
    """Map rows to the walker's records, noting each `package.approved` and `portfolio.scanned`
    event as it passes (a scan with its `seq`).

    The events count only when the walk reports no error, so noting one before the walker checks
    its row is safe: a broken row anywhere makes the whole read unavailable.
    """
    for seq, prev_hash, hash_hex, event_id, event_text in rows:
        try:
            event = json.loads(event_text)
        except TypeError, json.JSONDecodeError:
            event = None  # the walker reports it as a tampered row
        if isinstance(event, dict) and event.get("action") == "package.approved":
            approvals.append(event)
        if isinstance(event, dict) and event.get("action") == SCAN_ACTION:
            scans.append((seq, event))
        yield {
            "seq": seq,
            "prev_hash": prev_hash,
            "hash": hash_hex,
            "event_id": event_id,
            "event": event,
        }


class _ConnectParams(Protocol):
    """`factory_events.db.ConnectParams`, which this module can't import at its top level."""

    def as_kwargs(self) -> dict[str, str]: ...


def _scan_time(value: object) -> datetime | None:
    """The scan's time, whole seconds in UTC as the feed writes it, or `None`."""
    if not isinstance(value, str) or not _SCAN_TIME.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None  # not a calendar time: the candidate is skipped with this reason


def _candidacy(event: dict[str, Any], now: datetime) -> tuple[datetime | None, str]:
    """The event's scan time if it may be the newest scan, else why it is skipped."""
    if event.get("actor") != SCAN_SOURCE:
        return None, f"its actor is not {SCAN_SOURCE}"
    source = event.get("source")
    if not isinstance(source, dict) or source.get("system") != SCAN_SOURCE:
        return None, f"its source.system is not {SCAN_SOURCE}"
    scanned = _scan_time(event.get("timestamp"))
    if scanned is None:
        return None, "its timestamp is not a whole-second UTC time"
    ahead = (scanned - now).total_seconds()
    if ahead > SCAN_FUTURE_SKEW_SECONDS:
        return None, f"its timestamp is {ahead:.0f}s after this pass, past the tolerance"
    return scanned, ""


def _rows(evidence: object) -> tuple[list[dict[str, Any]] | None, str]:
    """A `success` event's rows, or why its evidence is outside the contract (no partial read)."""
    if not isinstance(evidence, list):
        return None, "its evidence is not a list"
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict) or item.get("type") != "repository":
            return None, "an evidence item is not a repository row"
        repository, checks = item.get("repository"), item.get("checks")
        if (
            not isinstance(repository, str)
            or not _REPOSITORY.fullmatch(repository)
            or repository != repository.lower()
        ):
            return None, "a row's repository is not a lowercased owner/repo"
        if repository in seen:
            return None, "a repository has more than one row"
        if (
            not isinstance(checks, dict)
            or not set(checks) <= SCAN_CHECKS
            or not all(isinstance(status, str) for status in checks.values())
        ):
            return None, "a row's checks are not the four ids with string statuses"
        seen.add(repository)
        rows.append({"repository": repository, "checks": checks})
    return rows, ""


def portfolio_scan(scans: list[tuple[int, dict[str, Any]]], now: datetime) -> dict[str, Any]:
    """The newest usable `portfolio.scanned` event, or the reason there is none.

    TWO TIERS. Candidacy -- the actor and `source.system` are `portfolio-scan`, the `timestamp` is
    a whole-second UTC time and not past the skew tolerance -- skips an event with a reason, and an
    older candidate may then win. The newest candidate BY `timestamp` (the scan's time, never the
    `seq`, so a late append of an older scan never wins) then decides alone: its `result` and
    evidence give rows (`success`), a hold for every repository (`unknown`), or, outside the
    contract, no usable event. That one never falls back to an older scan, which would let an
    out-of-contract or `unknown` newest scan be overridden by an older pass. Two candidates with
    the same newest time are ambiguous and give no usable event.

    The answer always has `scan` (an object or `None`), `reason` (when `scan` is `None`),
    `skipped` (the count of non-candidates) and `skipped_reason` (the newest-by-`seq` one's).
    """
    candidates: list[tuple[datetime, int, dict[str, Any]]] = []
    skipped: list[str] = []
    for seq, event in scans:
        scanned, why = _candidacy(event, now)
        if scanned is None:
            skipped.append(f"seq {seq}: {why}")
        else:
            candidates.append((scanned, seq, event))
    answer: dict[str, Any] = {
        "scan": None,
        "skipped": len(skipped),
        "skipped_reason": skipped[-1] if skipped else None,
    }
    if not candidates:
        return {**answer, "reason": f"no {SCAN_ACTION} event in the chain"}
    newest_time = max(scanned for scanned, _seq, _event in candidates)
    newest = [(seq, event) for scanned, seq, event in candidates if scanned == newest_time]
    if len(newest) > 1:
        seqs = ", ".join(str(seq) for seq, _event in newest)
        return {**answer, "reason": f"seqs {seqs} share the newest scan time, so neither is read"}
    seq, event = newest[0]
    scanned_at = event["timestamp"]
    base = {"scanned_at": scanned_at, "scanned_epoch": int(newest_time.timestamp()), "seq": seq}
    result = event.get("result")
    if result == "unknown":
        return {**answer, "scan": {**base, "result": "unknown", "rows": []}}
    if result != "success":
        why = f"its result is {result!r}"
    else:
        rows, why = _rows(event.get("evidence"))
        if rows is not None:
            return {**answer, "scan": {**base, "result": "success", "rows": rows}}
    return {
        **answer,
        "reason": f"the newest {SCAN_ACTION} event (seq {seq}, {scanned_at}) is outside the "
        f"contract: {why}",
    }


def _walk(
    params: _ConnectParams,
) -> tuple[list[str], dict | None, int, list[dict[str, Any]], list[tuple[int, dict[str, Any]]]]:
    """`params.as_kwargs()` holds the password, so it goes straight to `psycopg.connect` and is
    never logged."""
    psycopg = import_module("psycopg")
    store = import_module("factory_events.store")
    approvals: list[dict[str, Any]] = []
    scans: list[tuple[int, dict[str, Any]]] = []
    with psycopg.connect(
        **params.as_kwargs(),
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        application_name="orchestrator",
    ) as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with conn.transaction(), conn.cursor(name="orchestrator_approval_walk") as cur:
            cur.execute(WALK)
            errors, last, count = store.walk_records(_records(cur, approvals, scans))
    return errors, last, count, approvals, scans


def main() -> int:
    try:
        chain = import_module("factory_events.chain")
        # Strictly the read role: no append fallback. A missing setting or a set retired
        # variable raises ChainError here, before any connection. An AttributeError here means a
        # security-standards checkout older than amendment 26's increment 1 (`60ec80f7`).
        params = chain.connection_params(chain.READ_ROLE)
        errors, last, count, approvals, scans = _walk(params)
    except Exception as exc:
        # Not swallowed: reported by type as UNAVAILABLE, which the caller raises as a refusal.
        # The text stays out because a driver's can repeat connection details.
        _emit("failure", error_type=type(exc).__name__)
        return UNAVAILABLE
    seq = last["seq"] if last else 0
    head = last["hash"][:HEAD_PREFIX] if last else None
    if errors:
        # The walker's message names a seq and the kind of break, never row content.
        _emit("broken", seq=seq, head=head, events=count, error=errors[0])
        return UNAVAILABLE
    _emit(
        VERIFIED_OUTCOME,
        seq=seq,
        head=head,
        events=count,
        package_approved=approvals,
        portfolio_scan=portfolio_scan(scans, datetime.now(UTC)),
    )
    return VERIFIED


if __name__ == "__main__":
    sys.exit(main())
