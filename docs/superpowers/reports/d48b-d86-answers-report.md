Outcome: done

# Report: record Devon's D-86 answers in ADR-0055 amendment 38

Pull request: AlobarQuest/orchestrator #835, branch `hq/d48b-d86-answers`, based on `d5793c2e`.
The amendment's content is in commit `cb56f09c`. The commit that adds this report becomes the
pull request's head. The pull request isn't merged.

## What I built

In `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, amendment 38 only.
Its status stays Proposed.

- **`### D-86 answers (2026-11-28)`**, after `### Open items`, includes the following:
  - HQ's record of the answer, quoted verbatim (copied from the handoff by script).
  - Items 1 to 9 as answered.
  - Item 10, deferred to Phase 2 with T2.
  - HQ's scope rule (i) to (iv), stated as HQ's reading.
- **`### The pass as answered`** includes the following:
  - T1 to T6, one line each, marked "out of this pass, Phase 2 input (D-86)".
  - What was read.
  - The in-pass table.
  - The approvals, grouped for one sitting.
  - The out list, one line per row with its reason.
  - The row count.
  - The two open items, D86-A and D86-B.
- One line under `#### 4. The proposed order` and `#### 5. Code tasks` points to the new section.
  Their text stays as written.
- The header's "Amended" line for amendment 38 mentions the D-86 answers and the two open items.

I didn't do the optional rename of `d48b-rotate-everything-plan-question.md`.

### The in-pass table: 8 rows, plus 1 waiting

The 8 in-pass rows:

- `orchestrator-operator-bearer`, next revision 2.
- `orchestrator-observer-bearer`, revision 3.
- `orchestrator-drift-reporter-bearer`, revision 3.
- `orchestrator-factory-runner-bearer`, revision 1 (it has no package).
- `change-manager-full-bearer`, revision 3.
- `change-manager-read-bearer`, revision 4.
- `change-manager-propose-bearer`, revision 2.
- `change-manager-observe-bearer`, revision 2.

`change-manager-execute-bearer` (revision 1) waits on D86-B.

I decided each row from the registries and code, not from the handoff. For every candidate, a
script ran the following in-process:

- It derived the plan with `plans.candidates` from the registry row.
- It looked for any `HumanAct`.
- It looked for any attested-kind reader with no read.
- It compared the standing package at intent-packages `cdf68a92` with `plans.select_plan`
  (criteria) and `destinations.same` (destinations), the reads `decompose.py` lines 58-78 make.

All nine have no person's act and no unread reader. All seven existing packages' criteria describe
the derived plan. Two packages list destinations that differ from the registry's: the observer
bearer's revision 2 and the read bearer's revision 3. The decomposer refuses both. The revision
that each rotation makes carries the registry's destinations (`standing.py` lines 256-291), so no
separate paired revision is needed.

**Approvals.** Every rotation is a new revision, and none is approved by policy:

- Seven rows need revision 2 or later, which needs Devon by name (D-34 A).
- The two revision-1 rows aren't lane types (`standing_approval.py` `LANE_KINDS`), so Devon
  approves those by name too.

All nine fit one sitting. Their `work` records fall under change-manager's work policy.

### Rejected candidates

- `openrouter-generic` and `alobar-id-postgres` fail (i). A UUID grep across orchestrator,
  change-manager, factory-runner, intent-packages and sds-hq finds no reader: only fixtures and
  their own packages.
- The mirror tokens fail (iii) and (i).
- The `bws-tok-*` rows fail (iii).

### The out list: 56 rows

The 56 out rows are named one per line by registry. With the 8 in and the 1 waiting, they make 65.
The 11 unregistered credentials are out too, under D-86 (3) and item 10.

## Open items for Devon

Both are in the amendment and in `docs/superpowers/reports/d48b-d86-answers-question.md`.

- **D86-A, ping URLs.** (A) Out, as amendment 38 says. (B) In, by recreating each check, which is
  a person's act and so P2. Recommendation: A.
- **D86-B, edits inside a row's own registry.** These are the execute bearer's `rotated_by_sds`
  flag and the `rotate_requested` dates. infraops reports a row due only by exposure, age or
  `rotate_requested` (`findings.py`), so a row that isn't due rotates only after a date is written.
  - (A) Both edits are allowed.
  - (B) Neither is allowed.
  - (C) Dates only.

  Recommendation: A, with the flag set only after HQ's D-84 condition 2 check.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_341`: exit 0. Lint, format and type checks passed; pytest collected 15188 items: 15187 passed, 1 skipped, 11 warnings (448 s).
- Security scanner (`security_scan.cli . --category security`): 0 BLOCK, 2 WARN, 1 INFO.
- `code-standards risk`: `normal`, so no vendor review is needed.
- The repository has no separate docs lint target (the Makefile's targets are `check`, `fix`,
  `layers` and `layer`).

I didn't run `/code-review`, because the change is prose-only and touches no logic, interface or
configuration.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/superpowers/reports/d48b-d86-answers-question.md` (the stop-point file the handoff names
  for Devon's questions)
- `docs/superpowers/reports/d48b-d86-answers-report.md` (this report)

## Self-review

- Every count in the amendment matches a script parse of the seven registries: 39, 5, 2, 9, 1, 1
  and 8 rows, 65 in total.
- The cited line ranges were checked with `sed -n` at `d5793c2e`.
- The quote was copied by regex from the handoff, not retyped.

## Concerns

- I read the other repositories at their local clones' `origin/main` without fetching, because the
  handoff forbids GitHub calls beyond the push and the pull request. The commits are change-manager
  `f7f8bc07`, sds-hq `ed06e3ab` and intent-packages `cdf68a92`.
- change-manager's `origin/main` moved from amendment 38's `ebd43ee4`. Its `.cred-consumers.toml`
  diffs empty between the two.
- I didn't read whether infraops reports any in-pass row due by age today.
- I didn't verify HQ's D-84 condition 2 check for the execute bearer's scope.
- The first `make check` run was stopped at the tool's 10-minute background limit while pytest
  was running, with no result. The rerun's result is the one this report gives.

## Standards followed

- **Code Brain rule 3 (logging) and rule 4 (error handling):** not applicable. The change is docs
  only.
- **Code Brain rule 5 (never log secrets):** each credential is named by registry id, Coolify
  variable name or a BWS UUID prefix only. No value, ping URL, token or fingerprint of a new value
  appears. The scanner found no BLOCK.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files, in history or inline):** the diff
  holds no token-shaped string. A grep of the added lines for token shapes was empty, and the
  scanner's history check found no BLOCK on the branch.
- **Infra Brain 1032 (`cred.exposure-rotate`):** the amendment says nothing about a row's removal
  or a purge closing an exposure. Amendment 38's revoke wording stands unchanged.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. No endpoint is added.
