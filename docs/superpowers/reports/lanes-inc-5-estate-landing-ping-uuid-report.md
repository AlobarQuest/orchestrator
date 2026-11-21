Outcome: done

# Report: lanes-inc-5-estate-landing-ping-uuid

ADR-0059 increment 5, step 3g-2: the code half of `docs/operations/deploy.md`'s estate-landing
cut-over item 6 (line 1037 at base, "A served release whose compose names the ping-URL secret's id,
with `estate-landing` removed from `test_deploy_lanes.py`'s `placeholders`"). It follows #757
(`6f54ab69`), with one difference: the lane reads through the lane secrets broker, so its keeper is
also in its broker allowlist entry.

- Base: `origin/main` at `d766d286`. The handoff's line references matched at that commit.
- Branch: `hq/lanes-inc-5-estate-landing-ping-uuid`.
- PR: #767, open and ready for review (not a draft), not merged.
- Code head: `985e9895`, which `make check` and both reviews ran on. The report commit follows it on
  the same branch.

## What I built

The keeper id `11e940d7-e43a-4ebd-9026-b508017d9cef` is a BWS secret id, not a secret, not a ping
URL, and not a check's ping UUID. It matches `.cred-consumers.toml`'s `estate-landing-ping-url` row
(`bws_uuid`) and `tests/rotation_worker/test_estate_landing_rows.py:33` (`KEEPER`).

1. `lanes/estate-landing/compose.yaml`:
   - Line 34: `SDS_DEADMAN_PING_URL_UUID: 11e940d7-e43a-4ebd-9026-b508017d9cef`.
   - Lines 19-25: the header paragraph is rewritten in `6f54ab69`'s shape. It says the helper reads
     the URL through the broker socket on every pass, the bare one included (the launcher calls
     `sds_deadman_arm` at `scripts/run-estate-landing.sh:152`), and that the allowlist entry lists
     the id too.
2. `lanes/secrets-broker/allowlist.toml` line 35: the keeper id, with `# estate-landing-ping-url`,
   at the end of `[lanes.estate-landing]`, as `[lanes.inert-landing]` lists its own.
   `tests/scripts/test_secrets_broker_allowlist.py`'s
   `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` already holds each entry equal
   to the launcher's UUID literals plus the compose's `SDS_DEADMAN_PING_URL_UUID`, so it needed no
   change: with the compose naming the id, it requires this line, and it fails without it
   (mutation check in the following section).
3. `.bws-secrets.toml` lines 544-556: one `[[secret]]` entry after `inert-landing-ping-url`, in its
   comment style: `name = "estate-landing-ping-url"`, `project = "Rotation / Keepers"`,
   `lanes = ["estate-landing"]`. The comment says the lane reads it through the broker and has no
   token keeper. The manifest didn't list the keeper before.
4. `tests/scripts/test_deploy_lanes.py`,
   `test_every_lanes_ping_url_secret_is_listed_for_that_lane`:
   - Line 1391 (base): `estate-landing` leaves `placeholders`, which is now `bump-driver`,
     `change-window`, and `follow-up-mint`.
   - Line 1407: `estate-landing` joins the `<= set(named)` set.
   - The docstring is unchanged: it describes `placeholders` in general, which stays true for the
     remaining lanes.
5. `tests/rotation_worker/test_estate_landing_rows.py`: it asserted nothing about the keeper in the
   manifest or allowlist (the lane has no token row, so it has no declared-reach test like inert's).
   I added `test_the_ping_url_keeper_is_in_the_lane_s_manifest_secrets_and_its_broker_entry`
   (lines 106-116), with `tomllib`, `load_allowlist` and `ALLOWLIST` (lines 11, 27, 37).
6. `tests/scripts/test_run_estate_landing.py`:
   - Line 166: unchanged. It is the harness's default environment, not a copy of the compose. With
     `BWS_ACCESS_TOKEN_BROAD` set to a stand-in, it deliberately runs the dead-man helper's
     empty-id fallthrough to the name lookup, which `test_each_pass_reads_its_secrets_with_the_lanes_one_token`
     and `test_container_branch_runs_on_the_socket_without_a_token` assert. `6f54ab69` kept inert's
     harness default the same way. No empty-id case was removed.
   - Line 424 (`_brokered` docstring): "The compose's ping-URL id is empty" becomes "The harness's
     ping-URL id is empty", because the compose's no longer is.
   - Lines 464-465: "Once the compose names the ping-URL secret (a later change)" becomes "With a
     ping-URL id named, as the compose names one". This test stays the by-id broker path's test.
   - Line 508: "which the container's compose names once its secret exists" becomes "which the
     container's compose names".
   - Lines 608-609: the compose assertion expects the literal keeper id, with `6f54ab69`'s comment.
   - Lines 662-671: `test_the_repositorys_manifest_lists_the_lanes_secrets` lists the keeper beside
     the five secrets and exempts it from the launcher-names-it check, because its id reaches the
     launcher from the compose through `lane-run`. The handoff didn't name this test, but the
     manifest change fails it otherwise; this is `6f54ab69`'s form.
7. `docs/operations/deploy.md` lines 1000-1005: "the following five … Its ping-URL secret joins
   them, in the manifest and the allowlist, when it exists; until then … `placeholders`" becomes
   "its ping-URL secret and the following five … the broker's allowlist lists exactly these six",
   plus a sentence naming `estate-landing-ping-url` and the helper reading it through the socket on
   every pass. Item 6 (line 1037) is unchanged, as `6f54ab69` left inert's item. Item 7 already
   says "all six secrets".

No ADR, launcher, `lane-run`, `sds-deadman.sh`, `scripts/secrets_broker.py`, crontab,
`.cred-consumers.toml`, or other lane's compose or allowlist entry changed.

### Item 8 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`: `scripts/lane_run.py:70`,
  `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`, copied into the launcher's environment at
  `:109`; `:113` sets `SDS_SECRETS_SOCKET` when the socket is mounted.
  `test_every_compose_variable_lane_run_must_pass_is_in_its_list` passes.
- Under the broker, `scripts/sds-deadman.sh` reads the URL by that id through the socket:
  `sds_deadman_resolve` (`:146-149`) calls `sds_deadman_resolve_by_uuid` when the id is set, which
  accepts a socket in place of a token (`sds_in_lane_container`, `:106`) and calls
  `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` (`:118`). The one-argument form reads through the
  socket when `SDS_SECRETS_SOCKET` names one (`scripts/sds-bws.sh:77`).
  `test_with_a_ping_url_named_the_socket_serves_every_read` proves it in the harness: `bws` never
  runs.

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_estate_landing.py`, `test_estate_landing_rows.py`,
  `test_secrets_broker_allowlist.py`, and `test_run_inert_landing.py` had 230 passed.
- Mutation checks, each restored afterward:
  - Estate-landing removed from the manifest entry's `lanes`: the new rows test and
    `test_every_lanes_ping_url_secret_is_listed_for_that_lane` fail (2 failed).
  - The allowlist line removed: the new rows test and
    `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` fail (2 failed).
  - The compose value set back to `""`: `test_every_lanes_ping_url_secret_is_listed_for_that_lane`
    and the allowlist test fail (2 failed).
- `make check` on `985e9895`, database `orchestrator_test_hq_w_252`, exited 0:
  - Ruff: "All checks passed!", 1038 files already formatted.
  - Pyright: 0 errors, 0 warnings.
  - Tests: 14848 passed, 1 skipped (14849 collected).
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO, the same three #757
  reported (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`,
  `bws.least-privilege-scope`). The diff causes none of them.
- `/code-review` (low) on `origin/main...HEAD`: no findings.
- `code-standards risk`: `high-risk` (secrets: `.bws-secrets.toml`, `lanes/secrets-broker/allowlist.toml`),
  so I also ran `vendor-review`: Claude and Codex both approve, 0 findings kept, 0 dropped. agy: off
  by configuration.

## Files changed

- `lanes/estate-landing/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/rotation_worker/test_estate_landing_rows.py`
- `tests/scripts/test_run_estate_landing.py`
- `docs/superpowers/reports/lanes-inc-5-estate-landing-ping-uuid-report.md` (this report)

## Self-review

- The diff follows `6f54ab69`, adding only the allowlist line and the new rows test that the broker
  difference needs.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call. I ran nothing in
  `~/Projects/orchestrator`.

## Concerns

- The broker serves the allowlist baked into its own release. Until the broker moves to a release
  with this entry (item 7), a lane container on this release would have its ping-URL read refused
  (`not_allowed`) and ping nothing, while the pass still runs. Item 7 already moves the broker
  before the lane's `--add`, so this needs no change; HQ keeps that order.
- HQ's release of #767 is the served-release half of item 6.
- The test database `orchestrator_test_hq_w_252` still exists for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the added test catches nothing; it
  asserts directly.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a check's
  ping UUID, or a token. The only id added is the keeper's BWS secret id, labeled "not a secret,
  not the ping URL" in the compose and the test.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value; the compose maps no token at all. The scan has 0 BLOCK findings, and the
  diff adds no test token values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port,
  no domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits one BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL.
