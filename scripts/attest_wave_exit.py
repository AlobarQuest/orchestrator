#!/usr/bin/env python3
"""Attest a wave exit bar against the rule itself, clause by clause.

This programme's most persistent failure is reasoning from a *summary* of a rule instead of the
rule. Criteria #5, #6 and #7 were each marked MET on evidence that did not support them, and
Wave 3's four-clause bar was tracked as three clauses for two days -- caught by someone reading
the authoritative line one step before declaring, which is attention, not machinery.

`scripts/attest_exit_criteria.py` already covers one class of this (a scorecard cell whose status
rests on a route production serves). This generalises the same pattern to a whole exit bar:

1. **The manifest is pinned to the authoritative text.** Each bar carries a verbatim mirror of the
   plan's exit sentence and the clause decomposition must *reconstruct that mirror exactly*. A
   manifest that accounts for less text than the bar contains cannot pass, so a clause cannot be
   dropped, and a mirror that has drifted from the plan cannot pass either. A manifest holding a
   paraphrase would be the fifth summary; a byte-identical mirror is the estate's own
   cross-repo-fixture pattern applied to prose.

2. **Every clause resolves to exactly one of four states**, and `unavailable` -- could not be
   measured -- never collapses into `pass` or `fail`. A clause nothing measures is `unavailable`,
   because a tool that reported `pass` for an unmeasured clause would have automated the summary
   rather than replaced it.

The mirror exists because the authoritative plan lives in `~/docs`, outside this repository and
outside CI's reach. So the pin has two layers: the *mirror* layer (clauses reconstruct the stored
bar) runs everywhere including CI, and the *source* layer (the stored bar still equals the plan's)
runs wherever the plan is readable. When the plan is unreachable the source layer is reported
`unavailable` and the run is inconclusive -- never quietly passing.

Usage:
    python scripts/attest_wave_exit.py
    python scripts/attest_wave_exit.py --wave 3
    python scripts/attest_wave_exit.py --pin-only
    python scripts/attest_wave_exit.py --record docs/evidence/exit-manifest/$(date -u +%F).json

Exit 0 attested, 1 a clause was measured and missed, 2 inconclusive (something could not be
measured, or nothing in scope was demonstrated at all), 3 the pin is broken and no clause result
means anything until it is reconciled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "docs/operations/wave-exit-manifest.toml"
DEFAULT_PLAN = Path(
    "~/docs/software-delivery-system/2026-07-09-program-phase2-post-mvp-plan.md"
).expanduser()
DEFAULT_OPENAPI_URL = "https://sds.alobar.net/openapi.json"
DEFAULT_TIMEOUT_SECONDS = 300
OUTPUT_RETAINED_CHARS = 20_000

Result = Literal["pass", "fail", "not_applicable", "unavailable"]

#: Ordered weakest-first among *substantive* results. `not_applicable` is deliberately absent:
#: it is neutral rather than weak, so a clause with one passing and one inapplicable check has
#: been demonstrated. Only a clause whose every check is inapplicable is itself inapplicable.
_PRECEDENCE: tuple[Result, ...] = ("fail", "unavailable", "pass")


# --------------------------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Clause:
    """One clause of an exit bar, split into the rule and the running commentary on the rule.

    `text` is the normative half -- what a check must demonstrate. `annotation` is the status
    marks the plan carries inline (`**(✅ 5 drills)**` and the like). Both are verbatim, because
    the pin reconstructs the whole bar from them; the split exists so a check is aimed at the
    rule rather than at a sentence that also records who closed it and when.
    """

    number: int
    separator: str
    text: str
    annotation: str
    checks: tuple[dict[str, Any], ...]
    not_applicable_reason: str | None = None
    note: str = ""

    def rendered(self) -> str:
        return f"{self.separator}{self.text}{self.annotation}"


@dataclass(frozen=True)
class Bar:
    wave: int
    anchor: str
    body: str
    body_sha256: str
    clauses: tuple[Clause, ...]
    closeout: str = ""

    def reconstructed(self) -> str:
        return "".join(clause.rendered() for clause in self.clauses)


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    authoritative_source: str
    bars: tuple[Bar, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_manifest(source: Path | dict[str, Any]) -> Manifest:
    """Load and structurally validate the manifest, refusing shapes that could hide a clause."""
    if isinstance(source, Path):
        with source.open("rb") as handle:
            document = tomllib.load(handle)
    else:
        document = source

    _require(document.get("schema_version") == 1, "manifest schema_version must be 1")
    raw_bars = document.get("bar") or []
    _require(bool(raw_bars), "manifest declares no [[bar]] entries")

    bars: list[Bar] = []
    for raw in raw_bars:
        body = raw["body"]
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        _require(
            digest == raw["body_sha256"],
            f"wave {raw['wave']}: body_sha256 does not match the stored bar text "
            f"(declared {raw['body_sha256'][:16]}…, actual {digest[:16]}…). The mirror was "
            "edited without restating its digest, which is how an invisible whitespace change "
            "gets in.",
        )

        raw_clauses = raw.get("clause") or []
        # `clause_separator` is the punctuation this sentence uses to mark a clause boundary; a
        # clause's own `separator` is the literal glue that reconstructs the bar, and defaults to
        # it. They differ where a bar ends a clause with a full stop, so the guard below keys on
        # the semantic delimiter -- keying it on the literal glue would mean scanning every text
        # for a single space, which matches everything and guards nothing.
        clause_separator = raw["clause_separator"]
        _require(
            bool(clause_separator.strip()),
            f"wave {raw['wave']}: clause_separator must carry punctuation, not whitespace alone",
        )

        _require(
            clause_separator in body,
            f"wave {raw['wave']}: clause_separator {clause_separator!r} does not occur in the "
            "bar at all, so the guards keyed on it are vacuous and one clause could hold the "
            "whole sentence.",
        )
        _require(
            raw["clause_count"] == len(raw_clauses),
            f"wave {raw['wave']}: declares clause_count {raw['clause_count']} and carries "
            f"{len(raw_clauses)} clauses. The count is stated explicitly because it is the "
            "number this whole file exists to protect -- changing it must be a visible edit.",
        )

        clauses: list[Clause] = []
        default_separators = 0
        annotation_occurrences = 0
        for index, raw_clause in enumerate(raw_clauses):
            declared_separator = raw_clause.get("separator")
            separator = (
                ("" if index == 0 else clause_separator)
                if declared_separator is None
                else declared_separator
            )
            # Restating the bar's own separator is not an override; only a DIFFERENT glue is,
            # and only a different glue could smuggle a boundary into itself.
            is_override = index > 0 and separator != clause_separator
            text = raw_clause["text"]
            annotation = raw_clause.get("annotation", "")
            _require(
                index > 0 or separator == "",
                f"wave {raw['wave']}: the first clause may not declare a separator",
            )
            if index > 0 and not is_override:
                default_separators += 1

            # Reconstruction is exact but NOT unique, and the non-unique dimension is exactly the
            # hide-a-clause dimension: a following clause can be swallowed into this one's
            # annotation or into an override separator, leaving the bar byte-identical while the
            # count silently drops. Each of the three routes is closed here.
            _require(
                clause_separator not in text,
                f"wave {raw['wave']} clause {raw_clause['number']}: the normative text contains "
                f"this bar's clause separator ({clause_separator!r}). Two clauses declared as one "
                "reconstruct the bar exactly while halving the count -- split them.",
            )
            _require(
                not (is_override and clause_separator in separator),
                f"wave {raw['wave']} clause {raw_clause['number']}: an override separator may not "
                f"contain the clause separator ({clause_separator!r}) -- that is how a clause is "
                "absorbed into the glue between two others.",
            )
            declared_occurrences = raw_clause.get("annotation_separator_count", 0)
            actual_occurrences = annotation.count(clause_separator)
            _require(
                declared_occurrences == actual_occurrences,
                f"wave {raw['wave']} clause {raw_clause['number']}: the annotation contains the "
                f"clause separator {actual_occurrences} time(s) and declares "
                f"{declared_occurrences}. An annotation carrying the separator is how a clause "
                "hides inside its neighbour's commentary, so it must be declared and counted.",
            )
            annotation_occurrences += actual_occurrences
            checks = tuple(raw_clause.get("check") or ())
            for check in checks:
                _require(
                    check.get("kind") in CHECK_KINDS,
                    f"wave {raw['wave']} clause {raw_clause['number']}: unknown check kind "
                    f"{check.get('kind')!r}",
                )
            # An excused clause may carry no checks: otherwise the excuse silently discards a
            # measured `fail`, turning a red clause into a green run with one manifest line.
            _require(
                not (raw_clause.get("not_applicable_reason") and checks),
                f"wave {raw['wave']} clause {raw_clause['number']}: declares "
                "not_applicable_reason AND checks. The reason short-circuits the result, so the "
                "checks would run and their outcome be discarded -- drop one.",
            )
            clauses.append(
                Clause(
                    number=raw_clause["number"],
                    separator=separator,
                    text=text,
                    annotation=raw_clause.get("annotation", ""),
                    checks=checks,
                    not_applicable_reason=raw_clause.get("not_applicable_reason"),
                    note=raw_clause.get("note", ""),
                )
            )
        _require(bool(clauses), f"wave {raw['wave']}: declares no clauses")
        # Every occurrence of the separator in the bar must be accounted for: either it joins two
        # clauses, or it sits inside an annotation that declared it. An unaccounted occurrence is
        # a clause boundary the manifest does not know about.
        _require(
            body.count(clause_separator) == default_separators + annotation_occurrences,
            f"wave {raw['wave']}: the bar contains the clause separator "
            f"{body.count(clause_separator)} time(s), but the clauses account for "
            f"{default_separators + annotation_occurrences} "
            f"({default_separators} clause boundaries + {annotation_occurrences} declared inside "
            "annotations). An unaccounted separator is a clause boundary nothing is measuring.",
        )
        bars.append(
            Bar(
                wave=raw["wave"],
                anchor=raw["anchor"],
                body=body,
                body_sha256=raw["body_sha256"],
                clauses=tuple(clauses),
                closeout=raw.get("closeout", ""),
            )
        )

    return Manifest(
        schema_version=document["schema_version"],
        authoritative_source=document["authoritative_source"],
        bars=tuple(bars),
    )


# --------------------------------------------------------------------------------------------
# the pin
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pin:
    holds: bool
    reason: str | None
    body: str
    source_body: str | None
    reconstructed: str
    absent_clauses: tuple[str, ...] = ()

    def describe(self) -> str:
        if self.holds:
            return "pin holds: the manifest's clauses reconstruct the plan's bar exactly"
        if self.reason == "anchor_not_found":
            return "the plan carries no line starting with this bar's anchor"
        if self.reason == "anchor_ambiguous":
            return "the plan carries more than one line starting with this bar's anchor"
        if self.reason == "bar_paragraph_extends":
            return (
                "the bar's paragraph now carries more than the pinned sentence -- a clause was "
                f"appended underneath it: {self.source_body!r}"
            )
        if self.reason == "source_diverged":
            return (
                "the plan's bar is no longer the text this manifest was written against -- "
                "reconcile the manifest before trusting any clause result"
            )
        return (
            "the manifest's clauses do not reconstruct the bar: it accounts for "
            f"{len(self.reconstructed)} characters of a {len(self.body)}-character bar"
        )


def verify_pin(bar: Bar, document: str) -> Pin:
    """Check the stored bar against the authoritative plan and against its own clauses."""
    reconstructed = bar.reconstructed()
    lines = document.split("\n")
    indexes = [index for index, line in enumerate(lines) if line.startswith(bar.anchor)]
    if not indexes:
        return Pin(False, "anchor_not_found", bar.body, None, reconstructed)
    if len(indexes) > 1:
        return Pin(False, "anchor_ambiguous", bar.body, None, reconstructed)

    # The bar is its own PARAGRAPH, and the pin covers the paragraph rather than the line.
    # Anchoring on the line alone leaves the most ordinary way a plan grows -- appending a
    # sentence underneath -- entirely outside the pin's field of view, which is the exact
    # direction of the failure this tool exists to catch.
    index = indexes[0]
    tail = lines[index + 1 :]
    if tail and tail[0].strip():
        return Pin(False, "bar_paragraph_extends", bar.body, tail[0].strip(), reconstructed)

    source_body = lines[index].removeprefix(bar.anchor).strip()
    absent = tuple(clause.text for clause in bar.clauses if clause.text not in source_body)

    if source_body != bar.body:
        return Pin(False, "source_diverged", bar.body, source_body, reconstructed, absent)
    if reconstructed != bar.body:
        return Pin(
            False, "clauses_do_not_reconstruct", bar.body, source_body, reconstructed, absent
        )
    return Pin(True, None, bar.body, source_body, reconstructed)


# --------------------------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckOutcome:
    result: Result
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _truncate(text: str) -> str:
    if len(text) <= OUTPUT_RETAINED_CHARS:
        return text
    return text[:OUTPUT_RETAINED_CHARS] + f"\n… [{len(text) - OUTPUT_RETAINED_CHARS} chars elided]"


def run_command_check(spec: dict[str, Any], cwd: Path) -> CheckOutcome:
    """Run a command and classify its exit. Retains argv, exit code and output verbatim.

    The manifest declares which exits mean *unmeasurable* rather than *not met*, because the two
    are the distinction this tool exists to keep. A command that cannot be found or times out is
    always unmeasurable -- it never reports a miss it did not observe.
    """
    argv = [str(part) for part in spec["argv"]]
    timeout = int(spec.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - argv comes from a reviewed in-repo manifest
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return CheckOutcome(
            "unavailable", f"{argv[0]} is not installed on this machine", {"argv": argv}
        )
    except subprocess.TimeoutExpired:
        return CheckOutcome(
            "unavailable", f"timed out after {timeout}s", {"argv": argv, "timeout": timeout}
        )
    except OSError as error:
        return CheckOutcome("unavailable", f"could not run: {error}", {"argv": argv})

    duration = round(time.monotonic() - started, 3)
    evidence = {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": _truncate(completed.stdout),
        "stderr": _truncate(completed.stderr),
        "duration_seconds": duration,
    }
    combined = completed.stdout + completed.stderr
    pattern = spec.get("unavailable_pattern")
    unavailable_codes = set(spec.get("unavailable_exit_codes", ()))

    # A probe explains WHY it could not measure; carrying that up is the whole value of the
    # `unavailable` state, and "exit 7" on its own would bury it.
    said = (
        next((line for line in reversed(combined.strip().splitlines()) if line.strip()), "")
        .removeprefix("UNAVAILABLE ")
        .strip()
    )
    if pattern and re.search(pattern, combined):
        return CheckOutcome("unavailable", said or f"output matched {pattern!r}", evidence)
    if completed.returncode in unavailable_codes or completed.returncode == 127:
        return CheckOutcome("unavailable", said or f"exit {completed.returncode}", evidence)
    if completed.returncode == 0:
        return CheckOutcome("pass", spec.get("proves", "exit 0"), evidence)
    return CheckOutcome("fail", f"exit {completed.returncode}", evidence)


def run_retained_evidence_check(spec: dict[str, Any], cwd: Path) -> CheckOutcome:
    """Assert a retained artifact exists and still hashes to what was recorded.

    This does not re-derive the fact the artifact records; it establishes that the evidence the
    claim rests on is retained and unaltered. That is why a clause resting on retained evidence
    also carries a live check wherever one is possible, and why the report names which is which.
    """
    raw = Path(spec["path"]).expanduser()
    path = raw if raw.is_absolute() else cwd / raw
    if not path.exists():
        return CheckOutcome(
            "unavailable",
            f"retained artifact is missing: {spec['path']} (evidence in a scratchpad is "
            "not evidence)",
            {"path": str(path)},
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    evidence = {"path": str(path), "sha256": digest, "declared_sha256": spec["sha256"]}
    if digest != spec["sha256"]:
        return CheckOutcome(
            "fail",
            f"retained artifact changed since it was pinned ({digest[:16]}… != "
            f"{spec['sha256'][:16]}…)",
            evidence,
        )
    return CheckOutcome(
        "pass", spec.get("proves", "retained artifact matches its digest"), evidence
    )


def run_routes_served_check(spec: dict[str, Any], openapi_url: str) -> CheckOutcome:
    """The class `attest_exit_criteria.py` already covers, expressed as one check kind."""
    try:
        request = urllib.request.Request(
            openapi_url, headers={"User-Agent": "wsp239-attest-wave-exit/1"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https only
            served = set(json.load(response)["paths"])
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as error:
        return CheckOutcome(
            "unavailable", f"could not read {openapi_url}: {error}", {"openapi_url": openapi_url}
        )

    routes = list(spec["routes"])
    missing = [route for route in routes if route not in served]
    evidence = {"openapi_url": openapi_url, "routes": routes, "missing": missing}
    if missing:
        return CheckOutcome("fail", f"production does not serve {missing}", evidence)
    return CheckOutcome(
        "pass", spec.get("proves", f"production serves all {len(routes)} routes"), evidence
    )


CHECK_KINDS = {"command", "retained_evidence", "routes_served"}


def run_check(spec: dict[str, Any], cwd: Path, openapi_url: str) -> CheckOutcome:
    kind = spec["kind"]
    if kind == "command":
        return run_command_check(spec, cwd)
    if kind == "retained_evidence":
        return run_retained_evidence_check(spec, cwd)
    return run_routes_served_check(spec, openapi_url)


# --------------------------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClauseOutcome:
    result: Result
    checks: tuple[CheckOutcome, ...] = ()
    detail: str = ""


def resolve_clause(clause: Clause, results: list[Result], checks: tuple = ()) -> ClauseOutcome:
    """A clause is as strong as its weakest check, and an unchecked clause is unmeasured."""
    if clause.not_applicable_reason:
        return ClauseOutcome("not_applicable", checks, clause.not_applicable_reason)
    if not results:
        return ClauseOutcome("unavailable", checks, "no check measures this clause")
    substantive = [result for result in results if result != "not_applicable"]
    if not substantive:
        return ClauseOutcome("not_applicable", checks, "every check is inapplicable here")
    for candidate in _PRECEDENCE:
        if candidate in substantive:
            return ClauseOutcome(candidate, checks)
    return ClauseOutcome("unavailable", checks)


@dataclass(frozen=True)
class Verdict:
    name: str
    exit_code: int

    @classmethod
    def of(cls, *, pin_broken: bool, clause_results: list[Result]) -> Verdict:
        if pin_broken:
            return cls("pin_broken", 3)
        if "fail" in clause_results:
            return cls("failed", 1)
        if "unavailable" in clause_results:
            return cls("inconclusive", 2)
        if "pass" not in clause_results:
            # Nothing was demonstrated. Without this, a run whose every clause is declared
            # inapplicable -- or that matched no clause at all -- reports "every clause is
            # demonstrated" having demonstrated nothing, which is this tool's own failure mode
            # turned inward.
            return cls("nothing_demonstrated", 2)
        return cls("attested", 0)


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------

_MARK: dict[Result, str] = {
    "pass": "PASS",
    "fail": "FAIL",
    "not_applicable": "N/A ",
    "unavailable": "UNAV",
}
_VERDICT_LINE = {
    "attested": "ATTESTED -- every clause of every bar in scope is demonstrated.",
    "failed": "FAILED -- a clause was measured and is not met.",
    "inconclusive": "INCONCLUSIVE -- a clause could not be measured. This is not a pass.",
    "nothing_demonstrated": (
        "NOTHING DEMONSTRATED -- no clause in scope resolved to a pass. This is not an "
        "attestation; check whether every clause was declared inapplicable or skipped."
    ),
    "pin_broken": (
        "PIN BROKEN -- the manifest no longer matches the authoritative bar. No clause result "
        "below is evidence of anything until the manifest is reconciled with the plan."
    ),
}


def pin_for(bar: Bar, document: str | None) -> tuple[Pin, str]:
    """Resolve both pin layers. Without the plan only the mirror layer can run.

    `unverifiable` is not a softer `verified`: the clauses reconstruct the stored bar, but
    whether that bar is still the plan's could not be established, so the run is inconclusive.
    """
    if document is not None:
        pin = verify_pin(bar, document)
        return pin, "verified" if pin.holds else "broken"

    reconstructed = bar.reconstructed()
    holds = reconstructed == bar.body
    pin = Pin(
        holds,
        None if holds else "clauses_do_not_reconstruct",
        bar.body,
        None,
        reconstructed,
    )
    return pin, "unverifiable" if holds else "broken"


def _print_pin(bar: Bar, pin: Pin, pin_status: str) -> None:
    if pin_status == "broken":
        print(f"  PIN BROKEN : {pin.describe()}")
        if pin.absent_clauses:
            print("  clauses declared by the manifest but ABSENT from the plan's bar:")
            for text in pin.absent_clauses:
                print(f"    - {text}")
        if pin.source_body is not None and pin.source_body != bar.body:
            print(f"    manifest mirror : {len(bar.body)} chars")
            print(f"    plan's bar      : {len(pin.source_body)} chars")
        print(
            "  measurement SKIPPED for this bar: a clause result measured against a bar that "
            "is not the rule would read as a demonstration of the rule."
        )
    elif pin_status == "unverifiable":
        print(
            "  PIN PARTIAL: clauses reconstruct the stored bar, but the plan is not readable "
            "here, so divergence from the authoritative text could not be ruled out."
        )
    else:
        print(f"  pin        : {pin.describe()}")


def _attest_clauses(
    bar: Bar, pin_status: str, args: argparse.Namespace
) -> tuple[list[Result], list[dict[str, Any]]]:
    results: list[Result] = []
    record: list[dict[str, Any]] = []
    for clause in bar.clauses:
        # Nothing is measured in pin-only mode, nor when the pin is broken: a clause result taken
        # against a bar that is not the rule is worse than no result, because it reads as a
        # demonstration. Both leave every clause `unavailable`.
        measured = () if (args.pin_only or pin_status == "broken") else clause.checks
        outcomes = tuple(run_check(check, args.repo_root, args.openapi_url) for check in measured)
        outcome = resolve_clause(clause, [o.result for o in outcomes], outcomes)
        results.append(outcome.result)

        print(f"  [{_MARK[outcome.result]}] {bar.wave}.{clause.number} {clause.text}")
        if outcome.detail:
            print(f"         {outcome.detail}")
        for check, check_outcome in zip(measured, outcomes, strict=True):
            print(f"         · {check['kind']}: {check_outcome.result} — {check_outcome.detail}")

        record.append(
            {
                "number": clause.number,
                "text": clause.text,
                "annotation": clause.annotation,
                "result": outcome.result,
                "detail": outcome.detail,
                "note": clause.note,
                "checks": [
                    {
                        "kind": check["kind"],
                        "basis": check.get("basis", "live"),
                        "result": check_outcome.result,
                        "detail": check_outcome.detail,
                        "evidence": check_outcome.evidence,
                    }
                    for check, check_outcome in zip(measured, outcomes, strict=True)
                ],
            }
        )
    return results, record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attest a wave exit bar clause by clause.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--plan", type=Path, default=None, help="authoritative plan document")
    parser.add_argument("--wave", type=int, action="append", help="limit to these waves")
    parser.add_argument("--openapi-url", default=DEFAULT_OPENAPI_URL)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--pin-only",
        action="store_true",
        help="verify the pin and skip measurement (every clause reports unavailable)",
    )
    parser.add_argument("--record", type=Path, default=None, help="write a retained run record")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    plan_path = args.plan or Path(manifest.authoritative_source).expanduser()
    document = plan_path.read_text(encoding="utf-8") if plan_path.exists() else None

    bars = [bar for bar in manifest.bars if not args.wave or bar.wave in args.wave]
    if not bars:
        print(f"no bar matches --wave {args.wave}", file=sys.stderr)
        return 1

    print(f"manifest : {args.manifest}")
    print(f"plan     : {plan_path}{'' if document else '  (NOT READABLE)'}")
    print(f"openapi  : {args.openapi_url}\n")

    all_results: list[Result] = []
    record_bars: list[dict[str, Any]] = []

    for bar in bars:
        pin, pin_status = pin_for(bar, document)
        print(f"=== Wave {bar.wave} exit bar — {len(bar.clauses)} clauses ===")
        print(f"  bar sha256 : {bar.body_sha256}")
        _print_pin(bar, pin, pin_status)
        if pin_status == "unverifiable":
            all_results.append("unavailable")

        results, record_clauses = _attest_clauses(bar, pin_status, args)
        all_results.extend(results)
        print()
        record_bars.append(
            {
                "wave": bar.wave,
                "anchor": bar.anchor,
                "body": bar.body,
                "body_sha256": bar.body_sha256,
                "closeout": bar.closeout,
                "pin": pin_status,
                "pin_reason": pin.reason,
                "clauses": record_clauses,
            }
        )

    verdict = Verdict.of(
        pin_broken=any(entry["pin"] == "broken" for entry in record_bars),
        clause_results=all_results,
    )
    tally = {state: all_results.count(state) for state in _MARK if all_results.count(state)}
    print(f"{_VERDICT_LINE[verdict.name]}")
    print(f"clauses: {tally}")

    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(
            json.dumps(
                {
                    "measured_at": datetime.now(UTC).isoformat(),
                    "verdict": verdict.name,
                    "exit_code": verdict.exit_code,
                    "manifest": str(args.manifest),
                    "manifest_sha256": hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest(),
                    "authoritative_source": str(plan_path),
                    "authoritative_source_readable": document is not None,
                    "openapi_url": args.openapi_url,
                    "pin_only": args.pin_only,
                    "bars": record_bars,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"retained record: {args.record}")

    return verdict.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
