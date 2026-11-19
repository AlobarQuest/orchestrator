Outcome: done

# ADR-0059 Amendment 14, fix round 1 (PR #750): build report

Worker W-233. Branch `hq/adr-0059-a14-change-window-fix-1`, pushed fast-forward to PR #750's branch
`hq/adr-0059-a14-change-window` from its head `0170dd2a`. The amendment's fix is commit `3de31dd5`;
this report is the commit after it, and that commit is the new head of #750.

## Sync

`git log 0170dd2a..origin/hq/adr-0059-a14-change-window` after `git fetch origin` printed nothing,
so no later commit existed. The handoff's `git reset --hard origin/hq/adr-0059-a14-change-window`
was denied by the permission layer. I used `git merge --ff-only origin/hq/adr-0059-a14-change-window`
on the clean tree instead (it was at `3585b4f9`, an ancestor), which leaves the same result: HEAD at
`0170dd2a` before any change.

## What I changed

All edits are in Amendment 14 of
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`. Line ranges are the
file's at `3de31dd5`.

1. **The Coolify window token's authority is removed.**
   - Decision item 3's token bullet (lines 4166-4183): D-68's quoted words stay. It then states what
     the sources show: ADR-0055 at `3585b4f9` line 341 (team-wide `read`, `write`, `deploy`, no
     `read:sensitive`, its reach, and its "probably"), line 525 (on dev beta.470, a token without
     `write` gets 403 on `POST`/`PATCH /applications/{uuid}/envs` and restart), and infraops
     `src/change-manager/tools.ts` at `fdf3e91b` lines 189, 210, 248 and 255 (PATCH
     `/applications/{uuid}`) and 222 (deploy). It says an application PATCH without `write` wasn't
     measured and that the sources don't show whether production beta.473 offers an
     application-scoped permission. D-70 (sds-hq `state/decisions.md` at `9fd32943`, line 5) decides
     the token, and it isn't answered.
   - Decision item 7's lead-in and token bullet (lines 4264-4272): the merge authorizes everything
     listed except the Coolify window token; the token is authorized only once D-70's answer is
     recorded in a later amendment or row, and the mint gate stops before creating it.
   - Decision item 7's read-grants bullet (lines 4305-4308): the grant on the Coolify window token
     waits for D-70's recorded answer.
   - Verify items (lines 4336-4342): a new item names D-70 as open, landing at increment 8's mint
     gate. The mint-gate item drops the "narrowest set" fallback, the "records what that set reaches"
     clause, the `.cred-consumers.toml` line 665 sentence, W-223's "option b" sentence and the
     closing "Devon's decision under the standing answer" sentence. It now reads: the mint gate reads
     the production instance's token screen and creates only the token D-70's recorded answer names.
   - Table rows (lines 4372, 4374, 4383): the "Agents outside" row and the two rows that called the
     identity question answered now say the Coolify window token is open under D-70, and no longer
     call the four secrets "narrow".
2. **Two false statements about current state.**
   - Decision item 2 (lines 4150-4152): the launcher unsets the four variables
     `scripts/run-change-window.sh` line 153 names: `COOLIFY_DEV_BASE_URL`, `COOLIFY_DEV_API_TOKEN`,
     `COOLIFY_PROD_BASE_URL` and `COOLIFY_PROD_API_TOKEN`.
   - The alert-hub sender bullet (lines 4193-4197): the `report-*` glob sentence is gone. It says only
     that the notice needs no rule change, as today's `report-infraops` sender's doesn't.
3. **The credential rule is cited** (lines 4285-4296): item 7's `execute` bullet quotes sds-hq
   `prompts/cycle.md` line 41 at `68466c01` verbatim, from its first sentence to its attribution
   (Devon, 2026-11-14, interactive session, after G-57: "Draft approved"), beside the standing
   answer it already quoted.
4. **#750's body.** I read the existing body and edited it in place with `gh pr edit 750
   --body-file`: the credentials bullet and the verify-items bullet now say the token waits for
   D-70, the "deploy.md agrees" line is corrected, existing follow-ups 1 and 3 absorb (b) and (d),
   and (a), (c) and (e) are new follow-ups.

## Tests and checks run

- `git diff 0170dd2a..3de31dd5 -U0`: every hunk header is at or after line 4150, inside Amendment 14
  (which starts at line 4003). `git diff --numstat 3585b4f9..3de31dd5` on the ADR: 384 added, 0
  removed, so the amendment still removes no line above 4003.
- Source re-reads at the cited commits: ADR-0055 `3585b4f9` lines 341 and 525; infraops `fdf3e91b`
  `tools.ts` 189, 210, 222, 248, 255; `scripts/run-change-window.sh` line 153 (the `unset` line);
  sds-hq `68466c01` `prompts/cycle.md` line 41; sds-hq `9fd32943` `state/decisions.md` line 5 (the
  `### D-70` heading). Each line holds the text cited.
- A grep of the amendment for "narrowest set", "line 665", "option b", `report-*`, "every
  `COOLIFY" and "with the permissions in decision" found nothing.
- `make check` on the clean committed tree at `3de31dd5`, against `orchestrator_test_hq_w_233`, with
  `TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL`, `SECURITY_STANDARDS_DIR` (the fixture) and
  `PGTZ=UTC`, after `alembic upgrade head`: exit 0. Ruff "All checks passed!", pyright 0 errors,
  **15,832 passed, 1 skipped (15,833 collected)**, 11 warnings, in 331 seconds.
- Security scan from the repo root (`security_scan.cli . --category security`): **0 BLOCK**, 2 WARN
  (the gitignore-coverage pattern check; this diff touches no secret file).

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (Amendment 14 only)
- `docs/superpowers/reports/adr-0059-a14-change-window-fix-1-report.md` (this report)
- PR #750's body (through `gh`)

## Self-review

- The token bullet attributes "team-wide" to line 341 and keeps its "probably", and says line 525
  measured the envs PATCH and restart, not the application PATCH the window makes.
- "Decided by" doesn't list D-70: it isn't decided.
- The amendment's title still says "on its own narrow credentials". I left it: three of the four
  are narrow under either D-70 option, and renaming a heading can break references to it.
- The alert-hub `2218702` citation in the sources paragraph still supports the sender bullet's
  "the narrowest credential alert-hub offers".

## Concerns

- D-70 is unanswered, so increment 8's mint gate can't create the Coolify window token, and a
  `--submit` pass can't run, until a later amendment or row records the answer. This is the gap the
  verify item names, not a defect of this change.
- The `reset --hard` deviation (Sync) has the same result, but it differs from the handoff's
  command.
- The test database `orchestrator_test_hq_w_233` still exists for HQ's teardown.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** doesn't apply; the change is
  docs only and adds no logging.
- **Code Brain rule 4 (never silently swallow an exception):** doesn't apply; the change is docs
  only and adds no code.
- **Code Brain rule 5 (never log secrets, credentials, tokens, or full request bodies):** the diff
  names no secret value. Existing secrets appear only by the truncated UUIDs the amendment already
  used (`bbd71f41-…`, `4ed74de3-…`); the new token is named by role.
- **Infra Brain 591, 592 (no BWS token in tracked files or history) and 593 (no inline
  `BWS_ACCESS_TOKEN` value):** no token, BWS or otherwise, is in the diff; the scan reports 0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`):** not changed by this amendment. I saw no secret value
  in any source I read.
- **Infra Brain 212 (HTTPS for public FQDNs):** the lane has no FQDN. Its one plain-HTTP address,
  `http://100.92.138.19:8000`, is the tailnet Coolify address G-73 probed, not a public FQDN; the
  change keeps it as the only destination for whichever token D-70 names, and change-manager,
  alert-hub and the Anthropic API stay HTTPS.
