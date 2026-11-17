Outcome: done

# Report: lanes-inc-4-work-carrier-ping-uuid

ADR-0059 increment 4, the work carrier's cut-over item 6 in `docs/operations/deploy.md`, code half.
This is the change #697 (`5a781dbb`) made for the rotation decomposer.

- Base: `origin/main` at `2b56b525`. Every handoff line reference matched at that commit.
- Branch: `hq/lanes-inc-4-work-carrier-ping-uuid`.
- PR: #732, open and ready for review, not merged.
- Code head: `3de56739`, which `make check` ran on. The report commit follows it on the same branch.

## What I built

The keeper id `55fbae20-e503-4eb5-b75c-b5040164d394` is a BWS secret id: not a secret, and not a
ping URL or a check's ping UUID. It matches `.cred-consumers.toml`'s `work-carrier-ping-url` row
(`bws_uuid`, line 1234).

1. `lanes/work-carrier/compose.yaml`
   - Line 33: `SDS_DEADMAN_PING_URL_UUID` holds the literal keeper id.
   - Lines 18-23: the header paragraph is rewritten in #697's shape, with this lane's terms ("a
     `--register` pass", "a bare pass pings nothing").
2. `.bws-secrets.toml`, lines 446-458: one `[[secret]]` entry after `rotation-decomposer-ping-url`,
   with `name = "work-carrier-ping-url"`, `project = "Rotation / Keepers"`, and
   `lanes = ["work-carrier"]`. Its comment follows #697's entry, naming `work-carrier-bws-token` as
   the token keeper the manifest doesn't list. The manifest didn't list the keeper before.
3. `tests/scripts/test_deploy_lanes.py`, `test_every_lanes_ping_url_secret_is_listed_for_that_lane`
   - Lines 1191-1195: the docstring drops the G-67 sentence and says "No lane is waiting for its
     keeper." The placeholder mechanism and its description stay for a later lane.
   - Line 1197: `placeholders: set[str] = set()`.
   - Line 1218: `work-carrier` joins the `<= set(named)` set.
   - Line 859 (`lanes_in_registry`): unchanged. It lists registered lanes and encodes no ping-URL
     value.
   - Mutation check: with the compose value set back to `""`, the test fails on
     `assert blank == placeholders` (`{'work-carrier'} == set()`). Restoring the value makes it pass.
4. `tests/scripts/test_run_work_carrier.py`
   - Lines 622-623: the compose test asserts the literal id, with #697's comment.
   - Lines 667-677: the manifest test, renamed from `..._lists_the_lanes_eight_secrets` to
     `..._lists_the_lanes_secrets`, lists the ping-URL secret beside `LANE_SECRETS`. The launcher
     check exempts that id, because it reaches the launcher from the compose through `lane-run`. This
     is #697's form.
   - Line 225: unchanged. It is the launcher harness's default environment, not a copy of the
     compose. With `BWS_ACCESS_TOKEN_BROAD` set to a stand-in (line 231), it deliberately runs the
     dead-man helper's empty-id fallthrough to the name lookup, and
     `test_a_registering_pass_reads_all_eight_secrets_with_the_lanes_one_token` (line 260) asserts
     that path ("the dead-man helper's Healthchecks key, by the broad stand-in"). So no empty-id case
     was removed and the fallthrough stays tested there. #697 also kept its harness's empty-id cases.
5. `tests/rotation_worker/test_work_carrier_rows.py`, lines 103-104: #697's two-line form. The keeper
   is now in the lane's manifest secrets, and the declared reach equals them (and `REACH`).
6. `docs/operations/deploy.md`, work-carrier section:
   - Lines 598-599: "Its `.bws-secrets.toml` secrets are eight" becomes "its ping-URL secret and the
     following eight", because the manifest now lists nine for the lane.
   - Lines 629-630: "Its compose leaves `SDS_DEADMAN_PING_URL_UUID` empty until … `placeholders`"
     becomes #697's sentence: the compose names the variable, the BWS secret id of
     `work-carrier-ping-url`, which the dead-man helper reads the URL from with the lane's own token.
   - Line 650, step 7: "the `lane-run --read` proof over all eight secrets" becomes "all nine
     secrets", since the proof reads what the manifest lists (as #697's lane reads "all four").
   - Step 6 (line 648) is unchanged, as #697 left its lane's step 6.

No ADR or other lane's compose changed.

### Item 6 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`:
  - `lanes/lane-run:4` runs `scripts/lane_run.py`.
  - `scripts/lane_run.py:49` is `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`.
  - `scripts/lane_run.py:77`, in `lane_environment`, copies each `COPIED` name into the launcher's
    environment.
- `scripts/sds-deadman.sh` reads the URL by that id with the lane's own token:
  - `scripts/run-work-carrier.sh:194-195` sources the helper and calls `sds_outcome_begin`.
  - That calls `sds_deadman_resolve` (`scripts/sds-deadman.sh:204`).
  - When the variable is set, that calls `sds_deadman_resolve_by_uuid` (`:126-127`).
  - That refuses when `BWS_ACCESS_TOKEN` is unset (`:105-107`), then calls
    `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` in the one-argument form (`:109`).
  - The one-argument form reads with the ambient `BWS_ACCESS_TOKEN`, the lane's token
    (`scripts/sds-bws.sh`, `sds_bws_value`; only its two-argument branch sets another token, and that
    overrides it).

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_work_carrier.py`, and `test_work_carrier_rows.py`
  had 165 passed.
- `make check` on `3de56739`, database `orchestrator_test_hq_w_193`, exited 0:
  - Ruff: "All checks passed!", 1077 files already formatted.
  - Pyright: 0 errors.
  - Tests: 15275 items collected, 15274 passed, 1 skipped.
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO, the same three #697
  reported. The diff causes none of them:
  - WARN `bws.secret-files-gitignored`.
  - WARN `bws.manifest-matches-usage`.
  - INFO `bws.least-privilege-scope`: a judgment finding.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/work-carrier/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/scripts/test_run_work_carrier.py`
- `tests/rotation_worker/test_work_carrier_rows.py`
- `docs/superpowers/reports/lanes-inc-4-work-carrier-ping-uuid-report.md` (this report)

## Self-review

- The diff follows #697 line for line, apart from the lane's own wording (`--register`, bare pass)
  and two deploy.md counts (eight to nine) that #697's lane didn't have.
- No change to the launcher, `lane-run`, `sds-deadman.sh`, `scripts/run-work-carrier.sh`,
  `.cred-consumers.toml`, any ADR, or any other lane's compose.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call.

## Concerns

- The work carrier's launcher harness runs only the empty-id path, so no work-carrier launcher test
  exercises a `--register` ping by id. The by-id path is the shared helper's, tested in
  `tests/scripts/test_sds_deadman.py` (~614-633) and in the rotation decomposer's and change
  proposer's harnesses. I left this as is; adding a by-id harness case is a later, optional change.
- The `REACH` comment in `test_work_carrier_rows.py` (lines 45-46) still reads "The eight
  `.bws-secrets.toml` secrets the lane reads and its ping URL's keeper". It stays true (the launcher
  reads eight; the helper reads the keeper), and #697 left its sibling comment, so I didn't change it.
- HQ's release of #732 is the served-release half of item 6. The test database
  `orchestrator_test_hq_w_193` still exists for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the diff adds no handler. The edited
  tests assert directly and catch nothing.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a check's
  ping UUID, or a token. The only id added is the keeper's BWS secret id, kept distinct from the
  ping URL in the compose comment, the manifest comment, and the test comment.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value. The compose keeps `${WORK_CARRIER_BWS_ACCESS_TOKEN}`. The scan has 0
  BLOCK findings, and the diff adds no new test values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port, no
  domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits a BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL.
