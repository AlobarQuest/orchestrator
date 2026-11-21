Outcome: done

# Report: the broker row's reach names the bump driver's four secrets

PR: AlobarQuest/orchestrator#772, branch `hq/lanes-inc-5-bump-driver-broker-reach`. The change
commit is `bef6316a1e704b58ddb75bb9c04e53bbecb39e27`; this report is the commit after it. The base
is `origin/main` at `8c0f97b7`, the same commit the handoff read. Not merged.

## What I built

- `.cred-consumers.toml`, the `secrets-broker-bws-token` row. `reach_secrets` now also lists
  `750f737f-…`, `da8134b0-…`, `2f708d08-…` and `430691e2-…`, so it holds seven ids. Each id matches
  `[lanes.bump-driver]` in `lanes/secrets-broker/allowlist.toml` at base, so the first stop point
  didn't apply. The comment block lists every single-secret grant by project: Rotation / Keepers
  (four), Brains - Contributor (two) and Factory Events (one). It dates the bump driver's four to
  G-84 on 2026-11-21 and keeps Devon's quoted choice.
- `.cred-consumers.toml`, the `bump-driver-ping-url` row. The comment and the consumer `note` say
  the grant is given at G-84 and proven at G-86. Before, they said the read happens "once ... is
  granted".
- `scripts/run-bump-driver.sh`, comment only (lines 42-46). The header said the launcher reads
  "the same five secrets, plus the App key" and that the allowlist lists "exactly the seven". It
  now names all eight reads: the five Mac secrets, the chain's read password (`2f708d08-…`, which
  the old sentence left out), the App key, and the ping-URL secret that `sds-deadman.sh` reads. So
  "exactly the eight" matches the allowlist.
- `docs/operations/credentials.md` (~437) and `docs/operations/secrets-broker.md` (~71): each grant
  is stated as given at G-84 and proven at G-86, not as Devon's future act at the gate.
- `docs/operations/deploy.md`, the bump driver's cut-over step 5 (~1163). This passage turned up in
  the handoff's grep. Before, it said the Brain keys and the read password "are outside what the
  broker's account reads (... the row's reach)", which this change makes false. It now says they
  sit outside the account's projects and take single-secret grants, given at G-84 and listed in
  `reach_secrets`. The ping-URL secret is folded into the same sentence. I left deploy.md ~1243
  (follow-up-mint's future grant) unchanged because it's out of scope and still true. No ADR text
  changed.
- `tests/rotation_worker/test_secrets_broker_row.py`: `SECRETS` held exactly three ids and now
  holds all seven. `test_the_row_declares_the_granted_projects_and_secrets` checks the whole set
  for equality.

## Tests and checks

- `make check`: green, with 14,865 passed and 1 skipped (ruff, pyright and pytest). It ran against
  `orchestrator_test_hq_w_257`.
- Targeted tests: 102 passed across `test_secrets_broker_row.py`, `test_bump_driver_rows.py` and
  `test_lane_credentials.py`.
- Mutation: I removed `2f708d08-…` from `reach_secrets`, and
  `test_the_row_declares_the_granted_projects_and_secrets` failed (1 failed, 5 passed). After I
  restored the file from a copy, `cmp` showed it byte-identical and the file passed again (6
  passed).
- Security scan from the worktree root: 0 BLOCK. It reported 2 WARN, on `.gitignore` and
  `.bws-secrets.toml`, neither of which this change touches, and 1 INFO.
- `code-standards risk`: `high-risk`, because the `secrets` rule matched the test file's path. I
  ran the `vendor-review` skill. Claude approved and Codex approved, with 0 findings and 0 dropped.
  agy is off by configuration. Exit code 0.
- infraops `cred-findings`: I made no infraops call. `git grep reach_secrets` in
  infraops-mcp-server finds no Python reader. The orchestrator's loader,
  `bws_lane_token.check_row` and `declared_reach`, accepts the new row, and
  `test_no_other_lane_token_row_declares_the_same_reach` passes. So the second stop point didn't
  apply.

## Files changed

- `.cred-consumers.toml`
- `scripts/run-bump-driver.sh` (comments only)
- `docs/operations/credentials.md`
- `docs/operations/secrets-broker.md`
- `docs/operations/deploy.md`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `docs/superpowers/reports/lanes-inc-5-bump-driver-broker-reach-report.md` (this report)

## Self-review

- The change touches no code: `run-bump-driver.sh` changes only in comments.
- I left the allowlist, every compose, `.bws-secrets.toml` and the follow-up-mint rows alone, as
  the handoff says.
- New comment lines stay within 100 columns.
- The deploy.md step 5 edit goes slightly past the two files the handoff named. The handoff's grep
  rule covers it: the old text pointed at the very `reach_secrets` field this change updates, so
  it was false once the change landed.

## Concerns

- I didn't measure the security scan's two WARN findings on base. They're on files this diff
  doesn't touch, so this change can't have added them.
- Teardown: I'm leaving the worktree and `orchestrator_test_hq_w_257` in place for HQ to remove
  after the merge.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** this change touches no logging code.
- **Code Brain rule 4 (never swallow an exception silently):** the one changed test asserts set
  equality and catches nothing.
- **Code Brain rule 5 (never log secrets):** nothing new prints anything. The diff, the commit,
  the PR and this report hold only BWS secret ids, never a value, ping URL or token.
- **Infra Brain 591, 592, 593:** no BWS token appears in any tracked file or the commit. No inline
  `BWS_ACCESS_TOKEN` value appears. The test adds only BWS secret UUIDs, which don't match the
  token shape. The security scan shows 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** no secret value appeared during this session.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply, because nothing is served.
- **Infra Brain 2890 (never commit a ping URL):** no ping URL is committed. Only the ping-URL
  secret's BWS id (`430691e2-…`) is, and ids are allowed.
- **Infra Brain lesson 450 (rotation landmines):** the test, the comments, the docs and this
  report carry ids only, with no secret values.
