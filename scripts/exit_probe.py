#!/usr/bin/env python3
"""Tri-state probes for the wave-exit manifest's `command` checks.

Each probe answers one clause of one exit bar and exits **0 met, 1 not met, 7 could not
measure**. The third code is the reason this file exists: a probe that cannot reach production,
or that needs a fact no read surface serves, must say so rather than return a verdict it did not
observe. `scripts/attest_wave_exit.py` maps exit 7 to `unavailable`, which makes the whole run
inconclusive.

Probes print a one-line summary on stdout and their supporting numbers as JSON, so the retained
run record carries what the measurement saw rather than a paraphrase of it.

Usage:
    python scripts/exit_probe.py <probe> [args…]
    python scripts/exit_probe.py --list
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_BASE = os.environ.get("ORCHESTRATOR_API_URL", "https://sds.alobar.net")
SYSTEM_BEARER_UUID = "221a48d5-3f29-4898-b300-b4820140c880"  # orchestrator-system; value at runtime
CREDENTIAL_KEY_ID = "orchestrator-system"
USER_AGENT = "wsp239-exit-probe/1 (+repo orchestrator)"

PASS, FAIL, UNAVAILABLE = 0, 1, 7


class Unavailable(RuntimeError):
    """The measurement could not be taken. Never a verdict about the subject."""


def _bearer() -> str:
    """Fetch the SYSTEM bearer in-process. The value never reaches argv or stdout."""
    if token := os.environ.get("ORCHESTRATOR_API_TOKEN"):
        return token
    access = os.environ.get("BWS_ACCESS_TOKEN")
    if not access:
        try:
            access = subprocess.run(  # noqa: S603
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    "Claude",
                    "-a",
                    "BWS_ACCESS_TOKEN_SDS",
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise Unavailable(f"Keychain lookup failed: {error}") from error
    if not access:
        raise Unavailable(
            "no BWS access token (Keychain service=Claude account=BWS_ACCESS_TOKEN_SDS), so the "
            "orchestrator read API is unreachable from here"
        )
    # FORCE_COLOR/CLICOLOR_FORCE make `bws` wrap its JSON in ANSI escapes even on a pipe.
    environment = {
        k: v for k, v in os.environ.items() if k not in {"FORCE_COLOR", "CLICOLOR_FORCE"}
    }
    environment["BWS_ACCESS_TOKEN"] = access
    try:
        completed = subprocess.run(  # noqa: S603
            ["bws", "secret", "get", SYSTEM_BEARER_UUID, "--output", "json", "--color", "no"],
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"bws unavailable: {error}") from error
    if completed.returncode != 0:
        raise Unavailable(f"bws secret get exited {completed.returncode}")
    try:
        return json.loads(completed.stdout)["value"]
    except (json.JSONDecodeError, KeyError) as error:
        raise Unavailable(f"could not parse the bws response: {error}") from error


_TOKEN: str | None = None


def api_get(path: str):
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _bearer()
    request = urllib.request.Request(
        API_BASE + path,
        headers={
            "Authorization": f"Bearer {_TOKEN}",
            "X-Credential-Key-Id": CREDENTIAL_KEY_ID,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise Unavailable(f"GET {path} answered HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise Unavailable(f"GET {path} failed: {error}") from error


def ledger() -> list[dict]:
    # include_inactive=true or the bare call returns [] -- every production unit is terminal.
    return api_get("/api/v1/status-ledger?include_inactive=true")


def report(ok: bool, summary: str, **evidence) -> int:
    # The host is env-overridable, so a record that does not name it cannot substantiate the
    # `proves` strings that say "against production".
    evidence.setdefault("measured_against", API_BASE)
    print(summary)
    if evidence:
        print(json.dumps(evidence, indent=2, sort_keys=True))
    return PASS if ok else FAIL


def _slo_metrics(payload: dict) -> dict[str, dict]:
    """The report's status-bearing metrics, refusing to answer for a response that has none.

    A report carrying no `status`-bearing metric is a SHAPE that changed, not a report that ran
    and computed nothing -- and the two are the distinction this whole file exists to keep. Every
    caller here would otherwise read the empty mapping as a miss and report the clause not met on
    a question it never got to ask.
    """
    metrics = {k: v for k, v in payload.items() if isinstance(v, dict) and "status" in v}
    if not metrics:
        raise Unavailable(
            "the SLO report served no status-bearing metric "
            f"(keys: {sorted(payload)}), so what it computes could not be measured"
        )
    return metrics


# --------------------------------------------------------------------------------------------
# Wave 1
# --------------------------------------------------------------------------------------------


def drills_are_scripted() -> int:
    """Wave 1 clause 1: the drills exist as re-runnable scripts and the runner reaches every one.

    `run-drills.sh` selects drills by glob rather than by an enumerated list, so reachability is
    established by expanding *its own* pattern and comparing the expansion with the drills on
    disk. Checking for filenames in the runner's text instead would report a false miss on a
    correctly-wired harness -- it did, on the first run of this probe.
    """
    drills = sorted(path.name for path in REPO_ROOT.glob("scripts/drill-*.sh"))
    runner = REPO_ROOT / "scripts/run-drills.sh"
    if not runner.exists():
        return report(False, "FAIL scripts/run-drills.sh is absent", drills=drills)

    patterns = re.findall(r"for\s+\w+\s+in\s+(\S+\.sh)\b", runner.read_text(encoding="utf-8"))
    if not patterns:
        return report(False, "FAIL run-drills.sh iterates no drill pattern", drills=drills)
    reached = sorted({path.name for pattern in patterns for path in REPO_ROOT.glob(pattern)})
    unreached = [name for name in drills if name not in reached]
    ok = len(drills) == 5 and not unreached
    return report(
        ok,
        f"{'PASS' if ok else 'FAIL'} {len(drills)} drill scripts, {len(reached)} reached by "
        f"run-drills.sh's own pattern {patterns}",
        drills=drills,
        reached=reached,
        unreached=unreached,
    )


def slo_report_runs() -> int:
    """Wave 1 clause 2: the SLO report computes real numbers against production, now."""
    payload = api_get("/api/v1/slo-report")
    metrics = _slo_metrics(payload)
    computed = sorted(k for k, v in metrics.items() if v["status"] == "computed")
    return report(
        bool(computed),
        f"{'PASS' if computed else 'FAIL'} SLO report served "
        f"{len(computed)}/{len(metrics)} metrics computed",
        window=[payload.get("since"), payload.get("until")],
        computed=computed,
        statuses={k: v["status"] for k, v in sorted(metrics.items())},
    )


def completed_units_carry_adjudications() -> int:
    """Wave 1 clause 3, the half production can answer.

    This is a NECESSARY condition, not the criterion: it establishes that no completed unit
    finished with zero recorded outcomes. The sufficient form -- every *required* criterion
    carries one -- needs the required-criteria set, which `required_criteria_are_readable`
    reports is on no read surface.
    """
    rows = [row for row in ledger() if row["unit_state"] == "completed"]
    if not rows:
        raise Unavailable("no completed units in the ledger to measure")
    bare = []
    for row in rows:
        pack = api_get(f"/api/v1/work-units/{row['unit_id']}/evidence-pack")
        if "adjudications" not in pack:
            # An absent key must not read as "this unit completed with nothing adjudicated".
            raise Unavailable(
                "the evidence pack no longer projects an `adjudications` key "
                f"(keys: {sorted(pack)}), so a unit carrying none cannot be told from a "
                "projection that stopped carrying them"
            )
        current = [a for a in pack["adjudications"] if a.get("current")]
        if not current or any(not a.get("outcome") for a in current):
            bare.append(
                {
                    "unit_id": row["unit_id"],
                    "unit_key": row["unit_key"],
                    "current_adjudications": len(current),
                }
            )
    return report(
        not bare,
        f"{'PASS' if not bare else 'FAIL'} {len(rows) - len(bare)}/{len(rows)} completed units "
        "carry at least one current adjudication with an outcome",
        completed_units=len(rows),
        without_adjudications=bare,
    )


def required_criteria_are_readable() -> int:
    """Wave 1 clause 3, the half production cannot answer -- and why that is the finding.

    Proving 'no unit completes with an unadjudicated AC' over the real population needs the
    REQUIRED criterion set per unit. The evidence pack projects `adjudications` and `evidence`
    and no criteria list, so the comparison cannot be made from outside the database.

    It reports `unavailable` in BOTH branches and can never return a verdict. A key appearing
    changes only the message -- to one naming itself as the next piece of work -- because a probe
    that promoted itself on the mere presence of a `criteri`-ish key would flip this clause green
    while nothing had ever compared the required set against the adjudicated one. Do not describe
    this as starting to work by itself; someone has to implement the comparison and delete it.
    """
    rows = [row for row in ledger() if row["unit_state"] == "completed"]
    if not rows:
        raise Unavailable("no completed units in the ledger to measure")
    pack = api_get(f"/api/v1/work-units/{rows[0]['unit_id']}/evidence-pack")
    candidates = [k for k in pack if "criteri" in k.lower()]
    if not candidates:
        raise Unavailable(
            "the required-criteria set is served by no production read surface "
            f"(evidence-pack keys: {sorted(pack)}), so completeness cannot be measured over the "
            "real population -- only the guard that enforces it can be exercised, in the suite"
        )
    # A key APPEARING is not the comparison. Returning pass here would let any release that adds
    # a `criteria`-ish key flip this clause green while nothing had ever compared the required
    # set against the adjudicated one -- a probe that silently removes itself.
    raise Unavailable(
        f"the evidence pack now serves {candidates}, so this is measurable at last -- but the "
        "required-vs-adjudicated comparison is not implemented here, so the clause remains "
        "unmeasured. Implement it and remove this probe."
    )


def budget_breach_is_instrumented() -> int:
    """Wave 1 clause 4, live half: the breach counter is a real metric, not `not_instrumented`.

    The demonstration that a capped unit HALTS is historical and is carried by the retained
    WS-P2.4 closeout; a windowed counter reading zero would mean no recent breach, never that
    the cap stopped working, so this probe deliberately does not assert a non-zero value.
    """
    payload = api_get("/api/v1/slo-report")
    # `_slo_metrics` first, so "the report serves metrics and not this one" -- genuinely not
    # instrumented -- stays distinguishable from "the report is no longer a report", which is
    # unmeasurable. Reading only `budget_breach` would collapse the two into a miss.
    metrics = _slo_metrics(payload)
    metric = payload.get("budget_breach")
    if not isinstance(metric, dict):
        return report(
            False,
            "FAIL the SLO report serves no budget_breach metric",
            served_metrics=sorted(metrics),
        )
    if "status" not in metric:
        # `_slo_metrics` proves SOME metric carries a status; this one is the subject, and
        # `.get("status") != "not_instrumented"` reads `None` as instrumented -- a PASS for
        # something never observed. Guarding only the report and not the metric was this
        # probe's own version of the defect it exists to report.
        raise Unavailable(
            f"the budget_breach metric carries no `status` (keys: {sorted(metric)}), so whether "
            "the counter is instrumented could not be read"
        )
    ok = metric["status"] != "not_instrumented"
    return report(
        ok,
        f"{'PASS' if ok else 'FAIL'} budget_breach is {metric.get('status')} "
        f"(value {metric.get('value')} in window)",
        metric=metric,
    )


def lifecycle_guards_are_wired() -> int:
    """Wave 1 clause 5: run the guard that enforces it, rather than asserting it is enforced."""
    target = "tests/architecture/test_unreachable_guards.py"
    if not (REPO_ROOT / target).exists():
        return report(False, f"FAIL {target} is absent -- the guard this clause names is gone")
    pytest = REPO_ROOT / ".venv/bin/pytest"
    if not pytest.exists():
        raise Unavailable(f"{pytest} is absent; cannot execute the guard")
    completed = subprocess.run(  # noqa: S603
        [str(pytest), target, "-q"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=600
    )
    tail = completed.stdout.strip().splitlines()[-1:] or [""]
    return report(
        completed.returncode == 0,
        f"{'PASS' if completed.returncode == 0 else 'FAIL'} {target}: {tail[0]}",
        exit_code=completed.returncode,
        summary_line=tail[0],
    )


# --------------------------------------------------------------------------------------------
# Wave 2
# --------------------------------------------------------------------------------------------


def evidence_pack_reached_a_merged_pr(*repos: str) -> int:
    """Wave 2 clause 1: count the Evidence Pack marker by REST enumeration.

    Never `gh search`, which is blind to comments on these repositories and reports 0 whether or
    not a marker is present -- a zero from it is not evidence of absence.
    """
    if not repos:
        # A manifest that names no repository is an authoring fact, not the marker being absent.
        raise Unavailable("no repository was declared; the manifest must name where to count")
    marker = "sds-evidence-pack"
    found: dict[str, int] = {}
    for repo in repos:
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    "gh",
                    "api",
                    f"repos/{repo}/issues/comments",
                    "--paginate",
                    "--jq",
                    f'[.[] | select(.body | contains("{marker}"))] | length',
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise Unavailable(f"gh is unavailable here: {error}") from error
        if completed.returncode != 0:
            raise Unavailable(
                f"gh api against {repo} exited {completed.returncode}: "
                f"{completed.stderr.strip()[:200]}"
            )
        counts = [int(line) for line in completed.stdout.split() if line.isdigit()]
        if not counts:
            # The estate rule that a search zero is not evidence of absence, made mechanical: a
            # zero must come from a page that reported zero, never from output nothing parsed.
            raise Unavailable(
                f"gh reported no count for {repo} (exit 0, stdout "
                f"{completed.stdout.strip()[:120]!r}), so the marker count is unread rather "
                "than zero"
            )
        found[repo] = sum(counts)
    total = sum(found.values())
    return report(
        total > 0,
        f"{'PASS' if total else 'FAIL'} {total} '{marker}' marker comment(s) across "
        f"{len(repos)} repositories",
        per_repository=found,
        method="REST comment enumeration",
    )


ALL_HOPS = (
    "intent",
    "unit",
    "pr",
    "commit",
    "artifact",
    "deployment",
    "conditions",
    "observations",
)


def _chains(path: str) -> list[dict]:
    """The chains a traceability answer carries. An absent key is not an absent chain."""
    payload = api_get(path)
    if "chains" not in payload:
        raise Unavailable(
            f"the traceability answer carries no `chains` key (keys: {sorted(payload)}), so "
            "'no chain resolves this' cannot be told from 'the response was reshaped'"
        )
    return payload["chains"] or []


def _hops(chain: dict) -> dict[str, int]:
    """How many entries each hop of one chain carries; a singular hop counts 1 or 0.

    Read defensively for the same reason `_chains` is, one level down, and it fails BOTH ways.
    A renamed key counts 0 and reports the clause NOT MET on a fact about the response. A hop
    whose value stopped being a container is counted by `len`, and `len("sha-abc")` is 7 -- so a
    scalar reads as a populated hop and the probe PASSES on something it never observed. Both
    are refused rather than counted.
    """
    counts: dict[str, int] = {}
    for hop in ALL_HOPS:
        if hop not in chain:
            raise Unavailable(
                f"a traceability chain carries no `{hop}` hop (hops: {sorted(chain)}), so an "
                "unanswered hop cannot be told from a renamed one"
            )
        value = chain[hop]
        if value is None:
            counts[hop] = 0
        elif isinstance(value, dict):
            counts[hop] = 1
        elif isinstance(value, list):
            counts[hop] = len(value)
        else:
            raise Unavailable(
                f"the `{hop}` hop is a {type(value).__name__} rather than an object or a list, "
                "and counting its length would read a scalar as a populated hop"
            )
    return counts


def _release_unit_ids(pack: dict) -> list[str]:
    """The unit ids a release evidence pack holds. An unreadable projection is not an empty one."""
    ids = []
    for entry in pack["units"]:
        unit = entry.get("work_unit") if isinstance(entry, dict) else None
        if not isinstance(unit, dict) or not unit.get("id"):
            raise Unavailable(
                "a release evidence pack unit carries no `work_unit.id`, so whether the "
                "release's chain set covers every unit of the release could not be established"
            )
        ids.append(str(unit["id"]))
    return ids


def _chain_unit_ids(chains: list[dict]) -> list[str]:
    """The unit ids a set of chains resolves. Mirrors `_release_unit_ids`, one surface over."""
    ids = []
    for chain in chains:
        unit = chain.get("unit")
        if not isinstance(unit, dict) or not unit.get("id"):
            raise Unavailable(
                "a traceability chain carries no `unit.id`, so which unit it answers for could "
                "not be established"
            )
        ids.append(str(unit["id"]))
    return ids


def _a_production_chain_carries_a_condition() -> dict:
    """Run the conditions join against something KNOWN to be present, and report what it found.

    This is the estate's own rule made mechanical: a zero is not evidence of absence until the
    same query has answered non-zero for a subject that has one. Without it, a join that silently
    stopped carrying conditions would make EVERY release read "no divergence" for ever, and the
    hop would be permanently excused with nothing saying so -- the switched-off reporting
    obligation, in hop form.

    A per-unit read that fails is counted and skipped rather than raised: it must never
    manufacture a demonstration, and it must not turn a clause the rest of the run measured into
    an unmeasurable one. A renamed or scalar hop still raises out of `_hops`, loudly, because that
    is a fact about the response rather than about one unit.
    """
    scanned = unread = 0
    for row in ledger():
        scanned += 1
        try:
            chains = _chains(f"/api/v1/traceability?work_unit_id={row['unit_id']}")
        except Unavailable:
            unread += 1
            continue
        for chain in chains:
            if _hops(chain)["conditions"]:
                return {"carrier_unit_key": row["unit_key"], "scanned": scanned, "unread": unread}
    return {"carrier_unit_key": None, "scanned": scanned, "unread": unread}


def _conditions_hop_is_inapplicable(pack: dict, chains: list[dict]) -> tuple[str | None, dict]:
    """Whether THIS release's empty `conditions` hop is inapplicable, and what established that.

    `ReconciliationCondition` records a divergence between pushed reality and stored lifecycle
    state, so a release that went perfectly has none and requiring the hop non-empty makes the
    clause satisfiable only by a release that went wrong (HQ ruling, 2026-08-06). But "there was
    nothing to carry" is a claim about the world, and it is refused unless two things were
    MEASURED in this run:

      * the query looked at every unit of the release -- a chain set short of the release's own
        unit list cannot report "no divergence", only "no divergence among the units it read",
        and a divergence on an omitted unit is exactly the case this must not excuse;
      * the conditions join demonstrably still carries conditions somewhere in production.

    Either unmet and the hop is NOT excused: it stays unanswered and the release fails on it,
    which is what it did before this existed. Nothing a manifest, an argument or an environment
    variable can say reaches this function -- an inapplicable hop is earned from the two readings
    below or it is not granted.

    A release resolving to NO chain is refused first and separately. Both readings would be
    vacuously satisfied by it -- nothing uncovered, because nothing was covered -- and a release
    nothing was read for is the empty population that lets a check pass having asked no question.
    """
    if not chains:
        return None, {"chains": 0}
    covered = set(_chain_unit_ids(chains))
    uncovered = sorted(set(_release_unit_ids(pack)) - covered)
    demonstration: dict = {"units_not_covered_by_a_chain": uncovered}
    if uncovered:
        return None, demonstration
    demonstration |= _a_production_chain_carries_a_condition()
    if not demonstration["carrier_unit_key"]:
        return None, demonstration
    return (
        "the release's chain set covers every unit of the release and carries no divergence, and "
        "the conditions join is demonstrably still carrying them elsewhere in production "
        f"(unit {demonstration['carrier_unit_key']}) -- so a divergence exists to be recorded "
        "and this release has none",
        demonstration,
    )


#: The hops that may EARN `not_applicable`, and the reading that earns it for each. Membership
#: excuses nothing by itself. `observations` is deliberately absent and must stay absent: a
#: unit-scoped observation is an ordinary thing that nothing currently produces, which is a real
#: gap in the system rather than a condition of the world, and marking it inapplicable would
#: convert an absence into a shrug.
INAPPLICABLE_WHEN: dict[str, Callable[[dict, list[dict]], tuple[str | None, dict]]] = {
    "conditions": _conditions_hop_is_inapplicable,
}


def traceability_answers_for_a_real_release(*required: str) -> int:
    """Wave 2 clause 2: a production-anchored chain resolves the hops the caller requires.

    The required hops are an ARGUMENT, declared in the manifest, because which hops make a chain
    "end-to-end" is a judgment and burying it in this file would hide it. Whatever is required,
    the full hop census is reported: measured 2026-08-05, no single production chain spans
    everything -- PR-bearing units carry `pr` and no release binding, and the deploy-verification
    units that carry the binding never had a PR.
    """
    if not required:
        raise Unavailable("no required hops were declared; the manifest must name them")
    unknown = [hop for hop in required if hop not in ALL_HOPS]
    if unknown:
        raise Unavailable(f"not hops of this chain: {unknown}")
    chains = _chains("/api/v1/traceability?environment=production")
    if not chains:
        return report(False, "FAIL no production-anchored chain exists")
    census = []
    complete = []
    for chain in chains:
        hops = _hops(chain)
        census.append({"unit_key": chain["unit"]["unit_key"], "hops": hops})
        if all(hops[hop] for hop in required):
            complete.append(chain["unit"]["unit_key"])
    empty_everywhere = sorted(hop for hop in ALL_HOPS if not any(c["hops"][hop] for c in census))
    return report(
        bool(complete),
        f"{'PASS' if complete else 'FAIL'} {len(complete)}/{len(chains)} production chains "
        f"resolve {' -> '.join(required)}"
        + (f"; hops empty on every chain: {empty_everywhere}" if empty_everywhere else ""),
        required_hops=list(required),
        complete_chains=complete,
        hop_census=census,
        empty_on_every_chain=empty_everywhere,
    )


def release_chain_answers_every_hop(*revisions: str) -> int:
    """Wave 2 clause 2 under the 2026-08-05 ruling on what "end-to-end" means.

    THE RULING. "End-to-end for a real release" is the RELEASE's chain assembled across its
    units, not one unit's chain spanning every hop. Requiring a single chain would require the
    system to violate its own design: `record_release_artifact` refuses until a unit is
    COMPLETED, so a release binding necessarily lands on a later unit than the one that opened
    the pull request, and no unit can ever carry both.

    WHICH REVISIONS ARE REAL RELEASES IS THE MANIFEST'S DECLARATION, NOT THIS PROBE'S FINDING,
    and the guard below is narrower than that word: it refuses a revision that never shipped (no
    release-artifact binding), and nothing here can tell a production release from a fixture.
    That is exactly why the revisions are an argument. The estate holds a drill-fixture revision
    whose composed chain does answer every hop -- on an artifact digest of `sha256:aaaa…` -- and
    it would clear this guard comfortably. What keeps it out is the manifest not naming it, in
    the open, next to the reason.

    Two surfaces, each answering the half it owns. The release-level evidence pack establishes
    that the revision shipped at all; the traceability query anchored on the same revision
    supplies the hops, because the clause is about what THAT query answers. The hop list is the
    whole chain (`ALL_HOPS`), which is the plan's own enumeration of the C question-list.

    A hop may resolve INAPPLICABLE rather than unanswered, but only by earning it -- see
    `INAPPLICABLE_WHEN`. The hop list itself never shrinks: every hop is required of every
    release, and an inapplicable one is recorded with the reading that established it, so the
    record shows a measurement rather than an exemption.
    """
    if not revisions:
        raise Unavailable("no revision was declared; the manifest must name the real release(s)")
    census = []
    complete = []
    for revision in revisions:
        pack = api_get(f"/api/v1/revisions/{revision}/evidence-pack")
        for key in ("units", "release_artifacts", "deployments"):
            if key not in pack:
                raise Unavailable(
                    f"the release evidence pack carries no `{key}` key (keys: {sorted(pack)}), "
                    "so a revision that shipped could not be told from one that did not"
                )
        if not pack["release_artifacts"]:
            raise Unavailable(
                f"revision {revision} carries no release-artifact binding, so nothing was "
                "released and this measurement has no subject"
            )
        chains = _chains(f"/api/v1/traceability?revision_id={revision}")
        union = {hop: sum(_hops(chain)[hop] for chain in chains) for hop in ALL_HOPS}
        missing = sorted(hop for hop, count in union.items() if not count)
        inapplicable: dict[str, str] = {}
        demonstrations: dict[str, dict] = {}
        for hop, resolve in INAPPLICABLE_WHEN.items():
            if hop not in missing:
                continue
            reason, demonstration = resolve(pack, chains)
            demonstrations[hop] = demonstration
            if reason is not None:
                missing.remove(hop)
                inapplicable[hop] = reason
        census.append(
            {
                "revision_id": revision,
                # `chains` is what the hops were composed from; `pack_units` is what the release
                # pack holds. They are two different counts and must not share a name.
                "chains": len(chains),
                "pack_units": len(pack["units"]),
                "release_artifacts": len(pack["release_artifacts"]),
                "deployments": len(pack["deployments"]),
                "composed_hops": union,
                "unanswered_hops": missing,
                # Both are recorded whether or not the hop was excused, so the record shows what
                # the reading found rather than only that it granted something.
                "inapplicable_hops": inapplicable,
                "inapplicability_measured": demonstrations,
            }
        )
        if not missing:
            complete.append(revision)
    excused = sorted({hop for row in census for hop in row["inapplicable_hops"]})
    return report(
        bool(complete),
        f"{'PASS' if complete else 'FAIL'} {len(complete)}/{len(revisions)} declared real "
        f"release(s) answer {' -> '.join(ALL_HOPS)} composed across their units"
        + (f"; inapplicable on measurement: {excused}" if excused else ""),
        required_hops=list(ALL_HOPS),
        complete_releases=complete,
        release_census=census,
    )


def tracker_is_a_projection() -> int:
    """Wave 2 clause 3: canonical-side bindings exist and the import ban is enforced."""
    bindings = api_get("/api/v1/tracker-bindings")
    if isinstance(bindings, list):
        rows = bindings
    elif isinstance(bindings, dict) and "bindings" in bindings:
        rows = bindings["bindings"] or []
    else:
        # A dict answer with no `bindings` key is a reshaped surface, not an empty estate.
        raise Unavailable(
            f"the tracker-bindings answer is a {type(bindings).__name__} carrying no `bindings` "
            "key, so an empty projection cannot be told from a changed response"
        )
    target = "tests/tracker_projection_adapter"
    pytest = REPO_ROOT / ".venv/bin/pytest"
    if not pytest.exists():
        raise Unavailable(f"{pytest} is absent; cannot execute the isolation tests")
    completed = subprocess.run(  # noqa: S603
        [str(pytest), target, "-q"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=600
    )
    ok = bool(rows) and completed.returncode == 0
    return report(
        ok,
        f"{'PASS' if ok else 'FAIL'} {len(rows)} canonical tracker binding(s); "
        f"adapter isolation suite exit {completed.returncode}",
        bindings=len(rows),
        isolation_exit_code=completed.returncode,
    )


def every_pr_producing_unit_is_bound() -> int:
    """Wave 2 clause 4 (the precondition): the WS-P2.16 guard is deployed and has held.

    The binding table has no GET, so it is read through its guard: a unit carrying
    `github.pr.create` cannot leave EXECUTING without a binding for the current attempt, so a
    completed PR-capable unit is one the guard admitted.
    """
    served = set(_openapi_paths())
    route = "/api/v1/work-units/{unit_id}/pr-binding"
    if route not in served:
        return report(False, f"FAIL production does not serve {route}")
    capable = []
    for row in ledger():
        if row["unit_state"] != "completed":
            continue
        pack = api_get(f"/api/v1/work-units/{row['unit_id']}/evidence-pack")
        capabilities = pack["authority"]["envelope"].get("capabilities") or {}
        if capabilities.get("github.pr.create") == "allowed":
            capable.append(row["unit_key"])
    if not capable:
        # Nothing has passed the guard this check reads THROUGH, so there is nothing to observe
        # -- which is not the same as a guard that did not hold, and must not be reported as one.
        raise Unavailable(
            "no completed unit carries `github.pr.create`, so no unit has passed the submit "
            "guard this check reads through and the binding rule is unexercised"
        )
    return report(
        True,
        f"PASS {route} is served and {len(capable)} completed PR-capable unit(s) passed the "
        "submit guard that requires a binding",
        pr_capable_completed_units=len(capable),
    )


def _openapi_paths() -> list[str]:
    try:
        request = urllib.request.Request(
            f"{API_BASE}/openapi.json", headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return sorted(json.load(response)["paths"])
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as error:
        raise Unavailable(f"could not read the production OpenAPI document: {error}") from error


# --------------------------------------------------------------------------------------------
# Wave 3
# --------------------------------------------------------------------------------------------


def repositories_onboarded(minimum: str, *repos: str) -> int:
    """Wave 3 clause 1: run the kit across the candidate repositories and count admissions."""
    portfolio = _portfolio_command()
    results: dict[str, object] = {}
    for repo in repos:
        path = Path("~/Projects").expanduser() / repo
        if not path.exists():
            results[repo] = "checkout absent"
            continue
        completed = subprocess.run(  # noqa: S603
            [*portfolio, "onboard", str(path)], capture_output=True, text=True, timeout=900
        )
        payload = _trailing_json(completed.stdout)
        if payload is None:
            results[repo] = f"unparseable output (exit {completed.returncode})"
            continue
        if "admission_passed" not in payload:
            # The kit renaming this key would read as every repository failing admission -- a
            # verdict about the repositories taken from a fact about the tool. Routed into the
            # unmeasured set so a shortfall alongside it stays `unavailable` rather than `fail`.
            results[repo] = "the kit's JSON carries no admission_passed key"
            continue
        results[repo] = {
            "admission_passed": payload["admission_passed"],
            "not_passing": sorted(
                check["id"]
                for check in payload.get("checks", [])
                if check.get("status") not in {"pass", "not-applicable"}
            ),
        }
    unmeasured = [repo for repo, value in results.items() if isinstance(value, str)]
    if len(unmeasured) == len(repos):
        raise Unavailable(f"no candidate checkout could be measured: {results}")
    passed = sorted(
        repo
        for repo, value in results.items()
        if isinstance(value, dict) and value["admission_passed"]
    )
    ok = len(passed) >= int(minimum)
    if not ok and unmeasured:
        # Short of the bar WITH repositories we could not measure is not a miss: one of them
        # might have cleared it. Reporting `fail` here would assert something unobserved.
        raise Unavailable(
            f"{len(passed)} of {len(repos)} admission-clean, short of {minimum}, but "
            f"{unmeasured} could not be measured -- the shortfall is unproven"
        )
    return report(
        ok,
        f"{'PASS' if ok else 'FAIL'} {len(passed)} of {len(repos)} repositories are "
        f"admission-clean (bar: >={minimum})",
        admission_passed=passed,
        per_repository=results,
    )


def routing_policy_is_the_only_source() -> int:
    """Wave 3 clause 3: run WS-P2.38's derivation pin rather than reading that it exists.

    The pin lives in `intent-packages` because that repository owns the policy file. The literal
    `model:` still physically exists in factory-runner's workflow; what the pin establishes is
    that an edit diverging from the policy is refused at a gate, so the policy is the only place
    the decision is *made*. That is the standard WS-P2.23's brief guard was held to.
    """
    root = Path("~/Projects/intent-packages").expanduser()
    script = root / "scripts/check_routing_policy_compatibility.py"
    if not script.exists():
        raise Unavailable(f"{script} is absent; the routing pin cannot be executed here")
    interpreter = root / ".venv/bin/python"
    argv = [str(interpreter if interpreter.exists() else sys.executable), str(script)]
    try:
        completed = subprocess.run(  # noqa: S603
            argv, cwd=root, capture_output=True, text=True, timeout=300
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"could not run the routing pin: {error}") from error
    if "Unresolvable" in completed.stderr or "HTTP" in completed.stderr:
        raise Unavailable(f"the routing pin could not resolve both sides: {completed.stderr[:200]}")
    return report(
        completed.returncode == 0,
        f"{'PASS' if completed.returncode == 0 else 'FAIL'} routing pin exit "
        f"{completed.returncode}",
        exit_code=completed.returncode,
        stdout=completed.stdout.strip(),
    )


def unmeasured(*reason: str) -> int:
    """Declare, in the manifest, a named part of a clause that nothing here measures.

    The manifest's rule is that a check which cannot demonstrate a clause must say `unavailable`
    rather than pass on a narrower property. Where a clause has a conjunct no probe reaches, this
    states it outright and makes the clause `unavailable`. It exists so the gap is visible in the
    manifest and in the retained record instead of living in a docstring nobody reads.
    """
    raise Unavailable(" ".join(reason) or "no reason declared")


def _trailing_json(output: str) -> dict | None:
    """`portfolio onboard` prints a human summary and then its JSON document."""
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.rstrip() == "{":
            try:
                return json.loads("\n".join(lines[index:]))
            except json.JSONDecodeError:
                return None
    return None


def _portfolio_command() -> list[str]:
    for candidate in (
        [str(Path("~/Projects/project-standards/.venv/bin/portfolio").expanduser())],
        ["portfolio"],
    ):
        try:
            probe = subprocess.run(  # noqa: S603
                [*candidate, "--help"], capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    raise Unavailable("the `portfolio` conformance kit is not installed on this machine")


def profiles_are_proven(*pairs: str) -> int:
    """Wave 3 clause 2: each named unit completed under the profile it is cited for.

    Arguments are `<unit_id_prefix>:<change_class>`. Keyed on unit id, never on `unit_key`,
    which is not unique -- `change-manager-ac-001` names two different units.
    """
    if not pairs:
        raise Unavailable("no profiles were cited; the manifest must name them")
    rows = ledger()
    outcome: dict[str, dict] = {}
    for pair in pairs:
        prefix, _, expected = pair.partition(":")
        matches = [row for row in rows if row["unit_id"].startswith(prefix)]
        if len(matches) != 1:
            outcome[pair] = {"proven": False, "why": f"{len(matches)} units match {prefix}"}
            continue
        row = matches[0]
        pack = api_get(f"/api/v1/work-units/{row['unit_id']}/evidence-pack")
        actual = pack["authority"]["envelope"].get("change_class")
        outcome[pair] = {
            "proven": row["unit_state"] == "completed" and actual == expected,
            "unit_key": row["unit_key"],
            "state": row["unit_state"],
            "change_class": actual,
        }
    proven = sorted(k for k, v in outcome.items() if v["proven"])
    return report(
        len(proven) == len(pairs),
        f"{'PASS' if len(proven) == len(pairs) else 'FAIL'} {len(proven)}/{len(pairs)} cited "
        "profiles carried a real unit through to completion",
        per_profile=outcome,
    )


def workflows_were_consecutive(first: str, second: str) -> int:
    """Wave 3 clause 4, live half: both units completed and nothing ran between them.

    'Without improvisation' is measured by the SLO report's improvisation counter over a window
    and is not readable per unit, so that half is carried by the retained reconciliation record.
    What this establishes is the part that IS readable: the two runs completed and were
    consecutive among dispatched work.
    """
    rows = ledger()
    runs = sorted(
        (
            row
            for row in rows
            if row.get("actor_id") == "factory-runner" and row.get("last_event_at")
        ),
        key=lambda row: row["last_event_at"],
    )
    order = {row["unit_id"]: index for index, row in enumerate(runs)}

    picked = []
    for prefix in (first, second):
        matches = [row for row in rows if row["unit_id"].startswith(prefix)]
        if len(matches) != 1:
            return report(
                False, f"FAIL {len(matches)} units match the prefix {prefix!r}; expected exactly 1"
            )
        picked.append(matches[0])

    missing = [row["unit_key"] for row in picked if row["unit_id"] not in order]
    if missing:
        raise Unavailable(f"not a runner-claimed unit, so its ordering is unknown: {missing}")

    left, right = (order[row["unit_id"]] for row in picked)
    between = [
        {"unit_key": row["unit_key"], "state": row["unit_state"], "at": row["last_event_at"]}
        for row in runs[min(left, right) + 1 : max(left, right)]
    ]
    states = [row["unit_state"] for row in picked]
    ok = left < right and not between and states == ["completed", "completed"]
    return report(
        ok,
        f"{'PASS' if ok else 'FAIL'} {picked[0]['unit_key']} then {picked[1]['unit_key']}: "
        f"states {states}, {len(between)} runner-claimed unit(s) between them",
        units=[
            {
                "unit_id": row["unit_id"],
                "unit_key": row["unit_key"],
                "state": row["unit_state"],
                "last_event_at": row["last_event_at"],
            }
            for row in picked
        ],
        between=between,
        note=(
            "'without improvisation' is a windowed SLO counter and is not readable per unit; "
            "that half is carried by the retained reconciliation record"
        ),
    )


#: Every probe the manifest may name, by the name it names. Module-level rather than built inside
#: `main` so a test can assert the manifest invokes nothing that is not here: a manifest naming a
#: probe that does not exist reports `unavailable` for ever, and silently.
PROBES: dict[str, Callable[..., int]] = {
    "drills-are-scripted": drills_are_scripted,
    "slo-report-runs": slo_report_runs,
    "completed-units-carry-adjudications": completed_units_carry_adjudications,
    "required-criteria-are-readable": required_criteria_are_readable,
    "budget-breach-is-instrumented": budget_breach_is_instrumented,
    "lifecycle-guards-are-wired": lifecycle_guards_are_wired,
    "evidence-pack-reached-a-merged-pr": evidence_pack_reached_a_merged_pr,
    "traceability-answers-for-a-real-release": traceability_answers_for_a_real_release,
    "release-chain-answers-every-hop": release_chain_answers_every_hop,
    "tracker-is-a-projection": tracker_is_a_projection,
    "unmeasured": unmeasured,
    "every-pr-producing-unit-is-bound": every_pr_producing_unit_is_bound,
    "repositories-onboarded": repositories_onboarded,
    "routing-policy-is-the-only-source": routing_policy_is_the_only_source,
    "profiles-are-proven": profiles_are_proven,
    "workflows-were-consecutive": workflows_were_consecutive,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"--list", "-l"}:
        for name in sorted(PROBES):
            print(name)
        return PASS
    name, *arguments = argv
    if name not in PROBES:
        print(f"unknown probe {name!r}; --list to see them all", file=sys.stderr)
        return UNAVAILABLE
    try:
        return PROBES[name](*arguments)
    except Unavailable as reason:
        print(f"UNAVAILABLE {reason}", file=sys.stderr)
        return UNAVAILABLE
    except TypeError as error:
        print(f"UNAVAILABLE probe {name} called wrongly: {error}", file=sys.stderr)
        return UNAVAILABLE
    except Exception as error:  # noqa: BLE001 - a crash is not a verdict about the subject
        # Without this, a ledger or evidence-pack schema change reports "the clause was measured
        # and is not met", which is the tool's central distinction inverted.
        print(f"UNAVAILABLE probe {name} raised {type(error).__name__}: {error}", file=sys.stderr)
        return UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
