Outcome: done

# Report: follow-up minting's records after infra-drift stops minting

Worker W-262, handoff `lanes-inc-9-follow-up-mint-records`. Branch
`hq/lanes-inc-9-follow-up-mint-records`, base `origin/main` at `5e1a408a`. PR #780, ready (not a
draft), not merged. The gates and CI ran at head `c512e407`; the commit that records them is the
next one.

## What I built

This change is ADR-0059 increment 9 (Amendment 10), step 3j-inc-9. It carries out
`docs/operations/deploy.md`, follow-up minting's cut-over list, item 10 (lines 1257-1260 at
`5e1a408a`): "In this repository, after step 9 merges: the SYSTEM row's consumers in
`.cred-consumers.toml` (the infraops `drift-audit.sh mint-follow-ups` reader leaves;
`run-follow-up-mint.sh` joins the orchestrator reader's list) and the keeper's note; ADR-0007's
'Scheduled trigger'; `follow-up-scheduling.md`, 'Running a pass'; and `credentials.md`'s SDS
consumer paragraph."

I confirmed step 9 at the source: at infraops-mcp-server `c7de0b70`, `scripts/drift-audit.sh` has
no line matching `mint` or `BWS_ACCESS_TOKEN_SDS`. That was a read-only `git show`.

Line numbers in the following list are on the branch.

1. `.cred-consumers.toml`:
   - Line 50, the keeper's note on the `bws-sds-operator-token` row. I removed only the clause
     naming `drift-audit.sh`'s mint-follow-ups step. The attestation "(Devon, 2026-10-23)" and the
     other on-demand readers stay as they were. The lane isn't added here, because it reads
     through the broker's token, not this Keychain item.
   - The SYSTEM row (`orchestrator-system-bearer`, `221a48d5-…`), lines 213-216: I removed the
     `runtime-fetch` consumer with `repo = "infraops-mcp-server"` and
     `ref = "scripts/drift-audit.sh mint-follow-ups (03:00)"`. I added `run-follow-up-mint.sh` to
     the orchestrator reader's comma-joined `ref`, after `run-estate-landing.sh`. That's the same
     shape the bump driver, another broker lane, uses there.
   - The attestation is unchanged. The row's last consumer edit, `f817a6ee` (#609), changed only
     that `ref` line, with no date or attestation field, so this change adds none. No entry carries
     `consumers_verified` (file header, line 12). The consumer set loses a reader that
     `c7de0b70` shows no longer reads, and gains the lane reader the 2026-11-22 scheduled pass
     proved.
   - The `follow-up-mint-ping-url` row (HQ answer A): I changed the comment (lines 1454-1459) and
     the `bws-secret` keeper note (line 1474) to #772's granted-and-proved form. Devon gave the
     grant at G-87 on 2026-11-21, and the broker logged `ok` for it on the 2026-11-22 scheduled
     pass.
   - The `secrets-broker-bws-token` row: I added `46e714ca-32b7-4e89-9817-b509013363a5` to
     `reach_secrets` (line 1498). The reach comment (lines 1481-1490) lists
     `follow-up-mint-ping-url` among the Rotation / Keepers grants, and says Devon gave it at G-87
     and the first scheduled pass read it, in #772's style.
2. `tests/rotation_worker/test_secrets_broker_row.py` line 46: `SECRETS` gains the same id, with
   the comment `# follow-up-mint-ping-url`.
3. `docs/decisions/0007-declared-follow-up-scheduling.md` lines 127-132: I added a dated note
   ("**Amended 2026-11-22 (ADR-0059 Amendment 10, increment 9).**") at the end of "Scheduled
   trigger", following the repo's dated-amendment convention. It says the trigger is the
   `follow-up-mint` lane and that `drift-audit.sh` no longer mints (#173, `c7de0b70`). The
   original decision text is unchanged.
4. `docs/operations/follow-up-scheduling.md`, "Running a pass" (lines 87-98, 104-105):
   - The daily pass is the lane's 06:43 New York `lane-run follow-up-mint`.
   - The text saying `drift-audit.sh` runs the pass is gone, and so is the claim that
     `run-follow-up-mint.sh` was deleted.
   - The manual-run text matches the launcher: it runs only in the lane's container, so an extra
     pass is `docker exec <container> lane-run follow-up-mint` on `prod-vps`. A bare CLI pass needs
     `--idempotency-key` (required by `cli.py`) and the three variables the launcher sets.
5. `docs/operations/credentials.md`, the SDS consumer paragraph (lines 90-93, 99-100): the
   `drift-audit.sh` reader and its separate BWS identity are described in the past tense and
   removed by #173. The lane is the bearer's one scheduled minting reader, through the broker.
6. `docs/operations/deploy.md`:
   - Lines 1219-1222: the "Until the infraops-mcp-server change in step 9 lands…" paragraph now
     says the lane is the one scheduled minting pass, and that a same-day second pass (a hand run)
     mints nothing twice.
   - I didn't mark items 9 and 10 done: that list marks none of its finished items 1 to 8.
7. Comment-only edits per HQ's answer A:
   - `scripts/run-follow-up-mint.sh` lines 27-28.
   - `lanes/follow-up-mint/crontab` lines 2-4. The schedule line is unchanged.
   - `scripts/install-landing-ledger-launchd.sh` lines 14-15.
   - `tests/scripts/test_sds_bws.py` lines 222-223, a docstring only. The `>= 11` floor is
     unchanged.

HQ's answer is committed as
`docs/superpowers/reports/lanes-inc-9-follow-up-mint-records-question-answered.md`.

### Item 7: the grep

`git grep -n -i "drift-audit.sh mint\|mint-follow-ups (03:00)\|BWS_ACCESS_TOKEN_SDS"` at the base
returned 156 lines. They fall into the following groups:

- **Fixed:**
  - `.cred-consumers.toml:221`: the SYSTEM row's infraops reader, removed.
  - `.cred-consumers.toml:50`: the keeper's note, which names the `mint-follow-ups` step and
    matched on `BWS_ACCESS_TOKEN_SDS`. I edited it.
- **Left, still true:**
  - `docs/operations/deploy.md:1256` and `:1258` are items 9 and 10 themselves. They describe the
    steps and stay as written.
  - Every other `BWS_ACCESS_TOKEN_SDS` hit outside history names the Mac Keychain account of the
    `sds-operator` token, which other lanes still read. That covers 37 other lines in
    `.cred-consumers.toml`, the `scripts/run-*.sh` launchers, `sds-token.sh`,
    `deploy_orchestrator.py`, `exit_probe.py`, `replay_deploy_signal.py`, their tests,
    `credentials.md` lines 58, 302, 313 and 404, `PROJECT.md:113`, and ADR-0059 lines 40 and 702.
    None of them says infra-drift mints.
- **Left, history:** I left `docs/superpowers/reports/` (lanes-inc-9-follow-up-mint, lanes-inc-1,
  lanes-inc-3, lanes-inc-4 and adr-0059 reports), `docs/superpowers/plans/` and
  `docs/superpowers/specs/` unchanged. These are dated records.

Hits from a wider search (`drift-audit` and `mint-follow-ups`) outside history:

- ADR-0059 lines 823, 1080, 3004-3023, 3121-3169 and 3199: amendment text pinned to infraops
  `c3359bf` or describing the plan. I left them unchanged because they're decision records.
- `ADR-0055` lines 1031 and 1160, `rotation-executor.md` lines 1054 and 1077,
  `src/rotation_worker/adapters/change_manager.py:40`, `local-development.md:524` and
  `sds-deadman.sh:49`: these are about `drift-audit.sh`'s change-manager `full` bearer, its ping
  path or its env-file ping URL, not about minting. They still hold at `c7de0b70`.
- `scripts/run-deploy-watcher.sh:106` and `scripts/run-estate-landing.sh:27`: these cite
  `drift-audit.sh` as the precedent for an identity override. I left them unchanged, as HQ
  directed; see the concerns section.

A last sweep at the head searched for `infra-drift` or `infraops` together with `mint` or
`follow-up`, outside history, uncut. It returned 33 lines:

- The lines this diff changed, and deploy.md items 9 and 10.
- `deploy.md:1201` ("after infra-drift's 03:00 audit") gives the order of the schedule, which is
  still true.
- ADR-0059 lines 823, 1079-1083 and Amendments 9 and 10 (2981-3218): decision records, left
  unchanged. I read line 823's row in full: it lists minting among infra-drift's duties, and
  Amendment 9's table (line 3074) already supersedes that row. Line 3199 names ADR-0007,
  `follow-up-scheduling.md` and `credentials.md` as the records increment 9 changes, and all three
  are in this diff.
- Lines that don't describe minting: `PROJECT.md` (backlog history), `deploy.md:1322`,
  `ADR-0059:4356` and `test_follow_up_mint_rows.py:81`.
- `scripts/run-follow-up-mint.sh:72`: "The key form infra-drift's Node CLI sends; the route accepts
  it and never reads it." I left it unchanged; see the concerns section.

## Tests and checks

- `make check` with `orchestrator_test_hq_w_262`, at `c512e407`: ruff "All checks passed!",
  pyright "0 errors, 0 warnings, 0 informations", pytest 14,873 passed and 1 skipped (317 s).
  `pytest --collect-only -q` reports "14874/14885 tests collected (11 deselected)". An earlier run
  at `1dff3530` gave the same pass count.
- CI on PR #780 at `c512e407` (run 45021730118): `Quality` passed (14,848 passed and 26 skipped in
  the job log), and so did `Rollout transcription currency` and `Runner consumer compatibility`.
- Mutation proof for `test_secrets_broker_row.py`. I copied both files to a backup directory
  outside the worktree and cleared `tests/**/__pycache__` around each run. The assertion is a set
  equality (`declared_reach(...) == (PROJECTS, SECRETS)`).
  - Control: 6 passed.
  - Id removed from the row's `reach_secrets`: 1 failed, 5 passed. Restored with `cp`, and `cmp`
    showed identical files.
  - Id removed from the test's `SECRETS`: 1 failed, 5 passed. Restored with `cp`, and `cmp` showed
    identical files.
  - After restoring: 6 passed, and `git status` was clean.
- Security scan from the repo root, at `c512e407`: BLOCK 0, WARN 2 (`bws.secret-files-gitignored`,
  `bws.manifest-matches-usage`), INFO 1 (`bws.least-privilege-scope`). I couldn't run a baseline
  scan of `origin/main` in a scratch directory (the command was denied). This diff changes no
  gitignore, no `.bws-secrets.toml` and no code reference to a secret id, so it can't add either
  WARN. I didn't prove that with a run, though.
- `code-standards risk`: `high-risk`, matched rule `secrets: tests/rotation_worker/test_secrets_broker_row.py`.
  So I ran the `vendor-review` skill (`/tmp/vendor-review-v67m3eji`, diff sha256 `aa25d5f6…`).
  - Review complete: Claude and Codex both approve, with 0 findings and 0 dropped.
  - agy is off by configuration.
  - Blast radius: the registry and the docs that cite it. `test_secrets_broker_row.py` and the
    `test_sds_bws.py` docstring have no dependents.
- `/code-review` (low) on `origin/main...HEAD`: I ran it twice, the second time at `c512e407`.
  Both runs returned an empty result (`(none)`), not an explicit verdict.

## Files changed

- `.cred-consumers.toml`
- `docs/decisions/0007-declared-follow-up-scheduling.md`
- `docs/operations/credentials.md`
- `docs/operations/deploy.md`
- `docs/operations/follow-up-scheduling.md`
- `lanes/follow-up-mint/crontab` (a comment only)
- `scripts/install-landing-ledger-launchd.sh` (a comment only)
- `scripts/run-follow-up-mint.sh` (a comment only)
- `tests/rotation_worker/test_secrets_broker_row.py`
- `tests/scripts/test_sds_bws.py` (a docstring only)
- `docs/superpowers/reports/lanes-inc-9-follow-up-mint-records-question-answered.md` (HQ's answer)
- This report

## Self-review

- The two registry changes are the only behavioural ones: one consumer fewer and one lane reader
  named on the SYSTEM row, and one id more in the broker's declared reach. The broker-row test pins
  the reach in both directions. No other credential row's fields changed; only the
  `follow-up-mint-ping-url` row's comment and note text changed, which HQ approved.
- The launcher, crontab and installer edits are inside `#` comments. `make check` covers the lane
  tests and the shell checks.
- I didn't give the G-87 grant a date of my own. The date 2026-11-21 comes from HQ's answer.
- The crontab's line 4 is shorter than its neighbours. I kept it that way so the diff touches only
  the three lines that changed.

## Concerns

- **Follow-ups (HQ directed: leave unchanged).** `scripts/run-deploy-watcher.sh:106` and
  `scripts/run-estate-landing.sh:27` cite infraops-mcp-server's `drift-audit.sh` as "the in-estate
  precedent for overriding the identity for a single foreign call". At `c7de0b70`,
  `drift-audit.sh` reads only its own `BWS_ACCESS_TOKEN` and overrides no identity, so the
  precedent is gone. Both are other lanes' launchers.
- **One more stale launcher comment, not in HQ's answer.** `scripts/run-follow-up-mint.sh:72`
  says "The key form infra-drift's Node CLI sends". `c7de0b70` removed the Node CLI's
  `mint-follow-ups` command, so "sends" is now "sent". I left it because HQ's answer covered lines
  27-28 of that launcher only. It's a candidate for the same follow-up.
- **Lane image drift (accepted by HQ).** The launcher and crontab comment edits ship in the lane
  image, so the running release's copies differ until the next lane release. The difference is in
  comments only.
- **Scan baseline.** The two WARN findings weren't compared against `origin/main` by a run; see
  the tests and checks section.
- **Not covered by the Standards section:** none.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging changes. The diff touches no
  logger and no log call.
- **Code Brain rule 4 (never swallow an exception silently):** the one changed test adds a set
  member and has no `try` or `except`. The docstring edit changes no behaviour.
- **Code Brain rule 5 (never log secrets):** no test output, doc or comment prints a ping URL, a
  check's ping UUID or a token. The only ids added are the BWS secret id `46e714ca-…` and the
  existing `221a48d5-…`, which are allowed.
- **Infra Brain 591, 592 and 593:** no BWS token in any tracked file or commit, and no inline
  `BWS_ACCESS_TOKEN` value. The security scan reports BLOCK 0.
- **Infra Brain 1032 (exposure means rotate; never revoke a credential whose consumer set isn't
  attested):** I saw no secret value. The SYSTEM row's consumer set stays true. The reader I
  removed no longer reads at `c7de0b70`, and the reader I added is the lane whose scheduled pass
  read the bearer through the broker on 2026-11-22. The row carries no `consumers_verified`, and
  I added no attestation field, following `f817a6ee`. The keeper note's 2026-10-23 attestation
  keeps its date and only loses the reader that left.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; nothing served changes.
- **Infra Brain 2890 (never commit a ping URL):** no `https://healthcheck.alobar.net/ping/...` URL
  is in the diff. Only the ping-URL secret's BWS id is.
- **Infra Brain lesson 450 (rotation landmines):** ids only. The test, the docs and this report
  carry no secret value.
