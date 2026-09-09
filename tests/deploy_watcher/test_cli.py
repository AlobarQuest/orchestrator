"""The watcher's composition: what one pass does with one change record.

ADR-0022 gave `_watch_one` two new obligations and both are easy to get wrong quietly. The
unit-scoped observation must be written ONLY where the orchestrator's own record binds the landing
to the unit — a commit trailer is written by the party whose compliance it describes. And a record
that is CLOSED while its latest rollout did not succeed must reach a person, because nothing
un-settles a record and the contradiction would otherwise sit in the database saying nothing.

**These drive the real `_watch_one`, through the real `observe`, with no test-only parameters.**
The reporting surface and the acting surface are different tests in this estate, and the second is
the one that writes.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import httpx
import pytest
from typer.testing import CliRunner

from deploy_watcher import cli as watcher_cli
from deploy_watcher import observe as observe_module
from deploy_watcher import units as units_module
from deploy_watcher.change_manager import ChangeManagerClient
from deploy_watcher.model import ChangeRecord, Finding
from deploy_watcher.orchestrator import OrchestratorClient
from deploy_watcher.units import UNIT_CLAIM_UNBOUND, UNIT_CLAIM_UNKNOWN
from tests.deploy_watcher.test_observe import (
    _SUCCESS_QUERY,
    LATER_HEAD,
    LATER_RUN,
    MERGE,
    NOW,
    REPO,
    failed_rollout,
    reader_for,
    routes,
    supersession,
)

UNIT = "1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"
RECORD = ChangeRecord(
    item_id=52,
    identity="deploy::alobarquest/change-manager::46",
    target_repository=REPO,
    pull_request_number=46,
    acceptance_criteria=(),
)

RECORDED = {
    "verdict": "success",
    "production_reached": "yes",
    "workflow_attestation": "revision_confirmed",
    "item_status": "resolved",
}

_COMMIT = f"/repos/{REPO}/commits/{MERGE}"
# A landing with no claim on it: the ordinary shape, and the default for every case below that is
# about something other than the trailer.
NO_TRAILER = "bump alembic from 1.18.5 to 1.19.1 (#46)\n"


def _github(message: str | None, github_routes: dict[str, object] | None = None):
    """The observe harness, plus the one read ADR-0022 added. `None` = GitHub has no such commit.

    `github_routes` replaces the whole route table, so an ADR-0044 case can present a rollout that
    FAILED -- which the default table cannot, being a healthy landing.
    """
    extra = {} if message is None else {_COMMIT: {"commit": {"message": message}}}
    base = routes(**extra) if github_routes is None else {**github_routes, **extra}
    return reader_for(base)


def _orchestrator(history: object, posted: list[bytes], *, history_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(request.read())
            return httpx.Response(201, json={"id": "row"})
        return httpx.Response(history_status, json=history)

    return OrchestratorClient("t", transport=httpx.MockTransport(handler))


def _changes(page: dict, recorded: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=recorded)
        return httpx.Response(200, json=page)

    return ChangeManagerClient("t", transport=httpx.MockTransport(handler))


def _page(verdict: str = "success", commits: list[str] | None = None) -> dict:
    return {
        "observations": [{}],
        "merge_commits_observed": commits if commits is not None else [MERGE],
        "current": {"verdict": verdict, "run_id": 31426195637, "run_attempt": 1},
    }


def _bound_history(**overrides) -> list[dict]:
    payload = {
        "status": "merged",
        "repository": REPO.lower(),
        "pr_number": 46,
        "merge_commit_sha": MERGE,
    }
    payload.update(overrides)
    return [{"action": "pr_merge.recorded", "payload": payload}]


def _watch(
    *,
    message: str | None = NO_TRAILER,
    history: object = None,
    history_status: int = 200,
    page: dict | None = None,
    recorded: dict | None = None,
    github_routes: dict[str, object] | None = None,
) -> tuple[tuple[bool, bool], list[bytes]]:
    posted: list[bytes] = []
    with (
        _github(message, github_routes) as reader,
        _changes(page or _page(), recorded or RECORDED) as changes,
        _orchestrator(
            _bound_history() if history is None else history,
            posted,
            history_status=history_status,
        ) as units,
    ):
        answer = watcher_cli._watch_one(
            reader,
            changes,
            units,
            RECORD,
            now=NOW,
            actor="deploy-watcher",
            settle_seconds=1800,
            dry_run=False,
        )
    return answer, posted


# ---------------------------------------------------------------------------
# The unit-scoped observation
# ---------------------------------------------------------------------------


def test_a_bound_claim_is_observed_against_the_unit() -> None:
    """Phase-3's traceability hop filters on `subject_type="work_unit"`, so this is the row."""
    answer, posted = _watch(message=f"bump (#46)\n\nSDS-Unit: {UNIT}\n")
    assert answer == (False, False)
    assert len(posted) == 1
    assert UNIT.encode() in posted[0]
    assert b'"subject_type":"work_unit"' in posted[0].replace(b", ", b",")


def test_no_claim_writes_nothing_and_is_not_a_finding() -> None:
    """The ORDINARY case: almost every landing the watcher sees is an update the bot opened, and
    whether a trailer survives a squash is a repository setting."""
    answer, posted = _watch(message=NO_TRAILER)
    assert answer == (False, False)
    assert posted == []


def test_a_claim_the_orchestrator_does_not_hold_is_a_finding_and_writes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    answer, posted = _watch(
        message=f"bump (#46)\n\nSDS-Unit: {UNIT}\n",
        history={"error": {"code": "work_unit_not_found", "message": "no"}},
        history_status=404,
    )
    assert answer == (True, False)
    assert posted == []
    # THE FINDING'S TEXT, not only the exit code. A mutation that kept the `True` and dropped the
    # `_report` survived the first version of this test: the pass would have said "something was
    # found" and named nothing, which is a report nobody can act on.
    assert UNIT_CLAIM_UNKNOWN in capsys.readouterr().out


def test_a_404_that_is_not_the_orchestrators_is_INCOMPLETE_rather_than_a_finding() -> None:
    """A route the deployed image does not serve answers FastAPI's bare `{"detail": …}`. Reading
    every 404 as absence would accuse the orchestrator of losing every unit at once — and this
    estate HAS served a release whose routes production did not carry."""
    answer, posted = _watch(
        message=f"bump (#46)\n\nSDS-Unit: {UNIT}\n",
        history={"detail": "Not Found"},
        history_status=404,
    )
    assert answer == (False, True)
    assert posted == []


@pytest.mark.parametrize(
    "overrides",
    [
        # The orchestrator did not assert it MADE this landing: `already_merged` also covers a
        # pull request somebody else had landed before the merge call.
        {"status": "already_merged"},
        {"status": "refused"},
        # This unit landed a different pull request...
        {"pr_number": 45},
        # ...or a different commit.
        {"merge_commit_sha": "0" * 40},
        {"repository": "alobarquest/brain"},
    ],
)
def test_an_unbound_claim_is_a_finding_and_writes_nothing(
    overrides: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    answer, posted = _watch(
        message=f"bump (#46)\n\nSDS-Unit: {UNIT}\n", history=_bound_history(**overrides)
    )
    assert answer == (True, False)
    assert posted == []
    assert UNIT_CLAIM_UNBOUND in capsys.readouterr().out


def test_an_unreadable_commit_is_incomplete_never_a_silent_skip() -> None:
    """GitHub having no such commit is a statement about the QUESTION. Reading it as "no trailer"
    would skip the unit observation forever with the pass reporting success."""
    answer, posted = _watch(message=None)
    assert answer == (False, True)
    assert posted == []


def test_a_REFUSED_WRITE_is_incomplete_and_never_a_finding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The write half, which the read-failure case below does NOT reach.

    A mutation swallowing this `except` — or narrowing the client's own status check to 5xx —
    survived every other test in this file: the read succeeds, the POST 4xx's, and the pass would
    have reported a clean run having recorded nothing.
    """
    posted: list[bytes] = []

    def refuse_writes(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(request.read())
            return httpx.Response(409, json={"error": {"code": "observation_conflict"}})
        return httpx.Response(200, json=_bound_history())

    with (
        _github(f"bump (#46)\n\nSDS-Unit: {UNIT}\n") as reader,
        _changes(_page(), RECORDED) as changes,
        OrchestratorClient("t", transport=httpx.MockTransport(refuse_writes)) as units,
    ):
        answer = watcher_cli._watch_one(
            reader,
            changes,
            units,
            RECORD,
            now=NOW,
            actor="deploy-watcher",
            settle_seconds=1800,
            dry_run=False,
        )
    assert answer == (False, True), "a refused write is unmeasured, not a rollout finding"
    assert len(posted) == 1, "the write was attempted"
    assert "409" in capsys.readouterr().out


def test_an_unreachable_orchestrator_is_incomplete_never_a_finding() -> None:
    posted: list[bytes] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    with (
        _github(f"bump (#46)\n\nSDS-Unit: {UNIT}\n") as reader,
        _changes(_page(), RECORDED) as changes,
        OrchestratorClient("t", transport=httpx.MockTransport(refuse)) as units,
    ):
        answer = watcher_cli._watch_one(
            reader,
            changes,
            units,
            RECORD,
            now=NOW,
            actor="deploy-watcher",
            settle_seconds=1800,
            dry_run=False,
        )
    assert answer == (False, True)
    assert posted == []


# ---------------------------------------------------------------------------
# The closed-record contradiction
# ---------------------------------------------------------------------------


def test_a_closed_record_whose_rollout_failed_is_a_finding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing un-settles a record — reopening is a decision — so the contradiction has to reach a
    person some other way, and this is it."""
    answer, _ = _watch(
        page=_page(verdict="failed"), recorded={**RECORDED, "item_status": "resolved"}
    )
    assert answer == (True, False)
    assert watcher_cli.SETTLED_ROLLOUT_NOT_SUCCESS in capsys.readouterr().out


def test_a_closed_record_whose_rollout_succeeded_is_not_a_finding() -> None:
    """The control. Without it the test above passes on a check that fires for EVERY closed
    record, which is every record the watcher ever settles."""
    answer, _ = _watch(recorded={**RECORDED, "item_status": "resolved"})
    assert answer == (False, False)


def test_an_open_record_whose_rollout_failed_is_not_THIS_finding() -> None:
    """A failing rollout on a record still open is `rollout_did_not_succeed`, which `observe`
    already reports. This finding is specifically about a record that has been CLOSED anyway."""
    answer, _ = _watch(
        page=_page(verdict="failed"), recorded={**RECORDED, "item_status": "pending"}
    )
    assert answer == (False, False)


def test_a_merge_divergence_still_reports_before_the_contradiction_check() -> None:
    """Two merge commits mean the rows disagree about which landing they describe, and the server
    answers `current: None` — which must not read as "no contradiction" OR crash the check."""
    answer, _ = _watch(page={**_page(commits=[MERGE, "0" * 40]), "current": None})
    assert answer == (True, False)


# ---------------------------------------------------------------------------
# The credential
# ---------------------------------------------------------------------------


def test_the_orchestrator_credential_is_REQUIRED(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scope that exists with no value does not fail — it falls back to doing nothing while the
    pass reports success. This estate shipped that twice in one increment."""
    monkeypatch.setenv("DEPLOY_WATCHER_GITHUB_TOKEN", "g")
    monkeypatch.setenv("DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN", "c")
    monkeypatch.delenv("DEPLOY_WATCHER_ORCHESTRATOR_TOKEN", raising=False)
    result = CliRunner().invoke(watcher_cli.app, ["watch"])
    assert result.exit_code == watcher_cli.EXIT_BROKEN
    assert "DEPLOY_WATCHER_ORCHESTRATOR_TOKEN" in result.output


def test_the_recheck_pass_does_NOT_need_it() -> None:
    """The control on the line above: `recheck` re-derives stored observations from GitHub and
    speaks to the orchestrator not at all, so requiring the credential there would be ceremony.

    Asserted over the FUNCTION rather than by invoking it, because invoking it does not
    discriminate: `recheck` exits at its change-manager `_require` on the second line, so a
    requirement added anywhere after that survives a run-and-read-the-output test. Reviewed and
    replaced for exactly that reason.
    """
    assert "ORCHESTRATOR_TOKEN_VAR" not in inspect.getsource(watcher_cli.recheck)
    assert "ORCHESTRATOR_TOKEN_VAR" in inspect.getsource(watcher_cli.watch)


# ---------------------------------------------------------------------------
# The re-check — the estate's only defence against an ASSERTED observation
# ---------------------------------------------------------------------------

REVISION = "a47d4b187c93971a5b5915ce87a963bd4ef35e30"


def _stored(**overrides) -> dict:
    """A recorded observation, as change-manager serves it back."""
    row = {
        "run_id": 31426195637,
        "run_attempt": 1,
        "run_conclusion": "success",
        "rollout_job": "build-and-deploy",
        "rollout_job_conclusion": "success",
        "trigger_step": "Trigger Coolify redeploy",
        "trigger_step_conclusion": "success",
        "workflow_path": ".github/workflows/deploy.yml",
        "workflow_revision": REVISION,
        "workflow_attestation": "revision_confirmed",
        "merge_commit_sha": MERGE,
    }
    row.update(overrides)
    return row


def _recheck(stored: dict) -> tuple[tuple[bool, bool], str]:
    with reader_for(routes()) as reader:
        answer = watcher_cli._recheck_one(reader, REPO, 52, stored)
    return answer, ""


def test_a_row_that_still_matches_github_is_not_a_divergence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _recheck(_stored())[0] == (False, False)
    assert watcher_cli.RECHECK_DIVERGENCE not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_conclusion", "failure"),
        ("rollout_job_conclusion", "skipped"),
        ("trigger_step_conclusion", "failure"),
        ("workflow_revision", "0" * 40),
        # ADR-0022. THE FIELD A SETTLEMENT RESTS ON, and it was absent from the comparison while
        # two modules justified an `observe` credential moving a status on the grounds that this
        # command re-derives it. Caller-supplied, unvalidated, and it also decides `classified`.
        ("workflow_attestation", "rollout_unverified"),
    ],
)
def test_a_stored_fact_that_no_longer_matches_github_is_reported(
    field: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _recheck(_stored(**{field: value}))[0] == (True, False)
    assert watcher_cli.RECHECK_DIVERGENCE in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ADR-0044: the closed-record contradiction has an EXCEPTION twin
# ---------------------------------------------------------------------------


def _superseded_routes() -> dict[str, object]:
    """A failed rollout that a later, revision-confirming run has moved production past."""
    return failed_rollout(**supersession())


def test_a_closed_record_whose_failed_rollout_was_SUPERSEDED_is_an_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole point of ADR-0044 at this surface.

    `SETTLED_ROLLOUT_NOT_SUCCESS` is LATENT on today's population -- records 78 and 79 are
    `approved`, so it has never fired -- and becomes live the moment a person resolves one. Without
    this branch that natural act converts a rollout nobody can act on into a permanently-red
    finding, which is the shape the estate has now ruled against three times.
    """
    answer, _ = _watch(
        github_routes=_superseded_routes(),
        page=_page(verdict="failed"),
        recorded={**RECORDED, "item_status": "resolved"},
    )
    out = capsys.readouterr().out

    assert answer == (False, False)  # NOT found: an exception must not drive exit 2
    assert watcher_cli.SETTLED_SUPERSEDED_ROLLOUT in out
    assert watcher_cli.SETTLED_ROLLOUT_NOT_SUCCESS not in out
    assert "[exception]" in out


def test_the_SAME_closed_record_is_a_FINDING_when_nothing_superseded_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The discriminating half of the pair above: one fixture, one variable.

    Identical in every respect except that the branch's newest successful run is absent, so the
    excuse cannot be found. If this stopped reporting, the exception would have become a blanket
    suppression of the contradiction rather than a reason for one instance of it.
    """
    answer, _ = _watch(
        github_routes=failed_rollout(**{_SUCCESS_QUERY: {"total_count": 0, "workflow_runs": []}}),
        page=_page(verdict="failed"),
        recorded={**RECORDED, "item_status": "resolved"},
    )
    out = capsys.readouterr().out

    assert answer == (True, False)
    assert watcher_cli.SETTLED_ROLLOUT_NOT_SUCCESS in out
    assert watcher_cli.SETTLED_SUPERSEDED_ROLLOUT not in out


def test_the_settled_exception_cites_the_SAME_superseding_run_as_the_rollout_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The excuse is PASSED from `observe`, never recomputed here.

    A second copy of the four-clause predicate is drift this estate has paid for repeatedly, and
    the observable consequence of one copy is that both reports name the same run -- so a reader
    disputing one disputes both.
    """
    _watch(
        github_routes=_superseded_routes(),
        page=_page(verdict="failed"),
        recorded={**RECORDED, "item_status": "resolved"},
    )
    lines = [line for line in capsys.readouterr().out.splitlines() if "[exception]" in line]

    assert len(lines) == 2
    assert all(str(LATER_RUN) in line and LATER_HEAD[:8] in line for line in lines)


def test_an_exception_alone_never_drives_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    """An OPEN record whose failed rollout was superseded: one exception, no finding, exit 0.

    The rollout exception fires and the ledger one does not (the record is not closed), so this is
    the narrowest case in which the category has to hold on its own.
    """
    answer, _ = _watch(
        github_routes=_superseded_routes(),
        page=_page(verdict="failed"),
        recorded={**RECORDED, "item_status": "pending"},
    )
    out = capsys.readouterr().out

    assert answer == (False, False)
    assert watcher_cli._exit_code(findings=False, incomplete=False) == watcher_cli.EXIT_OK
    assert out.count("[exception]") == 1
    assert "[found]" not in out


def test_the_exception_marker_is_distinct_from_the_finding_marker() -> None:
    """A reader scanning for `[found]` must not see an exception, and an auditor must find them.

    A quieter `[found]` would make the two indistinguishable to every grep an operator writes.
    """
    excused = Finding("some_kind", "subject", "detail")
    assert "[found]" not in f"  [exception] {excused.kind}: {excused.subject} — {excused.detail}"


def _reported_kinds() -> dict[str, str]:
    """Every kind this program can put in a report, collected from the REPORTING call itself.

    AST-scanned for `Finding(<kind>, ...)` and for the `[found]`/`[exception]` format strings
    rather than filtered out of module constants by name, because the two predicates differ in the
    case that actually occurred: `change_manager_refused_the_observation` was an INLINE LITERAL in
    `_watch_one` until this change, so a scan of upper-case constants could not see it, and a guard
    that cannot see a member is not a guard. This resolves a constant to its value and takes a
    literal verbatim, so both shapes are collected.
    """
    found: dict[str, str] = {}
    for module in (observe_module, watcher_cli, units_module):
        source = ast.parse(pathlib.Path(inspect.getfile(module)).read_text())

        def resolve(node: ast.expr, module: object = module) -> str | None:
            """A constant's VALUE, resolved through the module's own namespace.

            `getattr` rather than a scan of local assignments, because `UNIT_CLAIM_UNKNOWN` is
            defined in `units` and REPORTED from `cli` -- so a collector that read only local
            assignments would miss every kind one module imports from another, which is two of
            them here.
            """
            if isinstance(node, ast.Name):
                value = getattr(module, node.id, None)
                return value if isinstance(value, str) else None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            return None

        for node in ast.walk(source):
            kind: str | None = None
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Finding"
                and node.args
            ):
                kind = resolve(node.args[0])
            elif isinstance(node, ast.JoinedStr) and "[found]" in ast.unparse(node):
                # The one reported kind that reaches the log without a `Finding`: `_watch_one`
                # prints change-manager's refusal directly.
                for part in node.values:
                    if isinstance(part, ast.FormattedValue):
                        kind = resolve(part.value) or kind
            if kind:
                found[kind] = getattr(module, "__name__", "?")
    return found


def test_the_kind_collector_sees_the_kinds_this_change_added() -> None:
    """Pins the scanner to reality, so the substring guard below cannot pass by seeing nothing.

    A collector that silently matched zero call sites would make its sibling vacuously green --
    which is how a mutation set comes to report a pass it did not earn.
    """
    kinds = _reported_kinds()

    assert observe_module.SUPERSEDED_ROLLOUT in kinds
    assert watcher_cli.SETTLED_SUPERSEDED_ROLLOUT in kinds
    assert watcher_cli.SETTLED_ROLLOUT_NOT_SUCCESS in kinds
    assert watcher_cli.CHANGE_MANAGER_REFUSED in kinds, "the inline literal must be collected"
    assert units_module.UNIT_CLAIM_UNKNOWN in kinds
    assert len(kinds) >= 12


def test_no_reported_kind_is_a_substring_of_another() -> None:
    """THE REPORT IS READ BY SUBSTRING, so a kind that CONTAINS another is a broken discriminator.

    The tests above prove the exception is not a suppression with a PAIR of assertions over one
    captured report -- one kind ABSENT on the first pass, PRESENT on the second -- and an operator
    greps the log the same way. A kind containing another satisfies the "present" half on its own
    name and breaks the "absent" half for a reason that has nothing to do with what it tests. This
    is why ADR-0044's kinds are NOT spelled `rollout_did_not_succeed_but_superseded`.

    ONE PAIR IS EXEMPT AND IT IS PRE-EXISTING DEBT, NOT A JUSTIFICATION.
    `rollout_did_not_succeed` (`observe`) is contained in
    `a_closed_record_whose_latest_rollout_did_not_succeed` (`cli`) -- found BY this control on its
    first run, and older than this change. Renaming a reported kind changes what an operator greps
    for, so it is outside this change's subject and is reported rather than done. Any OTHER
    collision, including a new one involving either of these two, still reds.
    """
    known_debt = (
        observe_module.ROLLOUT_NOT_SUCCESS,
        watcher_cli.SETTLED_ROLLOUT_NOT_SUCCESS,
    )
    kinds = sorted(_reported_kinds())
    collisions = sorted(
        (kind, other) for kind in kinds for other in kinds if kind != other and kind in other
    )

    assert collisions == [known_debt]
