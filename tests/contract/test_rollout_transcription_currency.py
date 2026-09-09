"""The offline half of the gate that refuses a rollout revision nobody transcribed.

`scripts/check_rollout_transcription_currency.py` asks GitHub what each rollout workflow's bytes
are today and refuses any that `deploy_watcher.workflows.REGISTRY` does not hold. The reading is
over the network, so it runs as a CI step -- everything it DECIDES with is pure, and pure is what
gets tested here. Its sibling `test_profile_budget_agreement.py` exists for the same reason and
says why: a check nothing checks is the defect it guards against, wearing a different hat.

The two properties worth pinning are the ones a reader would not guess from the predicate, which
is a membership test:

  - EVERY repository is reported on every run. A second stale transcription must not hide behind
    the first, and a stale one must not hide behind an UNREADABLE one -- so an unreadable
    repository is a row and the loop continues, rather than a raise that ends the pass.
  - An unreadable repository is a FAILURE, never a pass. A check that goes quiet when it cannot
    read is the permanently-quiet twin of a permanently-red one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deploy_watcher.workflows import RolloutWorkflow
from scripts import check_rollout_transcription_currency as check

TRANSCRIBED = "1111111111111111111111111111111111111111"
ALSO_TRANSCRIBED = "2222222222222222222222222222222222222222"
UNKNOWN = "9999999999999999999999999999999999999999"

REGISTRY = {TRANSCRIBED: object(), ALSO_TRANSCRIBED: object()}

WORKFLOWS = {
    "alobarquest/first": RolloutWorkflow(".github/workflows/one.yml", 1),
    "alobarquest/second": RolloutWorkflow(".github/workflows/two.yml", 2, trigger_branch="live"),
}


def _reader(answers: dict[str, str | Exception]):
    """A stand-in for the network: a blob sha per repository, or something raised for it."""

    def read(repository: str, path: str, ref: str) -> str:
        answer = answers[repository]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return read


def _run(answers: dict[str, str | Exception], monkeypatch) -> int:
    monkeypatch.setattr(check, "ROLLOUT_WORKFLOWS", WORKFLOWS)
    monkeypatch.setattr(check, "REGISTRY", REGISTRY)
    monkeypatch.setattr(check, "read_blob_sha", _reader(answers))
    return check.main()


def test_every_rollout_transcribed_passes(monkeypatch, capsys) -> None:
    code = _run(
        {"alobarquest/first": TRANSCRIBED, "alobarquest/second": ALSO_TRANSCRIBED}, monkeypatch
    )
    captured = capsys.readouterr()

    assert code == 0
    assert "PASS" in captured.out
    assert captured.err == ""


def test_an_untranscribed_revision_fails_and_names_what_moved(monkeypatch, capsys) -> None:
    """`brain#62`'s shape: the bytes moved, nothing red until somebody happened to look."""
    code = _run({"alobarquest/first": TRANSCRIBED, "alobarquest/second": UNKNOWN}, monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert "alobarquest/second" in captured.err
    assert ".github/workflows/two.yml" in captured.err
    assert UNKNOWN in captured.err
    assert "live" in captured.err


def test_the_green_repository_is_reported_beside_the_red_one(monkeypatch, capsys) -> None:
    """Red and green in ONE run: the report is a census, not a first-failure abort."""
    code = _run({"alobarquest/first": TRANSCRIBED, "alobarquest/second": UNKNOWN}, monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert f"alobarquest/first .github/workflows/one.yml@main -> {TRANSCRIBED} :: transcribed" in (
        captured.out
    )
    assert "alobarquest/second .github/workflows/two.yml@live" in captured.out


def test_a_second_stale_transcription_does_not_hide_behind_the_first(monkeypatch, capsys) -> None:
    code = _run({"alobarquest/first": UNKNOWN, "alobarquest/second": UNKNOWN}, monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert "alobarquest/first" in captured.err
    assert "alobarquest/second" in captured.err
    assert "2 of 2" in captured.err


def test_an_unreadable_repository_fails_rather_than_passing(monkeypatch, capsys) -> None:
    """Never silence: nothing was compared, so nothing may be reported as agreeing."""
    boom = check.Unresolvable("HTTP 404 reading one.yml")
    code = _run({"alobarquest/first": boom, "alobarquest/second": ALSO_TRANSCRIBED}, monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert "unreadable" in captured.err
    assert "HTTP 404" in captured.err
    assert "alobarquest/first" in captured.err


def test_a_stale_transcription_does_not_hide_behind_an_unreadable_repository(
    monkeypatch, capsys
) -> None:
    """The first repository alphabetically cannot be read; the second has genuinely moved.

    A `raise` where the loop has its `continue` would end the pass on the first and report the
    second as nothing at all -- so the outage would mask the finding.
    """
    boom = check.Unresolvable("HTTP 500 reading one.yml")
    code = _run({"alobarquest/first": boom, "alobarquest/second": UNKNOWN}, monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert "2 of 2" in captured.err
    assert "not transcribed" in captured.err
    assert UNKNOWN in captured.err


def test_every_repository_is_reported_in_a_stable_order(monkeypatch, capsys) -> None:
    _run({"alobarquest/first": TRANSCRIBED, "alobarquest/second": ALSO_TRANSCRIBED}, monkeypatch)
    lines = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("alobarquest/")
    ]

    assert [line.split()[0] for line in lines] == ["alobarquest/first", "alobarquest/second"]


def test_the_audit_asks_each_workflow_for_its_own_trigger_branch(monkeypatch) -> None:
    """`trigger_branch` is transcribed rather than assumed to be `main`, so the read must use it."""
    asked: list[tuple[str, str, str]] = []

    def record(repository: str, path: str, ref: str) -> str:
        asked.append((repository, path, ref))
        return TRANSCRIBED

    rows = check.audit(WORKFLOWS, REGISTRY, record)

    assert asked == [
        ("alobarquest/first", ".github/workflows/one.yml", "main"),
        ("alobarquest/second", ".github/workflows/two.yml", "live"),
    ]
    assert [row.problem for row in rows] == [None, None]


def test_a_blob_sha_is_read_from_a_file_answer() -> None:
    assert check.blob_sha_of({"sha": TRANSCRIBED}, "r", "p", "main") == TRANSCRIBED


@pytest.mark.parametrize(
    "document",
    [[{"sha": TRANSCRIBED}], {}, {"sha": ""}, {"sha": None}, {"sha": 7}, "not json at all"],
    ids=["directory-listing", "no-sha", "empty-sha", "null-sha", "non-string-sha", "not-an-object"],
)
def test_an_answer_that_is_not_a_file_is_unresolvable_rather_than_a_crash(document: object) -> None:
    """A directory answers a LIST, on which `.get` would raise rather than refuse."""
    with pytest.raises(check.Unresolvable):
        check.blob_sha_of(document, "alobarquest/first", ".github/workflows/one.yml", "main")


def test_the_gate_runs_the_script_this_module_tests() -> None:
    """A check nothing invokes is the defect it guards against, wearing a different hat."""
    workflows = [
        path
        for path in Path(".github/workflows").glob("*.yml")
        if "check_rollout_transcription_currency.py" in path.read_text(encoding="utf-8")
    ]

    assert [path.name for path in workflows] == ["quality.yml"]
