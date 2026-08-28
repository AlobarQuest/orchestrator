"""The work lane's watcher (ADR-0029): an approved record whose work is built is retired.

**THE RETIREMENT CALL ITSELF IS WHAT THESE TESTS ASSERT, not the predicate behind it.** Twice in
one day this estate shipped a change fully tested in the module that COMPUTES a value and untested
in the module that CONSUMES it, and the mutation reverting the consumer passed the whole suite.
So every case here records what the watcher actually sent — or that it sent nothing — rather than
what it decided.

The completion rule itself lives in the orchestrator and is tested against a real database in
`tests/services/test_change_record_work.py`. Here the verdict is a value the watcher is HANDED,
which is exactly the shape of the production dependency: this program relays and does not reduce.
"""

from __future__ import annotations

import io

import pytest

from work_carrier.change_manager import ChangeManagerError, WorkRecord
from work_watcher.change_manager import RetirementRefused
from work_watcher.cli import EXIT_FINDINGS, EXIT_OK, EXIT_TOOL_FAILURE, EXIT_UNUSABLE, run
from work_watcher.orchestrator_client import OrchestratorError, WorkCompletion


def record(**overrides) -> WorkRecord:
    base = {
        "change_record_id": 61,
        "package_id": "infraops-mcp-server-npm-eslint",
        "package_revision": 1,
        "package_source_repository": "AlobarQuest/intent-packages",
        "reasoning": "the human approved building it",
        "decided_by": "devon",
    }
    return WorkRecord(**{**base, **overrides})


class Source:
    def __init__(self, *records: WorkRecord, error: Exception | None = None) -> None:
        self._records = records
        self._error = error

    def approved_work(self) -> tuple[WorkRecord, ...]:
        if self._error is not None:
            raise self._error
        return self._records


class Reader:
    """The orchestrator, answering the completion question it derives."""

    def __init__(self, answers: dict[int, WorkCompletion | Exception]) -> None:
        self._answers = answers
        self.asked: list[int] = []

    def work_for(self, change_record_id: int) -> WorkCompletion:
        self.asked.append(change_record_id)
        answer = self._answers[change_record_id]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Retirer:
    """change-manager, recording exactly what it was asked to retire."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[int, str, int]] = []
        self._error = error

    def retire(self, item_id: int, *, package_id: str, package_revision: int) -> dict:
        self.calls.append((item_id, package_id, package_revision))
        if self._error is not None:
            raise self._error
        return {"id": item_id, "status": "resolved"}


def complete(**overrides) -> WorkCompletion:
    base = {"all_units_completed": True, "unit_states": ("completed",), "revision_count": 1}
    return WorkCompletion(**{**base, **overrides})


def incomplete(*states: str) -> WorkCompletion:
    return WorkCompletion(
        all_units_completed=False, unit_states=states, revision_count=1 if states else 0
    )


def _run(argv, source, reader, retirer) -> tuple[int, str]:
    out = io.StringIO()
    code = run(argv, source=source, reader=reader, retirer=retirer, out=out)
    return code, out.getvalue()


# --- the act, and the absence of it ------------------------------------------------------------


def test_a_record_whose_work_is_complete_is_retired_with_its_own_locator() -> None:
    """THE acceptance test. Not "the predicate said True" -- the call was made, with the
    locator the record carries, which is what change-manager checks against the stored row."""
    retirer = Retirer()
    code, out = _run(["--retire"], Source(record()), Reader({61: complete()}), retirer)

    assert retirer.calls == [(61, "infraops-mcp-server-npm-eslint", 1)]
    assert code == EXIT_OK
    assert "[RETIRED]" in out
    assert "1 retired" in out


def test_a_bare_pass_writes_nothing_even_with_a_retirer_to_hand() -> None:
    """THE FLAG DECIDES, not the presence of a client.

    Handing `run` a working retirer and omitting `--retire` is the only way to prove the branch
    is keyed on the flag rather than on how the caller happened to configure the environment --
    a version keyed on the client would pass every other test in this file.
    """
    retirer = Retirer()
    code, out = _run([], Source(record()), Reader({61: complete()}), retirer)

    assert retirer.calls == []
    assert code == EXIT_OK
    assert "would retire" in out
    assert "0 retired" in out


def test_an_incomplete_record_is_not_retired_and_is_not_a_finding() -> None:
    """The approved queue's ordinary state. Reporting it would leave this control permanently
    red for doing its job, which this estate has now recorded four times."""
    retirer = Retirer()
    code, out = _run(
        ["--retire"], Source(record()), Reader({61: incomplete("completed", "executing")}), retirer
    )

    assert retirer.calls == []
    assert code == EXIT_OK
    assert "[WAITING]" in out
    assert "completed, executing" in out


def test_a_record_with_no_work_at_all_is_not_a_finding() -> None:
    """Not yet carried. That is the carry's business on the same pass, not this one's."""
    retirer = Retirer()
    code, out = _run(["--retire"], Source(record()), Reader({61: incomplete()}), retirer)

    assert retirer.calls == []
    assert code == EXIT_OK
    assert "no units yet" in out


def test_an_empty_queue_is_clean() -> None:
    code, out = _run(["--retire"], Source(), Reader({}), Retirer())

    assert code == EXIT_OK
    assert "0 approved, 0 retired, 0 findings" in out


# --- findings, and what is deliberately not one -------------------------------------------------


def test_a_refused_retirement_is_a_finding() -> None:
    """change-manager refusing is a fact about the subject that needs a person."""
    retirer = Retirer(error=RetirementRefused("change-manager answered 409"))
    code, out = _run(["--retire"], Source(record()), Reader({61: complete()}), retirer)

    assert retirer.calls == [(61, "infraops-mcp-server-npm-eslint", 1)]
    assert code == EXIT_FINDINGS
    assert "NOT RETIRED" in out


def test_an_orchestrator_that_cannot_answer_about_one_record_is_a_finding() -> None:
    code, out = _run(
        ["--retire"],
        Source(record()),
        Reader({61: OrchestratorError("the orchestrator answered 503")}),
        Retirer(),
    )

    assert code == EXIT_FINDINGS
    assert "[FINDING]" in out


def test_one_bad_record_does_not_stop_the_others_being_retired() -> None:
    """Per-record isolation. Each retirement is its own transaction in change-manager, so there
    is nothing partial to unwind -- and an approved queue must not be stranded behind one row."""
    retirer = Retirer()
    code, out = _run(
        ["--retire"],
        Source(record(change_record_id=61), record(change_record_id=62, package_revision=2)),
        Reader({61: OrchestratorError("unreadable"), 62: complete()}),
        retirer,
    )

    assert retirer.calls == [(62, "infraops-mcp-server-npm-eslint", 2)]
    assert code == EXIT_FINDINGS
    assert "1 retired, 1 findings" in out


def test_an_unreadable_listing_is_a_tool_failure_not_a_finding() -> None:
    """A broken tool and an honest report about subjects are different answers."""
    code, out = _run(
        ["--retire"],
        Source(error=ChangeManagerError("change-manager could not be read")),
        Reader({}),
        Retirer(),
    )

    assert code == EXIT_TOOL_FAILURE
    assert "[TOOL FAILURE]" in out


# --- replay, which is the property a sweeping producer needs -----------------------------------


def test_a_second_pass_over_a_retired_record_is_clean() -> None:
    """The idempotent-replay property, end to end.

    change-manager answers 200 unchanged on an already-terminal record, so the second pass here
    sees the record gone from the approved listing OR sees it and replays. Both are clean; this
    asserts the second, which is the one that could have been made a finding by mistake.
    """
    retirer = Retirer()
    source, reader = Source(record()), Reader({61: complete()})

    first, _ = _run(["--retire"], source, reader, retirer)
    second, out = _run(["--retire"], source, Reader({61: complete()}), retirer)

    assert first == EXIT_OK and second == EXIT_OK
    assert len(retirer.calls) == 2
    assert "1 retired" in out


# --- configuration, refused before anything is read --------------------------------------------


def test_a_missing_change_manager_credential_is_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORK_WATCHER_CHANGE_MANAGER_TOKEN", raising=False)
    out = io.StringIO()

    assert run(["--retire"], out=out) == EXIT_UNUSABLE
    assert "WORK_WATCHER_CHANGE_MANAGER_TOKEN" in out.getvalue()


def test_a_missing_orchestrator_credential_is_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused BEFORE the listing is read, so a misconfigured pass cannot half-run."""
    monkeypatch.delenv("WORK_WATCHER_ORCHESTRATOR_TOKEN", raising=False)
    out = io.StringIO()

    assert run(["--retire"], source=Source(record()), out=out) == EXIT_UNUSABLE
    assert "WORK_WATCHER_ORCHESTRATOR_TOKEN" in out.getvalue()
