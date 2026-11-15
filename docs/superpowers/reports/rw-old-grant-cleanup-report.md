Outcome: done

# rw-old-grant-cleanup: build report

PR: https://github.com/AlobarQuest/orchestrator/pull/716 (branch `hq/rw-old-grant-cleanup`,
commit `8d0d191f`, base `origin/main` `177448d5`). Open, not merged. HQ reviews and merges.

## What I built

The revision watcher's manifests and the test that checks them stop naming the write-capable
Coolify token's grant, which Devon removed at HQ gate G-64 on 2026-11-15.

1. `.cred-consumers.toml`, row `revision-watcher-bws-token`: removed
   `bbd71f41-b7df-4ae9-8fdb-b41501447308` from `reach_secrets`. The comment lists what the account
   sees (the five `revision-watcher` lane secrets and the ping URL's keeper), keeps "a token's
   bind requires it to see exactly this set", and says once that the grant on
   `prod-coolify-api-token` was removed at HQ gate G-64 on 2026-11-15. The "until a later gate
   removes it" clause is gone.
2. `tests/rotation_worker/test_revision_watcher_rows.py`: removed `STILL_GRANTED`, its comment,
   and `test_a_secret_still_granted_is_neither_a_lane_secret_nor_read_by_the_launcher`. `GRANTED`
   is the plain `frozenset`; its comment says six granted at G-30, the read-only token at G-62, and
   the write-capable token's grant removed at G-64. The reach assertions are
   `secrets == _lane_secrets()` and `secrets == GRANTED`. `LAUNCHER` stays: another test
   (`test_the_read_app_key_is_read_by_the_lane_s_launcher`) reads it.
   I kept no exception mechanism: a repo-wide grep finds no other reader of `STILL_GRANTED` (the
   only other hits are prose in the historical `docs/superpowers/reports/rw-ro-token-report.md`).
3. `.bws-secrets.toml`, comment above `prod-coolify-api-token`: replaced "the lane's account
   still holds a direct read grant on it … and a later gate removes that grant" with: the launcher
   no longer reads it, and the lane's account no longer sees it, because Devon removed that
   account's direct read grant at HQ gate G-64 on 2026-11-15. No `[[secret]]` entry or `lanes`
   list changed.

## Grep hits (item 4)

I grepped `docs/`, `scripts/`, `src/`, `tests/`, and `lanes/` (and the two manifests) for
`bbd71f41`, `prod-coolify-api-token`, `STILL_GRANTED`, "later gate", and "still holds". A second
pass grepped `docs/operations/`, `docs/decisions/`, `lanes/`, `scripts/`, `src/`, `tests/`, and
every `CLAUDE.md` for "write-capable", `G-62`, `G-64`, and "revision watcher" within 60 characters
of "grant". I also read the files that #712 (`7d980eaf`) and #714 (`177448d5`) changed, since
those wrote the "grant stays" text.

Fixed (the only statements that said the revision watcher still holds the grant):

- `.cred-consumers.toml:903-908`: the `reach_secrets` comment and the UUID itself.
- `.bws-secrets.toml:133-136`: the "still holds … a later gate removes that grant" comment.
- `tests/rotation_worker/test_revision_watcher_rows.py:38-40,93,98-104`: the `STILL_GRANTED`
  comment, constant, assertion, and test.

Not fixed (dated records or statements that don't claim a live grant):

- `docs/superpowers/reports/rw-ro-token-report.md:18,25-26,31,38,64,128,134`: the G-62 build
  report. It records the state when it was written and names this follow-up; reports are history.
- `docs/superpowers/reports/lanes-inc-3-revision-watcher-rows-report.md:57`: the G-30 report's
  list of granted secrets, a dated record.
- `docs/superpowers/reports/lanes-inc-3-revision-watcher-report.md:38,200`: the increment 3 report
  that named the token as the lane's then-current read, a dated record.
- `docs/superpowers/plans/2026-11-05-platform-part-2-api-identity-cli.md:472,540,558` and
  `docs/superpowers/plans/2026-10-16-handoff-change-manager-overlap.md:94`: plans about the token
  itself, not about the revision watcher's grant.
- `docs/superpowers/specs/2026-10-30-postgres-role-rotation-design.md:150`: names the token for a
  2026-10-30 Coolify write; unrelated to the grant.
- `.bws-secrets.toml:130-133`: "It stays declared because a lane image built before that change
  still fetches it, until the lane is released and redeployed". That is about the image, not the
  grant, so I left it. See the concerns section.
- `docs/decisions/0059-…md:1386,1552`: "write-capable key" about the SDS lanes GitHub App.
- `docs/decisions/0059-…md:718`, `src/orchestrator/services/lifecycle/claims.py:49`,
  `tests/contract/test_rollout_policy_ratification.py:95`,
  `tests/architecture/test_wsp21_invariant_scan.py:123`: "still holds" matches about other
  subjects.

No hit is in `scripts/`, `src/`, or `lanes/` for this grant, so nothing there needed changing.

## Tests and checks

- `make check` in the worktree, against `orchestrator_test_hq_w_173` (migrated with
  `alembic upgrade head`, `PGTZ=UTC`, `SECURITY_STANDARDS_DIR` at the fixtures): ruff
  `All checks passed!`, pyright `0 errors, 0 warnings, 0 informations`, pytest **collected
  15,002 tests** (xdist `[15002 items]`): 14,988 passed, 1 skipped, **13 failed**, exit 2.
- All 13 failures are in `tests/work_watcher/test_launcher_orchestration.py`, a file this change
  doesn't touch. Control: the same file on an unmodified `git archive` of `origin/main`
  (`177448d5`) fails the same 13 (13 failed, 2 passed). Cause: the test runs
  `scripts/run-work-carrier.sh` with the real `HOME`, and this Mac has `~/.factory/chain-db.env`
  (the FE-5 switch file, added to the launcher by #703). The launcher then fetches the
  factory-events read password, which the test's stub `bws` doesn't answer, and fails with
  "could not read the factory-events read password from BWS (narrow)". With `HOME` set to an empty
  temp directory, all 15 tests in the file pass. CI has no switch file; `main`'s last three
  `Quality` runs are green.
- `tests/rotation_worker/test_revision_watcher_rows.py`: 13 passed.
- Mutation check: re-adding `bbd71f41-…` to `reach_secrets` fails
  `test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper`; restored, 13
  pass. 1 mutant, 0 survivors.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml`
- `tests/rotation_worker/test_revision_watcher_rows.py`
- `docs/superpowers/reports/rw-old-grant-cleanup-report.md` (this report, committed to the PR
  as the G-62 report was in #712)

## Self-review

- The row's reach and `GRANTED` hold the same six UUIDs, equal to the five lane secrets plus the
  ping URL keeper, which the ping keeper's `lanes` membership makes `_lane_secrets()`.
- `test_no_other_lane_token_row_declares_the_same_reach` still passes: the six-UUID reach is
  distinct from every other lane token's.
- Comments state the rule as it stands, with the G-64 removal named once per file.
- `/code-review` at low effort on the diff: no findings.

## Concerns

- `tests/work_watcher/test_launcher_orchestration.py` isn't hermetic: it inherits the operator's
  `HOME`, so on any machine with `~/.factory/chain-db.env` it fails 13 tests, and `make check` on
  the operator Mac is red for that reason. The fix is to set `HOME` to the test's tree in `_run`.
  I didn't make it, because it's outside this handoff's scope.
- `.bws-secrets.toml` keeps the `prod-coolify-api-token` entry "because a lane image built before
  that change still fetches it". With the grant gone, such an image (or a rollback to one) can't
  read it, so the lane fails at that fetch. `docs/operations/deploy.md` carries notes from the
  `7d980eaf` release (#714), so the deployed lane runs the launcher that reads only the read-only
  token; the exposure is a rollback to a pre-#712 image. That sentence now sits beside "the lane's
  account no longer sees it". I left it because the handoff says to change no `[[secret]]` entry;
  HQ can decide whether the entry and that sentence go. I made no Coolify or VPS call to confirm
  the deployed digest.
- The test database `orchestrator_test_hq_w_173` and the worktree are left in place for a
  possible fix round. Two temp directories, `/tmp/w173-control.*` and `/tmp/w173-home.*`, remain;
  I didn't delete them.

## Standards followed

- Code Brain rule 4 (BLOCK, never silently swallow an exception): no exception handling added or
  changed.
- Code Brain rule 5 (BLOCK, never log or write secrets): the diff holds only BWS UUIDs, which are
  identifiers. No token value appears in any file, commit, or command, and I didn't run `bws` or
  read the Keychain.
- Code Brain rule 3 (structured logs to stdout): doesn't apply; no production code changed.
- Infra Brain 2, 591, 592, and 593 (BLOCK, no secret, `.env`, or BWS token in a tracked file or
  commit): none added. The comments describe grants by name and UUID only.
- Repo style: the comments use present tense and active voice and avoid "should". `make check`
  ran with its count reported. Each comment states the rule as it stands, with the history in this
  report, per the placement rule.
