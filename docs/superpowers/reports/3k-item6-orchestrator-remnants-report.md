Outcome: done

# 3k item 6: orchestrator fork-chain remnants (first part)

Branch `hq/3k-item6-remnants`, base `origin/main` `ef463b44`. PR: AlobarQuest/orchestrator#805 (not merged; held for the paired
intent-packages PR).

## What I built

1. **Fixture.** `tests/fixtures/rotation_destination_format.json` lines 153 and 155: removed
   `run-tool-installer.sh, ` from the `ref` value and from the `destination` string of the
   orchestrator `runtime-fetch` row. Nothing else changed; the file still ends `\n}\n`.
   `DESTINATION_FORMAT_SHA256` in `tests/rotation_worker/test_destinations.py` line 29 updated.
   - Old hash: `3c81cc3d22882598efa0486af4bf980f620f482243198ae1912f0134c0453f41`
   - New hash: `24f0db4325ea10b2ac435c49cd900545e80e558912c7485ce45f1587432d3539`
2. **Stale comments and docstrings.**
   - `scripts/sds-deadman.sh` 323-329: dropped the `--install` for `run-tool-installer.sh` clause
     (that launcher is in localops); reflowed the paragraph.
   - `scripts/run-bump-proposer.sh` 174, `run-change-proposer.sh` 88, `run-work-carrier.sh` 151:
     the outcome-alarm model is now `sds-membership-check`, an outcome-alarm lane still here.
   - `scripts/run-membership-check.sh` 39, 89-93, 115, 128 (post-edit lines): removed the fork-list read references
     (the check makes no fork-list read; `src/membership_check` has no fork code and no `gh` call).
     Line 128 now says any `gh` call in the pass uses the minted token.
   - `src/estate_clients/github_app_mint.py` 17: "the fork-sync App" is now "localops' fork-sync
     App". The clause describes the key's stored form, which `.bws-secrets.toml` 274-284 shows is
     still true of that App's key; its reader is localops' `fork-sync onboard`.
   - `docs/operations/deploy.md` 1489-1496: the membership-check paragraph mirrored the launcher's
     fork-list wording (found by the sweep in item 5); same removals, reflowed.
   - `src/landing_ledger/record.py` 198-199: "the forks leave ... when they leave" is now past tense.
   - `src/landing_ledger/github.py` 214-215: dropped "and neither fork is among them".
   - `tests/membership_check/test_consistency.py` 302-303: the docstring no longer says the forks
     stay members; it says the check holds no fork list to compare against.
3. **`PROJECT.md` 200:** marked `[x]` with "moved to localops (#753); tracked there".
4. **Manifest grep:** `docs/operations/wave-exit-manifest.toml`,
   `docs/operations/exit-criteria-claims.toml` and `docs/evidence/` contain none of: the fixture
   name, `DESTINATION_FORMAT_SHA256`, the old hash, `fork-sync-watch`, `fork list`,
   `fork-sync App`, `run-tool-installer`, `test_a_granted_fork`, or any changed file's path. No
   attested bar is affected. `deploy.md` is not named there either.
5. **Sweep for anything the handoff's list missed:**
   `grep -rnE 'fork-sync|fork-harden|tool-installer|tool_installer|fork list|fork_sync' scripts src tests lanes docs/operations`.
   `scripts/run-tool-installer.sh` is absent. Remaining hits after this PR:
   - `models.py` 110, `model_in_checks.json`, `tests/services/test_revert_*` and
     `test_inert_landing_admission.py`: on the Don't list.
   - `test_wsp21_invariant_scan.py` 181, `test_interpreter_agreement.py` 107 and
     `local-development.md` 499: history or measurement notes; left.
   - `docs/operations/membership-seed.toml`: applied history (see `test_seed.py`, in the following
     section); left.
   - `docs/operations/fork-hardening.md` and `docs/operations/onboarding-a-fork.md`: **not changed,
     reported.** Both whole pages describe the chain as running from this repository (main-tree
     venv paths, `com.devon.fork-*` LaunchAgents, `src/fork_sync/`, `src/tool_installer/`).
     localops has no copy of either (its tree has no `docs/operations/`), and the root `CLAUDE.md`
     lists `onboarding-a-fork` among this repo's pages. Moving them is cross-repo work and deleting
     them would drop knowledge, so HQ decides where they go.

## Left as is, and why

- `tests/landing_ledger/test_cli.py` 314-315: "the two fork rows left with the fork chain" is
  already true; no change needed.
- `tests/sds_members/test_seed.py` 69-72 (the `forks` set): **report, no change.** The test pins
  `docs/operations/membership-seed.toml`, the phase-1 seed that the module docstring calls "applied
  history", not the live member record. The seed still grants the five forks
  `dependency_updates` and `security_patches`, so the assertion passes, but the seed no longer
  matches the member record now that the forks have left. Per the handoff I changed no behaviour.
  HQ can decide whether to delete the `forks` comparison (the docstring's own rule: "a list that
  changes on purpose is a reason to edit or delete its comparison").
- `test_wsp21_invariant_scan.py` 181, 307 and `landing/interfaces.py` 257: left by instruction.
- Everything under the handoff's "Don't" list: untouched.

## Tests and checks

- `make check` with `orchestrator_test_hq_w_297`: green. Ruff "All checks passed", pyright
  0 errors, pytest **14,777 collected**, 14,776 passed, 1 skipped.
- After a comment reflow, re-ran `tests/scripts`, `tests/rotation_worker/test_destinations.py`,
  `tests/membership_check` and `tests/landing_ledger`: 2,037 passed. `bash -n` on every script:
  clean.
- PR #805 CI on commit `323d4b9f`: `Quality`, `Rollout transcription currency` and
  `Runner consumer compatibility` all passed. The final head's run is recorded in the PR, not here,
  to avoid another CI cycle.
- `code-standards risk --repo .`: `normal`, so no vendor review is required.
- `/code-review`: skipped at my discretion (comment and fixture text only, no logic). I
  self-reviewed the diff line by line instead.

## Files changed

`PROJECT.md`, `scripts/run-bump-proposer.sh`, `scripts/run-change-proposer.sh`,
`scripts/run-membership-check.sh`, `scripts/run-work-carrier.sh`, `scripts/sds-deadman.sh`,
`src/estate_clients/github_app_mint.py`, `src/landing_ledger/github.py`,
`src/landing_ledger/record.py`, `tests/fixtures/rotation_destination_format.json`,
`tests/membership_check/test_consistency.py`, `tests/rotation_worker/test_destinations.py`, and
this report.

## Self-review

- Only comments, docstrings, one backlog line, the fixture and its hash changed; no executable
  line changed (`bash -n` clean, the full suite green).
- `sds-membership-check` is an outcome alarm (`scripts/CLAUDE.md` lists `run-membership-check.sh`
  among the outcome-alarm launchers), so the three "as X's is" references stay accurate.

## Concerns

- `scripts/run-membership-check.sh` still sets and exports `GH_TOKEN` (lines 130-131, 156) and
  `tests/scripts/test_run_membership_check.py` asserts it. Its only documented reader was the fork
  list's `gh api` read, which no longer exists. Removing it is a behaviour change outside this
  handoff, so I left it and reworded the comment. HQ may want a follow-up to drop it.
- The `test_seed.py` `forks` set, described in the preceding section.
- `docs/operations/fork-hardening.md` and `onboarding-a-fork.md` still describe the chain as living
  here; see item 5.
- The PR holds for the paired intent-packages fixture PR; the hashes must match.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** no logging changed.
- **Code Brain 4 (never silently swallow an exception):** no error path changed.
- **Code Brain 5 (never log secrets, credentials, tokens):** no logging changed; no secret read.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files, history, or inline):** the diff and
  this report carry no token-shaped string; no BWS call was made.
- **Infra Brain 1032 `cred.exposure-rotate`:** no registry, exposure, or `consumers_verified`
  change. The fixture is a format test; `.cred-consumers.toml` and `.bws-secrets.toml` are
  untouched.
- **Infra Brain 212 (HTTPS):** not applicable.
