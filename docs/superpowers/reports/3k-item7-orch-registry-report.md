Outcome: done

# 3k item 7 (b): orchestrator registry drops localops' ended reads

- PR: AlobarQuest/orchestrator#806 (open, not draft; held for HQ to merge).
- Branch: `hq/3k-item7-orch-registry`, base `origin/main` `ef463b44`.
- Head SHA of the change: `12287ad9930815a54cfd2e4f36f7777410be2d61`. The report commit that follows it moves the PR head.

## What I built

I made four comment and row edits to the credential registry, so it no longer names the localops run-time BWS reads that localops #19 (`4f7bd937`) ended. The change list cites line numbers at orchestrator `ef463b44` and at localops `origin/main` `4f7bd937`:

1. **`.cred-consumers.toml` 150-155: I removed the localops `scripts/run-tool-installer.sh` `runtime-fetch` consumer of `bws-sds-operator-token`.** Confirmed at localops main: `scripts/run-tool-installer.sh`, `run-fork-sync-watch.sh`, `run-fork-harden.sh`, and `sds-deadman.sh` contain none of `BWS_ACCESS_TOKEN`, `sds-token`, `sds-bws`, `51509d5e`, `b415d03b`, or `bws`.
2. **`.bws-secrets.toml` 195-197: I removed the change-manager READ bearer's `fork-sync onboard` clause.** localops `src/fork_sync/bws.py` holds only `APP_PRIVATE_KEY_UUID` (27) and `ANTHROPIC_API_KEY_UUID` (29), and localops main names `314f276d` and `FORK_SYNC_CHANGE_MANAGER_TOKEN` only in `docs/superpowers/reports/localops-fork-sync-land-report.md` 57 and 60. The read is gone, so I dropped the clause.
3. **`.bws-secrets.toml` 229-231: the Healthchecks Management API key (`51509d5e-…`) now says localops reads it only at install time.** The comment names `scripts/sds-install.sh` and its installers, run interactively, and says the launchers read no BWS. Sources: localops `scripts/sds-install.sh` 44-52 (sourced by `install-fork-sync-watch-launchd.sh` 38 and `install-tool-installer-launchd.sh` 36), `install-fork-harden-launchd.sh` 34 and 55-63, and the `sds-bws.sh` header, which says no scheduled launcher sources it.
4. **`.bws-secrets.toml` 306-307: I removed the `--install`-pass bullet (`run-tool-installer.sh`) from the `sds-bridge` alert sender token's readers.**

The App key and Anthropic key reads by `fork-sync onboard` (`.bws-secrets.toml` 270-287) are unchanged.

## Step 3: intent-packages

`packages/rotation-bws-sds-operator-token/package.yaml` at intent-packages `origin/main` (`00b32ef`) still lists a tool-installer destination at line 47:
`runtime-fetch repo="orchestrator" ref="scripts/run-tool-installer.sh"`.
It names `repo="orchestrator"`, not `localops`. The registry never had an orchestrator row for that script, so the line is stale on both the repository and the read. Fixing it is a follow-up for the paired revision. I didn't change it.

## Step 4: readers of these rows and comments

- No test counts the removed consumer row or reads the edited comments. A search of `src`, `tests`, and `scripts` for `tool-installer` finds only two places. One is `tests/fixtures/rotation_destination_format.json` 153 and 155, which W-297's #805 changes, so I left it alone. The other is a comment at `scripts/sds-deadman.sh` 325.
- At run time, `src/rotation_worker/readers.py::scheduled_reads` (line 123) builds the rotation reader verification from each registry consumer's `healthchecks_check`. With the row gone, `sds-tool-installer` drops out of the checks the executor waits on after rotating `bws-sds-operator-token`. That's the intended result, because the job no longer reads the token.
- `rotation_proposer/generate.py`, `rotation_worker/registry.py`, and `membership_check` read the registry generically. None of them keys on this row.
- No test needed a change.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_298`, with `PGTZ=UTC` and `SECURITY_STANDARDS_DIR` set to the fixture: exit 0. Result: 14776 passed, 1 skipped.
- CI on #806: see the CI section.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml`
- `docs/superpowers/reports/3k-item7-orch-registry-report.md` (this report)

## Self-review

- The diff removes 7 lines from `.cred-consumers.toml` and rewrites 3 comments in `.bws-secrets.toml`. No `[[secret]]` table, UUID, `lanes` list, `[[credential.exposure]]` entry, or `consumers_verified` changed.
- The edited READ-bearer sentence still parses: "…run-work-carrier, and on demand scripts/replay_deploy_signal.py."
- I didn't use `/code-review` or the `code-standards risk` gate. The change is comment and data only, and `make check` exercises the registry parsers.

## Concerns

- `scripts/sds-deadman.sh` 325 still names `--install` for `run-tool-installer.sh` as an example of a lane whose bare invocation pings. That script left the orchestrator, so the example is stale. The handoff limits this PR to the two registry files, so I didn't change it.
- The intent-packages destination at line 47 (see step 3) names the wrong repository as well as an ended read.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** No code changed, so no logging changed.
- **Code Brain 4 (never silently swallow an exception):** No code changed.
- **Code Brain 5 (never log secrets, credentials, tokens):** No code changed. I read no secret value and made no BWS calls.
- **Infra Brain 591 `bws.no-token-in-tracked-files`:** The diff and this report hold UUIDs and abbreviated UUID prefixes only. They contain no token-shaped string.
- **Infra Brain 592 `bws.no-token-in-git-history`:** The commit adds no token. It only removes and rewrites comment text and one consumer row.
- **Infra Brain 593 `bws.bootstrap-token-not-inline`:** No bootstrap token appears anywhere in the change.
- **Infra Brain 1032 `cred.exposure-rotate`:** I added and removed no `[[credential.exposure]]` entry, and `consumers_verified` is unchanged.
- **Infra Brain 212 (HTTPS):** Not applicable.

## CI

All three checks passed on the change commit `12287ad9` (run 45444193958):

- `Quality`: pass, with 14763 passed and 14 skipped.
- `Rollout transcription currency`: pass.
- `Runner consumer compatibility`: pass.

No formatting fix was needed. The report-only commit that follows reruns CI on the new head.
