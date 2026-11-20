Outcome: done

# Report: name the lane secrets broker as the holder of the bump driver's chain read grant

Worker W-249. Branch `hq/bump-driver-broker-holder-text`, base `origin/main` at `262cc34c`
(confirmed at dispatch). PR #764, ready for review (not a draft), not merged. The ADR head is
`353210e8`, and this report's commit follows it.

## What I built

- **ADR-0059 Amendment 17**, appended at the end of
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (lines 4813-4905
  at `353210e8`). It's the next number after Amendment 16 and dated 2026-11-20. The amendment says
  the following:
  - Under ADR-0055 amendment 30, the broker's account `lane-secrets-broker` holds the read grant
    that Amendment 13 decision item 7 gives "the bump-driver lane's BWS machine account". It's a
    single-secret read grant on `2f708d08-…`, Devon's act at the bump driver's cut-over gate.
  - The same holds for `750f737f-…` (`CODE_BRAIN_CONTRIBUTOR_KEY`) and `da8134b0-…`
    (`INFRA_BRAIN_CONTRIBUTOR_KEY`), both in `Brains - Contributor`.
  - Reach: the broker's account reads the three secrets, so amendment 30 item 8 applies. Only
    `[lanes.bump-driver]` lists any of the three, so the chain's read password still has the two
    lane readers that Amendment 13 names.
  - No other authority changes: no new account, mint, project grant, or network path.
- **Item 2 (passages).** I listed each passage in the amendment's "Passages this amendment changes"
  table and didn't edit earlier text. The file's latest amendments do the same: 13 says "ADR-0055
  isn't edited", and 16 says "None is rewritten". Later amendments also cite this file by line, so
  an inserted pointer would shift every later citation. Amendment 8 did insert pointers into
  Amendment 7 (line 2931), so the file's style isn't uniform; I followed the latest one. The table
  lists these passages:
  - Amendment 13 item 7 (3898-3901).
  - Amendment 13's passage rows at 3998, 4001, 4011, 4012, and 4013.
  - Decision 2's identity bullet (486-492).
  - The broker-lane paragraph in "Cut-over for one lane" (1132-1133).
- **Passages I checked and left out:**
  - Line 3360 is Amendment 11 decision item 5. It names the work-carrier lane account, not the
    bump driver.
  - ADR-0055 names no bump-driver account anywhere. A grep for `bump`, the three UUIDs, and
    "second reader" turns up none. Amendment 13 governs ADR-0055's chain-reader passages from
    ADR-0059.
  - Neither ADR names a holder for the two Brain keys beyond decision 2's per-lane rule and the
    broker-lane paragraph.
  The amendment's closing paragraph says all three.
- **Item 3.** Neither ADR-0055 amendment 30 nor `secrets-broker.md` states a single-secret rule for
  later lanes. Both state the project form, and so does ADR-0059's broker-lane paragraph. The
  amendment cites each and says the three grants are narrower in form. For `Factory Events` it
  gives the reason shown at base: a project grant would also reach the append password, whose
  holders ADR-0055 amendment 25 item 3 names. For `Brains - Contributor` it calls the
  single-secret form HQ's proposal.

## Sources for each fact (at `262cc34c` unless noted)

- Amendment 13 item 7: ADR-0059 lines 3892-3920; the grant is at 3898-3901.
- Amendment 13's passage table: ADR-0059 lines 3998, 4001, and 4007-4013.
- Decision 2's identity bullet: ADR-0059 lines 486-492. The broker-lane paragraph: lines 1127-1138.
- The three secrets' projects: `.bws-secrets.toml` lines 260-263 and 266-269 (`Brains -
  Contributor`) and 569-572 (`Factory Events`).
- The append password in `Factory Events`: `.bws-secrets.toml` line 556.
- The bump-driver allowlist: `lanes/secrets-broker/allowlist.toml` lines 37-46. The three UUIDs
  appear only at lines 42-44. The allowlist has no `[lanes.work-carrier]`; its lanes are
  `inert-landing`, `estate-landing`, and `bump-driver`.
- The broker's reach: `.cred-consumers.toml` lines 1418-1419 (two secrets, two projects).
- The project form of the broker's grants:
  - ADR-0055 amendment 30, Context and "What it grants, and nothing more" (from line 4292).
  - `docs/operations/secrets-broker.md` lines 20-21 and 69.
- ADR-0055 amendment 25 item 3 (the account table): ADR-0055 lines 3628-3642.
- The cut-over gate: `docs/operations/deploy.md` lines 1157-1170.
- Devon's recorded broker grants: sds-hq `state/goal.md` at `1cce7e3`, line 60.
- HQ's plan for the three grants: the same file, line 39.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_249` passed: **14,839 passed, 1 skipped**, and all
  lint checks passed. Exit code 0.
- Security scan from the repo root (`--category security`): **0 BLOCK, 2 WARN, 1 INFO**. Both
  WARNs are in files this diff doesn't touch: an uncovered `*-migration/` glob, and an undeclared
  UUID `aaff45ca-…` in code. The INFO is the standing judgment item.
- `code-standards risk --repo .`: `normal`, so no vendor review was needed.
- `/code-review` (medium) on `origin/main...HEAD` found three nits:
  - Allowlist range 37-45: fixed to 37-46.
  - The Brain keys' grant form read as settled: item 2 now calls it HQ's proposal.
  - The Context cites 3898-3899 and the table cites 3898-3901: left as is. Both are correct,
    because the Context quote ends on line 3899 and the table quotes the longer passage.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (+93 lines,
  appended only).
- `docs/superpowers/reports/bump-driver-broker-holder-text-report.md` (this report). It falls
  under the `docs/superpowers/**` pattern in `layers.toml` (line 758).

## Self-review

- The amendment states only what the sources show. It says nothing of `sds-operator`'s or other
  holders' status for `2f708d08-…`, and it doesn't repeat Amendment 13's "seventh grant" count.
- The "Decided by" line separates Devon's recorded grants (`Rotation / Keepers`) from HQ's plan
  (`Factory Events`, `Brains - Contributor`).
- There's no secret value, token, or ping URL in the diff, only shortened UUIDs.

## Concerns

- **Devon's record covers only part of the single-secret form.** sds-hq `goal.md` line 60 records
  single-secret grants for `Rotation / Keepers` only. Using the same form for `Factory Events` and
  `Brains - Contributor` is HQ's plan, and the amendment says so. It doesn't block anything:
  every grant is still Devon's act at the gate.
- **Stale text the handoff kept out of scope.** I didn't edit either of the following:
  - `.bws-secrets.toml` lines 564-567 still say the bump driver reads `2f708d08-…` "with the
    lane's own token" and that "the bump-driver lane account's grant on it is Devon's act".
  - After Devon's grants, the `secrets-broker-bws-token` row's `reach_secrets` in
    `.cred-consumers.toml` (line 1418) needs the three UUIDs.
  Both belong in the cut-over's code step.
- **The handoff's `partial` outcome isn't a worker outcome.** The worker set is `done`, `blocked`,
  or `failed`. This report uses `done`.

## Standards followed

- **Code Brain rules 3 and 4 (structured logs; never swallow an exception):** don't apply. The
  diff is docs only.
- **Code Brain rule 5 (never log secrets):** the amendment contains no secret value, token, or ping
  URL. It names secrets by registry name and shortened UUID (`2f708d08-…`), as the ADR does.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN` value):** the diff names the variable only in prose and holds no token. The
  security scan found 0 BLOCK findings.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value in any file or output during
  the work.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. Nothing is served.
- **Infra Brain 2890 (never commit a ping URL):** the diff contains no ping URL.
