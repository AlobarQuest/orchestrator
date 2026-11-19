Outcome: done

# Report: lanes-inc-4-proposers-ping-uuid

ADR-0059 increment 4, steps 3f-4 and 3f-5: the code half of `docs/operations/deploy.md`'s
bump-proposer item 7 (line 738, "A served release whose compose names the ping-URL secret's id, with
`bump-proposer` removed from `test_deploy_lanes.py`'s `placeholders`") and rotation-proposer item 8
(line 836, the same for `rotation-proposer`). It follows #732 (`cc299052`).

- Base: `origin/main` at `2f809005`. Every handoff line reference matched at that commit.
- Branch: `hq/lanes-inc-4-proposers-ping-uuid`.
- PR: #748, open and ready for review (not a draft), not merged.
- Code head: `5dea4dfe`, which `make check` ran on. The report commit follows it on the same branch.

## What I built

The two keeper ids are BWS secret ids, not secrets and not ping URLs or check ping UUIDs. They match
`.cred-consumers.toml`'s `bws_uuid` lines 1287 (`786f75e2-…`, bump) and 1338 (`506a5436-…`,
rotation).

1. Compose files:
   - `lanes/bump-proposer/compose.yaml` line 32: `SDS_DEADMAN_PING_URL_UUID:
     786f75e2-3d02-4667-8607-b50601312a9d`. Lines 17-22: the header is rewritten in #732's shape.
     It pings on a `--submit` pass and a bare pass pings nothing, because the launcher's
     `sds_outcome_begin` is an outcome alarm (`scripts/run-bump-proposer.sh:190`).
   - `lanes/rotation-proposer/compose.yaml` line 34: `SDS_DEADMAN_PING_URL_UUID:
     506a5436-4643-4040-8a8b-b50601312be2`. Lines 19-24: the header is rewritten the same way.
     It reads the URL "on every pass, the bare one included", because this launcher uses
     `sds_deadman_arm` (`scripts/run-rotation-proposer.sh:128`), which skips only on `--dry-run`
     (`scripts/sds-deadman.sh:339`).
2. `.bws-secrets.toml` lines 500-524: two `[[secret]]` entries after `work-carrier-ping-url`, in its
   comment style, each `project = "Rotation / Keepers"`, with `lanes = ["bump-proposer"]` and
   `lanes = ["rotation-proposer"]`. The manifest didn't list either keeper before.
3. `tests/scripts/test_deploy_lanes.py`, `test_every_lanes_ping_url_secret_is_listed_for_that_lane`:
   - Lines 1196-1198: the docstring drops the G-75 sentence. It still describes `placeholders`,
     which is true for the four remaining lanes.
   - Lines 1200-1205: `placeholders` is `bump-driver`, `estate-landing`, `follow-up-mint`, and
     `inert-landing`.
   - Lines 1217 and 1226: `bump-proposer` and `rotation-proposer` join the `<= set(named)` set.
   - Mutation check: with either compose value set back to `""`, the test fails on
     `assert blank == placeholders`. With the value restored, it passes.
4. `tests/rotation_worker/test_proposer_lane_rows.py`:
   - Line 40: the `Lane.reach` comment no longer says the keeper isn't listed. It reads "The
     `.bws-secrets.toml` secrets the lane reads, its ping URL's keeper among them."
   - Lines 139-140: #732's form. The keeper is in the lane's secrets, and the declared reach equals
     them (and `lane.reach`).
5. Launcher tests:
   - `tests/scripts/test_run_bump_proposer.py:180` and `tests/scripts/test_run_rotation_proposer_lane.py:179`:
     unchanged. Each is the harness's default environment, not a copy of the compose. With
     `BWS_ACCESS_TOKEN_BROAD` set to a stand-in, each deliberately runs the dead-man helper's
     empty-id fallthrough to the name lookup, and tests assert that path (bump line 220,
     "the dead-man helper's Healthchecks key, by the broad stand-in"; rotation line 218). #732 kept
     its harness default the same way. No empty-id case was removed.
   - `test_run_bump_proposer.py:553-554` and `test_run_rotation_proposer_lane.py:720-721`: the
     compose assertions now expect the literal keeper id, with #732's comment.
   - `test_run_bump_proposer.py:601-611` and `test_run_rotation_proposer_lane.py:769-779`:
     `test_the_repositorys_manifest_lists_the_lanes_secrets` lists the keeper beside the lane's own
     secrets and exempts it from the launcher-names-it check, because its id reaches the launcher
     from the compose through `lane-run`. This is #732's form. The handoff didn't name these tests,
     but the manifest change makes them fail otherwise.
   - The by-id path in the rotation harness stays tested by
     `test_with_a_ping_url_named_the_fence_reads_only_that_secret` (line 378), unchanged.
6. `docs/operations/deploy.md`:
   - Bump proposer, lines 689-692: "the following seven … joins them when it exists; until then …
     `placeholders`" becomes "its ping-URL secret and the following seven", plus #732's sentence
     naming `bump-proposer-ping-url` and the dead-man helper.
   - Rotation proposer, lines 792-795: the same change for "the following four". It keeps "the
     launcher reads the four on `--submit` only; a dry run reads none of them" and says the helper
     reads the URL on every pass.
   - Items 7 and 8 are unchanged, as #732 left its item 6. Items 8 ("all eight secrets") and 9
     ("all five secrets") already counted the keeper.
   - Lines 907, 970, and 1051 still say "joins them when it exists". They belong to lanes still in
     `placeholders`, so they stay true.

No ADR, launcher, `lane-run`, `sds-deadman.sh`, crontab, `.cred-consumers.toml`, or other lane's
compose changed.

### Item 7 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`:
  - `lanes/lane-run:4` runs `scripts/lane_run.py`.
  - `scripts/lane_run.py:52`: `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`.
  - `scripts/lane_run.py:83` copies each `COPIED` name into the launcher's environment.
  - Each lane's `test_every_compose_variable_lane_run_must_pass_is_in_its_list` passes.
- `scripts/sds-deadman.sh` reads the URL by that id with the lane's own token:
  - `sds_deadman_resolve` (`:124`) calls `sds_deadman_resolve_by_uuid` when the variable is set
    (`:126-127`).
  - That function refuses when `BWS_ACCESS_TOKEN` is unset (`:105-107`), then calls
    `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` in the one-argument form (`:109`).
  - The one-argument form reads with the ambient `BWS_ACCESS_TOKEN`, the lane's token
    (`scripts/sds-bws.sh:63-65`). Only the two-argument branch (`:59-62`) sets another token.
  - Callers: `scripts/run-bump-proposer.sh:189-190` (`sds_outcome_begin`) and
    `scripts/run-rotation-proposer.sh:127-128` (`sds_deadman_arm`, which calls the resolver at
    `sds-deadman.sh:343`).

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_bump_proposer.py`,
  `test_run_rotation_proposer_lane.py`, and `test_proposer_lane_rows.py` had 249 passed.
- `make check` on `5dea4dfe`, database `orchestrator_test_hq_w_230`, exited 0:
  - Ruff: "All checks passed!", 1092 files already formatted.
  - Pyright: 0 errors, 0 warnings.
  - Tests: 15780 items collected, 15779 passed, 1 skipped.
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO, the same three #732
  reported. The diff causes none of them:
  - WARN `bws.secret-files-gitignored`.
  - WARN `bws.manifest-matches-usage`.
  - INFO `bws.least-privilege-scope`: a judgment finding.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/bump-proposer/compose.yaml`
- `lanes/rotation-proposer/compose.yaml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/rotation_worker/test_proposer_lane_rows.py`
- `tests/scripts/test_run_bump_proposer.py`
- `tests/scripts/test_run_rotation_proposer_lane.py`
- `docs/superpowers/reports/lanes-inc-4-proposers-ping-uuid-report.md` (this report)

## Self-review

- The diff follows #732, apart from each lane's own wording: the bump proposer pings on `--submit`,
  and the rotation proposer pings on every pass. I read both launchers to confirm which.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call. I ran nothing in
  `~/Projects/orchestrator`.

## Concerns

- The bump proposer's launcher harness runs only the empty-id path, so no bump-proposer launcher
  test runs a `--submit` ping by id. The by-id path is the shared helper's, tested in
  `tests/scripts/test_sds_deadman.py` and in the rotation proposer's fence test. #732 left the
  work carrier the same way.
- `tests/rotation_worker/test_proposer_lane_rows.py:87` still says "the account G-75 also gave the
  lane's reads". It describes the Mac token's account, not a pending id, so it's still true, and I
  left it.
- HQ's release of #748 is the served-release half of both items. The test database
  `orchestrator_test_hq_w_230` still exists for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the diff adds no handler. The edited
  tests assert directly and catch nothing.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a check's
  ping UUID, or a token. The only ids added are the keepers' BWS secret ids, labeled as "not a
  secret, not the ping URL" in the composes, the manifest, and the tests.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value. The composes keep `${BUMP_PROPOSER_BWS_ACCESS_TOKEN}` and
  `${ROTATION_PROPOSER_BWS_ACCESS_TOKEN}`. The scan has 0 BLOCK findings, and the diff adds no
  new test token values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. Neither lane serves anything: no
  port, no domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits two BWS secret ids and no
  `https://healthcheck.alobar.net/ping/...` URL.
