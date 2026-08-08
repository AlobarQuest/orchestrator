"""The audit pass end to end: what it reads from GitHub, and what a launcher learns from it.

The exit codes are the point of most of this. A launcher is the only consumer a scheduled pass
has, and a launcher reads one number.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from landing_ledger.audit import STALL_ELIGIBLE_NOT_ARMED
from landing_ledger.cli import (
    EXIT_FINDINGS,
    EXIT_INCOMPLETE,
    EXIT_OK,
    _exit_code,
    app,
    audit_pass,
    pass_moment,
)
from landing_ledger.github import (
    GitHubReader,
    _gate_revision,
    current_rule_revision,
    read_pending_updates,
)
from landing_ledger.orchestrator_client import LedgerWriteError
from landing_ledger.rules import GATE_PATH
from tests.landing_ledger.test_audit import PATCH_AND_MINOR, UNDERSCORED
from tests.landing_ledger.test_github import REPO, reader_for

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
HEAD = "b" * 40

TRAILER = """chore(actions): bump astral-sh/setup-uv from 5 to 7

---
updated-dependencies:
- dependency-name: astral-sh/setup-uv
  update-type: version-update:semver-major
...
"""


def _routes(
    *,
    armed: bool = False,
    conclusion: str = "success",
    author: str = "dependabot[bot]",
    gate: str | None = UNDERSCORED,
) -> dict[str, Any]:
    routes: dict[str, Any] = {
        f"/repos/{REPO}": {"default_branch": "main"},
        f"/repos/{REPO}/pulls": [{"number": 31, "user": {"login": author}}],
        f"/repos/{REPO}/pulls/31": {
            "number": 31,
            "title": "chore(actions): bump astral-sh/setup-uv from 5 to 7",
            "created_at": "2026-07-31T20:48:38Z",
            "auto_merge": {"merge_method": "squash"} if armed else None,
            "head": {"sha": HEAD, "ref": "dependabot/github_actions/astral-sh/setup-uv-7"},
        },
        f"/repos/{REPO}/commits/{HEAD}": {"commit": {"message": TRAILER}},
        f"/repos/{REPO}/actions/runs": {
            "workflow_runs": [
                {"id": 1, "path": ".github/workflows/quality.yml", "event": "pull_request"},
                # The gate's own run. It says the gate EXECUTED; it never says the change is
                # sound, so it must not count towards green.
                {"id": 2, "path": GATE_PATH, "event": "pull_request"},
                # A `push` run of the same workflow, which would double every name.
                {"id": 3, "path": ".github/workflows/quality.yml", "event": "push"},
            ]
        },
        f"/repos/{REPO}/actions/runs/1/jobs": {
            "jobs": [
                {
                    "name": "Quality",
                    "conclusion": conclusion,
                    "completed_at": "2026-08-01T00:00:00Z",
                }
            ]
        },
        f"/repos/{REPO}/actions/runs/2/jobs": {
            "jobs": [
                {
                    "name": "Dependabot gate",
                    "conclusion": "success",
                    "completed_at": "2026-08-01T00:00:00Z",
                }
            ]
        },
        f"/repos/{REPO}/actions/runs/3/jobs": {
            "jobs": [
                {
                    "name": "Quality",
                    "conclusion": "success",
                    "completed_at": "2026-08-01T00:00:00Z",
                }
            ]
        },
    }
    if gate is not None:
        routes[f"/repos/{REPO}/contents/{GATE_PATH}"] = {"sha": gate}
    return routes


class _Ledger:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def read_landings(self, repository: str) -> list[dict[str, Any]]:
        return self.rows


class _Recorder:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(payload)
        return {}


# ---------------------------------------------------------------------------------------------
# What the GitHub half reads.
# ---------------------------------------------------------------------------------------------


def test_the_gates_own_run_is_not_evidence_that_the_change_is_sound() -> None:
    """Counting it would let a repository look green on the strength of the very workflow under
    audit -- the runner attesting to its own compliance, one level out."""
    pending = read_pending_updates(reader_for(_routes()), REPO, "main")

    assert [check.name for check in pending[0].checks] == ["Quality"]


def test_only_the_single_pull_request_read_can_see_whether_anything_is_armed() -> None:
    """`auto_merge` is null on every row of the LIST endpoint, exactly as `merged_by` is on the
    landing path. Reading it from the listing would report the whole estate as unarmed."""
    armed = read_pending_updates(reader_for(_routes(armed=True)), REPO, "main")
    unarmed = read_pending_updates(reader_for(_routes(armed=False)), REPO, "main")

    assert (armed[0].armed, unarmed[0].armed) == (True, False)


def test_a_pull_request_nobody_automated_is_not_this_detectors_subject() -> None:
    assert read_pending_updates(reader_for(_routes(author="AlobarQuest")), REPO, "main") == ()


def test_a_repository_with_no_gate_answers_none_rather_than_raising() -> None:
    """Three repositories in this estate deliberately have none, so this is a real answer."""
    assert current_rule_revision(reader_for(_routes(gate=None)), REPO, "main") is None
    assert current_rule_revision(reader_for(_routes()), REPO, "main") == UNDERSCORED


def test_a_landing_with_no_gate_at_its_commit_pins_NOTHING_rather_than_a_placeholder() -> None:
    """The landing path's own gate read, which had no test of the absent case. A fabricated
    revision would reach the ledger as a pinned rule, and the audit would then report it as
    untranscribed -- a finding manufactured by the recorder rather than found in reality."""
    assert _gate_revision(reader_for(_routes(gate=None)), REPO, "c" * 40) is None
    assert _gate_revision(reader_for(_routes()), REPO, "c" * 40) == UNDERSCORED


# ---------------------------------------------------------------------------------------------
# One pass over one repository.
# ---------------------------------------------------------------------------------------------


def _run(routes: dict[str, Any], ledger: _Ledger, writer: Any, dry_run: bool = False) -> Any:
    return audit_pass(
        reader_for(routes),
        ledger,
        writer,
        repository=REPO,
        pass_id="20260808T120000Z",
        now=NOW,
        settle_seconds=3600,
        dry_run=dry_run,
    )


def test_a_pass_files_one_row_and_finds_the_unarmed_eligible_update() -> None:
    recorder = _Recorder()

    audit, _ = _run(_routes(), _Ledger(), recorder)

    assert [finding.kind for finding in audit.findings] == [STALL_ELIGIBLE_NOT_ARMED]
    assert len(recorder.bodies) == 1
    assert recorder.bodies[0]["facts"]["findings_found"] == 1


def test_github_being_unreadable_makes_the_answer_MISSING_not_clean() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    recorder = _Recorder()
    reader = GitHubReader(token="fixture", transport=httpx.MockTransport(handler))

    audit, _ = audit_pass(
        reader,
        _Ledger(),
        recorder,
        repository=REPO,
        pass_id="20260808T120000Z",
        now=NOW,
        settle_seconds=3600,
        dry_run=False,
    )

    assert audit.unavailable is True
    assert audit.findings == ()
    # The heartbeat is still filed: a pass that could not measure must be distinguishable from a
    # pass that never ran, and only a row can carry that.
    assert recorder.bodies[0]["facts"]["unavailable"] is True


def test_a_pass_whose_own_row_cannot_be_filed_reports_itself_as_MISSING() -> None:
    """The measurement can be perfect and the answer still absent. Reporting the verdict as filed
    when the write was refused would make a broken orchestrator look like a clean estate."""

    class _Refuses:
        def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise LedgerWriteError("orchestrator rejected POST /api/v1/observations: 503")

    audit, _ = _run(_routes(), _Ledger(), _Refuses())

    assert audit.unavailable is True


def test_a_dry_run_writes_nothing_and_still_computes_the_row() -> None:
    class _Explodes:
        def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("a dry run must not record observations")

    audit, body = _run(_routes(), _Ledger(), _Explodes(), dry_run=True)

    assert body["observation_type"] == "landing_audit"
    assert audit.unavailable is False


def test_the_ledgers_rows_reach_the_drift_detector() -> None:
    """A pass that read the ledger and dropped it would report zero findings forever."""
    from tests.landing_ledger.test_audit import MAJOR, landing

    rows = [{"facts": landing(revision=PATCH_AND_MINOR, update_type=MAJOR)}]

    audit, _ = _run(_routes(gate=None), _Ledger(rows), _Recorder())

    assert [finding.kind for finding in audit.findings] == ["rule_not_satisfied"]
    assert audit.permitted_landings == 1


# ---------------------------------------------------------------------------------------------
# What a launcher reads: the exit code.
# ---------------------------------------------------------------------------------------------


def _invoke(args: list[str], env: dict[str, str]) -> Any:
    return CliRunner(env=env).invoke(app, args)


def test_the_audit_command_is_reachable_under_its_real_invocation() -> None:
    assert _invoke(["audit", "--help"], {}).exit_code == 0
    unnamed = _invoke(["--help"], {})
    assert "audit" in unnamed.output


def test_the_audit_refuses_to_run_without_the_credential_it_reads_the_ledger_with() -> None:
    """Unlike `record`, a dry-run audit still needs it: the ledger IS the thing being audited."""
    result = _invoke(
        ["audit", "--repository", REPO, "--dry-run"],
        {"LANDING_LEDGER_GITHUB_TOKEN": "x", "LANDING_LEDGER_TOKEN": ""},
    )

    assert result.exit_code == 1
    assert "LANDING_LEDGER_TOKEN is required" in result.output


def test_the_three_exit_codes_are_distinct() -> None:
    """A broken tool and an honest finding sharing one code is a collision this estate has
    already paid for once."""
    assert len({EXIT_OK, EXIT_FINDINGS, EXIT_INCOMPLETE}) == 3
    assert EXIT_OK == 0


def test_the_records_clock_is_the_PASS_not_the_wall(monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator's replay check compares the whole stored command, `observed_at` included.
    A wall-clock timestamp would make re-running a pass by its own id an `idempotency_conflict` --
    the same key, a different payload -- rather than the replay it obviously is. Measured here by
    running the same pass id at two different wall times and demanding one record."""
    routes = _routes(armed=True, conclusion="failure")

    def _at(wall: datetime) -> dict[str, Any]:
        # Asserted on the body the PASS returns, never on `audit_observation` called by hand: the
        # question is which clock the pass hands it, and a test that supplies the clock itself
        # answers a different question and would pass with the defect present.
        _, body = audit_pass(
            reader_for(routes),
            _Ledger(),
            _Recorder(),
            repository=REPO,
            pass_id="20260808T120000Z",
            now=wall,
            settle_seconds=3600,
            dry_run=True,
        )
        return body

    assert _at(NOW) == _at(NOW + timedelta(days=3))
    assert _at(NOW)["observed_at"] == pass_moment("20260808T120000Z").isoformat()
    assert _at(NOW)["observed_at"] == "2026-08-08T12:00:00+00:00"


def test_a_pass_id_that_is_not_a_moment_is_refused_at_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`audit_pass` promises never to raise, so a malformed identity is caught once, up front."""
    result = _drive(monkeypatch, ["audit", "--repository", REPO, "--pass-id", "yesterday"])

    assert result.exit_code == 1
    assert "pass id must be" in result.output


def test_could_not_measure_outranks_found_something() -> None:
    """An incomplete pass cannot claim it found everything there was to find, so when both are
    true the caller must be told the weaker thing."""
    assert _exit_code(findings=False, incomplete=False) == EXIT_OK
    assert _exit_code(findings=True, incomplete=False) == EXIT_FINDINGS
    assert _exit_code(findings=False, incomplete=True) == EXIT_INCOMPLETE
    assert _exit_code(findings=True, incomplete=True) == EXIT_INCOMPLETE


class _FakeClient:
    """Stands in for the orchestrator half so the command can be driven end to end."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, **_: Any) -> None:
        self.rows = rows or []
        self.bodies: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read_landings(self, repository: str) -> list[dict[str, Any]]:
        return self.rows

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(payload)
        return {}


def _drive(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    *,
    routes: dict[str, Any] | None = None,
    unreachable: bool = False,
    rows: list[dict[str, Any]] | None = None,
) -> Any:
    def _reader(**_: Any) -> GitHubReader:
        if unreachable:
            return GitHubReader(
                token="fixture",
                transport=httpx.MockTransport(lambda request: httpx.Response(503)),
            )
        return reader_for(routes if routes is not None else _routes())

    monkeypatch.setattr("landing_ledger.cli.GitHubReader", _reader)
    monkeypatch.setattr(
        "landing_ledger.cli.OrchestratorClient", lambda **kwargs: _FakeClient(rows, **kwargs)
    )
    env = {"LANDING_LEDGER_GITHUB_TOKEN": "x", "LANDING_LEDGER_TOKEN": "y"}
    return CliRunner(env=env).invoke(app, argv)


def test_a_clean_audit_pass_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """The healthy case, so the codes below are a discrimination rather than a constant."""
    routes = _routes(armed=True, conclusion="failure")

    assert _drive(monkeypatch, ["audit", "--repository", REPO], routes=routes).exit_code == EXIT_OK


def test_an_audit_pass_that_FOUND_something_tells_its_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _drive(monkeypatch, ["audit", "--repository", REPO])

    assert result.exit_code == EXIT_FINDINGS
    assert STALL_ELIGIBLE_NOT_ARMED in result.output


def test_an_audit_pass_that_could_not_MEASURE_tells_its_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _drive(monkeypatch, ["audit", "--repository", REPO], unreachable=True)

    assert result.exit_code == EXIT_INCOMPLETE


def test_a_complete_recording_pass_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = dict(_routes())
    routes[f"/repos/{REPO}/branches/main"] = {"commit": {"sha": "z" * 40}}
    routes[f"/repos/{REPO}/commits"] = []

    result = _drive(monkeypatch, ["record", "--repository", REPO], routes=routes)

    assert result.exit_code == EXIT_OK


def test_a_recording_pass_that_read_NOTHING_does_not_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generator-3 defect, in the very thing being scheduled: the per-repository flag was
    printed into an aggregate nobody had to look at, and the pass exited 0 over a window it had
    not recorded."""
    result = _drive(monkeypatch, ["record", "--repository", REPO], unreachable=True)

    assert '"unavailable": true' in result.output
    assert result.exit_code == EXIT_INCOMPLETE
