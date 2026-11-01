# Rotation Part 5: close the backlog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the infraops 04:00 security window and everything only it used, hand over the last orchestrator bearer, and close the rotation backlog.

**Architecture:** Mostly deletion across four repositories, in dependency order. The rotation service stays unchanged: no `src/rotation_worker` change, no instance update.

**Tech Stack:** TypeScript (infraops), Python (orchestrator, change-manager, security-standards).

**Spec:** `docs/superpowers/specs/2026-10-31-rotation-layer-design.md`, Part 5.

## Devon's rulings, 2026-11-01

1. Retire the three revoked rows.
2. Remove the `run-security-window` step and change-manager's `rotation` source.
3. Revoke `bws-cred-rotation-token`, as the last step, through a task for Devon.
4. Hand over `orchestrator-factory-runner-bearer` by flag, with no early rotation.

## Measurements

| Question | Answer |
|---|---|
| What has the window run? | 134 nightly reports: 132 empty, one failed rotation and one blocked manual item, the last on 2026-07-02. It has never applied an `exec:` plan. |
| Open `security` records | 0 of 30 (24 resolved, 6 wontfix) |
| `rotation` records | 2, both resolved. They stay in the database. |
| Factory-runner bearer due | 2027-07-08 by `created` plus 365 days |
| project-standards' `FACTORY_RUNNER_TOKEN` | Live since 2026-10-27, read by `factory-runner-pilot.yml`, reported by `secret-fingerprint.yml`. The row is right and its comment is stale. |

## Global Constraints

- Never touch a main tree. Each repository works in `.worktrees/rotation-p5`.
- Check that no rotation unit is ready or claimed before merging change-manager.
- Keep `.cred-consumers.toml`'s other rows, the 03:00 scan, `cred-findings`, `record-rotation`, `rotationAnchor`, and `run-window`.
- Keep `CLASS_POLICY`'s class names. `tests/fixtures/rotation_class_policy.json` becomes a list of them, in both repositories.
- Secrets stay out of the transcript.

## Review Focus

- An approved `security` item after this work: nothing runs it, so the scan must offer no plan that promises execution.
- `change-window.sh` must still send its digest and ping Healthchecks with the step removed.
- A row the scan still reports must keep a manual remediation, never a rotation plan.
- `run-window` must still exclude `security` items from the LLM agent, and must exclude `rotation` items, the hazard at `run-window.ts:29`.
- The factory-runner row must generate a valid package at its due date, not only carry a flag.

---

### Task 1: Retire the window in infraops

**Files:** `.cred-consumers.toml`, `src/cli/change-mgr-cli.ts`, `src/security-drift/*`, `src/change-manager/api-client.ts`, `scripts/change-window.sh`, `tests/*`, `CLAUDE.md`.

- [ ] Delete the rows `github-classic-aihelper`, `github-classic-lifeops`, and `openai-project`, leaving a comment like the `tok-ops-mini` one naming the date and reason.
- [ ] Delete `security-executor.ts`, `rotation-executor.ts`, `doRunSecurityWindow`, `credRotationBwsToken`, `rotationRefusals`, the CLI verb, `getApprovedBySource` if no other caller remains, and the step in `change-window.sh`.
- [ ] Remove the rotation remediation: `RotationPlanSpec`, `rotationClassification`, `consoleSteps`, `STAGING_SERVICE`, `SUPPORTED_CONSUMER_KINDS`, the `executor` policy field, the `rotation` remediation type, and its `emit.ts` branch. A row not handed over reports with a manual remediation.
- [ ] Change `NORMAL_EXEC` plans to `manual` steps, so no security record promises execution. `autofix.ts` stays.
- [ ] Delete `emit-state.ts`, `canonical.planHash`, and the `emitStateFile` plumbing when no reader remains. Keep `validated-store.ts`.
- [ ] Add `rotation` to `run-window.ts`'s exclusion, with a test.
- [ ] Delete or edit the tests the recon listed, and update `CLAUDE.md`. Keep `rotation_findings.json` byte-identical to the orchestrator's copy.
- [ ] Run `make check`, then `bash tests/change-window.test.sh`. Expected: all pass, and `cred-findings` output on the live registry is unchanged apart from the three rows.
- [ ] Commit.

### Task 2: Retire the executor's identity in security-standards

- [ ] Set `security-executor` to `status: retired` and keep the entry and its profile: recorded events resolve through it, and any status validates (`registry/README.md`). Deleting it would attribute those events to nobody.
- [ ] Run `make check`, and commit.

### Task 3: Remove the `rotation` source in change-manager

- [ ] Edit `app/sources.py`'s docstring, drop the dashboard tab, delete `scripts/seed_rotation_deploykey.py` and `tests/test_seed_rotation.py`. Correct the `app/guards.py` docstring. `source` is a free string, so nothing validates it, and `tests/test_api_actor.py`'s sample actor stays.
- [ ] Move the tests that use `rotation` as a sample source (`test_reconcile_source.py`, `test_web.py`, `test_deploy_sweep_immunity.py`) to the sample source `audit`.
- [ ] Run `make check`, and commit.

### Task 4: Hand over the bearer and close the records in the orchestrator

- [ ] Set `rotated_by_sds = true` on `orchestrator-factory-runner-bearer`, and correct the comment: seven repositories, project-standards included.
- [ ] Dry-run: generate the package in-process in a scratch directory from the row, validate it with intent-packages' validator, and confirm `select_plan` and the destination format accept it. Expected: AC-001 to AC-010, a distribute step, no `host_without_overlap`.
- [ ] ADR-0055: add the amendment for this layer, and list all amendments in the header. ADR-0054: correct the consequences and stale lines. Update `CLAUDE.md` and `docs/operations/rotation-proposer.md`, which name the window.
- [ ] `PROJECT.md`: close increments 6, 7, 8, 9 and the ADR-0054 item, naming what shipped, and add new items for what remains.
- [ ] Run `make check`, and commit.

### Task 5: Land, activate, and prove

- [ ] Merge infraops, then security-standards. Check no rotation unit is ready or claimed, then merge change-manager and confirm its revision is served. Merge the orchestrator.
- [ ] After infraops activates, prove the next `change-window` run logs no security step, sends its digest, and pings Healthchecks, and that `cred-findings` lists the factory-runner bearer in `last_rotated`.
- [ ] Remove `~/.config/infra-drift/security-emit-state.json` only after infraops activates.

### Task 6: Revoke the token

- [ ] Create a Todoist task for Devon, with steps, to revoke `bws-cred-rotation-token` and to move `orchestrator-factory-runner-bearer`'s keeper to `Rotation / Keepers` with a direct grant for the broad account. After the move, prove both reads and correct the row's note. This waits for activation in Task 5.
- [ ] After the revoke, prove it dead with a fresh login under an empty `HOME`, delete the Keychain item, and delete the registry row, the `cred-registry-integration.test.ts` entry, and the `BWS_CRED_ROTATION_TOKEN` override if any remains.

## Live proof

No proof exists for the bearer's first rotation until 2027. The dry run proves generation only.

Recorded 2026-11-01:

- **Window:** a kickstarted `com.devon.change-window` run activated infraops at `ad70255`, applied 0 of 0, ran no security step, and sent its digest. The Healthchecks ping is inferred from exit 0.
- **Findings:** `cred-findings` reports 0 findings and serves the bearer with the anchor 2026-07-08.
- **Keeper:** in `Rotation / Keepers`. Developer Machines and the root read the same hash from fresh logins, beside a known-good control read.
- **Token:** Devon removed its project instead of revoking it, so Task 6's Keychain and row deletions didn't run. A fresh login lists no project.
- **State file:** `security-emit-state.json` removed after a search found no reader.

The first 03:00 scan on this code ran after this record, so the manual checklists have no live proof here.
