# Rotation Part 3: records stay true — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed rotation shows as failed, `lastRotated` follows every rotation, an orphan key is removed where it can be, and one rule compares destinations.

**Architecture:** infraops' `cred-findings` gains a read-only `lastRotated` map. The proposer gains a walk of open and retired rotation records, beside its walk of due credentials. The contract asks a provider to discard what it just minted when the next write fails.

**Tech Stack:** Python 3.14, TypeScript (infraops), httpx through `ConfinedClient`, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-10-31-rotation-layer-design.md`, Part 3. Devon approved this part's plan in advance on 2026-10-31.

## Global Constraints

- Orchestrator worktree `.worktrees/rotation-p2`, branch `rotation-p3-records`, database `orchestrator_test_rotation_p2`. infraops work uses its own worktree under `~/Projects/infraops-mcp-server/.worktrees/rotation-p3`, never its main tree.
- A repeat pass never resets an age clock: recording is idempotent by comparison.
- An unreadable state answer records nothing, and an absent field leaves today's behavior.
- Discarding a key deletes only the id the mint returned, never a listed key.
- No approved package changes what it does. `rotation-worker compare` proves every handed-over row equal before and after.
- No code changes under `src/orchestrator/` unless a task finds one necessary; word guards apply if so.

## Review Focus

- A record resolved by a person rather than `work-watcher`: nothing is recorded.
- A record resolved for an exposure: nothing is recorded, as today.
- A `cancelled` unit and a later completed revision: only the latest revision's record reports `stalled`.
- A `record-rotation` that fails: the pass reports it and retries next pass.
- A discard whose delete fails: the original failure still reaches the unit, and the alert says a key may be left.

---

### Task 1: infraops serves `lastRotated`

**Files (infraops worktree):**
- Modify: `src/cli/security-drift-cli.ts`, its test, and the fixture it shares with the orchestrator.
- Create: `tests/fixtures/rotation_class_policy.json` in both repositories, generated from `CLASS_POLICY` (class, `executor`).

**Interfaces:**
- Produces: `doCredFindings` prints `{"schema_version":1,"findings":[...],"last_rotated":{"<id>":"<iso>"}}`, keys limited to handed-over rows. `schema_version` stays 1 because the field is additive.

- [ ] **Step 1: Write the failing test** that the field appears, holds only `rotated_by_sds` rows, and is `{}` for an empty state.
- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run the suite, pass. Step 5: Commit in the infraops worktree.**

### Task 2: The proposer walks records

**Files:**
- Create: `src/rotation_proposer/client.py`
- Modify: `src/rotation_proposer/cli.py`, `src/rotation_proposer/findings.py`, `src/rotation_proposer/outcome.py`, `tests/fixtures/rotation_findings.json`, the architecture tables
- Test: `tests/rotation_proposer/test_records.py`, `tests/rotation_proposer/test_client_surface.py`

**Interfaces:**
- Produces: `client.Orchestrator(base_url, credential_key_id, token)` with `.work(record_id) -> dict`, GET-only, on the observer bearer, route `/api/v1/change-records/{id}/work` as the decomposer spells it.
- Produces: `findings.read_due` also returns `last_rotated: dict[str, str] | None`; `None` when the field is absent.
- Produces: `findings.record_rotation(credential_id, date)`, passing `--date <YYYY-MM-DD>`.
- Produces: `"stalled"` in `FINDING_STATUSES`.

Behavior, on `--submit`, after the due walk:
- For each rotation record with status `approved`, read its work. Any unit `failed` or `cancelled` on the record's named revision reports `stalled`, with the unit keys in the detail. A credential not due is still walked.
- For each rotation record `resolved` by `RETIREMENT_ACTOR`, whose trigger isn't an exposure, and whose credential is handed over: when its retired date is newer than `last_rotated[id]` or that id is absent, run `record_rotation`. Read the retired date from the field `_resolved` already uses, confirming it on a real record through the existing client before coding. `last_rotated is None` skips this walk.
- The old `_record` path through `_act` goes, so one path records. A failed `record-rotation` is an `error` outcome.
- A record the proposer can't read is `error`, which holds that credential's alerts.

- [ ] **Step 1: Write failing tests**

Tests: `test_a_failed_unit_reports_stalled`, `test_a_cancelled_unit_reports_stalled`, `test_all_completed_is_not_stalled`, `test_a_credential_not_due_is_still_walked`, `test_a_retired_record_records_its_date`, `test_a_repeat_pass_records_nothing`, `test_a_person_resolved_record_records_nothing`, `test_an_exposure_records_nothing`, `test_an_absent_field_skips_the_walk`, `test_a_failed_record_is_an_error`, `test_the_two_false_rows_repair` (July state, October record).

- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run `tests/rotation_proposer`, `tests/architecture`, pass.**
- [ ] **Step 5: Mutate each guard with a green baseline and cleared bytecode, and report the count and survivors. Step 6: Commit.**

### Task 3: A mint's failure removes its key

**Files:**
- Modify: `src/rotation_worker/contract.py`, `src/rotation_worker/adapters/openrouter.py`
- Test: `tests/rotation_worker/test_discard_mint.py`

**Interfaces:**
- Produces: optional provider method `discard_minted(value: Value) -> None`, implemented by OpenRouter with the id its mint returned; `contract` calls it when `getattr` finds it.

Behavior: when `_stage`, or `record` in `_mint`, raises after a mint in this process, the contract calls `discard_minted`, then re-raises the original. A failure in the discard becomes a `ContractError` naming both failures and saying a key may be left. A provider with no such method generates its value locally, so it has nothing to discard.

- [ ] **Step 1: Write failing tests**

Tests: `test_a_failed_stage_deletes_the_minted_key`, `test_a_failed_evidence_write_deletes_it`, `test_a_failed_delete_reports_both`, `test_a_staged_key_is_never_deleted`, `test_a_provider_with_no_discard_is_unchanged`, `test_only_the_returned_id_is_deleted`.

- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run `tests/rotation_worker`, pass. Step 5: Mutate. Step 6: Commit.**

### Task 4: One comparison and two agreement tests

**Files:**
- Modify: `src/rotation_worker/destinations.py`, `src/rotation_worker/checks.py`, `src/rotation_proposer/standing.py`, `src/rotation_decomposer/decompose.py`
- Test: `tests/rotation_worker/test_destination_rule.py`, `tests/architecture/test_rotation_agreement.py`

**Interfaces:**
- Produces: `destinations.same(a: Sequence[str], b: Sequence[str]) -> bool`, order-free, used at all three sites.

The agreement tests hold the four `touches_no_hosted_service` spellings equal, and hold every `CLASS_TYPES` key to `rotation_class_policy.json`, listing classes deliberately untyped.

- [ ] **Step 1: Write failing tests, Step 2: run, fail. Step 3: Implement. Step 4: Run, pass. Step 5: Commit.**

### Task 5: Docs, gate, deploy, proof

- [ ] **Step 1: Write the docs.** `rotation-executor.md` cancel section gains the record step, when `record-rotation` applies, and the two staging keys to delete; `rotation-proposer.md` covers the record walk and `stalled`. Update `CLAUDE.md`'s `rotation_proposer` invariant on the one write to infraops, and correct the P3 orphan-key backlog line in `PROJECT.md` to the residual.
- [ ] **Step 2: Run `make check`, then a whole-branch review and `/code-review`, then one fix wave.**
- [ ] **Step 3: Merge infraops first, confirm its checkout serves the field, and confirm the proposer's `dist` is built.** Then confirm no rotation unit is active, merge the orchestrator, and reinstall both instances.
- [ ] **Step 4: Run `rotation-worker compare` over every handed-over row.** Expected: all equal.
- [ ] **Step 5: Run one proposer pass.** Expected: the two false rows repair, and a second pass records nothing.
- [ ] **Step 6: Record the proof, update both artifacts, and list every ruling.**

**No live proof in this part:** `stalled` and the orphan delete, until a real failure. Forcing either needs Devon's say-so.

## Live proof, recorded 2026-10-31

Merged as `319702a5` (#573) and infraops `#159`. Both instances run that commit. No orchestrator image changed.

- **`lastRotated`:** one proposer pass, against the merged infraops code in a worktree, recorded six rows, each moving forward: `github-finegrained-mirror` (October 20), and `bitbucket-mirror-token`, `factory-pr-token`, `orchestrator-observer-bearer`, `change-manager-propose-bearer`, and `change-manager-read-bearer` (October 21). The two false rows repaired themselves. The final review had predicted this list.
- **Idempotence:** a second pass recorded nothing.
- **Destinations:** `compare-destinations` found 16 of 17 packages equal. `bws-sds-operator-token`'s registry row gained a reader in #562 after its package was approved; the proposer revises that package when the credential comes due, as designed.

The infraops working copy activates through its own schedule, so the scheduled proposer first reads the new field after that lane runs.

Not proven live: `stalled`, `record-wontfix`, `due-after-rotation`, and the discard of a minted key.
