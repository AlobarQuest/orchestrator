Outcome: done

# Report: the inert lander's registry rows (W-243)

PR: AlobarQuest/orchestrator #755, branch `hq/lanes-inc-5-inert-landing-rows`, base
`origin/main` `a2af3b5c` (unchanged at dispatch). Code commits `f4866fe1` and `6a2dae8b`; this
report is the commit after them. The PR is open and ready, not merged.

## What I built

This is the first of the lane's two rows changes (deploy.md, the inert lander's cut-over item 3),
modelled on #746 (`3fffa6e5`).

- `.cred-consumers.toml` gets two blocks after the rotation proposer's:
  - `inert-landing-bws-token` (line 1359): `bws-lane-token`, keeper `2e6d746e-…`,
    `rotated_by_sds = true`, `rotate_requested = "2026-11-19"`, five-secret `reach_secrets`, and
    `reach_projects = []`. Its consumers are the keeper (`bws-secret`) and `coolify-env` with
    `instance = "prod"`, `resource_type = "service"`, uuid `w1ujl75mr0sqdrtur294ldrk`, key
    `INERT_LANDING_BWS_ACCESS_TOKEN` (the compose's line 22), and `redeploy = false`.
  - `inert-landing-ping-url` (line 1388): `healthchecks-ping-url`, keeper `35557473-…`,
    `rotated_by_sds = true`, check `sds-inert-landing`, and no `rotate_requested`. It carries the
    same fields as #746's ping-URL rows.
  - No row carries `created`. The block comment names G-78 for both keepers and the account. It
    says the account's grants are a later act (item 4) and that the account has no other token.
    I didn't copy #746's "grants came at G-75" wording, because no grant exists yet.
- Item 3: the read App key's row gains a `runtime-fetch` for `scripts/run-inert-landing.sh`
  (line 1504), check `sds-inert-landing`. The note says the container reads the key on every
  pass, bare or `--submit`, and pings every pass. The App-keys section header now says this reader
  lands ahead of the account's grant, as the work carrier's did.
- `lanes/inert-landing/compose.yaml`: the comment said `SDS_DEADMAN_PING_URL_UUID` is empty
  "until the lane's ping-URL secret exists". That was false after G-78, so it now names the keeper,
  with #746's wording. The value is unchanged (`""`), and the lane stays in `placeholders`.
- Tests: the new `tests/rotation_worker/test_inert_landing_rows.py` has 11 test functions and 13
  cases. In `tests/scripts/test_deploy_lanes.py`, the registry test is renamed to
  `..._registers_the_thirteen_moved_lanes` and adds `inert-landing`.

From the launcher, at base:

- The container reads the read key on every pass: `READ_APP_KEY_UUID` is set at line 101, and line
  194 reads it and pipes it into `sds-github-mint`. The read sits inside the container block
  (lines 148 to 229), before the `--submit` case at line 213.
- It arms the dead-man switch at line 134 (`sds_deadman_arm sds-inert-landing --finding 3 "$@"`),
  before the container branch and with no `--submit` condition, so every pass pings. deploy.md
  says so too ("this lane arms the dead-man switch, so a bare run pings its check").

No Mac `runtime-fetch` entry changed (lines 111 and 216).

### Item 4: the lane's other three secrets

| Secret | Its row | Earlier moved lanes' container readers | This PR |
|---|---|---|---|
| SYSTEM bearer `221a48d5-…` | `orchestrator-system-bearer`, this repository, line 191 | None. Its one orchestrator `runtime-fetch` (line 216) is a combined Mac `ref` listing `run-work-carrier.sh` and `run-rotation-decomposer.sh`. Those two lanes read this bearer in their containers, and neither got a separate container entry. #729 (`e7744356`) didn't touch the line. | No change |
| change-manager READ `314f276d-…` | `change-manager-read-bearer`, in **change-manager**'s registry, not this repository | Yes. At change-manager `main`, the row's entries for `run-bump-proposer.sh`, `run-work-carrier.sh`, `run-membership-check.sh` and `run-rotation-decomposer.sh` each describe the `sds-lane-…` container and the lane's token. Each replaced its Mac entry, because a destination's identity is `(repo, ref)`. `run-inert-landing.sh`'s entry still describes the Mac job. | No change. It is another repository's file, and the handoff forbids changing it. See follow-up 1. |
| `sds-bridge` sender `b415d03b-…` | None. A GitHub code search across AlobarQuest finds no `.cred-consumers.toml` naming it, only manifests, launchers and HQ files. | None possible | No change |

## Checks

1. **Reach derived.** `.bws-secrets.toml`'s entries whose `lanes` include `inert-landing` are four:
   - `221a48d5-…` (`ORCHESTRATOR_M2M_CREDENTIALS`, line 28)
   - `314f276d-…` (`change-manager/M2M_TOKEN_READ`, line 202)
   - `b415d03b-…` (`ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE`, line 307)
   - `314a966c-…` (`lanes-read-app-private-key`, line 369)

   With the ping-URL keeper `35557473-…`, that makes five. This matches HQ's set and the launcher's
   lines 90, 95, 98 and 101. `35557473-…` isn't in `.bws-secrets.toml` yet: item 6 adds it. The test
   follows #746's original form and requires `reach == manifest | {ping keeper}`, with the ping
   keeper absent from the manifest.
2. **Reach rule.**
   - The rule is `bws_lane_token.sharing_reach` (`src/rotation_worker/adapters/bws_lane_token.py`,
     line 108). It compares whole sets (`declared_reach(other) == mine`, line 118) and only over
     the `bws-lane-token` provider's rows (`registry.provider_entries`, line 119).
   - The inbox's other-keeper check (`worker._other_keepers`, line 824) filters on the same
     provider and on `rotated_by_sds`.
   - The registry has no other row for the `inert-landing` account. Before this change,
     `inert-landing` appears only in the sds-operator token row's Mac entry (line 111) and the
     SYSTEM bearer's combined ref (line 216). Neither is a token row of that account.
   - The nearest lane-token reach is the rotation decomposer's, `{221a48d5, 314f276d, b415d03b,
     936bbbf2}`. It shares three secrets with this row and differs from it.
   - infraops `cred-rotation.ts` has no reach rule: `grep reach` returns no match.
   - `test_no_other_lane_token_row_declares_the_same_reach` runs the rule over every lane-token
     row at `main` through `RepoMirror`. The new row passes, and a twin given any other row's whole
     reach is caught as that row.
3. **Rows parsed through the code.**
   - The rows go through `rotated_by_sds(mirror)`, then `bws_lane_token.check_row` and
     `lane_variable` (service uuid and key, matched to the compose's mapping), then
     `healthchecks_ping_url.check_row` and the check name, held to the launcher's
     `sds_deadman_arm` line.
   - The test asserts `redeploy is False` from the raw row, and that each keeper consumer's uuid
     equals its `bws_uuid`.
   - Only the token row is due: the token row has `rotate_requested == "2026-11-19"`, and the ping
     URL's row has no `rotate_requested`.
   - Neither row has `created` or `last_rotated`, and `plan_for(..., first_issue=True).issue` holds
     for both.
   - The read key's row has the launcher's reader with `sds-inert-landing`.
   - **Mutation check:** after committing, I copied the registry to `/tmp`, set the env consumer's
     `redeploy = true`, and added `rotate_requested = "2026-11-20"` to the ping-URL row. 2 of 13
     failed: `test_the_token_row_is_one_the_lane_token_adapter_acts_on` and
     `test_only_the_token_row_is_due`. I restored from the copy, and
     `/usr/bin/git diff --exit-code` was clean. The file then passed 13 of 13. I cleared
     `__pycache__` before the mutated run.
4. **Due-ness.**
   - In infraops `src/security-drift/cred-rotation.ts` at `fdf3e91`, lines 279 to 292 raise
     `cred.rotation-requested` when `now >= rotate_requested` and the row was never rotated, or was
     rotated earlier. The ping-URL row has no `rotate_requested`, so `requestedMs` is `NaN` and the
     comparison is false. Its class is `maxAgeDays: Number.POSITIVE_INFINITY` (line 98), so it
     raises no age finding either.
   - I ran `node dist/cli/security-drift-cli.js cred-findings` from a scratch copy
     (`/tmp/w243-infraops.1kwm`). `.git` was excluded, `node_modules` was a symlink, and nothing
     was built. Its own `cred-consumers.list` named only this branch's registry, and its state file
     was `{}` (mode 600). The real checkout and `~/.config/infra-drift` were untouched; the
     checkout's own uncommitted `PROJECT.md` was left alone.
   - With `--now 2026-11-20T12:00:00Z`, it found exactly one finding for the new ids:
     `{"check": "cred.rotation-requested", "id": "inert-landing-bws-token", "class": "bws-lane-token", "rotate_requested": "2026-11-19", "rotated_by_sds": true}`.
     There were 26 findings in total; the others come from the empty scratch state.
   - With `--now 2026-11-18T12:00:00Z`, it found none for either id (23 in total).
   - `last_rotated` served both ids as `null`.
5. **What the rotation proposer generates.**
   - The VPS container pass (06:31 New York, `--submit`) clones the four registries anonymously at
     `main` each pass. It writes its own `cred-consumers.list` and runs `cred-findings` against
     `cred-rotation-state.json` on the lane's volume (`run-rotation-proposer.sh`, lines 153 to 230).
     The merge is live on the next pass, with no activation step.
   - That pass sees `inert-landing-bws-token` due. `last_rotated` is `null` for it
     (`cli._first_issue`, line 436), and no package exists. So it generates revision 1 of
     `rotation-inert-landing-bws-token` (`generate.py`, line 295), a first-issue plan for
     `bws-lane-token`, and writes it through intent-packages' `create-rotation`.
   - The pass proposes nothing for `inert-landing-ping-url`, because no finding is due.
   - It acts on the row because orchestrator is in `REGISTRY_REPOSITORIES` and already holds the
     `credential_rotation` grant that its other `rotated_by_sds` rows need.
   - The volume state doesn't change anything. The state has no entry for either id, which serves
     `null`, the same as a never-rotated row. The proposer's one write to that state is
     `record-rotation`, which it makes only for a `resolved` record (`cli.py` docstring, lines 38 to
     48), so this pass writes nothing there for the new rows.
   - After that, revision 1 is the standing approval's case (ADR-0055 amendment 22): a lane
     credential's revision 1, approved by policy and checked by the carrier.
6. **Fixtures and existing tests.**
   - `lanes_in_registry` (`deploy_orchestrator.py`, line 1202) matches the `coolify-env` service
     consumer keyed `INERT_LANDING_BWS_ACCESS_TOKEN` (`_lane_of_token_consumer`, line 1193).
     `inert-landing` is in `LANE_CHECKS` (line 961), so it isn't refused as an unknown lane.
     `test_the_repositorys_registry_registers_the_thirteen_moved_lanes` now lists it.
   - `test_every_lanes_ping_url_secret_is_listed_for_that_lane` reads the composes'
     `SDS_DEADMAN_PING_URL_UUID`. The value is still `""`, so `inert-landing` stays in
     `placeholders`, and I didn't change that test.
   - The other lanes' rows tests list fixed `OTHER_LANE_TOKENS`, and #746's test computes its
     lane-token population at run time, so the new row doesn't change what any of them asserts.
     The full `make check` runs them all.
   - Once this merges, `registered_lanes` (line 1256, reading `origin/main`) returns the thirteen
     lanes. `require_lanes_current` (line 1266) and `lanes_to_follow` (line 1295) act only on a
     lane with a finished `--add` run, and `inert-landing` has none. So nothing treats it as
     registered, holding a release, before its token is issued and its `--add` passes (item 7).
7. **Membership check.** `src/membership_check/cli.py` line 119 reads
   `of_a_type(rotated_by_sds(...))` only to derive the set of repositories whose registry hands the
   SDS a credential (`_flagged_repositories`). orchestrator already has such rows, so the set, and
   the pass, don't change.

## Tests and checks run

- `make check` with `TEST_DATABASE_URL` set to `orchestrator_test_hq_w_243`,
  `SECURITY_STANDARDS_DIR=tests/fixtures/security-standards` and `PGTZ=UTC` passed (rc 0): ruff reported "All checks passed!" and 1,032 files already formatted, pyright reported 0 errors, and the tests ran 14,708 passed, 1 skipped, 11 warnings.
  The first run failed on one E501 in the new test's docstring. I fixed it in its own commit and
  re-ran the gate.
- The targeted run (the new test, `test_deploy_lanes.py`, `test_proposer_lane_rows.py`,
  `test_work_carrier_rows.py`, `test_rotation_decomposer_rows.py` and `test_run_inert_landing.py`)
  passed 207.
- The security scan from the repo root found 0 BLOCK findings, 2 WARN and 1 INFO. Both WARNs
  predate this diff: `bws.secret-files-gitignored` (`*-migration/`) and
  `bws.manifest-matches-usage` (`aaff45ca-…`). The INFO is the judgment prompt on machine-account
  scope.
- `/code-review` (low) on `origin/main...HEAD` returned no findings.

## Files changed

- `.cred-consumers.toml`
- `lanes/inert-landing/compose.yaml` (comment only)
- `tests/rotation_worker/test_inert_landing_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-5-inert-landing-rows-report.md` (this report)

## Self-review and deviations

There are no deviations from the handoff. Item 4 adds no reader, for the reasons in the table.

## Concerns and follow-ups

1. **change-manager's READ bearer row** still describes `run-inert-landing.sh` as the Mac job
   (launchd at :35, the broad Keychain account). The earlier moved lanes' entries there name the
   container and the lane's token. When the lane cuts over, someone must update that entry in
   change-manager. This PR can't: the handoff limits changes to orchestrator.
2. **deploy.md**, in the inert lander's section, says "Its ping-URL secret joins them when it
   exists". The secret exists since G-78, but the sentence still holds for the manifest and the
   compose, which item 6 changes. I left it alone; item 6's change can reword it.
3. **Two mints waiting at once.** If another lane-token issue is still waiting on a person's mint
   when this one starts, `inbox.bind` binds neither (`other_mints`, `inbox.py`, line 120), as #746's
   report described. That isn't a stop.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The diff adds
  registry rows, a comment and tests.
- **Code Brain rule 4 (never swallow an exception):** the new test has no `try` or `except`. Every
  read failure raises and fails the test.
- **Code Brain rule 5 (never log secrets):** nothing added prints a value. The rows and tests name
  BWS secret ids, a Coolify service uuid and a check name only. No ping URL, or the UUID inside
  one, appears; `35557473-…` is the keeper's BWS id.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files or history, no inline bootstrap
  token):** no token appears. The scan found 0 BLOCK findings.
- **Infra Brain 2890 (never commit a ping URL):** no `healthcheck.alobar.net/ping/` URL is in the
  diff.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable, because there's no domain change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, the tests,
  the report or a log. The rows name ids only, and the infraops run used an empty scratch state
  and printed findings with ids only.
