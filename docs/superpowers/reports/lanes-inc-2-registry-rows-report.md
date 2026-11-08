Outcome: done

# Report: execution-watch's two credential registry rows (ADR-0059 increment 2, cut-over item 4)

Worker W-57. Branch `hq/lanes-inc-2-registry-rows`, base `origin/main` at `13814b17`. PR
[#639](https://github.com/AlobarQuest/orchestrator/pull/639), opened ready for review (not
draft), not merged. The rows are in commit `3775b80b`; this report is the commit after it.

## What I built

Change 1 of ADR-0055 amendment 19 item 8 step 2: both execution-watch rows, appended to
`.cred-consumers.toml` under a comment block that cites ADR-0055 amendment 19 and ADR-0059
increment 2.

- **`execution-watch-bws-token`.** Class and provider `bws-lane-token`. Keeper `bws_uuid`
  `5a21575b-b25b-42b5-bf05-b4fc00cd30e0`, `rotated_by_sds = true`, and
  `rotate_requested = "2026-11-08"`. It lists three `reach_secrets` (check 1) and
  `reach_projects = []`. It has two consumers: its keeper, and the `coolify-env` on `prod`
  service `svkt4ux8o16dnl7n3k6qcd99`, key `EXECUTION_WATCH_BWS_ACCESS_TOKEN`, `redeploy = false`.
- **`execution-watch-ping-url`.** Class and provider `healthchecks-ping-url`. Keeper `bws_uuid`
  `97802aac-5a7f-45e8-8185-b4fc00cd318e` and `rotated_by_sds = true`. It has no
  `rotate_requested`, and its only consumer is its keeper.

### Deviations from the handoff

1. **No `created` on either row.** HQ approved this in its answer to my question. infraops'
   `cred-findings` serves `last_rotated` as `rotationAnchor(spec, state) ?? null`, and
   `rotationAnchor` falls back to `created` (`cred-rotation.ts:209-211`). The proposer's
   `_first_issue` (`src/rotation_proposer/cli.py:413-418`) then returns false, which causes two
   failures. The token gets the rotation plan, and the ping-URL row gets a `GenerateError`
   (`generate.py:123-155`). `_record_retired` (`cli.py:550-569`) also never records an issue
   completed that same day. A comment on each row says why the field is absent and that the
   first `record-rotation` sets the anchor. Nothing in the orchestrator requires `created`: a
   grep of `src/rotation_worker`, `src/rotation_proposer`, `src/membership_check` and
   `scripts/deploy_orchestrator.py` finds no reader. infraops parses the rows without it (check
   4), and `make check` passes.
2. **`reach_secrets` on one line**, not spread over several lines as in W-56's draft. infraops'
   strict TOML subset refuses a multi-line array (`line 10: unsupported value syntax: [`), and
   a registry that doesn't parse stops `cred-findings` for every credential.
3. **A `note` on three consumers** (the token's keeper, its `coolify-env`, and the ping-URL
   keeper), as every other row in the file has. `note` isn't an identity field
   (`destinations.py`), so it changes no destination string.

## Checks

### 1. `reach_secrets`, derived

I read `.bws-secrets.toml` for every `[[secret]]` whose `lanes` names `execution-watch`. Three
entries match, and none lists more than one lane:

| Line | BWS secret id | Name | Project |
|---|---|---|---|
| 108-111 | `f793576f-e9aa-4f9d-8089-b4a000b9e2d5` | `orchestrator-observer` | Rotation / Keepers |
| 235-238 | `b415d03b-f92d-46b1-8a9d-b4f10103df49` | `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` | SDS Operator |
| 271-274 | `97802aac-5a7f-45e8-8185-b4fc00cd318e` | `execution-watch-ping-url` (the ping-URL keeper) | Rotation / Keepers |

The ping-URL keeper is already one of the manifest's lane entries, so adding it gives no fourth
id. The set equals G-11 step 5's three grants. The test pins this: `declared_reach(token row)`
must equal `(frozenset(), <the manifest's execution-watch secrets>)`.

### 2. Both rows parsed through the code

`tests/rotation_worker/test_execution_watch_rows.py` commits the repository's own
`.cred-consumers.toml` as the orchestrator's registry in a local origin. It reads the file as the
executor does, through `RepoMirror` and `registry.rotated_by_sds`, and then checks the following:

- Both ids are handed to the SDS.
- The token row passes `bws_lane_token.check_row`, `lane_variable` (it returns the service uuid
  and key), and `declared_reach` (it matches the manifest).
  `sharing_reach(provider_entries(mirror, "bws-lane-token"), row) == []`, so no other row shares
  its reach.
- The ping-URL row passes `healthchecks_ping_url.check_row`.
- Only the token row carries `rotate_requested`, and neither row carries `created` or
  `last_rotated`.
- `rotation_proposer.generate.plan_for(row, first_issue=True)` gives the issue plan for both.

**Mutation check.** I copied the file to `/tmp/w57-registry.bak`, applied each mutant, ran the
test, and restored the file from the copy. `cmp` then confirmed the file was identical, and the
control run passed 6 of 6.

| Mutant | Result |
|---|---|
| `redeploy = true` on the token's `coolify-env` | 1 failed, 5 passed |
| `resource_type = "application"` | 1 failed, 5 passed |
| `reach_secrets` drops the bridge sender | 1 failed, 5 passed |
| `created = "2026-11-08"` on the token row | 1 failed, 5 passed |
| `rotate_requested` on the ping-URL row | 1 failed, 5 passed |
| an extra `runtime-fetch` consumer on the ping-URL row | 1 failed, 5 passed |

Six mutants, six killed, no survivors.

### 3. The `coolify-env` uuid is the service's

- `bws_lane_token.lane_variable` (`adapters/bws_lane_token.py:78-93`) requires
  `resource_type == "service"`.
- `build_provider` builds `CoolifyClient(coolify_url, token, "service", variable["uuid"],
  restarts=False)` (`bws_lane_token.py:145-147`).
- For `resource_type == "service"`, `CoolifyClient` (`src/rotation_worker/coolify.py:67-74`) sets
  its one permitted write to `PATCH /api/v1/services/{uuid}/envs`. `set_variable` (`:94-103`)
  sends a single `PATCH` there for a service (an application gets two, one per preview row).
  `permits` (`:88-92`) refuses the restart because `restarts=False`.

So the row's `uuid` goes straight into Coolify's service path, and it must be the service's uuid
(`svkt4ux8o16dnl7n3k6qcd99`), not the inner application's.

### 4. Due-ness, measured

From `~/Projects/infraops-mcp-server/src/security-drift/cred-rotation.ts`:

- Age (`:209-211`, `:266-277`): the anchor is `state.lastRotated ?? spec.last_rotated ??
  spec.created`. Neither row has `last_rotated` or `created`, so neither has an anchor or an age
  finding. `healthchecks-ping-url` also has `maxAgeDays: Infinity` (`:97-102`).
- Request (`:279-290`): `rotate_requested` makes a row
  due once that day comes, unless it was rotated on or after that day. Only the token row has it.

I measured this by running infraops' **built** `cred-findings` from a scratch copy, without
editing or building the checkout. I copied `dist/` and `package.json` to `/tmp/w57-infraops.LRs0/app`,
symlinked `node_modules`, pointed `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` at a
scratch list naming this worktree's `.cred-consumers.toml`, and gave it an empty `0600` state
file. The production `~/.config/infra-drift/cred-consumers.list` was not used. I ran
`node dist/cli/security-drift-cli.js cred-findings --now 2026-11-08T17:00:00Z` (the built `dist/`
carries both classes; its checkout is at `c3359bf`). Filtered to these rows, it printed:

```
[{'check': 'cred.rotation-requested', 'id': 'execution-watch-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-08', 'rotated_by_sds': True}]
{'execution-watch-bws-token': None, 'execution-watch-ping-url': None}
```

The whole file parses, the token row is due for its first issue, the ping-URL row gives no
finding, and both report `last_rotated: null`. The scratch run reports one more finding, for an
existing row, which comes from checking it against my empty scratch state, not the real state.

### 5. Fixtures

- `tests/architecture/test_rotation_agreement.py` holds the class vocabulary to infraops'
  `rotation_class_policy.json`. Both classes are already typed, so it needs no change. The issue
  fixtures `rotation_package_issue_bws-lane-token.json` and
  `rotation_package_issue_healthchecks-ping-url.json` render from synthetic rows, so they don't
  change either.
- `tests/scripts/test_deploy_lanes.py::test_the_repositorys_registry_registers_no_lane_yet` read
  this file and asserted no lane was registered. Its body reads registry rows only
  (`deploy.lanes_in_registry(<file text>)`), not released lanes, so I changed only its expected
  set to `{"execution-watch"}`. I renamed it `..._registers_the_execution_watch_lane` and gave
  it a docstring stating ADR-0059's rule that a registered lane holds no release until its
  `--add` passes (`require_lanes_current` skips it, `scripts/deploy_orchestrator.py:1269-1273`).
- No other test reads the repository's real registry for its rows. The one other reader,
  `tests/unit_verifier/test_unit_verifier_launcher.py:38`, looks up a different row and passes.

### 6. How the rows go live

- **infraops' 03:00 scan.** `scripts/drift-audit.sh:186` runs
  `node "$REPO/dist/cli/security-drift-cli.js" run`. `run` loads
  `loadCredConsumerFiles(readList(p.credConsumersList))` (`security-drift-cli.ts:119`). The list
  is `~/.config/infra-drift/cred-consumers.list` (`paths.ts:46`), and line 4 is
  `/Users/devon/Projects/orchestrator/.cred-consumers.toml`, the main tree's working copy. No
  `git fetch` or `pull` exists on that path: the script and CLI have none, and the only "pull"
  hits in this repository's `scripts/` are comments.
- **The rotation proposer.** `scripts/run-rotation-proposer.sh` (header lines 7-12) runs
  `cred-findings` (`doCredFindings`, `security-drift-cli.ts:171-176`), which reads the same list,
  so due findings also come from the main tree's working copy. The proposer reads its own
  registry rows through `RepoMirror(default_mirror_root("rotation-proposer"))`
  (`rotation_proposer/cli.py:839-843`). `RepoMirror.sync` fetches GitHub's `main`
  (`git_mirror.py:64-77`).
- **The VM executor.** `registry._entries` (`registry.py:127-140`) calls `mirror.sync` and then
  `mirror.show(repository, commit, ".cred-consumers.toml")` for each registry repository. It reads
  the commit `AlobarQuest/orchestrator`'s `main` names when the pass fetches, that is, the merge
  commit from its first pass after the merge.
- **The deploy command's release hold.** `registered_lanes` reads `origin/main`
  (`deploy_orchestrator.py:1259-1261`). After the merge and a fetch, it covers `execution-watch`.
  The lane holds nothing until its `--add` passes.

HQ's reading holds. The merge alone makes nothing due, because what's due comes from the main
tree's working copy. An interactive pull of the main tree makes the token row due at the next
scan or proposer pass. The executor and the proposer's row lookup read the merged commit from
GitHub `main` on their next pass.

## Tests and checks run

| Check | Result |
|---|---|
| `make check` (database `orchestrator_test_hq_w_57`, `PGTZ=UTC`) | exit 0. Lint "All checks passed", pyright 0 errors, 14135 passed, 1 skipped |
| `pytest tests/rotation_worker/test_execution_watch_rows.py tests/scripts/test_deploy_lanes.py` | 95 passed |
| Mutation check (check 2) | 6 of 6 mutants killed |
| infraops `cred-findings` over the file (check 4) | token due, ping URL not due, `last_rotated` null for both |
| Security scan from the repo root | no BLOCK. Two WARNs, both already on `main`: the `*-migration/` gitignore glob, and the undeclared UUID `aaff45ca-…` in `scripts/install-rotation-worker-launchd.sh`. Neither appears in this diff |
| Ping-URL grep (`healthcheck.alobar.net/ping/<hex>`) over the changed files | no match |
| `/code-review` low on `origin/main...HEAD` | no findings |

## Files changed

- `.cred-consumers.toml`: the comment block and the two rows.
- `tests/rotation_worker/test_execution_watch_rows.py` (new): checks 1, 2 and 4 in code. It falls
  under `layers.toml`'s `tests/rotation_worker/**`.
- `tests/scripts/test_deploy_lanes.py`: the renamed test and its expected set.
- `docs/superpowers/reports/lanes-inc-2-registry-rows-report.md`: this report.

## Self-review

- The rows hold ids only: BWS secret ids, a Coolify service uuid, and a variable name. No value,
  ping URL, or UUID inside a ping URL appears in the file, the test, or this report.
- The test reads the registry through the executor's own reader at `main` of a local origin, not
  with `tomllib` alone. So a registry problem that breaks the executor also fails the test.
- The `created` mutant is killed by an explicit assertion. A later edit that restores the
  handoff's original decision fails the test and points at its docstring's reason.

## Concerns and follow-ups

- **Change 2 is HQ's.** After the token's `store` unit completes, add `rotate_requested` to the
  `execution-watch-ping-url` row. That makes `test_only_the_token_row_is_due_and_neither_has_an_age_anchor`
  fail, so the same change must update that assertion.
- **The ADR text.** ADR-0055 amendment 19 and ADR-0059 don't say that a first issue needs a row
  with no `created`. A future lane's rows could repeat the mistake. The comment in
  `.cred-consumers.toml` and the test cover this lane only. I recommend that HQ state the rule in
  the runbook's "The registry rows" (`docs/operations/rotation-executor.md`) or in an ADR
  amendment. I didn't edit either, because the handoff didn't scope it.
- Teardown: the worktree, the `orchestrator_test_hq_w_57` database and the scratch directories
  `/tmp/w57-infraops.LRs0` and `/tmp/w57-registry.bak` remain for HQ's resume or teardown. The
  untracked `docs/superpowers/reports/lanes-inc-2-registry-rows-question-answered.md` is HQ's
  file, and I didn't commit it.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The diff is a TOML
  registry and two tests.
- **Code Brain rule 4 (never swallow an exception silently):** the new test catches nothing. Every
  adapter refusal (`ContractError`, `RegistryError`) propagates and fails the test, as the
  mutation check shows.
- **Code Brain rule 5 (never log secrets):** nothing added prints a value. The test asserts on
  BWS secret ids, a service uuid and a variable name. No ping URL or UUID inside a ping URL
  appears anywhere. The ping-URL keeper's BWS secret id (`97802aac-…`) is a different thing, and
  the prose keeps the two apart.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; placeholder test
  values):** no token or token-shaped string is added. The test uses the real registry's ids, and
  the security scan reports no BLOCK.
- **Infra Brain 2890 (never commit a ping URL):** no `https://healthcheck.alobar.net/ping/` URL
  with a UUID is in the diff. A grep of the changed files finds none.
- **Infra Brain 1032 (`cred.exposure-rotate`):** neither row declares a `[[credential.exposure]]`,
  so the rule doesn't apply. The scratch `cred-findings` run gave no exposure finding for either.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, the test,
  this report, or any log. The rows name ids only.
