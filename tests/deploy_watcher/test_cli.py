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

import inspect

import httpx
import pytest
from typer.testing import CliRunner

from deploy_watcher import cli as watcher_cli
from deploy_watcher.change_manager import ChangeManagerClient
from deploy_watcher.model import ChangeRecord
from deploy_watcher.orchestrator import OrchestratorClient
from deploy_watcher.units import UNIT_CLAIM_UNBOUND, UNIT_CLAIM_UNKNOWN
from tests.deploy_watcher.test_observe import MERGE, NOW, REPO, reader_for, routes

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


def _github(message: str | None):
    """The observe harness, plus the one read ADR-0022 added. `None` = GitHub has no such commit."""
    extra = {} if message is None else {_COMMIT: {"commit": {"message": message}}}
    return reader_for(routes(**extra))


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
) -> tuple[tuple[bool, bool], list[bytes]]:
    posted: list[bytes] = []
    with (
        _github(message) as reader,
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
