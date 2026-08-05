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
    metrics = {k: v for k, v in payload.items() if isinstance(v, dict) and "status" in v}
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
        current = [a for a in pack.get("adjudications", []) if a.get("current")]
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
    and no criteria list, so the comparison cannot be made from outside the database. This probe
    looks for such a key and reports `unavailable` while none exists, so it starts working by
    itself if a release ever serves one.
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
    metric = payload.get("budget_breach")
    if not isinstance(metric, dict):
        return report(False, "FAIL the SLO report serves no budget_breach metric")
    ok = metric.get("status") != "not_instrumented"
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
        found[repo] = sum(int(line) for line in completed.stdout.split() if line.isdigit())
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
    payload = api_get("/api/v1/traceability?environment=production")
    chains = payload.get("chains") or []
    if not chains:
        return report(False, "FAIL no production-anchored chain exists")
    census = []
    complete = []
    for chain in chains:
        hops = {
            hop: (1 if isinstance(chain.get(hop), dict) else len(chain.get(hop) or []))
            for hop in ALL_HOPS
        }
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


def tracker_is_a_projection() -> int:
    """Wave 2 clause 3: canonical-side bindings exist and the import ban is enforced."""
    bindings = api_get("/api/v1/tracker-bindings")
    rows = bindings if isinstance(bindings, list) else bindings.get("bindings") or []
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
    return report(
        bool(capable),
        f"{'PASS' if capable else 'FAIL'} {route} is served and {len(capable)} completed "
        "PR-capable unit(s) passed the submit guard that requires a binding",
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
        results[repo] = {
            "admission_passed": payload.get("admission_passed"),
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


def main(argv: list[str]) -> int:
    probes: dict[str, Callable[..., int]] = {
        "drills-are-scripted": drills_are_scripted,
        "slo-report-runs": slo_report_runs,
        "completed-units-carry-adjudications": completed_units_carry_adjudications,
        "required-criteria-are-readable": required_criteria_are_readable,
        "budget-breach-is-instrumented": budget_breach_is_instrumented,
        "lifecycle-guards-are-wired": lifecycle_guards_are_wired,
        "evidence-pack-reached-a-merged-pr": evidence_pack_reached_a_merged_pr,
        "traceability-answers-for-a-real-release": traceability_answers_for_a_real_release,
        "tracker-is-a-projection": tracker_is_a_projection,
        "unmeasured": unmeasured,
        "every-pr-producing-unit-is-bound": every_pr_producing_unit_is_bound,
        "repositories-onboarded": repositories_onboarded,
        "routing-policy-is-the-only-source": routing_policy_is_the_only_source,
        "profiles-are-proven": profiles_are_proven,
        "workflows-were-consecutive": workflows_were_consecutive,
    }
    if not argv or argv[0] in {"--list", "-l"}:
        for name in sorted(probes):
            print(name)
        return PASS
    name, *arguments = argv
    if name not in probes:
        print(f"unknown probe {name!r}; --list to see them all", file=sys.stderr)
        return UNAVAILABLE
    try:
        return probes[name](*arguments)
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
