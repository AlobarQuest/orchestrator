"""The work carrier's portfolio read in the lane's container (ADR-0059 decision 5, D-55 A).

The container has no portfolio scan: `lane-run` passes no `WORK_CARRIER_PORTFOLIO`, and each pass's
`HOME` is new and empty, so `~/.portfolio/portfolio.json` is absent. The container's launcher hands
the carry the chain's newest scan instead (`--portfolio-scan`, ADR-0059 amendment 11; its tests are
`test_scan_feed.py`). These tests pin the file read with no such feed: a repository-targeted record
is held (UNKNOWN, never carried), while a standing rotation package, which asks no portfolio
question, still carries.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from scripts import lane_run
from tests.work_carrier.test_operational_branch import ROTATED, ROTATION_GRANTS, operational
from tests.work_carrier.test_workability import GRANTED, TARGET, payload
from work_carrier.workability import OPERATIONAL, PORTFOLIO_ENV, UNDECIDED, judge, load_portfolio


@pytest.fixture
def container_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The environment `lane-run` gives the launcher: a new empty `HOME`, and no portfolio path."""
    home = tmp_path / "lane-run-home"
    home.mkdir()
    environment = lane_run.lane_environment(
        {"BWS_ACCESS_TOKEN": "t", PORTFOLIO_ENV: "/somewhere/portfolio.json"}, str(home)
    )
    assert PORTFOLIO_ENV not in environment
    monkeypatch.delenv(PORTFOLIO_ENV, raising=False)
    monkeypatch.setenv("HOME", environment["HOME"])
    return home


def test_the_container_has_no_portfolio_to_read(container_home: Path) -> None:
    loaded = load_portfolio()
    assert loaded.projects == ()
    assert loaded.age_seconds is None
    assert str(container_home / ".portfolio" / "portfolio.json") in loaded.detail


@pytest.mark.usefixtures("container_home")
def test_a_repository_targeted_record_holds_while_a_standing_rotation_still_carries() -> None:
    """The targeted record is granted, so only the missing portfolio holds it: UNKNOWN on its
    capability and permissions, never a refusal and never a carry."""
    grants = {**ROTATION_GRANTS, "app_changes": GRANTED["app_changes"]}
    out = io.StringIO()
    decisions = judge(
        [("repository-targeted", payload(TARGET)), ("standing rotation", operational())],
        out,
        grants=grants,
        rotated=ROTATED,
    )

    assert decisions == (UNDECIDED, OPERATIONAL)
    # Only the targeted record's block names these three terms.
    terms = {"granted", "capable", "permissions"}
    rows = [line.split()[:2] for line in out.getvalue().splitlines()]
    verdicts = {row[0]: row[1] for row in rows if len(row) == 2 and row[0] in terms}
    assert verdicts == {"granted": "yes", "capable": "unknown", "permissions": "unknown"}
