Outcome: done

# Report: lanes-inc-3-landing-ledger-ping-uuid

ADR-0059 increment 3, the landing ledger's cut-over, act (e): the lane's compose names its ping-URL
secret.

- **PR:** AlobarQuest/orchestrator#646 (draft), branch `hq/lanes-inc-3-landing-ledger-ping-uuid`
- **Head:** the commit that adds this report, on top of the code commit
  `a3ff7834511aebfd585f2d7e3a5067c6d417eb99`
- **Base:** `5145adff4093133bbe67d9dc97c1d6d951947c05`

## The analog PR

The handoff names execution-watch's change as "PR #630 (`4bee0f6f`, handoff 3d-2b)". That commit
is the rotation executor's lane-token and ping-URL types (`lanes-inc-2-executor`), which touches no
compose or manifest. The execution-watch compose change is #638 (`13814b17`, "execution-watch's
compose names its ping-URL secret", report `lanes-inc-2-ping-uuid-report.md`). I followed #638
throughout. #638 is also the PR that added `test_every_lanes_ping_url_secret_is_listed_for_that_lane`.

## What changed

1. **`lanes/landing-ledger/compose.yaml`**
   - Line 21: `SDS_DEADMAN_PING_URL_UUID: ebabdd63-2eff-4b8e-99b7-b4fd01754eae`, a literal, with
     the `PLACEHOLDER` comment dropped.
   - Lines 8-13: the header's `PLACEHOLDER:` paragraph is replaced with #638's shape: the value is
     the BWS secret id of `landing-ledger-ping-url` in `Rotation / Keepers`, an id, not a secret
     and not the ping URL; `lane-run` passes it to the launcher, whose dead-man helper reads the URL
     with the lane's own token. Its last sentence differs from execution-watch's: since this
     secret already holds the URL (unit `2695999c-…` completed `store`), it says the executor
     writes the URL from its inbox, and that while the secret holds the empty marker the helper
     pings nothing, rather than "until the executor writes the URL".
2. **`.bws-secrets.toml`**
   - Lines 288-299: a `[[secret]]` entry `uuid = "ebabdd63-…"`, `name = "landing-ledger-ping-url"`,
     `project = "Rotation / Keepers"`, `lanes = ["landing-ledger"]`, commented in the style of
     `execution-watch-ping-url` (created 2026-11-09 by `rotation-worker create-keeper`).
   - **No entry for `landing-ledger-bws-token`.** The convention is stated at
     `.bws-secrets.toml:267-269` in the `execution-watch-ping-url` comment: "The lane token's own
     keeper isn't listed here: only the executor reads it, by the id its `.cred-consumers.toml` row
     names". The new entry repeats it at lines 293-294, naming the `landing-ledger-bws-token` row.
3. **Stale comments**
   - `.bws-secrets.toml:280-281` (`lanes-read-app-private-key`): "This field names what the lane's
     account is to be granted; no grant exists until the lane's cut-over" becomes "The
     `landing-ledger` machine account has a direct read grant on it (made 2026-11-09 at the lane's
     cut-over)."
   - `docs/operations/deploy.md:317-322`, an operations page (not an ADR): the landing-ledger
     paragraph said the compose leaves `SDS_DEADMAN_PING_URL_UUID` empty as a placeholder and a
     pass logs "alerting disabled this run". It now says the lane's secrets include the ping-URL
     secret and the compose names its id. `deploy.md:329-330`, cut-over list item 3 ("the
     ping-URL secret's id in the compose … and in `.bws-secrets.toml`, merged and served") becomes
     "The orchestrator serving the release whose compose and `.bws-secrets.toml` name the ping-URL
     secret's id". #638 made the same kind of edit to `deploy.md`.
   - `tests/scripts/test_run_landing_ledger.py:222`: the docstring of
     `test_without_a_ping_url_secret_a_pass_pings_nothing` opened "The compose's placeholder:". It
     now states the behaviour alone. The test itself is unchanged: it drives the launcher with an
     explicit empty value, which is still the helper's fall-through path (and the Mac path).
   - No ADR line changed.

## Item 4: the value reaches the dead-man helper

- `scripts/lane_run.py:46`: `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`, and
  `lane_environment` (`scripts/lane_run.py:67-74`) copies each `COPIED` name the source sets into
  the launcher's environment, beside the fixed `PATH` and `HOME`.
- `scripts/run-landing-ledger.sh:71-72` sources `scripts/sds-deadman.sh` and arms
  `sds-landing-ledger`.
- `scripts/sds-deadman.sh:126-128`: `sds_deadman_resolve` takes `sds_deadman_resolve_by_uuid` when
  `SDS_DEADMAN_PING_URL_UUID` is non-empty. `scripts/sds-deadman.sh:103-118`: that function
  refuses without `BWS_ACCESS_TOKEN` (line 105), and reads the URL with
  `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` (line 109), the one-argument form that uses the
  environment's `BWS_ACCESS_TOKEN`, the lane's own token (`scripts/sds-bws.sh:57-59`).

Both are present, so no question was needed.

## Item 5: tests

- `tests/scripts/test_deploy_lanes.py::test_every_lanes_ping_url_secret_is_listed_for_that_lane`
  (added by #638) had grown a `placeholders = {"landing-ledger"}` allowance that skipped an empty
  value. I removed the allowance, which returns the loop to #638's shape: an empty value is in
  `named` and isn't in the manifest, so it fails. I added
  `assert {"execution-watch", "landing-ledger"} <= set(named)` so the guard covers landing-ledger by
  name, and dropped the `assert named` it makes redundant.
- `tests/scripts/test_run_landing_ledger.py::test_the_compose_meets_the_add_refusals_and_maps_nothing_else`
  asserted the value `""`; it now asserts the id.
- `tests/scripts/test_run_landing_ledger.py::test_the_repositorys_manifest_lists_the_ledgers_secrets`
  now expects the ping-URL secret third, and exempts it from "the launcher names each secret",
  as execution-watch's test does.
- **Mutation:** with the compose value set back to `""`, the guard failed
  (`1 failed`); restoring the file and clearing `__pycache__` returned it to green.

## Checks run

All with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` at `orchestrator_test_hq_w_65`,
`SECURITY_STANDARDS_DIR=$PWD/tests/fixtures/security-standards`, `PGTZ=UTC`, after
`uv sync --frozen` and `alembic upgrade head` (exit 0), as the Quality job does.

- Focused: `test_deploy_lanes.py`, `test_run_landing_ledger.py`, `test_lane_run.py`,
  `test_sds_deadman.py`: 218 passed.
- `make check` (the Quality job's step): ruff and pyright clean
  ("All checks passed!", "0 errors, 0 warnings"); pytest **14,208 collected: 14,207 passed,
  1 skipped**, 0 failed. A first run failed on one E501 in my docstring edit, which I fixed.
- Security scan from the repo root: no BLOCK finding. Two WARNs predate this change and don't name
  its ids (`*-migration/` gitignore glob; undeclared `aaff45ca-…`).
- `git diff` holds no `healthcheck.alobar.net/ping` string.

## Files changed

- `lanes/landing-ledger/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/scripts/test_run_landing_ledger.py`
- `docs/superpowers/reports/lanes-inc-3-landing-ledger-ping-uuid-report.md` (this report)

## Self-review

- The diff commits one BWS secret id, `ebabdd63-…`, already in `.cred-consumers.toml`; it names the
  token keeper only as `landing-ledger-bws-token`. It commits no ping URL, no check UUID, and no
  token.
- The guard edit makes the check stricter, not looser: no lane may leave the value empty.
- I didn't run `/code-review`; the diff is comment, data, and test-expectation edits, mirrored from
  #638.

## Concerns

- The handoff's analog-PR pointer is wrong (#630 rather than #638); see "The analog PR".
- `.bws-secrets.toml:267-269` (execution-watch's entry) says the token keeper's registry row lands
  "once the lane's cut-over lands that row"; that row exists (`.cred-consumers.toml:689`). It was
  made stale by an earlier change, not this one or G-18, so I left it.
- The `deploy.md` cut-over list's items 1 and 2 may already be done (the registry rows landed in
  #644 and #645); I changed only item 3, which this change addresses.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging changes; the diff touches no
  log call.
- **Code Brain rule 4 (never swallow an exception silently):** I added no test; the edited tests
  catch nothing, and each assertion fails loudly with the lane or value it checked.
- **Code Brain rule 5 (never log secrets):** no test prints a ping URL, a check's ping UUID, or a
  token. Assertion messages name a lane or a BWS secret id only; the secret id `ebabdd63-…` is kept
  distinct from any ping URL's UUID, and the comments say it's an id, not the ping URL.
- **Infra Brain 591, 592, 593:** no BWS token in tracked files or history (the scan shows no BLOCK);
  no inline `BWS_ACCESS_TOKEN` value (the compose keeps `${LANDING_LEDGER_BWS_ACCESS_TOKEN}`); test
  values are the existing obvious placeholders (`a-lane-token`, `33333333-…`), none token-shaped.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during this work.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; the lane serves nothing, with no
  port, domain, or labels.
- **Infra Brain 2890 (never commit a ping URL):** the diff commits a BWS secret id only; it holds no
  `https://healthcheck.alobar.net/ping/...` string (checked with `git diff | grep`).
