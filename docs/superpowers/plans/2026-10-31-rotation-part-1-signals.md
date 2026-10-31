# Rotation Part 1: every signal has a home — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stopped worker, a failed or stuck rotation unit, and every proposer or decomposer finding each reach alert-hub as one keyed alert.

**Architecture:** The worker's existing `Notifier` gains subjects from the ledger read it already makes, plus a first-sighting clock for waits. The proposer and decomposer each gain a small `alerts.py` shaped like `bump_driver/alerts.py`. Liveness is a per-pass heartbeat that alert-hub's silence watch reads.

**Tech Stack:** Python 3.14, httpx through `ConfinedClient`, `alert_hub_client.KeyedAlerts`, bash launchers, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-rotation-layer-design.md`, Part 1.

## Global Constraints

- Worktree `.worktrees/rotation-p1`, `TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1/orchestrator_test_rotation_p1`. Absolute `cd` in every Bash call. Never touch the main tree.
- An alert failure must never fail a unit, a claim, or a pass.
- An instance alerts only about units it owns.
- Every new key goes through `alert_key`.
- `ROTATION_WAIT_ALERT_HOURS = 48`, `ROTATION_READER_WAIT_ALERT_HOURS = 192`, `ROTATION_APPROVAL_ALERT_HOURS = 48`, `ROTATION_REVIEW_ALERT_HOURS = 48`, `ROTATION_BEHIND_ALERT_DAYS = 7`: plain ints with no off value. `ROTATION_APPROVAL_ALERT_HOURS` is defined once per lane that uses it.
- Sender: worker `rotation-executor.` and `rotation-executor-keychain`; proposer and decomposer `sds-bridge`, env `SDS_BRIDGE_ALERT_TOKEN`, BWS `b415d03b-f92d-46b1-8a9d-b4f10103df49`, narrow identity.
- Every parsed `bws` call passes `--color no` (`sds_bws_value` does). No secret in a tool argument.
- No code changes under `src/orchestrator/`, so no image deploy.

**Differences from the spec, both Claude's calls:**

- `rotation-act` keys keep their bytes: `alert_key("rotation-act", uuid)` equals today's f-string, because a UUID holds only plain characters. Task 3 asserts this instead of migrating anything.
- The `rotation-executor.` sender keeps its trailing dot. `update_source` can't rename a sender, and a new sender means a new token on the VM for a cosmetic gain.

The spec's proof step is already done by reading: `alert-hub/src/engine/lifecycle.ts` returns `resolve_ignored`, with no alert and no notification, when a resolve finds nothing open. Task 8 confirms it live in the sender's history.

## Review Focus

- The ledger read fails mid-pass: every remembered key stays as it was, nothing resolves.
- alert-hub answers 500 or times out at heartbeat or sync: the pass still ends 0, and `submitted` is logged.
- A wait state file is corrupt or missing: the clock restarts, and nothing raises.
- A unit requeued from `failed` to `ready`: its failed alert resolves and its wait clock starts fresh, not from the old sighting.
- The other instance's failed unit in the ledger: this instance neither alerts nor resolves it.

---

### Task 1: Shared alert client additions

**Files:**
- Modify: `src/alert_hub_client/__init__.py`
- Test: `tests/alert_hub_client/test_first_sightings.py` (place beside the existing client tests)

**Interfaces:**
- Produces: `class FirstSightings(path: Path)`, `.update(current: Set[str], now: datetime, keep: Set[str] = frozenset()) -> dict[str, datetime]` returning each current key's first sighting, forgetting keys in neither `current` nor `keep`, and saving through a temporary file and rename. Corrupt or missing state reads as empty. An `OSError` on save raises `StateUnwritable`.
- Produces: `KeyedAlerts.heartbeat(key: str, title: str) -> None`, one `_send` with status `resolved` that touches no state and raises `AlertHubError` on failure.

- [ ] **Step 1: Write failing tests**

Tests: `test_a_key_keeps_its_first_sighting`, `test_a_key_gone_from_current_is_forgotten_so_its_clock_restarts`, `test_a_kept_key_survives_a_pass_that_did_not_read_it`, `test_corrupt_state_reads_as_empty`, `test_heartbeat_posts_resolved_and_writes_no_state`, `test_heartbeat_failure_raises_alert_hub_error`.

- [ ] **Step 2: Run them and see them fail**, with `.venv/bin/pytest tests/alert_hub_client -q`.
- [ ] **Step 3: Implement both.**
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit.**

### Task 2: Ownership

**Files:**
- Modify: `src/rotation_worker/envelope.py`, `src/rotation_worker/worker.py:265-273`
- Test: `tests/rotation_worker/test_ownership.py`

**Interfaces:**
- Produces: `envelope.KEYCHAIN_ADAPTERS: Final[frozenset[str]]`, and `worker._ours(setup, unit) -> bool`: `adapter_name in KEYCHAIN_ADAPTERS` equals `setup.store.read_back_kind == "keychain"`.

- [ ] **Step 1: Write failing tests**

Tests: `test_the_copy_equals_the_orchestrators` (imports `orchestrator.services.lifecycle.rotation_claims.KEYCHAIN_ADAPTERS`), `test_an_adapter_this_build_does_not_know_is_the_vms`, `test_the_october_30_case_stays_quiet_on_the_keychain_instance` (an unknown adapter on a Keychain store: `_take` returns 0, with no `unit_refused`, no claim, no intake read), `test_an_unknown_adapter_on_the_vm_is_still_refused`.

- [ ] **Step 2: Run, fail. Step 3: Implement; update `_ours`'s docstring to state the server's rule. Step 4: Run, pass, including `tests/rotation_worker`. Step 5: Commit.**

### Task 3: Failed, review, stuck, and waiting units

**Files:**
- Modify: `src/rotation_worker/worker.py`, `src/rotation_worker/notify.py`
- Test: `tests/rotation_worker/test_unit_alerts.py`

**Interfaces:**
- Consumes: `FirstSightings`, `envelope.KEYCHAIN_ADAPTERS`.
- Produces: `PassActs` gains `waits: dict[str, Wait]` and `seen: set[str]`, where `Wait(reason: str, hours: int, reader: bool)`. Keys: `unit_failed_key(unit_id)`, `unit_review_key`, `unit_stuck_key`, `unit_waiting_key` in `notify.py`, each `alert_key("rotation-unit-<kind>", unit_id)`.

Behavior:
- `waiting_acts` is renamed `unit_alerts` and keeps its return shape. For each ledger row in `failed` or `revision_required` that is a rotation unit this instance owns, it adds `rotation-unit-failed`. The message holds the credential, `last_failure` scrubbed and capped at `MAX_REASON`, and the occurrence's staging key name, with a link to `/review/units/<id>`. For `awaiting_review` with `last_event_at` older than `ROTATION_REVIEW_ALERT_HOURS`, it adds `rotation-unit-review`. A transient read of the unit puts every one of that unit's keys in `unread`.
- A row is read only when its state or a pending blocker needs it, so a completed estate adds no calls.
- Each `unit_waiting` log site (`_take:228`, `_take:260`, `_adapter_waits`, `_overlap_waits`, `_restart_waits`) also records `found.waits[unit_id]`; the reader waits at `:228` and `:260` set `reader=True`. `unit_skipped` records a wait only for a unit `_take` reached past `_ours`, tracked in `found.seen`.
- `unit_not_worked`, and a `_refuse` whose claim raised, record `rotation-unit-stuck` on the same clock with the 48-hour threshold.
- `run_pass` updates `FirstSightings(state_dir / "wait-since.json")` once after the unit loop, then raises each wait past its threshold. Waits are keyed by unit and survive only while the unit is sighted.
- The mint waits keep their own `rotation-mint` alert and take no clock.
- All of this stays inside the existing `try` in `run_pass`, so it can't fail a pass.

- [ ] **Step 1: Write failing tests**

Tests: `test_a_failed_owned_unit_raises_one_alert_naming_the_reason`, `test_a_failed_foreign_unit_raises_nothing`, `test_a_requeued_unit_resolves_its_failed_alert`, `test_a_bump_unit_in_revision_required_raises_nothing`, `test_awaiting_review_alerts_at_48_hours_and_not_before`, `test_a_wait_alerts_at_48_hours_and_not_at_31`, `test_a_reader_wait_alerts_at_192_hours_and_not_at_168`, `test_a_requeued_unit_starts_its_clock_fresh`, `test_a_skip_on_a_unit_never_reached_past_ours_starts_no_clock`, `test_not_worked_raises_stuck_after_the_threshold`, `test_an_unread_unit_holds_its_keys`, `test_a_ledger_outage_resolves_nothing_and_the_pass_ends_normally`, `test_an_alert_hub_outage_never_fails_a_unit`, `test_act_keys_keep_their_bytes`, `test_a_corrupt_clock_file_restarts_the_clock`.

- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run, pass.**
- [ ] **Step 5: Mutate each guard** (the ownership filter, the threshold comparison, the `unread` hold, the reader flag), clearing `__pycache__` around each, and report the count and every survivor.
- [ ] **Step 6: Commit.**

### Task 4: Heartbeat and behind `main`

**Files:**
- Modify: `src/rotation_worker/cli.py` (`run`), `src/rotation_worker/notify.py`, `src/rotation_worker/git_mirror.py`, `src/rotation_worker/worker.py`
- Test: `tests/rotation_worker/test_heartbeat.py`, `tests/rotation_worker/test_behind_main.py`

**Interfaces:**
- Produces: `Notifier.heartbeat(store_kind: str) -> None`, key `alert_key("rotation-heartbeat", store_kind)`, swallowing every `AlertHubError` to `notify_failed`.
- Produces: `RepoMirror.oldest_change_since(repository: str, revision: str, paths: Sequence[str]) -> datetime | None`, using `git log --format=%ct <revision>..refs/heads/main -- <paths>` and taking the last line. `revision` must match `config.REVISION_PATTERN`.
- Produces: `worker.behind(setup, now) -> tuple[dict[str, Act], set[str]]`, key `alert_key("rotation-instance-behind", store_kind)`, paths `src/rotation_worker`, `src/estate_clients`, `src/alert_hub_client`. It syncs `AlobarQuest/orchestrator` each pass. A `MirrorError` holds the key and logs `behind_unread`.

`run` sends the heartbeat after `pass_finished`. A pass that crashes sends none, which is the signal.

- [ ] **Step 1: Write failing tests**

Tests: `test_a_finished_pass_sends_one_heartbeat`, `test_a_crashed_pass_sends_none`, `test_a_heartbeat_failure_still_returns_0`, `test_no_notifier_means_no_heartbeat`, `test_six_days_behind_raises_nothing`, `test_eight_days_behind_raises_once`, `test_a_change_outside_the_paths_is_not_behind`, `test_a_revision_the_mirror_lacks_holds_the_key`, against a real bare git repository in `tmp_path`.

- [ ] **Step 2: Run, fail. Step 3: Implement; add the heartbeat and `behind` alert route facts to `tests/rotation_worker/test_client_surface.py` if it pins them. Step 4: Run, pass. Step 5: Commit.**

### Task 5: Proposer findings

**Files:**
- Create: `src/rotation_proposer/alerts.py`
- Modify: `src/rotation_proposer/cli.py`, `scripts/run-rotation-proposer.sh`, `tests/architecture/test_out_of_process_isolation.py`, `tests/architecture/test_wsp21_invariant_scan.py` if it requires a row
- Test: `tests/rotation_proposer/test_alerts.py`

**Interfaces:**
- Consumes: `Outcome`, `FINDING_STATUSES`, `FirstSightings`.
- Produces: `sync_alerts(outcomes: Sequence[Outcome], *, token: str, state_dir: Path, now: datetime, transport: httpx.BaseTransport | None = None) -> str | None`. Keys: `alert_key("rotation-finding", credential_id, status)` and `alert_key("rotation-approval", credential_id)`.

Behavior:
- Each finding fires once and resolves on the first pass that no longer reports it. `awaiting-approval` and `declined` fire after `ROTATION_APPROVAL_ALERT_HOURS`.
- `run` calls `sync_alerts` only on `--submit`, after `_pass` returned. A pass that exits 2 syncs nothing, so every key holds.
- A sync failure prints to stderr and leaves the exit code alone.
- State dir: `ROTATION_PROPOSER_STATE_DIR`, else `~/Library/Application Support/sds/rotation-proposer`.
- The launcher reads `SDS_BRIDGE_ALERT_TOKEN` on `--submit` through the narrow identity and keeps `--finding 3`. `_credentials` requires it on `--submit`.

- [ ] **Step 1: Write failing tests**

Tests: `test_each_finding_status_raises_one_keyed_alert`, `test_a_finding_that_stops_resolves`, `test_awaiting_approval_waits_48_hours`, `test_declined_waits_48_hours`, `test_a_dry_run_sends_nothing`, `test_an_unusable_pass_syncs_nothing`, `test_a_sync_failure_keeps_the_exit_code`, `test_no_key_is_a_substring_of_another_finding_kind`, and the launcher test `test_the_launcher_reads_the_bridge_token_only_on_submit`.

- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run, pass, including `tests/architecture/test_out_of_process_isolation.py`. Step 5: Commit.**

### Task 6: Decomposer waits

**Files:**
- Create: `src/rotation_decomposer/alerts.py`
- Modify: `src/rotation_decomposer/cli.py`, `scripts/run-rotation-decomposer.sh`, the same architecture tests as Task 5 if they require a row
- Test: `tests/rotation_decomposer/test_alerts.py`

**Interfaces:**
- Produces: `sync_alerts(outcomes, *, token, state_dir, now, transport=None) -> str | None` with the Task 5 shape, keyed `alert_key("rotation-decomposition", outcome.subject)`.

An `awaiting-approval` outcome past 48 hours fires. A pass with an `unread` outcome holds every key. A `--dry-run` sends nothing. The launcher adds the token to its environment. The outcome alarm stays.

- [ ] **Step 1: Write failing tests**

Tests: `test_a_proposal_waiting_48_hours_alerts`, `test_a_decided_proposal_resolves`, `test_an_unread_pass_holds_every_key`, `test_a_dry_run_sends_nothing`.

- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run, pass. Step 5: Commit.**

### Task 7: Docs and the whole gate

**Files:**
- Modify: `docs/operations/rotation-executor.md`, `docs/operations/rotation-proposer.md`, `docs/operations/deploy.md`, `CLAUDE.md`, `PROJECT.md`

- [ ] **Step 1: Write the docs**

`rotation-executor.md` gains an "Alerts" section: each key, what raises it, what clears it, and the next step, plus the reinstall rule. `deploy.md` states that a change to the three paths reinstalls both instances. `CLAUDE.md` gets invariants below the managed block for ownership (the copy and its test), the wait clock, and the heartbeat. Fix `ADR-0055:1846`'s key grammar by the amendment in Part 5, not here.

- [ ] **Step 2: Run `make check` with the database and `SECURITY_STANDARDS_DIR` set**

Run: `rtk proxy make check > /private/tmp/claude-501/-Users-devon-Projects-orchestrator/9e28ea29-0ae6-4126-b510-9ad6a925a3d9/scratchpad/p1-check.log 2>&1`, then read the collected-test count.
Expected: exit 0, with no fewer tests collected than `main`.

- [ ] **Step 3: Whole-branch review by a fresh reviewer, then `/code-review`.**
- [ ] **Step 4: Commit, and open the pull request.**

### Task 8: Deploy and live proof

This task writes to production. It runs only after Devon approves this plan.

- [ ] **Step 1: Confirm no rotation unit is ready, claimed, or executing** through the observer. Merge, then confirm `main` holds the commit.
- [ ] **Step 2: Reinstall the VM instance** with `provision.sh` at the full merge commit, and the Mac with `scripts/install-rotation-worker-launchd.sh <commit>` from the main tree. Confirm both log the new `revision`, and that `behind` raises nothing.
- [ ] **Step 3: Confirm the heartbeat.** Each sender's `lastSeenAt` advances every pass, and `search_history` shows no new row.
- [ ] **Step 4: Set `expectedIntervalS: 1800`** on senders `13501e98-29cd-4026-b23d-3318578621bf` and `fc44fc18-e718-42fc-9347-4709c697bbf8`, only after step 3.
- [ ] **Step 5: Prove liveness.** Confirm no rotation unit is ready, `launchctl bootout` the Mac agent, wait past 30 minutes, confirm `silent:rotation-executor-keychain` opens, bootstrap the agent, and confirm it resolves.
- [ ] **Step 6: Run the proposer and decomposer on their schedule** and confirm 0 findings raises nothing and the launchers read the token (their next pass exits as before).
- [ ] **Step 7: Update the SDS Layer Map and SDS year plan artifacts**, and list every ruling with its cost if wrong.

**No live proof in this part, stated plainly:** `rotation-unit-failed`, `-review`, `-stuck`, `-waiting`, `rotation-instance-behind`, and the findings alerts have tests only, until a real event. A canary rotation failed on purpose could prove the reporting path, and that needs Devon's say-so. Worker liveness is proven live on the Mac only; the VM uses the same code and a different sender.

## Live proof, recorded 2026-10-31

Merged as `4e850fbc` (#570). Both instances run that commit, and each `source_revision` matches. No orchestrator image changed.

- **Heartbeat:** each sender's `lastSeenAt` advanced on its pass, and alert-hub's history gained no row.
- **Silence watch:** with `expectedIntervalS: 1800` set and the Mac agent stopped at 20:49:32 UTC, `silent:rotation-executor-keychain` opened at 21:20:19. After the agent restarted, it resolved at 21:26:19.
- **Launchers:** the decomposer and proposer each passed with exit 0 at this commit, and the proposer wrote `alerts.json` and `approval-since.json`, so it read the `sds-bridge` token.
- **First run:** no worker or lane alert fired on production state.

Not proven live: `rotation-unit-failed`, `-review`, `-stuck`, `-waiting`, `rotation-instance-behind`, the findings alerts, and the VM's silence alert. These have tests only until a real event.

Deferred, with their cost if wrong in the session's rulings: the duplicated `alerts.py` shape in two lanes, per-pass reads of failed non-rotation units, and the per-pass fetch of the behind check. The `rotation-executor.` sender keeps its trailing dot.
