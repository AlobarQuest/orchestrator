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

The walk also collects the `mac_conformance.reported` events (ADR-0059 Amendment 13, decision item
4), and a verified record carries one `mac_conformance` object: the newest such event's ids and
statuses, or the reason there is none. The bump driver's lane reads it; nothing here judges its
age or whether it holds a bump. `mac_conformance` explains the selection.

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
from typing import Any, Final, Protocol, cast

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

CONFORMANCE_ACTION = "mac_conformance.reported"
CONFORMANCE_SOURCE = "mac-conformance-facts"
# The producer writes a report's own `generated_at`, with microseconds only when the report has a
# fraction, so this time, unlike the scan's, may carry one.
_REPORT_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z")
# A wiring executor becomes a file name under the pass's `HOME` (`<label>.plist`) or a hook
# command, and any append holder can write this event, so the name is held to a plain word: no
# separator, no leading dot.
_EXECUTOR = re.compile(r"(launchagent|hook):[A-Za-z0-9][A-Za-z0-9._-]*")
WIRING_STATUSES = frozenset({"present", "absent"})  # not-a-vocabulary: the producer's two words


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
    reports: list[tuple[int, dict[str, Any]]],
) -> Iterator[dict]:
    """Map rows to the walker's records, noting each `package.approved`, `portfolio.scanned` and
    `mac_conformance.reported` event as it passes (a scan or a report with its `seq`).

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
        if isinstance(event, dict) and event.get("action") == CONFORMANCE_ACTION:
            reports.append((seq, event))
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


_Noted = list[tuple[int, dict[str, Any]]]


def _report_time(value: object) -> datetime | None:
    """A UTC `Z` time as the producer writes it, or `None`."""
    if not isinstance(value, str) or not _REPORT_TIME.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None  # not a calendar time: the candidate is skipped with this reason


def _report_candidacy(event: dict[str, Any], now: datetime) -> tuple[datetime | None, str]:
    """The event's time if it may be the newest report, else why it is skipped."""
    if event.get("actor") != CONFORMANCE_SOURCE:
        return None, f"its actor is not {CONFORMANCE_SOURCE}"
    source = event.get("source")
    if not isinstance(source, dict) or source.get("system") != CONFORMANCE_SOURCE:
        return None, f"its source.system is not {CONFORMANCE_SOURCE}"
    reported = _report_time(event.get("timestamp"))
    if reported is None:
        return None, "its timestamp is not a UTC time"
    ahead = (reported - now).total_seconds()
    if ahead > SCAN_FUTURE_SKEW_SECONDS:
        return None, f"its timestamp is {ahead:.0f}s after this pass, past the tolerance"
    return reported, ""


def _optional_text(value: object) -> bool:
    return value is None or isinstance(value, str)


def _report_part(first: object) -> tuple[dict[str, Any] | None, str]:
    """The report item: a readable report's `generated_at`, or an unreadable one's reason."""
    if not isinstance(first, dict):
        return None, "its first evidence item is not a report item"
    if first.get("type") == "infra-drift-report":
        if _report_time(first.get("generated_at")) is None:
            return None, "its report's generated_at is not a UTC time"
        return {"generated_at": first["generated_at"], "unreadable": None}, ""
    if first.get("type") == "infra-drift-report-unreadable":
        if not isinstance(first.get("reason"), str):
            return None, "its unreadable report carries no reason"
        return {"generated_at": None, "unreadable": first["reason"]}, ""
    return None, "its first evidence item is not a report item"


def _instance(item: dict[str, Any], facts: dict[str, Any]) -> str:
    name, ok = item.get("name"), item.get("ok")
    if not isinstance(name, str) or not isinstance(ok, bool) or name in facts["instances"]:
        return "an instance item is not a unique name with a boolean ok"
    facts["instances"][name] = ok
    return ""


def _proposal(item: dict[str, Any], facts: dict[str, Any]) -> str:
    target = item.get("target")
    if (
        not isinstance(item.get("instance"), str)
        or not _optional_text(item.get("id"))
        or not isinstance(target, dict)
        or not _optional_text(target.get("uuid"))
        or not _optional_text(target.get("name"))
    ):
        return "a proposal item is not an instance, an id and a target's uuid and name"
    facts["proposals"].append(
        {
            "instance": item["instance"],
            "id": item.get("id"),
            "target": {"uuid": target.get("uuid"), "name": target.get("name")},
        }
    )
    return ""


def _wiring(item: dict[str, Any], facts: dict[str, Any]) -> str:
    executor, status = item.get("executor"), item.get("status")
    if (
        not isinstance(executor, str)
        or not _EXECUTOR.fullmatch(executor)
        or status not in WIRING_STATUSES
        or executor in facts["wiring"]
    ):
        return "a wiring item is not a unique launchagent: or hook: name with a known status"
    facts["wiring"][executor] = status
    return ""


_ITEM_READERS = {  # not-a-vocabulary: the producer's evidence item types, read here only
    "infra-drift-instance": _instance,
    "infra-drift-proposal": _proposal,
    "wiring": _wiring,
}


def _report_facts(event: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """A newest event's ids and statuses, or why its evidence is outside the contract (no partial
    read). Nothing but these ids and statuses is copied."""
    result = event.get("result")
    if result not in ("success", "unknown"):
        return None, f"its result is {result!r}"
    evidence = event.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return None, "its evidence is not a non-empty list"
    report, why = _report_part(evidence[0])
    if report is None:
        return None, why
    facts: dict[str, Any] = {
        **report,
        "result": result,
        "instances": {},
        "proposals": [],
        "wiring": {},
    }
    for item in evidence[1:]:
        kind = item.get("type") if isinstance(item, dict) else None
        reader = _ITEM_READERS.get(kind) if isinstance(kind, str) else None
        if reader is None:
            return None, "an evidence item is of no known type"
        if report["unreadable"] is not None and kind != "wiring":
            return None, "an unreadable report carries instance or proposal items"
        why = reader(cast(dict[str, Any], item), facts)
        if why:
            return None, why
    return facts, ""


def mac_conformance(reports: _Noted, now: datetime) -> dict[str, Any]:
    """The newest `mac_conformance.reported` event's ids and statuses, or the reason there is none.

    The same two tiers as `portfolio_scan`. Candidacy -- the actor and `source.system` are
    `mac-conformance-facts`, and the `timestamp` is a UTC time (fractional seconds allowed, as the
    producer writes a report's own `generated_at`) not past the skew tolerance -- skips an event
    with a reason, and an older candidate may then win. The newest candidate BY `timestamp`, never
    by `seq`, then decides alone: an unreadable-report record, whose time is the nightly's run, wins
    over the previous day's usable event, and an out-of-contract newest event gives no usable event
    rather than falling back to an older one. Two candidates with the same newest time are
    ambiguous and give none.

    The answer always has `event` (an object or `None`), `reason` (when `event` is `None`),
    `skipped` and `skipped_reason`. Whether the event is fresh enough, or holds a bump, is the
    reader's: it judges at its own pass time.
    """
    candidates: list[tuple[datetime, int, dict[str, Any]]] = []
    skipped: list[str] = []
    for seq, event in reports:
        reported, why = _report_candidacy(event, now)
        if reported is None:
            skipped.append(f"seq {seq}: {why}")
        else:
            candidates.append((reported, seq, event))
    answer: dict[str, Any] = {
        "event": None,
        "skipped": len(skipped),
        "skipped_reason": skipped[-1] if skipped else None,
    }
    if not candidates:
        return {**answer, "reason": f"no {CONFORMANCE_ACTION} event in the chain"}
    newest_time = max(reported for reported, _seq, _event in candidates)
    newest = [(seq, event) for reported, seq, event in candidates if reported == newest_time]
    if len(newest) > 1:
        seqs = ", ".join(str(seq) for seq, _event in newest)
        return {**answer, "reason": f"seqs {seqs} share the newest report time, so neither is read"}
    seq, event = newest[0]
    facts, why = _report_facts(event)
    if facts is None:
        return {
            **answer,
            "reason": f"the newest {CONFORMANCE_ACTION} event (seq {seq}, {event['timestamp']}) "
            f"is outside the contract: {why}",
        }
    return {**answer, "event": {"seq": seq, "timestamp": event["timestamp"], **facts}}


def _walk(
    params: _ConnectParams,
) -> tuple[list[str], dict | None, int, list[dict[str, Any]], _Noted, _Noted]:
    """`params.as_kwargs()` holds the password, so it goes straight to `psycopg.connect` and is
    never logged."""
    psycopg = import_module("psycopg")
    store = import_module("factory_events.store")
    approvals: list[dict[str, Any]] = []
    scans: _Noted = []
    reports: _Noted = []
    with psycopg.connect(
        **params.as_kwargs(),
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        application_name="orchestrator",
    ) as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with conn.transaction(), conn.cursor(name="orchestrator_approval_walk") as cur:
            cur.execute(WALK)
            errors, last, count = store.walk_records(_records(cur, approvals, scans, reports))
    return errors, last, count, approvals, scans, reports


def main() -> int:
    try:
        chain = import_module("factory_events.chain")
        # Strictly the read role: no append fallback. A missing setting or a set retired
        # variable raises ChainError here, before any connection. An AttributeError here means a
        # security-standards checkout older than amendment 26's increment 1 (`60ec80f7`).
        params = chain.connection_params(chain.READ_ROLE)
        errors, last, count, approvals, scans, reports = _walk(params)
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
    now = datetime.now(UTC)
    _emit(
        VERIFIED_OUTCOME,
        seq=seq,
        head=head,
        events=count,
        package_approved=approvals,
        portfolio_scan=portfolio_scan(scans, now),
        mac_conformance=mac_conformance(reports, now),
    )
    return VERIFIED


if __name__ == "__main__":
    sys.exit(main())
