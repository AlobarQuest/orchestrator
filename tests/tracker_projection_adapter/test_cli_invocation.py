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

    # `project` parses as a named subcommand: `project --help` exits 0. The collapsed-lone-command
    # regression would instead reject "project" as an unexpected argument (exit code 2). We assert
    # exit codes, not help-body text, because Typer/rich wraps help output to the terminal width —
    # a width-dependent substring match passes on a wide local terminal and fails in narrow CI.
    assert runner.invoke(app, ["project", "--help"]).exit_code == 0
    assert runner.invoke(app, ["project", "--nonsense-flag"]).exit_code == 2


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


class FakeClient:
    def __init__(self, bindings: list[dict] | None = None, **kwargs: object) -> None:
        self.reported: list[dict] = []
        self._bindings = bindings or []

    def tracker_bindings(self) -> list[dict]:
        return self._bindings

    def report_tracker_reconciliation(self, *, observed_states, idempotency_key) -> dict:
        self.reported = observed_states
        return {}


class FakeProjectorCM:
    def __init__(self, completed: dict) -> None:
        self._completed = completed
        self.calls: list[tuple] = []

    def __enter__(self) -> "FakeProjectorCM":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def item_completed(self, item_ref) -> bool:
        self.calls.append(("item_completed", item_ref.external_item_id))
        return self._completed[item_ref.external_item_id]


def test_reconcile_is_a_named_command() -> None:
    assert runner.invoke(app, ["reconcile", "--help"]).exit_code == 0
    assert runner.invoke(app, ["reconcile", "--nonsense-flag"]).exit_code == 2


def test_reconcile_dry_run_runs(monkeypatch) -> None:
    binding = {
        "work_unit_id": "u1",
        "tracker_system": "todoist",
        "external_item_id": "tid-1",
        "external_url": None,
        "projected_state": "ready",
    }
    projectors: list[FakeProjectorCM] = []

    def _make_projector(**kw: object) -> FakeProjectorCM:
        projector = FakeProjectorCM(completed={"tid-1": False})
        projectors.append(projector)
        return projector

    monkeypatch.setenv("TRACKER_PROJECTION_TOKEN", "t")
    monkeypatch.setenv("TODOIST_API_TOKEN", "tt")
    client = FakeClient(bindings=[binding])
    monkeypatch.setattr(cli, "OrchestratorClient", lambda **kw: client)
    monkeypatch.setattr(cli, "TodoistProjector", _make_projector)
    result = runner.invoke(app, ["reconcile", "--dry-run", "--todoist-project-id", "p"])
    assert result.exit_code == 0
    assert '"reported": 1' in result.output
    assert client.reported == []
    assert projectors[0].calls == [("item_completed", "tid-1")]
