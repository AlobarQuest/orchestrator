Outcome: done

# Report: lanes-inc-4-change-proposer-ping-uuid

ADR-0059 increment 4, cut-over step 6 in `docs/operations/deploy.md`, code half, for the
change-proposer lane. This is the change #685 (`5ed15482`) made for the membership check.

- Base: `origin/main` at `261d99bf`. Every handoff line reference matched at that commit.
- Branch: `hq/lanes-inc-4-change-proposer-ping-uuid`.
- PR: #692, open and ready for review, not merged.
- Code commit: `49fb0150`, which `make check` ran on. The report commit follows it on the same
  branch.

## What I built

The keeper id `7651ccc2-7e13-417b-86da-b50100c0d4b2` is a BWS secret id: not a secret, and not a
ping URL or a check's ping UUID. It matches `.cred-consumers.toml`'s `change-proposer-ping-url` row
(`bws_uuid`, line 1102) and the `GRANTED` set in `test_change_proposer_rows.py`.

1. `lanes/change-proposer/compose.yaml`
   - Line 23: `SDS_DEADMAN_PING_URL_UUID` holds the literal keeper id.
   - Lines 9-15: the header is rewritten in #685's shape. It keeps this lane's own fact that the
     helper reads the URL only on a `--submit` pass and a dry run pings nothing.
2. `.bws-secrets.toml`, lines 404-416: one `[[secret]]` entry after `membership-check-ping-url`,
   with `name = "change-proposer-ping-url"`, `project = "Rotation / Keepers"`, and
   `lanes = ["change-proposer"]`. Its comment follows #685's entry, naming
   `change-proposer-bws-token` as the token keeper the manifest doesn't list.
3. `tests/scripts/test_deploy_lanes.py`, `test_every_lanes_ping_url_secret_is_listed_for_that_lane`
   - Lines 1193-1194: the docstring drops "The change proposer's keeper was created at HQ gate
     G-49; a later change writes its id." The rotation decomposer sentence stays.
   - Line 1195: `placeholders = {"rotation-decomposer"}`.
   - Line 1207: `change-proposer` joins the `<= set(named)` set.
   - Mutation check: with the compose value set back to `""`, the test fails on
     `assert blank == placeholders`. Restoring the value makes it pass again.
4. `tests/scripts/test_run_change_proposer.py`
   - Line 345: the docstring begins "An empty `SDS_DEADMAN_PING_URL_UUID`", because the compose
     isn't empty. The test is unchanged and still covers the dead-man helper's empty-id
     fallthrough, so no fallthrough coverage was removed.
   - Lines 411-412: the compose test asserts the literal id, with #685's comment.
   - Lines 461-473: the manifest test lists the ping-URL secret fourth. The launcher check exempts
     that id, because the id reaches the launcher from the compose through `lane-run`; the
     docstring says so.
   - Line 386 is unchanged. It is the Mac path's `SDS_DEADMAN_PING_URL_UUID=""`, which has no
     compose and doesn't assert the compose's value.
5. `tests/rotation_worker/test_change_proposer_rows.py`, lines 94-95: #685's two-line form. The
   keeper is now in the lane's manifest secrets, and the declared reach equals them.
6. `docs/operations/deploy.md`, change-proposer paragraph, lines 466-474 (reflowed):
   - The `.bws-secrets.toml` secrets list adds "its ping-URL secret". "The first two" (the Mac's
     reads) stays true.
   - "its compose leaves `SDS_DEADMAN_PING_URL_UUID` empty until the lane's ping-URL secret exists,
     so until then a `--submit` pass in the container still runs, pings nothing and says so"
     becomes #685's sentence: the compose names the variable, the BWS secret id of
     `change-proposer-ping-url`, which the dead-man helper reads the URL from with the lane's own
     token.
   - Line 487 (step 6) is unchanged, as #685 left its lane's step 6.

### Item 5 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`:
  - `lanes/lane-run:4` runs `scripts/lane_run.py`.
  - `scripts/lane_run.py:46` is `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`.
  - `scripts/lane_run.py:71`, in `lane_environment`, copies each `COPIED` name into the launcher's
    environment.
- `scripts/sds-deadman.sh` reads the URL by that id with the lane's own token:
  - `scripts/run-change-proposer.sh:118-119` sources the helper and calls `sds_outcome_begin`.
  - That calls `sds_deadman_resolve` (`scripts/sds-deadman.sh:204`).
  - When the variable is set, that calls `sds_deadman_resolve_by_uuid` (`:126-127`).
  - That refuses when `BWS_ACCESS_TOKEN` is unset (`:105-107`), then calls
    `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` in the one-argument form (`:109`).
  - The one-argument form reads with the ambient `BWS_ACCESS_TOKEN`, the lane's token
    (`scripts/sds-bws.sh:57-59`).

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_change_proposer.py`, and
  `test_change_proposer_rows.py` had 138 passed.
- `make check` on `49fb0150`, database `orchestrator_test_hq_w_134`, exited 0:
  - Ruff: "All checks passed!", 1060 files already formatted.
  - Pyright: 0 errors.
  - Tests: 14832 items collected, 14831 passed, 1 skipped.
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO, the same three #685
  reported. The diff causes none of them:
  - WARN `bws.secret-files-gitignored` (`.gitignore`).
  - WARN `bws.manifest-matches-usage` (`.bws-secrets.toml`).
  - INFO `bws.least-privilege-scope`: a judgment finding.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/change-proposer/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/scripts/test_run_change_proposer.py`
- `tests/rotation_worker/test_change_proposer_rows.py`
- `docs/superpowers/reports/lanes-inc-4-change-proposer-ping-uuid-report.md` (this report)

## Self-review

- The diff follows #685 line for line, apart from the change proposer's dry-run fact kept in the
  compose header.
- No change to the launcher, `lane-run`, `sds-deadman.sh`, `.cred-consumers.toml`, any ADR, or the
  rotation decomposer's compose.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call.

## Concerns

None for this change. HQ's release of #692 is the served-release half of step 6. The test database
`orchestrator_test_hq_w_134` still exists for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the diff adds no test and no
  handler. The edited tests assert directly and catch nothing.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a
  check's ping UUID, or a token. The only id added is the keeper's BWS secret id, kept distinct
  from the ping URL's UUID in the compose comment, the manifest comment, and the test comment.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value. The compose keeps `${CHANGE_PROPOSER_BWS_ACCESS_TOKEN}`. The scan has
  0 BLOCK findings, and the diff adds no new test values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port, no
  domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits a BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL.
