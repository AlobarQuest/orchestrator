"""What is due, as infraops answers it: the contract, the precedence, and every refusal.

`tests/fixtures/rotation_findings.json` is the shape infraops' `security-drift-cli cred-findings`
prints. It is the contract between the two repositories; a change to either side's reading of it
starts here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rotation_proposer import findings
from rotation_proposer.findings import CLI_SCRIPT, Due, FindingsError, parse, read_due

FIXTURE = Path("tests/fixtures/rotation_findings.json")


def _document(*entries: dict) -> str:
    return json.dumps({"schema_version": 1, "findings": list(entries)})


AGE = {"check": "cred.rotation-age", "id": "k", "class": "openai-key", "anchor": "2025-01-02"}
REQUESTED = {
    "check": "cred.rotation-requested",
    "id": "k",
    "class": "openai-key",
    "rotate_requested": "2026-10-07",
}
EXPOSURE = {
    "check": "cred.exposure-rotate",
    "id": "k",
    "class": "openai-key",
    "exposure_id": "transcript-1",
    "exposure_date": "2026-07-09",
}


def test_the_contract_fixture_reads_as_one_due_per_credential() -> None:
    due, unrecognised = parse(FIXTURE.read_text())
    assert due == [
        Due(
            "github-classic-aihelper",
            "github-pat-classic",
            "exposure",
            "codex-2026-07-02",
            "2026-07-02",
        ),
        Due("openrouter-generic", "openrouter-key", "requested", "2026-10-07", "2026-10-07"),
    ]
    assert unrecognised == []


@pytest.mark.parametrize(
    ("entries", "occurrence"),
    [
        ((AGE,), "age-2025-01-02"),
        ((REQUESTED,), "requested-2026-10-07"),
        ((EXPOSURE,), "exposure-transcript-1"),
        ((AGE, REQUESTED), "requested-2026-10-07"),
        ((REQUESTED, AGE), "requested-2026-10-07"),
        ((AGE, EXPOSURE, REQUESTED), "exposure-transcript-1"),
    ],
)
def test_one_rotation_answers_every_trigger_and_the_precedence_names_it(
    entries, occurrence
) -> None:
    """Kills: dropping the precedence (two occurrences for one credential would revise each other
    on alternate passes forever), and reading the order of the list instead of the trigger."""
    due, _ = parse(_document(*entries))
    assert [item.occurrence for item in due] == [occurrence]


def test_an_age_occurrence_is_dated_by_the_last_rotation_never_by_the_pass() -> None:
    """Kills: a time-of-day or a clock in the occurrence. A value that moved between passes would
    make every daily pass see a new rotation and revise the package again."""
    due, _ = parse(_document(dict(AGE, anchor="2025-01-02T13:14:15.000Z")))
    assert due[0].occurrence == "age-2025-01-02"
    assert due[0].dated == "2025-01-02"


def test_a_check_that_is_not_a_rotation_is_skipped_without_a_finding() -> None:
    due, unrecognised = parse(_document({"check": "cred.unknown-class", "id": "k", "class": "x"}))
    assert (due, unrecognised) == ([], [])


def test_a_check_this_program_has_never_heard_of_is_reported_not_skipped() -> None:
    """Kills: ignoring unknown checks. The day infraops adds a new trigger, skipping it silently
    would leave that rotation unproposed with nothing saying so."""
    due, unrecognised = parse(_document({"check": "cred.rotation-expiry", "id": "k", "class": "x"}))
    assert due == []
    assert [(u.credential_id, u.check) for u in unrecognised] == [("k", "cred.rotation-expiry")]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"schema_version": 2, "findings": []}),
        json.dumps({"findings": []}),
        json.dumps({"schema_version": 1}),
        json.dumps({"schema_version": 1, "findings": ["x"]}),
        _document(dict(AGE, anchor="last tuesday")),
        _document({k: v for k, v in AGE.items() if k != "anchor"}),
        _document(dict(REQUESTED, rotate_requested=None)),
        _document({k: v for k, v in EXPOSURE.items() if k != "exposure_id"}),
        _document(dict(EXPOSURE, exposure_date="soon")),
        # A value that would corrupt the quoted YAML line or the observation reference.
        _document(dict(EXPOSURE, exposure_id="it's")),
        _document(dict(EXPOSURE, exposure_id="a:b")),
        _document(dict(AGE, id="k\nstatus: approved")),
        _document(dict(AGE, id="")),
        _document(dict(AGE, **{"class": None})),
    ],
)
def test_anything_unreadable_refuses_the_whole_pass(text) -> None:
    """A pass that cannot read what is due cannot say what is NOT due, so nothing is guessed."""
    with pytest.raises(FindingsError):
        parse(text)


# --- asking infraops ------------------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    script = tmp_path / CLI_SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text("// stand-in\n")
    monkeypatch.setattr(findings, "node_binary", lambda: Path("/bin/node"))
    return tmp_path


def _answer(code: int, stdout: str = "", stderr: str = ""):
    seen: list[list[str]] = []

    def run(command):
        seen.append(command)
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    return run, seen


def test_infraops_is_asked_through_its_own_cli_and_subcommand(checkout) -> None:
    run, seen = _answer(0, FIXTURE.read_text())
    due, _ = read_due(checkout, run=run)
    assert seen == [["/bin/node", str(checkout / CLI_SCRIPT), "cred-findings"]]
    assert len(due) == 2


def test_a_refusal_from_infraops_names_its_last_line(checkout) -> None:
    """An infraops that predates the subcommand says so on its last line; that is the message."""
    run, _ = _answer(1, stderr="trace\nError: unknown command: cred-findings\n")
    with pytest.raises(FindingsError, match="unknown command: cred-findings"):
        read_due(checkout, run=run)


def test_no_infraops_checkout_is_a_named_refusal(tmp_path) -> None:
    with pytest.raises(FindingsError, match="no infraops security-drift CLI"):
        read_due(tmp_path, run=_answer(0)[0])


def test_node_is_found_where_launchd_cannot_see_it(monkeypatch, tmp_path) -> None:
    """launchd's PATH names only system directories; `node` from Homebrew is not on it."""
    fallback = tmp_path / "node"
    fallback.write_text("")
    monkeypatch.setattr(findings.shutil, "which", lambda name: None)
    monkeypatch.setattr(findings, "NODE_FALLBACKS", (tmp_path / "absent", fallback))
    assert findings.node_binary() == fallback
    monkeypatch.setattr(findings, "NODE_FALLBACKS", (tmp_path / "absent",))
    with pytest.raises(FindingsError, match="node is not on this machine"):
        findings.node_binary()
