"""The four exit codes, which are the only thing a scheduled caller reads.

3 outranks 2 by the vocabulary its two sibling detectors use: an incomplete pass cannot claim it
found everything there was to find. The healthy case is exercised first so the codes below are a
discrimination rather than a constant.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from revision_watcher import cli
from revision_watcher.census import BEHIND, CURRENT, UNREADABLE, UNSTAMPED, Pass, Reading
from revision_watcher.subjects import Subject
from tests.revision_watcher.conftest import OLD, TIP, WHEN_TIP

SUBJECT = Subject("app-brain", "https://app-brain.example/api/health", "AlobarQuest/brain")
ENV = {
    "REVISION_WATCHER_GITHUB_TOKEN": "x",
    "ORCHESTRATOR_API_URL": "https://sds.example",
    "ORCHESTRATOR_API_CREDENTIAL_KEY_ID": "orchestrator-observer",
    "ORCHESTRATOR_API_TOKEN": "y",
    "REVISION_WATCHER_PLATFORM_URL": "",
    "REVISION_WATCHER_PLATFORM_TOKEN": "",
}


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.rows.append(payload)
        return {}

    def __enter__(self) -> _Recorder:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _drive(monkeypatch, result: Pass, recorder: Any = None, argv: list[str] | None = None):
    recorder = _Recorder() if recorder is None else recorder

    class _Null:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(cli, "GitHubReader", lambda **_: _Null())
    monkeypatch.setattr(cli, "ApplicationReader", lambda **_: _Null())
    monkeypatch.setattr(cli, "PlatformReader", lambda **_: _Null())
    monkeypatch.setattr(cli, "sweep", lambda **_: result)
    monkeypatch.setattr(cli, "open_client", lambda **_: recorder)
    return CliRunner(env=ENV).invoke(cli.app, argv or []), recorder


def _reading(state: str, served: str | None = TIP) -> Reading:
    return Reading(SUBJECT, state, served=served, expected=TIP, observed_at=WHEN_TIP)


def test_an_estate_serving_what_its_branches_name_exits_zero(monkeypatch) -> None:
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(CURRENT)]))

    assert result.exit_code == cli.EXIT_CLEAN


def test_an_application_behind_its_branch_tells_its_launcher(monkeypatch) -> None:
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(BEHIND, served=OLD)]))

    assert result.exit_code == cli.EXIT_FOUND


def test_an_application_that_could_not_be_read_is_INCOMPLETE_not_found(monkeypatch) -> None:
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(UNREADABLE, served=None)]))

    assert result.exit_code == cli.EXIT_INCOMPLETE


def test_could_not_measure_OUTRANKS_found_something(monkeypatch) -> None:
    """A pass that missed a subject cannot claim it found everything there was to find."""
    result, _ = _drive(
        monkeypatch,
        Pass(readings=[_reading(BEHIND, served=OLD), _reading(UNREADABLE, served=None)]),
    )

    assert result.exit_code == cli.EXIT_INCOMPLETE


def test_an_UNSTAMPED_application_alone_exits_zero(monkeypatch) -> None:
    """It is neither a finding nor a failure to measure -- it is an application that never
    claimed to be askable, and six of eighteen are in that state."""
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(UNSTAMPED, served=None)]))

    assert result.exit_code == cli.EXIT_CLEAN


def test_an_undeclared_application_is_a_finding(monkeypatch) -> None:
    """The table policing itself. Without this the declared list rots silently, which is the one
    objection that makes a declared list the defect rather than the design."""
    result, _ = _drive(monkeypatch, Pass(readings=[_reading(CURRENT)], undeclared=["newcomer"]))

    assert result.exit_code == cli.EXIT_FOUND


def test_coverage_that_could_not_be_measured_is_INCOMPLETE(monkeypatch) -> None:
    result, _ = _drive(
        monkeypatch,
        Pass(readings=[_reading(CURRENT)], coverage_unmeasured="platform unreachable"),
    )

    assert result.exit_code == cli.EXIT_INCOMPLETE


def test_a_measured_application_gets_a_row(monkeypatch) -> None:
    _, recorder = _drive(monkeypatch, Pass(readings=[_reading(CURRENT)]))

    assert [row["facts"]["application"] for row in recorder.rows] == ["app-brain"]


def test_an_unreadable_application_gets_NO_row(monkeypatch) -> None:
    """Nothing was measured, so there is nothing to assert. Its absence is already carried by the
    incomplete exit; a row saying "this could not be read" would be this lane asserting a
    condition about an application it never reached."""
    _, recorder = _drive(monkeypatch, Pass(readings=[_reading(UNREADABLE, served=None)]))

    assert recorder.rows == []


def test_a_dry_run_writes_NOTHING(monkeypatch) -> None:
    class _Explodes:
        def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("a dry run must not record observations")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    result, _ = _drive(
        monkeypatch, Pass(readings=[_reading(CURRENT)]), _Explodes(), argv=["--dry-run"]
    )

    assert result.exit_code == cli.EXIT_CLEAN


@pytest.mark.parametrize(
    "missing",
    ["REVISION_WATCHER_GITHUB_TOKEN", "ORCHESTRATOR_API_URL", "ORCHESTRATOR_API_TOKEN"],
)
def test_a_missing_credential_is_the_TOOL_failing_rather_than_a_finding(
    monkeypatch, missing
) -> None:
    """A broken tool sharing a code with an honest finding is a collision this estate has already
    paid for."""

    class _Null:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(cli, "GitHubReader", lambda **_: _Null())
    monkeypatch.setattr(cli, "ApplicationReader", lambda **_: _Null())
    monkeypatch.setattr(cli, "sweep", lambda **_: Pass(readings=[_reading(CURRENT)]))
    monkeypatch.setattr(cli, "open_client", lambda **_: _Recorder())

    result = CliRunner(env={**ENV, missing: ""}).invoke(cli.app, [])

    assert result.exit_code == cli.EXIT_TOOL_FAILED
