# Remediating a stuck Dependabot pull request

The half of ADR-0016 that gives the factory its job. Native auto-merge lands routine dependency
updates and can only *decline* the ones that fail; this is what happens next.

## When this applies

A Dependabot pull request whose required checks fail and stay failed. Not one that is merely
waiting, and not one blocked by an infrastructure incident — re-run first and be sure the failure
is real.

## The two things that make this simpler than it looks

**1. The fix is almost never to the pull request. It is to `main`.** A dependency bump fails
because the new version changed something the codebase relies on — new lint rules, a removed API, a
stricter default. Dependabot's diff is usually correct; the repository is what needs work.

**So the factory targets `main` with its own pull request, and never touches Dependabot's branch.**
Pushing to Dependabot's branch would be overwritten by its next rebase, and would leave the factory
authoring commits inside a producer's workspace.

**2. Once the blocker lands, the original PR completes itself.** Comment `@dependabot rebase`; the
PR picks up the fixed `main`, its checks pass, and **if auto-merge is already armed it merges with
no further action.** The two mechanisms compose — the factory removes the obstacle and the native
lane finishes the job. Nobody merges the dependency update by hand.

## Prerequisite, and it is narrow today

The target repository must have **both**:

- a caller workflow at `.github/workflows/factory-runner-pilot.yml`, pinned to factory-runner's
  `RECOMMENDED_CALLER_PIN`; and
- membership in `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES`.

Measured 2026-08-07: six repositories have a caller (orchestrator, intent-packages, change-manager,
brain, security-standards, infraops-mcp-server) and **the allowlist contains one**
(`AlobarQuest/intent-packages`). Widening it is a standing authority change, decided per repository.

**`factory-runner` and `project-standards` have no caller by decision (ADR-0015) and are therefore
outside this recipe permanently** — remediate those by hand. Note the shape of that: `factory-runner`
PR #33 is the case that motivated this document and is the one case the factory cannot take.

## Procedure

1. **Confirm the failure is real.** Re-run the failed check. Read the log for the actual breakage,
   not the exit code.
2. **Author a `maintenance-remediation` package** — `factory create --profile maintenance-remediation
   --reach source_repository --name <slug> --title <title>`. Its outcome is *the repository passes
   its own checks against the new version*, not *the Dependabot PR merges*. Name the blocking PR in
   `sources` so provenance survives.
3. **Approve it through the audited CLI** (`intent_packages`: `transition` → `approve` →
   `verify-approval`). Never hand-edit `lineage.yaml`; a hand-written approval can never verify.
   Update the repository's package-hash snapshot in the same commit if it keeps one.
4. **Intake** — `orchestrator emit-intake-payload …`, then Devon pastes it at
   `/review/intakes/new`.
5. **Hand-author the decomposition proposal** against
   `POST /api/v1/package-intakes/{revision_id}/decomposition-proposals` — `factory decompose`
   speaks dependency-update only. Two traps: `ac_mappings[].ac_id` wants the criterion's **database
   UUID** while evidence and adjudication want the human string; `expected_version` must be **0**.
6. **Devon approves twice** — the decomposition proposal, then the unit's authority envelope via
   the dedicated form, not the generic approve button.
7. **SYSTEM `commands/ready`**, then dispatch at the next unused ordinal. Verify by a **new
   dispatch-record id and a new Actions run** — a reused ordinal returns `status: "dispatched"`
   and does nothing.
8. **Verify and complete** — named-check evidence via the verifier credential, then `/verify`.
9. **Return to the Dependabot PR**: `@dependabot rebase`. It should now pass and, if armed, merge
   itself.

## The envelope

Settled; do not re-derive it. Edit-shaped, because the coding agent produces the diff:

- **no `mutation_commands` key at all** — there is no honest value, and a fig leaf makes the
  envelope lie about what mutates
- `constraints.allowed_commands`: `["uv sync", "make check"]` in that order — `finalize-run`
  re-executes the list, so every entry must be idempotent, and the reusable workflow syncs the
  *runner*, never the target repository
- `budgets.max_llm_calls`: **`max_attempts × ~20`**, not a multiple of an observed run. Measured
  burn has been 8–29 per attempt, and the ceiling is write-once inside the fingerprinted envelope —
  an over-budget unit is permanently dead, with no cure
- `change_class: maintenance-remediation`; `reach: [source_repository]` confirmed against App Brain,
  never inferred

**Dry-run before spending the approval.** Prove the verifier executes in a bare tree
(`git archive HEAD | tar -x`, no `.venv`, `env -i PATH=/usr/bin:/bin`) reaching a real
`collected N items`, and run the ordered list twice in one checkout.

## Worked example, live at time of writing

**`intent-packages` #50** — ruff 0.15.22 → 0.16.1, `Lint, type-check, and test=FAILURE` while
`validate` and `Routing policy compatibility` pass. The new ruff version flags code the repository
has not yet updated. It sits in the one repository currently in the dispatch allowlist, which makes
it the natural first execution of this recipe — and the first proof that the factory's remediation
half of ADR-0016 works end to end.

**`factory-runner` #33** — ruff 0.15.20 → 0.16.0, same shape, auto-merge already armed and holding
correctly. Outside this recipe (no caller, by ADR-0015). When someone fixes those lint failures by
hand, the armed auto-merge completes the update unattended, which is the composition above running
without the factory's half.
