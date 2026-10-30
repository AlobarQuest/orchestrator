# Postgres role rotation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `postgres-role` a seventh rotation type, so the Authentik database password rotates through the existing process with one click.

**Architecture:** A forced-command helper on the VPS is the executor's only reach into a database. A new provider drives it from the existing `host` step, restoring the old value on failure. Everything else is the existing derived plan, generated package, and policy recognition.

**Tech stack:** Python 3.14 (executor), stdlib-only Python 3.12 (helper), system `ssh`, Coolify API, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-postgres-role-rotation-design.md`

## Global constraints

- Passwords never reach argv, logs, evidence, or a tool argument; they travel on stdin only.
- Passwords match `^[0-9a-f]{64}$`.
- Existing plans, criteria, and authority fingerprints don't change: `overlap` defaults to true.
- `host_without_overlap` keeps its current meaning and stays unrecognised by `rotation_known_good`.
- Word guards: read the `ws32` and `ws34` lists before naming anything under `src/orchestrator/`.
- New files get a `layers.toml` entry. `make check` must pass with its own test database.

## Review focus

- The database accepts the new value but a consumer never loads it: host restores the old value (Task 3).
- The undo itself fails: the unit fails with both errors named and no value in the message (Task 3).
- A probe connects over loopback, which trusts every password: `probe` must use the non-loopback address (Task 1).
- A value with a quote or newline arrives at the helper: refused before any `docker` call (Task 1).
- The SSH host key differs from the pinned one: the call fails closed, with no password sent (Task 2).

---

### Task 1: The helper

**Files:**
- Create: `deploy/vps/sds-pg-rotate` (stdlib Python, executable)
- Test: `tests/deploy/test_sds_pg_rotate.py`
- Modify: `layers.toml`

**Interfaces:**
- Produces: stdin one JSON line `{"verb","target","password"?}`; stdout one JSON line. `set` → `{"changed": bool}`, `probe` → `{"status": 200|401|null}`, `loaded` → `{"fingerprints": {container: str}}`, refusal → `{"refused": "<reason>"}` with exit 2.

- [ ] **Step 1: Write failing tests** with a fake `docker` on `PATH` and a temporary `SDS_PG_ROTATE_TARGETS` path: `test_set_alters_role_over_stdin`, `test_set_is_a_no_op_when_the_password_already_works`, `test_probe_maps_auth_failure_to_401`, `test_probe_maps_any_other_failure_to_null`, `test_probe_never_uses_loopback`, `test_unknown_target_is_refused`, `test_unknown_verb_is_refused`, `test_non_hex_password_is_refused_before_docker_runs`, `test_no_password_appears_in_docker_argv`, `test_loaded_returns_fingerprints_only`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/deploy/test_sds_pg_rotate.py -v`. Expected: FAIL, file missing.
- [ ] **Step 3:** Implement. Targets TOML: `[target.<name>]` with `container`, `role`, `database`, and `[[target.<name>.consumer]]` `container`, `env`. The targets path is a constant in production; the test override is read only when the file's owner isn't root.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit.

### Task 2: The connection and Coolify services

**Files:**
- Create: `src/rotation_worker/helper_client.py`
- Modify: `src/rotation_worker/coolify.py`, `src/rotation_worker/config.py`, `src/rotation_worker/adapters/__init__.py`
- Test: `tests/rotation_worker/test_helper_client.py`, `tests/rotation_worker/test_coolify.py`, `tests/rotation_worker/test_config_logs_store.py`

**Interfaces:**
- Produces: `HelperClient(host: str, known_host: str, key: Value, state_dir: Path)` with `set(target: str, value: Value) -> bool`, `probe(target: str, value: Value) -> int | None`, `loaded(target: str) -> dict[str, str]`; each raises `ContractError` with no value in the message.
- Produces: `CoolifyClient(base_url, token, resource_type: Literal["application", "service"], uuid, transport=None)`; a service `set_variable` sends one PATCH with no `is_preview`, an application keeps `PREVIEW_ROWS`. Update existing callers.
- Produces: config `pg_helper_key_id`, `pg_helper_host`, `pg_helper_known_host`, all optional together; `AdapterContext` gains `pg_helper: Callable[[], HelperClient] | None` built lazily.

- [ ] **Step 1: Write failing tests:** `test_ssh_argv_pins_the_host_key_and_batch_mode`, `test_value_goes_on_stdin_not_argv`, `test_key_file_is_0600_and_removed_after_the_call`, `test_host_key_mismatch_raises_without_the_value`, `test_service_write_sends_one_row`, `test_application_write_still_sends_two_rows`, `test_permits_is_exact_for_a_service`, `test_helper_settings_are_all_or_none`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement. `ssh -i <file> -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<file>`.
- [ ] **Step 4:** Run them plus `tests/rotation_worker/`. Expected: PASS.
- [ ] **Step 5:** Commit.

### Task 3: The type and provider

**Files:**
- Create: `src/rotation_worker/adapters/postgres_role.py`
- Modify: `src/rotation_worker/plans.py`, `src/rotation_worker/types.py`, `src/rotation_worker/contract.py`, `src/rotation_worker/adapters/__init__.py`, `src/rotation_worker/adapters/orchestrator_bearer.py`, `src/rotation_worker/adapters/change_manager.py`, `src/rotation_worker/registry.py`
- Test: `tests/rotation_worker/test_postgres_role.py`, `tests/rotation_worker/test_plans.py`

**Interfaces:**
- Consumes: `HelperClient`, `CoolifyClient` from Task 2.
- Produces: `Capabilities.overlap: bool = True`; `Provider.consumer_loaded(consumer: Mapping[str, Any], new: Value) -> bool`, which existing hosted providers answer with `accepts(self, new)` and `UnitRun._holds` calls for `HOSTED_KINDS`; `NAME = "postgres-role"`; `build_provider(...)`.

- [ ] **Step 1: Write failing tests:** `test_derived_plan_is_the_expected_nine_steps`, `test_existing_types_derive_unchanged`, `test_host_order_is_coolify_then_set_then_restart_then_loaded`, `test_a_failure_at_set_restores_the_old_value_and_raises`, `test_a_failure_at_restart_restores_the_old_value_and_raises`, `test_a_failure_at_loaded_restores_the_old_value_and_raises`, `test_a_failed_undo_names_both_failures_with_no_value`, `test_probe_is_live_at_200_dead_at_401`, `test_revoke_raises_because_the_old_value_should_already_be_refused`, `test_consumer_loaded_needs_fingerprints_and_health_200`, `test_a_row_with_no_helper_target_is_refused_before_the_claim`, `test_hosted_providers_consumer_loaded_matches_accepts`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement. `LOADED_WAIT` 180 seconds, polling every 5. `helper_target` and `health_url` are read from the registry row; a `health_url` must be `https`. Register `"postgres-role"` in `TYPES`, `ADAPTERS`, and `CLASS_TYPES["postgres-role-password"]`.
- [ ] **Step 4:** Run them plus `tests/rotation_worker/`. Expected: PASS.
- [ ] **Step 5:** Mutate each undo branch and the host order, clearing `__pycache__` around each mutation. Report the count and every survivor.
- [ ] **Step 6:** Commit.

### Task 4: Package generation, recognition, and the amendment

**Files:**
- Modify: `src/rotation_proposer/generate.py`, `src/orchestrator/factory-policy.toml`, `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- Test: `tests/rotation_proposer/` generation tests, `tests/services/test_rotation_decomposition_policy.py`

**Interfaces:**
- Consumes: `Capabilities.overlap`.
- Produces: `HOST` condition for `overlap=False`, exactly: "The hosted service replaces the old value with the new one, and its dependents restart; they're unavailable until the restart finishes." Package prose adds that the restart can happen at any hour of the change window.

- [ ] **Step 1: Write failing tests:** `test_generated_package_for_postgres_states_the_outage`, `test_existing_generated_packages_are_unchanged`, `test_known_good_recognises_a_postgres_role_unit`, `test_known_good_still_refuses_host_without_overlap`, `test_decomposer_generates_postgres_units_with_no_person_act`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement. Add `postgres-role` to the adapter list of each `rotation_known_good` row the package's reach uses. Bump the policy version if `test_factory_policy.py` requires it. Write amendment 17: a type with no overlap, and the helper as the executor's new reach.
- [ ] **Step 4:** Run them. Expected: PASS.
- [ ] **Step 5:** Commit.

### Task 5: Operating it

**Files:**
- Modify: `src/rotation_worker/cli.py`, `docs/operations/rotation-executor.md`, `CLAUDE.md`
- Test: `tests/rotation_worker/test_cli.py` (or the existing CLI test module)

**Interfaces:**
- Produces: `rotation-worker adopt-keeper <credential>` (value on stdin; refuses anything but `^[0-9a-f]{64}$` for this class) and `rotation-worker pg-restore <credential>` (reads the keeper, repeats host's writes).

- [ ] **Step 1: Write failing tests:** `test_adopt_keeper_refuses_a_wrong_shape`, `test_adopt_keeper_reads_stdin_not_argv`, `test_pg_restore_uses_the_keepers_value`, both through the CLI entry point.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement, then document the helper install, key creation, and targets file in the runbook, add "A failed Postgres host" under "If a rotation stalls after host", and state the rule once in `CLAUDE.md`'s invariants.
- [ ] **Step 4:** Run `make check` with the worktree's database. Expected: PASS.
- [ ] **Step 5:** Commit, then run `/code-review`.

### Task 6: Live run (after merge and deploy)

- [ ] Create the helper's key, store it in BWS `Rotation / Admin`, install the helper, targets file, and `authorized_keys` line. Prove `ssh` can't run any other command, and that a wrong pinned host key is refused.
- [ ] Measure `rotation-coolify-token` writing a throwaway variable on a throwaway service.
- [ ] Merge the infraops change: class table (`executor: false`, `maxAgeDays: Infinity`) and the `alobar-id-postgres` row.
- [ ] Update the VM to the merged commit, then run `adopt-keeper` and confirm its fingerprint is `5ab2e4d8eec1`. Delete `Claude/AUTHENTIK_POSTGRES_PASSWORD`.
- [ ] Set `rotate_requested`, approve the revision (the one click), and watch every unit to completion.
- [ ] Verify the old value is refused, `id.alobar.net` is healthy, and `record-rotation` ran.
