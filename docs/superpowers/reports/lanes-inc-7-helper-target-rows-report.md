Outcome: done

# Report: `helper_target` on the lane-token rows (increment 7, item c2)

PR: #793, a draft titled "The twelve lane-token rows name their helper target (merge at increment
7 cut-over)". Branch `hq/lanes-inc-7-helper-target-rows`, base `769b02af`. HQ merges it; I didn't.

## What I built

I re-ran the enumeration with `tomllib` on `.cred-consumers.toml` at `769b02af`. It found 14
`bws-lane-token` rows, the same as HQ's list. Twelve of them, each a lane that loads its own token,
now carry `helper_target`. In each row the line comes right after `rotated_by_sds = true`, before
the row's first `[[credential.consumer]]`. In both TOML and the infraops parser, a line after that
table header would belong to the consumer table.

| Row | `helper_target` | Container (read from the repo) |
|---|---|---|
| `execution-watch-bws-token` | `execution-watch` | `execution-watch-svkt4ux8o16dnl7n3k6qcd99` |
| `landing-ledger-bws-token` | `landing-ledger` | `landing-ledger-gs1z8orebdnq9ug574sia6fs` |
| `deploy-watcher-bws-token` | `deploy-watcher` | `deploy-watcher-p12q9n6xcy5u8eib1m4gmvar` |
| `revision-watcher-bws-token` | `revision-watcher` | `revision-watcher-kmjkrhlmv6c7ee76g77a8491` |
| `unit-verifier-bws-token` | `unit-verifier` | `unit-verifier-y144dc0981smrtzl587hqa4f` |
| `pin-watcher-bws-token` | `pin-watcher` | `pin-watcher-athv39w06xtmvqtxnjs3m8d0` |
| `membership-check-bws-token` | `membership-check` | `membership-check-qb6f52pfvhcoh2w2sgrhii2z` |
| `change-proposer-bws-token` | `change-proposer` | `change-proposer-w35r4jsmylloghoexdc18gfl` |
| `rotation-decomposer-bws-token` | `rotation-decomposer` | `rotation-decomposer-hcghb8xli5qqo6kl8enuu080` |
| `work-carrier-bws-token` | `work-carrier` | `work-carrier-am99g0klcsa7u8dpb4t19xjr` |
| `bump-proposer-bws-token` | `bump-proposer` | `bump-proposer-dsmi1mj0xbjrsu8kamdxocp8` |
| `rotation-proposer-bws-token` | `rotation-proposer` | `rotation-proposer-chyzh4utebg7ruouxxp42d0s` |

Each name passes `bws_lane_token.HELPER_TARGET`. It also passes the helper's own `IDENT` regex
(`deploy/vps/sds-pg-rotate` line 35, the same pattern).

I derived each container name as `<service>-<resource uuid>`:

- The service is the one service in `lanes/<lane>/compose.yaml`. In every one of the 12 files it's
  named after the lane, and its `BWS_ACCESS_TOKEN` maps `${<LANE>_BWS_ACCESS_TOKEN}`.
- The uuid is the row's `coolify-env` consumer `uuid`.

The page marks these names as read from the repo, to be confirmed with `docker ps` at the cut-over.
I ran no `docker` or VPS command.

The following rows don't change:

- `inert-landing-bws-token`: inert landing is a broker lane, and its compose file maps no
  `BWS_ACCESS_TOKEN`.
- `secrets-broker-bws-token`: `rotated_by_sds = false`.

### Docs

`docs/operations/rotation-executor.md` changes in two places:

- **"Install the helper":** a table of the 12 consumers-only targets the cut-over installs, each
  with its container name and the shared `env = "BWS_ACCESS_TOKEN"`. The text explains how the
  names were derived and that a broker lane gets no target.
- **Lane-token section:** the "No `bws-lane-token` row … names `helper_target`" paragraph now names
  which rows carry the field and which two don't. It states both merge preconditions:
  1. The helper is reinstalled with the targets. A helper from before `4d8ca873`, or one without
     the row's target, refuses the target, so the reissue waits on its 48-hour alert.
  2. The live executor's install is at or after `c5300a2c`. An install from before `4d8ca873`
     ignores the field and keeps the `PATCH` proof without saying so. One from before `c5300a2c`
     doesn't name the proof in the step's note.

  It also says a row naming a target needs the executor's `pg_helper_*` settings.

`src/rotation_worker/CLAUDE.md` 114-121 is unchanged. Its text still holds, including "No broker
lane's row may get `helper_target`".

## Stop-point checks, none triggered

- **Row list:** identical to HQ's.
- **Other readers of the field or the row as a whole:** `helper_target` is read only in
  `bws_lane_token.py`, plus `postgres_role.py` for its own class. I checked every other loader of
  the file:
  - `rotation_worker.registry._entries`, `checks.py` and `plans.derive` read named fields, and the
    plan is derived from capabilities, destinations, keeper and store kind.
  - `rotation_proposer.generate.render` and `plan_for` read class, id, identity, check name and
    destinations.
  - `rotation_proposer.standing_approval` reads class and id.
  - `membership_check.declaration.hands_over` reads `rotated_by_sds`.
  - `scripts/deploy_orchestrator.py::lanes_in_registry` reads consumers.
  - `scripts/sds-cred-registries.sh`, `run-rotation-proposer.sh` and `lane_run.py` only list the
    file's path.

  No digest, fixture or pin covers a whole row. The existing per-lane row tests assert subsets
  (`{TOKEN, PING_URL} <= set(rows)`), not exact key sets.
- **Compose service names:** each of the 12 compose files gives one service.
- **Behavior before the reinstall:** this was checked by tracing `worker._take`. The rows are read
  from `origin/main`, so nothing changes until the PR merges. If the PR merged before the reinstall,
  the old helper's `bad_target` reaches `_loaded_waits` as a `ContractError`. That's a wait on the
  48-hour clock, never a pass. `build_provider` raises before the claim only when the instance has
  no `pg_helper`. The VM executor has one (`rotation-executor.md` line 494: `pg_helper_host`
  "stays"). The Mac `rotation-worker` never builds this adapter, because its store kind is
  `keychain` and `bws-lane-token` is `bws-secret`.
- **infraops `loadCredConsumerFiles`** (`src/security-drift/cred-consumers.ts` at `origin/main`,
  read with `git show`): any `key = value` that matches `^[A-Za-z0-9_-]+$` is stored on the row.
  There's no unknown-key refusal, and `postgres-role` rows in other repositories already carry
  `helper_target`.

## Tests and checks

`tests/rotation_worker/test_lane_token_helper_targets.py` has 8 tests, which read the real file
through `RepoMirror` the way the executor reads it. They check the following rules:

1. Every `rotated_by_sds` `bws-lane-token` row whose lane isn't a broker lane names `helper_target`.
   The broker lanes are taken from `lanes/secrets-broker/allowlist.toml`, so no id is hard-coded.
2. No broker lane's row, and not `secrets-broker-bws-token`, names one.
3. Each target equals the compose service whose `BWS_ACCESS_TOKEN` maps the row's variable.
4. Every row naming a target passes `check_row`, and at least one does.
5. No consumer table carries the field.
6. `credential_destinations`, `plan_for` and the rendered revision (`render`) are equal with and
   without the field, for both issue and reissue. So no intent-packages revision is needed.
7. `hands_over` (the membership check's parse, D-59) and `lanes_in_registry` (the deploy command)
   accept the file and answer the same with and without the field.
8. The operations page's table equals the derived `<service>-<uuid>` names.

### Mutation proof

Every mutation was restored with copy-and-restore and checked with `cmp`, and `__pycache__` was
cleared around the code mutations. All 9 were caught:

| Mutation | Failing tests |
|---|---|
| M1: drop `landing-ledger`'s target | 1 |
| M2: add a target to `inert-landing` | 2, 3 |
| M3: add a target to `secrets-broker` | 2 |
| M4: wrong name `pin_watcher` | 3 |
| M5: regex-refused name `1pin` | 3, 4 |
| M6: move the line into the `coolify-env` consumer table | 1, 5 |
| M7: `generate.render` reads `helper_target` | 6 |
| M8: `credential_destinations` reads `helper_target` | 6 |
| M9: one container name wrong in the doc table | 8 |

Rule 7 was not mutation-tested.

### Checks run

- **`make check`** against `orchestrator_test_hq_w_276`: exit 0, 14694 passed, 1 skipped (14695
  collected).
- **`code-standards risk`:** `high-risk`, matching the `auth` rule on the new test's path. The
  vendor review ran in `/tmp/vendor-review-g1m5z4ju`. Claude approved with 0 findings, and Codex
  approved with 0 findings. agy is off by configuration. 0 findings were kept and 0 dropped, so
  nothing needed reproducing. The review is complete for the seats that ran.
- **`/code-review` (medium):** three nits.
  - The doc table isn't checked against the registry. I fixed this with test 8.
  - The field takes effect at merge before the reinstall. That's by design: the PR is a draft, and
    the preconditions are in the docs.
  - The test's `environment` lookup assumes the dict form of compose `environment`. I didn't
    change this: a list form makes the test fail with an error, not pass.

## Files changed

- `.cred-consumers.toml`: 12 lines.
- `docs/operations/rotation-executor.md`
- `tests/rotation_worker/test_lane_token_helper_targets.py`: new, covered by `layers.toml`'s
  `tests/rotation_worker/**`.
- `docs/superpowers/reports/lanes-inc-7-helper-target-rows-report.md`: this report.

## Self-review

The change touches only the 12 rows' `helper_target` lines, the operations page and a new test. I
made no other row, field, helper, provisioning, systemd or ADR change, and no VM, VPS, Coolify,
BWS, infraops or token access.

## Concerns and follow-ups for HQ

- **A reissue in flight at merge:** `execution-watch-bws-token` carries `rotate_requested =
  "2026-11-08"`, and any of the 12 may have a reissue under way when this merges. The executor
  reads the row at run time, so that reissue's `verify` switches to the `loaded` proof. That's
  intended, but the lane must have restarted onto the new token, or `verify` waits on the 48-hour
  clock. Check for open lane-token rotation units before merging.
- **Executor helper settings:** a row naming a target needs the VM executor's `pg_helper_*`
  settings. Without them, the pass logs the unit `unit_not_worked`, a stuck wait. The VM has them
  according to the docs. Confirm at the cut-over.
- **Container names:** I didn't verify them live. Confirm each with `docker ps` at the cut-over.
- **ADR text:** ADR-0055 amendment 29 at about line 4511 ("What isn't done … one helper target per
  lane in `targets.toml`") will be stale once this merges. That's an ADR follow-up, not changed
  here.

## Open questions

None.

## Standards followed

- **Code Brain 3 (structured JSON logs, no file logs):** no logging change.
- **Code Brain 4 (never swallow an error):** no error-handling change. The new tests fail loudly on
  any malformed input, for example `len(loading) == 1` with the list shown.
- **Code Brain 5 (never log secrets):** the docs and tests name containers, targets, variable names
  and BWS or Coolify uuids only, never a value.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files, no inline `BWS_ACCESS_TOKEN`
  value):** no token or token-shaped string was added. `env = "BWS_ACCESS_TOKEN"` names the
  variable only.
- **Infra Brain 1032 (no revoke without attested consumers):** the change only moves the 12 rows
  from the `PATCH` proof to the stricter `loaded` match. `LoadedRead.mismatch` and `_loaded_waits`
  are unchanged, so a mismatch, an empty answer or an unreadable answer still waits or fails, never
  passes. The merge preconditions are written down so that an old executor doesn't keep the
  `PATCH` proof unnoticed.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable, since no domain changed.
- **Repo rules:** `make check` is green on my own database. The new test file sits in a layer.
  Nothing in the orchestrator `CLAUDE.md` invariants is contradicted, and the rotation worker's
  `CLAUDE.md` rule still holds as written. `code-standards risk` ran, and since it was `high-risk`,
  the vendor review and `/code-review` both ran.
