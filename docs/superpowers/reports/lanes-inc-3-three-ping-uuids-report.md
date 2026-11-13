Outcome: done

# Report: lanes-inc-3-three-ping-uuids

This is the code half of ADR-0059 increment 3's cut-over step 6 (`docs/operations/deploy.md`) for
three lanes: the revision watcher, the unit verifier, and the pin watcher. Each lane's compose now
names its ping-URL secret's BWS id. The change copies the deploy watcher's `aff18d25` (#680) lane
for lane.

- Base: `origin/main` at `06cfea7c`, the commit the handoff read its facts at. Each keeper id in the
  handoff's table matches its `.cred-consumers.toml` row's `bws_uuid` (lines 902, 952 and 1002).
- Branch: `hq/lanes-inc-3-three-ping-uuids`.
- PR: #683, ready for review, not merged. The code commits are `da39bd12` and `e0b81a5f` (a count fix in `deploy.md`); the PR head moves to the
  report commit that follows it.

## What I built

Line numbers are in the post-change files.

Per lane, `lanes/<lane>/compose.yaml`:

- `revision-watcher`: lines 12-17 replace the header paragraph that said the id was empty, in the
  shape #680 gave `lanes/deploy-watcher/compose.yaml`. Line 26:
  `SDS_DEADMAN_PING_URL_UUID: 9b2722ea-8ec1-425b-ac2e-b4ff00cba931`.
- `unit-verifier`: lines 11-16 replace the header paragraph. Line 25:
  `SDS_DEADMAN_PING_URL_UUID: 1cfc8352-c960-4afc-bd49-b4ff00e8783e`.
- `pin-watcher`: lines 11-16 replace the header paragraph. Line 25:
  `SDS_DEADMAN_PING_URL_UUID: 872684c0-899e-43b7-9c2d-b50000e88b21`.

`.bws-secrets.toml` lines 344-381: three `[[secret]]` entries, `revision-watcher-ping-url`,
`unit-verifier-ping-url`, and `pin-watcher-ping-url`, each with `project = "Rotation / Keepers"` and
`lanes = ["<lane>"]`, worded as the `deploy-watcher-ping-url` entry (lines 331-342).

`tests/scripts/test_deploy_lanes.py`:

- Line 1192: `placeholders = {"membership-check"}`.
- Lines 1203-1210: the named-lane assertion requires all six lanes. The loop that ties each
  compose's id to a manifest entry for that lane now covers the three.

Per lane, `tests/scripts/test_run_<lane>.py` (same three edits in each, as #680):

- The `test_without_a_ping_url_secret_a_pass_pings_nothing` docstring no longer calls the
  compose's value empty (revision watcher 294, unit verifier 306, pin watcher 315).
- The compose test expects the id (revision watcher 389-390, unit verifier 380-381, pin watcher
  428-429).
- The manifest test expects the ping-URL secret as the lane's last secret, and the launcher check
  exempts it because its id reaches the launcher through `lane-run` (revision watcher 433-446,
  unit verifier 424-435, pin watcher 478-489).

Per lane, `tests/rotation_worker/test_<lane>_rows.py` (revision watcher 88-89, unit verifier 91-92,
pin watcher 92-93): the token row's declared reach now equals the manifest's lane secrets, and the
ping keeper is one of them, replacing `_lane_secrets() | {ping_keeper}`. This is #680's edit to
`test_deploy_watcher_rows.py`. Without it the tests fail, because each keeper is now in the
manifest.

`docs/operations/deploy.md` (item 4, the only stale lines): each lane paragraph said the compose
leaves the id empty and listed the `.bws-secrets.toml` secrets without the ping-URL secret. Each
now names the ping-URL secret among them and names the id, in #680's words.

- Revision watcher, lines 360-364. "on the Mac … still read them" became "the first four" (the read-only App's key is read only in the container, `scripts/run-revision-watcher.sh` lines 216-218), so the
  Mac's reach isn't read as including the ping-URL secret.
- Pin watcher, lines 392-396.
- Unit verifier, lines 465-470.

I left the numbered cut-over lists unchanged: step 6 completes only when HQ releases this.

To find stale lines, I grepped the whole repository (excluding `docs/superpowers/reports` and
`docs/history`) for `empty`, `placeholder`, `pings nothing`, `alerting disabled`, and "later change
writes" near the three lane names or the ping-URL variable. That search covered ADR-0059, the
nested `CLAUDE.md` files, the launchers, and the rows tests' `GRANTED` comments. It found no other
stale line for these lanes; ADR-0059 line 2614 and `deploy.md` line 433 concern `membership-check`.

No ADR, `.cred-consumers.toml`, launcher, `lane-run`, or `sds-deadman.sh` change. The
membership check's compose and tests are unchanged.

## Item 5 confirmations (no change made)

- `lane-run` passes the variable: `scripts/lane_run.py:46` reads
  `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`, line 71 copies each, and line 10 of the
  docstring calls it "a BWS UUID, not a secret".
- `scripts/sds-deadman.sh` reads the URL by that id with the lane's own token:
  `sds_deadman_resolve` (line 124) calls `sds_deadman_resolve_by_uuid` when
  `SDS_DEADMAN_PING_URL_UUID` is set (lines 126-127). `sds_deadman_resolve_by_uuid` (line 103)
  requires `BWS_ACCESS_TOKEN` (lines 105-106), then reads
  `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` (line 109), the one-argument form that keeps the
  token in the environment.

## Keeper-entry convention

The `deploy-watcher-ping-url` entry (`.bws-secrets.toml` lines 336-337) says: "The lane token's own
keeper isn't listed here: only the executor reads it, by the id its `.cred-consumers.toml` row
names (`deploy-watcher-bws-token`)." I added no entry for `<lane>-bws-token`, and each new entry
repeats that sentence with its own lane's token keeper.

## Tests and checks

- Targeted run (`test_deploy_lanes.py`, the three `test_run_<lane>.py`, and the three
  `test_<lane>_rows.py`): 247 passed.
- `make check` on the committed tree (`e0b81a5f`, after the deploy.md count fix), database `orchestrator_test_hq_w_119`, `PGTZ=UTC`, fixture
  `SECURITY_STANDARDS_DIR`: exit 0. Pytest collected 14736 items: 14735 passed and 1 was skipped. Lint, format, and type checks reported no errors.
- Security scan from the repo root: exit 0; 0 BLOCK, 2 WARN, 1 INFO. Both WARNs are pre-existing and
  are the same two #680 reported: the glob `*-migration/` isn't gitignored, and an undeclared UUID
  (`aaff45ca-…`) this diff doesn't touch. The INFO is the standing least-privilege judgment item.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/revision-watcher/compose.yaml`, `lanes/unit-verifier/compose.yaml`,
  `lanes/pin-watcher/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/scripts/test_run_revision_watcher.py`, `tests/scripts/test_run_unit_verifier.py`,
  `tests/scripts/test_run_pin_watcher.py`
- `tests/rotation_worker/test_revision_watcher_rows.py`,
  `tests/rotation_worker/test_unit_verifier_rows.py`, `tests/rotation_worker/test_pin_watcher_rows.py`
- This report.

## Self-review

- The diff is #680's seven-file change applied to each lane: compose header and value, manifest
  entry, guard, compose test, manifest test, rows test, and the deploy.md paragraph.
- The handoff didn't name the rows tests, but item 3 covers every test the change affects, and
  each failed once its keeper joined the manifest. Each test's `GRANTED` set already contains the
  keeper, so the edit widens no reach.
- Each `test_without_a_ping_url_secret_a_pass_pings_nothing` and each Mac fixture still pass an
  empty value on purpose: they test the fall-through path, not the compose.

## Concerns

None. `membership-check` is the one lane left in `placeholders`; its ping URL isn't issued yet.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): no logging changes.
- Code Brain rule 4 (never swallow an exception silently): I added no tests and no exception
  handling. The edited assertions fail loudly and name the lane or UUID.
- Code Brain rule 5 (never log secrets): no log line or test output prints a ping URL, a check's
  ping UUID, or a token. The committed values are BWS secret ids, distinct from the checks' ping
  UUIDs, and every comment calls each "an id, not a secret and not the ping URL".
- Infra Brain 591, 592, and 593: the diff holds no BWS token and no inline `BWS_ACCESS_TOKEN` value.
  Each compose maps only `${<LANE>_BWS_ACCESS_TOKEN}`. The scan reports 0 BLOCK.
- Infra Brain 1032 (exposure means rotate): I saw no secret value during this work.
- Infra Brain 212 (HTTPS for public FQDNs): doesn't apply; the lanes serve nothing (no port, no
  domain).
- Infra Brain 2890 (never commit a ping URL): the diff commits only BWS secret ids. No
  `https://healthcheck.alobar.net/ping/...` string appears in it.
