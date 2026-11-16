"""`work-watcher repositories`: the repositories the lane's container mints its GitHub token for.

ADR-0059 decision 2 bounds each pass's mint to its lane's repositories, so the list must hold every
repository the pass reads on GitHub (the packages clone and each live record's pull request) and
fail closed, with nothing on stdout, when it can't be derived.
"""

from __future__ import annotations

import contextlib
import io

import pytest

from work_carrier.change_manager import ChangeManagerError
from work_watcher.cli import EXIT_OK, EXIT_TOOL_FAILURE, EXIT_UNUSABLE, main, repositories
from work_watcher.pipeline import Pipeline, PipelineRecord


def _row(change_record_id: int, status: str, reasoning: str) -> PipelineRecord:
    return PipelineRecord(
        change_record_id=change_record_id,
        status=status,
        package_id=f"package-{change_record_id}",
        package_revision=1,
        reasoning=reasoning,
    )


def _reasoning(repository: str, number: int) -> str:
    """The opening clause `bump_proposer.cli._reasoning` writes, which `pull_request_of` reads."""
    return (
        f"{repository} carries an open dependency update of eslint from 9.0.0 to 9.1.0 "
        f"(pull request {number}); the rest of the reasoning"
    )


class Listing:
    def __init__(self, *rows: PipelineRecord, error: Exception | None = None) -> None:
        self._rows = rows
        self._error = error

    def approved_work(self):  # pragma: no cover - the subcommand never asks for it
        raise AssertionError("repositories reads the pipeline, not the approved queue")

    def work_pipeline(self) -> Pipeline:
        if self._error is not None:
            raise self._error
        return Pipeline(self._rows, ("a row the listing could not parse",))


def _repositories(listing: Listing) -> tuple[int, list[str], str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err):
        code = repositories(listing, out=out)
    return code, out.getvalue().splitlines(), err.getvalue()


def test_it_names_the_packages_repository_and_each_live_records_pull_request_repository() -> None:
    code, names, _ = _repositories(
        Listing(
            _row(1, "approved", _reasoning("AlobarQuest/community-atlas", 12)),
            _row(2, "pending", _reasoning("AlobarQuest/crm", 3)),
            _row(3, "approved", "the human approved building it"),
        )
    )
    assert code == EXIT_OK
    assert names == [
        "AlobarQuest/community-atlas",
        "AlobarQuest/crm",
        "AlobarQuest/intent-packages",
    ]


def test_a_record_that_is_not_live_names_nothing() -> None:
    """The watcher asks GitHub only about pending and approved records, so a retired or
    superseded record's pull request needs no token."""
    code, names, _ = _repositories(
        Listing(
            _row(1, "resolved", _reasoning("AlobarQuest/community-atlas", 12)),
            _row(2, "wontfix", _reasoning("AlobarQuest/crm", 3)),
        )
    )
    assert code == EXIT_OK
    assert names == ["AlobarQuest/intent-packages"]


def test_a_repository_named_twice_in_different_case_is_printed_once() -> None:
    code, names, _ = _repositories(
        Listing(
            _row(1, "approved", _reasoning("AlobarQuest/CRM", 3)),
            _row(2, "approved", _reasoning("alobarquest/crm", 4)),
            _row(3, "approved", _reasoning("alobarquest/Intent-Packages", 5)),
        )
    )
    assert code == EXIT_OK
    assert names == ["AlobarQuest/CRM", "AlobarQuest/intent-packages"]


def test_an_unreadable_listing_is_a_tool_failure_with_nothing_on_stdout() -> None:
    code, names, err = _repositories(Listing(error=ChangeManagerError("503 from change-manager")))
    assert code == EXIT_TOOL_FAILURE
    assert names == []
    assert "503 from change-manager" in err


def _entrypoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, str, str]:
    """`work-watcher repositories` as the launcher runs it, through `main`."""
    monkeypatch.setattr("sys.argv", ["work-watcher", *argv])
    code = main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_no_change_manager_credential_is_unusable_with_nothing_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("WORK_WATCHER_CHANGE_MANAGER_TOKEN", raising=False)
    code, out, err = _entrypoint(monkeypatch, capsys, "repositories")
    assert code == EXIT_UNUSABLE
    assert out == ""
    assert "WORK_WATCHER_CHANGE_MANAGER_TOKEN is not set" in err


def test_an_extra_argument_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WORK_WATCHER_CHANGE_MANAGER_TOKEN", "a-token")
    code, out, err = _entrypoint(monkeypatch, capsys, "repositories", "--retire")
    assert code == EXIT_UNUSABLE
    assert out == ""
    assert "usage: work-watcher repositories" in err
