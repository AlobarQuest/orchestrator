"""The audit pass end to end: what it reads from GitHub, and what a launcher learns from it.

The exit codes are the point of most of this. A launcher is the only consumer a scheduled pass
has, and a launcher reads one number.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from landing_ledger.audit import (
    BRANCH_NOT_GREEN,
    EXCEPTION_UPDATE_TYPE_UNPARSEABLE,
    STALL_ELIGIBLE_NOT_ARMED,
    STALL_METADATA_UNREADABLE,
)
from landing_ledger.audit import branch_status as branch_status
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
    workflow_runs_at,
)
from landing_ledger.orchestrator_client import LedgerWriteError
from landing_ledger.rules import GATE_PATH
from tests.landing_ledger.test_audit import (
    PATCH_AND_MINOR,
    UNDERSCORED,
    factory_landing,
    history,
    pack,
)
from tests.landing_ledger.test_audit import (
    UNIT as FACTORY_UNIT,
)
from tests.landing_ledger.test_github import REPO, reader_for

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
HEAD = "b" * 40
TIP = "d" * 40

# The 2026-08-10 factory landing, as `test_audit` shapes it from production. Reused rather than
# restated so the pass-level test and the detector-level tests cannot drift into two landings.
FACTORY_LANDING = factory_landing(repository=REPO)
FACTORY_PACK = pack()
FACTORY_HISTORY = history(repository=REPO)

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
    title: str = "chore(actions): bump astral-sh/setup-uv from 5 to 7",
    message: str = TRAILER,
    tip_status: str = "completed",
    tip_conclusion: str | None = "success",
    tip: bool = True,
    gate_at_tip: bool = False,
) -> dict[str, Any]:
    routes: dict[str, Any] = {
        f"/repos/{REPO}": {"default_branch": "main"},
        f"/repos/{REPO}/pulls": [{"number": 31, "user": {"login": author}}],
        f"/repos/{REPO}/pulls/31": {
            "number": 31,
            "title": title,
            "created_at": "2026-07-31T20:48:38Z",
            "auto_merge": {"merge_method": "squash"} if armed else None,
            "head": {"sha": HEAD, "ref": "dependabot/github_actions/astral-sh/setup-uv-7"},
        },
        f"/repos/{REPO}/commits/{HEAD}": {"commit": {"message": message}},
        f"/repos/{REPO}/actions/runs": {
            "workflow_runs": [
                # THESE TWO CARRY A FULL RUN SHAPE, and that is what makes the exclusions
                # testable. Without `status`/`conclusion`/`updated_at` they are dropped by
                # `workflow_runs_at`'s structural filter instead, so a test asserting the branch
                # read skips them passes whether or not the event and path exclusions exist --
                # measured by mutation, which survived both until these fields were added.
                {
                    "id": 1,
                    "path": ".github/workflows/quality.yml",
                    "event": "pull_request",
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": "2026-08-01T00:00:00Z",
                },
                # The gate's own run. It says the gate EXECUTED; it never says the change is
                # sound, so it must not count towards green.
                {
                    "id": 2,
                    "path": GATE_PATH,
                    "event": "pull_request",
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": "2026-08-01T00:00:00Z",
                },
                # A `push` run of the same workflow, which would double every name here and is
                # detector C's whole subject one route over -- the mock matches on path alone, so
                # this listing answers both `head_sha=<pull request head>` and `head_sha=<tip>`.
                {
                    "id": 3,
                    "path": ".github/workflows/quality.yml",
                    "event": "push",
                    "status": tip_status,
                    "conclusion": tip_conclusion,
                    "updated_at": "2026-08-01T00:00:00Z",
                },
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
    if gate_at_tip:
        # A GATE RUN THAT IS NOT A PULL-REQUEST RUN. The two exclusions in `workflow_runs_at`
        # overlap on every run in the default fixture -- the gate's own run is a `pull_request`
        # run there, so it is excluded twice over and removing the path exclusion changes
        # nothing. Measured by mutation, which survived until this case existed. `failure` rather
        # than `success` so the difference is loud: without the path exclusion the branch reads
        # red on the strength of the very workflow under audit.
        routes[f"/repos/{REPO}/actions/runs"]["workflow_runs"].append(
            {
                "id": 4,
                "path": GATE_PATH,
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
                "updated_at": "2026-08-02T00:00:00Z",
            }
        )
    if gate is not None:
        routes[f"/repos/{REPO}/contents/{GATE_PATH}"] = {"sha": gate}
    if tip:
        routes[f"/repos/{REPO}/branches/main"] = {"commit": {"sha": TIP}}
    return routes


class _Ledger:
    """The orchestrator as `audit_pass` sees it: the recorded landings, plus the two per-unit
    reads the factory half of detector A needs. One credential, three read paths."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        packs: dict[str, dict[str, Any]] | None = None,
        histories: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.rows = rows or []
        self.packs = packs or {}
        self.histories = histories or {}

    def read_landings(self, repository: str) -> list[dict[str, Any]]:
        return self.rows

    def read_evidence_pack(self, work_unit_id: str) -> dict[str, Any] | None:
        return self.packs.get(work_unit_id)

    def read_unit_history(self, work_unit_id: str) -> list[dict[str, Any]] | None:
        return self.histories.get(work_unit_id)


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


class _UnreachableOrchestrator(_Ledger):
    """It answers the ledger read and then cannot answer the unit reads -- which is the shape a
    partial outage takes, and the one where a swallowed error would look like a clean estate."""

    def read_evidence_pack(self, work_unit_id: str) -> dict[str, Any] | None:
        raise LedgerWriteError("orchestrator is unreachable for GET: ConnectError")


def test_an_orchestrator_that_cannot_answer_the_factory_check_makes_the_pass_INCOMPLETE() -> None:
    """A landing recorded as factory-permitted whose claim could not be resolved is a repository
    whose answer is missing, not one that was found clean. It reaches the incomplete exit code,
    which outranks findings, and the heartbeat row still says so."""
    recorder = _Recorder()
    ledger = _UnreachableOrchestrator([{"facts": FACTORY_LANDING}])

    audit, body = _run(_routes(), ledger, recorder)

    assert audit.unavailable
    assert body["facts"]["unavailable"] is True
    assert "[UNAVAILABLE]" in body["summary"]
    # And everything the pass measured WITHOUT asking the orchestrator survives. Letting the
    # caller's blanket catch take the repository would discard the rule and stall findings too --
    # findings that need no orchestrator at all -- so one unreadable landing would blank three
    # detectors instead of one.
    assert [finding.kind for finding in audit.findings] == [STALL_ELIGIBLE_NOT_ARMED]
    assert audit.landings_audited == 1


def test_a_factory_landing_the_orchestrator_confirms_is_recorded_as_audited_and_clean() -> None:
    """The whole path, through the same client surface the launcher uses: the ledger's recorded
    landing selects a unit, the orchestrator's own records answer for it, and the repository's
    heartbeat carries the factory denominator so `no findings` is never bare."""
    recorder = _Recorder()
    ledger = _Ledger(
        [{"facts": FACTORY_LANDING}],
        packs={FACTORY_UNIT: FACTORY_PACK},
        histories={FACTORY_UNIT: FACTORY_HISTORY},
    )

    audit, body = _run(_routes(), ledger, recorder)

    assert [finding.kind for finding in audit.findings] == [STALL_ELIGIBLE_NOT_ARMED]
    assert not audit.unavailable
    assert body["facts"]["factory_landings"] == 1


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


def test_a_pass_whose_only_condition_is_an_EXCEPTION_exits_ZERO_and_still_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this increment closes, at the surface the launcher reads.

    A requirement range with no metadata trailer -- the shape seven live subjects had on
    2026-08-23 -- made this pass exit 2 every night on a condition no pass will ever clear. It is
    quiet in the exit code and loud in the report, which is the whole point of the category:
    exiting non-zero forever is how a control stops being read, and suppressing it entirely is
    how a fact gets lost.
    """
    routes = _routes(
        title="chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
        message="chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
    )

    result = _drive(monkeypatch, ["audit", "--repository", REPO], routes=routes)

    assert result.exit_code == EXIT_OK
    assert EXCEPTION_UPDATE_TYPE_UNPARSEABLE in result.output
    assert STALL_METADATA_UNREADABLE not in result.output


def test_a_pass_whose_metadata_is_missing_from_a_CLASSIFIABLE_title_still_exits_TWO(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The discriminator at the same surface. Without it the change above is a suppression."""
    routes = _routes(message="chore(actions): bump astral-sh/setup-uv from 5 to 7")

    result = _drive(monkeypatch, ["audit", "--repository", REPO], routes=routes)

    assert result.exit_code == EXIT_FINDINGS
    assert STALL_METADATA_UNREADABLE in result.output


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


# ---------------------------------------------------------------------------------------------
# Detector C at the surface a launcher reads: a red default branch is a finding, an undecided one
# is not, and a branch nobody could ask about is missing rather than clean.
# ---------------------------------------------------------------------------------------------


def test_the_branch_read_skips_the_pull_request_runs_and_the_gates_own_run() -> None:
    """A pull-request run is a verdict about a PROPOSAL, and the gate's run says only that the
    gate executed. Neither is evidence about the branch."""
    runs = workflow_runs_at(reader_for(_routes()), REPO, TIP)

    assert [(run.run, run.path, run.conclusion) for run in runs] == [
        (3, ".github/workflows/quality.yml", "success")
    ]


def test_the_GATE_is_not_evidence_about_the_branch_even_when_it_ran_on_a_push() -> None:
    """The path exclusion on its own, with the event exclusion unable to cover for it.

    The gate's run says the gate EXECUTED and never that the change is sound, so counting it
    would let a repository's health rest on the very workflow under audit -- and here it would
    report the branch RED on that basis.
    """
    reader = reader_for(_routes(gate_at_tip=True))
    runs = workflow_runs_at(reader, REPO, TIP)

    assert [run.path for run in runs] == [".github/workflows/quality.yml"]
    assert branch_status(TIP, runs).state == "passing"


def test_the_branch_read_carries_an_UNFINISHED_run_rather_than_dropping_it() -> None:
    """`status` and `conclusion` both reach the judge verbatim. Dropping a run with no conclusion
    here would erase the difference between in-flight and unverified before anything could read
    it, which is the distinction the whole detector is."""
    runs = workflow_runs_at(
        reader_for(_routes(tip_status="in_progress", tip_conclusion=None)), REPO, TIP
    )

    assert [(run.status, run.conclusion) for run in runs] == [("in_progress", None)]


def test_a_pass_over_a_RED_default_branch_exits_with_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _drive(
        monkeypatch, ["audit", "--repository", REPO], routes=_routes(tip_conclusion="failure")
    )

    assert result.exit_code == EXIT_FINDINGS
    assert BRANCH_NOT_GREEN in result.output


def test_a_pass_over_an_IN_FLIGHT_default_branch_does_NOT_report_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The discriminator for the test above. Without it the detector could be keyed on "not
    green" rather than on "decided against", and would red this control every night -- an
    undecided tip is the ordinary state under the current arming identity."""
    result = _drive(
        monkeypatch,
        ["audit", "--repository", REPO],
        routes=_routes(tip_status="in_progress", tip_conclusion=None),
    )

    assert BRANCH_NOT_GREEN not in result.output
    assert '"state": "in_flight"' in result.output


def test_a_branch_that_could_not_be_read_is_INCOMPLETE_and_keeps_what_was_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nested catch, at the surface. Only the branch read fails here -- the tip is missing
    while every other route answers -- so the pass must reach the incomplete code without
    discarding the open-update finding it had already computed."""
    result = _drive(monkeypatch, ["audit", "--repository", REPO], routes=_routes(tip=False))

    assert result.exit_code == EXIT_INCOMPLETE
    assert '"branch": null' in result.output
    assert STALL_ELIGIBLE_NOT_ARMED in result.output
