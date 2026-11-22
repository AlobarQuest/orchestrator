Outcome: done

# Build report: change-window's ping-URL registry row, due for its first issue, and the digest sender's row

Worker W-267. Handoff: `lanes-inc-8-change-window-ping-row` (ADR-0059 increment 8; deploy.md's
change-window cut-over item 4, plus #784 follow-up 1). Base `origin/main` at `319cdede`, the base
the handoff named. I confirmed each repository fact there. The keepers' existence, their ids, the
G-88 grant and G-88's read of the Healthchecks listing are the handoff's word: I made no BWS,
Coolify, VM or Healthchecks call.

PR: [#785](https://github.com/AlobarQuest/orchestrator/pull/785), ready for review, not merged.
The code is complete at `9c108ee8`, and this report's commit sits on top of it.

## What I built

- `.cred-consumers.toml:1874-1899`: the block `change-window-ping-url`, after the change-window
  rows.
  - Fields: class and provider `healthchecks-ping-url`,
    `bws_uuid = "1d6a0ab4-e164-4fe2-8ccb-b50a00e255c3"`, `rotated_by_sds = true`,
    `rotate_requested = "2026-11-22"` and `healthchecks_check = "Change Manager OPs Check"`. The
    keeper is the row's one `bws-secret` consumer.
  - There's no `created`, for the reason the landing ledger's block gives.
  - The comment names G-88 and the VM root's `create-keeper`. It says the lane keeps the Mac
    window's check (no new check) and that G-88's read found exactly one check of that name.
  - It says the broker reads the keeper for the lane once the compose names its id (item 5),
    through the single-secret grant Devon gave at G-88, and that item 6's `lane-run --read` is
    the proof. It doesn't say the broker has proven a read. The consumer `note` says the same.
- `.cred-consumers.toml:1847-1872`: the block `alert-hub-report-change-window-sender`.
  - Fields: class `alert-hub-sender-token`, `provider = "alert-hub"`,
    `provider_identity = "alert-hub sender report-change-window (source report-change-window)"`,
    `bws_uuid = "71616bf5-14ba-4888-b3f8-b50a00e13ed2"`, `created = "2026-11-22"`,
    `disposition = "reissue"`, and no `rotated_by_sds`.
  - Consumers: the keeper (`bws-secret`) and `scripts/run-change-window.sh` (`runtime-fetch`,
    `healthchecks_check = "Change Manager OPs Check"`), in the form the Coolify-token and
    Anthropic-key rows use. The reader note says the launcher reads it on `--submit` passes only
    and never exports it (`scripts/run-change-window.sh:72`, `:136-139`).
  - No `fingerprint_sha256_8`: no session has read the value (see check 2's field audit).
- `tests/rotation_worker/test_change_window_rows.py` (new): seven tests, modelled on
  `test_follow_up_mint_rows.py`.
- `docs/operations/deploy.md`: two statements, nothing else.
  - `:1284-1285`: "(slug `operations-check`)" becomes "(its slug in the read-only listing is
    empty, as HQ gate G-88 read it)". I rewrapped the paragraph to 100 columns; its other words
    are unchanged.
  - `:1342-1343`: "The digest sender token has no registry row yet." becomes "The digest sender
    token's row is `alert-hub-report-change-window-sender`."

I changed no compose, allowlist, `.bws-secrets.toml`, `reach_secrets`, `placeholders` or ADR.
`1d6a0ab4-…` is in neither the allowlist nor the broker row's `reach_secrets`.

## Checks

### 1. A new row may land already due

- `src/rotation_worker/registry.py` has no rule on `rotate_requested`. It reads rows
  (`_entries`, `:141-155`), refuses a duplicate id (`credentials`, `:124-133`), and asks
  `rotated_by_sds is True` (`:56-57`). The one reader of the due date is
  `rotation_proposer/findings.py`, which maps infraops' `cred.rotation-requested` to the
  trigger `requested` (`:54`).
- The ping-URL adapter's `check_row` (`adapters/healthchecks_ping_url.py:64-76`) asks for exactly
  the destinations `bws-secret` and `provider`, plus a trimmed check name (`registry.check_name`,
  `:79-84`). A name with spaces passes, because the only rule is non-empty and trimmed.
- The reach rule (`bws_lane_token.sharing_reach`) compares lane-token rows only, so a lane with no
  token row passes it. `test_the_lane_has_no_token_row_and_reads_through_the_broker` runs it over
  every lane-token row beside these.
- In infraops (`src/security-drift/cred-rotation.ts` at `68c4af5`), the date is checked only as a
  quoted real date (`invalidRotateRequestedFinding`, `:220-228`). The due rule (`:301-312`) is a
  requested date with no recorded rotation on or after it. Neither reads another row or an
  earlier state.
- So the row may land already due, and it needs no token row.

### 2. Both rows parsed through the code

`test_change_window_rows.py` commits this repository's file through the `Origins` fixture and
reads it with `credentials(RepoMirror)` (the sender row isn't in `rotated_by_sds(mirror)`). The
tests cover the following:

- The ping row is handed to the SDS, and `bws_uuid` is its keeper.
- `check_row` passes, `healthchecks_check == "Change Manager OPs Check"`, and the launcher has
  `CHECK_NAME="Change Manager OPs Check"` (`:59`) and `sds_outcome_begin "$CHECK_NAME"
  change-window` (`:100`).
- For both rows, the keeper consumer's uuid equals `bws_uuid`.
- The ping row's `rotate_requested == "2026-11-22"`, and it has no `created` or `last_rotated`.
- `plan_for(…, first_issue=True).issue`, and `render(…, first_issue=True)` gives package
  `rotation-change-window-ping-url` whose acceptance names the check. This proves the name with
  spaces and capitals survives the proposer's renderer.
- The sender row isn't handed over, has no `rotate_requested`, is class
  `alert-hub-sender-token`, names source `report-change-window`, and its reader is the launcher,
  which holds its keeper id (`:72`).
- No lane-token row names `CHANGE_WINDOW_BWS_ACCESS_TOKEN`, the reach rule passes for every
  lane-token row, and the compose maps no token and mounts the broker socket.

I dropped follow-up minting's manifest-and-allowlist test on purpose: `1d6a0ab4-…` joins the
allowlist and the compose in later items, so that test can't pass yet.

Result: 7 passed.

The mutation: from a backup copy, I set the ping row's `healthchecks_check` to
`"operations-check"` (the slug deploy.md used to state). Result: 2 failed
(`test_the_ping_url_row_names_only_its_keeper_and_the_launcher_s_check`,
`test_a_first_issue_renders_an_issue_package_reading_the_check`), 5 passed. I restored the file
with `cp`, confirmed it with `cmp`, and cleared `__pycache__`. After that: 7 passed.

The sender row's fields. Nothing requires `fingerprint_sha256_8`, `created` or `disposition`:

- infraops' parser (`src/security-drift/cred-consumers.ts`) declares all three optional
  (`:47`, `:57`, `:62`) and refuses a row only for a missing string `id` or `class` or a
  non-bool `rotated_by_sds` (`:206-212`). The fingerprint is read only in an exposure finding's
  text, with `?` when absent (`cred-rotation.ts:277`).
- `rotation_worker.registry` reads none of the three.
- `src/membership_check/` reads only `rotated_by_sds` (`declaration.py:177-200`).
- I set `created` and `disposition` anyway, as the other two G-88 change-window rows do. `created`
  is the age anchor (`rotationAnchor`, `cred-rotation.ts:210-212`). Without it, a row of a class
  with a 365-day limit (`alert-hub-sender-token`, `:53`) never ages. The row isn't
  `rotated_by_sds`, so the anchor doesn't affect any first-issue path.

### 3. Due-ness, from infraops' `cred-findings`

- Setup:
  - The copy: an `rsync` of `~/Projects/infraops-mcp-server` (no `.git`, HEAD `68c4af5`) into
    `/tmp/w267-infraops.lbKi`. I ran `node dist/cli/security-drift-cli.js cred-findings --now
    2026-11-22T20:00:00Z` from it.
  - Each run had its own `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR`. Each held a
    one-line `cred-consumers.list` and an empty `{}` `cred-rotation-state.json` (mode `0600`).
  - Treated used this branch's file, and control used `origin/main`'s.
  - I didn't edit or build the checkout, and I didn't touch `~/.config/infra-drift`.
- Treated: exit 0, 31 findings. Control: exit 0, 30 findings.
- As `(check, id)` pairs, treated minus control is
  `{('cred.rotation-requested', 'change-window-ping-url')}`, and control minus treated is empty.
  The finding:
  `{'check': 'cred.rotation-requested', 'id': 'change-window-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-22', 'rotated_by_sds': True}`.
- The sender row adds no finding: it has no `rotate_requested`, no exposure, and its anchor
  (`created = "2026-11-22"`) is 0 days old against a 365-day limit.
- Treated's `last_rotated` holds `change-window-ping-url` with the value `null`. It doesn't hold
  the sender row, because infraops serves `last_rotated` for handed-over rows only. The scratch
  state is empty, so this shows what the rows' own fields produce, not the live state.

### 4. The executor's check lookup

- The VM instance builds the listing client in `rotation_worker/cli.py:137-141`
  (`open_ping_urls`), with the Management key's BWS id from `config.toml`'s
  `healthchecks_management_key_id` (`docs/operations/rotation-executor.md:444`).
- The adapter's `read_mint` (`adapters/healthchecks_ping_url.py:115-127`) calls
  `PingUrls.ping_url(self._check)`, where the check is the row's `healthchecks_check`
  (`rotation_proposer/generate.py:262-271` gives the same name to the package).
- `readers.PingUrls._read` (`src/rotation_worker/readers.py:283-318`) makes one `GET
  /api/v3/checks/` with the Management key and keeps the checks whose `name` equals the given
  name exactly (`:309`). It requires exactly one (`:310-311`) with a non-empty, trimmed
  `ping_url` (`:312-317`). It never reads `slug`.
- So the lookup is by name only. A name with spaces resolves to exactly one check when the
  Management key's listing holds one check of that name, whatever its slug. G-88 read exactly one
  in the self-hosted listing.
- No other code assumes a slug-shaped name: `LANE_CHECKS` already maps `change-window` to
  `"Change Manager OPs Check"` (`scripts/deploy_orchestrator.py:1000`), and the proposer passes the
  name into acceptance text only (`generate.py:178-179`, `:387`).
- Not verified (no Healthchecks call): that the Management key's project is the one holding
  `Change Manager OPs Check`. G-88's read was of the read-only listing. The runbook describes one
  self-hosted Healthchecks project whose checks the VM's and the Mac's readers share
  (`rotation-executor.md:888-890`), which points to the same project, but no read has shown it. If the check sits in
  another project, the read raises `ReadError` ("0 Healthchecks checks are named …") and the unit
  is refused before the claim (`worker.py:720`), never stored wrong.

### 5. The rotation proposer

- Its next `--submit` pass reads `cred-findings` and parses the row as due with trigger
  `requested` (`findings.py:54`).
- `_generate` (`cli.py:444`) calls `render` with
  `first_issue=_first_issue(last_rotated, "change-window-ping-url")` (`:462`).
- `_first_issue` (`:436-441`) returns `True` when `last_rotated` holds the id with a `null` value,
  which check 3's treated run served.
- `plan_for` (`generate.py:131-162`) then returns the issue plan (`:149-150`). The type has only an
  issue plan, so `None` returns it too (`:151-156`).
- The pass generates `rotation-change-window-ping-url` revision 1: one machine unit, `store`, that
  writes the URL read from the Management key's listing into the keeper (ADR-0055 amendment 23).
- The executor's pre-mint read:
  - `_other_keepers` (`worker.py:824-834`) reads the keeper of every other `rotated_by_sds` row of
    provider `healthchecks-ping-url` through `setup.store.read`. `BwsStore.locate`
    (`secret_store.py:100-101`) returns the `bws-secret` uuid.
  - From this merge on, every other ping-URL unit's pre-mint read includes `1d6a0ab4-…`, and this
    row's unit reads every other ping-URL keeper.
  - The root has "Can read, write" on `Rotation / Keepers`, a project grant
    (`docs/operations/rotation-executor.md:448`, `:478-479`). The keeper is in
    `Rotation / Keepers`, so the root reads it through that grant.
  - If that read fails, `_read_mint_waits` turns the error into `ReadUnavailable` (`:716`), so the
    unit waits and isn't refused (`:720`).
- This is from code and docs only. I made no BWS or Healthchecks call.

### 6. Fixtures and existing tests

- `registered_lanes` (`scripts/deploy_orchestrator.py:1308-1310`) is `lanes_in_registry`
  (`:1252-1276`). It skips the broker's row and counts consumers that `_lane_of_token_consumer`
  (`:1243-1249`) matches: the lane-token kind, the lane-token resource type and a
  `*_BWS_ACCESS_TOKEN` key. Neither new row has such a consumer.
- I ran `lanes_in_registry` on this branch's file and on `origin/main`'s. The sets are equal, 13
  lanes: `bump-proposer, change-proposer, deploy-watcher, execution-watch, inert-landing,
  landing-ledger, membership-check, pin-watcher, revision-watcher, rotation-decomposer,
  rotation-proposer, unit-verifier, work-carrier`. So `registered_lanes` returns that set once
  this merges, and `change-window` isn't in it.
- `test_every_lanes_ping_url_secret_is_listed_for_that_lane`
  (`tests/scripts/test_deploy_lanes.py:1381-1400`) reads the composes, not the registry.
  `change-window` stays in `placeholders` (`:1388-1390`) until item 5 writes the compose's id. I
  didn't edit it.
- I grepped `tests/` for `.cred-consumers` and `healthchecks-ping-url`, and read the registry
  reads in `tests/scripts/test_sds_cred_registries.py`, `test_secrets_broker_row.py`,
  `tests/architecture/test_rotation_edges.py`, `test_membership_edges.py` and
  `tests/rotation_proposer/test_pass.py`. They use fixtures or pin the broker row's
  `reach_secrets` (`test_secrets_broker_row.py:40-46`), which I didn't change. None pins the set of
  this file's ids or asserts on every ping-URL row. The full `make check` passing confirms it.
- `layers.toml` covers the new test (`tests/rotation_worker/**`, `:679`) and this report
  (`docs/superpowers/**`, `:758`).

### 7. The membership check

- `src/membership_check/` reads registries in two ways: `declaration.hands_over`
  (`declaration.py:177-200`), which asks whether any row has `rotated_by_sds = true`, and the
  mirror read in `cli.py:115-122`, which lists the repositories holding a handed-over row.
- This repository already holds handed-over rows, so both answers are unchanged: the
  orchestrator still needs the `credential_rotation` grant it has, and its pass doesn't change.

### Gates

- `make check` with `orchestrator_test_hq_w_267`: exit 0. ruff and pyright clean; pytest 14890 passed, 1 skipped, 11 warnings (5 min 41 s).
- CI on `dc7c88d6` (run `45036661888`): `Quality` passed (14865 passed, 26 skipped; 14,891
  collected, the same total as the local run, so the new file's seven tests ran),
  `Rollout transcription currency` and `Runner consumer compatibility` passed.
- The security scan from the repo root: no BLOCK. Two WARNs (`bws.secret-files-gitignored`,
  `bws.manifest-matches-usage`) and one INFO (`bws.least-privilege-scope`). A clean archive of
  `origin/main` gives the same three, so none is this change's.
- `code-standards risk`: `normal`, so `vendor-review` doesn't apply.
- `/code-review` (low) on `origin/main...HEAD`: the forked review returned an empty result,
  `(none)`, with no finding listed. I can't tell an explicit no-findings verdict from an empty
  return.

## Files changed

- `.cred-consumers.toml` (two new blocks)
- `docs/operations/deploy.md` (two statements)
- `tests/rotation_worker/test_change_window_rows.py` (new)
- `docs/superpowers/reports/lanes-inc-8-change-window-ping-row-report.md` (this report)

## Self-review

- Each id, uuid and date in the rows matches the handoff and the launcher's defaults
  (`scripts/run-change-window.sh:63-72`). The tests check the uuids against both.
- The ping row's comment and note avoid claiming a proven broker read.
- `rotate_requested` is today's date, so the row is due on the proposer's next daily pass, after
  merge.

## Deviations

- I placed both blocks after the Anthropic-key row, the end of the file, so the change-window rows
  stay together. The sender block comes first, matching the other two non-rotated rows.
- The deploy.md rewrap: the first statement's edit made its line too long, so I rewrapped the
  rest of that paragraph (`:1284-1288`). Its words are unchanged, but the diff touches three
  lines beyond the statement.
- I set `created` and `disposition` on the sender row. Neither is required (check 2), but the
  in-file precedent sets both, and `created` lets the row age.

## Concerns

- Not verified: that the Management key's project lists `Change Manager OPs Check` (check 4). A
  wrong project refuses the unit before the claim. Nothing is written.
- Not verified by a read: that keeper `1d6a0ab4-…` holds the inbox's empty marker.
  `create-keeper` stores `Value(EMPTY)` and refuses to overwrite an existing secret
  (`rotation_worker/cli.py:378-402`), so a keeper it made holds the marker until something writes
  it. The executor's `checks._check_issue_fact` (`checks.py:178`) reads it before the claim and
  refuses an issue on anything else.
- The Mac job still pings `Change Manager OPs Check` until cut-over item 8. The executor reads the
  check's URL and pings nothing, so the issue doesn't disturb the Mac job's alarm.
- No Code Brain or Infra Brain concern falls outside the handoff's Standards section.

## Follow-ups left

- Item 5: the compose's ping-URL id and removing `change-window` from `placeholders`.
- The broker allowlist entry for `1d6a0ab4-…`, and the broker row's `reach_secrets` and
  `test_secrets_broker_row.py`'s `SECRETS` after item 6 proves the reads.
- The executor's write of the keeper (HQ, through `sds-hq chain`).
- ADR-0059 text.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout, not files): no logging change. The diff
  adds registry rows, a test and doc text.
- **Code Brain rule 4** (never swallow an exception silently): the new test catches nothing. Every
  failure raises through pytest.
- **Code Brain rule 5** (never log secrets, credentials, auth headers, tokens): nothing added
  prints a token, ping URL, ping-URL UUID, bearer, key or password. The rows and tests name BWS
  secret ids only, which aren't the ping URL's UUID.
- **Infra Brain 591, 592, 593**: no BWS token in any tracked file or in history, and no bootstrap
  token inline. The scan has no BLOCK. The test holds no placeholder token values.
- **Infra Brain 2890** (never commit a ping URL): no `https://healthcheck.alobar.net/ping/` URL is
  in the diff.
- **Infra Brain 1032** (`cred.exposure-rotate`): neither row declares a `[[credential.exposure]]`,
  and check 3 shows no exposure finding.
- **Infra Brain 212** (HTTPS for public FQDNs): not applicable. There's no domain change.
- **Infra Brain lesson 450** (rotation landmines): no secret value enters the registry, a plan, a
  test or a log. The rows name ids only.
