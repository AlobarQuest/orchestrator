"""The watcher alerts on a failed unit (bridge Part 3, Ruling 8).

A record in the approved queue whose work has a `failed` unit holds one alert open, keyed exactly
`dependency-unit-failed:...` (never the proposer's key), and the alert
resolves when the record leaves
the watcher's population (retired, or superseded). What the watcher SENT is asserted, not what it
decided, as in `test_work_watcher.py`.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from alert_hub_client import Alert, dependency_bump_key, dependency_unit_failed_key
from bump_proposer import alerts as proposer_alerts
from tests.standing_identity.fixtures import DEPENDENCY, PACKAGE_ID, REPOSITORY, write_package
from work_carrier.change_manager import WorkRecord
from work_watcher import alerts as watcher_alerts
from work_watcher.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, run
from work_watcher.orchestrator_client import OrchestratorError, WorkCompletion
from work_watcher.pipeline import Pipeline, PipelineRecord


def record(change_record_id: int = 61, package_id: str = PACKAGE_ID, revision: int = 1):
    return WorkRecord(
        change_record_id=change_record_id,
        package_id=package_id,
        package_revision=revision,
        package_source_repository="AlobarQuest/intent-packages",
        reasoning="the human approved building it",
        decided_by="devon",
    )


class Source:
    def __init__(self, *records: WorkRecord, others: tuple[PipelineRecord, ...] = ()) -> None:
        self._records = records
        self._others = others

    def approved_work(self) -> tuple[WorkRecord, ...]:
        return self._records

    def work_pipeline(self) -> Pipeline:
        approved = tuple(
            PipelineRecord(r.change_record_id, "approved", r.package_id, r.package_revision, "")
            for r in self._records
        )
        return Pipeline(approved + self._others, ())


class Reader:
    def __init__(self, answers: dict[int, WorkCompletion | Exception]) -> None:
        self._answers = answers

    def work_for(self, change_record_id: int) -> WorkCompletion:
        answer = self._answers[change_record_id]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Sink:
    """Records what the watcher handed the alert client."""

    def __init__(self, error: str | None = None) -> None:
        self.calls: list[tuple[dict[str, Alert], set[str], bool]] = []
        self._error = error

    def sync(self, firing, held, hold_all):
        self.calls.append((dict(firing), set(held), hold_all))
        return self._error


def failed() -> WorkCompletion:
    return WorkCompletion(all_units_completed=False, unit_states=("failed",), revision_count=1)


def running() -> WorkCompletion:
    return WorkCompletion(all_units_completed=False, unit_states=("in_progress",), revision_count=1)


def done() -> WorkCompletion:
    return WorkCompletion(all_units_completed=True, unit_states=("completed",), revision_count=1)


class Retirer:
    def retire(self, item_id: int, *, package_id: str, package_revision: int) -> dict:
        return {"id": item_id, "status": "resolved"}


def go(source, answers, sink, root: Path, *argv: str):
    out = io.StringIO()
    code = run(
        list(argv),
        source=source,
        reader=cast(Any, Reader(answers)),
        retirer=cast(Any, Retirer()),
        alerts=sink,
        packages=root,
        out=out,
    )
    return code, out.getvalue()


KEY = dependency_unit_failed_key(REPOSITORY, "npm", DEPENDENCY)


def test_a_record_with_a_failed_unit_alerts_once(tmp_path: Path) -> None:
    write_package(tmp_path)
    sink = Sink()

    code, output = go(Source(record()), {61: failed()}, sink, tmp_path, "--retire")

    assert code == EXIT_OK, output
    assert [set(firing) for firing, _, _ in sink.calls] == [{KEY}]
    alert = sink.calls[0][0][KEY]
    assert "61" in alert.message
    assert sink.calls[0][2] is False


def test_a_failed_unit_does_not_fire_on_a_pass_that_was_not_asked_to_act(tmp_path: Path) -> None:
    write_package(tmp_path)
    sink = Sink()

    code, output = go(Source(record()), {61: failed()}, sink, tmp_path)

    assert code == EXIT_OK, output
    assert sink.calls == []


def test_a_record_whose_units_are_not_failed_raises_nothing(tmp_path: Path) -> None:
    write_package(tmp_path)
    sink = Sink()

    go(Source(record()), {61: running()}, sink, tmp_path, "--retire")

    assert [firing for firing, _, _ in sink.calls] == [{}]


def test_the_alert_resolves_when_the_record_is_retired(tmp_path: Path) -> None:
    write_package(tmp_path)
    sink = Sink()

    go(Source(record()), {61: done()}, sink, tmp_path, "--retire")

    assert sink.calls == [({}, set(), False)]


def test_the_alert_resolves_when_the_record_is_superseded(tmp_path: Path) -> None:
    write_package(tmp_path)
    sink = Sink()
    newer = PipelineRecord(70, "pending", PACKAGE_ID, 2, "")

    code, _ = go(Source(record(), others=(newer,)), {61: failed()}, sink, tmp_path, "--retire")

    assert code == EXIT_FINDINGS  # the staleness finding it already reported
    assert [firing for firing, _, _ in sink.calls] == [{}]


@pytest.mark.parametrize(
    "package_id, kwargs",
    [
        ("rotation-github-pat", {"profile": "rotation"}),
        ("infraops-mcp-server-npm-old", {"standing": "false"}),
        ("infraops-mcp-server-docker-old", {}),
    ],
    ids=["rotation", "not-standing", "no-tooling-in-the-id"],
)
def test_a_record_that_is_not_a_standing_package_never_alerts(
    tmp_path: Path, package_id: str, kwargs: dict
) -> None:
    write_package(tmp_path, package_id, **kwargs)
    sink = Sink()

    go(Source(record(package_id=package_id)), {61: failed()}, sink, tmp_path, "--retire")

    assert [firing for firing, _, _ in sink.calls] == [{}]


def test_the_key_differs_from_the_proposers_only_in_its_prefix() -> None:
    proposer = proposer_alerts.key_for(REPOSITORY, "npm", DEPENDENCY)
    assert proposer == dependency_bump_key(REPOSITORY, "npm", DEPENDENCY)
    assert KEY != proposer
    assert proposer.split(":", 1)[0] == "dependency-bump"
    assert KEY.split(":", 1)[0] == "dependency-unit-failed"
    assert KEY.split(":", 1)[1] == proposer.split(":", 1)[1]


def test_the_watcher_fires_that_key_and_never_the_proposers(tmp_path: Path) -> None:
    write_package(tmp_path)
    sink = Sink()

    go(Source(record()), {61: failed()}, sink, tmp_path, "--retire")

    assert set(sink.calls[0][0]) == {KEY}


def test_a_record_the_orchestrator_cannot_answer_about_holds_its_key(tmp_path: Path) -> None:
    write_package(tmp_path)
    sink = Sink()

    code, _ = go(Source(record()), {61: OrchestratorError("HTTP 503")}, sink, tmp_path, "--retire")

    assert code == EXIT_FINDINGS
    assert sink.calls == [({}, {KEY}, False)]


def test_an_unreadable_package_is_a_finding_and_holds_every_remembered_key(
    tmp_path: Path,
) -> None:
    sink = Sink()  # no packages directory at all

    code, output = go(Source(record()), {61: failed()}, sink, tmp_path, "--retire")

    assert code == EXIT_FINDINGS, output
    assert sink.calls == [({}, set(), True)]
    assert "unreadable" in output


def test_a_readable_pass_after_an_unreadable_one_resumes(tmp_path: Path) -> None:
    sent: list[dict] = []
    sender = hub(tmp_path, sent)
    write_package(tmp_path / "checkout")
    go(Source(record()), {61: failed()}, sender, tmp_path / "checkout", "--retire")
    assert [(p["key"], p["status"]) for p in sent] == [(KEY, "firing")]

    # The package vanishes: nothing resolves and nothing re-fires, and the pass is a finding.
    code, _ = go(Source(record()), {61: failed()}, sender, tmp_path / "empty", "--retire")
    assert code == EXIT_FINDINGS
    assert len(sent) == 1

    # Readable again and the unit is still failed: still nothing new; once retired, it resolves.
    code, _ = go(Source(record()), {61: failed()}, sender, tmp_path / "checkout", "--retire")
    assert code == EXIT_OK and len(sent) == 1
    go(Source(record()), {61: done()}, sender, tmp_path / "checkout", "--retire")
    assert [(p["key"], p["status"]) for p in sent][-1] == (KEY, "resolved")


def test_an_unreadable_package_and_an_alert_hub_failure_are_both_findings(tmp_path: Path) -> None:
    code, output = go(
        Source(record()),
        {61: failed()},
        Sink("alert-hub: 1 alert(s) not sent"),
        tmp_path,
        "--retire",
    )

    assert code == EXIT_FINDINGS
    assert "2 findings" in output


def test_an_alert_hub_failure_is_a_finding(tmp_path: Path) -> None:
    write_package(tmp_path)

    code, output = go(
        Source(record()),
        {61: failed()},
        Sink("alert-hub: 1 alert(s) not sent"),
        tmp_path,
        "--retire",
    )

    assert code == EXIT_FINDINGS
    assert "alert-hub" in output


def test_an_acting_pass_without_the_sender_token_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SDS_BRIDGE_ALERT_TOKEN", raising=False)
    out = io.StringIO()

    code = run(
        ["--retire"],
        source=Source(),
        reader=cast(Any, Reader({})),
        retirer=cast(Any, Retirer()),
        out=out,
    )

    assert code == EXIT_UNUSABLE
    assert "SDS_BRIDGE_ALERT_TOKEN" in out.getvalue()


def test_a_bare_pass_does_not_need_the_sender_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDS_BRIDGE_ALERT_TOKEN", raising=False)

    code = run([], source=Source(), reader=cast(Any, Reader({})), out=io.StringIO())

    assert code == EXIT_OK


# ---- the real sender: one fire, remembered, then a resolve ------------------------------------


def hub(tmp_path: Path, sent: list[dict]) -> watcher_alerts.HubAlerts:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(202)

    return watcher_alerts.HubAlerts(
        "not-a-real-token", tmp_path / "state", transport=httpx.MockTransport(handler)
    )


def test_the_real_sender_fires_once_then_resolves(tmp_path: Path) -> None:
    sent: list[dict] = []
    sender = hub(tmp_path, sent)
    firing = {KEY: Alert("t", "m")}

    assert sender.sync(firing, set(), False) is None
    assert sender.sync(firing, set(), False) is None
    assert [(p["key"], p["status"]) for p in sent] == [(KEY, "firing")]

    assert sender.sync({}, set(), False) is None
    assert [(p["key"], p["status"]) for p in sent][-1] == (KEY, "resolved")


def test_the_real_sender_neither_fires_nor_resolves_a_held_key(tmp_path: Path) -> None:
    sent: list[dict] = []
    sender = hub(tmp_path, sent)
    sender.sync({KEY: Alert("t", "m")}, set(), False)
    sent.clear()

    sender.sync({}, {KEY}, False)
    sender.sync({}, set(), True)

    assert sent == []
