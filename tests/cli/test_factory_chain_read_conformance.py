"""The chain read's `mac_conformance` answer (ADR-0059 Amendment 13, decision item 4).

`orchestrator.factory_chain_read` collects the `mac_conformance.reported` events in the same
snapshot walk it makes for `package.approved`, and a verified record carries the newest one's ids
and statuses or the reason there is none. The events here are shaped as security-standards #101's
adapter (`factory_events.adapters.mac_conformance`, head `f3defd0b`) appends them. `mac_conformance`
is pure, so most of these call it directly; the last few run the child with the fake driver from
`test_package_chain_database`, to show the answer rides only a verified record.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import orchestrator.factory_chain_read as factory_chain_read
from orchestrator.factory_chain_read import SCAN_FUTURE_SKEW_SECONDS, mac_conformance
from tests.cli.test_package_chain_database import _fake_driver, _main, _read_chain, _row

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
WIRING = [
    {"type": "wiring", "executor": "hook:bws-scan-gate.sh", "status": "present"},
    {"type": "wiring", "executor": "launchagent:com.devon.change-window", "status": "present"},
    {"type": "wiring", "executor": "launchagent:com.devon.factory-events", "status": "present"},
    {"type": "wiring", "executor": "launchagent:com.devon.portfolio-scan", "status": "absent"},
    {"type": "wiring", "executor": "launchagent:com.devon.security-scan", "status": "present"},
]


def readable(
    generated_at: str = "2026-11-18T08:00:39Z",
    *,
    instances: dict[str, bool] | None = None,
    proposals: list[dict[str, Any]] | None = None,
    wiring: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """A readable report's event: `timestamp` and `source.ref` are its `generated_at`."""
    oks = {"prod": True, "dev": True} if instances is None else instances
    evidence: list[dict[str, Any]] = [{"type": "infra-drift-report", "generated_at": generated_at}]
    evidence += [{"type": "infra-drift-instance", "name": n, "ok": ok} for n, ok in oks.items()]
    evidence += proposals or []
    evidence += WIRING if wiring is None else wiring
    event = {
        "schema": "factory-event/v1",
        "event_id": f"evt-{generated_at}",
        "timestamp": generated_at,
        "actor": "mac-conformance-facts",
        "action": "mac_conformance.reported",
        "target": None,
        "result": "success" if all(oks.values()) else "unknown",
        "evidence": evidence,
        "source": {"system": "mac-conformance-facts", "ref": generated_at},
    }
    return {**event, **overrides}


def unreadable(run_at: str = "2026-11-18T08:30:02Z", reason: str = "report is not JSON") -> dict:
    """An unreadable newest report's record: `timestamp` is the nightly's run time."""
    return {
        **readable(run_at),
        "event_id": f"evt-unreadable-{run_at}",
        "result": "unknown",
        "evidence": [{"type": "infra-drift-report-unreadable", "reason": reason}, *WIRING],
        "source": {"system": "mac-conformance-facts", "ref": "unreadable:2026-11-18.json"},
    }


def proposal(instance: str, uuid: str | None, name: str | None, pid: str = "p-1") -> dict:
    return {
        "type": "infra-drift-proposal",
        "instance": instance,
        "id": pid,
        "target": {"uuid": uuid, "name": name},
    }


def test_the_newest_by_timestamp_wins_whatever_its_seq() -> None:
    """A late or out-of-order append never makes an older report win."""
    newer = readable("2026-11-18T08:00:39Z")
    older = readable("2026-11-17T08:00:12Z", instances={"prod": False})

    answer = mac_conformance([(10, newer), (11, older)], NOW)

    assert answer["event"]["seq"] == 10
    assert answer["event"]["generated_at"] == "2026-11-18T08:00:39Z"
    assert answer["event"]["instances"] == {"prod": True, "dev": True}


def test_an_unreadable_record_wins_over_the_previous_days_usable_event() -> None:
    """Its time is the nightly's run, later than the previous report's `generated_at`."""
    previous = readable("2026-11-17T07:59:59Z")
    record = unreadable("2026-11-18T08:30:02Z")

    answer = mac_conformance([(4, record), (3, previous)], NOW)

    event = answer["event"]
    assert event["seq"] == 4
    assert event["unreadable"] == "report is not JSON"
    assert event["generated_at"] is None
    assert event["result"] == "unknown"
    assert event["instances"] == {} and event["proposals"] == []
    assert event["wiring"]["launchagent:com.devon.change-window"] == "present"


def test_the_ids_and_statuses_are_carried_and_nothing_else() -> None:
    event = readable(
        proposals=[
            proposal("prod", "re45tafypao3nly3qa9a79dp", "change-manager"),
            proposal("dev", None, None, "p-2"),
        ],
    )

    answer = mac_conformance([(1, event)], NOW)

    assert answer == {
        "event": {
            "seq": 1,
            "timestamp": "2026-11-18T08:00:39Z",
            "generated_at": "2026-11-18T08:00:39Z",
            "unreadable": None,
            "result": "success",
            "instances": {"prod": True, "dev": True},
            "proposals": [
                {
                    "instance": "prod",
                    "id": "p-1",
                    "target": {"uuid": "re45tafypao3nly3qa9a79dp", "name": "change-manager"},
                },
                {"instance": "dev", "id": "p-2", "target": {"uuid": None, "name": None}},
            ],
            "wiring": {item["executor"]: item["status"] for item in WIRING},
        },
        "skipped": 0,
        "skipped_reason": None,
    }


def test_a_report_time_with_a_fraction_is_read() -> None:
    """The producer writes microseconds when the report has them; the scan's whole-second form
    would skip such an event and hold every bump for ever."""
    answer = mac_conformance([(1, readable("2026-11-18T08:00:39.123456Z"))], NOW)

    assert answer["event"]["generated_at"] == "2026-11-18T08:00:39.123456Z"


def test_no_event_is_no_usable_event_with_its_reason() -> None:
    assert mac_conformance([], NOW) == {
        "event": None,
        "reason": "no mac_conformance.reported event in the chain",
        "skipped": 0,
        "skipped_reason": None,
    }


@pytest.mark.parametrize(
    ("override", "why"),
    [
        ({"actor": "portfolio-scan"}, "its actor is not mac-conformance-facts"),
        ({"source": {"system": "x", "ref": "r"}}, "its source.system is not mac-conformance-facts"),
        ({"timestamp": "2026-11-18 08:00:39"}, "its timestamp is not a UTC time"),
        ({"timestamp": "2026-13-18T08:00:39Z"}, "its timestamp is not a UTC time"),
    ],
)
def test_a_non_candidate_is_skipped_and_an_older_candidate_is_used(
    override: dict[str, Any], why: str
) -> None:
    older = readable("2026-11-17T08:00:12Z")
    forged = readable("2026-11-18T08:00:39Z", **override)

    answer = mac_conformance([(1, older), (2, forged)], NOW)

    assert answer["event"]["seq"] == 1
    assert answer["skipped"] == 1
    assert answer["skipped_reason"] == f"seq 2: {why}"


def test_a_future_dated_event_past_the_tolerance_is_skipped() -> None:
    """Dated ahead, it would never age out of the append-only chain."""
    ahead = (NOW + timedelta(seconds=SCAN_FUTURE_SKEW_SECONDS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    answer = mac_conformance([(1, readable("2026-11-17T08:00:12Z")), (2, readable(ahead))], NOW)

    assert answer["event"]["seq"] == 1
    assert "past the tolerance" in answer["skipped_reason"]


def test_two_events_sharing_the_newest_time_are_ambiguous() -> None:
    answer = mac_conformance([(1, readable()), (2, readable())], NOW)

    assert answer["event"] is None
    assert answer["reason"] == "seqs 1, 2 share the newest report time, so neither is read"


@pytest.mark.parametrize(
    ("evidence", "why"),
    [
        ([], "its evidence is not a non-empty list"),
        (WIRING, "its first evidence item is not a report item"),
        (
            [{"type": "infra-drift-report", "generated_at": "yesterday"}],
            "its report's generated_at is not a UTC time",
        ),
        ([{"type": "infra-drift-report-unreadable"}], "its unreadable report carries no reason"),
        (
            [
                {"type": "infra-drift-report-unreadable", "reason": "x"},
                {"type": "infra-drift-instance", "name": "prod", "ok": True},
            ],
            "an unreadable report carries instance or proposal items",
        ),
        (
            [
                {"type": "infra-drift-report", "generated_at": "2026-11-18T08:00:39Z"},
                {"type": "infra-drift-instance", "name": "prod", "ok": "yes"},
            ],
            "an instance item is not a unique name with a boolean ok",
        ),
        (
            [
                {"type": "infra-drift-report", "generated_at": "2026-11-18T08:00:39Z"},
                {"type": "infra-drift-proposal", "instance": "prod", "id": 3, "target": {}},
            ],
            "a proposal item is not a listed instance, an id and a target's uuid and name",
        ),
        (
            [
                {"type": "infra-drift-report", "generated_at": "2026-11-18T08:00:39Z"},
                {"type": "infra-drift-instance", "name": "prod", "ok": True},
                {"type": "infra-drift-proposal", "instance": "ghost", "id": "p", "target": {}},
            ],
            "a proposal item is not a listed instance, an id and a target's uuid and name",
        ),
        (
            [
                {"type": "infra-drift-report", "generated_at": "2026-11-18T08:00:39Z"},
                {"type": "repository", "repository": "o/r"},
            ],
            "an evidence item is of no known type",
        ),
    ],
)
def test_a_newest_event_outside_the_contract_never_falls_back_to_an_older_one(
    evidence: list[Any], why: str
) -> None:
    older = readable("2026-11-17T08:00:12Z")
    newest = {**readable(), "evidence": evidence}

    answer = mac_conformance([(1, older), (2, newest)], NOW)

    assert answer["event"] is None
    assert answer["reason"] == (
        "the newest mac_conformance.reported event (seq 2, 2026-11-18T08:00:39Z) is outside the "
        f"contract: {why}"
    )


@pytest.mark.parametrize(
    "executor",
    [
        "launchagent:../../.ssh/authorized_keys",
        "launchagent:com/devon",
        "launchagent:.hidden",
        "hook:",
        "github-actions:quality.yml",
        "launchagent:com.devon.x y",
    ],
)
def test_a_wiring_name_that_isnt_a_plain_word_is_outside_the_contract(executor: str) -> None:
    """Any append holder can write this event, and a name becomes a file under the pass's HOME."""
    wiring = [{"type": "wiring", "executor": executor, "status": "present"}]

    answer = mac_conformance([(1, readable(wiring=wiring))], NOW)

    assert answer["event"] is None
    assert "a wiring item is not a unique launchagent: or hook: name" in answer["reason"]


@pytest.mark.parametrize("status", ["registered", None, True])
def test_a_wiring_status_outside_the_two_words_is_outside_the_contract(status: Any) -> None:
    wiring = [{"type": "wiring", "executor": "hook:bws-scan-gate.sh", "status": status}]

    assert mac_conformance([(1, readable(wiring=wiring))], NOW)["event"] is None


def test_a_repeated_wiring_entry_is_outside_the_contract() -> None:
    assert mac_conformance([(1, readable(wiring=[*WIRING, WIRING[0]]))], NOW)["event"] is None


def test_an_unknown_result_word_is_outside_the_contract() -> None:
    answer = mac_conformance([(1, readable(result="failure"))], NOW)

    assert answer["event"] is None
    assert answer["reason"].endswith("its result is 'failure'")


# ---- Through the child: the answer rides a verified record only --------------------------------


def test_a_verified_record_carries_the_selection_beside_the_approvals_and_the_scan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    recent = readable(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _fake_driver(monkeypatch, rows=[_row(1, recent)])

    code, record, _out = _main(capsys)

    assert code == factory_chain_read.VERIFIED
    assert record["mac_conformance"]["event"]["seq"] == 1
    assert record["package_approved"] == []
    assert record["portfolio_scan"]["scan"] is None


def test_a_verified_record_with_no_event_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    _fake_driver(monkeypatch, rows=[])

    _code, record, _out = _main(capsys)

    assert record["mac_conformance"] == {
        "event": None,
        "reason": "no mac_conformance.reported event in the chain",
        "skipped": 0,
        "skipped_reason": None,
    }


def test_a_broken_walk_carries_no_selection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    _fake_driver(monkeypatch, rows=[_row(1, readable())], walk_errors=["seq 1: hash mismatch"])

    code, record, _out = _main(capsys)

    assert code == factory_chain_read.UNAVAILABLE
    assert "mac_conformance" not in record
