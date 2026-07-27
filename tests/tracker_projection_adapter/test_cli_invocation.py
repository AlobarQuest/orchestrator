"""CLI-level invocation tests for the tracker projection adapter.

The Task-7 unit tests exercised `project()` directly and never went through the Typer CLI, so
they missed that a lone Typer command collapses to the top level — which broke the
`tracker-projection-adapter project ...` invocation the launcher uses. These tests drive the
actual CLI so that regression cannot recur.
"""

from typer.testing import CliRunner

from tracker_projection_adapter import cli
from tracker_projection_adapter.cli import app

runner = CliRunner()


def test_project_is_a_named_command() -> None:
    top = runner.invoke(app, ["--help"])
    assert top.exit_code == 0
    assert "project" in top.output

    sub = runner.invoke(app, ["project", "--help"])
    assert sub.exit_code == 0
    assert "--todoist-project-id" in sub.output


def test_project_dry_run_reads_and_makes_no_writes(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.upserts: list[dict] = []

        def status_ledger(self) -> list[dict]:
            return [{"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "ready"}]

        def tracker_bindings(self) -> list[dict]:
            return []

        def upsert_tracker_binding(self, **kwargs: object) -> dict:
            self.upserts.append(kwargs)
            return {}

    monkeypatch.setenv("TRACKER_PROJECTION_TOKEN", "fixture-token")
    monkeypatch.setattr(cli, "OrchestratorClient", FakeClient)

    result = runner.invoke(app, ["project", "--dry-run", "--todoist-project-id", "proj-1"])
    assert result.exit_code == 0, result.output
    assert '"create": 1' in result.output


def test_missing_token_exits_nonzero(monkeypatch) -> None:
    monkeypatch.delenv("TRACKER_PROJECTION_TOKEN", raising=False)
    result = runner.invoke(app, ["project", "--dry-run", "--todoist-project-id", "proj-1"])
    assert result.exit_code == 1
