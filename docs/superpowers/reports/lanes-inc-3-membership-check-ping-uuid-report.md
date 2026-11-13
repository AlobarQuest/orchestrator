Outcome: done

# Report: lanes-inc-3-membership-check-ping-uuid

ADR-0059 increment 3, cut-over step 6 in `docs/operations/deploy.md`, code half, for the
membership-check lane. This is the change #683 (`54982342`) made for three lanes, applied to the
last lane in `test_deploy_lanes.py`'s `placeholders`.

- Base: `origin/main` at `17f02b32`. Every handoff line reference matched at that commit.
- Branch: `hq/lanes-inc-3-membership-check-ping-uuid`.
- PR: #685, open and ready for review, not merged.
- Code commit: `790c6d27`, which `make check` ran on. The report commit follows it on the same
  branch.

## What I built

The keeper id `5fc44897-b4df-44ae-8491-b5000129d9be` is a BWS secret id: not a secret, and not a
ping URL or a check's ping UUID. It matches `.cred-consumers.toml`'s `membership-check-ping-url`
row (`bws_uuid`, line 1052).

1. `lanes/membership-check/compose.yaml`
   - Line 24: `SDS_DEADMAN_PING_URL_UUID` holds the literal keeper id.
   - Lines 10-16: the header is rewritten in the shape #683 gave the other composes, keeping this
     lane's own fact that the pass still runs when nothing pings.
2. `.bws-secrets.toml`, lines 382-394: one `[[secret]]` entry after `pin-watcher-ping-url`, with
   `name = "membership-check-ping-url"`, `project = "Rotation / Keepers"`, and
   `lanes = ["membership-check"]`. Its comment follows the three entries #683 added.

   The keeper-entry convention line is: "The lane token's own keeper isn't listed here: only the
   executor reads it, by the id its `.cred-consumers.toml` row names
   (`membership-check-bws-token`)."
3. `tests/scripts/test_deploy_lanes.py`, `test_every_lanes_ping_url_secret_is_listed_for_that_lane`
   - Lines 1189-1194: `placeholders` is `placeholders: set[str] = set()`, and the docstring says the
     set is empty while every lane's compose names its secret's id.
   - Line 1209: `membership-check` joins the `<= set(named)` set.
   - The guard stays: every named id must be in the lane's manifest secrets, and
     `assert blank == placeholders` stays.

   I kept the mechanism, empty, for the following reasons:
   - A lane added before its ping-URL secret exists still needs it. ADR-0059 line 2613 shows a new
     lane's change adding itself to the set.
   - Five step-6 lines in `deploy.md` (349, 377, 409, 447, and 482) name `placeholders`. Removing
     the name would have made all five stale.
   - Mutation check: with the compose value set back to `""`, the test fails on
     `assert blank == placeholders`. Restoring the value makes it pass again.
4. `tests/scripts/test_run_membership_check.py`
   - Line 397: the docstring now begins "An empty `SDS_DEADMAN_PING_URL_UUID`", because the compose
     isn't empty. The test is unchanged and still covers the dead-man helper's empty-id
     fallthrough, so no fallthrough coverage was removed.
   - Lines 457-458: the compose test asserts the literal id, with #683's comment.
   - Lines 494-505: the manifest test lists the ping-URL secret fourth. The launcher check exempts
     that id, with #683's comment, because the id reaches the launcher from the compose through
     `lane-run`.
   - Line 113 is unchanged. It is the `MAC` environment, the Mac path, which has no compose and no
     ping-URL secret. It doesn't assert the compose's value.
5. `tests/rotation_worker/test_membership_check_rows.py`, lines 93-94: #683's two-line form. The
   keeper is now in the lane's manifest secrets, and the declared reach equals them.
6. `docs/operations/deploy.md`, membership-check paragraph, lines 425-434 (reflowed):
   - The `.bws-secrets.toml` secrets list adds "its ping-URL secret". "The first two" (the Mac's
     reads) stays true.
   - "its compose leaves `SDS_DEADMAN_PING_URL_UUID` empty until the lane's ping-URL secret exists,
     so until then the container's pass still runs, pings nothing and says so" becomes the sentence
     #683 used: the compose names the variable, the BWS secret id of `membership-check-ping-url`,
     which the dead-man helper reads the URL from with the lane's own token.
   - Line 447 (step 6) is unchanged, as #683 left its lanes' step 6.

### Item 5 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`:
  - `lanes/lane-run:4` runs `scripts/lane_run.py`.
  - `scripts/lane_run.py:46` is `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`.
  - `scripts/lane_run.py:71`, in `lane_environment`, copies each `COPIED` name the source sets into
    the launcher's environment.
- `scripts/sds-deadman.sh` reads the URL by that id with the lane's own token:
  - `scripts/run-membership-check.sh:95` calls `sds_outcome_begin`.
  - That calls `sds_deadman_resolve` (`scripts/sds-deadman.sh:204`).
  - When the variable is set, that calls `sds_deadman_resolve_by_uuid` (`:126-127`).
  - That refuses when `BWS_ACCESS_TOKEN` is unset (`:105-107`), then calls
    `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` in the one-argument form (`:109`).
  - The one-argument form reads with the ambient `BWS_ACCESS_TOKEN`, which is the lane's token
    (`scripts/sds-bws.sh:57-59`).

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_membership_check.py`, and
  `test_membership_check_rows.py` had 137 passed.
- `make check` on `790c6d27`, database `orchestrator_test_hq_w_123`, exited 0:
  - Ruff: "All checks passed!"
  - Pyright: 0 errors.
  - Tests: 14736 items collected, 14735 passed, 1 skipped.
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO. The diff causes none of
  them:
  - WARN `bws.secret-files-gitignored`: `.gitignore` doesn't cover `*-migration/`.
  - WARN `bws.manifest-matches-usage`: the undeclared UUID is the organization id in
    `scripts/install-rotation-worker-launchd.sh:55`.
  - INFO `bws.least-privilege-scope`: a judgment finding.

  A control scan of a `git archive` of the base couldn't run, because the scanner needs a git
  repository.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/membership-check/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/scripts/test_run_membership_check.py`
- `tests/rotation_worker/test_membership_check_rows.py`
- `docs/superpowers/reports/lanes-inc-3-membership-check-ping-uuid-report.md` (this report)

## Self-review

- The diff follows #683 line for line, apart from the `placeholders` decision explained in the
  preceding section.
- No change to the launcher, `lane-run`, `sds-deadman.sh`, `.cred-consumers.toml`, or any ADR.
- ADR-0059 line 2613 records an earlier change that added `membership-check` to the placeholder
  lanes. It is history, not a stale fact, so I left it.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call.

## Concerns

None for this change. HQ's release of #685 is the served-release half of step 6.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the diff adds no test and no
  handler. The edited tests assert directly and catch nothing.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a
  check's ping UUID, or a token. The only id added is the keeper's BWS secret id, which is kept
  distinct from the ping URL's UUID in the compose comment, the manifest comment, and the test
  comment.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value. The compose keeps `${MEMBERSHIP_CHECK_BWS_ACCESS_TOKEN}`. The scan has
  0 BLOCK findings, and the diff adds no new test values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port, no
  domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits a BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL.
