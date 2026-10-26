# Rotation gate graduation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The verifier passes a rotation unit's `rotation-step/1` criteria from the executor's evidence and completes the unit, and a new lane runs `/verify` on submitted rotation units, so no person adjudicates or completes a rotation unit whose evidence is clean.

**Architecture:** A rotation branch in `verifier.py::_verify_criterion` asks a new pure module, `services/verifier/rotation_verification.py`, whether the current evidence proves the criterion's step; the answer replaces `judgment_required` with `passed`, and never produces `failed`. The verifier's existing `_target_state` then moves `SUBMITTED -> COMPLETED`, and completion resolves the next unit's dependency as today. A new out-of-process program, `src/rotation_verifier/`, holds only the VERIFIER bearer and runs one pass every 10 minutes.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, pytest, httpx through `estate_clients.confined`, launchd.

**Spec:** Devon's decisions, 2026-10-26 (this session): graduate the gate; all rotation adapters; a new single-purpose lane; coverage judged by parsing the executor's "n of m" note, with full coverage required before a person's revoke. Design brief: the exploration notes summarised under "Facts" in the following section. Precedent: ADR-0055 amendment 11.

## Global Constraints

- The criterion floor stays human: `external_attestation` stays in `JUDGMENT_TYPES`, and `evaluate_criterion` is unchanged. The R1 control (`tests/services/test_criterion_evidence_vocabulary.py:67-75`) must stay green.
- The rotation branch returns `passed` or leaves `judgment_required`. It never returns `failed`.
- No new `from_verifier_evaluation=True` call site (`tests/architecture/test_verifier_evaluation_flag.py`).
- No package bytes change and no re-approval: criteria keep `external_attestation`.
- The executor never holds the VERIFIER bearer (ADR-0055 decision 4). The lane holds only that bearer.
- Human acts (console mints, revokes, consumer attestations) stay `/review` dependencies, untouched.
- Every new module-level string collection is registered in `VOCABULARY_REGISTRY` or marked `# not-a-vocabulary:`.
- The lane's installer sources `scripts/sds-install.sh` and calls `sds_refuse_linked_worktree`.

## Facts

- `verifier.py:115-142` `_verify_criterion`; the named-check override at `:128-132` is the pattern for a branch after `evaluate_criterion`.
- `_target_state` (`verifier.py:347-352`) already completes a unit when nothing is `judgment_required` and nothing failed; `VERIFIER_EDGES` include `SUBMITTED -> COMPLETED`.
- `rotation_claims.is_rotation_unit(unit, revision)` and `RotationExecutors.owner(unit)`; settings reach routes through `SettingsDep` and `api/routes/lifecycle.py::rotation_executors(settings)`.
- Evidence: `Evidence.recorded_by`, `evidence_type == "rotation.step"` (`rotation_worker/worker.py:71`), payload validated by `rotation_evidence.RotationStepEvidence`; a SYSTEM-recovered row carries a `recovery` key.
- AC evidence text in every standing package matches `^external: the (?:(\w+) unit's )?(\w+) step's rotation-step/1 evidence, filed by the rotation executor\.$` after folding whitespace.
- The coverage note is built at `rotation_worker/contract.py:590`: `f"{n} of {m} consumers hold or attest the new value"`, optionally followed by `"; the old value is live before the revoke"`.
- `GET /api/v1/status-ledger?state=submitted` is open to every authenticated role, VERIFIER included.

## Review Focus

- **A `rotation.step` row on a non-rotation unit** must never pass an `external_attestation` criterion. Test in Task 1 (`test_non_rotation_unit_stays_judgment`).
- **Evidence filed by anyone but the unit's owning executor**, including the other executor instance and a SYSTEM recovery, must stay with a person. Task 1 (`test_wrong_recorder_stays_judgment`, `test_recovered_row_stays_judgment`).
- **Retire on an old value that was already refused** (`retire_old` with old `dead`) must pass, not fail. Task 1 (`test_retire_old_already_dead_passes`).
- **A partial coverage note before a person's revoke** ("1 of 2 …; the old value is live before the revoke") must stay with a person, while "2 of 10" on a mint unit passes. Task 1 (`test_partial_coverage_before_revoke_stays_judgment`, `test_partial_coverage_on_mint_passes`).
- **The lane meeting a unit whose version moved** between its ledger read and its verify (a 409 or a domain refusal) must report it and go on to the next unit, not stop the pass. Task 3 (`test_version_conflict_skips_unit`).

---

### Task 1: Judge a rotation step from its evidence

**Files:**
- Create: `src/orchestrator/services/verifier/rotation_verification.py`
- Test: `tests/services/test_rotation_verification.py`

**Interfaces:**
- Produces: `rotation_step_verdict(criterion_evidence: str, rotation_unit: str | None, evidence_type: str, payload: Mapping[str, object]) -> str | None` returning `None` when the evidence proves the criterion, else the reason the criterion stays with a person. Pure; no session, no unit.
- Produces: `ROTATION_STEP_EVIDENCE_TYPE = "rotation.step"`.

Rules, every one required for `None`:
1. `evidence_type == "rotation.step"` and `RotationStepEvidence.model_validate(payload)` succeeds, and `"recovery" not in payload`.
2. The AC text matches the pattern in Facts; its step equals `payload["step"]`; when it names a unit, that equals `rotation_unit`.
3. Exactly one `old` and one `new` fingerprint, with different `sha256_prefix`.
4. No probe has outcome `indeterminate`. Every probe whose subject isn't `known_bad` has a `known_bad` probe on the same target with outcome `dead`.
5. Per step: `verify_new` and `confirm_dead` need at least one non-control probe, each with `outcome == expected`. `retire_old`'s `old` probe may be `live` or `dead`. `confirm_dead` needs `new` live and `old` dead. `verify_consumers` needs the note to start with `n of m consumers hold or attest the new value` with 0 ≤ n ≤ m and m ≥ 1, and n == m when the note says the old value is live before the revoke (a person revokes next). Partial coverage before then is by design: `retire_old` refuses any consumer without the new value (`contract.py::_retire_old`). Devon chose this over "N of N only" on 2026-10-26 after the 13 completed rotations showed every mint and store unit filing partial coverage. Other steps (mint, stage, host, store, distribute) need rules 1–4 only.

- [ ] **Step 1: Write the failing tests.** One passing case per step kind built from the payloads in production (drift-reporter retire: `verify_consumers` "10 of 10 …", `retire_old` with old live, `confirm_dead` with four probes). Fall-back cases, each asserting a non-`None` reason: `test_wrong_evidence_type`, `test_recovered_row_stays_judgment`, `test_step_mismatch`, `test_unit_mismatch`, `test_unparseable_ac_text`, `test_missing_control`, `test_indeterminate_probe`, `test_same_fingerprints`, `test_partial_coverage_before_revoke_stays_judgment`, `test_partial_coverage_on_mint_passes`, `test_coverage_note_unparseable`, `test_confirm_dead_old_live`. Plus `test_retire_old_already_dead_passes`.
- [ ] **Step 2:** Run `pytest tests/services/test_rotation_verification.py -q`. Expected: FAIL, module not found.
- [ ] **Step 3:** Implement. Register the step-name set and evidence type constant in `VOCABULARY_REGISTRY` (`tests/architecture/test_cross_boundary_vocabulary.py`) or mark them.
- [ ] **Step 4:** Run the same tests. Expected: PASS.
- [ ] **Step 5:** Add `tests/contract/test_rotation_step_contract.py`: `ROTATION_STEP_EVIDENCE_TYPE` equals the executor's constant (read from `rotation_worker/worker.py` source, since the packages can't import each other), and a note built by the executor's format string for `n == m` passes the coverage rule while `n < m` doesn't.
- [ ] **Step 6:** Commit: `feat(verifier): judge a rotation step from the executor's evidence`.

### Task 2: The verifier uses the verdict and completes the unit

**Files:**
- Modify: `src/orchestrator/services/verifier/verifier.py` (`_verify_criterion`, `VerifyCommand` consumers)
- Modify: `src/orchestrator/services/verifier/verifier_types.py` (`VerifyCommand` gains `rotation_executors: RotationExecutors = NO_ROTATION_EXECUTORS`)
- Modify: `src/orchestrator/api/routes/verifier.py` (`verify` takes `SettingsDep`, passes `rotation_executors(settings)`)
- Test: `tests/services/test_verifier_rotation.py`, `tests/api/test_rotation_worker_flow.py`

**Interfaces:**
- Consumes: `rotation_step_verdict`, `ROTATION_STEP_EVIDENCE_TYPE` (Task 1); `is_rotation_unit`, `RotationExecutors.owner`.
- Produces: in `_verify_criterion`, when `status == "judgment_required"`, the criterion is `external_attestation`, the unit is a rotation unit, `evidence.recorded_by == command.rotation_executors.owner(unit)`, and the verdict is `None`, then `status, outcome, reason = ("deterministic", "passed", "rotation step evidence proves <step>")` (use the `EvaluationStatus` value the deterministic path uses).

- [ ] **Step 1: Write the failing tests:** `test_rotation_unit_completes_on_clean_evidence` (SUBMITTED → COMPLETED, adjudications recorded by the verifier); `test_non_rotation_unit_stays_judgment` (same payload on a non-rotation unit → AWAITING_REVIEW); `test_wrong_recorder_stays_judgment` (recorded by the other executor id); `test_unconfigured_executors_stay_judgment` (`NO_ROTATION_EXECUTORS`). In `test_rotation_worker_flow.py`, replace the manual adjudication and resolve calls after the retire unit with `/verify` as VERIFIER, and assert `completed` and that the next unit's dependency resolved by itself.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement the branch and thread `rotation_executors` through the route.
- [ ] **Step 4:** Run the tests plus `tests/services/test_criterion_evidence_vocabulary.py`, `test_adjudications.py`, `tests/architecture/test_verifier_evaluation_flag.py`, `test_recovery_actions_cannot_complete.py`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(verifier): complete a rotation unit whose step evidence is clean`.

### Task 3: The rotation-verifier lane

**Files:**
- Create: `src/rotation_verifier/__init__.py`, `client.py`, `cli.py`
- Create: `scripts/run-rotation-verifier.sh`, `scripts/com.devon.rotation-verifier.plist`, `scripts/install-rotation-verifier-launchd.sh`
- Modify: `pyproject.toml` (`rotation-verifier = "rotation_verifier.cli:main"`, package listed), `tests/architecture/*` allowlists that enumerate out-of-process programs and outbound HTTP (`OUTBOUND_ALLOWLIST`), `.bws-secrets.toml` comment naming the lane as a VERIFIER consumer
- Test: `tests/rotation_verifier/test_pass.py`, `tests/scripts/test_launchd_installers.py` (picks up the installer)

**Interfaces:**
- `client.py`: `VerifierClient(base_url: str, key_id: str, token: str)` over `ConfinedClient`; permits only `GET /api/v1/status-ledger`, `GET /api/v1/work-units/{id}`, `POST /api/v1/work-units/{id}/verify`. Methods `submitted() -> list[str]`, `unit(unit_id: str) -> dict`, `verify(unit_id: str, version: int) -> dict`.
- `cli.py`: `run_pass(client) -> list[Outcome]`; for each submitted unit whose `authority.constraints` carries `rotation_unit`, POST verify with `idempotency_key = f"rotation-verifier-{unit_id}-v{version}"`; print one line per unit: `unit_key  completed|awaiting_review|refused  reason`. Exit 0 when every unit verified or none was submitted; exit 3 (the lane's finding code) when any unit went to `awaiting_review` or was refused; exit 2 on transport or auth failure.

- [ ] **Step 1: Write the failing tests** against a fake transport: `test_only_rotation_units_are_verified`, `test_completed_and_awaiting_review_are_reported`, `test_version_conflict_skips_unit`, `test_client_refuses_unpermitted_path`, `test_exit_codes`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement the client and pass, mirroring `src/image_release/client.py` for the VERIFIER confinement.
- [ ] **Step 4:** Write `run-rotation-verifier.sh` from `run-deploy-watcher.sh`'s shape: source `sds-token.sh`, fetch the VERIFIER bearer by UUID `660d5846-abcb-4751-be86-b483012899eb` with `--color no`, arm the dead-man check `sds-rotation-verifier` with `--finding 3`. Plist `StartInterval` 600. Installer from `install-deploy-watcher-launchd.sh`.
- [ ] **Step 5:** Run the tests and `tests/scripts/`. Expected: PASS.
- [ ] **Step 6:** Commit: `feat(rotation-verifier): a lane that verifies submitted rotation units`.

### Task 4: Record the decision

**Files:**
- Modify: `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (Amendment 14, in amendment 11's structure: what enforces the gate today, why the person adds nothing, the decision, what stays human, alternatives, cost)
- Modify: `CLAUDE.md` (one invariant bullet), `docs/operations/rotation-executor.md`, `docs/operations/verifier.md`, `docs/operations/local-development.md` (lane list)

- [ ] **Step 1:** Write the amendment and docs.
- [ ] **Step 2:** Commit: `docs(adr-0055): amendment 14, rotation step evidence completes its unit`.

### Task 5: Verify, review, ship

- [ ] **Step 1:** Run `make check` in the worktree with its own test database. Expected: green; read the collected-test count.
- [ ] **Step 2:** Mutation-test `rotation_verification.py` and the verifier branch (each rule flipped or removed); report caught and survivors.
- [ ] **Step 3:** Run `/code-review`; fix findings.
- [ ] **Step 4:** Open the PR, wait for CI, merge.
- [ ] **Step 5:** Deploy the orchestrator with the deploy command (`docs/operations/deploy.md`); no migration. Read `openapi.json` and confirm the image revision.
- [ ] **Step 6:** Fast-forward the main tree, `uv pip install -e .`, create the `sds-rotation-verifier` check on self-hosted Healthchecks, install the lane, and run one dry pass.
- [ ] **Step 7:** Next rotation unit to submit (content-mini or the full bearer) completes with no person; read its adjudications' `decided_by`.
