Outcome: done

# Report: the follow-up-mint lane's files and launcher (ADR-0059 increment 9, Amendment 10)

Worker W-213, branch `hq/lanes-inc-9-follow-up-mint`, base `origin/main` at `0e2265de`. Code,
tests, lane files, manifest rows and docs only. I made no production call, no BWS read, and no
change in infraops-mcp-server or `.cred-consumers.toml`.

## What changed

| File | Change |
|---|---|
| `scripts/run-follow-up-mint.sh` | New. The container-only launcher. |
| `lanes/follow-up-mint/compose.yaml` | New. One service; stateless, no volume, no network, no ports or labels; empty ping-URL placeholder. |
| `lanes/follow-up-mint/crontab` | New. `43 6 * * * lane-run follow-up-mint`. |
| `tests/scripts/test_run_follow_up_mint.py` | New. 18 tests (fakes only). |
| `scripts/deploy_orchestrator.py` | One `LANE_CHECKS` row: `"follow-up-mint": "sds-follow-up-mint"`, in alphabetical order. |
| `tests/scripts/test_deploy_lanes.py` | `follow-up-mint` added to the ping-URL test's `placeholders`. |
| `.bws-secrets.toml` | `follow-up-mint` on the SYSTEM row's (`221a48d5-…`) `lanes`; readers comment extended. |
| `layers.toml` | The launcher and its test in `admission`, beside `docs/operations/follow-up-scheduling.md` (the follow-up service's layer, as the rotation decomposer's launcher sits in `rotation`). `lanes/**` already covers the lane files. |
| `docs/operations/deploy.md` | The lane's paragraph and its eight cut-over steps, in "Registered lanes and the lane-token matcher", after the bump driver's. |
| `docs/operations/credentials.md` | One passage after the SDS consumer paragraph (lines ~88-92): the lane account as a future SYSTEM holder. That paragraph itself is unchanged; it changes at cut-over. |

I didn't need to touch `src/`, the Dockerfile, `lanes/lane-run`, `scripts/lane_run.py`,
`test_lane_image.py` or `test_sds_bws.py`. The image copies `scripts/run-*.sh` and `lanes/` by glob,
and `lane-run` resolves `scripts/run-<lane>.sh`, so the lane needs no image edit.

## The secret and how it is read

There's one secret, the orchestrator SYSTEM bearer `221a48d5-3f29-4898-b300-b4820140c880`. The
launcher reads it on every pass with `sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID"`. That's the
one-argument form: `bws` gets the lane's `BWS_ACCESS_TOKEN` from the environment, never as an
argument, with `--color no` and the colour variables unset (the helper). Then
`unset BWS_ACCESS_TOKEN` runs before the CLI. The key id is `orchestrator-system`. The launcher has
no Keychain read, no `gh` and no Mac branch.

`deploy.lane_secrets(manifest, "follow-up-mint")` returns the SYSTEM UUID alone. A test pins that,
and the ping-URL secret joins it when it exists.

## The call and its variables

```
"$REPO_ROOT/.venv/bin/orchestrator" mint-follow-ups --idempotency-key "$IDEMPOTENCY_KEY" --json
```

- `ORCHESTRATOR_API_URL`: `${ORCHESTRATOR_API_URL:-https://sds.alobar.net}`, as the other lane
  launchers set it.
- `ORCHESTRATOR_API_TOKEN`: the bearer.
- `ORCHESTRATOR_API_CREDENTIAL_KEY_ID`: `orchestrator-system`.
- `IDEMPOTENCY_KEY`: `follow-up-mint:$(date -u +%Y-%m-%dT%H:%M:%SZ)`. This is the Node CLI's form
  (`follow-up-mint:${new Date().toISOString()}`, `src/cli/orchestrator-cli.ts` at `c3359bf`,
  confirmed with `git show`) without milliseconds. The route accepts the key and never reads it.
- `--json`: the CLI prints its result, or its error, as one JSON line on stdout. That's structured
  output into the lane log and the `/fail` body (Code Brain rule 3).

The entry point is `orchestrator = "orchestrator.cli:app"` (`pyproject.toml` line 32), and a test
pins it. The launcher takes no arguments: anything passed is refused before anything runs, so
`lane-run follow-up-mint --dry-run` can't be mistaken for a reporting run.

## The crontab and why

`43 6 * * * lane-run follow-up-mint`, daily at 06:43 America/New_York.

- It runs after infra-drift's 03:00 audit. On a day both mint, the lane's pass finds the audit's
  units `already_minted`.
- It's outside estate landing's 02:00 to 06:00 window, which the quiet-slot rule keeps clear for
  lane redeploys (`deploy.LANDING_HOURS`; the test asserts the hour isn't in it).
- Minute 43 is free of every lane's start minute. `lane_start_minutes` ignores the hour, so the
  hourly lanes count too. The taken minutes are 0, 2, 5, 12, 15, 16, 20, 22, 25, 30, 31, 32, 35, 37,
  40, 42, 45, 47, 50, 51, 52 and 55.
- It runs daily, as Amendment 10 decides; a follow-up comes due in days.

## Failure handling

The lane is an outcome alarm (`sds_outcome_begin`/`sds_outcome_end`, no `--told`).

- If `BWS_ACCESS_TOKEN` is missing, the launcher prints one `FATAL` line and exits 1 before
  `sds_outcome_begin`. So it resolves no ping URL, reads no Keychain or BWS, and runs no CLI. A test
  asserts all three logs are empty.
- If the bearer can't be read, or comes back empty, `sds_outcome_fail` runs before the call. That's
  exit 1 and a `/fail` ping with the message. This fails closed, unlike infra-drift's step, which
  sends the empty bearer.
- If the CLI exits 1, `sds_outcome_end 1` sends a `/fail` ping with the report. Any other code (2 is
  a typer usage error, 127 a missing binary) is folded to 1 and named in the report.
- The launcher sends no alert-hub message.

## Report-don't-fix findings

1. **Two passes on one day mint nothing twice.** In `services/intake/follow_ups.py`,
   `evaluate_due` returns `SKIP_ALREADY_MINTED` for a revision that already has its follow-up unit.
   It matches by identity: `follow_up_unit_id(revision_id)` is content-addressed. The unique index
   on `(work_package_revision_id, unit_key)` and the primary key on the derived id are the backstop:
   an `IntegrityError` in the revision's savepoint becomes `already_minted` too. The passes are
   hours apart (03:00 and 06:43), and even a concurrent pair is caught by the constraint. The only
   cost is that the later pass's report lists `already_minted` skips. The route never reads the
   idempotency key, so the two keys don't interact. **I recommend lane first, then the infraops
   PR.** The overlap is harmless, and that order never leaves a day with no minting pass. The
   reverse order leaves a gap whenever the infraops PR merges before the lane's first scheduled
   pass.
2. **The `.cred-consumers.toml` edits at cut-over** (both after the infraops PR merges):
   - The SYSTEM row's `runtime-fetch` consumer for `repo = "orchestrator"` (line 216) adds
     `run-follow-up-mint.sh`. Its `ref` becomes `"scripts/run-activation-sweep.sh,
     run-bump-driver.sh, run-estate-landing.sh, run-follow-up-mint.sh, run-inert-landing.sh,
     run-rotation-decomposer.sh, run-tool-installer.sh, run-work-carrier.sh, exit_probe.py,
     deploy_orchestrator.py"`. The launchers are listed alphabetically, so it goes between
     `run-estate-landing.sh` and `run-inert-landing.sh`.
   - The whole `[[credential.consumer]]` block with `repo = "infraops-mcp-server"`,
     `ref = "scripts/drift-audit.sh mint-follow-ups (03:00)"` (lines 218-221) is removed.
   - The keeper's `note` (line 50) names "infraops-mcp-server scripts/drift-audit.sh's
     mint-follow-ups step, whose check pings success when the read fails" as a scheduled reader.
     That clause leaves too, and the lane becomes a scheduled reader with its own check. The
     handoff didn't list this line.
   - The lane's own token and ping-URL rows are added in identity-gate step 2 (deploy.md), not at
     this point.
3. **What the infraops PR must remove** (read from `c3359bf` with `git show` only). It removes the
   whole block at `scripts/drift-audit.sh` 152-180. That block covers the header comment, the
   `ORCHESTRATOR_MINT_TOKEN` export, and the subshell that sets `BWS_ACCESS_TOKEN` from
   `/usr/bin/security find-generic-password -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w` and calls
   `get_secret_by_id` with `BWS_ORCHESTRATOR_SYSTEM_SECRET_ID` (default `221a48d5-…`). It also
   covers the `ORCHESTRATOR_MINT_CREDENTIAL_KEY_ID` export and the `node … mint-follow-ups` call
   with its ok and WARN log lines. That Keychain read is the script's only use of
   `BWS_ACCESS_TOKEN_SDS`, judging from the block's own comment ("fetched with a DIFFERENT BWS
   identity from every other secret in this script"). The PR checks the whole file to confirm it.
   The Node CLI's `mint-follow-ups` command (`doMintFollowUps`, `makeMintClient`,
   `ORCHESTRATOR_MINT_*`) then has no caller in the audit. Whether to delete it is that PR's choice:
   the handoff scope here is read-only.
4. **`sds_outcome_*` handles the empty placeholder the way `sds_deadman_arm` does.** Both call
   `sds_deadman_resolve`. For an empty `SDS_DEADMAN_PING_URL_UUID`, `-n` is false, so both fall to
   the name lookup. In the container there's no `BWS_ACCESS_TOKEN_BROAD` and no
   `/usr/bin/security`, so the lookup sets the reason "no SDS_DEADMAN_PING_URL_UUID and no broad
   BWS identity" and leaves the URL empty. Every ping is then a no-op, and the pass still runs. The
   only difference is the log prefix: `[follow-up-mint] … — alerting disabled this run` against
   `[deadman] …`. A test covers the empty-placeholder pass with a stand-in broad identity, as the
   decomposer's test does.

## Tests and checks

- `make check` with `TEST_DATABASE_URL=…/orchestrator_test_hq_w_213` and
  `SECURITY_STANDARDS_DIR`: exit 0, with ruff, format and pyright clean, **15,607 passed, 1 skipped
  (15,608 collected)** in 326 s. That run was before the two review fixes. After the fixes, I ran
  `test_run_follow_up_mint.py` and `test_deploy_lanes.py` (107 passed), ruff check and format clean,
  and a second full `make check` at the fixed head: exit 0, 15,607 passed, 1 skipped, in 355 s.
- I ran the glob-based guards early: `test_deploy_lanes.py`, `test_sds_deadman.py`,
  `test_sds_bws.py`, `test_lane_image.py`, `test_lane_run.py` and `test_layer_manifest.py`. All
  passed, 314 in total. On the first run, `test_sds_bws.py` caught that the launcher must source
  `sds-bws.sh` itself. `shellcheck scripts/run-follow-up-mint.sh` is clean.
- A mutation check on the argv test: adding `--token "$ORCHESTRATOR_API_TOKEN"` to the call makes
  `test_no_secret_is_a_process_argument` fail. The launcher was restored afterward, and `git diff`
  shows it unchanged.

The launcher tests cover:

- The bearer read with the one token.
- The three variables and the key id.
- The URL default and its override.
- The idempotency-key form (regex on argv, plus the literal line).
- No secret in argv, and no Keychain or `gh`.
- The refusal without `BWS_ACCESS_TOKEN`, with nothing called.
- Refusing any argument.
- A refused bearer and an empty bearer, each failing before the call with `/start` then `/fail`.
- Exit 0, 1, 2 and 127 mapped to 0, 1, 1 and 1, with `/fail` on each non-zero exit.
- The empty-placeholder pass.
- The compose's exact service, its `add_refusals` result, and its only top-level key being
  `services`.
- Not stateful, and the `LANE_CHECKS` row.
- The crontab line, its hour, and its unique minute.
- The manifest's lane secrets.

## Self-review

I ran `/code-review` at medium. It reported five findings, and I fixed two:

- The `LANE_CHECKS` row was out of alphabetical order. It now sits before `fork-harden`.
- The argv test read only the first line of a continued command. It now checks the whole joined
  call, token by token, and the mutation check confirms it fails on a leak.

I left three unchanged, each for the reason given:

- The CLI's stderr is merged into the report. That's the decomposer's pattern, so the `/fail` body
  carries an error trace. With `--json`, the CLI's own errors are JSON on stdout.
- The review asked whether `sds-bws.sh` loads before `sds_outcome_begin`. It does: `sds-deadman.sh`
  sources it by its own directory.
- `lanes` names a grant that doesn't exist yet. That's deliberate and matches #739: the manifest
  names the future grant, and the identity proof runs only after the grant.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** the CLI runs with `--json`, so its result
  and its error are one JSON line, copied to stdout (the lane log) by `sds_outcome_end`. The
  launcher writes no file beyond the helper's temporary report, which it removes on exit.
- **Code Brain rule 4 (no silent swallow):** there's no `|| true` on the bearer read or the mint
  call. A failed or empty read fails the pass through `sds_outcome_fail`. A non-zero CLI exit fails
  it through `sds_outcome_end`, and an unexpected code is named in the report and folded to 1, not
  0.
- **Code Brain rule 5 (never log secrets):** the bearer and the lane token never reach stdout,
  stderr or argv (tested). The `FATAL` messages name the credential, not its value.
- **Infra Brain 212 (HTTPS):** the lane has no FQDN, and it calls `https://sds.alobar.net`.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history):** no token or value is
  written anywhere. The tests use fake values like `a-lane-token`, which aren't token-shaped.
- **Infra Brain 593 (no inline bootstrap token):** the compose maps `BWS_ACCESS_TOKEN` from
  `${FOLLOW_UP_MINT_BWS_ACCESS_TOKEN}` only, and the launcher holds no value.
- **Infra Brain 1032 (rotate an exposed credential):** I surfaced no credential, so nothing needs
  rotating.
- **Infra Brain rule 2 (no secrets or `.env` files):** I committed none.
- **Infra Brain rule 3 (no `:latest`):** the image is `sds-lanes:${SDS_LANES_IMAGE_TAG}`, the pinned
  tag every lane uses.
- **Infra Brain rule 4 (no Coolify scheduled task beside the in-container scheduler):** the
  schedule is the container's crontab alone, gated by `LANE_SCHEDULE_ENABLED`, which defaults to
  `false`.

## Concerns and follow-ups

- `scripts/CLAUDE.md` lists the outcome-alarm launchers by name. The list doesn't include
  `run-follow-up-mint.sh`, and that file was outside my fence. The next change that touches it
  needs to add the launcher. No test keys on the list.
- The docstring of `test_sds_bws.py::test_the_launcher_population_is_what_was_consolidated` still
  says `run-follow-up-mint.sh` was deleted and its pass lives in `drift-audit.sh`. Nothing had to
  change for a test to pass, so I left it within the fence rule. A cut-over doc change can correct
  it. `scripts/install-landing-ledger-launchd.sh` line 13 and
  `tests/architecture/test_external_content_observes_only.py` line 133 say the same thing in
  comments.
- The lane's launcher is outside `test_external_content_observes_only.py`'s population, which
  excludes `scripts/` launchers. That's the same position the old `run-follow-up-mint.sh` held.
- `docs/operations/follow-up-scheduling.md` 85-93 and ADR-0007's "Scheduled trigger" still say
  infra-drift runs the pass. Those are cut-over edits, outside this PR by the handoff.

## Cut-over steps that remain

These are in `docs/operations/deploy.md`, the follow-up-mint paragraph, steps 1-8:

1. The orchestrator serves this release, and then HQ creates `sds-lane-follow-up-mint` from the
   compose without deploying it.
2. The identity gate:
   - The new `follow-up-mint` lane account gets **two read grants**: the SYSTEM bearer
     (`221a48d5-…`, `SDS Operator`) and its own `follow-up-mint-ping-url`.
   - The VM root's keeper commands run for the lane token and the ping URL.
   - The lane's two registry rows land (token, then ping URL).
3. The token is minted and the executor writes it. Then the ping URL's row is made due, and the
   executor writes it.
4. `sds_upsert_check` creates `sds-follow-up-mint` at install.
5. A release ships whose compose names the ping-URL secret's id, with the lane out of
   `placeholders`.
6. `--add`, the `lane-run --read` proof, the schedule turned on, and the first scheduled pass. This
   is the first half of the done check.
7. The infraops-mcp-server PR removes `drift-audit.sh` 152-180, including the
   `BWS_ACCESS_TOKEN_SDS` read. This is the second half of the done check.
8. After that PR: the `.cred-consumers.toml` edits in finding 2, ADR-0007's "Scheduled trigger",
   `follow-up-scheduling.md` "Running a pass", and `credentials.md`'s SDS consumer paragraph.
