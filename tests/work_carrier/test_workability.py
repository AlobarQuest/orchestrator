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

import httpx
import pytest

from work_carrier.change_manager import WorkRecord
from work_carrier.cli import EXIT_OK, run
from work_carrier.declaration import (
    FILENAME,
    Declaration,
    GitHubDeclarationSource,
    is_allowed,
    parse,
)
from work_carrier.prepare import emit_key
from work_carrier.workability import (
    CANNOT_DECIDE,
    NO,
    UNKNOWN,
    WOULD_CARRY,
    WOULD_REFUSE,
    YES,
    Portfolio,
    assess,
    load_portfolio,
    project_for,
    report,
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


class _Declares:
    def __init__(self, answer: Declaration) -> None:
        self._answer = answer
        self.asked: list[str] = []

    def declaration(self, slug: str) -> Declaration:
        self.asked.append(slug)
        return self._answer


DECLARED_TRUE = Declaration(True, "factory-target.toml declares factory_target = true")
DECLARED_FALSE = Declaration(False, "factory-target.toml declares factory_target = false")
UNREADABLE = Declaration(None, "github rejected the read of the declaration: 403")


# -------------------------------------------------------------------------------------
# The three constraints, composed
# -------------------------------------------------------------------------------------


def test_a_repository_that_opts_in_and_measures_clean_would_be_carried() -> None:
    verdict = assess(TARGET, DECLARED_TRUE, portfolio(project()))
    assert verdict.decision == WOULD_CARRY
    assert [c.verdict for c in verdict.constraints] == [YES, YES, YES]


def test_a_repository_that_declared_false_would_be_refused() -> None:
    verdict = assess(TARGET, DECLARED_FALSE, portfolio(project()))
    assert verdict.decision == WOULD_REFUSE
    assert verdict.constraints[0].verdict == NO


def test_a_capability_violation_would_be_refused_and_names_the_check() -> None:
    """The third outcome the live population cannot currently produce, constructed.

    Every repository the sweep measures reports `pass` on all four today, so a
    capability refusal has never been observed — which is exactly why it is built
    here rather than asserted to work.
    """
    rows = portfolio(project(**{"runner.caller": "violation"}))
    verdict = assess(TARGET, DECLARED_TRUE, rows)
    assert verdict.decision == WOULD_REFUSE
    capable = verdict.constraints[1]
    assert capable.verdict == NO
    assert "runner.caller" in capable.detail


def test_a_permission_violation_is_its_own_constraint_not_folded_into_capability() -> None:
    rows = portfolio(project(**{"factory.pat_access": "violation"}))
    verdict = assess(TARGET, DECLARED_TRUE, rows)
    assert verdict.decision == WOULD_REFUSE
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
    verdict = assess("AlobarQuest/never-heard-of-it", DECLARED_TRUE, portfolio(project()))
    assert verdict.decision == CANNOT_DECIDE
    assert verdict.decision != WOULD_REFUSE
    assert [c.verdict for c in verdict.constraints] == [YES, UNKNOWN, UNKNOWN]
    assert "lookup that found nothing" in verdict.constraints[1].detail


def test_two_projects_answering_to_one_name_cannot_be_decided_rather_than_picked() -> None:
    rows = portfolio(
        project(), {"name": "other", "path": "/tmp/infraops-mcp-server", "factory": []}
    )
    found, why = project_for(rows, TARGET)
    assert found is None
    assert "more than one project" in why
    assert assess(TARGET, DECLARED_TRUE, rows).decision == CANNOT_DECIDE


def test_a_repository_the_sweep_never_measured_cannot_be_decided() -> None:
    """An empty `factory` block is out of the sweep's scope, not a clean bill of health."""
    rows = portfolio(
        {"name": "infraops-mcp-server", "path": "/x/infraops-mcp-server", "factory": []}
    )
    verdict = assess(TARGET, DECLARED_TRUE, rows)
    assert verdict.decision == CANNOT_DECIDE
    assert "recorded no factory checks" in verdict.constraints[1].detail


def test_a_check_the_sweep_did_not_record_cannot_be_decided() -> None:
    rows = portfolio(project())
    rows.projects[0]["factory"] = [
        row for row in rows.projects[0]["factory"] if row["id"] != "factory.landing_known"
    ]
    verdict = assess(TARGET, DECLARED_TRUE, rows)
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
    verdict = assess(TARGET, DECLARED_TRUE, rows)
    assert verdict.decision == CANNOT_DECIDE
    assert verdict.constraints[2].verdict == UNKNOWN
    # NOT RECORDED, rather than recorded with a value nothing can read. Both reach UNKNOWN,
    # so asserting the verdict alone cannot tell them apart -- and a reader told
    # `factory.pat_access is None` goes looking for a check that answered, where one told the
    # sweep recorded nothing goes looking at the sweep.
    assert "recorded no factory.pat_access" in verdict.constraints[2].detail


def test_a_check_that_is_unknown_rather_than_passing_cannot_be_decided() -> None:
    rows = portfolio(project(**{"factory.landing_known": "unknown"}))
    verdict = assess(TARGET, DECLARED_TRUE, rows)
    assert verdict.decision == CANNOT_DECIDE
    assert verdict.constraints[1].verdict == UNKNOWN


def test_an_unreadable_declaration_cannot_be_decided_and_is_not_read_as_absence() -> None:
    verdict = assess(TARGET, UNREADABLE, portfolio(project()))
    assert verdict.decision == CANNOT_DECIDE
    assert verdict.constraints[0].verdict == UNKNOWN


# -------------------------------------------------------------------------------------
# Composition: a definite NO decides; no combination of UNKNOWNs can
# -------------------------------------------------------------------------------------


def test_a_definite_refusal_beside_an_unknown_still_refuses() -> None:
    """Three-valued AND. A repository that answered `false` answered, whatever else is unknown.

    The property that matters is the ASYMMETRY, and both halves are asserted here:
    a NO decides through an UNKNOWN, and UNKNOWNs alone never produce a refusal.
    """
    verdict = assess("AlobarQuest/never-heard-of-it", DECLARED_FALSE, portfolio(project()))
    assert verdict.decision == WOULD_REFUSE


def test_no_combination_of_unknowns_produces_a_refusal() -> None:
    verdict = assess("AlobarQuest/never-heard-of-it", UNREADABLE, portfolio())
    assert verdict.decision == CANNOT_DECIDE
    assert all(c.verdict == UNKNOWN for c in verdict.constraints)


def test_a_payload_naming_no_target_repository_is_nothing_to_judge_not_a_refusal() -> None:
    verdict = assess(None, Declaration(None, "no repository to ask about"), portfolio(project()))
    assert verdict.decision == CANNOT_DECIDE
    assert verdict.repository is None


# -------------------------------------------------------------------------------------
# `factory.pat_scope` rides in the report and never disqualifies
# -------------------------------------------------------------------------------------


def test_pat_scope_being_permanently_unknown_does_not_stop_a_repository_being_carried() -> None:
    """Its own check says it is ALWAYS unknown, so a term requiring it passes nothing, ever."""
    rows = portfolio(project(**{"factory.pat_scope": "unknown"}))
    assert assess(TARGET, DECLARED_TRUE, rows).decision == WOULD_CARRY


def test_the_workflow_residual_is_named_on_every_repository_that_would_be_carried() -> None:
    """Not disqualifying is not the same as not mattering, and the output must say so."""
    out = io.StringIO()
    report(
        [("change record 1", payload())],
        out,
        source=_Declares(DECLARED_TRUE),
        portfolio=portfolio(project()),
    )
    text = out.getvalue()
    assert WOULD_CARRY in text
    assert "pat_scope" in text
    assert ".github/workflows/**" in text


def test_the_residual_is_not_claimed_about_a_repository_that_would_not_be_carried() -> None:
    out = io.StringIO()
    report(
        [("change record 1", payload())],
        out,
        source=_Declares(DECLARED_FALSE),
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
    report([], out, source=_Declares(DECLARED_TRUE), portfolio=portfolio(age_seconds=7200))
    assert "2h old" in out.getvalue()


def test_a_missing_capability_source_is_named_rather_than_read_as_no_capability(
    tmp_path: Path,
) -> None:
    loaded = load_portfolio(tmp_path / "absent.json")
    assert loaded.projects == ()
    assert "could not be read" in loaded.detail
    assert assess(TARGET, DECLARED_TRUE, loaded).decision == CANNOT_DECIDE


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


class _Raises:
    def declaration(self, slug: str) -> Declaration:
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
    report_into: io.StringIO | None = None,
    source=None,
) -> tuple[int, str]:
    """Run the carry, sending the report's own output somewhere else.

    THE DIFFERENTIAL IS TOTAL BECAUSE OF THIS, and a filter would not be. Subtracting
    the report's lines from one stream means knowing every shape it can print: a shape
    added later would start counting as a carry line, and a carry line the report had
    suppressed would be subtracted with it and the comparison would pass. Routing the
    report to its own buffer leaves `out` carrying exactly what the carry wrote, so the
    two runs are compared byte for byte with nothing excluded.
    """
    if report_into is None:
        monkeypatch.setattr("work_carrier.cli.report_workability", lambda *a, **k: None)
    else:
        monkeypatch.setattr(
            "work_carrier.cli.report_workability",
            lambda subjects, out: report(
                subjects, report_into, source=source, portfolio=portfolio(project())
            ),
        )
    # The record must PREPARE, or the report short-circuits on an empty subject list and the
    # differential proves nothing about the path that reads a declaration. The emitter is
    # injected the way this package's other end-to-end tests inject it.
    rec = _record()
    import work_carrier.cli as cli_module
    import work_carrier.prepare as prepare_module

    original = prepare_module.prepare
    monkeypatch.setattr(
        cli_module,
        "prepare",
        lambda r, **kw: original(r, **kw, runner=_emitting(_emitted(r))),
    )
    out = io.StringIO()
    code = run(["--checkout-root", str(root)], source=_Source([rec]), out=out)
    return code, out.getvalue()


def test_the_report_changes_neither_the_exit_code_nor_anything_the_carry_printed(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The differential that makes this increment safe to land.

    Reported and not-reported are run over the same queue and their carry streams
    compared byte for byte. The record here is REFUSED by the real emitter, so the pass
    prepares and is not registered, so the pass exits 0 — and the sibling below runs the
    same differential with a report whose reader raises, which is the direction a report
    would most plausibly move an exit code in.
    """
    block = io.StringIO()
    reported_code, reported = _carry(
        checkout_root, monkeypatch, report_into=block, source=_Declares(DECLARED_TRUE)
    )
    silent_code, silent = _carry(checkout_root, monkeypatch)

    assert reported_code == silent_code == EXIT_OK
    assert reported == silent
    assert reported != "", "the differential compared two empty streams"
    assert "[WORKABILITY]" in block.getvalue()


def test_a_report_whose_reader_raises_changes_nothing_and_says_so(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report that could raise could change the exit code. This one cannot.

    Same differential with a reader that raises: the carry stream must be unchanged,
    and the report must SAY it failed rather than print a verdict it did not compute.
    """
    block = io.StringIO()
    broken_code, broken = _carry(checkout_root, monkeypatch, report_into=block, source=_Raises())
    silent_code, silent = _carry(checkout_root, monkeypatch)

    assert broken_code == silent_code == EXIT_OK
    assert broken == silent
    assert broken != "", "the differential compared two empty streams"
    assert "failed and changed nothing" in block.getvalue()
    assert WOULD_CARRY not in block.getvalue()
    assert WOULD_REFUSE not in block.getvalue()


def test_the_carry_really_does_print_the_report_into_its_own_output(
    checkout_root: Path,
) -> None:
    """The wiring, unpatched — or the differential above would be about nothing.

    Both tests above replace `report_workability`, so neither of them can see that the
    carry calls it at all. This one runs the real path and looks for the block in the
    stream a person reads.
    """
    out = io.StringIO()
    run(["--checkout-root", str(checkout_root)], source=_Source([_record()]), out=out)
    assert "[WORKABILITY]" in out.getvalue()


def test_an_empty_queue_still_says_the_check_ran(checkout_root: Path) -> None:
    """Silence would be indistinguishable from the report having been removed."""
    out = io.StringIO()
    code = run(["--checkout-root", str(checkout_root)], source=_Source([]), out=out)
    assert code == EXIT_OK
    assert "no repository was judged" in out.getvalue()


def test_the_report_asks_about_the_package_target_and_never_the_records_repository(
    checkout_root: Path,
) -> None:
    """A `work` record's `package_source_repository` is always the packages repository.

    Measured against production 2026-09-11: `target_repository` is null on all seven
    work records and `package_source_repository` is `AlobarQuest/intent-packages` on
    every one. Keying on either would judge every carry against a repository that opts
    in and measures clean, so the check would pass everything forever.
    """
    asked = _Declares(DECLARED_TRUE)
    report([("change record 1", payload())], io.StringIO(), source=asked, portfolio=portfolio())
    assert asked.asked == [TARGET]
    assert "AlobarQuest/intent-packages" not in asked.asked


# -------------------------------------------------------------------------------------
# Reading the declaration itself
# -------------------------------------------------------------------------------------


def test_a_declaration_is_read_as_the_repositorys_own_answer() -> None:
    answer = parse('factory_target = true\nfactory_target_reason = "a target"\n')
    assert answer.target is True


def test_a_declaration_that_says_false_is_a_different_answer_from_one_that_cannot_be_read() -> None:
    assert parse('factory_target = false\nfactory_target_reason = "no"\n').target is False
    assert parse("factory_target = not-toml [[[").target is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        'factory_target = "true"\nfactory_target_reason = "x"\n',
        "factory_target = true\n",
        'factory_target = true\nfactory_target_reason = "   "\n',
    ],
)
def test_a_declaration_missing_either_key_or_carrying_the_wrong_type_cannot_be_read(
    text: str,
) -> None:
    """Both keys are required, including the reason: a bare `true` becomes folklore."""
    assert parse(text).target is None


def _routed(
    contents: httpx.Response | Exception, repository: httpx.Response | Exception
) -> tuple[httpx.MockTransport, list[str]]:
    """A transport that answers the two routes differently, and records what was asked.

    The two are told apart by PATH rather than by call order, so a test cannot pass
    because the reader happened to make the requests in the order the stub expected --
    and the recorded list is what makes "the probe was never made" assertable.
    """

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        answer = repository if request.url.path.endswith("/brain") else contents
        if isinstance(answer, Exception):
            raise answer
        return answer

    return httpx.MockTransport(handler), seen


def _reader(transport: httpx.MockTransport) -> GitHubDeclarationSource:
    return GitHubDeclarationSource(
        token="t", base_url="https://example.invalid", transport=transport
    )


def test_an_absent_declaration_is_an_answer_once_the_repository_itself_answers() -> None:
    """GitHub answers 404 three ways, and this is the one branch that returns a definite NO.

    A definite NO decides through every UNKNOWN beside it, so the two states that are
    otherwise indistinguishable from "has not opted in" -- a repository that does not
    exist, and a private one this credential may not read -- are settled by one read of
    the repository itself rather than named and left to the reader. This is the case
    where that read ANSWERS, so the absence is the repository's own silence.
    """

    transport, seen = _routed(httpx.Response(404, json={}), httpx.Response(200, json={}))
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is False
    assert "has not opted in" in answer.detail
    assert seen == [
        "/repos/AlobarQuest/brain/contents/factory-target.toml",
        "/repos/AlobarQuest/brain",
    ]


@pytest.mark.parametrize(
    ("repository_status", "expected"),
    [
        (404, "does not exist or one this credential cannot see"),
        (403, "not an answer either way"),
        (429, "transient"),
        (500, "transient"),
        (502, "transient"),
        (301, "renamed or moved"),
    ],
)
def test_an_absence_the_repository_does_not_confirm_is_could_not_tell(
    repository_status: int, expected: str
) -> None:
    """The behaviour change, and the whole reason the probe exists.

    A slug nobody should have asked about and a private repository this credential
    cannot see both 404 on the contents route, and both used to come back as a definite
    "has not opted in" -- a refusal manufactured from a typo or a permission fault. The
    repository does not answer, so there is no absence to report.

    **THE VERDICT IS ONE THING AND THE DIAGNOSIS IS SEVERAL, and the parametrization
    says so rather than pinning one sentence over all of them.** Every row answers
    `None`; a `429` or a `502` is this estate's ordinary weather, and telling a reader
    the repository does not exist would send him to check a name that is fine. That is
    the same wrong-diagnosis defect this module exists to remove, one branch over.
    """
    transport, seen = _routed(
        httpx.Response(404, json={}), httpx.Response(repository_status, json={})
    )
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is None
    assert expected in answer.detail
    assert len(seen) == 2


def test_a_confirmed_absence_does_not_claim_the_credential_could_have_read_the_file() -> None:
    """The one state the probe still cannot tell from an absence, named in the line.

    `GET /repos/{slug}` needs only metadata; the contents route needs contents, and they
    are separate permissions -- so a credential holding the first and not the second
    answers 200 to the probe and 404 above for a PRIVATE repository, which is exactly the
    shape of a confirmed absence. It is left that way deliberately: project-standards'
    `_remote_contents` confirms the same fact by the same request, and a reader that grew
    a third probe on its own would be the two drifting rather than the hole closing. What
    must not happen is the LINE claiming more than was proved.
    """
    transport, _ = _routed(httpx.Response(404, json={}), httpx.Response(200, json={}))
    detail = _reader(transport).declaration("AlobarQuest/brain").detail
    assert "does NOT prove" in detail
    assert "CONTENTS" in detail


def test_a_redirect_on_the_declaration_is_a_rename_rather_than_a_malformed_file() -> None:
    """A renamed repository used to be reported as a file that exists and is broken.

    Redirects are not followed, so a 301 delivers GitHub's redirect envelope rather than
    the file -- and it fell past both the 404 branch and the >= 400 branch into the
    parser, which said `factory_target` must be a bool and got None. The verdict was
    already "could not tell"; this asserts the sentence is true as well, and that the
    reader did not go on to probe a repository it had been told nothing about.
    """
    transport, seen = _routed(httpx.Response(301, json={}), httpx.Response(200, json={}))
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is None
    assert "renamed or moved" in answer.detail
    assert "factory_target" not in answer.detail
    assert seen == ["/repos/AlobarQuest/brain/contents/factory-target.toml"]


def test_the_probe_can_derive_its_path_only_because_the_guard_ends_at_the_file() -> None:
    """The coupling that makes the derivation safe, asserted rather than assumed.

    `_confirm_absence` strips `/contents/<file>` off a path `is_allowed` has admitted,
    and `str.removesuffix` is a NO-OP when the suffix is absent -- so widening the route
    guard past the file (a nested path, an optional directory segment) would silently
    make the probe re-ask the contents route or ask for a directory, turning every
    genuine absence into "could not tell" with nothing red. This reds on that edit, which
    is what a second unfalsifiable pattern beside the first could not do.
    """
    from work_carrier.declaration import _ALLOWED

    assert _ALLOWED.pattern.endswith(re.escape(f"/contents/{FILENAME}") + "$")


def test_an_unreachable_repository_probe_is_could_not_tell_and_prints_no_request() -> None:
    """The probe's own failure is the third state, not the absence by default."""

    transport, _ = _routed(httpx.Response(404, json={}), httpx.ConnectError("no route"))
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is None
    assert "ConnectError" in answer.detail
    assert "no route" not in answer.detail


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param(None, id="transport-raised"),
        pytest.param(500, id="server-error"),
        pytest.param(429, id="rate-limited"),
    ],
)
def test_a_transient_contents_failure_never_becomes_an_absence(contents: int | None) -> None:
    """THE ORDERING RULE, and it is asserted by the probe NOT HAVING BEEN MADE.

    project-standards' `_remote_contents` tests its diagnostic for 404 FIRST for this
    reason: a contents call that failed for any other reason says nothing about whether
    the file is there, and a repository probe that succeeds a moment later must not be
    allowed to turn it into an absence. The repository here would answer 200 -- so a
    reader that probed first, or probed on every failure, would report `False`.
    """

    failure = httpx.ConnectError("no route") if contents is None else httpx.Response(contents)
    transport, seen = _routed(failure, httpx.Response(200, json={}))
    answer = _reader(transport).declaration("AlobarQuest/brain")
    assert answer.target is None
    assert seen == ["/repos/AlobarQuest/brain/contents/factory-target.toml"]


def test_the_probe_asks_the_bare_repository_route_and_nothing_else() -> None:
    """What the probe sends, and that a refused slug sends nothing at all.

    It does NOT discriminate the derivation, and saying so is the point: behind the
    route guard, composing `/repos/{slug}` afresh would build the same request for every
    slug that can reach the probe, so a control claiming otherwise would pass either way.
    The derivation is chosen so that argument never has to be made -- on the repository
    route a slug carrying a query builds an admissible PATH with the rest in the query,
    which a path-only check admits, and the contents route being checked first is the
    only reason that cannot happen here.
    """

    transport, seen = _routed(httpx.Response(404, json={}), httpx.Response(404, json={}))
    assert _reader(transport).declaration("AlobarQuest/brain?ref=other").target is None
    assert seen == []

    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(404 if "contents" in request.url.path else 200, json={})

    assert _reader(httpx.MockTransport(handler)).declaration("AlobarQuest/brain").target is False
    assert asked[1].url.path == "/repos/AlobarQuest/brain"
    assert asked[1].url.query == b""
    assert asked[1].headers["accept"] == "application/vnd.github+json"


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_a_github_refusal_is_could_not_tell_rather_than_has_not_opted_in(status: int) -> None:

    source = GitHubDeclarationSource(
        token="t",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={})),
    )
    assert source.declaration("AlobarQuest/brain").target is None


def test_an_unreachable_github_is_could_not_tell_rather_than_a_traceback() -> None:
    """`httpx` raises three unrelated families and the third is a `ValueError`."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    source = GitHubDeclarationSource(
        token="t", base_url="https://example.invalid", transport=httpx.MockTransport(refuse)
    )
    answer = source.declaration("AlobarQuest/brain")
    assert answer.target is None
    assert "unreachable" in answer.detail
    assert "no route" not in answer.detail


def test_a_malformed_host_is_could_not_tell_rather_than_a_unicode_error() -> None:
    """IDNA encoding of a doubled dot raises `UnicodeError`, which is neither httpx family."""
    source = GitHubDeclarationSource(token="t", base_url="https://api..github.invalid")
    answer = source.declaration("AlobarQuest/brain")
    assert answer.target is None
    assert "unreachable" in answer.detail


def test_the_reader_asks_for_the_default_branch_and_the_raw_bytes() -> None:
    """No `ref`, so the branch asked about is the one a change would land on."""

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text='factory_target = true\nfactory_target_reason = "x"\n')

    source = GitHubDeclarationSource(
        token="t", base_url="https://example.invalid", transport=httpx.MockTransport(handler)
    )
    assert source.declaration("AlobarQuest/brain").target is True
    assert seen[0].url.params.get("ref") is None
    assert seen[0].headers["accept"] == "application/vnd.github.raw"
    assert is_allowed(seen[0].url.path)


def test_the_declaration_is_read_from_github_rather_than_from_a_checkout(tmp_path: Path) -> None:
    """A checkout is not a fresh source and must not become one by accident.

    Measured 2026-09-11: seven of eight working trees on this machine did not carry
    `factory-target.toml` hours after every declaration had landed, because nothing
    pulls them — so a reader that fell back to the filesystem would report the five
    repositories that declare `true` as not having opted in. The module names one
    route, and this asserts it is a URL rather than a path.
    """
    source = Path("src/work_carrier/declaration.py").read_text(encoding="utf-8")
    assert "api.github.com" in source
    for filesystem in ("Path(", "open(", "read_text", "is_file", "exists()"):
        assert filesystem not in source, filesystem


def test_the_credential_is_read_from_the_environment_and_its_absence_is_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine with no GitHub credential reports "could not tell", never "not a target"."""
    monkeypatch.delenv("WORK_CARRIER_GITHUB_TOKEN", raising=False)
    out = io.StringIO()
    report([("change record 1", payload())], out, portfolio=portfolio(project()))
    text = out.getvalue()
    assert CANNOT_DECIDE in text
    assert WOULD_REFUSE not in text
    assert "WORK_CARRIER_GITHUB_TOKEN" in text


def test_a_credential_in_the_environment_is_never_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORK_CARRIER_GITHUB_TOKEN", "ghp-not-a-real-secret-0123456789")
    out = io.StringIO()
    report([], out, portfolio=portfolio())
    assert "ghp-not-a-real-secret-0123456789" not in out.getvalue()


def test_a_credential_that_makes_the_reader_raise_is_never_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ASCII case above cannot see this, and it is the one that reaches a log.

    A token carrying a non-ASCII byte makes `httpx` raise while it encodes the
    `Authorization` header -- inside the report's own broad guard, whose handler is
    therefore holding an exception whose repr is `Bearer <token>` verbatim. The control
    asserts the FAILURE PATH was taken as well as that the value is absent, or a report
    that quietly succeeded would satisfy it.
    """
    secret = "ghp-not-a-real-secret-\u00ff\u0100"
    monkeypatch.setenv("WORK_CARRIER_GITHUB_TOKEN", secret)
    out = io.StringIO()
    report([("a record", payload(TARGET))], out, portfolio=portfolio(project()))
    text = out.getvalue()
    assert "failed and changed nothing" in text, text
    assert secret not in text
    assert "ghp-not-a-real-secret" not in text
    assert "Bearer" not in text


def test_one_unjudgeable_subject_does_not_silence_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard one level too high makes the PASS total and leaves the REPORT partial.

    The reader raises for one repository and answers for the other, so a guard wrapped
    round the whole loop prints a single failure line and drops the second subject
    entirely -- indistinguishable, to a reader, from a queue that was shorter. Both
    halves are asserted: the failure is named AND the survivor is still judged.
    """

    class _RaisesFor:
        def declaration(self, slug: str) -> Declaration:
            if slug == "AlobarQuest/broken":
                raise RuntimeError("the reader is broken")
            return Declaration(True, "factory-target.toml declares factory_target = true")

    out = io.StringIO()
    report(
        [("first", payload("AlobarQuest/broken")), ("second", payload(TARGET))],
        out,
        source=_RaisesFor(),
        portfolio=portfolio(project()),
    )
    text = out.getvalue()
    assert "judging first failed and changed nothing" in text, text
    assert "RuntimeError" in text, text
    assert WOULD_CARRY in text, text
    assert "second" in text, text


# -------------------------------------------------------------------------------------
# The launcher passes the credential through
# -------------------------------------------------------------------------------------


def test_the_launcher_supplies_the_declaration_credential() -> None:
    """A variable the program reads and the launcher never exports is a silent `cannot decide`."""
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
