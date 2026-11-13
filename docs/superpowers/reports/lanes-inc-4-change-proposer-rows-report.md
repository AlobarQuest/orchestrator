Outcome: done

# Report: the change proposer's registry rows (ADR-0059 increment 4, step 3f-1, cut-over step 3)

Worker W-126. Branch `hq/lanes-inc-4-change-proposer-rows`, base `origin/main` at `ad83d903` (the
handoff's base; every fact it cites held there). PR
[#688](https://github.com/AlobarQuest/orchestrator/pull/688), opened ready, not merged. Code commit
`465958a9`; this report is committed after it on the same branch.

## What I built

The `change-proposer` lane's registry rows, mirroring PR #677's state for the membership check
(token due, ping URL not yet due):

- `.cred-consumers.toml:1064-1111`: a block "The change-proposer lane's token and ping URL
  (ADR-0059 increment 4)" right after the membership check's block, before "The two lane GitHub
  Apps' private keys", with two rows:
  - `change-proposer-bws-token`: class and provider `bws-lane-token`, `bws_uuid`
    `141260f5-bc4b-44d8-8ac8-b50100c0d40a`, `rotated_by_sds = true`,
    `rotate_requested = "2026-11-13"`, `reach_secrets` (check 1), `reach_projects = []`.
    Consumers: the keeper (`bws-secret`) and `coolify-env` (`prod`, `service`,
    `w35r4jsmylloghoexdc18gfl`, `CHANGE_PROPOSER_BWS_ACCESS_TOKEN`, `redeploy = false`).
  - `change-proposer-ping-url`: class and provider `healthchecks-ping-url`, `bws_uuid`
    `7651ccc2-7e13-417b-86da-b50100c0d4b2`, `rotated_by_sds = true`,
    `healthchecks_check = "sds-change-proposer"`, one keeper consumer, no `rotate_requested`.
  - Neither row carries `created`. The block comment says the token is due, the ping URL's row is
    dated by a later change once the token's `store` unit completes, and both keepers and the
    account's four reads came at G-49.
- Grant comments: `.cred-consumers.toml` (the App keys' block, ~1125) and `.bws-secrets.toml` (the
  read App key's entry, ~313-315) now name `change-proposer` among the accounts holding a read grant
  (G-49), replacing "granted at its cut-over". A `git grep` for other such text found one more:
  the `test_every_lanes_ping_url_secret_is_listed_for_that_lane` docstring in
  `tests/scripts/test_deploy_lanes.py` said the keeper "is created at its cut-over"; it now says
  the keeper was created at G-49 and a later change writes its id. Its body is unchanged.
- Not changed, as instructed: the Mac `runtime-fetch` entries for `run-change-proposer.sh`
  (`.cred-consumers.toml:76-78`, `:390-392`), the App key row's container consumer
  (`:1188-1190`), and the compose's `SDS_DEADMAN_PING_URL_UUID`.
- `tests/rotation_worker/test_change_proposer_rows.py` (19 tests): `test_membership_check_rows.py`
  at #677's state (`4646dfbb`), retargeted; `membership-check-bws-token` joins the other-lane list.
- `tests/scripts/test_deploy_lanes.py`: the registry test registers eight moved lanes.

## Checks

### 1. `reach_secrets`, derived

The `.bws-secrets.toml` entries whose `lanes` include `change-proposer`, at this base:

| id | name | where |
| --- | --- | --- |
| `f793576f-e9aa-4f9d-8089-b4a000b9e2d5` | `orchestrator-observer` | `.bws-secrets.toml:122-125` |
| `acccb346-4baa-43ec-a1d4-b4a400c048ee` | `change-manager/M2M_TOKEN_PROPOSE` | `.bws-secrets.toml:181-184` |
| `314a966c-5654-4e3c-8358-b4fd01268775` | `lanes-read-app-private-key` | `.bws-secrets.toml:317-320` |

Plus the ping-URL keeper `7651ccc2-7e13-417b-86da-b50100c0d4b2`, which has no `.bws-secrets.toml`
entry yet (as at #677's state for the membership check). The alert sender `b415d03b-…` lists
`execution-watch`, `pin-watcher`, `revision-watcher` and `unit-verifier` only (`:268`). The set
matches HQ's three plus the ping keeper, the four G-49 grants. The test asserts it both ways: equal
to the manifest's lane secrets plus the ping keeper, and equal to the granted set.

### 2. The rows, parsed through the code

`test_change_proposer_rows.py` loads this repository's `.cred-consumers.toml` into the test origins
and reads it with `rotation_worker.registry.rotated_by_sds` through `RepoMirror`, as the executor
does. It checks:

- `bws_lane_token.check_row` and `lane_variable`: the service uuid and
  `CHANGE_PROPOSER_BWS_ACCESS_TOKEN`, and that the compose maps it
  (`lanes/change-proposer/compose.yaml:22`).
- `redeploy is False`, read from the row's own consumer.
- `bws_lane_token.declared_reach` against check 1's set.
- The shared-reach rule, once per other lane token (deploy-watcher, execution-watch,
  landing-ledger, membership-check, pin-watcher, revision-watcher, unit-verifier): each shares at
  least one secret with this row, `sharing_reach` returns `[]` for this row, and a twin row copying
  the other lane's whole reach set is reported. That shows the rule compares whole sets and passes.
- `healthchecks_ping_url.check_row` and `healthchecks_check = "sds-change-proposer"`.
- Only the token row is due: token `rotate_requested == "2026-11-13"`, ping URL has none.
- Each keeper consumer's uuid equals its row's `bws_uuid`; neither row has `created` or
  `last_rotated`; `rotation_proposer.generate.plan_for(..., first_issue=True)` derives an issue plan
  for both; the read App key row's reader for `scripts/run-change-proposer.sh` names that key's id.

Mutation check (copy at `/tmp/w126-cc.bak`, restored after each; `cmp` confirmed byte-identical):

| Mutation | Result |
| --- | --- |
| `redeploy = true` on the token's `coolify-env` | 1 failed, 18 passed |
| `acccb346-…` removed from `reach_secrets` | 1 failed, 18 passed |
| ping-URL keeper consumer's uuid set to the token's id | 1 failed, 18 passed |
| `rotate_requested` added to the ping-URL row | 1 failed, 18 passed |

### 3. Due-ness

infraops `src/security-drift/cred-rotation.ts` (checkout at `c3359bf`): `cred.rotation-requested`
fires when `now >= rotate_requested` and the row has no later recorded rotation (~279-291).
`bws-lane-token` has `maxAgeDays: 365` (~95), and with no `created` there is no age anchor, so no
`cred.rotation-age`. `healthchecks-ping-url` has `maxAgeDays: Number.POSITIVE_INFINITY` (~97-98),
and with no `rotate_requested` (`Date.parse` of undefined is `NaN`, so `now >= NaN` is false) and
no exposure, the row is never due.

I ran `cred-findings` from a scratch `git archive` of `~/Projects/infraops-mcp-server`
(`/tmp/w126-infraops.Z37T`, `node_modules` symlinked, no edit or build in the checkout), with
`INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` at a scratch directory whose list names a
copy of this branch's registry and an empty 0600 rotation state, `--now 2026-11-13T20:00:00Z`. As
in #677, a two-line `.mts` wrapper called the CLI's exported `main` through `tsx`. Result for the
two rows:

```
{'check': 'cred.rotation-requested', 'id': 'change-proposer-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-13', 'rotated_by_sds': True}
last_rotated: {'change-proposer-bws-token': None, 'change-proposer-ping-url': None}
unknown-class: []
```

No finding for `change-proposer-ping-url`, and no `cred.unknown-class` anywhere.

### 4. Fixtures and existing tests

- `tests/scripts/test_deploy_lanes.py::test_the_repositorys_registry_registers_the_seven_moved_lanes`
  reads the real `.cred-consumers.toml` through `deploy.lanes_in_registry`
  (`scripts/deploy_orchestrator.py:1201`), which keys on each `coolify-env` consumer with
  `resource_type = "service"` and a `<LANE>_BWS_ACCESS_TOKEN` key. The new row adds
  `change-proposer`, already in `LANE_CHECKS` (`:954`). Renamed to `..._eight_moved_lanes` and the
  set extended.
- `test_every_lanes_ping_url_secret_is_listed_for_that_lane` reads the composes'
  `SDS_DEADMAN_PING_URL_UUID`; the compose is unchanged, so `change-proposer` stays in its
  `placeholders`. Docstring sentence updated only (see What I built).
- The other `test_*_rows.py` files compare their row against their own other-lane lists and
  `sharing_reach` over every lane row; they pass with the new row (focused run 117 passed, and in
  `make check`). Unchanged.
- No agreement fixture pins the registry's lane rows: `git grep` for the new ids found only
  `.cred-consumers.toml` and the new test.
- **`registered_lanes` once this merges** (`scripts/deploy_orchestrator.py:1255`, reading
  `origin/main`): `{change-proposer, deploy-watcher, execution-watch, landing-ledger,
  membership-check, pin-watcher, revision-watcher, unit-verifier}`.
- **Does anything treat the change proposer as registered before its token is issued?** Yes, in
  name only, and it holds nothing. `registered_lanes` keys on the row's existence, not on a token
  being issued, so from merge it lists `change-proposer`. Its two readers both skip a lane with no
  passed `--add`: `require_lanes_current` (`:1265-1291`) checks only lanes whose `last_finished`
  run exists, and `lanes_to_follow` (`:1294-1300`, used by `finish` at `:899`) only lanes with a
  passed `--add`. The operator Mac's lane state directory
  (`~/Library/Application Support/sds/deploy-lanes/`) has no `change-proposer` directory (listed
  read-only today), so no release is held and `finish` prints no `lanes-prepare` for it until its
  `--add` passes.

### 5. How the rows go live

The 03:00 scan and `cred-findings` read the registries listed in
`~/.config/infra-drift/cred-consumers.list`, whose orchestrator entry is the main tree's working
copy, `/Users/devon/Projects/orchestrator/.cred-consumers.toml` (line 4; read, not used for the
check-3 run). The rotation proposer gets its due list from that `cred-findings`
(`scripts/run-rotation-proposer.sh:7-8`). ADR-0031 §1 (lines 37-39): each scheduled launcher
fast-forwards the working copy it runs before running it, through
`~/.claude/bin/activate-checkout.sh`'s `activate_checkout`; the orchestrator's launchers call it on
their `REPO_ROOT` (for example `scripts/run-bump-driver.sh:64`). The rotation proposer's own call
activates the intent-packages checkout (`scripts/run-rotation-proposer.sh:117`), so the main tree's
registry reaches it at the first orchestrator launcher pass after the merge. The executor reads the
registry at `main` through `RepoMirror`.

## Gates

- `make check` with `orchestrator_test_hq_w_126` (`PGTZ=UTC`): ruff "All checks passed!", ruff
  format "1059 files already formatted", pyright "0 errors, 0 warnings, 0 informations", pytest
  **14808 passed, 1 skipped**, exit 0.
- Security scan from the repo root: exit 0, no BLOCK. Two WARNs, both present at #677 and neither
  from this diff: `bws.secret-files-gitignored` (`*-migration/`) and `bws.manifest-matches-usage`
  (`aaff45ca-…`). One INFO judgment item (`bws.least-privilege-scope`).
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml` (comment only)
- `tests/rotation_worker/test_change_proposer_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-4-change-proposer-rows-report.md` (this report)

## Self-review and deviations

- No deviations from the handoff. The one edit beyond the two named comments is the
  `test_deploy_lanes.py` docstring sentence that said the keeper is created at cut-over; it is
  comment text that the handoff's "grep for others" covers.
- I left `docs/operations/deploy.md`'s cut-over list and ADR-0059 unchanged, as #677 did.

## Concerns and follow-ups

- Remaining, per `docs/operations/deploy.md` steps 5-6: date the ping URL's row once the token's
  `store` unit completes, and write `7651ccc2-…` as the compose's `SDS_DEADMAN_PING_URL_UUID`
  (with `change-proposer` removed from `placeholders` and a `.bws-secrets.toml` entry for the
  keeper, as the membership check's later PRs did).
- Scratch files under `/tmp/w126-*` hold no secret; the scratch registry copy is this branch's
  tracked file.
- Teardown (worktree, `orchestrator_test_hq_w_126`, branch) waits for HQ after the merge.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change; the diff is registry
  rows, comments and tests.
- **Code Brain rule 4 (never swallow an exception silently):** the new test has no `try`/`except`;
  every failure surfaces as a pytest failure.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, ping URL, the UUID
  inside a ping URL, or the App key. The rows and tests name BWS secret ids only, which are not the
  ping URL's UUID; the check-3 run printed only finding facts.
- **Infra Brain 591, 592, 593:** no BWS token in tracked files or history; no bootstrap token
  inline; the test uses no token values. The security scan reports no BLOCK.
- **Infra Brain 2890:** no `https://healthcheck.alobar.net/ping/` URL is committed; the ping-URL
  row names the check and the keeper's id only.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; no domain change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a test or a
  log; the rows name ids only.
