"""The pass fails open, counts what it lost, and reaches the CLI under its real invocation."""

from typing import Any

import httpx
from typer.testing import CliRunner

from landing_ledger.cli import app, record_landings
from landing_ledger.github import GitHubReader
from landing_ledger.orchestrator_client import LedgerWriteError
from tests.landing_ledger.test_github import REPO, gate_routes, reader_for


class _Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._fail = fail

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._fail:
            raise LedgerWriteError("orchestrator rejected POST /api/v1/observations: 503")
        self.bodies.append(payload)
        return {}


def _pass_routes() -> dict[str, object]:
    routes = gate_routes()
    routes[f"/repos/{REPO}"] = {"default_branch": "main"}
    routes[f"/repos/{REPO}/branches/main"] = {
        "commit": {"sha": "e931db8d31debfb08fd8f8410a4778f33c437fc1"}
    }
    routes[f"/repos/{REPO}/commits"] = [
        {"sha": "e931db8d31debfb08fd8f8410a4778f33c437fc1", "parents": []}
    ]
    routes[f"/repos/{REPO}/commits/e931db8d31debfb08fd8f8410a4778f33c437fc1"] = routes[
        f"/repos/{REPO}/commits/e931db8d"
    ]
    return routes


def _run(reader: GitHubReader, writer: Any, dry_run: bool = False) -> dict[str, Any]:
    return record_landings(
        reader,
        writer,
        repository=REPO,
        since="2026-08-01T00:00:00+00:00",
        pages=1,
        dry_run=dry_run,
    )


def test_a_pass_records_every_landing_it_can_read() -> None:
    recorder = _Recorder()

    summary = _run(reader_for(_pass_routes()), recorder)

    assert summary == {
        "repository": REPO,
        "landings": 1,
        "recorded": 1,
        "skipped": 0,
        "unavailable": False,
    }
    assert len(recorder.bodies) == 1


def test_github_being_unreachable_costs_the_pass_and_nothing_else() -> None:
    """A recorder is not a gate: nothing waits on it, so an outage must not raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    reader = GitHubReader(token="fixture", transport=httpx.MockTransport(handler))
    recorder = _Recorder()

    summary = _run(reader, recorder)

    assert summary["unavailable"] is True
    assert summary["recorded"] == 0
    assert recorder.bodies == []


def test_one_unreadable_landing_is_counted_rather_than_discarding_the_pass() -> None:
    routes = _pass_routes()
    readable = "e931db8d31debfb08fd8f8410a4778f33c437fc1"
    routes[f"/repos/{REPO}/commits"] = [
        {"sha": readable, "parents": [{"sha": "unreadable"}]},
        {"sha": "unreadable", "parents": []},
    ]
    recorder = _Recorder()

    summary = _run(reader_for(routes), recorder)

    assert (summary["landings"], summary["recorded"], summary["skipped"]) == (2, 1, 1)
    assert summary["unavailable"] is False
    assert len(recorder.bodies) == 1


def test_the_orchestrator_being_unreachable_is_counted_not_raised() -> None:
    summary = _run(reader_for(_pass_routes()), _Recorder(fail=True))

    assert (summary["recorded"], summary["skipped"]) == (0, 1)


def test_a_dry_run_writes_nothing() -> None:
    class _Explodes:
        def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("dry run must not record observations")

    summary = _run(reader_for(_pass_routes()), _Explodes(), dry_run=True)

    assert summary["recorded"] == 1


def test_the_command_is_reachable_under_its_real_invocation() -> None:
    """A lone Typer command collapses to the top level; the callback is what keeps `record`
    named, and only invoking it the way a launcher does proves that."""
    named = CliRunner().invoke(app, ["record", "--help"])
    unnamed = CliRunner().invoke(app, ["--help"])

    assert named.exit_code == 0
    # The discriminator: had the callback been dropped, `record` would collapse to the top level,
    # `record --help` would fail, and the top-level help would carry the options instead.
    assert "record" in unnamed.output
    assert "--repository" not in unnamed.output


def test_the_command_refuses_to_run_without_a_github_credential() -> None:
    result = CliRunner(env={"LANDING_LEDGER_GITHUB_TOKEN": "", "LANDING_LEDGER_TOKEN": ""}).invoke(
        app, ["record", "--repository", REPO]
    )

    assert result.exit_code == 1
