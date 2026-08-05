"""Negative controls for the measurement layer of the wave-exit manifest.

`scripts/attest_wave_exit.py` decides `pass | fail | not_applicable | unavailable` for every
clause of every exit bar, and it decides almost all of them by running a probe from
`scripts/exit_probe.py`. Until WS-P2.40 nothing tested those probes, so the component that judges
everything else did not meet the bar it enforces.

**The bar here is not "a test exists" -- it is "the test fails when the probe is wrong."** Every
control below breaks the WORLD the probe reads: an API payload missing the key the probe asserts
on, a ledger with no matching unit, a conformance kit reporting one repository short. Breaking the
probe instead would only test the edit, so where a probe's machinery is a subprocess (pytest,
`gh`, the kit) it is given a different tree and a different executable, never a patched call.

Two shapes get particular attention, because they are where a probe silently passes:

* **the empty population** -- a probe that iterates and asserts "none violated" passes vacuously
  on zero items, and several of these read production collections that can legitimately be empty;
* **the absent key** -- a probe reading a field the response no longer carries must report
  `unavailable`, never `pass` and never `fail`. That distinction is the whole manifest.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import exit_probe
from scripts.exit_probe import FAIL, PASS, UNAVAILABLE, Unavailable

# ---------------------------------------------------------------------------------------------
# world-building helpers
# ---------------------------------------------------------------------------------------------


def _api(routes: dict[str, object]):
    """A production read surface serving exactly `routes`.

    Anything the probe asks for that this world does not serve raises `Unavailable`, the way the
    real `api_get` reports an HTTP error -- so a probe that wanders off the paths its clause is
    about reports unmeasurable rather than quietly reading `None`.
    """

    def get(path: str):
        if path not in routes:
            raise Unavailable(f"GET {path} answered HTTP 404")
        return routes[path]

    return get


def _ledger_row(unit_id: str, state: str, **overrides) -> dict:
    row = {
        "unit_id": unit_id,
        "unit_key": f"unit-{unit_id}",
        "unit_state": state,
        "actor_id": "factory-runner",
        "last_event_at": "2026-08-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _world(rows: list[dict], packs: dict[str, dict], **routes):
    """The two surfaces most probes read: the ledger, and a per-unit evidence pack."""
    served: dict[str, object] = {"/api/v1/status-ledger?include_inactive=true": rows}
    for unit_id, pack in packs.items():
        served[f"/api/v1/work-units/{unit_id}/evidence-pack"] = pack
    served.update(routes)
    return _api(served)


def _pack(*, adjudications: list | None = None, capabilities: dict | None = None, **extra) -> dict:
    pack: dict = {
        "adjudications": (
            [{"current": True, "outcome": "passed"}] if adjudications is None else adjudications
        ),
        "evidence": [],
        "authority": {"envelope": {"capabilities": capabilities or {}}},
    }
    pack.update(extra)
    return pack


def _executable(path: Path, body: str) -> Path:
    """Write a real executable into the world, so the probe runs it rather than a stub of itself."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_pytest(root: Path, exit_code: int) -> None:
    _executable(
        root / ".venv/bin/pytest", f"print('summary line')\nraise SystemExit({exit_code})\n"
    )


def _fake_gh(root: Path, monkeypatch, body: str) -> None:
    """Put a `gh` on PATH that answers however the world is supposed to answer today."""
    _executable(root / "bin/gh", body)
    monkeypatch.setenv("PATH", f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}")


_DICT_HOPS = ("intent", "unit", "pr")


def _chain(unit_key: str, **present: bool) -> dict:
    """One traceability chain, with every hop populated unless the caller empties it."""
    chain: dict = {}
    for hop in exit_probe.ALL_HOPS:
        carried = present.get(hop, True)
        if hop in _DICT_HOPS:
            chain[hop] = {"placeholder": unit_key} if carried else None
        else:
            chain[hop] = [{"placeholder": unit_key}] if carried else []
    chain["unit"] = {"unit_key": unit_key, "id": unit_key}
    return chain


# =============================================================================================
# Wave 1 clause 1 -- drills_are_scripted
# =============================================================================================


def _drill_world(root: Path, drills: int, pattern: str = "scripts/drill-*.sh") -> None:
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for index in range(1, drills + 1):
        (root / f"scripts/drill-{index}-x.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "scripts/run-drills.sh").write_text(
        f'#!/bin/sh\nfor drill in {pattern}; do\n  "$drill"\ndone\n', encoding="utf-8"
    )


def test_drills_probe_passes_on_the_world_it_is_meant_to_pass_on(tmp_path, monkeypatch):
    _drill_world(tmp_path, drills=5)
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.drills_are_scripted() == PASS


def test_drills_probe_fails_when_a_drill_is_missing(tmp_path, monkeypatch):
    """Four drills is the exact shape the plan's '5 drills' annotation would misreport."""
    _drill_world(tmp_path, drills=4)
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.drills_are_scripted() == FAIL


def test_drills_probe_fails_when_the_runner_glob_cannot_reach_every_drill(tmp_path, monkeypatch):
    """Five scripts on disk that the harness never runs is the failure this clause is about."""
    _drill_world(tmp_path, drills=5, pattern="scripts/drill-1-*.sh")
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.drills_are_scripted() == FAIL


def test_drills_probe_fails_when_the_runner_is_absent(tmp_path, monkeypatch):
    _drill_world(tmp_path, drills=5)
    (tmp_path / "scripts/run-drills.sh").unlink()
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.drills_are_scripted() == FAIL


def test_drills_probe_fails_when_the_runner_iterates_nothing(tmp_path, monkeypatch):
    """A harness that no longer loops over drills cannot be reaching them, however it is named."""
    _drill_world(tmp_path, drills=5)
    (tmp_path / "scripts/run-drills.sh").write_text("#!/bin/sh\necho done\n", encoding="utf-8")
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.drills_are_scripted() == FAIL


def test_drills_probe_does_not_pass_on_an_empty_population(tmp_path, monkeypatch):
    """Zero drills reached by a runner that reaches zero drills is a vacuous agreement."""
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts/run-drills.sh").write_text(
        '#!/bin/sh\nfor d in scripts/drill-*.sh; do "$d"; done\n', encoding="utf-8"
    )
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.drills_are_scripted() == FAIL


# =============================================================================================
# Wave 1 clause 2 -- slo_report_runs
# =============================================================================================


def _slo(payload: dict):
    return _api({"/api/v1/slo-report": payload})


def test_slo_probe_passes_when_at_least_one_metric_is_computed(monkeypatch):
    monkeypatch.setattr(exit_probe, "api_get", _slo({"lead_time": {"status": "computed"}}))

    assert exit_probe.slo_report_runs() == PASS


def test_slo_probe_fails_when_the_report_computes_nothing(monkeypatch):
    """Served but computing nothing is the report NOT running -- the clause's actual subject."""
    monkeypatch.setattr(
        exit_probe,
        "api_get",
        _slo({"lead_time": {"status": "not_instrumented"}, "budget_breach": {"status": "empty"}}),
    )

    assert exit_probe.slo_report_runs() == FAIL


def test_slo_probe_is_unavailable_when_the_report_carries_no_metric_shape(monkeypatch):
    """The absent-key shape: a response with no `status`-bearing metric was never measured.

    Reporting `fail` here would answer "the SLO report does not run" from a fact about the
    response schema -- the tool's central distinction inverted, one layer down.
    """
    monkeypatch.setattr(exit_probe, "api_get", _slo({"since": "x", "until": "y"}))

    with pytest.raises(Unavailable):
        exit_probe.slo_report_runs()


def test_slo_probe_is_unavailable_when_production_cannot_be_read(monkeypatch):
    monkeypatch.setattr(exit_probe, "api_get", _api({}))

    with pytest.raises(Unavailable):
        exit_probe.slo_report_runs()


# =============================================================================================
# Wave 1 clause 3 -- completed_units_carry_adjudications / required_criteria_are_readable
# =============================================================================================


def test_adjudication_probe_passes_when_every_completed_unit_carries_one(monkeypatch):
    rows = [_ledger_row("u1", "completed"), _ledger_row("u2", "cancelled")]
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {"u1": _pack()}))

    assert exit_probe.completed_units_carry_adjudications() == PASS


def test_adjudication_probe_fails_on_a_completed_unit_with_no_current_adjudication(monkeypatch):
    rows = [_ledger_row("u1", "completed")]
    stale = _pack(adjudications=[{"current": False, "outcome": "passed"}])
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {"u1": stale}))

    assert exit_probe.completed_units_carry_adjudications() == FAIL


def test_adjudication_probe_fails_on_an_adjudication_with_no_outcome(monkeypatch):
    rows = [_ledger_row("u1", "completed")]
    hollow = _pack(adjudications=[{"current": True, "outcome": None}])
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {"u1": hollow}))

    assert exit_probe.completed_units_carry_adjudications() == FAIL


def test_adjudication_probe_is_unavailable_on_an_empty_population(monkeypatch):
    """No completed unit is nothing to measure, never 'no completed unit finished bare'."""
    monkeypatch.setattr(exit_probe, "api_get", _world([_ledger_row("u1", "ready")], {}))

    with pytest.raises(Unavailable):
        exit_probe.completed_units_carry_adjudications()


def test_adjudication_probe_is_unavailable_when_the_pack_stops_serving_adjudications(monkeypatch):
    """An absent key must not read as 'this unit completed with nothing adjudicated'."""
    rows = [_ledger_row("u1", "completed")]
    reshaped = {"evidence": [], "authority": {"envelope": {}}}
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {"u1": reshaped}))

    with pytest.raises(Unavailable):
        exit_probe.completed_units_carry_adjudications()


def test_required_criteria_probe_is_unavailable_while_no_criteria_key_is_served(monkeypatch):
    rows = [_ledger_row("u1", "completed")]
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {"u1": _pack()}))

    with pytest.raises(Unavailable, match="no production read surface"):
        exit_probe.required_criteria_are_readable()


def test_required_criteria_probe_does_not_pass_merely_because_a_criteria_key_appeared(monkeypatch):
    """The documented fail-open this probe was written against, pinned.

    A release that adds any `criteria`-ish key must not flip this clause green while nothing has
    ever compared the required set against the adjudicated one -- that is a probe that silently
    removes itself.
    """
    rows = [_ledger_row("u1", "completed")]
    generous = _pack(acceptance_criteria=[{"ac_id": "AC-001"}])
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {"u1": generous}))

    with pytest.raises(Unavailable, match="not implemented here"):
        exit_probe.required_criteria_are_readable()


def test_required_criteria_probe_is_unavailable_on_an_empty_population(monkeypatch):
    monkeypatch.setattr(exit_probe, "api_get", _world([], {}))

    with pytest.raises(Unavailable):
        exit_probe.required_criteria_are_readable()


# =============================================================================================
# Wave 1 clause 4 -- budget_breach_is_instrumented
# =============================================================================================


def test_budget_probe_passes_on_an_instrumented_counter_reading_zero(monkeypatch):
    """Zero in the window means quiet, not broken -- the probe must not require a breach."""
    monkeypatch.setattr(
        exit_probe, "api_get", _slo({"budget_breach": {"status": "computed", "value": 0}})
    )

    assert exit_probe.budget_breach_is_instrumented() == PASS


def test_budget_probe_fails_when_the_counter_is_not_instrumented(monkeypatch):
    monkeypatch.setattr(
        exit_probe, "api_get", _slo({"budget_breach": {"status": "not_instrumented"}})
    )

    assert exit_probe.budget_breach_is_instrumented() == FAIL


def test_budget_probe_fails_when_the_report_serves_metrics_but_not_this_one(monkeypatch):
    """A report that computes other things and not this one genuinely is not instrumented."""
    monkeypatch.setattr(exit_probe, "api_get", _slo({"lead_time": {"status": "computed"}}))

    assert exit_probe.budget_breach_is_instrumented() == FAIL


def test_budget_probe_is_unavailable_when_the_report_has_no_metric_shape_at_all(monkeypatch):
    """Distinguishing 'not instrumented' from 'the report is not the report any more'."""
    monkeypatch.setattr(exit_probe, "api_get", _slo({"since": "x"}))

    with pytest.raises(Unavailable):
        exit_probe.budget_breach_is_instrumented()


def test_budget_probe_is_unavailable_when_the_metric_itself_loses_its_status(monkeypatch):
    """The absent key one level in, and it fails OPEN rather than closed -- so it needs its own
    control.

    Guarding the report and not the metric leaves `metric.get("status") != "not_instrumented"`
    reading `None` as instrumented: a PASS for something never observed, in the probe written to
    keep exactly that distinction. Found by adversarial review of this branch.
    """
    monkeypatch.setattr(
        exit_probe,
        "api_get",
        _slo({"lead_time": {"status": "computed"}, "budget_breach": {"value": 3}}),
    )

    with pytest.raises(Unavailable):
        exit_probe.budget_breach_is_instrumented()


# =============================================================================================
# Wave 1 clause 5 -- lifecycle_guards_are_wired
# =============================================================================================


def _guard_world(root: Path, *, guard_present: bool = True, pytest_exit: int | None = 0) -> None:
    if guard_present:
        target = root / "tests/architecture/test_unreachable_guards.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_guard():\n    pass\n", encoding="utf-8")
    if pytest_exit is not None:
        _fake_pytest(root, pytest_exit)


def test_guard_probe_passes_when_the_guard_runs_green(tmp_path, monkeypatch):
    _guard_world(tmp_path)
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.lifecycle_guards_are_wired() == PASS


def test_guard_probe_fails_when_the_guard_runs_red(tmp_path, monkeypatch):
    _guard_world(tmp_path, pytest_exit=1)
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.lifecycle_guards_are_wired() == FAIL


def test_guard_probe_fails_when_the_guard_collects_nothing(tmp_path, monkeypatch):
    """pytest exit 5 is 'no tests collected' -- the repo-wide trap, pinned closed here."""
    _guard_world(tmp_path, pytest_exit=5)
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.lifecycle_guards_are_wired() == FAIL


def test_guard_probe_fails_when_the_guard_this_clause_names_is_gone(tmp_path, monkeypatch):
    _guard_world(tmp_path, guard_present=False)
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    assert exit_probe.lifecycle_guards_are_wired() == FAIL


def test_guard_probe_is_unavailable_without_an_interpreter_to_run_it(tmp_path, monkeypatch):
    _guard_world(tmp_path, pytest_exit=None)
    monkeypatch.setattr(exit_probe, "REPO_ROOT", tmp_path)

    with pytest.raises(Unavailable):
        exit_probe.lifecycle_guards_are_wired()


# =============================================================================================
# Wave 2 clause 1 -- evidence_pack_reached_a_merged_pr
# =============================================================================================


def test_marker_probe_passes_when_a_repository_reports_a_marker(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, "print(2)\n")

    assert exit_probe.evidence_pack_reached_a_merged_pr("AlobarQuest/x") == PASS


def test_marker_probe_fails_when_every_repository_reports_zero(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, "print(0)\n")

    assert exit_probe.evidence_pack_reached_a_merged_pr("AlobarQuest/x", "AlobarQuest/y") == FAIL


def test_marker_probe_does_not_report_a_verdict_on_an_empty_repository_list():
    """Iterating nothing invites a vacuous pass; answering `fail` is the other wrong answer.

    A manifest that names no repository is an authoring fact, not the marker being absent, so
    the honest answer is that nothing was counted -- matching what every sibling probe does with
    an empty argument list.
    """
    with pytest.raises(Unavailable, match="no repository was declared"):
        exit_probe.evidence_pack_reached_a_merged_pr()


def test_marker_probe_is_unavailable_when_gh_refuses(tmp_path, monkeypatch):
    _fake_gh(
        tmp_path,
        monkeypatch,
        "import sys\nsys.stderr.write('gh: bad credentials\\n')\nraise SystemExit(1)\n",
    )

    with pytest.raises(Unavailable):
        exit_probe.evidence_pack_reached_a_merged_pr("AlobarQuest/x")


def test_marker_probe_is_unavailable_when_gh_returns_no_count(tmp_path, monkeypatch):
    """A zero must come from a page that reported zero, never from output nothing parsed.

    This is the estate rule that a search zero is not evidence of absence, made mechanical: `gh`
    exiting 0 with nothing on stdout would otherwise sum to zero and report the clause missed.
    """
    _fake_gh(tmp_path, monkeypatch, "pass\n")

    with pytest.raises(Unavailable):
        exit_probe.evidence_pack_reached_a_merged_pr("AlobarQuest/x")


# =============================================================================================
# Wave 2 clause 2 -- traceability_answers_for_a_real_release (the per-unit census)
# =============================================================================================

_PRODUCTION_CHAINS = "/api/v1/traceability?environment=production"

#: The chain, written out. NEVER derived from `exit_probe.ALL_HOPS` -- see the test below.
THE_WHOLE_CHAIN = (
    "intent",
    "unit",
    "pr",
    "commit",
    "artifact",
    "deployment",
    "conditions",
    "observations",
)


def test_the_chain_hop_list_is_pinned_by_a_literal():
    """The hop list IS the judgment this clause turns on, so it may not be derived anywhere.

    Every fixture in this file builds itself by iterating `ALL_HOPS`, and `_hops` reads the same
    constant -- so a hop that leaves the tuple leaves the fixtures with it, and the whole file
    goes on agreeing with itself about a shorter chain. Adversarial review measured exactly that:
    dropping `conditions`, and separately dropping `intent` and `commit`, each left all 86 tests
    green. This literal is the only thing that notices.

    It is the plan's own enumeration at exit criterion #6 -- "intent -> unit -> PR -> commit ->
    artifact -> deployment -> observation ... including the observation tail (reconciliation
    conditions + observations)" -- not a list chosen here.

    This is the WS-P2.39 lesson recurring one layer in: that workstream pinned a bar's TEXT and
    left its DECOMPOSITION unguarded. A pin must make unique the thing the judgment lives in.
    """
    assert exit_probe.ALL_HOPS == THE_WHOLE_CHAIN


def test_the_release_check_requires_the_whole_chain_and_records_which_hops_those_were(
    monkeypatch, capsys
):
    """The check may not narrow the hop list, and the record must show the list it used.

    Pinning `ALL_HOPS` alone would not catch a release probe that required a subset of it, and
    pinning the probe body alone would not catch the tuple shrinking underneath both.
    """
    monkeypatch.setattr(exit_probe, "api_get", _api(_release_routes("rev-1", [_chain("a")])))

    exit_probe.release_chain_answers_every_hop("rev-1")
    payload = json.loads(capsys.readouterr().out.split("\n", 1)[1])

    assert payload["required_hops"] == list(THE_WHOLE_CHAIN)


def test_traceability_probe_passes_when_a_chain_resolves_every_required_hop(monkeypatch):
    monkeypatch.setattr(
        exit_probe, "api_get", _api({_PRODUCTION_CHAINS: {"chains": [_chain("a")]}})
    )

    assert exit_probe.traceability_answers_for_a_real_release(*exit_probe.ALL_HOPS) == PASS


def test_traceability_probe_fails_when_a_required_hop_is_empty_on_every_chain(monkeypatch):
    chains = {"chains": [_chain("a", artifact=False)]}
    monkeypatch.setattr(exit_probe, "api_get", _api({_PRODUCTION_CHAINS: chains}))

    assert exit_probe.traceability_answers_for_a_real_release("unit", "artifact") == FAIL


def test_traceability_probe_fails_when_the_hops_are_spread_across_different_chains(monkeypatch):
    """The measured production shape: one chain has the PR, another the artifact, neither both.

    The per-unit census is deliberately NOT satisfied by a union -- that reading is what the
    release-level check answers, under an explicit ruling, rather than something this probe
    quietly grants.
    """
    chains = {"chains": [_chain("a", artifact=False), _chain("b", pr=False)]}
    monkeypatch.setattr(exit_probe, "api_get", _api({_PRODUCTION_CHAINS: chains}))

    assert exit_probe.traceability_answers_for_a_real_release("pr", "artifact") == FAIL


def test_traceability_probe_fails_when_production_holds_no_chain(monkeypatch):
    monkeypatch.setattr(exit_probe, "api_get", _api({_PRODUCTION_CHAINS: {"chains": []}}))

    assert exit_probe.traceability_answers_for_a_real_release("unit") == FAIL


def test_traceability_probe_is_unavailable_when_the_response_has_no_chains_key(monkeypatch):
    """An absent key is not an absent chain."""
    monkeypatch.setattr(exit_probe, "api_get", _api({_PRODUCTION_CHAINS: {"anchor": {}}}))

    with pytest.raises(Unavailable):
        exit_probe.traceability_answers_for_a_real_release("unit")


def test_traceability_probe_refuses_a_hop_that_is_not_of_this_chain(monkeypatch):
    monkeypatch.setattr(
        exit_probe, "api_get", _api({_PRODUCTION_CHAINS: {"chains": [_chain("a")]}})
    )

    with pytest.raises(Unavailable, match="not hops of this chain"):
        exit_probe.traceability_answers_for_a_real_release("unit", "invented")


def test_traceability_probe_refuses_to_run_with_no_required_hops():
    """Requiring nothing would make every chain complete -- the vacuous pass, in hop form."""
    with pytest.raises(Unavailable, match="no required hops"):
        exit_probe.traceability_answers_for_a_real_release()


def test_a_renamed_hop_key_is_unavailable_rather_than_an_unanswered_hop(monkeypatch):
    """Counting an absent key as zero reports the clause not met on a fact about the response."""
    renamed = _chain("a")
    renamed["pull_request"] = renamed.pop("pr")
    monkeypatch.setattr(exit_probe, "api_get", _api({_PRODUCTION_CHAINS: {"chains": [renamed]}}))

    with pytest.raises(Unavailable, match="renamed"):
        exit_probe.traceability_answers_for_a_real_release("pr")


def test_a_hop_that_stops_being_a_container_is_unavailable_rather_than_a_populated_hop(
    monkeypatch,
):
    """The fail-OPEN direction, and the reason `_hops` validates instead of counting.

    `len` on a scalar is a number, so `len("sha-abc") == 7` reads as a hop carrying seven
    entries. A chain whose hops all became strings would report the whole chain resolved --
    a probe passing on something it never observed. Found by adversarial review of this branch.
    """
    scalar: dict = dict.fromkeys(exit_probe.ALL_HOPS, "sha-abc")
    scalar["unit"] = {"unit_key": "a", "id": "a"}
    monkeypatch.setattr(exit_probe, "api_get", _api({_PRODUCTION_CHAINS: {"chains": [scalar]}}))

    with pytest.raises(Unavailable, match="scalar"):
        exit_probe.traceability_answers_for_a_real_release(*exit_probe.ALL_HOPS)


# =============================================================================================
# Wave 2 clause 2 -- release_chain_answers_every_hop (HQ's 2026-08-05 ruling)
# =============================================================================================


def _release_routes(revision: str, chains: list[dict], *, artifacts: int = 1) -> dict:
    return {
        f"/api/v1/revisions/{revision}/evidence-pack": {
            "revision": {"id": revision},
            "units": [{"work_unit": {"id": chain["unit"]["id"]}} for chain in chains],
            "release_artifacts": [{"artifact_digest": "sha256:x"}] * artifacts,
            "deployments": [{"environment": "production"}],
        },
        f"/api/v1/traceability?revision_id={revision}": {"chains": chains},
    }


def test_release_chain_passes_when_the_revisions_units_together_answer_every_hop(monkeypatch):
    """The ruling's whole point: no single chain need span everything, the RELEASE must."""
    chains = [
        _chain("implementation", artifact=False, deployment=False),
        _chain("verification", pr=False),
    ]
    monkeypatch.setattr(exit_probe, "api_get", _api(_release_routes("rev-1", chains)))

    assert exit_probe.release_chain_answers_every_hop("rev-1") == PASS


def test_release_chain_fails_when_a_hop_is_absent_from_every_unit_of_the_release(monkeypatch):
    """The measured production shape: no unit of a real release carries a PR binding."""
    chains = [_chain("verification", pr=False), _chain("follow-up", pr=False)]
    monkeypatch.setattr(exit_probe, "api_get", _api(_release_routes("rev-1", chains)))

    assert exit_probe.release_chain_answers_every_hop("rev-1") == FAIL


def test_release_chain_passes_on_one_whole_release_among_several(monkeypatch):
    """The clause asks for A real release, so one complete revision is the bar."""
    routes = _release_routes("rev-whole", [_chain("a")])
    routes.update(_release_routes("rev-short", [_chain("b", observations=False)]))
    monkeypatch.setattr(exit_probe, "api_get", _api(routes))

    assert exit_probe.release_chain_answers_every_hop("rev-whole", "rev-short") == PASS
    assert exit_probe.release_chain_answers_every_hop("rev-short") == FAIL


def test_release_chain_fails_when_the_revision_resolves_to_no_chain(monkeypatch):
    monkeypatch.setattr(exit_probe, "api_get", _api(_release_routes("rev-1", [])))

    assert exit_probe.release_chain_answers_every_hop("rev-1") == FAIL


def test_release_chain_refuses_a_revision_that_is_not_a_release(monkeypatch):
    """A revision with no artifact binding never shipped, so it cannot answer this clause.

    Declaring one would measure something other than what the clause is about, so the probe says
    so rather than reporting the missing hops as a miss.
    """
    routes = _release_routes("rev-1", [_chain("a")], artifacts=0)
    monkeypatch.setattr(exit_probe, "api_get", _api(routes))

    with pytest.raises(Unavailable, match="no release-artifact binding"):
        exit_probe.release_chain_answers_every_hop("rev-1")


def test_release_chain_refuses_to_run_with_no_declared_revision():
    """Composing across zero releases would answer every hop by asking no question."""
    with pytest.raises(Unavailable, match="no revision"):
        exit_probe.release_chain_answers_every_hop()


def test_release_chain_is_unavailable_when_the_release_pack_loses_a_key(monkeypatch):
    routes = _release_routes("rev-1", [_chain("a")])
    pack = routes["/api/v1/revisions/rev-1/evidence-pack"]
    routes["/api/v1/revisions/rev-1/evidence-pack"] = {
        key: value for key, value in pack.items() if key != "release_artifacts"
    }
    monkeypatch.setattr(exit_probe, "api_get", _api(routes))

    with pytest.raises(Unavailable, match="release_artifacts"):
        exit_probe.release_chain_answers_every_hop("rev-1")


def test_release_chain_is_unavailable_when_the_traceability_response_loses_chains(monkeypatch):
    routes = _release_routes("rev-1", [_chain("a")])
    routes["/api/v1/traceability?revision_id=rev-1"] = {"anchor": {}}
    monkeypatch.setattr(exit_probe, "api_get", _api(routes))

    with pytest.raises(Unavailable, match="chains"):
        exit_probe.release_chain_answers_every_hop("rev-1")


# =============================================================================================
# Wave 2 clause 3 -- tracker_is_a_projection
# =============================================================================================


def _tracker_world(root: Path, monkeypatch, bindings, *, pytest_exit: int | None = 0) -> None:
    (root / "tests/tracker_projection_adapter").mkdir(parents=True, exist_ok=True)
    if pytest_exit is not None:
        _fake_pytest(root, pytest_exit)
    monkeypatch.setattr(exit_probe, "REPO_ROOT", root)
    monkeypatch.setattr(exit_probe, "api_get", _api({"/api/v1/tracker-bindings": bindings}))


def test_tracker_probe_passes_with_bindings_and_a_green_isolation_suite(tmp_path, monkeypatch):
    _tracker_world(tmp_path, monkeypatch, [{"id": "b1"}])

    assert exit_probe.tracker_is_a_projection() == PASS


def test_tracker_probe_fails_on_an_empty_binding_population(tmp_path, monkeypatch):
    """Zero canonical bindings is a projection of nothing, however green the isolation suite."""
    _tracker_world(tmp_path, monkeypatch, [])

    assert exit_probe.tracker_is_a_projection() == FAIL


def test_tracker_probe_fails_when_the_adapter_isolation_suite_is_red(tmp_path, monkeypatch):
    _tracker_world(tmp_path, monkeypatch, [{"id": "b1"}], pytest_exit=1)

    assert exit_probe.tracker_is_a_projection() == FAIL


def test_tracker_probe_fails_when_the_isolation_suite_collects_nothing(tmp_path, monkeypatch):
    _tracker_world(tmp_path, monkeypatch, [{"id": "b1"}], pytest_exit=5)

    assert exit_probe.tracker_is_a_projection() == FAIL


def test_tracker_probe_is_unavailable_without_an_interpreter(tmp_path, monkeypatch):
    _tracker_world(tmp_path, monkeypatch, [{"id": "b1"}], pytest_exit=None)

    with pytest.raises(Unavailable):
        exit_probe.tracker_is_a_projection()


def test_tracker_probe_is_unavailable_when_the_response_loses_its_binding_key(
    tmp_path, monkeypatch
):
    """A dict answer with no `bindings` key is a reshaped surface, not an empty estate."""
    _tracker_world(tmp_path, monkeypatch, {"items": []})

    with pytest.raises(Unavailable):
        exit_probe.tracker_is_a_projection()


# =============================================================================================
# Wave 2 clause 4 -- every_pr_producing_unit_is_bound
# =============================================================================================

PR_BINDING_ROUTE = "/api/v1/work-units/{unit_id}/pr-binding"


def _binding_world(monkeypatch, rows, packs, *, served=(PR_BINDING_ROUTE,)):
    monkeypatch.setattr(exit_probe, "_openapi_paths", lambda: list(served))
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, packs))


def test_binding_probe_passes_with_a_served_route_and_a_pr_capable_completed_unit(monkeypatch):
    rows = [_ledger_row("u1", "completed")]
    _binding_world(monkeypatch, rows, {"u1": _pack(capabilities={"github.pr.create": "allowed"})})

    assert exit_probe.every_pr_producing_unit_is_bound() == PASS


def test_binding_probe_fails_when_production_does_not_serve_the_route(monkeypatch):
    """The WS-P2.1 failure: a route that exists in `main` and not on the machine serving traffic."""
    _binding_world(monkeypatch, [], {}, served=("/api/v1/status-ledger",))

    assert exit_probe.every_pr_producing_unit_is_bound() == FAIL


def test_binding_probe_does_not_pass_when_no_completed_unit_is_pr_capable(monkeypatch):
    """The empty population: a guard nothing has ever passed through is not a guard that held.

    Nor is it a guard that failed -- there is nothing to observe, so the answer is that the rule
    is unexercised rather than unmet.
    """
    rows = [_ledger_row("u1", "completed"), _ledger_row("u2", "cancelled")]
    _binding_world(monkeypatch, rows, {"u1": _pack(capabilities={"repo.read": "allowed"})})

    with pytest.raises(Unavailable, match="unexercised"):
        exit_probe.every_pr_producing_unit_is_bound()


def test_binding_probe_is_unavailable_when_the_pack_loses_its_authority_projection(monkeypatch):
    """A crash is not a verdict about the subject: `main` maps it to unmeasurable, not to a miss."""
    rows = [_ledger_row("u1", "completed")]
    _binding_world(monkeypatch, rows, {"u1": {"adjudications": []}})

    assert exit_probe.main(["every-pr-producing-unit-is-bound"]) == UNAVAILABLE


# =============================================================================================
# Wave 3 clause 1 -- repositories_onboarded
# =============================================================================================


def _kit_world(home: Path, monkeypatch, results: dict[str, dict | None]) -> None:
    """A `~/Projects` holding the named checkouts, and a kit that answers for some of them."""
    monkeypatch.setenv("HOME", str(home))
    for repo in results:
        (home / "Projects" / repo).mkdir(parents=True, exist_ok=True)
    answers = {repo: payload for repo, payload in results.items() if payload is not None}
    _executable(
        home / "Projects/project-standards/.venv/bin/portfolio",
        "import json, sys\n"
        f"ANSWERS = json.loads({json.dumps(json.dumps(answers))})\n"
        "if sys.argv[1:] == ['--help']:\n"
        "    print('usage')\n"
        "    raise SystemExit(0)\n"
        "name = sys.argv[-1].rstrip('/').split('/')[-1]\n"
        "if name not in ANSWERS:\n"
        "    print('the kit could not read this checkout')\n"
        "    raise SystemExit(1)\n"
        "print('summary line')\n"
        "print(json.dumps(ANSWERS[name], indent=1))\n",
    )


def _onboarded(passed: bool) -> dict:
    return {
        "admission_passed": passed,
        "checks": [{"id": "runner.caller", "status": "pass" if passed else "violation"}],
    }


def test_onboarding_probe_passes_when_the_bar_is_met(tmp_path, monkeypatch):
    _kit_world(tmp_path, monkeypatch, {"a": _onboarded(True), "b": _onboarded(True)})

    assert exit_probe.repositories_onboarded("2", "a", "b") == PASS


def test_onboarding_probe_fails_one_repository_short_of_the_bar(tmp_path, monkeypatch):
    """The named shape: an onboard result one repository short must not read as met."""
    _kit_world(tmp_path, monkeypatch, {"a": _onboarded(True), "b": _onboarded(False)})

    assert exit_probe.repositories_onboarded("2", "a", "b") == FAIL


def test_onboarding_probe_is_unavailable_when_a_shortfall_is_unproven(tmp_path, monkeypatch):
    """Short of the bar WITH an unmeasured candidate is not a miss -- it might have cleared it."""
    _kit_world(tmp_path, monkeypatch, {"a": _onboarded(True), "b": None})

    with pytest.raises(Unavailable, match="unproven"):
        exit_probe.repositories_onboarded("2", "a", "b")


def test_onboarding_probe_is_unavailable_when_no_checkout_can_be_measured(tmp_path, monkeypatch):
    _kit_world(tmp_path, monkeypatch, {"a": None})

    with pytest.raises(Unavailable, match="no candidate checkout"):
        exit_probe.repositories_onboarded("1", "a")


def test_onboarding_probe_is_unavailable_when_the_kit_stops_reporting_admission(
    tmp_path, monkeypatch
):
    """A renamed key would read as every repository failing admission -- a verdict about the
    repositories taken from a fact about the tool."""
    _kit_world(tmp_path, monkeypatch, {"a": {"checks": []}, "b": {"checks": []}})

    with pytest.raises(Unavailable):
        exit_probe.repositories_onboarded("1", "a", "b")


def test_onboarding_probe_is_unavailable_without_the_kit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))

    with pytest.raises(Unavailable, match="conformance kit"):
        exit_probe.repositories_onboarded("1", "a")


# =============================================================================================
# Wave 3 clause 2 -- profiles_are_proven
# =============================================================================================


def _profile_pack(change_class: str | None) -> dict:
    return {"authority": {"envelope": {"change_class": change_class, "capabilities": {}}}}


def test_profile_probe_passes_when_each_cited_unit_completed_under_its_class(monkeypatch):
    rows = [_ledger_row("aaaa1111", "completed"), _ledger_row("bbbb2222", "completed")]
    packs = {
        "aaaa1111": _profile_pack("dependency-update"),
        "bbbb2222": _profile_pack("software-delivery"),
    }
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, packs))

    assert exit_probe.profiles_are_proven("aaaa:dependency-update", "bbbb:software-delivery") == (
        PASS
    )


def test_profile_probe_fails_when_a_cited_unit_ran_under_a_different_class(monkeypatch):
    rows = [_ledger_row("aaaa1111", "completed")]
    packs = {"aaaa1111": _profile_pack("maintenance-remediation")}
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, packs))

    assert exit_probe.profiles_are_proven("aaaa:dependency-update") == FAIL


def test_profile_probe_fails_when_a_cited_unit_never_completed(monkeypatch):
    rows = [_ledger_row("aaaa1111", "failed")]
    packs = {"aaaa1111": _profile_pack("dependency-update")}
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, packs))

    assert exit_probe.profiles_are_proven("aaaa:dependency-update") == FAIL


def test_profile_probe_fails_when_a_prefix_names_no_unit(monkeypatch):
    monkeypatch.setattr(exit_probe, "api_get", _world([_ledger_row("aaaa1111", "completed")], {}))

    assert exit_probe.profiles_are_proven("zzzz:dependency-update") == FAIL


def test_profile_probe_fails_when_a_prefix_names_more_than_one_unit(monkeypatch):
    """`unit_key` is not unique and neither is a short prefix; two matches proves nothing."""
    rows = [_ledger_row("aaaa1111", "completed"), _ledger_row("aaaa2222", "completed")]
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {}))

    assert exit_probe.profiles_are_proven("aaaa:dependency-update") == FAIL


def test_profile_probe_refuses_to_run_with_no_cited_profiles():
    with pytest.raises(Unavailable, match="no profiles were cited"):
        exit_probe.profiles_are_proven()


# =============================================================================================
# Wave 3 clause 3 -- routing_policy_is_the_only_source
# =============================================================================================


def _routing_world(home: Path, monkeypatch, body: str | None) -> None:
    monkeypatch.setenv("HOME", str(home))
    if body is not None:
        _executable(
            home / "Projects/intent-packages/scripts/check_routing_policy_compatibility.py", body
        )


def test_routing_probe_passes_when_the_derivation_pin_holds(tmp_path, monkeypatch):
    _routing_world(tmp_path, monkeypatch, "print('policy and workflow agree')\n")

    assert exit_probe.routing_policy_is_the_only_source() == PASS


def test_routing_probe_fails_when_the_derivation_pin_is_broken(tmp_path, monkeypatch):
    _routing_world(tmp_path, monkeypatch, "print('diverged')\nraise SystemExit(1)\n")

    assert exit_probe.routing_policy_is_the_only_source() == FAIL


def test_routing_probe_is_unavailable_when_the_pin_is_not_on_this_machine(tmp_path, monkeypatch):
    _routing_world(tmp_path, monkeypatch, None)

    with pytest.raises(Unavailable, match="is absent"):
        exit_probe.routing_policy_is_the_only_source()


def test_routing_probe_is_unavailable_when_the_pin_cannot_resolve_both_sides(tmp_path, monkeypatch):
    """A pin that could not read one side has not established that the two agree."""
    _routing_world(
        tmp_path,
        monkeypatch,
        "import sys\nsys.stderr.write('HTTP 403 reading the workflow\\n')\nraise SystemExit(1)\n",
    )

    with pytest.raises(Unavailable, match="could not resolve"):
        exit_probe.routing_policy_is_the_only_source()


# =============================================================================================
# Wave 3 clause 4 -- workflows_were_consecutive
# =============================================================================================


def _runs(*units) -> list[dict]:
    return [
        _ledger_row(unit_id, state, actor_id=actor, last_event_at=f"2026-08-0{index + 1}T00:00:00Z")
        for index, (unit_id, state, actor) in enumerate(units)
    ]


def test_consecutive_probe_passes_when_the_two_runs_are_adjacent(monkeypatch):
    rows = _runs(
        ("aaaa1111", "completed", "factory-runner"), ("bbbb2222", "completed", "factory-runner")
    )
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {}))

    assert exit_probe.workflows_were_consecutive("aaaa", "bbbb") == PASS


def test_consecutive_probe_fails_when_a_runner_unit_ran_between_them(monkeypatch):
    rows = _runs(
        ("aaaa1111", "completed", "factory-runner"),
        ("cccc3333", "failed", "factory-runner"),
        ("bbbb2222", "completed", "factory-runner"),
    )
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {}))

    assert exit_probe.workflows_were_consecutive("aaaa", "bbbb") == FAIL


def test_consecutive_probe_fails_when_the_named_order_is_reversed(monkeypatch):
    rows = _runs(
        ("aaaa1111", "completed", "factory-runner"), ("bbbb2222", "completed", "factory-runner")
    )
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {}))

    assert exit_probe.workflows_were_consecutive("bbbb", "aaaa") == FAIL


def test_consecutive_probe_fails_when_one_run_did_not_complete(monkeypatch):
    rows = _runs(
        ("aaaa1111", "completed", "factory-runner"), ("bbbb2222", "failed", "factory-runner")
    )
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {}))

    assert exit_probe.workflows_were_consecutive("aaaa", "bbbb") == FAIL


def test_consecutive_probe_fails_on_an_ambiguous_prefix(monkeypatch):
    rows = _runs(
        ("aaaa1111", "completed", "factory-runner"), ("aaaa2222", "completed", "factory-runner")
    )
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {}))

    assert exit_probe.workflows_were_consecutive("aaaa", "aaaa") == FAIL


def test_consecutive_probe_is_unavailable_when_a_unit_was_never_runner_claimed(monkeypatch):
    """Ordering among dispatched work is unknown for a unit that never appeared in it."""
    rows = _runs(("aaaa1111", "completed", "factory-runner"), ("bbbb2222", "completed", "devon"))
    monkeypatch.setattr(exit_probe, "api_get", _world(rows, {}))

    with pytest.raises(Unavailable, match="ordering is unknown"):
        exit_probe.workflows_were_consecutive("aaaa", "bbbb")


# =============================================================================================
# the declaration probe, the dispatcher, and the join with the manifest
# =============================================================================================


def test_unmeasured_never_reports_a_verdict():
    """It exists so a clause's unreached conjunct is visible; a pass from it would be the defect."""
    assert exit_probe.main(["unmeasured", "nothing measures the second conjunct"]) == UNAVAILABLE


def test_unmeasured_with_no_reason_is_still_unmeasured():
    assert exit_probe.main(["unmeasured"]) == UNAVAILABLE


def test_an_unknown_probe_name_is_unavailable_not_a_verdict():
    assert exit_probe.main(["no-such-probe"]) == UNAVAILABLE


def test_a_probe_called_with_the_wrong_arity_is_unavailable_not_a_verdict():
    assert exit_probe.main(["workflows-were-consecutive", "only-one-argument"]) == UNAVAILABLE


def test_an_unexpected_crash_is_unavailable_not_a_miss(monkeypatch):
    """A ledger or evidence-pack schema change must never read as 'measured and not met'."""

    def explode(_path: str):
        raise KeyError("unit_state")

    monkeypatch.setattr(exit_probe, "api_get", explode)

    assert exit_probe.main(["completed-units-carry-adjudications"]) == UNAVAILABLE


def test_the_manifest_and_the_probe_registry_name_exactly_the_same_probes():
    """The join, asserted BOTH ways, because each direction hides a different silent failure.

    A manifest naming a probe that does not exist reports `unavailable` for ever. A probe no
    manifest invokes is measurement code with no caller -- the shape this estate has shipped
    before and now refuses on sight.

    Keyed on the shipped manifest rather than on a list restated here, and matched on the script
    rather than on the interpreter token, so a check written `python`/`uv run` is not skipped.
    """
    document = tomllib.loads(
        (exit_probe.REPO_ROOT / "docs/operations/wave-exit-manifest.toml").read_text(
            encoding="utf-8"
        )
    )
    invoked = {
        check["argv"][2]
        for bar in document["bar"]
        for clause in bar["clause"]
        for check in clause.get("check", [])
        if check["kind"] == "command"
        and len(check["argv"]) > 2
        and str(check["argv"][1]).endswith("exit_probe.py")
    }

    assert invoked == set(exit_probe.PROBES)


def test_a_report_always_names_the_host_it_measured(capsys, monkeypatch):
    """A record that does not name the host cannot substantiate a `proves` saying 'production'."""
    monkeypatch.setattr(exit_probe, "API_BASE", "https://example.invalid")

    exit_probe.report(True, "PASS something")
    payload = json.loads(capsys.readouterr().out.split("\n", 1)[1])

    assert payload["measured_against"] == "https://example.invalid"
