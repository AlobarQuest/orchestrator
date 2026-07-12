# Factory Improvisation Diagnostic — one real feature, through the declared contract

**Date:** 2026-07-12
**Status:** PLANNED — not yet run
**Decided by:** Devon, 2026-07-12
**Subject repo:** `AlobarQuest/orchestrator`
**Program hook:** this is **exit criterion #10** — *"two consecutive real workflows complete without
improvisation"* — pulled forward from Wave 3 and run **once, as a measurement instead of a gate.**

---

## 1. The question

> **Can this factory build an ordinary feature through its own declared contract — and if not, exactly
> where does it break?**

Not "is the factory broken." That framing is wrong: the narrow dispatch posture is **deliberate**
(master plan constraint #2 — change-classes graduate one rung at a time; `dependency-update` is rung
one, docs-only is the declared next). The real question is whether the **next rung exists at all**,
and Wave 3's exit criteria (three profiles proven, five repos onboarded, two clean workflows) are
unreachable if it doesn't.

**Why measure instead of scope a fix:** every artifact this program has killed died of *authored
intent never validated against executable reality*. WS-P2.16's plan was killed **six times** in one
day, each time by a defect that had passed a careful read. Scoping a "fix the factory" workstream from
my speculation would repeat exactly that. **Run it. Count what breaks. Let the worklist come from
evidence.**

---

## 2. THE RULE — follow the contract, do not fix it

> **When the run hits a wall: RECORD it, route around it, and keep going. Do NOT stop and patch the
> runner, the envelope, the CLI, or the orchestrator.**

The moment we start fixing, we stop measuring, and the ledger becomes a record of what we *chose* to
notice. Fixes are the *output* of this run, not part of it.

Corollary: **no pre-emptive fixes.** Do not "just quickly" add the `Write` tool or extend
`allowed_commands` before starting. The gaps are the data.

---

## 3. The subject — a real feature, chosen to be representative

**`PROJECT.md` P2:** *"Provide a helper that computes `authority.conformance` from real repo state
(`security_scan.cli.scan` + `portfolio.compliance.build_rows`, both importable and local-only) so
decomposition authors do not hand-type the claim."*

Chosen because it is the **shape of work the factory claims it will do**, and it stresses three known
suspects at once:

- It needs a **new module** → exercises the missing `Write` tool.
- It needs **real tests** → exercises "the unit cannot run this repo's suite."
- It maps naturally to **several ACs** → exercises "the runner writes one evidence row per unit."

It is also genuinely useful either way: it removes a hand-typed conformance claim that the dispatch
gate already trusts.

**Not** WS-P2.16's own units: entangling the diagnostic with the subsystem under test is how you get a
measurement you can't trust.

---

## 4. The lane — local-heavy

**Local-heavy is the lane that does all the real work, and nobody is measuring it.** WS-P2.1,
WS-P2.15 and WS-P2.16 were all session-built. The GitHub-hosted runner has produced exactly one thing
in its life — a `ruff 0.15.20 → 0.15.21` bump, which the master plan itself calls *"a machinery
proof"* with honesty caveats.

Hosted dispatch is **out of scope for this session**: it needs a production env write
(`ALLOWED_CHANGE_CLASSES`), and mixing diagnosis with infra mutation in one session violates the
standing session-discipline rule. Do it deliberately, afterwards, informed by this ledger.

---

## 5. The yardstick — the contract as DECLARED

From `factory-runner/docs/local-heavy-runtime.md` (the contract we are measuring against):

```
local-heavy-prepare   # fetch brief, validate envelope, CLAIM, START, write .sds-local-heavy/
local-heavy-renew     # renew the lease
local-heavy-reclaim   # recover an expired lease through the API
local-heavy-finalize  # run allowed verification commands, create a draft evidence-bearing PR,
                      # submit runner.pr.opened AND runner.verification evidence, transition to submitted
```

Plus the governed lifecycle: intent package → approval → intake → decomposition proposal → **Devon
approves (irreversible)** → SYSTEM `ready` → per-unit authority approval → claim → build → evidence →
adjudication → merge.

**Anything we do that is not on this list is an improvisation.**

---

## 6. The improvisation ledger

One row per deviation. This is the deliverable.

| field | meaning |
|---|---|
| `#` | sequence |
| `phase` | authoring / intake / decomposition / authority / claim / build / verify / evidence / adjudicate / merge |
| `wanted` | what the contract should have let us do |
| `contract offered` | what it actually offered (or nothing) |
| `what we did instead` | the improvisation |
| `root cause` | the defect, at file:line where possible |
| `class` | `missing-tool` / `missing-command` / `vocabulary` / `unexpressible-in-envelope` / `undischargeable-AC` / `doc-vs-reality` / `forced-by-design` |
| `blocking?` | did it stop the run, or just cost time |

**What counts as an improvisation** (be strict — the metric is only worth having if it is honest):

- Any orchestrator API call made **by hand** instead of through the declared CLI/runner command.
- Any tool the worker needed and did not have.
- Any command the envelope **cannot express**.
- Any AC that **no evidence the contract produces** can discharge.
- Any recovery action (`recover-evidence`, `requeue`, `reclaim`) used to get unstuck rather than to
  exercise a designed recovery path.
- Any step where the declared docs and the code **disagree**.
- Any moment we had to read source to know what the contract meant.

---

## 7. Pre-registered predictions

**Recorded BEFORE the run so they cannot be rationalised afterwards.** If these are wrong, that is the
best possible outcome and WS-P2.16 proceeds as planned.

| # | Prediction | Basis |
|---|---|---|
| P1 | **`ac_id` means two things.** `ac_mappings[].ac_id` wants the criterion's DB **UUID**; evidence and adjudication want `"AC-001"`. Failure is a bare `package_acceptance_criterion_not_found`. | `services/decomposition.py` keys on `str(criterion.id)` |
| P2 | **Every automated AC evaluates to `judgment_required`** regardless of evidence, forcing out-of-band adjudication via the verifier M2M credential. | `automated_test` ∈ neither `DETERMINISTIC_TYPES` nor `JUDGMENT_TYPES` (`verifier_evaluators.py:51`) |
| P3 | **`allowed_commands` cannot express "run the tests."** No Postgres, no `SECURITY_STANDARDS_DIR`; `make check` hard-fails at finalize, and exits 0 having verified nothing without a `.venv`. Test evidence must come from the `Quality` check on the PR head instead. | `CLAUDE.md` invariant; `factory-runner-pilot.yml` has no services |
| P4 | **A multi-AC unit cannot discharge ACs #2..N.** The runner writes **one** evidence row per unit (`_first_ac_id`). Forces either one-AC-per-unit decomposition (unnatural) or out-of-band evidence. | `factory-runner/cli.py:219,549,569` |
| P5 | **The 15-minute lease cannot survive a real build.** `local-heavy-prepare` claims *and starts* immediately; the established practice of "claim at the evidence push" is **already a deviation forced by the contract's own design.** | `local-heavy-runtime.md`; WS-P2.15 needed `recover-evidence` |
| P6 | **No `unit_pr_binding` row is written** — the chain criterion #6 depends on stays silently broken, in the session lane too. | `factory-runner/client.py` has no `pr_binding` call |
| P7 | **doc-vs-reality:** `finalize` is documented to submit `runner.verification` evidence; `cli.py` never calls `build_verification_evidence`. | `local-heavy-runtime.md:93-95` vs `cli.py` |

**Falsification matters as much as confirmation.** A prediction that does not fire is a fact about the
system we did not have.

---

## 8. The run

1. **Author** the intent package (`intent-packages`, profile `software-delivery`). Count friction.
2. **Devon approves.** Annotated tag to rescue `approval_ledger_commit` (intent-packages PRs are
   squash-only).
3. **Intake** — `emit-intake-payload` offline, then POST from a browser (`/api` is M2M-only at the
   proxy). Log the browser step as declared friction, not improvisation.
4. **Decomposition proposal → Devon approves** (*irreversible* — get it right).
5. **SYSTEM `ready`** command.
6. **Per-unit authority approval** (human, `/review`).
7. **`local-heavy-prepare`** — the contract's claim+start. **Use the real command.** If we cannot, that
   is improvisation #1 and it tells us everything.
8. **Build the feature.** Note every tool we reach for that a dispatched worker would not have.
9. **`local-heavy-finalize`** — the real command. Record precisely what it does and does not submit.
10. **Verify / adjudicate** — record every AC that cannot be discharged by contract-produced evidence.
11. **Devon merges.**

**Bounds.** If a step is genuinely impossible, record it and route around it — do not fight it, and do
not fix it. If the run cannot reach `submitted` at all, that is a *complete and valid result*: it means
the contract cannot execute an ordinary feature, which is the finding.

---

## 9. Deliverables

1. **The improvisation ledger** — the count, and every row.
2. **A verdict on exit criterion #10**: could one real workflow complete without improvisation? (If the
   count is > 0, the answer is no, and we now know the exact price of "yes".)
3. **The factory-capability worklist, derived from evidence** — the scope of the workstream Devon asked
   for, built from what actually broke rather than what I guessed would break.
4. **A decision on WS-P2.16's shape.** Specifically: does the submit guard need to bind the **session**
   lane too? (Predicted: yes — WS-P2.16's own units are session-built and PR-capable, so the guard will
   bite them. That is not a bug; it is the forcing function that makes criterion #6 real. But it must be
   *designed*, not discovered at deploy.)
5. **The feature itself**, merged — the diagnostic pays for itself.

---

## 10. What this is not

- Not a fix. Not a refactor of the runner. Not WS-P2.16.
- Not a judgment of the hosted runner's dispatch posture, which is deliberately narrow.
- Not a pass/fail of the program. A high improvisation count is **information**, and it is the exact
  information Wave 3 needs before anyone onboards a fifth repo onto machinery that cannot build.
