"""The container carrier's capability source: the scan feed from the chain (ADR-0059 amendment 11).

On the lane's container pass the launcher hands `work-carrier` the verified chain read's
`portfolio_scan` answer as `--portfolio-scan <file>`. These tests pin what the carrier does with
it: the newest scan's rows answer `capable` and `permissions` exactly as `portfolio.json`'s rows
do, joined on the full `owner/repo` case-folded; a missing, stale, `unknown` or rowless feed holds
a repository-targeted record (UNKNOWN, so `UNDECIDED`, exit 2); every such pass prints one `[feed]`
line; and a pass given no feed (the Mac's) still reads the file.
"""

from __future__ import annotations

import io
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from orchestrator.factory_chain_read import portfolio_scan
from tests.cli.test_factory_chain_read_scan import row, scan
from tests.work_carrier.test_workability import (
    TARGET,
    _emitted,
    _emitting,
    _record,
    _Source,
    member,
)
from work_carrier import cli as cli_module
from work_carrier.cli import EXIT_OK, EXIT_UNUSABLE, run
from work_carrier.workability import (
    NO,
    PORTFOLIO_MAX_AGE_HOURS,
    UNDECIDED,
    UNKNOWN,
    WORKABLE,
    YES,
    ScanFeed,
    assess,
    load_scan_feed,
)

ALL_PASS = {
    "runner.caller": "pass",
    "factory.secrets": "pass",
    "factory.landing_known": "pass",
    "factory.pat_access": "pass",
}


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _feed_file(tmp_path: Path, answer: object) -> Path:
    path = tmp_path / "portfolio-scan.json"
    path.write_text(json.dumps(answer), encoding="utf-8")
    return path


def _answer(*events: tuple[int, dict[str, Any]]) -> dict[str, Any]:
    """The chain read's own answer for these events, judged at the real current time."""
    return portfolio_scan(list(events), datetime.now(UTC))


def _fresh(*rows: dict[str, Any], hours: float = 1, result: str = "success") -> dict[str, Any]:
    scanned = _stamp(datetime.now(UTC) - timedelta(hours=hours))
    return _answer((5, scan(scanned, result=result, evidence=list(rows))))


def _load(tmp_path: Path, answer: object) -> ScanFeed:
    return load_scan_feed(_feed_file(tmp_path, answer))


def _terms(feed: ScanFeed, repository: str = TARGET) -> dict[str, str]:
    verdict = assess(repository, True, feed)
    return {term.name: term.verdict for term in verdict.constraints}


# ---- The feed's answers, as the file's ----------------------------------------------------------


def test_a_fresh_feed_answers_as_the_file_does(tmp_path: Path) -> None:
    feed = _load(tmp_path, _fresh(row(TARGET.lower(), **ALL_PASS)))

    assert feed.usable
    assert assess(TARGET, True, feed).decision == WORKABLE


def test_the_join_is_the_whole_name_case_folded(tmp_path: Path) -> None:
    """`AlobarQuest/infraops-mcp-server` on the record finds the lowercased row, and a repository
    with the same name under another owner does not."""
    feed = _load(tmp_path, _fresh(row("example-owner/infraops-mcp-server", **ALL_PASS)))

    assert _terms(feed) == {"granted": YES, "capable": UNKNOWN, "permissions": UNKNOWN}
    assert assess("Example-Owner/Infraops-MCP-Server", True, feed).decision == WORKABLE


def test_a_repository_with_no_row_holds_for_a_reason_that_says_so(tmp_path: Path) -> None:
    """A repository outside the member record never has a row, so its record holds this way."""
    feed = _load(tmp_path, _fresh(row("example-owner/a-member", **ALL_PASS)))

    verdict = assess(TARGET, True, feed)

    assert verdict.decision == UNDECIDED
    assert "has no row for AlobarQuest/infraops-mcp-server" in verdict.constraints[1].detail


@pytest.mark.parametrize(
    ("status", "capable"),
    [("violation", NO), ("not-applicable", UNKNOWN), ("a-status-nobody-planned", UNKNOWN)],
)
def test_a_status_maps_as_the_file_maps_it(tmp_path: Path, status: str, capable: str) -> None:
    checks = {**ALL_PASS, "runner.caller": status}
    feed = _load(tmp_path, _fresh(row(TARGET.lower(), **checks)))

    assert _terms(feed)["capable"] == capable


def test_a_missing_check_is_unknown(tmp_path: Path) -> None:
    checks = {key: value for key, value in ALL_PASS.items() if key != "factory.secrets"}
    feed = _load(tmp_path, _fresh(row(TARGET.lower(), **checks)))

    assert _terms(feed)["capable"] == UNKNOWN


# ---- Holds -------------------------------------------------------------------------------------


def test_no_event_holds_every_repository_targeted_record(tmp_path: Path) -> None:
    feed = _load(tmp_path, _answer())

    assert not feed.usable
    assert "no portfolio.scanned event in the chain" in feed.detail
    assert _terms(feed)["capable"] == UNKNOWN


def test_a_scan_past_48_hours_holds_whatever_its_seq(tmp_path: Path) -> None:
    """An old scan time appended last (the highest `seq`) is still old: the age is the scan's."""
    hours = PORTFOLIO_MAX_AGE_HOURS + 1
    old = _stamp(datetime.now(UTC) - timedelta(hours=hours))
    feed = _load(tmp_path, _answer((999, scan(old, evidence=[row(TARGET.lower(), **ALL_PASS)]))))

    assert not feed.usable
    assert f"past the {PORTFOLIO_MAX_AGE_HOURS}h limit" in feed.detail
    assert assess(TARGET, True, feed).decision == UNDECIDED


def test_a_scan_just_inside_48_hours_is_read(tmp_path: Path) -> None:
    feed = _load(
        tmp_path, _fresh(row(TARGET.lower(), **ALL_PASS), hours=PORTFOLIO_MAX_AGE_HOURS - 0.1)
    )

    assert feed.usable


def test_an_unknown_scan_holds_every_record(tmp_path: Path) -> None:
    feed = _load(tmp_path, _fresh(result="unknown"))

    assert not feed.usable
    assert "could not read the member record" in feed.detail
    assert assess(TARGET, True, feed).decision == UNDECIDED


def test_a_malformed_newest_event_holds_and_is_never_a_pass(tmp_path: Path) -> None:
    bad = scan(_stamp(datetime.now(UTC)), evidence=[{"type": "other"}])
    feed = _load(tmp_path, _answer((1, bad)))

    assert not feed.usable
    assert "outside the contract" in feed.detail


@pytest.mark.parametrize(
    "answer",
    [
        "not json at all",
        {"scan": {"scanned_epoch": "yesterday", "scanned_at": "x", "result": "success"}},
        {"scan": {"scanned_epoch": True, "scanned_at": "x", "result": "success", "rows": []}},
        {"scan": {"scanned_epoch": 1, "scanned_at": "x", "result": "failure", "rows": []}},
        {"scan": {"scanned_epoch": 1, "scanned_at": "x", "result": "success", "rows": [1]}},
        None,
    ],
)
def test_an_unreadable_hand_over_holds(tmp_path: Path, answer: object) -> None:
    path = tmp_path / "feed.json"
    path.write_text(answer if isinstance(answer, str) else json.dumps(answer), encoding="utf-8")

    feed = load_scan_feed(path)

    assert not feed.usable
    assert assess(TARGET, True, feed).decision == UNDECIDED


def test_duplicate_rows_in_the_hand_over_hold(tmp_path: Path) -> None:
    scanned = {"scanned_epoch": int(time.time()), "scanned_at": "x", "result": "success"}
    rows = [{"repository": TARGET, "checks": ALL_PASS}, {"repository": TARGET, "checks": {}}]
    feed = _load(tmp_path, {"scan": {**scanned, "rows": rows}})

    assert not feed.usable


def test_a_missing_hand_over_file_holds(tmp_path: Path) -> None:
    feed = load_scan_feed(tmp_path / "absent.json")

    assert not feed.usable and "could not be read" in feed.detail


# ---- The pass ----------------------------------------------------------------------------------


def _pass(
    tmp_path: Path,
    checkout_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: object | None,
    records: tuple = (),
) -> tuple[int, str]:
    """A real bare pass, with the real judge; `answer` None is the Mac's pass (no feed)."""
    import work_carrier.prepare as prepare_module

    original = prepare_module.prepare
    monkeypatch.setattr(
        cli_module, "prepare", lambda r, **kw: original(r, **kw, runner=_emitting(_emitted(r)))
    )
    argv = ["--checkout-root", str(checkout_root)]
    if answer is not None:
        argv += ["--portfolio-scan", str(_feed_file(tmp_path, answer))]
    out = io.StringIO()
    code = run(argv, source=_Source(records), members=[member(app_changes="granted")], out=out)
    return code, out.getvalue()


def test_a_fresh_feed_carries_the_record(
    tmp_path: Path, checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control: without it every hold below could pass for free."""
    answer = _fresh(row(TARGET.lower(), **ALL_PASS))

    code, report = _pass(tmp_path, checkout_root, monkeypatch, answer, (_record(),))

    assert code == EXIT_OK
    assert f"[{WORKABLE}]" in report


def test_a_stale_feed_holds_the_record_and_exits_unusable(
    tmp_path: Path, checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = _fresh(row(TARGET.lower(), **ALL_PASS), hours=PORTFOLIO_MAX_AGE_HOURS + 1)

    code, report = _pass(tmp_path, checkout_root, monkeypatch, answer, (_record(),))

    assert code == EXIT_UNUSABLE
    assert UNDECIDED in report


def test_every_container_pass_prints_one_feed_line_first(
    tmp_path: Path, checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _code, report = _pass(tmp_path, checkout_root, monkeypatch, _fresh(row(TARGET.lower())))

    lines = report.splitlines()
    assert [line for line in lines if line.startswith("[feed] ")] == [lines[0]]
    assert "portfolio.scanned at " in lines[0] and "1 row, result success" in lines[0]
    assert "1h old" in lines[0]


def test_an_empty_queue_with_a_stale_feed_keeps_todays_exit(
    tmp_path: Path, checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = _fresh(row(TARGET.lower()), hours=PORTFOLIO_MAX_AGE_HOURS + 5)

    code, report = _pass(tmp_path, checkout_root, monkeypatch, answer)

    assert code == EXIT_OK
    assert report.startswith("[feed] ") and "past the 48h limit" in report


def test_the_feed_line_names_why_there_is_no_usable_event(
    tmp_path: Path, checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _code, report = _pass(tmp_path, checkout_root, monkeypatch, _answer())

    assert report.splitlines()[0].startswith(
        "[feed] no usable portfolio.scanned event: no portfolio.scanned event in the chain"
    )


def test_the_feed_line_comes_before_the_pass_can_end_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` returns before `run` when a registering pass has no orchestrator token."""
    feed = _feed_file(tmp_path, _answer())
    monkeypatch.delenv("WORK_CARRIER_ORCHESTRATOR_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["work-carrier", "--register", "--portfolio-scan", str(feed)])

    code = cli_module.main()

    lines = capsys.readouterr().out.splitlines()
    assert code == EXIT_UNUSABLE
    assert lines[0].startswith("[feed] no usable portfolio.scanned event")
    assert lines[1] == "[UNUSABLE] WORK_CARRIER_ORCHESTRATOR_TOKEN is not set"


def test_the_feed_line_is_printed_once_through_main(
    tmp_path: Path,
    checkout_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feed = _feed_file(tmp_path, _answer())
    argv = ["work-carrier", "--checkout-root", str(checkout_root), "--portfolio-scan", str(feed)]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli_module, "_source", lambda args: _Source(()))
    monkeypatch.setattr(cli_module, "read_members_from_environment", lambda: [])

    cli_module.main()

    out = capsys.readouterr().out
    assert out.count("[feed] ") == 1


def test_the_macs_pass_still_reads_the_file(
    tmp_path: Path, checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--portfolio-scan`: `WORK_CARRIER_PORTFOLIO` (here a fresh, clean file) answers."""
    rows = [
        {
            "name": "infraops-mcp-server",
            "path": "/somewhere/infraops-mcp-server",
            "factory": [{"id": key, "status": value} for key, value in ALL_PASS.items()],
        }
    ]
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(json.dumps({"projects": rows}), encoding="utf-8")
    monkeypatch.setenv("WORK_CARRIER_PORTFOLIO", str(portfolio))

    code, report = _pass(tmp_path, checkout_root, monkeypatch, None, (_record(),))

    assert code == EXIT_OK
    assert "[feed]" not in report
    assert "capability source: portfolio.json is " in report
    assert f"[{WORKABLE}]" in report
