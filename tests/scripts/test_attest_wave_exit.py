"""The property under test is that a clause cannot go unmeasured without the tool saying so.

Three failure modes are pinned here, because each one is a way the programme has actually
reasoned from a summary of a rule instead of the rule:

1. the authoritative text gains or loses a clause and the manifest does not (source divergence);
2. the manifest accounts for less text than the bar contains (short reconstruction);
3. a clause is declared but nothing measures it (a clause with no check).

The third is the quiet one. A tool that reported `pass` for an unmeasured clause would have
automated the summary rather than replaced it, so `no check` resolves to `unavailable` and
`unavailable` is pinned here never to collapse into either `pass` or `fail`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts.attest_wave_exit import (
    DEFAULT_MANIFEST,
    DEFAULT_PLAN,
    Verdict,
    load_manifest,
    main,
    resolve_clause,
    run_command_check,
    run_retained_evidence_check,
    verify_pin,
)

WAVE3_BODY = (
    "≥5 repositories onboarded through the kit; three software profiles + one non-software "
    "profile proven; the routing policy file is the only place model selection lives; two "
    "consecutive real workflows complete without improvisation (the C exit bar)."
)
WAVE3_CLAUSES = [
    "≥5 repositories onboarded through the kit",
    "three software profiles + one non-software profile proven",
    "the routing policy file is the only place model selection lives",
    "two consecutive real workflows complete without improvisation (the C exit bar).",
]


def _manifest(clause_texts: list[str], body: str = WAVE3_BODY, checks: list | None = None) -> dict:
    """A one-bar manifest whose clauses reconstruct `body` when nothing is tampered with."""
    return {
        "schema_version": 1,
        "authoritative_source": "plan.md",
        "bar": [
            {
                "wave": 3,
                "anchor": "**Wave 3 exit:**",
                "clause_count": len(clause_texts),
                "clause_separator": "; ",
                "body": body,
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "clause": [
                    {
                        "number": i + 1,
                        "separator": "" if i == 0 else "; ",
                        "text": text,
                        "annotation": "",
                        "check": checks if checks is not None else [],
                    }
                    for i, text in enumerate(clause_texts)
                ],
            }
        ],
    }


def _as_toml(manifest: dict) -> str:
    """Just enough TOML to round-trip the dict helpers through the file-based loader."""
    lines = [
        f"schema_version = {manifest['schema_version']}",
        f"authoritative_source = {json.dumps(manifest['authoritative_source'])}",
    ]
    for bar in manifest["bar"]:
        lines += [
            "",
            "[[bar]]",
            f"wave = {bar['wave']}",
            f"anchor = {json.dumps(bar['anchor'])}",
            f"clause_count = {bar['clause_count']}",
            f"clause_separator = {json.dumps(bar['clause_separator'])}",
            f"body = {json.dumps(bar['body'], ensure_ascii=False)}",
            f"body_sha256 = {json.dumps(bar['body_sha256'])}",
        ]
        for clause in bar["clause"]:
            lines += ["", "[[bar.clause]]", f"number = {clause['number']}"]
            if clause.get("separator"):
                lines.append(f"separator = {json.dumps(clause['separator'])}")
            lines.append(f"text = {json.dumps(clause['text'], ensure_ascii=False)}")
            if clause.get("annotation"):
                lines.append(f"annotation = {json.dumps(clause['annotation'], ensure_ascii=False)}")
            for check in clause.get("check") or []:
                lines += [
                    "",
                    "[[bar.clause.check]]",
                    f"kind = {json.dumps(check['kind'])}",
                    f"argv = {json.dumps(check['argv'])}",
                ]
    return "\n".join(lines) + "\n"


def _plan(body: str) -> str:
    return f"# plan\n\nsome preamble\n\n**Wave 3 exit:** {body}\n\ntrailing\n"


# --- the discriminating tests: a dropped clause must be loud, both directions -----------------


def test_a_clause_dropped_from_the_authoritative_source_breaks_the_pin():
    """The miss that motivated this tool: the bar has four clauses, a summary discussed three."""
    manifest = _manifest(WAVE3_CLAUSES)
    truncated = "; ".join(WAVE3_CLAUSES[:2] + [WAVE3_CLAUSES[3]])

    pin = verify_pin(load_manifest(manifest).bars[0], _plan(truncated))

    assert not pin.holds
    assert "the routing policy file is the only place model selection lives" in pin.absent_clauses
    assert pin.reason == "source_diverged"


def test_a_clause_dropped_from_the_manifest_is_refused_at_load():
    """The mirror image: the source is intact and the manifest under-enumerates it.

    Caught at load rather than at the pin, by the separator accounting -- a dropped clause
    leaves a separator in the bar that no clause boundary and no declared annotation accounts
    for.
    """
    with pytest.raises(ValueError, match="account"):
        load_manifest(_manifest(WAVE3_CLAUSES[:3]))


def test_the_pin_holds_when_the_manifest_and_the_source_agree():
    pin = verify_pin(load_manifest(_manifest(WAVE3_CLAUSES)).bars[0], _plan(WAVE3_BODY))

    assert pin.holds
    assert pin.reason is None


def test_an_edit_inside_a_clause_breaks_the_pin_even_though_the_count_is_unchanged():
    """Four clauses in, four clauses out -- but not the same four."""
    weakened = list(WAVE3_CLAUSES)
    weakened[0] = "≥3 repositories onboarded through the kit"

    pin = verify_pin(load_manifest(_manifest(WAVE3_CLAUSES)).bars[0], _plan("; ".join(weakened)))

    assert not pin.holds
    assert "≥5 repositories onboarded through the kit" in pin.absent_clauses


def test_a_whitespace_only_edit_to_the_mirror_is_caught_by_the_declared_digest():
    """The source is unreachable in CI, so the mirror needs its own integrity check."""
    manifest = _manifest(WAVE3_CLAUSES)
    manifest["bar"][0]["body"] = WAVE3_BODY + " "

    with pytest.raises(ValueError, match="body_sha256"):
        load_manifest(manifest)


def test_a_bar_whose_anchor_is_absent_from_the_source_is_a_pin_failure_not_a_pass():
    pin = verify_pin(load_manifest(_manifest(WAVE3_CLAUSES)).bars[0], "# plan\n\nno bar here\n")

    assert not pin.holds
    assert pin.reason == "anchor_not_found"


def test_a_duplicated_anchor_is_refused_rather_than_silently_taking_the_first():
    doubled = _plan(WAVE3_BODY) + _plan("something else entirely")

    pin = verify_pin(load_manifest(_manifest(WAVE3_CLAUSES)).bars[0], doubled)

    assert not pin.holds
    assert pin.reason == "anchor_ambiguous"


# --- the annotation split: status commentary is quarantined, never summarised -----------------


def test_reconstruction_covers_status_annotations_verbatim():
    """Wave 1's bar carries inline status marks; they are part of the text and must be accounted."""
    body = "drills pass **(✅ 5 drills)**; the SLO report runs **(✅ WS-P2.2)**"
    manifest = {
        "schema_version": 1,
        "authoritative_source": "plan.md",
        "bar": [
            {
                "wave": 1,
                "anchor": "**Wave 1 exit:**",
                "clause_count": 2,
                "clause_separator": "; ",
                "body": body,
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "clause": [
                    {
                        "number": 1,
                        "separator": "",
                        "text": "drills pass",
                        "annotation": " **(✅ 5 drills)**",
                        "check": [],
                    },
                    {
                        "number": 2,
                        "separator": "; ",
                        "text": "the SLO report runs",
                        "annotation": " **(✅ WS-P2.2)**",
                        "check": [],
                    },
                ],
            }
        ],
    }
    bar = load_manifest(manifest).bars[0]

    assert verify_pin(bar, f"**Wave 1 exit:** {body}\n").holds
    # The normative half is what a reader and a check see -- free of the status commentary.
    assert bar.clauses[0].text == "drills pass"


def test_a_normative_clause_may_not_swallow_the_separator():
    """Otherwise two clauses could be declared as one and the count would silently drop."""
    manifest = _manifest(["a; b", "c"], body="a; b; c")

    with pytest.raises(ValueError, match="separator"):
        load_manifest(manifest)


# --- typed results: `unavailable` is a distinct state and never collapses ----------------------


def test_a_clause_with_no_check_is_unavailable_not_pass():
    """The whole point: nothing measured it, so nothing may claim it."""
    bar = load_manifest(_manifest(WAVE3_CLAUSES)).bars[0]

    assert resolve_clause(bar.clauses[0], results=[]).result == "unavailable"


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        (["pass"], "pass"),
        (["pass", "pass"], "pass"),
        (["pass", "not_applicable"], "pass"),
        (["not_applicable"], "not_applicable"),
        (["pass", "fail"], "fail"),
        (["pass", "unavailable"], "unavailable"),
        # A fail outranks an unavailable: something was measured and missed.
        (["fail", "unavailable"], "fail"),
        (["unavailable", "not_applicable"], "unavailable"),
    ],
)
def test_clause_result_is_the_weakest_of_its_checks(results, expected):
    bar = load_manifest(_manifest(WAVE3_CLAUSES)).bars[0]

    assert resolve_clause(bar.clauses[0], results=results).result == expected


def test_an_unavailable_clause_makes_the_run_inconclusive_not_passing():
    verdict = Verdict.of(pin_broken=False, clause_results=["pass", "pass", "unavailable"])

    assert verdict.name == "inconclusive"
    assert verdict.exit_code == 2


@pytest.mark.parametrize(
    ("pin_broken", "results", "name", "code"),
    [
        (False, ["pass", "not_applicable"], "attested", 0),
        (False, ["pass", "fail"], "failed", 1),
        (False, ["pass", "unavailable"], "inconclusive", 2),
        (False, ["fail", "unavailable"], "failed", 1),
        # A broken pin outranks everything: the clause results were measured against a
        # text that is no longer the rule, so they are not evidence of anything.
        (True, ["pass", "pass"], "pin_broken", 3),
        (True, ["fail"], "pin_broken", 3),
    ],
)
def test_verdict_precedence(pin_broken, results, name, code):
    verdict = Verdict.of(pin_broken=pin_broken, clause_results=results)

    assert (verdict.name, verdict.exit_code) == (name, code)


# --- check execution: a check that could not run is unavailable, never fail -------------------


def test_a_command_that_does_not_exist_is_unavailable_not_fail():
    outcome = run_command_check(
        {"kind": "command", "argv": ["this-command-does-not-exist-wsp239"]}, cwd=Path.cwd()
    )

    assert outcome.result == "unavailable"


def test_a_command_that_exits_non_zero_is_a_fail():
    outcome = run_command_check(
        {"kind": "command", "argv": [sys.executable, "-c", "raise SystemExit(1)"]}, cwd=Path.cwd()
    )

    assert outcome.result == "fail"


def test_a_failing_check_reports_what_the_probe_measured_not_only_its_exit_code():
    """`proves` is written only on a pass, so a miss has to carry its own summary line.

    Without this the one thing a reader most needs -- what was measured and found short -- lives
    only inside the retained record, and the printed report says `exit 1`.
    """
    outcome = run_command_check(
        {
            "kind": "command",
            "argv": [
                sys.executable,
                "-c",
                "print('FAIL 0/2 releases answer every hop')\nraise SystemExit(1)",
            ],
            "proves": "this sentence is never written, because the check did not pass",
        },
        cwd=Path.cwd(),
    )

    assert outcome.result == "fail"
    assert "0/2 releases answer every hop" in outcome.detail
    assert "exit 1" in outcome.detail
    assert "never written" not in outcome.detail


def test_a_command_may_declare_the_exit_codes_that_mean_unmeasurable():
    outcome = run_command_check(
        {
            "kind": "command",
            "argv": [sys.executable, "-c", "raise SystemExit(7)"],
            "unavailable_exit_codes": [7],
        },
        cwd=Path.cwd(),
    )

    assert outcome.result == "unavailable"


def test_a_command_may_declare_the_output_that_means_unmeasurable():
    outcome = run_command_check(
        {
            "kind": "command",
            "argv": [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('401 Unauthorized'); raise SystemExit(1)",
            ],
            "unavailable_pattern": "401 Unauthorized",
        },
        cwd=Path.cwd(),
    )

    assert outcome.result == "unavailable"


def test_a_command_check_retains_the_command_and_its_output():
    """Section 4: a measurement is reproducible only if what produced it is kept."""
    outcome = run_command_check(
        {"kind": "command", "argv": [sys.executable, "-c", "print('measured')"]}, cwd=Path.cwd()
    )

    assert outcome.result == "pass"
    assert outcome.evidence["argv"] == [sys.executable, "-c", "print('measured')"]
    assert "measured" in outcome.evidence["stdout"]
    assert outcome.evidence["exit_code"] == 0


def test_retained_evidence_check_fails_when_the_artifact_digest_moved(tmp_path):
    artifact = tmp_path / "closeout.md"
    artifact.write_text("the measurement", encoding="utf-8")
    stale = hashlib.sha256(b"something else").hexdigest()

    outcome = run_retained_evidence_check(
        {"kind": "retained_evidence", "path": str(artifact), "sha256": stale}, cwd=tmp_path
    )

    assert outcome.result == "fail"


def test_retained_evidence_check_is_unavailable_when_the_artifact_is_gone(tmp_path):
    """Evidence in a scratchpad is not evidence -- a missing artifact is unmeasured, not met."""
    outcome = run_retained_evidence_check(
        {"kind": "retained_evidence", "path": str(tmp_path / "gone.md"), "sha256": "0" * 64},
        cwd=tmp_path,
    )

    assert outcome.result == "unavailable"


# --- the real manifest ------------------------------------------------------------------------


def test_the_shipped_manifest_loads_and_every_clause_reconstructs_its_mirror():
    manifest = load_manifest(DEFAULT_MANIFEST)

    assert [bar.wave for bar in manifest.bars] == [1, 2, 3]
    for bar in manifest.bars:
        assert bar.reconstructed() == bar.body, f"wave {bar.wave} clauses do not reconstruct"


def test_every_shipped_clause_carries_at_least_one_check():
    """A clause with no check reports `unavailable`; shipping one would be shipping a gap."""
    manifest = load_manifest(DEFAULT_MANIFEST)

    uncovered = [
        f"wave {bar.wave} clause {clause.number}"
        for bar in manifest.bars
        for clause in bar.clauses
        if not clause.checks and not clause.not_applicable_reason
    ]

    assert uncovered == []


@pytest.mark.skipif(not DEFAULT_PLAN.exists(), reason="authoritative plan is not on this machine")
def test_the_shipped_mirror_is_byte_identical_to_the_authoritative_source():
    """The mirror exists so CI can pin without the plan; this is what keeps it honest."""
    document = DEFAULT_PLAN.read_text(encoding="utf-8")

    for bar in load_manifest(DEFAULT_MANIFEST).bars:
        pin = verify_pin(bar, document)
        assert pin.holds, f"wave {bar.wave}: {pin.reason} -- absent {pin.absent_clauses}"


def test_cli_reports_inconclusive_when_the_plan_is_absent(tmp_path, capsys):
    """Not reaching the authoritative text is a measurement failure, not a pass."""
    code = main(
        [
            "--manifest",
            str(DEFAULT_MANIFEST),
            "--plan",
            str(tmp_path / "absent.md"),
            "--pin-only",
        ]
    )
    out = capsys.readouterr().out

    assert code == 2
    assert "INCONCLUSIVE" in out


def test_cli_writes_a_retained_record_carrying_the_verdict(tmp_path):
    record = tmp_path / "run.json"
    main(["--manifest", str(DEFAULT_MANIFEST), "--pin-only", "--record", str(record)])

    payload = json.loads(record.read_text(encoding="utf-8"))

    assert payload["verdict"] in {"attested", "failed", "inconclusive", "pin_broken"}
    assert payload["bars"][0]["wave"] == 1
    assert "measured_at" in payload


def test_a_broken_pin_suppresses_clause_measurement(tmp_path, capsys):
    """A PASS printed under a broken pin reads as a demonstration of a rule nobody has."""
    plan = tmp_path / "truncated.md"
    intact = DEFAULT_PLAN.read_text(encoding="utf-8") if DEFAULT_PLAN.exists() else ""
    dropped = "the routing policy file is the only place model selection lives; "
    if dropped not in intact:
        pytest.skip("authoritative plan is not on this machine")
    plan.write_text(intact.replace(dropped, "", 1), encoding="utf-8")

    code = main(["--manifest", str(DEFAULT_MANIFEST), "--plan", str(plan), "--wave", "3"])
    out = capsys.readouterr().out

    assert code == 3
    assert "PIN BROKEN" in out
    assert "measurement SKIPPED" in out
    assert "[PASS]" not in out
    # and it names the clause that vanished, rather than only that something changed
    assert "the routing policy file is the only place model selection lives" in out


@pytest.mark.parametrize(
    ("results", "name", "code"),
    [
        # Nothing demonstrated is not an attestation, however tidy the inputs look.
        ([], "nothing_demonstrated", 2),
        (["not_applicable"], "nothing_demonstrated", 2),
        (["not_applicable", "not_applicable"], "nothing_demonstrated", 2),
        # One real demonstration alongside inapplicable siblings still attests.
        (["pass", "not_applicable"], "attested", 0),
    ],
)
def test_a_run_that_demonstrates_nothing_is_not_attested(results, name, code):
    """Otherwise a manifest whose every clause is declared inapplicable reports success."""
    verdict = Verdict.of(pin_broken=False, clause_results=results)

    assert (verdict.name, verdict.exit_code) == (name, code)


def test_a_clause_may_not_be_excused_and_checked_at_once():
    """Otherwise one manifest line converts a measured `fail` into a green run.

    `resolve_clause` short-circuits on the reason before consulting results, while the CLI still
    runs the checks -- so an excuse alongside checks silently discards their outcome.
    """
    manifest = _manifest(WAVE3_CLAUSES)
    manifest["bar"][0]["clause"][0]["not_applicable_reason"] = "excused"
    manifest["bar"][0]["clause"][0]["check"] = [
        {"kind": "command", "argv": [sys.executable, "-c", "raise SystemExit(1)"]}
    ]

    with pytest.raises(ValueError, match="not_applicable_reason AND checks"):
        load_manifest(manifest)


def test_a_clause_declared_inapplicable_reports_its_reason():
    """The only route to `not_applicable` today, so its behaviour is pinned rather than assumed.

    No shipped clause uses it: every check kind returns pass/fail/unavailable, so this escape
    hatch is the one way a clause can be excused. It is deliberately loud -- the reason travels
    into the printed report and the retained record.
    """
    manifest = _manifest(WAVE3_CLAUSES)
    manifest["bar"][0]["clause"][0]["not_applicable_reason"] = "the subject does not exist here"
    clause = load_manifest(manifest).bars[0].clauses[0]

    outcome = resolve_clause(clause, results=[])

    assert outcome.result == "not_applicable"
    assert outcome.detail == "the subject does not exist here"


# --- the decomposition must be UNIQUE, not merely exact -------------------------------------
#
# Reconstruction being byte-exact does not make it unique, and the non-unique dimension is
# precisely the hide-a-clause dimension. Each test below is a route by which a clause vanished
# from the count while the bar stayed byte-identical, found by adversarial review.


def test_a_clause_absorbed_into_its_neighbours_annotation_is_refused():
    manifest = _manifest(WAVE3_CLAUSES[:1] + WAVE3_CLAUSES[2:])
    manifest["bar"][0]["clause"][0]["annotation"] = "; " + WAVE3_CLAUSES[1]

    with pytest.raises(ValueError, match="annotation contains the clause separator"):
        load_manifest(manifest)


def test_a_clause_absorbed_into_an_override_separator_is_refused():
    manifest = _manifest(WAVE3_CLAUSES[:1] + WAVE3_CLAUSES[2:])
    manifest["bar"][0]["clause"][1]["separator"] = f"; {WAVE3_CLAUSES[1]}; "

    with pytest.raises(ValueError, match="override separator"):
        load_manifest(manifest)


def test_a_clause_separator_that_does_not_occur_in_the_bar_is_refused():
    """Otherwise every guard keyed on it is vacuous and one clause can hold the whole sentence."""
    manifest = _manifest([WAVE3_BODY])
    manifest["bar"][0]["clause_separator"] = "\u00b6"

    with pytest.raises(ValueError, match="does not occur in the bar"):
        load_manifest(manifest)


def test_an_annotation_carrying_the_separator_must_declare_how_many():
    """Wave 1's bar has a genuine case, so it is declared and counted rather than forbidden."""
    body = "alpha; beta **(closed; done)**"
    manifest = {
        "schema_version": 1,
        "authoritative_source": "plan.md",
        "bar": [
            {
                "wave": 1,
                "anchor": "**Wave 1 exit:**",
                "clause_count": 2,
                "clause_separator": "; ",
                "body": body,
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "clause": [
                    {"number": 1, "text": "alpha", "check": []},
                    {
                        "number": 2,
                        "text": "beta",
                        "annotation": " **(closed; done)**",
                        "annotation_separator_count": 1,
                        "check": [],
                    },
                ],
            }
        ],
    }
    assert load_manifest(manifest).bars[0].reconstructed() == body

    del manifest["bar"][0]["clause"][1]["annotation_separator_count"]
    with pytest.raises(ValueError, match="annotation contains the clause separator"):
        load_manifest(manifest)


def test_the_shipped_manifest_pins_the_clause_count_of_every_bar():
    """The number this whole file exists to protect -- four, not three -- asserted outright."""
    counts = {bar.wave: len(bar.clauses) for bar in load_manifest(DEFAULT_MANIFEST).bars}

    assert counts == {1: 5, 2: 4, 3: 4}


def test_a_declared_clause_count_that_disagrees_with_the_clauses_is_refused():
    """`clause_count` earns its keep only if a wrong one is loud.

    Every other route to a vanished clause is caught by the separator accounting, which is why
    this declaration went untested: it is the backstop for a bar the accounting cannot see
    through. A count that is merely decorative would restate the emergent number rather than
    protect it.
    """
    manifest = _manifest(WAVE3_CLAUSES)
    manifest["bar"][0]["clause_count"] = 3

    with pytest.raises(ValueError, match="clause_count"):
        load_manifest(manifest)


def test_a_clauses_note_reaches_the_retained_record_whatever_the_result(tmp_path):
    """Where a clause-level rationale must live, pinned -- because `proves` cannot hold one.

    `proves` is per-CHECK and is written into the record only when that check PASSES: a check
    that reports `unavailable` or `fail` carries the probe's own reason instead. So a rationale
    for a clause that is deliberately unmeasured has to be a clause `note`, which is recorded
    unconditionally. WS-P2.40 put the scope annotations for clauses 1.3 and 1.5 there for
    exactly this reason.
    """
    record = tmp_path / "run.json"
    main(
        ["--manifest", str(DEFAULT_MANIFEST), "--wave", "1", "--pin-only", "--record", str(record)]
    )
    clauses = {
        clause["number"]: clause
        for clause in json.loads(record.read_text(encoding="utf-8"))["bars"][0]["clauses"]
    }

    assert clauses[3]["result"] == "unavailable"
    assert "SCOPE PROPERTY OF THIS TOOL" in clauses[3]["note"]
    assert "SCOPE PROPERTY OF THIS TOOL" in clauses[5]["note"]


def test_a_clause_appended_to_the_plan_beneath_the_bar_breaks_the_pin():
    """Appending a sentence is how a plan grows, and a line-scoped pin cannot see it."""
    bar = load_manifest(_manifest(WAVE3_CLAUSES)).bars[0]
    grown = _plan(WAVE3_BODY).replace(
        f"**Wave 3 exit:** {WAVE3_BODY}\n",
        f"**Wave 3 exit:** {WAVE3_BODY}\nAdditionally, a fifth requirement applies.\n",
    )

    pin = verify_pin(bar, grown)

    assert not pin.holds
    assert pin.reason == "bar_paragraph_extends"


def test_the_plan_being_unreadable_is_inconclusive_even_when_checks_run(tmp_path, capsys):
    """Without `--pin-only`, so the source layer is what makes the run inconclusive, not the flag.

    The earlier version of this test passed `--pin-only`, which independently makes every clause
    unavailable -- it stayed green under the very defect it names. A synthetic one-bar manifest
    with a trivially passing check keeps the clause results out of the way, so the only thing
    that can produce `unavailable` here is the unreadable plan.
    """
    manifest = _manifest(
        WAVE3_CLAUSES,
        checks=[{"kind": "command", "argv": [sys.executable, "-c", "pass"]}],
    )
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_as_toml(manifest), encoding="utf-8")

    code = main(["--manifest", str(manifest_path), "--plan", str(tmp_path / "absent.md")])
    out = capsys.readouterr().out

    assert code == 2
    assert "PIN PARTIAL" in out
    assert "INCONCLUSIVE" in out
    # every clause did pass; the run is inconclusive purely because the plan was unreachable
    assert out.count("[PASS]") == len(WAVE3_CLAUSES)


def test_the_retained_record_carries_the_measurement_not_just_a_shape(tmp_path):
    record = tmp_path / "run.json"
    code = main(
        ["--manifest", str(DEFAULT_MANIFEST), "--wave", "3", "--pin-only", "--record", str(record)]
    )
    payload = json.loads(record.read_text(encoding="utf-8"))
    bar = payload["bars"][0]

    assert code == 2
    assert payload["verdict"] == "inconclusive"
    assert payload["pin_only"] is True
    assert bar["wave"] == 3
    assert len(bar["clauses"]) == 4
    assert [clause["result"] for clause in bar["clauses"]] == ["unavailable"] * 4
    assert bar["body_sha256"] == hashlib.sha256(bar["body"].encode()).hexdigest()
