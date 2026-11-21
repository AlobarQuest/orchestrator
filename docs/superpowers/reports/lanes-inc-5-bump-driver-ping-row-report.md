Outcome: done

# Build report: the bump driver's ping-URL registry row, due for its first issue

Worker W-255. Handoff: `lanes-inc-5-bump-driver-ping-row` (ADR-0059 increment 5, step 3g-3,
deploy.md's bump-driver cut-over items 4 and 6). Base `origin/main` at `25ce64da`. I confirmed every
repository fact in the handoff there. The keeper's existence and the G-84 grants are the handoff's
word: I made no BWS call.

PR: [#770](https://github.com/AlobarQuest/orchestrator/pull/770), ready for review, not merged.
The code is complete at `bb6075cd`, and this report's commit sits on top of it.

## What I built

- `.cred-consumers.toml:1426-1449`: a new block, `bump-driver-ping-url`, after estate landing's.
  - Fields: class and provider `healthchecks-ping-url`, `bws_uuid = "430691e2-…"`,
    `rotated_by_sds = true`, `rotate_requested = "2026-11-21"` and
    `healthchecks_check = "sds-bump-driver"`. The keeper is the row's one `bws-secret` consumer.
  - There's no `created`, for the reason the landing ledger's block gives.
  - The comment names G-84 and the VM root's keeper command. It says the broker reads the keeper
    for the lane only once the compose names its id and `lane-secrets-broker` is granted a read on
    it, proven by the lane's `lane-run --read` (items 7 and 8). It doesn't say the grant exists.
- `.cred-consumers.toml:1600-1605`: the lane container's `runtime-fetch` reader on
  `lanes-read-app-private-key`, with `healthchecks_check = "sds-bump-driver"`.
  - Its note says the key is read through the broker each pass (dry or acting) and piped straight
    into `sds-github-mint`, and that the reader pings `sds-bump-driver` on acting passes only.
  - The row's narrative comment (`:1506-1507`) gains one sentence for the lane.
  - I didn't change the Mac `runtime-fetch` entries (`:62-64`, `:216`).
- `lanes/bump-driver/compose.yaml:23-26`: a comment change only. It names the keeper and G-84, as
  #765 did for estate landing. `SDS_DEADMAN_PING_URL_UUID` stays `""`, because that's item 7.
- `tests/rotation_worker/test_bump_driver_rows.py`: eight tests, modelled on
  `test_estate_landing_rows.py`.
  - It doesn't copy estate's `sds_deadman_arm` assertion. The bump driver is an outcome-alarm
    launcher, which must not contain the arming function's name (`scripts/CLAUDE.md`).
  - Instead, it asserts the launcher's `CHECK_NAME="sds-bump-driver"` and its
    `sds_outcome_begin "$CHECK_NAME" bump-driver "$_acting"` line.

I made no change to `lanes/secrets-broker/allowlist.toml`, `.bws-secrets.toml`, the broker's
`reach_secrets` or any doc. `430691e2-…` is in neither the allowlist nor `reach_secrets`.

## Checks

### 1. A new row may land already due

- `src/rotation_worker/registry.py` has no rule on `rotate_requested`. Its row rules are the keeper
  form (`RegistryEntry.keeper`, `:59-75`), `check_name` (`:79-84`), and unique ids
  (`credentials`, `:124-133`).
- The ping-URL adapter's `check_row` (`adapters/healthchecks_ping_url.py:64-76`) asks for exactly
  the destinations `bws-secret` and `provider`, plus a check name.
- The reach rule (`bws_lane_token.sharing_reach`) compares lane-token rows only, so a lane with no
  token row passes it. `test_the_lane_has_no_token_row_and_reads_through_the_broker` runs it over
  every lane-token row beside this one.
- A grep of `src`, `scripts` and `tests` for `rotate_requested` finds no rule that needs an earlier
  not-due state. The only reader in this repository is `rotation_proposer/findings.py:131`, which
  reads the date infraops serves.
- In infraops (`src/security-drift/cred-rotation.ts` at `68c4af5`), the date is checked only as a
  quoted real date (`invalidRotateRequestedFinding`, `:226`, `:249`, `:265`). The due rule
  (`:301-312`) is `now >= rotate_requested` with no recorded rotation after it. Neither reads
  another row or an earlier state.
- So nothing needed a split.

### 2. The row parsed through the code

`test_bump_driver_rows.py` commits this repository's file through the `Origins` fixture and reads
it with `rotated_by_sds(RepoMirror)`. The tests cover the following:

- `check_row`.
- The keeper consumer's uuid equals `bws_uuid`.
- `rotate_requested == "2026-11-21"`.
- No `created` or `last_rotated`.
- `plan_for(…, first_issue=True).issue`.
- No `BUMP_DRIVER_BWS_ACCESS_TOKEN` variable on any lane-token row, and the reach rule.
- The compose maps no token and mounts the broker socket.
- The read App key's one reader for the launcher.

Result: 8 passed.

I ran two mutations, each from a backup copy, restored with `cp` and confirmed with `cmp`:

| Mutation | Result |
|---|---|
| Removed the row's `rotate_requested` line | 1 failed, 7 passed (`test_the_row_is_due_for_its_first_issue`) |
| Set the keeper consumer's uuid to `00000000-0000-4000-8000-000000000000` | 1 failed, 7 passed (`test_the_keeper_consumer_is_the_bws_uuid`) |

After the restore, and with `__pycache__` cleared: 8 passed.

### 3. Due-ness, from infraops' `cred-findings`

- Setup:
  - The copy: an `rsync` of `~/Projects/infraops-mcp-server` (no `.git`, HEAD `68c4af5`) into
    `/tmp/w255-infraops.TibR`. I ran `node dist/cli/security-drift-cli.js cred-findings --now
    2026-11-21T14:00:00Z` from it.
  - Each run had its own `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR`. Each held a
    one-line `cred-consumers.list` and an empty `{}` `cred-rotation-state.json` (mode `0600`).
  - Treated used this branch's file; control used `origin/main`'s.
  - I didn't edit or build the checkout, and I didn't touch `~/.config/infra-drift`.
- Treated: exit 0, 29 findings. Control: exit 0, 28 findings. Every finding was
  `cred.rotation-requested`.
- As `(check, id)` pairs, treated minus control is
  `{('cred.rotation-requested', 'bump-driver-ping-url')}`, and control minus treated is empty. The
  finding:
  `{'check': 'cred.rotation-requested', 'id': 'bump-driver-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-21', 'rotated_by_sds': True}`.
- Treated's `last_rotated` serves `bump-driver-ping-url` as `null`. The scratch state is empty, so
  this shows what the row's own fields produce, not the live state.

### 4. The rotation proposer

- Its next `--submit` pass reads `cred-findings`. It parses this row as `Due` with trigger
  `requested` (`findings.py:131`) and `handed_over = True`.
- `_generate` (`cli.py:444`) calls `plan_for` with
  `first_issue=_first_issue(last_rotated, "bump-driver-ping-url")` (`:462`).
- `_first_issue` (`:436-441`) returns `True` for a `null` `last_rotated`.
- `plan_for` (`generate.py:131-162`) then returns the issue plan (`:149-150`). The type has only an
  issue plan, so `None` returns it too (`:151-156`).
- The pass generates `rotation-bump-driver-ping-url` revision 1, an issue plan with one machine
  unit named `store`, the URL read from the Management key's listing (ADR-0055 amendment 23).
- The executor's pre-mint read:
  - `_other_keepers` (`worker.py:824-834`) reads, through `setup.store.read`, the keeper of every
    other `rotated_by_sds` row of provider `healthchecks-ping-url`. `BwsStore.locate`
    (`secret_store.py:100-101`) returns the `bws-secret` uuid.
  - From this merge on, every other ping-URL unit's pre-mint read includes `430691e2-…`. That
    includes estate landing's, dated the same day. This row's unit reads every other ping-URL
    keeper too.
  - The root has "write on … `Rotation / Keepers`" (`docs/operations/rotation-executor.md:448`)
    and "Can read, write" there (`:478-479`), a project grant. The keeper is in
    `Rotation / Keepers`, so the root reads it through that grant.
  - If that read fails, `_read_mint_waits` turns the error into `ReadUnavailable`, so the unit
    waits and isn't refused (`:713-716`, `:728-729`).
  - This is from code and docs only; I made no BWS call.

### 5. Fixtures and existing tests

- `registered_lanes` (`scripts/deploy_orchestrator.py:1308`) is `lanes_in_registry` (`:1252-1276`).
  It counts lane-token `coolify-env` consumers on a `service` keyed `*_BWS_ACCESS_TOKEN` and skips
  the broker's row. This row has no such consumer.
- On this branch it returns these 13 lanes, the same as at the base:
  `bump-proposer, change-proposer, deploy-watcher, execution-watch, inert-landing, landing-ledger,
  membership-check, pin-watcher, revision-watcher, rotation-decomposer, rotation-proposer,
  unit-verifier, work-carrier`.
  - `inert-landing` is there through its own token row's variable.
  - `bump-driver` and `estate-landing` aren't there.
- `tests/scripts/test_deploy_lanes.py`:
  - Its `registered_lanes` tests (`:787`, `:801`, `:820`) use fixture worlds, not the real file.
  - `placeholders` (`:1388-1392`) still holds `bump-driver`, because the compose value stays empty
    (item 7). I didn't change it.
- I grepped `tests/` for every test that reads the real registry. The full `make check` run, with
  the row present, passed, so no test pins rows this change breaks. The ones I read:
  - `test_registry_array_strings.py` checks for commas inside array strings. The new row has no
    array.
  - `test_standing_approval.py` reads `landing-ledger-ping-url`.
  - `test_secrets_broker_row.py`'s `SECRETS` is unchanged. `430691e2-…` stays out of it, and so do
    the three G-84 ids (see follow-up 1).
  - The other `*_rows.py` files each read their own lane's rows.
- No agreement fixture lists ping-URL rows.

### 6. The membership check

- `src/membership_check/cli.py:110-121` reads the `rotated_by_sds` rows of a rotated class
  (`types.of_a_type`) only to collect the set of repositories that hand the SDS a credential.
- `consistency.py:173-198` compares that set with `credential_rotation` grants.
- `AlobarQuest/orchestrator` is already in the set (for example, through `estate-landing-ping-url`),
  so the pass doesn't change.

### Gates

- `make check` (database `orchestrator_test_hq_w_255`, migrated to head): 14,864 passed, 1 skipped,
  exit 0.
- Security scan from the repo root: 0 BLOCK findings. It found two WARNs, which were already there
  before this change (`bws.secret-files-gitignored` on `.gitignore` and
  `bws.manifest-matches-usage` on `.bws-secrets.toml`), and one INFO judgment item.
- `code-standards risk`: `normal`, so `vendor-review` didn't apply.
- `/code-review` at low effort on `origin/main...HEAD`: no findings.
- CI on PR head `91615f5f`: all three jobs passed. `Quality` collected and passed 14,839 tests, with 26 skipped
  (run 44911506762).

## Deviations

- `lanes/bump-driver/compose.yaml`: the handoff puts the compose in item 7's PR. I changed only its
  comment, naming the keeper, as #765 did for estate landing. The value stays empty.
- Handoff item 4: I left the broker's reach change as a follow-up (see "The broker's row").

## Files changed

- `.cred-consumers.toml`
- `lanes/bump-driver/compose.yaml` (comment only)
- `tests/rotation_worker/test_bump_driver_rows.py` (new)
- `docs/superpowers/reports/lanes-inc-5-bump-driver-ping-row-report.md` (this report)

## The lane's other secrets (handoff item 3)

The secrets come from the allowlist's `[lanes.bump-driver]` list, seven UUIDs. Estate landing and
inert landing have a container reader only on the read App key's row
(`.cred-consumers.toml:1562`, `:1569` at the base). So that row is the only one where I added a
bump-driver container reader.

| Secret | Row in `.cred-consumers.toml` | Estate or inert container reader | Bump-driver container reader |
|---|---|---|---|
| SYSTEM bearer `221a48d5-…` | `orchestrator-system-bearer` | None (a combined Mac `ref`, `:216`) | None added |
| Alert sender `b415d03b-…` | None (`reach_secrets` mentions only) | None | None |
| Read App key `314a966c-…` | `lanes-read-app-private-key` | Yes | Added |
| change-manager READ `314f276d-…` | None here (change-manager's registry owns it) | None | None |
| Brain keys `750f737f-…`, `da8134b0-…` | None | None | None |
| Read password `2f708d08-…` | `factory-events-read-password` | Not applicable | Already there at `:1709`, from 3g-3-inputs and #768; I added no duplicate |

## The broker's row (handoff item 4)

- At the base, `secrets-broker-bws-token`'s `reach_secrets` (`:1467` on this branch) holds three
  ids: `314f276d-…`, `35557473-…` and `11e940d7-…`. `test_secrets_broker_row.py`'s `SECRETS`
  holds the same three.
- Neither holds `750f737f-…`, `da8134b0-…` or `2f708d08-…`.
- The comment describes "three single secrets in Rotation / Keepers". These three are in
  `Brains - Contributor` and `Factory Events`.
- I didn't add them, because adding them would make other text in the repository wrong. Several
  passages still describe the grants as a later act:
  - deploy.md item 5 (~1158-1163) says they "are outside what the broker's account reads
    (… the `secrets-broker-bws-token` row's reach)" and are Devon's act at the cut-over gate.
  - The same wording is in `.bws-secrets.toml:256-261` and `:583-591`,
    `docs/operations/credentials.md` (the bump-driver paragraph), and the read password reader's
    note (`.cred-consumers.toml:1711`).
- Changing the reach alone would put the registry at odds with those docs. Rewriting them would go
  beyond items 4 and 6. Follow-up 1 lists the work.

## Folded-in follow-ups (handoff item 5)

The following table shows what #768 or earlier PRs had already done and what I left open.

| Follow-up | Status |
|---|---|
| #765 follow-up 3: a container reader on `coolify-estate-lander-token` | Already done by #768: one consumer naming both readers (`.cred-consumers.toml:678-680` at the base) |
| #766 follow-up 1: the inert token row's "later act" comment | Already done by #768 (`:1351-1357` at the base) |
| #766 follow-up 2: other lanes' "own token" ping notes | Nothing to change. All twelve notes (`execution-watch` through `rotation-proposer`, base `:788-1348`) belong to lanes that each have a `*-bws-token` row and read with their own token. The bump driver had no ping-URL keeper note before this row (#768 follow-up 1); its new note says the broker reads it. |
| #762 follow-up 2: stale "own token" text in `.bws-secrets.toml` and `credentials.md` | Already done by #768 (its `.bws-secrets.toml` and `credentials.md` hunks). Grepping both files at the base shows "own token" only for lanes that have their own token. |
| #764 follow-up 3: stale text at `.bws-secrets.toml` 255-257 and 564-567, and `.cred-consumers.toml` 1645 | Already done by #768 |
| #764 follow-up 4: the three UUIDs in the broker's `reach_secrets` | Left open; see follow-up 1 |

## Self-review

- The diff adds ids only. No value, ping URL or ping-URL UUID appears anywhere.
  `430691e2-4a36-4261-a28f-b50900e064b7` is a BWS secret id, not the UUID inside a ping URL.
- The reader note's claim "pings on acting passes only, never on `--dry-run`" comes from
  `scripts/run-bump-driver.sh:120-126` (`_acting=0` for `--dry-run`, passed to
  `sds_outcome_begin`) and `scripts/sds-deadman.sh:217-225`, which resolves the URL only when
  acting. deploy.md ~1082-1084 agrees.
- The read is at `run-bump-driver.sh:224-227`: `sds_bws_value "$READ_APP_KEY_UUID" | env -u
  BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET … sds-github-mint`. It's inside `sds_in_lane_container`,
  before any `--dry-run` branch.
- The compose comment is the only non-registry change. It changes no value, so a lane release
  isn't affected.

## Concerns

- None blocking.
- Every other ping-URL unit's pre-mint read gains `430691e2-…` from merge (check 4). The root reads
  it through its project grant, so I expect no wait. If the read fails anyway, the unit waits; it
  isn't refused.

## Follow-ups left

1. After the G-84 grants are proven at the bump driver's cut-over item 8, add `750f737f-…`,
   `da8134b0-…` and `2f708d08-…` to `secrets-broker-bws-token`'s `reach_secrets`. In the same
   change, do the following:
   - Add them to `SECRETS` in `test_secrets_broker_row.py`.
   - Reword the comment to name `Brains - Contributor` and `Factory Events`.
   - Rewrite the "Devon's act at the cut-over gate" text in deploy.md item 5,
     `.bws-secrets.toml:256-261` and `:583-591`, `credentials.md`'s bump-driver paragraph, and
     `.cred-consumers.toml:1711`.
   - Also add `430691e2-…` once Devon's single-secret grant on it is made.
2. Item 7, a later PR:
   - The compose's `SDS_DEADMAN_PING_URL_UUID: 430691e2-…`.
   - The broker allowlist's `[lanes.bump-driver]` entry and a `.bws-secrets.toml` `[[secret]]` with
     `lanes = ["bump-driver"]`.
   - `bump-driver` removed from `placeholders`.
   - deploy.md ~1082-1086's "when it exists" text.
   - A test like estate's `test_the_ping_url_keeper_is_in_the_lane_s_manifest_secrets_and_its_broker_entry`.
3. Item 6's executor write is HQ's act.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** no logging change. The diff
  adds a registry row, a comment and a test.
- **Code Brain rule 4 (never swallow an exception silently):** the new test catches nothing. Every
  failure is an assertion or a raised error.
- **Code Brain rule 5 (never log secrets, credentials, auth headers or tokens):** the diff prints
  nothing. The row and the test name only the BWS secret id and the check name, never a ping URL, a
  UUID inside one, a bearer, a key or a password.
- **Infra Brain 591, 592 and 593:** the diff adds no BWS token and no bootstrap token, inline or
  otherwise. The only id-shaped test value is the mutation's
  `00000000-0000-4000-8000-000000000000`, which was never committed and doesn't match the token
  shape. The scan found 0 BLOCK findings.
- **Infra Brain 2890 (never commit a ping URL):** no `https://healthcheck.alobar.net/ping/` string
  is added.
- **Infra Brain 1032 (cred.exposure-rotate):** the row declares no `[[credential.exposure]]`, and
  the treated `cred-findings` run raised no `cred.exposure-rotate`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable, because the diff changes no domain.
- **Infra Brain lesson 450 (rotation landmines):** the row, the test and this report name ids only.
  No secret value enters the registry, a plan, a test or a log.
