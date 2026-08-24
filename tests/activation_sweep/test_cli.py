"""The three answers a scheduled caller needs, and the two refusals that protect them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from activation_sweep.checkout import BEHIND, DIRTY
from activation_sweep.cli import (
    EXIT_FINDINGS,
    EXIT_INCOMPLETE,
    EXIT_OK,
    TOKEN_VARIABLE,
    app,
    sweep_checkout,
)
from activation_sweep.orchestrator_client import ForbiddenEndpointError, SweepWriteError
from tests.activation_sweep.conftest import Estate

runner = CliRunner()


class Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.error = error
        self.opened: dict[str, Any] = {}

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(payload)
        if self.error is not None:
            raise self.error
        return {"id": "00000000-0000-0000-0000-000000000000"}


def _install_recorder(monkeypatch: pytest.MonkeyPatch, written: Recorder) -> Recorder:
    """Stand in for the HTTP client, so the CLI is exercised through its real entry point."""
    seen: dict[str, Any] = {}

    class Client:
        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None: ...

        def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
            return written.record_observation(payload)

    def opened(**kwargs: Any) -> Client:
        seen.update(kwargs)
        return Client()

    monkeypatch.setattr("activation_sweep.cli.open_client", opened)
    monkeypatch.setenv(TOKEN_VARIABLE, "bearer-stand-in")
    written.opened = seen
    return written


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    return _install_recorder(monkeypatch, Recorder())


@pytest.fixture
def refusing_recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    return _install_recorder(
        monkeypatch, Recorder(error=SweepWriteError("orchestrator rejected POST: 401"))
    )


def _invoke(*args: str) -> Any:
    return runner.invoke(app, ["sweep", *args])


def test_a_clean_current_estate_exits_zero(estate: Estate, recorder: Recorder) -> None:
    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == EXIT_OK
    summaries = json.loads(result.stdout)
    assert summaries[0]["conditions"] == []
    assert summaries[0]["recorded"] is True
    assert len(recorder.bodies) == 1


def test_a_checkout_behind_its_upstream_is_a_finding(estate: Estate, recorder: Recorder) -> None:
    estate.land_upstream()

    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == EXIT_FINDINGS
    assert json.loads(result.stdout)[0]["conditions"] == [BEHIND]


def test_a_dirty_checkout_is_a_finding(estate: Estate, recorder: Recorder) -> None:
    estate.modify_tracked()

    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == EXIT_FINDINGS
    assert json.loads(result.stdout)[0]["conditions"] == [DIRTY]


def test_an_unmeasurable_checkout_outranks_a_finding(
    estate: Estate, recorder: Recorder, tmp_path: Path
) -> None:
    """An incomplete pass cannot claim it found everything there was to find. The pass still
    covers every other checkout: a program that died on the third of nine would discard the two
    it had already filed."""
    estate.land_upstream()
    absent = tmp_path / "not-a-repository"
    absent.mkdir()

    result = _invoke("--checkout", str(absent), "--checkout", str(estate.local))

    assert result.exit_code == EXIT_INCOMPLETE
    summaries = json.loads(result.stdout)
    assert summaries[0]["unavailable"] is True
    assert summaries[1]["conditions"] == [BEHIND]
    assert summaries[1]["recorded"] is True
    assert len(recorder.bodies) == 1


def test_a_measurement_that_could_not_be_filed_is_incomplete_not_clean(estate: Estate) -> None:
    """The measurement was fine and the answer is unreadable, which are different things and are
    reported as different fields. Both make the pass incomplete."""
    refused = Recorder(error=SweepWriteError("orchestrator rejected POST: 422"))

    summary = sweep_checkout(str(estate.local), refused, fetch=True, dry_run=False)

    assert summary["unavailable"] is False
    assert summary["recorded"] is False
    assert summary["conditions"] == []


def test_a_pass_that_filed_nothing_is_incomplete_and_never_exits_zero(
    estate: Estate, refusing_recorder: Recorder
) -> None:
    """THE ONLY CONTROL ON `_exit_code`'S SECOND CLAUSE, and it was missing. Without it, deleting
    `or summary["recorded"] is False` leaves the suite green while a pass in which the
    orchestrator refused every row exits 0 -- the lane reporting that the machine is current and
    everything was filed, having filed nothing. That is the silent-success shape the sweep exists
    to catch in something else."""
    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == EXIT_INCOMPLETE
    summary = json.loads(result.stdout)[0]
    assert summary["conditions"] == []
    assert summary["unavailable"] is False
    assert summary["recorded"] is False
    # And it says WHY. Nine expired-bearer 401s and nine broken repositories are otherwise the
    # same exit code and the same log line.
    assert "401" in summary["reason"]


def test_an_unmeasurable_checkout_says_why(estate: Estate, recorder: Recorder) -> None:
    """Two different unmeasurable checkouts, two different reasons -- which is the whole point:
    without the reason the operator sees one exit code and nine identical lines."""
    nested = estate.local / "nested"
    nested.mkdir()

    absent = _invoke("--checkout", str(estate.local / "does-not-exist"))
    subdirectory = _invoke("--checkout", str(nested))

    assert absent.exit_code == EXIT_INCOMPLETE
    assert subdirectory.exit_code == EXIT_INCOMPLETE
    assert "git rev-parse exited 128" in json.loads(absent.stdout)[0]["reason"]
    assert "not the root of a git working copy" in json.loads(subdirectory.stdout)[0]["reason"]


def test_the_credential_reaches_the_client(estate: Estate, recorder: Recorder) -> None:
    """`open_client` is stubbed everywhere else in this file, which would otherwise swallow every
    argument and leave the token's journey unasserted."""
    _invoke("--checkout", str(estate.local))

    assert recorder.opened["token"] == "bearer-stand-in"
    assert recorder.opened["credential_key_id"] == "orchestrator-observer"
    assert recorder.opened["base_url"] == "https://sds.alobar.net"


def test_a_guard_violation_is_never_absorbed_as_an_unreadable_checkout(estate: Estate) -> None:
    """`ForbiddenCommandError` and `ForbiddenEndpointError` are subclasses of families the
    per-checkout handler treats as recoverable. Absorbed, the two guards the isolation test
    exists to prove would fire in production and be reported as "this working copy could not be
    measured" -- the guard working and nobody hearing it."""
    forbidden = Recorder(error=ForbiddenEndpointError("the sweep may not write to /elsewhere"))

    with pytest.raises(ForbiddenEndpointError):
        sweep_checkout(str(estate.local), forbidden, fetch=True, dry_run=False)


def test_a_dry_run_writes_nothing_and_shows_what_it_would_have_written(estate: Estate) -> None:
    result = _invoke("--checkout", str(estate.local), "--dry-run")

    assert result.exit_code == EXIT_OK
    record = json.loads(result.stdout)[0]["record"]
    assert record["source_system"] == "machine_activation"
    assert json.loads(result.stdout)[0]["recorded"] is None


def test_a_dry_run_needs_no_credential(estate: Estate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)

    assert _invoke("--checkout", str(estate.local), "--dry-run").exit_code == EXIT_OK


def test_recording_without_a_credential_is_the_tool_failing(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)

    assert _invoke("--checkout", str(estate.local)).exit_code == 1


def test_an_unfetched_measurement_may_never_be_recorded(estate: Estate, recorder: Recorder) -> None:
    """SECTION 5.4 made mechanical. Without a fetch, `behind` is measured against stale refs and
    is always zero -- a row asserting that is worse than no row at all."""
    estate.land_upstream()

    refused = _invoke("--checkout", str(estate.local), "--no-fetch")

    assert refused.exit_code == 1
    assert recorder.bodies == []

    # And this is the very thing the refusal exists for, shown rather than described: with no
    # fetch the same behind checkout reads CURRENT and the pass exits clean.
    unfetched = _invoke("--checkout", str(estate.local), "--no-fetch", "--dry-run")

    assert unfetched.exit_code == EXIT_OK
    assert json.loads(unfetched.stdout)[0]["behind_by"] == 0

    fetched = _invoke("--checkout", str(estate.local), "--dry-run")

    assert fetched.exit_code == EXIT_FINDINGS
    assert json.loads(fetched.stdout)[0]["behind_by"] == 1


def test_an_unusable_orchestrator_url_is_the_tool_failing(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`httpx` refuses some malformed URLs at the CONSTRUCTOR and others at request time, so a
    guard on one half is not a guard. This is the constructor half, and it is the operator's typo
    rather than a checkout that could not be measured."""
    monkeypatch.setenv(TOKEN_VARIABLE, "bearer-stand-in")

    for unusable in ("https://ho\x00st", "https://sds..alobar.net", "https://" + "a" * 71 + ".net"):
        result = _invoke("--checkout", str(estate.local), "--orchestrator-url", unusable)

        assert result.exit_code == 1, unusable
        # `CliRunner` reports an ESCAPING exception as exit 1 too, so without this the test
        # cannot tell a clean translation from a traceback -- and the last two shapes construct
        # cleanly and would otherwise have raised at request time, been absorbed nine times, and
        # exited 3.
        assert result.exception is None or isinstance(result.exception, SystemExit), unusable
        assert "--orchestrator-url" in result.stdout + str(result.stderr or ""), unusable
