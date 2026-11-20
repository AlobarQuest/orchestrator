Outcome: done

# Report: estate landing's ping-URL registry row (W-250)

PR: #765, branch `hq/lanes-inc-5-estate-landing-ping-row`, base `origin/main` at `262cc34c`
(confirmed at dispatch: `git fetch` then `git log -1 origin/main`). Opened ready for review, not merged. The last code commit is `fdfc8551`; report-only commits follow it.

## What I built

deploy.md, estate landing's cut-over item 3 (line 1028):

- `.cred-consumers.toml` line 1411: the `estate-landing-ping-url` row, after the inert lander's
  blocks. Class and provider `healthchecks-ping-url`, `bws_uuid = "11e940d7-…"`,
  `rotated_by_sds = true`, `healthchecks_check = "sds-estate-landing"`, one `bws-secret` consumer
  (the keeper), no `rotate_requested`, no `created`. Its comment says the lane is on the broker and
  has no token row, names G-81 and the broker account's single-secret grant, and says item 5 dates
  the row. The keeper note says the executor writes it, and the broker reads it for the lane once the
  compose names its id (item 6).
- `.cred-consumers.toml` lines 1565-1570: a `runtime-fetch` consumer for `scripts/run-estate-landing.sh`
  on `lanes-read-app-private-key`, `healthchecks_check = "sds-estate-landing"`, noting that it's
  read through the lane secrets broker. The row's header comment gains one sentence on this reader.
- `.cred-consumers.toml` line 1563 (optional item 4): the inert lander's read-key note said "with
  the lane's own token". The inert compose maps no token and mounts the broker socket
  (`test_inert_landing_rows.py` asserts both), so I changed it to "through the lane secrets
  broker".
- `lanes/estate-landing/compose.yaml`: the comment said the ping-URL secret didn't exist yet. It
  now names `estate-landing-ping-url` and G-81. This is the change #755 made to the inert compose.
  The value stays `""`, so item 6 is still to come.
- `tests/rotation_worker/test_estate_landing_rows.py`, modelled on #755's
  `test_inert_landing_rows.py` (read at `b4175ea6`, where its ping row wasn't due yet): 8 tests.

I didn't touch the Mac `runtime-fetch` entries (lines 90-92, 216, 678-680), `.bws-secrets.toml`,
the broker allowlist, `test_deploy_lanes.py`, or any other repository.

## Launcher facts (handoff item 2)

- `scripts/run-estate-landing.sh` line 152: `sds_deadman_arm sds-estate-landing --finding 3 "$@"`,
  at top level and outside any conditional. It sits before the `if sds_in_lane_container` at line
  160, so every pass pings `sds-estate-landing`, bare or `--submit`. This matches deploy.md lines
  996-998.
- Lines 212-213: the in-container read is
  `sds_bws_value "$READ_APP_KEY_UUID" | env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET … sds-github-mint`.
  It's piped straight into the mint and runs every pass. In the container `sds_bws_value` reads
  through the broker's socket (header lines 56-67).

## The lane's other four secrets (handoff item 3)

#755's registry diff touched one consumer row only, the read App key's. So I added no container
reader on any of these four:

- SYSTEM bearer (`orchestrator-system-bearer`, line 191): #755 added no inert container reader.
  The row's one orchestrator `runtime-fetch` (line 216) already names both `run-estate-landing.sh`
  and `run-inert-landing.sh` without saying which host, so it covers the container read as written.
- change-manager READ bearer (`314f276d-…`) and `sds-bridge` alert sender (`b415d03b-…`): this
  repository has no row for either. `grep 'bws_uuid = "314f276d\|bws_uuid = "b415d03b'` returns no
  match; they appear only in `reach_secrets`. Their rows, if any, belong to change-manager and
  alert-hub. #755 added nothing for them.
- Estate lander's Coolify token (`coolify-estate-lander-token`, line 661): the inert lander has no
  Coolify token, so the inert pattern doesn't apply. The row's comment doesn't ask for a container
  reader either. Its one reader (line 678) says "with the narrow sds-operator identity", which is
  the Mac path. A container reader would look like this: `kind = "runtime-fetch"`,
  `ref = "scripts/run-estate-landing.sh"`, `healthchecks_check = "sds-estate-landing"`, and the
  note "read in the sds-lane-estate-landing container through the lane secrets broker on --submit
  passes only, not fatal if unreadable (re-points and restores are refused by name), as
  ESTATE_LANDING_COOLIFY_TOKEN; pings sds-estate-landing every pass" (launcher lines 241-246, inside the `--submit` case). I
  left it as a follow-up.

## Checks

1. **Reach.**
   - The keeper is a single secret (`bws_uuid` and the one `bws-secret` consumer).
     `healthchecks_ping_url.check_row` (`adapters/healthchecks_ping_url.py` lines 63-75) requires
     exactly the destinations `bws-secret` and `provider`, plus a check name. The row passes it.
   - `bws_lane_token.sharing_reach` (lines 108-122) compares lane-token rows only, so a ping row
     never enters it. `test_the_lane_has_no_token_row_and_reads_through_the_broker` asserts that
     every lane-token row still passes it, and that no row names `ESTATE_LANDING_BWS_ACCESS_TOKEN`.
   - No code pairs a ping row with a token row. I read `rotation_proposer/standing_approval.py`
     (`LANE_KINDS`, lines 38-40: a ping URL's kinds alone), `services/intake/standing_approval.py`
     (lines 42-47, provider names only), `rotation_proposer/generate.py`, `worker._read_mint_waits`
     (lines 693-715: it reads the check's URL and other keepers of its type), and
     `test_rotation_edges.py`. `grep` for an id-suffix derivation (`-ping-url"`, `-bws-token"`)
     across `src/rotation_*` found none.
   - In infraops, `cred-rotation.ts` has no reach rule, and `cred-consumers.ts` has no
     ping/healthchecks logic.
2. **Parse through the code.** `test_estate_landing_rows.py` reads the row at `main` through
   `RepoMirror` with `rotated_by_sds`, `credentials` and `provider_entries`. It runs `check_row`,
   checks the reach rule, asserts the keeper consumer's uuid equals `bws_uuid`, asserts there's no
   `rotate_requested`, no `created` and no `last_rotated`, and runs `plan_for(..., first_issue=True)`
   to get the issue plan. It also checks the reader on the read-key row. Result: 8 passed.
   - Mutation, from a copy in `/tmp`, after committing first:
     - The keeper consumer's uuid set to a placeholder fails `test_the_keeper_consumer_is_the_bws_uuid`
       (1 failed, 7 passed).
     - `rotate_requested = "2026-11-21"` added fails `test_the_row_is_not_yet_due` (1 failed, 7 passed).
     - After restoring, `/usr/bin/git diff --exit-code` was clean and the file passed 8 of 8.
     - The mutated file is TOML, not Python, so stale bytecode doesn't apply. I ran with
       `PYTHONDONTWRITEBYTECODE=1`.
3. **Due-ness.**
   - infraops `cred-rotation.ts` at `68c4af5` raises `cred.rotation-requested` only when
     `now >= rotate_requested` (around lines 301-312). With no `rotate_requested`, `requestedMs` is
     `NaN` and the comparison is false. The `healthchecks-ping-url` class is
     `maxAgeDays: Number.POSITIVE_INFINITY` (lines 97-103), so it raises no age finding.
   - `dist/security-drift/cred-rotation.js` and `src/…/cred-rotation.ts` have the same mtime, and
     `dist` was built 2026-11-20 03:00.
   - I ran `node dist/cli/security-drift-cli.js cred-findings --now 2026-11-21T12:00:00Z` from a
     scratch copy (`/tmp/w250-infraops.IAN7`). The copy excludes `.git`, symlinks `node_modules`,
     and I built nothing. `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` pointed at scratch
     directories, each with its own `cred-consumers.list` (this branch's registry only) and a `{}`
     state file (mode 600). The real checkout and `~/.config/infra-drift` were untouched, and I left
     the checkout's own uncommitted `PROJECT.md` alone.
   - Branch registry: 27 findings in total, none naming `estate-landing-ping-url`. `last_rotated`
     served it as `null`.
   - Control: the same registry with `rotate_requested = "2026-11-20"` added to the row gave 28
     findings. The one extra finding was exactly
     `{"check": "cred.rotation-requested", "id": "estate-landing-ping-url", "class": "healthchecks-ping-url", "rotate_requested": "2026-11-20", "rotated_by_sds": true}`.
4. **Rotation proposer.**
   - The VPS pass (`--submit`, daily) acts on what `cred-findings` reports due, plus rotation
     records that already exist (`rotation_proposer/cli.py` docstring, "IT WALKS ROTATION RECORDS,
     DUE OR NOT").
   - The row isn't due and has no package or record, so the next pass generates nothing for it: no
     observation, no `rotation-estate-landing-ping-url` package, no change record. Once item 5 adds
     `rotate_requested`, the pass generates revision 1, a first issue, because `last_rotated` is
     `null` (`cli._first_issue`, line 436).
   - A second reader acts at merge. `worker._other_keepers` (line 824) reads the keeper of every
     other `rotated_by_sds` row of the same provider before a ping-URL mint (`_read_mint_waits`,
     lines 712-719). `inert-landing-ping-url` is due, so its next pre-claim read reads `11e940d7-…`
     too. That keeper holds the keeper command's empty marker, which never equals a ping URL, so it
     doesn't refuse the inert unit. If the executor can't read the keeper, `StoreError` makes the
     inert unit wait, not refuse (lines 720-729). The handoff says the executor reads it. I couldn't
     confirm the grant, because the handoff rules out BWS calls.
5. **Fixtures and existing tests.**
   - `registered_lanes` (`scripts/deploy_orchestrator.py` line 1308) calls `lanes_in_registry`
     (line 1252). That function counts a lane only through a `coolify-env` service consumer whose
     key matches the lane-token variable (`_lane_of_token_consumer`, line 1243). The new row has
     none, so after the merge `registered_lanes` returns the same thirteen lanes:
     `test_the_repositorys_registry_registers_the_thirteen_moved_lanes` (line 856) is unchanged and
     passes. `estate-landing` isn't registered, so the release hold doesn't cover it. That's true
     of every broker lane with no token row, and the bump driver is in the same position.
   - `brokered` (line 1715) is derived from the compose and the broker allowlist, not the registry,
     so nothing expects a token row for the lane.
   - `test_every_lanes_ping_url_secret_is_listed_for_that_lane` (line 1381) reads the compose
     values and `.bws-secrets.toml`, not the registry. `placeholders` still holds `estate-landing`,
     and it passes.
   - `test_secrets_broker_row.py` pins the broker row to its own constant set, not to live grants,
     and passes.
   - `test_run_estate_landing.py` doesn't read the registry: `grep "cred-consumers\|REGISTRY"` over
     it returns no match.
   - The other readers are the files `grep -rln "cred-consumers\|REGISTRY_PATH" tests scripts src`
     lists: each lane's `test_*_rows.py`, `test_registry_and_compare.py`,
     `test_registry_array_strings.py`, `test_sds_cred_registries.py`, `test_deploy_orchestrator.py`,
     the membership-check and rotation-proposer tests, and the package fixtures
     (`tests/fixtures/rotation_package_issue_healthchecks-ping-url.json` and others). The fixtures
     are package bodies, not copies of the registry. I didn't read every body; all of them ran in
     `make check` and passed.
6. **Membership check.** `src/membership_check/declaration.py::hands_over` (line 177) reads a
   member's `.cred-consumers.toml` only to ask whether any row is `rotated_by_sds = true`, and only
   for members outside `REGISTRY_REPOSITORIES` (`cli.py` line 126). The orchestrator is in
   `REGISTRY_REPOSITORIES`, and it already has `true` rows, so its pass is unchanged.

## Tests and checks run

- `make check` (database `orchestrator_test_hq_w_250`): exit 0. Ruff check and format clean (1038 files), pyright 0 errors, 14847 passed, 1 skipped.
  - The first run failed on `ruff format` for the new test file. I formatted it, committed, and
    reran.
- Affected files before `make check`: `test_estate_landing_rows.py`, `test_inert_landing_rows.py`,
  `test_secrets_broker_row.py` and `test_deploy_lanes.py` gave 157 passed.
- Security scan from the repo root: 0 BLOCK, 2 WARN, 1 INFO.
  - `bws.secret-files-gitignored` (`*-migration/` glob) is about `.gitignore`.
  - `bws.manifest-matches-usage` (`aaff45ca-…`) is in `scripts/install-rotation-worker-launchd.sh`.
  - My diff touches neither file, and both are identical on `origin/main`. I couldn't establish a
    baseline: a scan of a `git archive` of `origin/main` showed 0 WARN, so it doesn't compare.
  - Re-run at HEAD `c30a96e6`, with the same result.
  - `11e940d7-…` isn't in `.bws-secrets.toml` until item 6, and the scan didn't flag it.
- `code-standards risk`: `normal`, so `vendor-review` isn't required.
- `/code-review` (low) on `origin/main...HEAD`: the skill returned `(none)` twice. I can't confirm
  from that output that a review ran, so treat the review gate as unconfirmed.

## Files changed

- `.cred-consumers.toml`
- `lanes/estate-landing/compose.yaml` (comment only)
- `tests/rotation_worker/test_estate_landing_rows.py` (new)
- `docs/superpowers/reports/lanes-inc-5-estate-landing-ping-row-report.md` (this file)

## Self-review and deviations

- One deviation beyond the handoff's list: the compose comment edit, which mirrors #755. It's a
  comment-only change and was false once G-81 created the keeper.
- I used optional item 4 (the inert lander's read-key note).

## Concerns and follow-ups

- **The broker row's declared reach is stale.** G-81 granted `lane-secrets-broker` a read on
  `11e940d7-…`. `secrets-broker-bws-token` (`reach_secrets`, line 1441) and its comment
  still name two single secrets in Rotation / Keepers, and `test_secrets_broker_row.py`'s `SECRETS`
  pins those two. The row is `rotated_by_sds = false`, so no executor acts on it. I left it alone
  because the handoff names one block, so it needs a follow-up change: add the id to the row,
  its comment, and the test's set. Deploy.md's item 4 is where that grant is recorded.
- **Two other inert-lander notes say "with its own token".** These are the
  `inert-landing-ping-url` keeper's note (line 1399) and the token row's account comment.
  They predate the broker, and the handoff allowed only the read-key note.
- **Container reader on `coolify-estate-lander-token`:** a follow-up, with the shape given earlier.
- **The inert ping-URL unit now reads the new keeper** before its mint (check 4). If the
  executor's account lacks the read on `11e940d7-…`, the inert unit waits until it has one.
- **The release hold doesn't cover broker lanes with no token row** (check 5). That's the existing
  design for the bump driver too, and this change doesn't alter it.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The diff adds a TOML
  row, a comment, and a test that logs nothing.
- **Code Brain rule 4 (never swallow an exception):** the new test has no `try` or `except`.
  Failures surface as assertion errors.
- **Code Brain rule 5 (never log secrets):** nothing added prints or holds a token, ping URL, ping
  UUID, bearer, key or password. The row and test name BWS secret ids only, and a BWS id isn't the
  ping URL's UUID. The `cred-findings` output I quote names ids and classes only.
- **Infra Brain 591, 592, 593:** the diff has no BWS token and no inline bootstrap token. The only
  test value, the mutation placeholder, was a UUID-shaped placeholder that doesn't match the token
  shape, and it never reached a commit. The scan reports 0 BLOCK.
- **Infra Brain 2890 (never commit a ping URL):** no `https://healthcheck.alobar.net/ping/` URL
  appears in the diff.
- **Infra Brain 1032 (cred.exposure-rotate):** the row declares no `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. There's no domain change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, the test, the
  report or a log. The rows name ids only, and the infraops run used an empty scratch state.
