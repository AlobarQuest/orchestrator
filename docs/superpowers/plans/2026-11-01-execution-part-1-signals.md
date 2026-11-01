# Execution Part 1: every signal has a home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stalled, held, or failed unit of any class raises one keyed alert that resolves when the unit clears.

**Architecture:** A new observer-readable route serves one row per open unit. A new scheduled lane, `execution_watch`, turns rows into keys through `KeyedAlerts` and `FirstSightings`, and skips each condition another lane holds. It reads the orchestrator and writes only to alert-hub.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, `httpx` through `ConfinedClient`, launchd.

**Spec:** `docs/superpowers/specs/2026-11-01-execution-layer-design.md`, Part 1. Devon's answers: the withdrawn-grant cleanup is accepted, `revision_required` alerts unless a bump lane owns it, and a missing cost actual stays a report.

## Global Constraints

- Keys: `execution-unit-failed:<unit>`, `execution-unit-held:<unit>`, `execution-stalled:<unit>`, `execution-dispatch-blocked:<unit>`, `execution-run-unclaimed:<unit>`, and `execution-watch-unread`.
- Thresholds: dispatch blocked 1 hour by first sighting, run unclaimed 2 hours, unread 2 hours. Each is a plain int with a real default and no off value.
- Runs every 15 minutes on the observer bearer (`orchestrator-observer`). Alert token: `sds-bridge`, through `sds_bws_value` with `--color no`.
- The observer gains one read and no write. Don't widen `require_operator_actor`.
- The first pass alerts on nothing. A missing known-subjects file refuses, and never re-baselines.
- `src/orchestrator/` word guards apply; reword, never allowlist.
- Worktree `.worktrees/execution-p1`, venv, and database `orchestrator_test_execution_p1`, with `PGTZ=UTC`.

## Review Focus

- A subject that clears while the lane is down: on return, it resolves, and it doesn't re-fire.
- A known subject that clears and returns: it counts as new, and it alerts.
- A bump unit whose repository loses its grant: the bump lane stops covering it, so this lane must alert.
- A failed dispatch followed by a later success: the latest record decides, so nothing stays raised.
- An orchestrator deploy: an unreadable pass holds every key and resolves none.

---

### Task 1: The `execution-status` read

**Files:**
- Create: `src/orchestrator/services/reporting/execution_status.py`
- Modify: `src/orchestrator/api/routes/reporting.py`, its response schema, `tests/architecture/test_scope_guards.py`, `tests/architecture/test_ws32_scope_guards.py`, `layers.toml`
- Test: `tests/services/test_execution_status.py`, `tests/api/test_execution_status_route.py`

**Interfaces:**
- Produces: `GET /api/v1/execution-status` returning `list[ExecutionStatusRow]`, with these fields: `unit_id`, `state`, `change_class: str | None`, `is_rotation: bool`, `standing: bool`, `target_repository: str | None`, `granted: bool`, `latest_dispatch_status: str | None`, `latest_dispatch_at: datetime | None`, `has_claim: bool`, `lease_lapsed: bool`, `breaker_open: bool`.

- [ ] **Step 1: Write failing tests.** `test_a_completed_or_cancelled_unit_has_no_row`, `test_lease_lapsed_is_true_only_past_grace` (uses `execution_stall`'s grace, by shrinking the setting), `test_the_latest_dispatch_record_decides`, `test_granted_reads_the_member_record`, `test_observer_may_read_and_a_worker_may_not`, and `test_a_rotation_unit_says_so`.
- [ ] **Step 2: Run them.** Expected: FAIL, route not found.
- [ ] **Step 3: Implement `execution_status(session, settings) -> list[ExecutionStatusRow]`.** Reuse `is_rotation_unit`, `service_for_change_class`, `MemberRecordGrants`, `stalled_executions`, and `dead_letter`'s circuit-breaker reader. Read members once per call. The route gates inline on OPERATOR or OBSERVER.
- [ ] **Step 4: Add the route to the GET inventory, and confirm `reporting.py` already sits in `WS42_DISPATCH_PATHS`.** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 2: The alert decisions

**Files:**
- Create: `src/execution_watch/__init__.py`, `src/execution_watch/decide.py`
- Test: `tests/execution_watch/test_decide.py`

**Interfaces:**
- Consumes: Task 1's row shape, as a local frozen dataclass.
- Produces: `conditions(row, now) -> set[Condition]`, `owned_elsewhere(row, condition) -> bool`, and `firing(rows, first_seen, known, now, thresholds) -> dict[str, Alert]`.

- [ ] **Step 1: Write failing tests, one per row of this table.**

| Condition | Rule | Owner, when |
|---|---|---|
| unit failed | State `failed` | `work_watcher`: `dependency-update` |
| unit held | State `blocked` or `revision_required` | `bump_driver`: `dependency-update`, `standing`, `granted`, and `revision_required` |
| stalled | `lease_lapsed` | None |
| dispatch blocked | `breaker_open`, or latest status `blocked` or `failed` for 1 hour | `bump_driver`: `dependency-update`, `standing`, `granted` |
| run unclaimed | Latest status `dispatched`, `has_claim` false, 2 hours since `latest_dispatch_at` | `bump_driver`: `dependency-update`, `standing`, `granted` |

Also write `test_a_rotation_unit_raises_nothing`, `test_a_bump_that_lost_its_grant_alerts`, `test_a_later_success_clears_dispatch_blocked`, `test_a_known_subject_stays_silent`, and `test_the_unclaimed_message_names_failing_then_cancelling`. Add `test_the_ownership_table_matches_the_bump_lane`, which runs `bump_driver`'s selection over the same row shapes.

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Each alert's message states the next step.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 3: The lane

**Files:**
- Create: `src/execution_watch/client.py`, `src/execution_watch/cli.py`, `scripts/run-execution-watch.sh`, `scripts/install-execution-watch-launchd.sh`, `scripts/com.devon.execution-watch.plist`
- Modify: `pyproject.toml` (console script), `layers.toml`, `tests/architecture/test_wsp21_invariant_scan.py` if needed, `docs/operations/local-development.md`
- Test: `tests/execution_watch/test_cli.py`, `tests/scripts/test_launchd_installers.py` coverage

**Interfaces:**
- Produces: console command `execution-watch`, with `--submit`, and `--seed`. Bare is a dry run that prints and sends nothing. State lives in `EXECUTION_WATCH_STATE_DIR`, else `~/Library/Application Support/sds/execution-watch`: `alerts.json`, `first-seen.json`, `known.json`.

- [ ] **Step 1: Write failing tests.** `test_seed_writes_known_subjects_and_sends_nothing`, `test_submit_without_known_refuses_and_raises_unread`, `test_an_unreadable_pass_holds_every_key`, `test_unread_alerts_after_two_hours_and_resolves`, `test_a_subject_gone_after_downtime_resolves`, `test_a_known_subject_that_clears_then_returns_alerts`, and `test_the_client_permits_only_execution_status`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** The client is a GET-only `ConfinedClient` confined to `/api/v1/execution-status`. Sightings save before alert-hub is asked, as in `held_alerts.py`. Ping Healthchecks through the launcher after each finished pass.
- [ ] **Step 4: Write the launcher and installer** from `run-work-carrier.sh`'s outcome pattern, reading the observer and `sds-bridge` tokens as `run-rotation-proposer.sh` does. Its exit codes follow the launcher header's convention. Prove the launcher under `env -i` with only `PATH` and `HOME`.
- [ ] **Step 5: Run `make check` with `PGTZ=UTC`.** Expected: all pass, reading the collected count.
- [ ] **Step 6: Commit.**

### Task 4: Land, deploy, and prove

- [ ] Run a whole-branch review and a mutation pass over `decide.py`, and report the count and every survivor.
- [ ] Open the pull request, and merge on green. Check nothing is active, deploy with `scripts/deploy_orchestrator.py`, and read `openapi.json` for the route and its response fields.
- [ ] From the main tree after it updates, run `execution-watch --seed`, create the Healthchecks check, and install the agent. The `sds-bridge` sender is shared, so Healthchecks, not `silent:`, hears this lane stop.
- [ ] Prove one kickstarted pass: exit 0, no alert fired, the Healthchecks check is up, and known.json records every subject the seed read.
- [ ] Update `CLAUDE.md`, the Layer Map, the year plan, and memory, and record the proof here.

## Live proof

This pass proves the read, the seed, the Healthchecks ping, and silence on history. Each alert has tests only until a real event. A staged failure on `sds-canary` proves more, and needs Devon's say-so first.

Recorded 2026-11-01: #583 deployed as `5d2af5f-exec1-amd64`, and Parts 2 to 5 as `750625c-exec2-amd64`, each with the digest matched and no migration. The observer read returned; the seed recorded 2 known subjects, both old failed `caller-pin` units; check `sds-execution-watch` was created; and scheduled passes read 2 raised, 0 firing. A pass after the second deploy read the same. Each alert has tests only until a real event, and a live claim refusal needs a real withdrawal.
