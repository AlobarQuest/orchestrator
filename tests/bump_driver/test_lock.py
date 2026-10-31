"""Only one acting pass at a time: a second must not reset a mirror under a running generator."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import Any

import pytest

from bump_driver import cli
from bump_driver.cli import LOCK_FILE, main


@pytest.fixture
def passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    for name in (
        "ORCHESTRATOR_API_TOKEN",
        "ORCHESTRATOR_API_CREDENTIAL_KEY_ID",
        "CHANGE_MANAGER_TOKEN",
        "BUMP_DRIVER_GITHUB_TOKEN",
        "SDS_BRIDGE_ALERT_TOKEN",
    ):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("BUMP_DRIVER_STATE_DIR", str(tmp_path / "state"))
    ran: list[bool] = []

    def run(environ: Any, state: Path, home: Path, *, dry_run: bool) -> int:
        ran.append(dry_run)
        # Held exclusively for the whole pass: even a shared request is refused while it runs.
        if not dry_run:
            with (state / LOCK_FILE).open("w") as other, pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return 7

    monkeypatch.setattr(cli, "_pass", run)
    return ran


def _held(tmp_path: Path) -> Any:
    state = tmp_path / "state"
    state.mkdir()
    handle = (state / LOCK_FILE).open("w")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def test_an_acting_pass_runs_under_the_lock_and_answers_its_own_code(passes: list[bool]) -> None:
    assert main([]) == 7
    assert passes == [False]


def test_the_lock_is_released_after_a_pass(passes: list[bool]) -> None:
    assert main([]) == 7
    assert main([]) == 7
    assert passes == [False, False]


def test_a_held_lock_exits_clean_and_says_so(
    passes: list[bool], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _held(tmp_path):
        assert main([]) == 0
    assert passes == []
    assert "another bump-driver pass is running" in capsys.readouterr().out


def test_a_dry_run_takes_no_lock(passes: list[bool], tmp_path: Path) -> None:
    with _held(tmp_path):
        assert main(["--dry-run"]) == 7
    assert passes == [True]


def test_a_dry_run_creates_no_lock_file(passes: list[bool], tmp_path: Path) -> None:
    main(["--dry-run"])
    assert not (tmp_path / "state" / LOCK_FILE).exists()


def test_an_unusable_state_directory_is_the_tool_failing(
    passes: list[bool], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "file").write_text("")
    monkeypatch.setenv("BUMP_DRIVER_STATE_DIR", str(tmp_path / "file" / "state"))
    assert main([]) == 1
    assert passes == []
