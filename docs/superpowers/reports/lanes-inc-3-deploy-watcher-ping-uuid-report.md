Outcome: done

# Report: lanes-inc-3-deploy-watcher-ping-uuid

This is the code half of ADR-0059 increment 3's deploy-watcher cut-over, step 6 in
`docs/operations/deploy.md` (lines 347-348). The lane's compose now names its ping-URL secret's
BWS id. The change follows the landing ledger's `531d42c2` (#646), which was itself modeled on
execution-watch's.

- Base: `origin/main` at `b91eb83c`. The handoff read its facts at `3df369b2`, and each one still
  holds at `b91eb83c`.
- Branch: `hq/lanes-inc-3-deploy-watcher-ping-uuid`.
- PR: #680, ready for review, not merged. The code commit is `cc363563`; the PR head moves to the
  report commit that follows it.

## What I built

- `lanes/deploy-watcher/compose.yaml`
  - Lines 9-14: the header paragraph that said the id was empty is replaced. It now describes the
    id, the secret, and its readers in the same terms as `lanes/execution-watch/compose.yaml`
    lines 5-9 and `lanes/landing-ledger/compose.yaml`. I kept the existing sentence about
    `LANE_SCHEDULE_ENABLED`.
  - Line 22: `SDS_DEADMAN_PING_URL_UUID: 4f7f3d8b-a83e-4ae5-a665-b4ff0004b875`.
- `.bws-secrets.toml` lines 331-342: a new `[[secret]]` entry with `name = "deploy-watcher-ping-url"`,
  `project = "Rotation / Keepers"`, and `lanes = ["deploy-watcher"]`, written in the style of
  `landing-ledger-ping-url` (lines 318-329). The entry leaves out "until then it holds the
  inbox's empty marker" because step 5 is done.
- `tests/scripts/test_deploy_lanes.py`
  - Line 1193: `deploy-watcher` is removed from `placeholders`.
  - Line 1208: the named-lane assertion now requires
    `{"deploy-watcher", "execution-watch", "landing-ledger"}`. As a result, the loop that ties
    each compose's id to a `.bws-secrets.toml` entry for that lane covers the deploy watcher.
- `tests/scripts/test_run_deploy_watcher.py`
  - Line 285: the docstring no longer calls the compose's value empty.
  - Lines 354-355: the compose test expects the id.
  - Lines 377-388: the manifest test expects the ping-URL secret as the lane's fourth secret. The
    launcher check exempts the ping-URL secret, as the landing ledger's test does, because its id
    reaches the launcher from the compose through `lane-run`.
- `tests/rotation_worker/test_deploy_watcher_rows.py` lines 82-83: the token row's declared reach
  now equals the manifest's lane secrets, and the ping keeper is one of them. This replaces
  `_lane_secrets() | {ping_keeper}`, the same edit #646 made in `test_landing_ledger_rows.py`.
  Without it the test fails, because the keeper is now in the manifest.
- `docs/operations/deploy.md` lines 330-335: this is the stale line from item 4. It said that the
  compose leaves the id empty and listed three `.bws-secrets.toml` secrets. It now names the
  ping-URL secret and the id. I left the numbered cut-over list (lines 338-350) as it was. This
  diff doesn't make any of its steps stale, and step 6 completes only when HQ releases the change.

No ADR, `.cred-consumers.toml`, launcher, `lane-run`, or `sds-deadman.sh` change. No other lane's
compose changed.

## Item 5 confirmations (no change made)

- `lane-run` passes the variable: `scripts/lane_run.py:46` reads
  `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`, and line 10 of its docstring calls the
  variable "a BWS UUID, not a secret".
- `scripts/sds-deadman.sh` reads the URL by that id with the lane's own token:
  - `sds_deadman_resolve` (lines 125-128) calls `sds_deadman_resolve_by_uuid` when
    `SDS_DEADMAN_PING_URL_UUID` is set.
  - `sds_deadman_resolve_by_uuid` (lines 103-119) requires `BWS_ACCESS_TOKEN` (lines 105-107), then
    reads `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` (line 109). The one-argument form keeps the
    token in the environment. The function never prints the URL and accepts only an `https://` value
    with no whitespace, quote, or backslash.

## Keeper-entry convention

`.bws-secrets.toml` line 323, in the `landing-ledger-ping-url` entry, says: "The lane token's own
keeper isn't listed here: only the executor reads it, by the id its `.cred-consumers.toml` row
names (`landing-ledger-bws-token`)." The landing ledger has no entry for its own token keeper, so
I added none for `deploy-watcher-bws-token`. The new entry repeats the same sentence for this lane
(line 336).

## Tests and checks

- Targeted run (`test_deploy_lanes.py`, `test_run_deploy_watcher.py`,
  `test_deploy_watcher_rows.py`): 122 passed.
- `make check` on the committed tree with database `orchestrator_test_hq_w_114`, `PGTZ=UTC`, and
  the fixture `SECURITY_STANDARDS_DIR`: exit 0. It collected 14716 items: 14715 passed and 1 was
  skipped. Lint and type checks reported "All checks passed!".
- Security scan from the repo root: 0 BLOCK, 2 WARN, and 1 INFO. Neither WARN is from this diff.
  One reports that the glob `*-migration/` isn't gitignored. The other reports the undeclared UUID
  `aaff45ca-…`, which this diff doesn't touch. The INFO is the standing least-privilege judgment
  item.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/deploy-watcher/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/scripts/test_run_deploy_watcher.py`
- `tests/rotation_worker/test_deploy_watcher_rows.py`
- This report.

## Self-review

- The diff matches #646 one for one: compose header and value, manifest entry, guard,
  compose test, manifest test, rows test, and the deploy.md paragraph.
- The handoff didn't name `test_deploy_watcher_rows.py`, but item 3 calls for updating "any
  other test" the change affects, and this test failed once the keeper joined the manifest. HQ
  gate G-28 already granted the keeper to the lane (`GRANTED`), so this edit doesn't widen any
  reach.
- `test_run_deploy_watcher.py::test_without_a_ping_url_secret_a_pass_pings_nothing` and the
  `MAC` dictionary still pass an empty value. That is deliberate: they test the fall-through path,
  not the compose.

## Concerns

None. The four lanes still listed in `placeholders` (membership-check, pin-watcher,
revision-watcher, and unit-verifier) are other handoffs' work.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): no logging changes.
- Code Brain rule 4 (never swallow an exception silently): I added no new tests and no
  exception handling. The edited assertions fail loudly and name the lane or UUID.
- Code Brain rule 5 (never log secrets): no log line or test output prints a ping URL, the check's
  ping UUID, or a token. The committed value is the BWS secret id, which is distinct from the ping
  URL's UUID, and every comment calls it "an id, not a secret and not the ping URL".
- Infra Brain 591, 592, and 593: the diff contains no BWS token and no inline `BWS_ACCESS_TOKEN`
  value. The compose maps `${DEPLOY_WATCHER_BWS_ACCESS_TOKEN}` only. The scan reports 0 BLOCK.
- Infra Brain 1032 (exposure means rotate): I saw no secret value during this work.
- Infra Brain 212 (HTTPS for public FQDNs): doesn't apply, because the lane serves nothing (no port,
  no domain).
- Infra Brain 2890 (never commit a ping URL): the diff commits only the BWS secret id. No
  `https://healthcheck.alobar.net/ping/...` string appears in it. The test fixture's
  `https://hc.example/ping/deploy-watcher` was already in the file, and this diff doesn't change it.
