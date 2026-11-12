Outcome: done

# Report: the membership check's registry rows (ADR-0059 increment 3, step 3e-6, cut-over step 3)

Worker W-110. Branch `hq/lanes-inc-3-membership-check-rows`, base `origin/main` at `787aed48`
(not `519bb23f`; the one commit between, #676, touches only ADR-0059 and no fact this handoff
names). PR [#677](https://github.com/AlobarQuest/orchestrator/pull/677), opened ready, not merged.
Code commit `4646dfbb`; this report is committed after it on the same branch.

## What I built

Change 1 of the two changes for the `membership-check` lane, mirroring PR #673 (`e94ffa2a`):

- `.cred-consumers.toml`: a block "The membership-check lane's token and ping URL (ADR-0059
  increment 3)" after the pin watcher's, before "The two lane GitHub Apps' private keys", with two
  rows:
  - `membership-check-bws-token`: class and provider `bws-lane-token`, `bws_uuid`
    `eb70b29d-…84f`, `rotated_by_sds = true`, `rotate_requested = "2026-11-12"`, `reach_secrets`
    (check 1), `reach_projects = []`; consumers: the keeper (`bws-secret`) and `coolify-env`
    (`prod`, `service`, `qb6f52pfvhcoh2w2sgrhii2z`, `MEMBERSHIP_CHECK_BWS_ACCESS_TOKEN`,
    `redeploy = false`).
  - `membership-check-ping-url`: class and provider `healthchecks-ping-url`, `bws_uuid`
    `5fc44897-…9be`, `rotated_by_sds = true`, `healthchecks_check = "sds-membership-check"`, one
    keeper consumer, no `rotate_requested`.
  - Neither row carries `created`. The block comment says the token is due, the ping URL is dated
    by a later change, and both keepers and the account's four reads came at G-39.
- The grant comments: `.cred-consumers.toml` (the App keys' block) and `.bws-secrets.toml` (the read
  App key's entry) now name `membership-check` among the accounts holding a read grant (G-39). The
  `lanes-read-app-private-key` row's container `runtime-fetch` consumer for
  `scripts/run-membership-check.sh` was already present and is unchanged; the Mac `runtime-fetch`
  entries on other rows are unchanged.
- `tests/rotation_worker/test_membership_check_rows.py` (18 tests), modelled on
  `test_pin_watcher_rows.py`.
- `tests/scripts/test_deploy_lanes.py`: the registry test registers seven moved lanes.

## Checks

### 1. `reach_secrets`, derived

The `.bws-secrets.toml` entries whose `lanes` include `membership-check`, at this base:

| id | name | where |
| --- | --- | --- |
| `f793576f-e9aa-4f9d-8089-b4a000b9e2d5` | `orchestrator-observer` | `.bws-secrets.toml:120-124` |
| `314f276d-55ca-4ddc-a24d-b4a3013508cd` | `change-manager/M2M_TOKEN_READ` | `.bws-secrets.toml:172-176` |
| `314a966c-5654-4e3c-8358-b4fd01268775` | `lanes-read-app-private-key` | `.bws-secrets.toml:313-316` |

Plus the ping-URL keeper `5fc44897-b4df-44ae-8491-b5000129d9be` (`membership-check-ping-url`). The
alert sender `b415d03b-…` lists `execution-watch`, `pin-watcher`, `revision-watcher` and
`unit-verifier`, not `membership-check` (`.bws-secrets.toml:265`). The set matches HQ's and the
four G-39 grants. The test asserts it both ways: equal to the manifest's lane secrets plus the
ping keeper, and equal to the granted set.

### 2. The rows, parsed through the code

`test_membership_check_rows.py` loads this repository's `.cred-consumers.toml` into the test
origins and reads it with `rotation_worker.registry.rotated_by_sds` through `RepoMirror`, as the
executor does. It checks:

- `bws_lane_token.check_row` and `lane_variable`: the service uuid and
  `MEMBERSHIP_CHECK_BWS_ACCESS_TOKEN`, and the compose maps it
  (`lanes/membership-check/compose.yaml:22`).
- `redeploy is False`, read from the row's own consumer.
- `bws_lane_token.declared_reach` against check 1's set.
- The shared-reach rule, once per other lane token (deploy-watcher, execution-watch,
  landing-ledger, pin-watcher, revision-watcher, unit-verifier): each shares at least one secret
  with this row, `sharing_reach` returns `[]`, and a twin row copying the other lane's whole reach
  set is reported. That shows the rule compares whole sets.
- `healthchecks_ping_url.check_row` and `healthchecks_check = "sds-membership-check"`.
- Each keeper consumer's uuid equals its row's `bws_uuid`; neither row has `created` or
  `last_rotated`; `rotation_proposer.generate.plan_for(..., first_issue=True)` derives an issue plan
  for both; the read App key row's reader for the launcher names that key's id.

Mutation check (copy at `/tmp/w110-cc.bak`, restored after each; `cmp` confirmed byte-identical):

| Mutation | Result |
| --- | --- |
| `redeploy = true` on the token's `coolify-env` | 1 failed, 17 passed |
| `314f276d-…` removed from `reach_secrets` | 1 failed, 17 passed |
| ping-URL keeper consumer's uuid set to the token's id | 1 failed, 17 passed |

### 3. Due-ness

infraops `src/security-drift/cred-rotation.ts` (checkout at `c3359bf`): `cred.rotation-requested`
fires when `now >= rotate_requested` and the row has no later rotation (~279-291).
`healthchecks-ping-url` has `maxAgeDays: Number.POSITIVE_INFINITY` (~97-98), so a row without
`rotate_requested` and without an exposure is never due.

I ran `cred-findings` from a scratch `git archive` of `~/Projects/infraops-mcp-server`
(`/tmp/w110-infraops.O4p9`, `node_modules` symlinked read-only, no build), with
`INFRADRIFT_CONFIG_DIR` at a scratch list naming a copy of this branch's registry and an empty
rotation state, `--now 2026-11-12T20:00:00Z`. The CLI's entry guard only runs as `.js`, so a
two-line `.mts` wrapper called its exported `main`. Result for the two rows:

```
{'check': 'cred.rotation-requested', 'id': 'membership-check-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-12', 'rotated_by_sds': True}
last_rotated: {'membership-check-bws-token': None, 'membership-check-ping-url': None}
```

No finding for `membership-check-ping-url`, and no `cred.unknown-class` for either.

### 4. Fixtures and existing tests

- `tests/scripts/test_deploy_lanes.py::test_the_repositorys_registry_registers_the_six_moved_lanes`
  reads the real `.cred-consumers.toml` through `deploy.lanes_in_registry`, which keys on each
  `coolify-env` consumer with `resource_type = "service"` and a `<LANE>_BWS_ACCESS_TOKEN` key
  (`scripts/deploy_orchestrator.py:1192-1223`). The new row adds `membership-check`, already in
  `LANE_CHECKS` (`:962`). Renamed to `..._seven_moved_lanes` and the set extended.
- Once this merges, `registered_lanes` (`scripts/deploy_orchestrator.py:1255`, reading
  `origin/main`) returns `{deploy-watcher, execution-watch, landing-ledger, membership-check,
  pin-watcher, revision-watcher, unit-verifier}`. A registered lane with no passed `--add` holds no
  release (`require_lanes_current`).
- `test_every_lanes_ping_url_secret_is_listed_for_that_lane` reads the composes'
  `SDS_DEADMAN_PING_URL_UUID`; the compose is unchanged, so `membership-check` stays in its
  `placeholders`. Unchanged.
- `test_pin_watcher_rows.py` compares the pin watcher's row against its own other-lane list and
  `sharing_reach` over every lane row; it passes with the new row (run in the focused set, 125
  passed, and in `make check`). Unchanged.
- No agreement fixture pins the registry's lane rows or App key rows: `git grep` for the pin
  watcher's row ids found only `.cred-consumers.toml` and its own test.

### 5. How the rows go live

The 03:00 scan and `cred-findings` read the registries listed in
`~/.config/infra-drift/cred-consumers.list`, whose orchestrator entry is the main tree's working
copy, `/Users/devon/Projects/orchestrator/.cred-consumers.toml` (line 4; read, not used for the
check-3 run). The rotation proposer gets its due list from that `cred-findings`
(`scripts/run-rotation-proposer.sh:8`). ADR-0031 §1 (lines 37-39): each scheduled launcher
fast-forwards the working copy it runs before running it, through
`~/.claude/bin/activate-checkout.sh`'s `activate_checkout`; the orchestrator's launchers call it on
their `REPO_ROOT` (for example `scripts/run-bump-driver.sh:64`,
`scripts/run-activation-sweep.sh:103`). So the rows reach the scan and proposer at the first
launcher pass after the merge. The executor reads the registry at `main` through `RepoMirror`.

## Gates

- `make check` with `orchestrator_test_hq_w_110` (`PGTZ=UTC`): ruff "All checks passed", ruff
  format clean, pyright "0 errors, 0 warnings", pytest **14695 passed, 1 skipped**, exit 0.
- Security scan from the repo root: exit 0, no BLOCK. Two WARNs, neither from this diff:
  `bws.secret-files-gitignored` (`*-migration/`) and `bws.manifest-matches-usage` (`aaff45ca-…`,
  which appears only in earlier reports). One INFO judgment item.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml` (comment only)
- `tests/rotation_worker/test_membership_check_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-3-membership-check-rows-report.md` (this report)

## Self-review and deviations

- Base is `787aed48`, one commit past `519bb23f`; every fact the handoff cites held at it.
- `docs/operations/deploy.md:432-445` lists the lane's remaining cut-over steps; its step 3 is
  this change. I left it unchanged, as #673 did for the pin watcher's equivalent; the list is the
  order of remaining work, and HQ may prefer to tick it when the cut-over finishes.
- ADR-0059's table (~2560-2563) describes the pre-cut-over state as decided; ADRs are records, so I
  didn't edit them.

## Concerns and follow-ups

- Change 2 (the ping URL's row dated) and the compose naming `5fc44897-…` as
  `SDS_DEADMAN_PING_URL_UUID` (with `membership-check` removed from `placeholders`) remain, per
  `docs/operations/deploy.md` steps 5-6.
- Scratch files outside the worktree (`/tmp/w110-*`) hold no secret; the scratch registry copy is
  this branch's tracked file.
- Teardown (worktree, `orchestrator_test_hq_w_110`, branch) waits for HQ after the merge.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change; the diff is registry
  rows, comments and tests.
- **Code Brain rule 4 (never swallow an exception silently):** the new test has no `try`/`except`;
  every failure surfaces as a pytest failure.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, ping URL, the UUID
  inside a ping URL, or the App key. The rows and tests name BWS secret ids only, which are not the
  ping URL's UUID.
- **Infra Brain 591, 592, 593:** no BWS token in tracked files or history; no bootstrap token
  inline; the test uses no token values at all. The security scan reports no BLOCK.
- **Infra Brain 2890:** no `https://healthcheck.alobar.net/ping/` URL is committed; the ping-URL
  row names the check and the keeper's id only.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; no domain change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a test or a
  log; the rows name ids only, and the check-3 run printed only finding facts.
