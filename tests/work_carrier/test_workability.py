"""Should the SDS work on this repository at all — reported, and changing nothing.

Two properties carry this increment and they need different apparatus.

**That the report DECIDES NOTHING** is proven by differential: the carry is run with
the report and with it replaced by a no-op, and the exit code and every other line
must be identical — including when the report's own inputs are broken. An assertion
that the report "does not raise" is weaker than it looks, because a report that
raised inside a branch no test reaches would satisfy it.

**That the report DISCRIMINATES** is proven by driving the three outcomes over the
same code from different inputs, and by the pair of cases that are easy to collapse:
a repository that answered `false` (refuse) and a repository nothing could be learned
about (cannot decide). Collapsing those two is the defect the whole design is shaped
against — a lookup miss reading as "not capable" is a refusal manufactured from a bug.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import time
from pathlib import Path

import pytest

from sds_members.client import SERVICES, Member, MembersUnreadable
from work_carrier.change_manager import WorkRecord
from work_carrier.cli import (
    EXIT_FINDINGS,
    EXIT_OK,
    EXIT_TOOL_FAILURE,
    EXIT_UNUSABLE,
    run,
    worst,
)
from work_carrier.prepare import emit_key
from work_carrier.workability import (
    NO,
    NOT_WORKABLE,
    UNDECIDED,
    UNJUDGED,
    UNKNOWN,
    WORKABLE,
    YES,
    Portfolio,
    assess,
    grants_by_service,
    judge,
    load_portfolio,
    project_for,
    target_repository,
)

TARGET = "AlobarQuest/infraops-mcp-server"


def check(check_id: str, status: str) -> dict:
    return {"id": check_id, "status": status, "details": [], "fix": None}


def project(name: str = "infraops-mcp-server", **statuses: str) -> dict:
    """One `portfolio.json` project row, shaped the way the nightly sweep writes them."""
    declared = {
        "runner.caller": "pass",
        "factory.pat_access": "pass",
        "factory.pat_scope": "unknown",
        "factory.secrets": "pass",
        "factory.landing_known": "pass",
        **statuses,
    }
    return {
        "name": name,
        "path": f"/Users/devon/Projects/{name}",
        "factory": [check(key, value) for key, value in declared.items()],
    }


def portfolio(*rows: dict, age_seconds: float = 3600.0) -> Portfolio:
    return Portfolio(rows, age_seconds, f"portfolio.json is {int(age_seconds // 3600)}h old")


def payload(target: str | None = TARGET) -> dict:
    fields = {"package": "zod"} if target is None else {"target_repo": target, "package": "zod"}
    return {"enforcement_snapshot": {"profile_fields": fields}}


def member(repository: str = TARGET, *, state: str = "member", **grants: str) -> Member:
    """One member-record row; a service not named is `not_assessed`."""
    return Member(
        repository=repository,
        owner="devon",
        state=state,
        working_copy=None,
        machine_local=False,
        apps=(),
        grants={service: grants.get(service, "not_assessed") for service in SERVICES},
    )


def grants(*repositories: str, service: str = "app_changes") -> dict[str, frozenset[str]]:
    """The per-service grant sets the carrier judges by, built the way the carrier builds them."""
    return grants_by_service([member(r, **{service: "granted"}) for r in repositories])


GRANTED = grants(TARGET)
NOT_GRANTED = grants()


# -------------------------------------------------------------------------------------
# The three constraints, composed
# -------------------------------------------------------------------------------------


def test_a_granted_repository_that_measures_clean_would_be_carried() -> None:
    verdict = assess(TARGET, True, portfolio(project()))
    assert verdict.decision == WORKABLE
    assert [c.verdict for c in verdict.constraints] == [YES, YES, YES]


def test_an_ungranted_repository_would_be_refused() -> None:
    verdict = assess(TARGET, False, portfolio(project()))
    assert verdict.decision == NOT_WORKABLE
    assert verdict.constraints[0].verdict == NO


def test_a_capability_violation_would_be_refused_and_names_the_check() -> None:
    """The third outcome the live population cannot currently produce, constructed.

    Every repository the sweep measures reports `pass` on all four today, so a
    capability refusal has never been observed — which is exactly why it is built
    here rather than asserted to work.
    """
    rows = portfolio(project(**{"runner.caller": "violation"}))
    verdict = assess(TARGET, True, rows)
    assert verdict.decision == NOT_WORKABLE
    capable = verdict.constraints[1]
    assert capable.verdict == NO
    assert "runner.caller" in capable.detail


def test_a_permission_violation_is_its_own_constraint_not_folded_into_capability() -> None:
    rows = portfolio(project(**{"factory.pat_access": "violation"}))
    verdict = assess(TARGET, True, rows)
    assert verdict.decision == NOT_WORKABLE
    assert verdict.constraints[1].verdict == YES
    assert verdict.constraints[2].verdict == NO
    assert "factory.pat_access" in verdict.constraints[2].detail


# -------------------------------------------------------------------------------------
# A lookup that fails is NOT a refusal
# -------------------------------------------------------------------------------------


def test_a_repository_no_project_row_matches_cannot_be_decided_and_is_not_refused() -> None:
    """The defect this design is shaped against.

    `portfolio.json` keys projects by DIRECTORY NAME and a change record's package
    names an `owner/repo`, so the mapping is a guess that can miss. A miss that read
    as "not capable" would be a refusal manufactured from a bug, so it answers
    UNKNOWN — and the composition can never turn UNKNOWNs into a refusal.
    """
    verdict = assess("AlobarQuest/never-heard-of-it", True, portfolio(project()))
    assert verdict.decision == UNDECIDED
    assert verdict.decision != NOT_WORKABLE
    assert [c.verdict for c in verdict.constraints] == [YES, UNKNOWN, UNKNOWN]
    assert "lookup that found nothing" in verdict.constraints[1].detail


def test_two_projects_answering_to_one_name_cannot_be_decided_rather_than_picked() -> None:
    rows = portfolio(
        project(), {"name": "other", "path": "/tmp/infraops-mcp-server", "factory": []}
    )
    found, why = project_for(rows, TARGET)
    assert found is None
    assert "more than one project" in why
    assert assess(TARGET, True, rows).decision == UNDECIDED


def test_a_repository_the_sweep_never_measured_cannot_be_decided() -> None:
    """An empty `factory` block is out of the sweep's scope, not a clean bill of health."""
    rows = portfolio(
        {"name": "infraops-mcp-server", "path": "/x/infraops-mcp-server", "factory": []}
    )
    verdict = assess(TARGET, True, rows)
    assert verdict.decision == UNDECIDED
    assert "recorded no factory checks" in verdict.constraints[1].detail


def test_a_check_the_sweep_did_not_record_cannot_be_decided() -> None:
    rows = portfolio(project())
    rows.projects[0]["factory"] = [
        row for row in rows.projects[0]["factory"] if row["id"] != "factory.landing_known"
    ]
    verdict = assess(TARGET, True, rows)
    assert verdict.constraints[1].verdict == UNKNOWN
    assert "factory.landing_known" in verdict.constraints[1].detail


def test_a_check_row_whose_status_is_not_a_string_cannot_be_decided() -> None:
    """An unreadable row must not be indistinguishable from a measured one.

    The sweep writes these rows and nothing here validates them, so a `null` status —
    or a renamed field — has to reach the same answer as a check that was never
    recorded, rather than a status nothing can compare against `pass`.
    """
    rows = portfolio(project())
    for row in rows.projects[0]["factory"]:
        if row["id"] == "factory.pat_access":
            row["status"] = None
    verdict = assess(TARGET, True, rows)
    assert verdict.decision == UNDECIDED
    assert verdict.constraints[2].verdict == UNKNOWN
    # NOT RECORDED, rather than recorded with a value nothing can read. Both reach UNKNOWN,
    # so asserting the verdict alone cannot tell them apart -- and a reader told
    # `factory.pat_access is None` goes looking for a check that answered, where one told the
    # sweep recorded nothing goes looking at the sweep.
    assert "recorded no factory.pat_access" in verdict.constraints[2].detail


def test_a_check_that_is_unknown_rather_than_passing_cannot_be_decided() -> None:
    rows = portfolio(project(**{"factory.landing_known": "unknown"}))
    verdict = assess(TARGET, True, rows)
    assert verdict.decision == UNDECIDED
    assert verdict.constraints[1].verdict == UNKNOWN


# -------------------------------------------------------------------------------------
# Composition: a definite NO decides; no combination of UNKNOWNs can
# -------------------------------------------------------------------------------------


def test_a_definite_refusal_beside_an_unknown_still_refuses() -> None:
    """Three-valued AND. A repository the record does not grant answered, whatever else is unknown.

    The property that matters is the ASYMMETRY, and both halves are asserted here:
    a NO decides through an UNKNOWN, and UNKNOWNs alone never produce a refusal.
    """
    verdict = assess("AlobarQuest/never-heard-of-it", False, portfolio(project()))
    assert verdict.decision == NOT_WORKABLE


def test_no_combination_of_unknowns_produces_a_refusal() -> None:
    verdict = assess("AlobarQuest/never-heard-of-it", True, portfolio())
    assert verdict.decision == UNDECIDED
    assert all(c.verdict == UNKNOWN for c in verdict.constraints[1:])


def test_a_payload_naming_no_target_repository_is_nothing_to_judge_not_a_refusal() -> None:
    verdict = assess(None, False, portfolio(project()))
    assert verdict.decision == UNDECIDED
    assert verdict.repository is None


# -------------------------------------------------------------------------------------
# `factory.pat_scope` rides in the report and never disqualifies
# -------------------------------------------------------------------------------------


def test_pat_scope_being_permanently_unknown_does_not_stop_a_repository_being_carried() -> None:
    """Its own check says it is ALWAYS unknown, so a term requiring it passes nothing, ever."""
    rows = portfolio(project(**{"factory.pat_scope": "unknown"}))
    assert assess(TARGET, True, rows).decision == WORKABLE


def test_the_workflow_residual_is_named_on_every_repository_that_would_be_carried() -> None:
    """Not disqualifying is not the same as not mattering, and the output must say so."""
    out = io.StringIO()
    judge(
        [("change record 1", payload())],
        out,
        grants=GRANTED,
        portfolio=portfolio(project()),
    )
    text = out.getvalue()
    assert WORKABLE in text
    assert "pat_scope" in text
    assert ".github/workflows/**" in text


def test_the_residual_is_not_claimed_about_a_repository_that_would_not_be_carried() -> None:
    out = io.StringIO()
    judge(
        [("change record 1", payload())],
        out,
        grants=NOT_GRANTED,
        portfolio=portfolio(project()),
    )
    assert "pat_scope" not in out.getvalue()


# -------------------------------------------------------------------------------------
# The target repository comes from the PACKAGE, never from the record
# -------------------------------------------------------------------------------------


def test_the_target_repository_is_read_from_the_approved_packages_own_declaration() -> None:
    assert target_repository(payload()) == TARGET


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"enforcement_snapshot": None},
        {"enforcement_snapshot": {}},
        {"enforcement_snapshot": {"profile_fields": None}},
        {"enforcement_snapshot": {"profile_fields": {}}},
        {"enforcement_snapshot": {"profile_fields": {"target_repo": ""}}},
        {"enforcement_snapshot": {"profile_fields": {"target_repo": 7}}},
    ],
)
def test_every_hop_to_the_target_repository_is_defensive(broken: dict) -> None:
    """A profile that declares no target is an honest None, never a guess and never a refusal."""
    assert target_repository(broken) is None


# -------------------------------------------------------------------------------------
# The capability source's age, and what it does not cover
# -------------------------------------------------------------------------------------


def test_the_report_names_the_age_of_the_capability_source() -> None:
    out = io.StringIO()
    judge([], out, grants=GRANTED, portfolio=portfolio(age_seconds=7200))
    assert "2h old" in out.getvalue()


def test_a_missing_capability_source_is_named_rather_than_read_as_no_capability(
    tmp_path: Path,
) -> None:
    loaded = load_portfolio(tmp_path / "absent.json")
    assert loaded.projects == ()
    assert "could not be read" in loaded.detail
    assert assess(TARGET, True, loaded).decision == UNDECIDED


def test_an_unreadable_capability_source_is_named_rather_than_raising(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.json"
    path.write_text("{not json", encoding="utf-8")
    loaded = load_portfolio(path)
    assert loaded.projects == ()
    assert "not readable JSON" in loaded.detail


def test_a_capability_source_with_no_projects_list_is_named(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps({"untriaged_count": 0}), encoding="utf-8")
    assert "carries no projects list" in load_portfolio(path).detail


def test_the_age_is_measured_from_the_file_rather_than_from_a_field(tmp_path: Path) -> None:
    """`generated_at` in the real document is null, so the mtime is the only timestamp."""
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps({"projects": []}), encoding="utf-8")
    loaded = load_portfolio(path, now=time.time() + 86400 * 3)
    assert loaded.age_seconds is not None and loaded.age_seconds > 86400 * 2
    assert "3d old" in loaded.detail


# -------------------------------------------------------------------------------------
# THE SAFETY ARGUMENT: the report changes nothing
# -------------------------------------------------------------------------------------


class _Source:
    def __init__(self, records=()) -> None:
        self._records = tuple(records)

    def approved_work(self):
        return self._records


def _emitting(emitted: dict):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(emitted), stderr="")

    return runner


class _BrokenGrants(dict):
    """A grant table whose lookup raises, standing in for a defect inside the judge."""

    def get(self, key, default=None):
        raise RuntimeError("the reader is broken")


def _emitted(rec: WorkRecord) -> dict:
    """What the emitter returns for the fixture record, carrying the package's own target."""
    return {
        "package_id": rec.package_id,
        "revision": rec.package_revision,
        "source_repository": rec.package_source_repository,
        "change_record_id": rec.change_record_id,
        "idempotency_key": emit_key(rec),
        "expected_version": 0,
        "enforcement_snapshot": {"profile_fields": {"target_repo": TARGET, "package": "zod"}},
    }


def _record() -> WorkRecord:
    return WorkRecord(
        change_record_id=77,
        package_id="ws32-approved-software",
        package_revision=1,
        package_source_repository="AlobarQuest/intent-packages",
        reasoning="the estate decided to do this",
        decided_by="devon",
    )


def _carry(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    members: list[Member],
    judged_by: dict | None = None,
    portfolio_rows=None,
) -> tuple[int, str, list[dict]]:
    """Run a real `--register` carry with the judgment's inputs injected.

    THE WRITER IS WHAT DISCRIMINATES. A refusal that only changed the printed line would
    satisfy an assertion about the exit code and still register the record, so every test
    below reads what the writer was handed as well as what the pass returned.
    """
    monkeypatch.setattr(
        "work_carrier.cli.judge_workability",
        lambda subjects, out, grants: judge(
            subjects,
            out,
            grants=grants if judged_by is None else judged_by,
            portfolio=portfolio(project()) if portfolio_rows is None else portfolio_rows,
        ),
    )
    rec = _record()
    import work_carrier.cli as cli_module
    import work_carrier.prepare as prepare_module

    original = prepare_module.prepare
    monkeypatch.setattr(
        cli_module,
        "prepare",
        lambda r, **kw: original(r, **kw, runner=_emitting(_emitted(r))),
    )
    writer = _Registrar()
    code, text = _run_registering(root, rec, writer, members)
    return code, text, writer.payloads


def _run_registering(
    root: Path, rec: WorkRecord, writer, members: list[Member] | None
) -> tuple[int, str]:
    """The `run` call, behind an unannotated `writer`.

    The double is not an `OrchestratorClient` and pyright is right to say so: `run` takes
    the concrete client because nothing yet declares the two methods the carry actually
    uses as a protocol. `test_work_carrier.py` passes its own double through an
    unannotated parameter for the same reason; this matches it rather than adding a
    suppression comment that would be the first of its kind in this package.
    """
    out = io.StringIO()
    code = run(
        ["--checkout-root", str(root), "--register"],
        source=_Source([rec]),
        registrar=writer,
        members=members,
        out=out,
    )
    return code, out.getvalue()


class _Registrar:
    """The smallest orchestrator client the carry will accept, recording what it was given."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def carried_revisions(self, change_record_id: int) -> list[str]:
        return []

    def register_intake(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"id": "11111111-1111-1111-1111-111111111111"}


def test_all_three_yes_is_carried(checkout_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control, without which every refusal below could pass for free.

    A test suite in which nothing is ever carried cannot tell a working gate from a
    program that registers nothing, so this runs the same path with an estate that
    answers yes three times and asserts the record reached the writer.
    """
    code, text, payloads = _carry(
        checkout_root, monkeypatch, members=[member(app_changes="granted")]
    )

    assert code == EXIT_OK
    assert len(payloads) == 1
    assert WORKABLE in text
    assert "residual" in text, "the pat_scope residual belongs on the line that authorises work"


def test_a_repository_the_record_does_not_grant_is_not_carried_and_the_reason_is_printed(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Devon's rule: without all three, it is useless to go forward.

    A definite NO is a FINDING -- the record can never be carried while the repository
    says what it says -- so it exits 3, a finding, which the launcher reports to alert-hub
    (ADR-0021 amendment 2) and which differs from the 2 an unreachable answer gets.
    """
    code, text, payloads = _carry(checkout_root, monkeypatch, members=[member()])

    assert payloads == [], "a record that is not workable must never reach the writer"
    assert code == EXIT_FINDINGS
    assert NOT_WORKABLE in text
    assert "NOT CARRIED" in text
    assert "does not grant app_changes" in text


def test_a_constraint_nobody_could_answer_holds_the_record_under_a_different_code(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An UNKNOWN is not a NO, and the two must stay tellable apart.

    Nothing is wrong with this record -- a later pass may carry it -- so it is unusable
    INPUT rather than a finding, and in this lane that is a different code which the
    launcher ranks ABOVE a finding. Both halves are asserted, because a refusal that
    used one code for both would satisfy "it was not carried" and tell a reader the
    wrong thing about what to do.
    """
    code, text, payloads = _carry(
        checkout_root,
        monkeypatch,
        members=[member(app_changes="granted")],
        portfolio_rows=portfolio(),
    )

    assert payloads == []
    assert code == EXIT_UNUSABLE
    assert code != EXIT_FINDINGS
    assert UNDECIDED in text
    assert NOT_WORKABLE not in text


def test_a_judgment_that_raises_holds_the_record_and_reports_a_tool_failure(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old guard said "failed and changed nothing"; under a deciding check that sentence
    would be a fail-open.

    A record nobody judged must be held, not carried -- so the broad guard now yields a
    fourth answer that is not WORKABLE, and the pass reports the tool itself having
    failed. Asserting the writer saw nothing is the half that discriminates: a guard that
    swallowed the error and returned WORKABLE would print the same failure line.
    """
    code, text, payloads = _carry(
        checkout_root,
        monkeypatch,
        members=[member(app_changes="granted")],
        judged_by=_BrokenGrants(),
    )

    assert payloads == []
    assert code == EXIT_TOOL_FAILURE
    assert UNJUDGED in text
    assert "NOT carried" in text
    assert "RuntimeError" in text
    assert WORKABLE not in text


def test_pat_scopes_permanent_unknown_does_not_prevent_a_carry(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The carve-out, asserted rather than assumed, and it is load-bearing.

    `factory.pat_scope` is `unknown` BY CONSTRUCTION -- Actions secrets are write-only and
    no API reports a fine-grained PAT's permissions -- so a constraint that required it to
    pass would refuse every repository forever. The estate here reports it `violation`,
    which is stronger than the live `unknown`: even then the permission constraint reads
    only `factory.pat_access`, and the record is carried with the residual printed.
    """
    rows = portfolio(project(**{"factory.pat_scope": "violation"}))
    code, text, payloads = _carry(
        checkout_root, monkeypatch, members=[member(app_changes="granted")], portfolio_rows=rows
    )

    assert code == EXIT_OK
    assert len(payloads) == 1
    assert "pat_scope is unknown by construction" in text


def test_a_subject_whose_line_cannot_be_written_is_held_rather_than_carried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing the reason is part of answering, so failing to write it holds the record.

    The reverse order — record the decision, then print it — was proposed in review and
    is the fail-open: it keeps WORKABLE, the print raises, the guard prints "NOT carried",
    and the record is carried anyway, with the stream asserting the opposite of what
    happened. Both halves are asserted here, because the printed line alone cannot tell
    the two orders apart: the guard prints the same sentence either way, and only the
    returned decision says whether the record will be offered to the writer.

    The second subject still answers, which is the other half of the per-subject guard.
    """
    from work_carrier import workability as module

    real = module._print_one
    calls: list[str] = []

    def explode(label: str, verdict, out) -> None:
        calls.append(label)
        if label == "first":
            raise RuntimeError("the stream is gone")
        real(label, verdict, out)

    monkeypatch.setattr(module, "_print_one", explode)
    out = io.StringIO()
    decisions = judge(
        [("first", payload(TARGET)), ("second", payload(TARGET))],
        out,
        grants=GRANTED,
        portfolio=portfolio(project()),
    )

    assert calls == ["first", "second"]
    assert decisions == (UNJUDGED, WORKABLE)
    assert "first could not be judged, so it is NOT carried" in out.getvalue()


def test_the_exit_ranking_is_the_launchers_own(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two artifacts hold one ordering, so this reads the shell rather than restating it.

    `run-work-carrier.sh` folds both phases with a `_rank` whose arms are the authority
    for which outcome dominates, and the carry now returns more than one kind of code in
    a single pass -- so a `max()` over the numbers would answer 3 for `{1, 3}` and put a
    finding above a tool failure, inverting the launcher's verdict. The table is parsed
    out of the script, so an edit to either side reds this.
    """
    from work_carrier.cli import _RANK

    script = Path("scripts/run-work-carrier.sh").read_text(encoding="utf-8")
    arms = re.findall(r"^\s+(\d+|\*)\) echo (\d+) ;;$", script, re.MULTILINE)
    ranked = {int(code): int(rank) for code, rank in arms if code != "*"}

    assert ranked, "the launcher's _rank arms were not found — this test is measuring nothing"
    assert _RANK == ranked
    assert worst([EXIT_TOOL_FAILURE, EXIT_FINDINGS]) == EXIT_TOOL_FAILURE
    assert worst([EXIT_UNUSABLE, EXIT_TOOL_FAILURE, EXIT_FINDINGS]) == EXIT_UNUSABLE
    assert worst([]) == EXIT_OK

    # THE CATCH-ALL IS THE HALF THE TABLE CANNOT CARRY, and the shell says why: it ranks
    # rather than folding so that a code nobody planned for -- 127 for a missing binary is
    # the one that happens -- is preserved and DOMINANT instead of read as success. So the
    # star arm is asserted to exist, and the Python side asserted to agree with it rather
    # than raising a KeyError, which would be a pass that reported nothing because it could
    # not classify its own outcome.
    assert [rank for code, rank in arms if code == "*"] == ["4"]
    assert worst([127, EXIT_UNUSABLE]) == 127


def test_the_carry_really_does_ask_the_real_judge(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring, unpatched — or every test above would be about a function nobody calls.

    All of them replace `judge_workability`, so none can see that the carry reaches it at
    all. This runs the real one, with a record that really prepares and with this package's
    fail-closed defaults: a grant but no portfolio, so nothing can be answered, so
    nothing is carried and the pass reports unusable input. The exit code is the half that
    discriminates — the block alone would still print if the decision were thrown away.
    """
    import work_carrier.cli as cli_module
    import work_carrier.prepare as prepare_module

    original = prepare_module.prepare
    monkeypatch.setattr(
        cli_module, "prepare", lambda r, **kw: original(r, **kw, runner=_emitting(_emitted(r)))
    )
    out = io.StringIO()
    code = run(
        ["--checkout-root", str(checkout_root)],
        source=_Source([_record()]),
        members=[member(app_changes="granted")],
        out=out,
    )
    assert "[WORKABILITY]" in out.getvalue()
    assert UNDECIDED in out.getvalue()
    assert code == EXIT_UNUSABLE


def test_an_empty_queue_still_says_the_check_ran(checkout_root: Path) -> None:
    """Silence would be indistinguishable from the check having been removed."""
    out = io.StringIO()
    code = run(["--checkout-root", str(checkout_root)], source=_Source([]), members=[], out=out)
    assert code == EXIT_OK
    assert "no repository was judged" in out.getvalue()


def test_the_report_asks_about_the_package_target_and_never_the_records_repository(
    checkout_root: Path,
) -> None:
    """A `work` record's `package_source_repository` is always the packages repository.

    Measured against production 2026-09-11: `target_repository` is null on all seven
    work records and `package_source_repository` is `AlobarQuest/intent-packages` on
    every one. Keying on either would judge every carry against a repository that is granted
    and measures clean, so the check would pass everything forever.
    """
    out = io.StringIO()
    decisions = judge(
        [("change record 1", payload())],
        out,
        grants=grants("AlobarQuest/intent-packages"),
        portfolio=portfolio(project()),
    )
    assert decisions == (NOT_WORKABLE,)
    assert f"-> {TARGET}" in out.getvalue()


def test_one_unjudgeable_subject_does_not_silence_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard one level too high makes the PASS total and leaves the REPORT partial.

    The reader raises for one repository and answers for the other, so a guard wrapped
    round the whole loop prints a single failure line and drops the second subject
    entirely -- indistinguishable, to a reader, from a queue that was shorter. Both
    halves are asserted: the failure is named AND the survivor is still judged.
    """

    class _RaisesFor(frozenset):
        def __contains__(self, item: object) -> bool:
            if item == "alobarquest/broken":
                raise RuntimeError("the reader is broken")
            return super().__contains__(item)

    out = io.StringIO()
    judge(
        [("first", payload("AlobarQuest/broken")), ("second", payload(TARGET))],
        out,
        grants={**GRANTED, "app_changes": _RaisesFor({TARGET.lower()})},
        portfolio=portfolio(project()),
    )
    text = out.getvalue()
    assert "first could not be judged, so it is NOT carried" in text, text
    assert "RuntimeError" in text, text
    assert WORKABLE in text, text
    assert "second" in text, text


def test_a_record_in_an_ungranted_repository_is_not_workable() -> None:
    out = io.StringIO()
    decisions = judge(
        [("a record", payload("AlobarQuest/elsewhere"))],
        out,
        grants=GRANTED,
        portfolio=portfolio(project("elsewhere")),
    )
    assert decisions == (NOT_WORKABLE,)


def test_a_dependency_update_record_is_judged_by_dependency_updates() -> None:
    """Granted only `dependency_updates`: a dependency-update record is workable, an app change
    in the same repository is not -- the change class picks the service."""
    only = grants(TARGET, service="dependency_updates")
    dependency = {**payload(), "profile": "dependency-update"}
    app_change = {**payload(), "profile": "software"}
    decisions = judge(
        [("dependency update", dependency), ("app change", app_change)],
        io.StringIO(),
        grants=only,
        portfolio=portfolio(project()),
    )
    assert decisions == (WORKABLE, NOT_WORKABLE)


def test_the_grant_check_folds_case() -> None:
    folded = grants_by_service([member("AlobarQuest/Infraops-MCP-Server", app_changes="granted")])
    decisions = judge(
        [("a record", payload("alobarquest/infraops-mcp-server"))],
        io.StringIO(),
        grants=folded,
        portfolio=portfolio(project()),
    )
    assert decisions == (WORKABLE,)


@pytest.mark.parametrize("state", ["remediating", "withdrawn", "not_assessed"])
def test_a_grant_that_is_not_granted_does_not_admit(state: str) -> None:
    held = grants_by_service([member(app_changes=state)])
    decisions = judge(
        [("a record", payload())], io.StringIO(), grants=held, portfolio=portfolio(project())
    )
    assert decisions == (NOT_WORKABLE,)


def test_a_retired_members_grant_does_not_admit() -> None:
    retired = grants_by_service([member(state="retired", app_changes="granted")])
    decisions = judge(
        [("a record", payload())], io.StringIO(), grants=retired, portfolio=portfolio(project())
    )
    assert decisions == (NOT_WORKABLE,)


def test_an_operational_record_needs_no_grant() -> None:
    operational = {
        "profile": "non-software-operational",
        "package_id": "rotation-openrouter-generic",
        "enforcement_snapshot": {
            "profile_fields": {"standing": True},
            "reach": ["external_system"],
        },
    }
    decisions = judge(
        [("rotation", operational)], io.StringIO(), grants=NOT_GRANTED, portfolio=portfolio()
    )
    assert decisions == ("OPERATIONAL",)


def _unreadable(**_: object) -> list[Member]:
    raise MembersUnreadable("the member record is unreachable: ConnectError")


def test_an_unreadable_member_record_stops_the_pass_and_registers_nothing(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("work_carrier.cli.read_members_from_environment", _unreadable)
    writer = _Registrar()
    code, text = _run_registering(checkout_root, _record(), writer, None)
    assert code == EXIT_UNUSABLE
    assert writer.payloads == []
    assert "member record could not be read" in text
    assert "unreachable" in text


def test_an_unset_observer_token_stops_the_pass_and_registers_nothing(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SDS_MEMBERS_OBSERVER_TOKEN", raising=False)
    writer = _Registrar()
    code, text = _run_registering(checkout_root, _record(), writer, None)
    assert code == EXIT_UNUSABLE
    assert writer.payloads == []
    assert "SDS_MEMBERS_OBSERVER_TOKEN" in text


# -------------------------------------------------------------------------------------
# The launcher passes the credentials through
# -------------------------------------------------------------------------------------


def test_the_launcher_supplies_the_watchers_github_credential() -> None:
    """A variable the watcher reads and the launcher never exports is a silent `cannot assess`."""
    launcher = Path("scripts/run-work-carrier.sh").read_text(encoding="utf-8")
    assert "WORK_CARRIER_GITHUB_TOKEN" in launcher
    assert "gh auth token" in launcher


def test_the_launcher_still_runs_without_a_github_credential() -> None:
    """The read-only invocation is advertised to work anywhere; it must not gain a hard exit.

    Proven by running the launcher's own credential block under an environment with no
    `gh` on PATH, which is what a machine that never authenticated looks like.
    """
    script = Path("scripts/run-work-carrier.sh").read_text(encoding="utf-8")
    start = script.index('if [ -z "${WORK_CARRIER_GITHUB_TOKEN:-}" ]')
    block = script[start : script.index("\n\n", start)]
    result = subprocess.run(
        ["bash", "-c", f"set -uo pipefail\n{block}\necho rc=$?"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )
    assert result.returncode == 0
    assert "rc=0" in result.stdout
