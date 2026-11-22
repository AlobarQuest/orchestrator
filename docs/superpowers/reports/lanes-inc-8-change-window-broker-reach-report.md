Outcome: done

# Report: the broker's registry row names change-window's five secrets

Handoff: `lanes-inc-8-change-window-broker-reach` (ADR-0059 increment 8, step 3j; deploy.md, the
change-window cut-over's item 6). Worker W-270. Base `957bcebd`. PR #787, branch
`hq/lanes-inc-8-change-window-broker-reach`, head before this report's commit `37e6d575`. Not merged.

## What I built

I followed #780's `.cred-consumers.toml` broker block and `test_secrets_broker_row.py` parts.

1. `.cred-consumers.toml`, row `secrets-broker-bws-token`:
   - Line 1502: `reach_secrets` keeps the existing eight in order, then appends
     `3d41395a-…` (change-window-coolify-token), `aa7c76af-…` (change-manager/M2M_TOKEN_EXECUTE),
     `0e9ebf2d-…` (change-window-anthropic-key), `71616bf5-…`
     (ALERT_HUB_SENDER_TOKEN_REPORT_CHANGE_WINDOW), and `1d6a0ab4-…` (change-window-ping-url).
   - Lines 1483-1486: the Rotation / Keepers single-secret grant list names the five.
   - Lines 1492-1493: the change window's five were given at HQ gate G-88 on 2026-11-22, and the
     session handling G-89 proved them the same day with `lane-run change-window --read`.
2. `tests/rotation_worker/test_secrets_broker_row.py`: the `SECRETS` set has the five ids, each
   with a name comment.
3. The following stale text this change affected is fixed, in the `change-window-ping-url` row of
   `.cred-consumers.toml`:
   - Lines 1884-1888 (block comment): "The broker reads the keeper for the lane once the compose
     names its id … item 6's `lane-run --read` is its proof" became a statement that the compose
     names the id and the broker's account holds the grant, given at G-88 and proved by G-89's
     `lane-run --read` on 2026-11-22. I reflowed the short "Handed to the SDS" line in a second
     commit.
   - Line 1903 (keeper `note`): "reads it for the lane once the compose names its id" became
     "reads it for the lane through its single-secret grant", the wording #780 used.

   I found no stale "once" wording on the change-window lines in `docs/operations/credentials.md`
   (lines 440-452 already describe the G-88 grants as given) or in
   `docs/operations/secrets-broker.md`, so neither changed. deploy.md is unchanged.

### Id confirmation

All five ids match `lanes/secrets-broker/allowlist.toml` `[lanes.change-window]` at the base. Four
of them (Coolify, execute bearer, Anthropic, digest sender) match `scripts/run-change-window.sh`
lines 65-73. The launcher doesn't name the ping-URL id `1d6a0ab4-…`: the launcher gets it from
`SDS_DEADMAN_PING_URL_UUID`, which `lanes/change-window/compose.yaml` line 32 sets to the same id.
I treated that as consistent, not as a mismatch.

## Tests and checks

- Mutation: I copied `.cred-consumers.toml` to `/tmp/w270-cc.bak` and removed `71616bf5-…` from
  `reach_secrets`. Then `test_the_row_declares_the_granted_projects_and_secrets` failed (1 failed,
  5 passed). I restored the file with `cp`, `cmp` reported no difference, and the file passed again
  (6 passed).
- `make check` against `orchestrator_test_hq_w_270` (migrated, `SECURITY_STANDARDS_DIR` at the
  fixture, `PGTZ=UTC`): exit 0. Lint and pyright clean. 14,892 collected, 14,891 passed, 1 skipped.
  After the reflow commit, `test_secrets_broker_row.py` and `test_change_window_rows.py` passed
  (14 passed).
- Security scan from the repo root: 0 BLOCK, 2 WARN (`bws.secret-files-gitignored`,
  `bws.manifest-matches-usage`), 1 INFO (`bws.least-privilege-scope`). These are repo-level
  findings. This diff doesn't touch `.gitignore` or `.bws-secrets.toml`, so it didn't cause them.
- `code-standards risk`: `high-risk` (rule `secrets`, on the test file's name). I ran
  `vendor-review`: Claude and Codex both approved, 0 findings, 0 dropped. agy: off by
  configuration.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `docs/superpowers/reports/lanes-inc-8-change-window-broker-reach-report.md` (this report)

## Self-review

The change is data plus a pinned test, in #780's shape. The grant list in the comment and the test's
name comments use the allowlist's names. `reach_projects`, the allowlist, compose, launcher,
`scripts/secrets_broker.py`, `.bws-secrets.toml`, ADRs, and every other row's `reach_secrets` are
unchanged. I made no production call.

## Concerns

- The `estate-landing-ping-url` row's keeper note (`.cred-consumers.toml` line 1419) still says the
  broker reads it "once the compose names its id". That row is outside this handoff's scope, so I
  left it. It's a candidate for a one-line follow-up.
- The proof facts (G-88 grants, G-89's five `ok` lines and the `refused` control) come from the
  handoff. I didn't re-run the read, because the handoff rules out production acts.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): the diff makes no logging change.
- Code Brain rule 4 (never swallow an exception silently): the changed test adds set members only.
  It adds no `try` or `except`, and the mutation run shows it fails loudly.
- Code Brain rule 5 (never log secrets): the test and this report contain BWS secret ids only. No
  output printed a value.
- Infra Brain 591, 592, and 593: no BWS token in a tracked file or in history, and no inline
  `BWS_ACCESS_TOKEN` value. The scan reports 0 BLOCK findings.
- Infra Brain 1032 (exposure means rotate): I saw no secret value during the work.
- Infra Brain 212 (HTTPS for public FQDNs): doesn't apply. Nothing is served.
- Infra Brain 2890: the diff commits no ping URL, only the keeper's BWS secret id `1d6a0ab4-…`.
- Infra Brain lesson 450 (rotation landmines): ids only. No secret value in the test, report, or
  logs.
