"""The chain read's `portfolio_scan` answer (ADR-0059 amendment 11, decision item 3).

`orchestrator.factory_chain_read` collects the `portfolio.scanned` events in the same snapshot walk
it makes for `package.approved`, and a verified record carries the newest usable one or the reason
there is none. `portfolio_scan` is pure, so most of these call it directly; the last few run the
child with the fake driver from `test_package_chain_database`, to show the answer rides only a
verified record.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import orchestrator.factory_chain_read as factory_chain_read
from orchestrator.factory_chain_read import SCAN_FUTURE_SKEW_SECONDS, portfolio_scan
from tests.cli.test_package_chain_database import _fake_driver, _main, _read_chain, _row

NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)


def _at(delta: timedelta) -> str:
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan(
    scanned_at: str = "2026-11-17T08:00:35Z",
    *,
    result: str = "success",
    evidence: list[Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """One `portfolio.scanned` event, shaped as security-standards #99's adapter appends it."""
    event = {
        "schema": "factory-event/v1",
        "event_id": f"evt-{scanned_at}",
        "timestamp": scanned_at,
        "actor": "portfolio-scan",
        "action": "portfolio.scanned",
        "target": None,
        "result": result,
        "evidence": (
            [row("example-owner/repo-a", **{"runner.caller": "pass"})]
            if evidence is None
            else evidence
        ),
        "source": {"system": "portfolio-scan", "ref": scanned_at},
    }
    return {**event, **overrides}


def row(repository: str, **checks: str) -> dict[str, Any]:
    return {"type": "repository", "repository": repository, "checks": checks}


def test_the_newest_by_timestamp_wins_when_an_older_scan_has_the_higher_seq() -> None:
    newer = scan("2026-11-17T08:00:35Z", evidence=[row("example-owner/newer")])
    older = scan("2026-11-16T08:00:35Z", evidence=[row("example-owner/older")])

    answer = portfolio_scan([(10, newer), (11, older)], NOW)

    assert answer["scan"]["seq"] == 10
    assert answer["scan"]["scanned_at"] == "2026-11-17T08:00:35Z"
    assert [r["repository"] for r in answer["scan"]["rows"]] == ["example-owner/newer"]


def test_the_scan_time_is_a_parsed_utc_epoch() -> None:
    answer = portfolio_scan([(1, scan("2026-11-17T08:00:35Z"))], NOW)

    expected = datetime(2026, 11, 17, 8, 0, 35, tzinfo=UTC).timestamp()
    assert answer["scan"]["scanned_epoch"] == int(expected)


def test_no_event_is_no_usable_scan_with_its_reason() -> None:
    answer = portfolio_scan([], NOW)

    assert answer == {
        "scan": None,
        "reason": "no portfolio.scanned event in the chain",
        "skipped": 0,
        "skipped_reason": None,
    }


def test_an_unknown_result_is_a_scan_with_no_rows() -> None:
    unreadable = [{"type": "member-record-unreadable", "reason": "401"}]
    answer = portfolio_scan([(1, scan(result="unknown", evidence=unreadable))], NOW)

    assert answer["scan"]["result"] == "unknown"
    assert answer["scan"]["rows"] == []


def test_a_newest_unknown_scan_is_never_overridden_by_an_older_pass() -> None:
    older = scan("2026-11-16T08:00:35Z")
    newer = scan("2026-11-17T08:00:35Z", result="unknown", evidence=[])

    answer = portfolio_scan([(1, older), (2, newer)], NOW)

    assert answer["scan"]["result"] == "unknown"


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"result": "failure"}, "its result is 'failure'"),
        ({"evidence": [{"type": "member-record-unreadable", "reason": ""}]}, "not a repository"),
        ({"evidence": [{"repository": "example-owner/a", "checks": {}}]}, "not a repository"),
        ({"evidence": "rows"}, "not a list"),
        ({"evidence": [row("Example-Owner/a")]}, "lowercased"),
        ({"evidence": [row("example-owner")]}, "lowercased owner/repo"),
        ({"evidence": [row("example-owner/a"), row("example-owner/a")]}, "more than one row"),
        ({"evidence": [row("example-owner/a", **{"factory.pat_scope": "pass"})]}, "four ids"),
        (
            {"evidence": [{"type": "repository", "repository": "o/a", "checks": {"x": 1}}]},
            "four ids",
        ),
    ],
)
def test_a_newest_event_outside_the_contract_is_no_usable_scan_and_never_falls_back(
    overrides: dict[str, Any], why: str
) -> None:
    older = scan("2026-11-16T08:00:35Z")
    newest = scan("2026-11-17T08:00:35Z", **overrides)

    answer = portfolio_scan([(1, older), (2, newest)], NOW)

    assert answer["scan"] is None
    assert "seq 2" in answer["reason"] and why in answer["reason"]


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"actor": "factory-events-nightly"}, "actor is not portfolio-scan"),
        ({"source": {"system": "direct", "ref": "x"}}, "source.system is not portfolio-scan"),
        ({"timestamp": None}, "not a whole-second UTC time"),
        ({"timestamp": "2026-11-17T09:00:35.5Z"}, "not a whole-second UTC time"),
        ({"timestamp": "2026-02-30T09:00:35Z"}, "not a whole-second UTC time"),
    ],
)
def test_a_malformed_event_is_skipped_with_its_reason_and_an_older_valid_one_is_used(
    overrides: dict[str, Any], why: str
) -> None:
    valid = scan("2026-11-16T08:00:35Z")
    malformed = scan("2026-11-17T09:00:35Z", **overrides)

    answer = portfolio_scan([(1, valid), (2, malformed)], NOW)

    assert answer["scan"]["seq"] == 1
    assert answer["skipped"] == 1
    assert answer["skipped_reason"].startswith("seq 2: ") and why in answer["skipped_reason"]


def test_a_malformed_event_alone_is_never_read_as_a_pass() -> None:
    answer = portfolio_scan([(1, scan(timestamp=None))], NOW)

    assert answer["scan"] is None
    assert answer["reason"] == "no portfolio.scanned event in the chain"
    assert answer["skipped"] == 1


def test_a_future_dated_event_past_the_tolerance_is_skipped_and_an_older_one_is_used() -> None:
    future = scan(_at(timedelta(seconds=SCAN_FUTURE_SKEW_SECONDS + 1)))
    older = scan("2026-11-17T08:00:35Z")

    answer = portfolio_scan([(1, older), (2, future)], NOW)

    assert answer["scan"]["seq"] == 1
    assert "past the tolerance" in answer["skipped_reason"]


def test_a_future_dated_event_alone_gives_no_usable_scan() -> None:
    answer = portfolio_scan([(1, scan(_at(timedelta(days=30))))], NOW)

    assert answer["scan"] is None and answer["skipped"] == 1


def test_an_event_within_the_tolerance_is_read() -> None:
    within = scan(_at(timedelta(seconds=SCAN_FUTURE_SKEW_SECONDS)))

    answer = portfolio_scan([(1, within)], NOW)

    assert answer["scan"]["seq"] == 1


def test_two_events_sharing_the_newest_scan_time_are_ambiguous() -> None:
    first = scan("2026-11-17T08:00:35Z", evidence=[row("example-owner/a")])
    second = scan("2026-11-17T08:00:35Z", evidence=[row("example-owner/b")])

    answer = portfolio_scan([(3, first), (4, second)], NOW)

    assert answer["scan"] is None
    assert "seqs 3, 4 share the newest scan time" in answer["reason"]


def test_status_values_are_copied_whatever_they_are() -> None:
    event = scan(evidence=[row("example-owner/a", **{"runner.caller": "not-applicable"})])

    answer = portfolio_scan([(1, event)], NOW)

    assert answer["scan"]["rows"] == [
        {"repository": "example-owner/a", "checks": {"runner.caller": "not-applicable"}}
    ]


# ---- Through the child: the answer rides a verified record only --------------------------------


def test_a_verified_record_carries_the_scan_beside_the_approvals(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    recent = scan(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _fake_driver(monkeypatch, rows=[_row(1, recent)])

    code, record, _out = _main(capsys)

    assert code == factory_chain_read.VERIFIED
    assert record["portfolio_scan"]["scan"]["seq"] == 1
    assert record["package_approved"] == []


def test_a_verified_record_with_no_scan_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    _fake_driver(monkeypatch, rows=[])

    _code, record, _out = _main(capsys)

    assert record["portfolio_scan"]["scan"] is None
    assert record["portfolio_scan"]["reason"] == "no portfolio.scanned event in the chain"


def test_a_broken_walk_carries_no_scan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    _fake_driver(monkeypatch, rows=[_row(1, scan())], walk_errors=["seq 1: hash mismatch"])

    code, record, _out = _main(capsys)

    assert code == factory_chain_read.UNAVAILABLE
    assert "portfolio_scan" not in record
