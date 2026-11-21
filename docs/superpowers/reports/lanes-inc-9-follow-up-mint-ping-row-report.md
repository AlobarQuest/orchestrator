Outcome: done

# Build report: follow-up minting's ping-URL registry row, due for its first issue

Worker W-258. Handoff: `lanes-inc-9-follow-up-mint-ping-row` (ADR-0059 increment 9, Amendment 10;
deploy.md's follow-up-mint cut-over items 4 and 6). Base `origin/main` at `341555f5`, the base the
handoff named. I confirmed each repository fact there. The keeper's existence, its id and the G-87
check are the handoff's word: I made no BWS, Coolify, VM or Healthchecks call.

PR: [#773](https://github.com/AlobarQuest/orchestrator/pull/773), ready for review, not merged.
The code is complete at `b4156581`, and this report's commit sits on top of it.

## What I built

- `.cred-consumers.toml:1452-1476`: a new block, `follow-up-mint-ping-url`, after the bump driver's.
  - Fields: class and provider `healthchecks-ping-url`,
    `bws_uuid = "46e714ca-32b7-4e89-9817-b509013363a5"`, `rotated_by_sds = true`,
    `rotate_requested = "2026-11-21"` and `healthchecks_check = "sds-follow-up-mint"`. The keeper is
    the row's one `bws-secret` consumer.
  - There's no `created`, for the reason the landing ledger's block gives.
  - The comment names G-87 and the VM root's keeper command. It says the lane is new: it has no
    Mac job and so no Mac check to keep, and ADR-0059 decision 3 moved the name lookup to install
    time, so `sds-follow-up-mint` was made by hand at G-87.
  - It says the broker reads the keeper only once the compose names its id and
    `lane-secrets-broker` has been granted a read on it, proven by the lane's `lane-run --read`
    (items 5, 7 and 8). It doesn't say the grant exists or has been proven. The consumer `note`
    says the same.
- `.cred-consumers.toml:1744`: #772's follow-up 1, folded in (see "Follow-ups").
- `tests/rotation_worker/test_follow_up_mint_rows.py` (new): seven tests, modelled on
  `test_bump_driver_rows.py`.
  - I dropped two of the bump driver's tests on purpose:
    - The manifest-and-allowlist test: the keeper joins the allowlist and the compose in item 7, so
      that test can't pass yet.
    - The read App key reader test: follow-up minting doesn't read the App key. Its one work secret
      is the SYSTEM bearer.
  - The launcher assertion uses `sds_outcome_begin "$CHECK_NAME" follow-up-mint`
    (`scripts/run-follow-up-mint.sh:57`). The launcher is an outcome-alarm launcher.

I changed no compose, allowlist, `.bws-secrets.toml`, `reach_secrets`, doc or ADR.
`46e714ca-…` is in neither the allowlist nor `reach_secrets`.

## Checks

### 1. A new row may land already due

- `src/rotation_worker/registry.py` has no rule on `rotate_requested`. A grep of `src` and `scripts`
  finds one reader, `rotation_proposer/findings.py:131`, which reads the date infraops serves.
- The ping-URL adapter's `check_row` (`adapters/healthchecks_ping_url.py:64`) asks for exactly the
  destinations `bws-secret` and `provider`, plus a check name (`registry.check_name`).
- The reach rule (`bws_lane_token.sharing_reach`) compares lane-token rows only, so a lane with no
  token row passes it. `test_the_lane_has_no_token_row_and_reads_through_the_broker` runs it over
  every lane-token row beside this one.
- In infraops (`src/security-drift/cred-rotation.ts` at `68c4af5`), the date is checked only as a
  quoted real date (`invalidRotateRequestedFinding`, `:220`, `:249`, `:265`). The due rule
  (`:301-310`) is a requested date with no recorded rotation after it. Neither reads another row or
  an earlier state.
- So no rule needs an earlier not-due state, and the row needs no token row.

### 2. The row parsed through the code

`test_follow_up_mint_rows.py` commits this repository's file through the `Origins` fixture and reads
it with `rotated_by_sds(RepoMirror)`. The tests cover the following:

- The row is handed to the SDS, and `bws_uuid` is the keeper.
- `check_row` passes, `healthchecks_check == "sds-follow-up-mint"`, and the launcher names that
  check and begins the outcome alarm.
- The keeper consumer's uuid equals `bws_uuid`.
- `rotate_requested == "2026-11-21"`.
- There's no `created` or `last_rotated`.
- `plan_for(…, first_issue=True).issue`.
- No lane-token row names `FOLLOW_UP_MINT_BWS_ACCESS_TOKEN`, the reach rule passes for every
  lane-token row, and the compose maps no token and mounts the broker socket.

Result: 7 passed.

The mutation: from a backup copy, I set the row's `healthchecks_check` to `"sds-follow-up-mintx"`.
Result: 1 failed (`test_the_ping_url_row_names_only_its_keeper`), 6 passed. I restored the file with
`cp`, confirmed it with `cmp`, and cleared `__pycache__`. After that: 7 passed.

### 3. Due-ness, from infraops' `cred-findings`

- Setup:
  - The copy: an `rsync` of `~/Projects/infraops-mcp-server` (no `.git`, HEAD `68c4af5`) into
    `/tmp/w258-infraops.LWOn`. I ran `node dist/cli/security-drift-cli.js cred-findings --now
    2026-11-21T20:00:00Z` from it.
  - Each run had its own `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR`. Each held a
    one-line `cred-consumers.list` and an empty `{}` `cred-rotation-state.json` (mode `0600`).
  - Treated used this branch's file, and control used `origin/main`'s.
  - I didn't edit or build the checkout, and I didn't touch `~/.config/infra-drift`.
- Treated: exit 0, 30 findings. Control: exit 0, 29 findings. Every finding was
  `cred.rotation-requested`.
- As `(check, id)` pairs, treated minus control is
  `{('cred.rotation-requested', 'follow-up-mint-ping-url')}`, and control minus treated is empty.
  The finding:
  `{'check': 'cred.rotation-requested', 'id': 'follow-up-mint-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-21', 'rotated_by_sds': True}`.
- Treated serves no `last_rotated` value for the row (`None`). The scratch state is empty, so this
  shows what the row's own fields produce, not the live state.

### 4. The rotation proposer

- Its next `--submit` pass reads `cred-findings`. It parses the row as due with trigger
  `requested` (`findings.py:131`).
- `_generate` (`cli.py:444`) calls `plan_for` with
  `first_issue=_first_issue(last_rotated, "follow-up-mint-ping-url")` (`:462`).
- `_first_issue` (`:436`) returns `True` for a `null` `last_rotated`.
- `plan_for` (`generate.py:131`) then returns the issue plan (`:149-150`). The type has only an
  issue plan, so `None` returns it too (`:151-156`).
- The pass generates `rotation-follow-up-mint-ping-url` revision 1: an issue plan whose machine
  unit stores the URL read from the Management key's listing (ADR-0055 amendment 23).
- The executor's pre-mint read:
  - `_other_keepers` (`worker.py:824-834`) reads the keeper of every other `rotated_by_sds` row of
    provider `healthchecks-ping-url` through `setup.store.read`. `BwsStore.locate`
    (`secret_store.py:100-101`) returns the `bws-secret` uuid.
  - From this merge on, every other ping-URL unit's pre-mint read includes `46e714ca-…`. That
    includes the bump driver's and estate landing's, both due the same day. This row's unit also
    reads every other ping-URL keeper.
  - The root has write on `Rotation / Keepers` (`docs/operations/rotation-executor.md:448`), and
    "Can read, write" on that project (`:478-479`), a project grant. The keeper is in
    `Rotation / Keepers`, so the root reads it through that grant.
  - If that read fails, `_read_mint_waits` turns the error into `ReadUnavailable` (`:716`), so the
    unit waits and isn't refused (`:720`).
- The listing read: `readers.py:283-318` makes one `GET` of the checks listing and keeps the
  checks whose `name` equals the row's `healthchecks_check` (`:309`). It requires exactly one such
  check (`:310-311`) with a non-empty `ping_url` (`:312-317`).
  - It reads no status, `last_ping` or ping count.
  - So `sds-follow-up-mint` is found by name even though it has never pinged, provided it's the
    only check with that name.
  - Healthchecks' listing includes checks in state `new` along with their `ping_url`. That's from
    the API's documented shape. I didn't make the read.
- This is from code and docs only. I made no BWS or Healthchecks call.

### 5. Fixtures and existing tests

- `registered_lanes` (`scripts/deploy_orchestrator.py:1308`) is `lanes_in_registry` (`:1252`). It
  counts lane-token `coolify-env` consumers keyed `*_BWS_ACCESS_TOKEN` and skips the broker's row.
  This row has no such consumer.
- I ran `lanes_in_registry` on this branch's file and on `origin/main`'s. The sets are equal, 13
  lanes: `bump-proposer, change-proposer, deploy-watcher, execution-watch, inert-landing,
  landing-ledger, membership-check, pin-watcher, revision-watcher, rotation-decomposer,
  rotation-proposer, unit-verifier, work-carrier`. `follow-up-mint` isn't among them, and the merge
  doesn't change that.
- `tests/scripts/test_deploy_lanes.py`:
  - I read `test_every_lanes_ping_url_secret_is_listed_for_that_lane` (`:1381`). Its `placeholders`
    (`:1388-1391`) is `{"change-window", "follow-up-mint"}`, and it asserts that set equals the
    composes with an empty `SDS_DEADMAN_PING_URL_UUID`.
  - The follow-up-mint compose value stays `""`, so `follow-up-mint` stays in `placeholders` until
    item 7. I didn't change it.
- The broker row's `test_secrets_broker_row.py` `SECRETS` is unchanged, because `reach_secrets` is
  unchanged.
- The other `*_rows.py` files each read their own lane's rows. The full `make check` run, with the
  row present, passed, so no test pins rows this change breaks. No agreement fixture lists ping-URL
  rows.

### 6. The membership check

- `src/membership_check/cli.py:110-121` reads the `rotated_by_sds` rows of a rotated class
  (`of_a_type(rotated_by_sds(...))`) only to collect the set of repositories that hand the SDS a
  credential.
- `consistency.py:173-198` compares that set with `credential_rotation` grants.
- `AlobarQuest/orchestrator` is already in the set (for example, through `bump-driver-ping-url`),
  so the pass doesn't change.

## Gates

- `make check` (`TEST_DATABASE_URL` set to the database `orchestrator_test_hq_w_258`, plus
  `SECURITY_STANDARDS_DIR`): 14,872 passed, 1 skipped, exit 0.
  - The first run failed `ruff` E501 on the new test's docstring (101 characters). I rewrapped the
    line and reran.
- Security scan from the repo root: 0 BLOCK findings. It found two WARNs that #770 also reported
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) and one INFO judgment item
  (`bws.least-privilege-scope`).
- `code-standards risk`: `normal`, so `vendor-review` didn't apply.
- `/code-review` at low effort on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_follow_up_mint_rows.py` (new)
- `docs/superpowers/reports/lanes-inc-9-follow-up-mint-ping-row-report.md` (this report)

## Deviations

- The test file has seven tests, not the bump driver's nine. I dropped two on purpose, and "What I
  built" gives the reasons.
- Unlike #770, I didn't change the lane's compose comment. Its comment says the ping-URL secret
  doesn't exist yet (`lanes/follow-up-mint/compose.yaml:13-15`), which the handoff puts in item 7's
  change along with the value.

## The broker's row (handoff item 2)

The `secrets-broker-bws-token` comment (`.cred-consumers.toml:1486-1494`) lists the single-secret
grants in Rotation / Keepers as `change-manager/M2M_TOKEN_READ`, `inert-landing-ping-url`,
`estate-landing-ping-url` and `bump-driver-ping-url`. It also quotes Devon's choice: "Project by
project", with single-secret grants in Rotation / Keepers so the broker can't read every lane's
keeper. The needed edit is a later step, after item 8's `lane-run --read` proves the grant (as #772
did for the bump driver):

- Add `follow-up-mint-ping-url` to that comment's Rotation / Keepers list, with the gate that gave
  it.
- Add `46e714ca-…` to `reach_secrets`.
- Add `46e714ca-…` to `test_secrets_broker_row.py`'s `SECRETS`.

## Follow-ups

Done:

- #772 follow-up 1, at `.cred-consumers.toml:1744`. The bump driver's `factory-events-read-password`
  reader note said the broker's grant was "Devon's act at the bump driver's cut-over gate". It now
  says Devon gave it at HQ gate G-84 and the lane's `lane-run --read` proved it at G-86. That's
  plainly true at the base: the broker row's comment says Devon gave the four at G-84, the
  bump-driver ping-URL block says G-86 proved the read, and `reach_secrets` lists `2f708d08-…`.

Left:

1. #772 follow-up 2 (`docs/operations/deploy.md` ~1171, "Devon's other act comes before step 8's dry
   run"). The handoff didn't offer it, and it's outside the file I hold.
2. Item 7: the compose's `SDS_DEADMAN_PING_URL_UUID` (with its comment), the broker allowlist's
   `[lanes.follow-up-mint]` entry, the `.bws-secrets.toml` lane listing for `46e714ca-…`, and
   removing `follow-up-mint` from `placeholders`. When that lands, the manifest-and-allowlist test
   from `test_bump_driver_rows.py` can join `test_follow_up_mint_rows.py`.
3. After item 8: the broker row edits listed in "The broker's row", and this row's comment and
   consumer note stating the grant as given and proven.
4. Item 10, after the infraops PR: the SYSTEM row's consumers.

## Self-review

- The row's text claims nothing unproven. It states the grant as a condition, and the G-87 facts
  as the handoff gave them.
- The test reads the row the way the executor does (`RepoMirror` at `main`), not through
  `tomllib`, so a parse rule the executor applies would fail it.
- One mutation surviving would have shown a test reading the wrong field. It didn't survive.

## Concerns

- In check 4, the claim that a never-pinged check lists its `ping_url` rests on the Healthchecks
  API's documented listing shape, not on a read. The executor's read would raise `ReadError` for a
  check shown without a URL (`readers.py:312-317`), which refuses the unit. So a wrong assumption
  fails loudly at the store unit; it doesn't write a wrong value.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The diff adds a
  registry row and a test, and neither logs.
- **Code Brain rule 4 (never swallow an exception silently):** the new test catches nothing.
  Every failure is a raised assertion or exception.
- **Code Brain rule 5 (never log secrets):** nothing added prints or holds a token, a ping URL, a
  ping URL's UUID, a bearer, a key or a password. The row and the test name only the BWS secret id
  `46e714ca-…`, which is a secret's id, not the ping URL's UUID, and the check's name.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history, no inline bootstrap
  token):** the diff has no token or token-shaped value. The security scan from the repo root found
  no BLOCK.
- **Infra Brain 2890 (never commit a ping URL):** no `https://healthcheck.alobar.net/ping/` URL
  appears in the diff.
- **Infra Brain 1032 (cred.exposure-rotate):** the row declares no `[[credential.exposure]]`. It is
  due by `rotate_requested` alone.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. There's no domain change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a plan, a
  test or a log. The row names ids only.
