"""The factory-events database chain, walked and read in one snapshot (ADR-0055 am. 24 item 8).

`package_sources` runs this module as a subprocess (`python -m orchestrator.factory_chain_read`),
with security-standards' `src` on `PYTHONPATH`, when `FACTORY_EVENTS_READ_DSN` is set. In one
read-only transaction at repeatable read it walks every row from `seq` 1 through FE-1's walker
(`factory_events.store.walk_records`, the one both chains verify with) and collects the
`package.approved` events as they pass, so the verify and the events the caller matches against
see one snapshot. The `SELECT` is the one FE-1's README gives readers in other repositories.

The answer is one JSON record on stdout, which the caller captures: `outcome` `verified` with the
`package_approved` events (exit `VERIFIED`), or `broken` or `failure` (exit `UNAVAILABLE`). It is
this module's structured log record too, and it carries the `seq`, a prefix of the head hash and
an error's type, never the DSN or a driver's text, which can repeat it (amendment 24 item 13).

The module imports psycopg and `factory_events` lazily: `factory_events` lives in another
repository, and the caller's interpreter loads neither for the file chain.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Iterator
from importlib import import_module
from typing import Any

VERIFIED = 0
VERIFIED_OUTCOME = "verified"
UNAVAILABLE = 2
CONNECT_TIMEOUT_SECONDS = 10
HEAD_PREFIX = 12

WALK = "SELECT seq, prev_hash, hash, event_id, event FROM factory_events ORDER BY seq"


def _emit(outcome: str, **fields: object) -> None:
    record = {
        "logger": "orchestrator.factory_chain_read",
        "op": "read-package-approvals",
        "outcome": outcome,
        **{key: value for key, value in fields.items() if value is not None},
    }
    print(json.dumps(record, sort_keys=True), flush=True)


def _records(rows: Iterable[tuple[Any, ...]], approvals: list[dict[str, Any]]) -> Iterator[dict]:
    """Map rows to the walker's records, noting each `package.approved` event as it passes.

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
        yield {
            "seq": seq,
            "prev_hash": prev_hash,
            "hash": hash_hex,
            "event_id": event_id,
            "event": event,
        }


def _walk(dsn: str) -> tuple[list[str], dict | None, int, list[dict[str, Any]]]:
    psycopg = import_module("psycopg")
    store = import_module("factory_events.store")
    approvals: list[dict[str, Any]] = []
    with psycopg.connect(
        dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS, application_name="orchestrator"
    ) as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with conn.transaction(), conn.cursor(name="orchestrator_approval_walk") as cur:
            cur.execute(WALK)
            errors, last, count = store.walk_records(_records(cur, approvals))
    return errors, last, count, approvals


def main() -> int:
    try:
        dsn = os.environ.get("FACTORY_EVENTS_READ_DSN")
        if not dsn:
            raise LookupError("FACTORY_EVENTS_READ_DSN is not set")
        errors, last, count, approvals = _walk(dsn)
    except Exception as exc:
        # Not swallowed: reported by type as UNAVAILABLE, which the caller raises as a refusal.
        # The text stays out because a driver's can repeat the DSN.
        _emit("failure", error_type=type(exc).__name__)
        return UNAVAILABLE
    seq = last["seq"] if last else 0
    head = last["hash"][:HEAD_PREFIX] if last else None
    if errors:
        # The walker's message names a seq and the kind of break, never row content.
        _emit("broken", seq=seq, head=head, events=count, error=errors[0])
        return UNAVAILABLE
    _emit(VERIFIED_OUTCOME, seq=seq, head=head, events=count, package_approved=approvals)
    return VERIFIED


if __name__ == "__main__":
    sys.exit(main())
