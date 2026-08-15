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
   itself. **Check whether it is actually armed first** — `gh pr list --json autoMergeRequest`.
   A PR opened *before* the repository's `dependabot-auto-merge.yml` landed was never seen by that
   workflow and carries no auto-merge request; `intent-packages` #50 (opened 2026-08-01) is such a
   PR. The rebase fixes this by itself, because `on: pull_request` includes `synchronize`, so the
   push arms auto-merge and the merge follows — but do not read an unarmed pre-existing PR as the
   auto-merge lane being broken.

## The envelope

Two shapes, and **which one you are in is decided by the remediation, not by the profile.** Getting
this backwards is what cost the first live execution a package revision.

- **Edit-shaped** — the coding agent writes the diff and no command mutates a tracked file.
  **No `mutation_commands` key at all**: there is no honest value, and a fig leaf makes the
  envelope lie about what mutates. `constraints.allowed_commands`: `["uv sync", "make check"]`.
- **Command-shaped** — a command produces the diff, which is the case whenever the fix is "adopt
  the behaviour of the version being proposed", because the pinned toolchain cannot produce it.
  Then `allowed_commands` is `["uv sync", "<the mutator>", "make check"]` and
  `mutation_commands` names the mutator. It must be an ordered subset of `allowed_commands`.

In both shapes: `finalize-run` re-executes the whole list before checking `git status`, so **every
entry must be idempotent and the verifier must come last** — otherwise the recorded `make check`
attests to a tree that is not the one pushed. The reusable workflow syncs the *runner*, never the
target repository, which is why `uv sync` leads.
- `budgets.max_llm_calls`: **`max_attempts × ~20`**, not a multiple of an observed run. Measured
  burn has been 8–29 per attempt, and the ceiling is write-once inside the fingerprinted envelope —
  an over-budget unit is permanently dead, with no cure
- `change_class: maintenance-remediation`; `reach: [source_repository]` confirmed against App Brain,
  never inferred

**Dry-run before spending the approval.** Prove the verifier executes in a bare tree
(`git archive HEAD | tar -x`, no `.venv`, `env -i PATH=/usr/bin:/bin`) reaching a real
`collected N items`, and run the ordered list twice in one checkout.

**And prove the MUTATOR is reachable from the runner's environment, not just from your
machine.** This is the clause the first live execution was missing, and it cost a whole package
revision. `uv sync` installs the versions the repository *pins*, so when the fix is "adopt the
behaviour of the version Dependabot proposes", the checkout contains a tool that thinks the tree
is already correct. Revision 1 of `intent-packages-ruff-format` authorised `["uv sync", "make
check"]` and asked for a ruff-0.16 reformat in a tree pinned to ruff 0.15.22: `make fix` was a
no-op, `make check` passed seven times, and the coding agent spent its whole turn budget trying to
research what 0.16 had changed. Nothing in the envelope was wrong on its face — it simply could
not produce a diff.

**The idiom is `uvx <tool>@<version>`**, which fetches the proposed version for the run without
committing the bump. That keeps ADR-0016's composition intact: the factory removes the blocker,
Dependabot's own PR still lands the version change. Declare it in `mutation_commands` too — a
command genuinely produces the diff now, and the field is an ordered subset of
`allowed_commands`.

**Name the mechanism in `outcome`, not just the goal.** `allowed_commands` reaches the coding
agent only as prompt text; the unit's `outcome` is what steers it. Revision 2 said which command
produces the change *and* that `make fix` is a no-op here, and the attempt finished in 90 seconds
on 10 LLM calls against a 60 budget. Revision 1 said what the tree should look like afterwards and
burned 40 turns and $1.39 finding out it had no way to get there.

**The turn ceiling, not the budget, is what kills a floundering attempt.** Revision 1 died on
`error_max_turns` at 40 turns with `max_llm_calls: 60` barely touched — the ceiling is a literal
in factory-runner's workflow and is not derived from the envelope. A run that ends on max_turns is
telling you the unit was under-specified, not that its budget was too small.

## Worked example, live at time of writing

**`intent-packages` #50** — ruff 0.15.22 → 0.16.1, `Lint, type-check, and test=FAILURE` while
`validate` and `Routing policy compatibility` pass. The blocker turned out to be `ruff format
--check`, not `ruff check`: ruff 0.16 widened inline-comment spacing and formats Python inside
markdown fences, so the whole diff was seven documentation files under `docs/superpowers/`.

**Executed 2026-08-07. It took two package revisions.** Revision 1 died on the missing dry-run
clause above. Revision 2 (`uvx ruff@0.16.1 format .`) opened **PR #62** — exactly those seven
files, `Lint, type-check, and test` green on its head, observed from GitHub rather than asserted —
and the unit completed on that observation. 10 LLM calls, $0.14, about 90 seconds of runner time.

That is the first end-to-end proof of the factory's half of ADR-0016, and the composition still
has one hop left when this was written: #62 merges, then `@dependabot rebase` on #50 lets the
native lane land the bump nobody merged by hand.

**`factory-runner` #33** — ruff 0.15.20 → 0.16.0, same shape, auto-merge already armed and holding
correctly. Outside this recipe (no caller, by ADR-0015). When someone fixes those lint failures by
hand, the armed auto-merge completes the update unattended, which is the composition above running
without the factory's half.
