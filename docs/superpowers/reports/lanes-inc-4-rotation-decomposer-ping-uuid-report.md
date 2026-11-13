Outcome: done

# Report: lanes-inc-4-rotation-decomposer-ping-uuid

ADR-0059 increment 4, cut-over step 6 in `docs/operations/deploy.md`, code half, for the
rotation-decomposer lane. This is the change #692 (`b4a48f7c`) made for the change proposer.

- Base: `origin/main` at `5826fc32`. Every handoff line reference matched at that commit.
- Branch: `hq/lanes-inc-4-rotation-decomposer-ping-uuid`.
- PR: #697, open and ready for review, not merged.
- Code head: `5b5045c6`, which `make check` ran on. The report commit follows it on the same
  branch.

## What I built

The keeper id `936bbbf2-dc3b-4343-979a-b501013afc87` is a BWS secret id: not a secret, and not a
ping URL or a check's ping UUID. It matches `.cred-consumers.toml`'s `rotation-decomposer-ping-url`
row (`bws_uuid`, line 1152).

1. `lanes/rotation-decomposer/compose.yaml`
   - Line 28: `SDS_DEADMAN_PING_URL_UUID` holds the literal keeper id.
   - Lines 13-19: the header is rewritten in #692's shape, with this lane's own terms ("an acting
     pass", "a `--dry-run` pass pings nothing").
2. `.bws-secrets.toml`, lines 417-429: one `[[secret]]` entry after `change-proposer-ping-url`, with
   `name = "rotation-decomposer-ping-url"`, `project = "Rotation / Keepers"`, and
   `lanes = ["rotation-decomposer"]`. Its comment follows #692's entry, naming
   `rotation-decomposer-bws-token` as the token keeper the manifest doesn't list.
3. `tests/scripts/test_deploy_lanes.py`, `test_every_lanes_ping_url_secret_is_listed_for_that_lane`
   - Lines 1190-1194: the docstring drops "The rotation decomposer's keeper was created at HQ gate
     G-53; a later change writes its id." and says "No lane is waiting for its keeper." The
     placeholder mechanism and its description stay for a later lane.
   - Line 1196: `placeholders: set[str] = set()`.
   - Line 1215: `rotation-decomposer` joins the `<= set(named)` set.
   - Mutation check: with the compose value set back to `""`, the test fails on
     `assert blank == placeholders` (`{'rotation-decomposer'} == set()`). Restoring the value makes
     it pass again.
4. `tests/scripts/test_run_rotation_decomposer.py`
   - Line 197: the docstring begins "An empty `SDS_DEADMAN_PING_URL_UUID`", because the compose
     isn't empty. The test is unchanged and still covers the dead-man helper's empty-id
     fallthrough, so no fallthrough coverage was removed.
   - Lines 312-313: the compose test asserts the literal id, with #692's comment (shortened to fit
     the 100-column limit).
   - Lines 350-359: the manifest test lists the ping-URL secret fourth. The launcher check exempts
     that id, because the id reaches the launcher from the compose through `lane-run`; the
     docstring says so.
   - Lines 235, 260: unchanged. They are the Mac path's `SDS_DEADMAN_PING_URL_UUID=""`, which has no
     compose and doesn't assert the compose's value.
5. `tests/rotation_worker/test_rotation_decomposer_rows.py`, lines 99-100: #692's two-line form. The
   keeper is now in the lane's manifest secrets, and the declared reach equals them (and `GRANTED`).
6. `docs/operations/deploy.md`, rotation-decomposer paragraph, lines 503-512 (reflowed to 100
   columns):
   - The `.bws-secrets.toml` secrets list adds "its ping-URL secret". The Mac-identity sentence
     stays true.
   - "its compose leaves `SDS_DEADMAN_PING_URL_UUID` empty until the lane's ping-URL secret exists,
     so until then an acting pass in the container still runs, pings nothing and says so" becomes
     #692's sentence: the compose names the variable, the BWS secret id of
     `rotation-decomposer-ping-url`, which the dead-man helper reads the URL from with the lane's
     own token.
   - Step 6 (lines 524-525) is unchanged, as #692 left its lane's step 6.

No other stale comment: the `G-53` mentions left (`.cred-consumers.toml:1118`,
`test_rotation_decomposer_rows.py:46`) describe the grants, which are still true.

### Item 5 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`:
  - `lanes/lane-run:4` runs `scripts/lane_run.py`.
  - `scripts/lane_run.py:46` is `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`.
  - `scripts/lane_run.py:71`, in `lane_environment`, copies each `COPIED` name into the launcher's
    environment.
- `scripts/sds-deadman.sh` reads the URL by that id with the lane's own token:
  - `scripts/run-rotation-decomposer.sh:77-78` sources the helper and calls `sds_outcome_begin`.
  - That calls `sds_deadman_resolve` (`scripts/sds-deadman.sh:204`).
  - When the variable is set, that calls `sds_deadman_resolve_by_uuid` (`:126-127`).
  - That refuses when `BWS_ACCESS_TOKEN` is unset (`:105-107`), then calls
    `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` in the one-argument form (`:109`).
  - The one-argument form reads with the ambient `BWS_ACCESS_TOKEN`, the lane's token
    (`scripts/sds-bws.sh:57-59`).

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_rotation_decomposer.py`, and
  `test_rotation_decomposer_rows.py` had 132 passed.
- `make check` on `5b5045c6`, database `orchestrator_test_hq_w_142`, exited 0:
  - Ruff: "All checks passed!", 1061 files already formatted. The first run failed on one E501 in
    my test comment; commit `5b5045c6` shortened it.
  - Pyright: 0 errors.
  - Tests: 14851 items collected, 14850 passed, 1 skipped.
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO, the same three #692
  reported. The diff causes none of them:
  - WARN `bws.secret-files-gitignored`.
  - WARN `bws.manifest-matches-usage`.
  - INFO `bws.least-privilege-scope`: a judgment finding.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/rotation-decomposer/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/scripts/test_run_rotation_decomposer.py`
- `tests/rotation_worker/test_rotation_decomposer_rows.py`
- `docs/superpowers/reports/lanes-inc-4-rotation-decomposer-ping-uuid-report.md` (this report)

## Self-review

- The diff follows #692 line for line, apart from the lane's own wording ("acting pass",
  `--dry-run`) and the shortened test comment.
- No change to the launcher, `lane-run`, `sds-deadman.sh`, `.cred-consumers.toml`, any ADR, or any
  other lane's compose.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call.

## Concerns

None for this change. HQ's release of #697 is the served-release half of step 6. The test database
`orchestrator_test_hq_w_142` still exists for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the diff adds no test and no
  handler. The edited tests assert directly and catch nothing.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a
  check's ping UUID, or a token. The only id added is the keeper's BWS secret id, kept distinct
  from the ping URL's UUID in the compose comment, the manifest comment, and the test comment.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value. The compose keeps `${ROTATION_DECOMPOSER_BWS_ACCESS_TOKEN}`. The scan
  has 0 BLOCK findings, and the diff adds no new test values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port, no
  domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits a BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL.
