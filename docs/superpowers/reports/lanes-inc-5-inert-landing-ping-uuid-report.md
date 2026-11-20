Outcome: done

# Report: lanes-inc-5-inert-landing-ping-uuid

ADR-0059 increment 5, step 3g-1: the code half of `docs/operations/deploy.md`'s inert-lander
cut-over item 6 (line 937 at base, "A served release whose compose names the ping-URL secret's id,
with `inert-landing` removed from `test_deploy_lanes.py`'s `placeholders`"). It follows #748
(`669c8a78`).

- Base: `origin/main` at `6161a866`. The handoff's line references matched at that commit.
- Branch: `hq/lanes-inc-5-inert-landing-ping-uuid`.
- PR: #757, open and ready for review (not a draft), not merged.
- Code head: `bcbe4f1e`, which `make check` ran on. The report commit follows it on the same branch.

## What I built

The keeper id `35557473-f80c-44e3-892a-b507013158b1` is a BWS secret id, not a secret, not a ping
URL, and not a check's ping UUID. It matches `.cred-consumers.toml`'s `inert-landing-ping-url` row
(`bws_uuid`) and `tests/rotation_worker/test_inert_landing_rows.py:43`.

1. `lanes/inert-landing/compose.yaml`:
   - Line 28: `SDS_DEADMAN_PING_URL_UUID: 35557473-f80c-44e3-892a-b507013158b1`.
   - Lines 13-18: the header is rewritten in #748's rotation-proposer shape. It says the helper
     reads the URL "on every pass, the bare one included", because this launcher calls
     `sds_deadman_arm` (`scripts/run-inert-landing.sh:134`), which skips only on `--dry-run`
     (`scripts/sds-deadman.sh:339`). That matches `deploy.md`'s "a bare run pings its check".
2. `.bws-secrets.toml` lines 531-543: one `[[secret]]` entry after `rotation-proposer-ping-url`, in
   its comment style: `name = "inert-landing-ping-url"`, `project = "Rotation / Keepers"`,
   `lanes = ["inert-landing"]`. The manifest didn't list the keeper before.
3. `tests/scripts/test_deploy_lanes.py`,
   `test_every_lanes_ping_url_secret_is_listed_for_that_lane`:
   - Line 1221 (base): `inert-landing` leaves `placeholders`, which is now `bump-driver`,
     `change-window`, `estate-landing`, and `follow-up-mint`. (`change-window` was in the set at
     base; the handoff's list didn't name it.)
   - Line 1237: `inert-landing` joins the `<= set(named)` set.
   - The docstring is unchanged: it describes `placeholders` in general, which stays true for the
     remaining lanes.
   - Mutation check: with the compose value set back to `""`, the test fails. With it restored, it
     passes.
4. `tests/rotation_worker/test_inert_landing_rows.py` lines 90-96: the docstring that said the
   keeper joins the manifest later is removed, and the asserts take #748's form: the keeper is in
   the lane's secrets, and the declared reach equals them (and `REACH`).
5. `tests/scripts/test_run_inert_landing.py`:
   - Line 159: unchanged. It is the harness's default environment, not a copy of the compose. With
     `BWS_ACCESS_TOKEN_BROAD` set to a stand-in, it deliberately runs the dead-man helper's
     empty-id fallthrough to the name lookup, which line 208 asserts. #748 kept both proposers'
     harness defaults the same way. No empty-id case was removed.
   - Lines 463-464: the compose assertion expects the literal keeper id, with #748's comment.
   - Lines 508-517: `test_the_repositorys_manifest_lists_the_lanes_secrets` lists the keeper beside
     the four secrets and exempts it from the launcher-names-it check, because its id reaches the
     launcher from the compose through `lane-run`. The handoff didn't name this test, but the
     manifest change makes it fail otherwise; this is #748's form.
   - Line 381: the docstring's "which the container's compose names once its secret exists"
     becomes "which the container's compose names".
   - The by-id path stays tested by
     `test_with_a_ping_url_named_the_healthchecks_key_is_never_read` (line 214), unchanged.
6. `docs/operations/deploy.md` lines 906-909: "the following four … joins them when it exists;
   until then … `placeholders`" becomes "its ping-URL secret and the following four", plus a
   sentence naming `inert-landing-ping-url` and the dead-man helper reading the URL on every pass.
   Item 6 (line 937) is unchanged, as #748 left its items. Item 7 already says "all five secrets".
   Lines 970 and 1051 still say "joins them when it exists"; they belong to lanes still in
   `placeholders`, so they stay true.

No ADR, launcher, `lane-run`, `sds-deadman.sh`, crontab, `chain_flags`, `.cred-consumers.toml`, or
other lane's compose changed.

### Item 7 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`:
  - `lanes/lane-run:4` runs `scripts/lane_run.py`.
  - `scripts/lane_run.py:52`: `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`.
  - `scripts/lane_run.py:83` copies each `COPIED` name into the launcher's environment.
  - `test_every_compose_variable_lane_run_must_pass_is_in_its_list` in
    `tests/scripts/test_run_inert_landing.py` passes.
- `scripts/sds-deadman.sh` reads the URL by that id with the lane's own token:
  - `sds_deadman_resolve` (`:124`) calls `sds_deadman_resolve_by_uuid` when the variable is set
    (`:126-127`).
  - That function refuses when `BWS_ACCESS_TOKEN` is unset (`:105-107`), then calls
    `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` in the one-argument form (`:109`).
  - The one-argument form reads with the ambient `BWS_ACCESS_TOKEN`, the lane's token
    (`scripts/sds-bws.sh:64-66`). Only the two-argument branch (`:59-62`) sets another token.
  - Caller: `scripts/run-inert-landing.sh:133-134` (`sds_deadman_arm`, which calls the resolver
    after the dry-run check at `sds-deadman.sh:339-343`).

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_inert_landing.py`, and `test_inert_landing_rows.py`
  had 142 passed.
- `make check` on `bcbe4f1e`, database `orchestrator_test_hq_w_245`, exited 0:
  - Ruff: "All checks passed!", 1032 files already formatted.
  - Pyright: 0 errors, 0 warnings.
  - Tests: 14709 items collected, 14708 passed, 1 skipped.
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO, the same three #748
  reported. The diff causes none of them:
  - WARN `bws.secret-files-gitignored`.
  - WARN `bws.manifest-matches-usage`.
  - INFO `bws.least-privilege-scope`.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/inert-landing/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/rotation_worker/test_inert_landing_rows.py`
- `tests/scripts/test_run_inert_landing.py`
- `docs/superpowers/reports/lanes-inc-5-inert-landing-ping-uuid-report.md` (this report)

## Self-review

- The diff follows #748's rotation-proposer form, because both launchers arm the dead-man switch
  on every pass. I read the inert launcher to confirm it.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call. I ran nothing in
  `~/Projects/orchestrator`.

## Concerns

- HQ's release of #757 is the served-release half of item 6.
- The test database `orchestrator_test_hq_w_245` still exists for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the diff adds no handler. The edited
  tests assert directly and catch nothing.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a check's
  ping UUID, or a token. The only id added is the keeper's BWS secret id, labeled "not a secret,
  not the ping URL" in the compose, the manifest, and the test.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value. The compose keeps `${INERT_LANDING_BWS_ACCESS_TOKEN}`. The scan has
  0 BLOCK findings, and the diff adds no test token values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port,
  no domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits one BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL.
