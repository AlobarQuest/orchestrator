Outcome: done

# Report: the Coolify API token rotation adapter (ADR-0055 Amendment 33 item 10, D-82)

Worker W-319, branch `hq/coolify-token-adapter-code`, based on `9caad335`.
PR: https://github.com/AlobarQuest/orchestrator/pull/820 (head: the commit that carries this report's final version; see `gh pr view 820`). Not merged: the merge waits on the dev
Coolify measurement (D-82 item 14).

## What I built

The `coolify-api-token` credential type and its adapter. A person mints and revokes in Coolify's
UI. The executor proves the value live beside a refused control, checks its scope against the
row's declared abilities, stores it, waits for every scheduled reader, and after the person's
revoke proves the old value dead. No row is handed to the SDS: all three keep `rotated_by_sds`
unset (false), with no `rotate_requested`.

### Each change against item 10's list

| Item 10 entry | Change |
|---|---|
| `src/rotation_worker/adapters/coolify_api_token.py` | New. `NAME = "coolify-api-token"`, `PROVIDER = "coolify"`, `PLAN = derive(TYPES[NAME].capabilities, (), "", "bws-secret")` as `postgres_role.py` builds its own. Probe `GET /api/v1/version` (live exactly 200, `dead_statuses = {401}`), control `<digits>\|<random>` built at run time, `same_identity` by `GET /api/v1/teams/current`'s `id`, `check_scope` for item 4 and 13 C, `check_row` refusing a row without valid `abilities`, and `mint`, `host`, `consumer_loaded`, `revoke` raising `ContractError`. The `ConfinedClient` permits exactly five requests (the next table). |
| `adapters/__init__.py` | Import, an `ADAPTERS` entry with `capabilities=TYPES[...]`, `check_row`, `revoke_may_come_first=True`, and the context field `coolify_canary_application`. |
| `types.py` | `TYPES["coolify-api-token"]` with `Capabilities(machine_mint=False, machine_revoke=False)`, and `CLASS_TYPES["coolify-api-token"]`. |
| `tests/architecture/test_rotation_agreement.py` | `coolify-api-token` leaves `UNTYPED`. |
| `tests/architecture/test_rotation_edges.py` | A `PROVIDER_EDGES["coolify-api-token"]` row (base `COOLIFY_URL`, five routes), the five paths in `ROUTES`, the module in `_WORKER_CLIENTS` (one client), `coolify` in `NO_ADMIN_SECRET`, and the new config field in `NOT_IDENTITIES`. |
| `tests/architecture/test_wsp21_invariant_scan.py` | The module in `OUTBOUND_ALLOWLIST`, with its reason, beside `bitbucket.py`. |
| `layers.toml` | No change: `src/rotation_worker/**` and `tests/rotation_worker/**` already cover the new files (`test_layer_manifest.py` passes). |
| `src/orchestrator/factory-policy.toml` | `coolify-api-token` in both `rotation_adapters` lists that name `bitbucket` (the `live_estate` and `external_system` rotation patterns), and one rationale sentence in each saying it has Bitbucket's shape. |
| `.cred-consumers.toml` | `abilities` on each row (`["read", "write", "deploy"]`, `["read"]`, `["read", "write", "deploy"]`), the Mac paths removed from two notes, the estate lander's note naming the carried gap (D-82 item 15 A), and each row's comment saying why it isn't `rotated_by_sds` yet. |
| intent-packages | No change, as the handoff says. |
| `docs/operations/rotation-executor.md` | A "Coolify API tokens" section: the mint steps (tick `deploy` first), the revoke, the early-revoke path, the scope probes' table, the canary, and what a green ping proves per row. The config sample and its notes name `coolify_canary_application`, commented out until the canary exists. |

The five requests the confined client permits, all to `coolify_url` with the probed value as the
bearer:

| Request | Use |
|---|---|
| `GET /api/v1/version` | Probe, control, confirm-dead (item 2) |
| `GET /api/v1/teams/current` | Identity (item 3) |
| `PATCH /api/v1/projects/<random UUID drawn at build>`, JSON body `{"sds_scope_probe": true}` | `write` scope (item 4) |
| `GET /api/v1/deploy`, no query | `deploy` scope (item 4) |
| `GET /api/v1/applications/<canary uuid>/envs`, only when the canary is configured | `read:sensitive` absent (item 13 C) |

### Changes beyond item 10's list, and why

- **`src/rotation_worker/contract.py`: a `ScopeChecked` protocol.** `_verify_new` had no hook for
  a provider's scope check (it calls only `run.probe` and the `ShapeVerified` shape check). I
  added an opt-in protocol beside `ShapeVerified`, called only after the new value probes live,
  so a dead value's 401 is never read as scope. Its note joins any shape note. This doesn't change
  what the adapter does, so the stop point didn't apply. No other adapter implements it.
- **`config.py`, `worker.py`, `cli.py`: the canary setting**, following `coolify_url`'s path:
  `Config.coolify_canary_application` (in `VM_ONLY`), `Setup`, `build_setup`, `_provider`,
  `AdapterContext`. The VM config is host-local (`/etc/rotation-worker/config.toml`), and the
  install script writes no configuration, so no install or deploy file changes.
- **`tests/architecture/test_external_content_observes_only.py`:** the four new Coolify paths
  declared foreign on the `rotation_worker` row, with the service each belongs to. The canary env
  path matches the existing `/api/v1/applications/{}/envs` declaration.
- **`tests/rotation_worker/test_client_surface.py`:** the module may spell Coolify's `/api/v1/`
  paths (as `coolify.py` may), and is in `HTTP_CLIENTS`.
- **`tests/rotation_worker/test_postgres_role.py`:** `EXISTING_PLANS` pins the type's plan as
  `BY_PERSON`, Bitbucket's.
- **`tests/rotation_worker/test_estate_landing_rows.py`:** the docstring no longer names a Mac
  reader.
- **New tests:** `tests/rotation_worker/test_coolify_api_token.py` (33 tests) and
  `tests/rotation_worker/test_coolify_api_token_rows.py` (14 tests).

## How D-82's five answers show in the code

- **11 A:** no keeper moves and no `.bws-secrets.toml` project changes. The estate lander's and
  the revision watcher's row comments say each waits on Devon moving its keeper into
  `Rotation / Keepers`.
- **12 A:** `mint`, `host`, `consumer_loaded` and `revoke` raise `ContractError` naming the
  person's act (`test_mint_host_consumer_loaded_and_revoke_are_a_person_s`).
- **13 C:** `check_scope` reads the canary's env list and refuses a 200 whose entries carry
  `value` or `real_value`. It also refuses an empty list, a non-list, an entry that isn't an
  object, and any non-200 status, naming the status, because an empty list can't tell a `root`
  token from a correct one. The canary UUID is the executor setting
  `coolify_canary_application`, checked against `^[a-z0-9]{8,64}$` before it enters a path.
  With it unset, the provider still builds (the inbox's bind and `confirm` need no canary), and
  only verify-new refuses: `no coolify_canary_application is configured, so the new
  token's lack of read:sensitive can't be proved`. The answer is checked in memory; no log line,
  evidence or refusal carries it (`test_no_evidence_or_refusal_carries_a_value`).
- **14 yes:** every answer is a source reading, tested against a simulated Coolify that answers
  as the amendment states. The measurement the merge waits on is in the following section. The
  repository has no one-off measurement script: amendment 1's measurements are a log in
  `docs/superpowers/plans/2026-10-09-rotation-increment-2-measurement-log.md`, and
  `scripts/exit_probe.py` is the wave-exit prober. So I added no script.
- **15 A:** the estate lander row's note says a green pass proves only that a pass began after
  the keeper changed, and that the gap is carried (D-82 item 15 A). The operations page says the
  same. `test_the_estate_lander_row_carries_the_read_proof_gap` and
  `test_what_a_green_pass_proves_follows_each_launcher_s_read` pin it.

I read the answers in Coolify's source at tag `v4.0.0-beta.473` myself and they agree with the
amendment: `update_project` (`ProjectController.php`) checks the body against `name` and
`description` and answers 422 `Validation failed.` before it reads the UUID or looks up a project.
`validateIncomingRequest` needs `Content-Type: application/json` and a non-empty JSON body, which
the probe sends. `deploy` (`DeployController.php`) answers 400 `You must provide uuid or tag.`
with neither. `envs` (`ApplicationsController.php`) answers 404 for an unknown application and
otherwise passes each entry through `removeSensitiveData`. `current_team` returns the team with
its `id`. The token page is `/security/api-tokens`.

## The measurement the merge waits on (D-82 item 14)

On `dev-coolify`, with three throwaway tokens Devon mints there and revokes after the run:
**R** (`read`), **RWD** (`read` + `write` + `deploy`), and **RS** (`read` + `read:sensitive`).
Use a dev canary application holding one non-secret variable, and a random RFC UUID for the
project path. Each request's expected answer:

| Request | Control (`<digits>\|<random>`) | R | RWD | RS |
|---|---|---|---|---|
| `GET /api/v1/version` | 401 | 200 | 200 | 200 |
| `GET /api/v1/teams/current` | 401 | 200, same `id` (an integer) | 200, same `id` | 200, same `id` |
| `PATCH /api/v1/projects/<random UUID>`, `Content-Type: application/json`, body `{"sds_scope_probe": true}` | 401 | 403 `Missing required permissions: write` | 422 `Validation failed.` | 403 |
| `GET /api/v1/deploy`, no query | 401 | 403 `Missing required permissions: deploy` | 400 `You must provide uuid or tag.` on `4.0.0-beta.473` (source); 405 `This endpoint has changed to a POST request.` on `4.7.6` (measured, Round 1) | 403 |
| `GET /api/v1/applications/<dev canary uuid>/envs` | 401 | 200, a list of at least one entry, none with `value` or `real_value` | the same as R | 200, entries carrying `value` and `real_value` |

Also check that the project the PATCH names still doesn't exist afterward, and that no deployment
was queued (the dev Coolify's deployment list is unchanged). Print only statuses, the team `id`,
whether each entry has the two fields, and the fingerprints (`rotation_worker/values.py`) of the
tokens, never a token or an env value. If any answer differs, the adapter's `WRITE_ANSWERS`,
`DEPLOY_ANSWERS` or the canary rule changes before merge.

## What the canary gate must create

In production Coolify: one application, used only by this probe, holding exactly one environment
variable whose value is no secret (for example `SDS_CANARY=canary`). Nothing else may read or
deploy from it, and it needn't run. Coolify stores the variable twice (`is_preview` false and
true), which is fine. Its application uuid goes in the VPS host executor's
`/etc/rotation-worker/config.toml` as `coolify_canary_application = "<uuid>"`; no repository
file holds it. The variable must stay: an empty env list refuses every verify-new.

## Mac notes: what I found

At `9caad335`, from the worktree:

- `git grep -n "run-estate-landing.sh\|run-revision-watcher.sh" 9caad335` outside `docs/decisions`,
  `docs/history`, `docs/superpowers` and `tests/` found only the registry, the BWS manifest's
  comments, operations docs, compose comments, `layers.toml`, the launchers' own usage lines and
  `sds-deadman.sh` comments. No scheduler.
- The tracked plists (`scripts/com.devon.activation-sweep.plist`,
  `scripts/com.devon.rotation-worker.plist`) and their installers name neither lane.
- `grep -l "estate-landing\|revision-watcher" ~/Library/LaunchAgents/*.plist` found nothing.
- `launchctl list | grep -i "estate\|revision"` found only `com.devon.estate-dashboard`, an
  unrelated job.
- The lanes' crontabs are the scheduled readers: `16 * * * * lane-run estate-landing --submit` and
  `40 * * * * lane-run revision-watcher`, both in containers.

So I removed the Mac path from both rows' notes. `test_no_note_names_a_mac_reader` holds it.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_319`: **14890 items collected; 14889 passed, 1
  skipped, 0 failed**; ruff and pyright clean. That run was on `f43e9c28`. After the review fixes
  (`abc1bbfd`), I reran the rotation, architecture and services tests touching the change: 2672
  passed. A follow-up after the advisor's review (the next bullet list) changed code again, and
  the final `make check` on that tree: **14891 collected; 14890 passed, 1 skipped, 0 failed**,
  ruff and pyright clean. `/code-review` and the vendor review ran on the earlier diff, before
  that follow-up.
- Follow-up changes:
  - Renamed the setting `coolify_token_canary_application` to `coolify_canary_application`. The
    old name is 32 characters, and the orchestrator's rotation-evidence scan
    (`rotation_evidence.LONG_RUN`, `[A-Za-z0-9_+/=]{32,}`) refuses any evidence string holding
    it. Failure reasons leave through `commands/fail`, which that scan doesn't read, but a note
    naming the setting would have been refused.
  - Added `test_the_configured_canary_reaches_the_built_provider`, from the config file through
    `cli.build_setup` and `worker._provider` to the adapter. Mutation check: deleting the
    setting's line in each of `config.py`, `cli.py`, `worker.py` and `adapters/__init__.py`
    fails the test, four of four.
  - Rewrote the operations page's wrong-scope recovery as numbered steps, using the requeue route
    in its "Alerts" table: nothing requeues a failed unit by itself.
  - Confirmed `worker._other_keepers` sees only these three rows: no other registry
    (infraops-mcp-server, change-manager, factory-runner) has a `provider = "coolify"` row, and it
    counts only `rotated_by_sds` rows whose keeper the instance reads. Confirmed
    `revoke_may_come_first` reaches `contract._verify_consumers`, which then takes either answer
    for the old value.
- CI on PR #820 at `603f2f16` (run 45882303070): `Quality`, `Rollout transcription currency`
  and `Runner consumer compatibility` all succeeded; `Quality` ran 14877 passed, 14 skipped.
- `code-standards risk`: `high-risk` (auth: the adapter and its two test files).
- `/code-review` (high): five findings. Fixed three: the config sample's placeholder (an operator
  copying it got the malformed-UUID refusal, not the named one), the latent note overwrite in
  `_verify_new`, and `same_identity`'s four requests (now one team read per value, which also
  proves liveness). Added one rationale sentence to the policy lists. Not changed: "the answers
  are unmeasured" is D-82 item 14's gate, and "the adapter enters the policy lists before a row
  uses it" is item 10's instruction.
- `vendor-review` (`/tmp/vendor-review-tkctsp69`): seats that ran were Claude and Codex, both
  finished; agy was off by configuration. Codex raised no finding. Nothing was dropped. One finding
  was kept, from Claude (low, `cited`): a wrong-scope mint binds from the inbox, because
  `same_identity` checks the team only, and then fails verify-new on every attempt
  (`coolify_api_token.py` lines 185-189). A repro under `<dir>/repro/` confirms the behavior: a
  `read` + `write` token passes `same_identity` for the `read` row, and `store` refuses it twice
  with `doesn't declare write`. The repro asserts the behavior, not a failure against the code, so
  the label stays `cited`. I documented the recovery on the operations page.
- Token-shape grep of the diff: `git diff 9caad335..HEAD | grep -nE '^\+.*(0\.<uuid>\.|ghp_|github_pat_|sk-[A-Za-z0-9]{20}|[0-9]+\|[A-Za-z0-9]{40})'` found nothing (exit 1). Every test token is built at run time with `secrets`.

## Files changed

`.cred-consumers.toml`, `docs/operations/rotation-executor.md`,
`src/orchestrator/factory-policy.toml`, `src/rotation_worker/adapters/__init__.py`,
`src/rotation_worker/adapters/coolify_api_token.py` (new), `src/rotation_worker/cli.py`,
`src/rotation_worker/config.py`, `src/rotation_worker/contract.py`, `src/rotation_worker/types.py`,
`src/rotation_worker/worker.py`, `tests/architecture/test_external_content_observes_only.py`,
`tests/architecture/test_rotation_agreement.py`, `tests/architecture/test_rotation_edges.py`,
`tests/architecture/test_wsp21_invariant_scan.py`, `tests/rotation_worker/test_client_surface.py`,
`tests/rotation_worker/test_coolify_api_token.py` (new),
`tests/rotation_worker/test_coolify_api_token_rows.py` (new),
`tests/rotation_worker/test_estate_landing_rows.py`, `tests/rotation_worker/test_postgres_role.py`,
and this report.

## Self-review

- Fails closed: every unexpected status, empty or malformed canary answer, and transport failure
  in a scope probe raises a `ContractError` naming the ability and the status or exception type.
  A probe's transport failure returns `None`, which the contract treats as indeterminate.
- The write probe can't write: the handler refuses the body before it reads the UUID, and the UUID
  is an RFC UUID, which a Coolify uuid never is. The deploy probe names no target.
- The revision watcher's read-only scope holds: its row declares `["read"]`, so a mint with
  `write` or `deploy` fails verify-new before `store`.
- `check_row` refuses `read:sensitive` and `root` as declared abilities, so no row can ask for
  them.

## Concerns

- **A wrong-scope mint binds from the inbox.** The inbox's identity check is the team only, so a
  token minted with the wrong abilities is written to staging, and verify-new then fails the
  `store` unit. A requeue alone fails it again until a person deletes the staging secret and mints
  again. It fails closed; the
  cost is attempts and a manual step. My own vendor-review seat raised it. I left the code as
  amendment 33 places the check (verify-new), documented the recovery on the operations page, and
  name it here for HQ. Moving a scope check into the inbox's bind would be a design change.
- **Early revoke needs hand staging.** After an exposure, if Devon revokes first, the inbox can't
  bind (the old value no longer answers `same_identity`), so the value is staged by hand. That is
  the inbox's existing rule for every type; the operations page says so.
- **Not covered by the standards section:** none beyond the above. The adapter adds no logging;
  `contract.py` files evidence as for every adapter.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** the adapter adds no logging and no file
  logging; the executor's existing logging and evidence path are unchanged.
- **Code Brain 4 (never swallow an exception):** no bare `except: pass`. A probe's
  `TransportFailure` becomes `None` (indeterminate, failing the step through the contract); a
  scope probe's becomes a `ContractError` naming the exception type; an unexpected status fails
  verify-new naming it.
- **Code Brain 5 (never log secrets):** evidence carries fingerprints and fixed-text notes naming
  abilities and statuses only. The bearer is set per request, never in a message. The canary's
  env list is checked in memory and never logged, stored or put in a refusal
  (`test_no_evidence_or_refusal_carries_a_value`).
- **Infra Brain 591, 592, 593 (no token in tracked files, history, or inline):** no token or
  token-shaped literal in code, tests or docs; test tokens are built at run time; the docs
  describe the shape (`<digits>|<random>`). The diff's token-shape grep found nothing.
- **Infra Brain 1032 (cred.exposure-rotate):** each row keeps valid exposure and consumer fields;
  the plan's `verify` unit waits unclaimed until every `runtime-fetch` reader's check has run since
  the keeper's revision, and only its completion sends the revoke alert
  (`test_each_reader_is_machine_read_so_the_revoke_waits_on_evidence`).
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. The adapter adds no endpoint and
  calls Coolify only at the executor's existing tailnet `coolify_url`; it refuses any URL that is
  neither https nor a tailnet address.

## Teardown

The worktree `.worktrees/hq-W-319` and the database `orchestrator_test_hq_w_319` stay in place:
the PR is unmerged, and HQ resumes here after the measurement. Remove both, and the branch, once
#820 merges.

## Round 1

Fix round 1 of 2, on Devon's session's dev measurement (D-82 item 14, 2026-11-26 about 19:30
UTC, PR #820 comment 7203058301). Versions: `dev-coolify` `4.7.6`, `prod-coolify`
`4.0.0-beta.473`.

### The measured table

Statuses and messages only; no token or fingerprint.

| Request | Control | R (`read`) | RWD (`read` + `write` + `deploy`) | RS (`read` + `read:sensitive`) |
|---|---|---|---|---|
| `GET /api/v1/version` | 401 | 200 | 200 | 200 |
| `GET /api/v1/teams/current` | 401 | 200, team `id` 0 | 200, team `id` 0 | 200, team `id` 0 |
| `PATCH /api/v1/projects/<random UUID>` | 401 | 403 `Missing required permissions: write` | 422 `Validation failed.` | 403, the write message |
| `GET /api/v1/deploy`, no query | 401 | 403 `Missing required permissions: deploy` | **405 `This endpoint has changed to a POST request.`** | 403, the deploy message |
| `GET /api/v1/applications/<canary>/envs` | 401 | 200, no `value` or `real_value` | 200, no `value` or `real_value` | 200, both fields |

The measurement had no side effect. Only the RWD deploy cell differs from the expected table,
which said 400; the expected table's cell is corrected in the preceding section.

### What Coolify's source says, per version

Read with `gh api repos/coollabsio/coolify/contents/<path>?ref=<tag>`, read-only, nothing run.

| | `v4.0.0-beta.473` | `v4.7.6` |
|---|---|---|
| The `/deploy` route | `routes/api.php` 76: `Route::match(['get', 'post'], '/deploy', [DeployController::class, 'deploy'])->middleware(['api.ability:deploy'])` | `routes/api.php` 161: `Route::get('/deploy', [OtherController::class, 'post_required'])->middleware(['api.ability:deploy'])`; 162: `Route::post('/deploy', [DeployController::class, 'deploy'])`, same middleware |
| Its group's middleware | `routes/api.php` 38-41: `auth:sanctum`, `ApiAllowed`, `api.sensitive` | `routes/api.php` 69-72: `auth:sanctum`, `api.token.team`, `ApiAllowed`, `api.sensitive` |
| What a GET reaches past the ability check | `DeployController.php` `deploy()` (351): with no `uuid` and no `tag`, 384 answers 400 `You must provide uuid or tag.` | `OtherController.php` `post_required()` (14-19): 405 `This endpoint has changed to a POST request.`, `Allow: POST` |
| The ability check | `app/Http/Middleware/ApiAbility.php` 9-26: `root` passes; otherwise Sanctum's `CheckForAnyAbility`, whose refusal becomes 403 `Missing required permissions: deploy` | The same file, 30-72: a token limited to some projects is refused 403 on routes outside `SCOPED_ACCESS_ROUTES` (`deploy` is inside it), then the same `root` pass and 403 refusal |

So on both tags the route-level `api.ability:deploy` runs before the GET's handler, which agrees
with the measurement (R got 403, RWD got 405). The group middleware ahead of it answers only 401
(`auth:sanctum`, and on `4.7.6` `EnsureTokenBelongsToCurrentTeamMember` 26 and 34) or 403
(`ApiAllowed` "API is disabled." on both tags, beta.473's "not allowed to access the API" at 32,
and `4.7.6`'s `EXCEEDS_ACCESS_MESSAGE` at 57). Neither 400 nor 405 can reach a token without
`deploy` on either version. `invalidTokenResponse()` (a 400, `bootstrap/helpers/api.php` 41-44)
is called only inside the handler, past the ability check. A `root` token passes the ability
check and gets 400 or 405, as before; the canary probe is what refuses it.

### The change

- `DEPLOY_ANSWERS` is `{True: frozenset({400, 405}), False: frozenset({403})}` and
  `WRITE_ANSWERS` is `{True: frozenset({422}), False: frozenset({403})}`
  (`src/rotation_worker/adapters/coolify_api_token.py` 67-68); `check_scope` tests membership, and
  `_mismatch` names the set ("must answer 400 or 405; it answered HTTP 404"). The probe is still
  `GET /api/v1/deploy`; nothing sends `POST`. The module docstring says which version answers
  which.
- Tests (`tests/rotation_worker/test_coolify_api_token.py`): the simulated Coolify's held-deploy
  answer is a field (`deploy_get`, default 400). Added a held-deploy test for 400 and 405, a
  held-deploy answer outside the set (404) refused with its status named, an absent-deploy token
  refused and a read-only token passing with 403 on either version, a row that doesn't declare
  `deploy` refused when its token holds it and answers 400 or 405, and a team `id` 0 bind test.
  The existing 400 path (the full rotation's note "deploy held (400)") is kept. Because the source
  lets no token without `deploy` reach 400 or 405, the absent-deploy case stays a 403 test.

### The checks, no code change

- **Team `id` 0.** `_team` (193-205) returns the `id` when it is an `int` and not a `bool`, so 0
  comes back as 0; `same_identity` tests `new_team is not None` and then `==`. No other code reads
  the team. `test_team_id_zero_is_an_identity_not_a_missing_one` binds two team-0 values, and the
  simulated Coolify's default team was already 0 (`TEAM = 0`).
- **RS's PATCH 403 carries the write message.** The probe compares the status only
  (`_scope_status` returns `status_code`; the body is never read), so the message doesn't matter
  and RS's 403 is what a token without `write` must answer.

### Head and checks

- Fix commit `19fc2273`, fast-forward on `b1ba573e`; this report's commit follows it on
  `hq/coolify-token-adapter-code`.
- `make check` on the worktree with its own database (`orchestrator_test_hq_w_322`): lint and
  types clean, **14896 passed, 1 skipped**, 398.90s. The adapter's two test files alone: 53
  passed.
- Token-shape grep of the added lines of `git diff 9caad335..HEAD`
  (`grep '^+' | grep -nE '[0-9]+\|[A-Za-z0-9_-]{20,}|0\.[0-9a-f]{8}-[0-9a-f]{4}-|ghp_|github_pat_|sk-ant-|xox[bp]-'`):
  one hit, the original report's own grep pattern text; no token or token-shaped literal.
- `rotated_by_sds` stays unset (false) on `coolify-estate-lander-token`,
  `coolify-revision-watcher-read-token` and `coolify-change-window-token`; `.cred-consumers.toml`
  is unchanged in this round.

### Standards followed (Round 1)

- **Code Brain 3** (logging): no logging added; the adapter's notes and refusals go through the
  executor's existing evidence path.
- **Code Brain 4** (error handling): a transport failure stays an indeterminate step
  (`_scope_status` raises `ContractError` naming the error type); an answer outside a probe's set
  fails `verify_new` with the status named. No `except …: pass` added.
- **Code Brain 5** (security): refusals and notes name abilities and statuses only; no token,
  header or body is logged or kept.
- **Infra Brain 591, 592, 593**: no token or token-shaped literal added; test tokens are drawn
  at run time with `secrets` (the grep above).
- **Infra Brain 1032**: no row's exposure or consumer fields changed; `.cred-consumers.toml` is
  untouched this round.
- **Infra Brain 212** (HTTPS for public FQDNs): not applicable; no endpoint added.

### Follow-ups (not edits)

- No one-off measurement script exists: the dev measurement was run by hand in Devon's session,
  so a re-measurement on a later Coolify version is hand work too.
- Production's held-deploy answer on `4.0.0-beta.473` (400) rests on source, not measurement.
  Its first real rotation measures it, and fails closed on anything outside `{400, 405}`.
