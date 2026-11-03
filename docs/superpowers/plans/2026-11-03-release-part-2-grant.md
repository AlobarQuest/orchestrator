# Release Part 2: Release Binding Reads the Member Grant

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The server refuses release binding, deployment observations, and the machine-activation candidates read for a repository the member record doesn't grant `rollouts`, before anything is written.

**Architecture:** One new module, `services/release/release_grant.py`, answers two questions: does this unit's own repository hold `rollouts`, and does this repository. The two write services ask it after their idempotency-key replay and subject validation, before their same-tuple replay and any write. The candidates route asks it before its query. The two clients already turn a refusal into a missing answer, so they only gain tests.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, Postgres, pytest.

**Spec:** `docs/superpowers/specs/2026-11-03-release-layer-design.md` (Part 2, Constraints, Anti-goals).

## Global Constraints

- Read the grant with `target_grant.MemberRecordGrants(session).is_granted(repository, "rollouts")`: case-folded, `granted` only, non-retired member. `rollouts` is the wire value (word guards).
- The repository whose grant is read is the unit's envelope `target_repository` (`normalize_authority(unit.authority or {}).target_repository`), never the request body's `source_repository`.
- A body `source_repository` that differs from the unit's target, case-folded and stripped, is refused, and so is a unit that names no target repository.
- Order in both write services: idempotency-key replay first (answers as before, no grant check), then subject validation, then the repository refusal, then the same-tuple or same-environment replay, then writes.
- A refusal is a `DomainError` (no bare 500). Code names: `release_repository_not_granted` and `release_repository_mismatch`, remediation `restore_grant` for the first.
- Under `src/orchestrator/` no identifier or whitespace-free string may contain `deploy`, `dispatch`, `coolify`, or the other ws32/ws34 terms.
- Work in `.worktrees/release-p2` with its own venv and database `orchestrator_test_release_p2`; tests need `PGTZ=UTC`, `TEST_DATABASE_URL`, and `SECURITY_STANDARDS_DIR`. Never touch the main tree.

## Review Focus

1. A request whose body names a granted repository for a unit targeting an ungranted one: refused as a mismatch, never bound (Task 1, Task 2).
2. An exact retry (same idempotency key) after the grant is withdrawn: answers the stored binding, not an error (Task 2).
3. A fresh request for an already-bound source tuple after withdrawal: refused, not answered from the old binding (Task 2).
4. A repository spelled with different case in the member record and the envelope: granted (Task 1).
5. A retired member that still lists `rollouts` as granted: refused (Task 1).

---

### Task 1: The release grant question

**Files:**
- Create: `src/orchestrator/services/release/release_grant.py`
- Modify: `tests/_support/seeding.py` (add `grant_service`, `set_grant_state`)
- Modify: `layers.toml` (place the new module and test in the `release` layer)
- Test: `tests/services/test_release_grant.py`

**Interfaces:**
- Produces:
  - `RELEASE_GRANT_SERVICE: Final = "rollouts"`
  - `RELEASE_REPOSITORY_NOT_GRANTED: Final = "release_repository_not_granted"`
  - `RELEASE_REPOSITORY_MISMATCH: Final = "release_repository_mismatch"`
  - `repository_release_refusal(session: Session, repository: str) -> DomainError | None`: `None` when the repository holds `rollouts`.
  - `unit_release_refusal(session: Session, unit: WorkUnit, source_repository: str) -> DomainError | None`: mismatch when the unit names no target or `source_repository` differs from it, else `repository_release_refusal` for the unit's target.
  - Test helpers in `tests/_support/seeding.py`: `grant_service(session: Session, repository: str, service: str) -> None` (creates the member if missing, then sets that one grant to `granted`, leaving other grants alone) and `set_grant_state(session: Session, repository: str, service: str, state: str) -> None`.

- [ ] **Step 1: Write the failing tests** in `tests/services/test_release_grant.py`:
  - `test_a_granted_repository_is_not_refused`: `grant_service(s, "AlobarQuest/demo", "rollouts")`; `repository_release_refusal(s, "AlobarQuest/demo") is None`.
  - `test_the_grant_is_case_folded`: grant `AlobarQuest/Demo`, ask `alobarquest/demo`: `None`.
  - `test_another_service_s_grant_does_not_count`: grant only `app_changes`; the refusal's `code == "release_repository_not_granted"`.
  - `test_a_withdrawn_grant_is_refused`: grant, then `set_grant_state(..., "rollouts", "withdrawn")`: refused with that code.
  - `test_a_retired_member_is_refused`: grant, then set the member's `state = "retired"`: refused.
  - `test_a_unit_s_own_repository_is_asked`: a unit whose envelope targets a granted repository, `source_repository` the same: `unit_release_refusal(...) is None`.
  - `test_a_body_naming_another_repository_is_a_mismatch`: unit targets ungranted `AlobarQuest/a`, body names granted `AlobarQuest/b`: `code == "release_repository_mismatch"`.
  - `test_a_unit_naming_no_repository_is_a_mismatch`: envelope with no `target_repository`: `code == "release_repository_mismatch"`.
  - `test_the_body_spelling_may_differ_in_case`: target `AlobarQuest/Demo`, body ` alobarquest/demo `, granted: `None`.

- [ ] **Step 2: Run them to verify they fail**

Run: `PGTZ=UTC .venv/bin/pytest tests/services/test_release_grant.py -q`
Expected: FAIL, `ModuleNotFoundError: orchestrator.services.release.release_grant`.

- [ ] **Step 3: Implement the module and the two seeding helpers.** Build the `DomainError` messages in the style of `target_grant.target_not_granted_error`. Add both files to `layers.toml`'s `release` layer.

- [ ] **Step 4: Run them to verify they pass**, plus `tests/architecture/test_layer_manifest.py` and `tests/architecture/test_cross_boundary_vocabulary.py`.

Expected: PASS.

- [ ] **Step 5: Commit**: `git commit -m "Release Part 2: the release grant question"`

---

### Task 2: The server asks it before binding

**Files:**
- Modify: `src/orchestrator/services/release/release_artifacts.py` (`_record_release_artifact`, after `_validated_subject`, before `_existing_source_tuple`)
- Modify: `src/orchestrator/services/release/deployment_observations.py` (`_record_deployment_observation`, after `_validated_subject`, before the same-environment read; pass `binding.source_repository` and the implementation unit)
- Modify: `src/orchestrator/api/routes/release.py` (`machine_activation_candidates_route`: after the `kind` check, a non-blank repository without `rollouts` raises; a blank one still answers `[]`; update its docstring)
- Modify: every test fixture that binds or observes, so its unit targets a repository and that repository holds `rollouts` (run the suite to find them; recon found `tests/services/test_release_artifacts.py`, `test_deployment_observations.py`, `test_traceability.py`, `test_in_flight.py`, `test_reconciliation_detect_pass.py`, `tests/api/test_release_artifacts_api.py`, `test_deployment_observations_api.py`, `test_machine_activation_api.py`, `test_traceability_api.py`, `test_release_evidence_pack_api.py`, `tests/web/test_release_evidence_pack.py`, `tests/image_release/conftest.py`, `tests/protocol/drills/test_drill_deploy_split_brain.py`, `tests/idempotency/matrix.py`)
- Modify: `CLAUDE.md` (the grant invariant bullet that starts "Dispatch admission, the claim, ...": add that release binding, deployment observations, and the candidates read refuse with `release_repository_not_granted` or `release_repository_mismatch`, keyed on the unit's own target)
- Test: `tests/services/test_release_artifacts.py`, `tests/services/test_deployment_observations.py`, `tests/api/test_machine_activation_api.py`

**Interfaces:**
- Consumes: Task 1's `unit_release_refusal`, `repository_release_refusal`, `grant_service`, `set_grant_state`.

- [ ] **Step 1: Write the failing tests**
  - In `test_release_artifacts.py`:
    - `test_an_ungranted_repository_is_refused_before_anything_is_written`: refused with `release_repository_not_granted`; count of `ReleaseArtifactBinding` and `Event` rows unchanged.
    - `test_a_body_naming_another_repository_is_refused_as_a_mismatch`.
    - `test_an_exact_retry_after_withdrawal_answers_the_stored_binding`: bind, withdraw, resend the same command: returns the same binding id.
    - `test_a_fresh_request_for_a_bound_tuple_after_withdrawal_is_refused`: bind, withdraw, send the same facts with a new idempotency key: `release_repository_not_granted`.
  - In `test_deployment_observations.py`:
    - `test_an_observation_for_a_binding_whose_repository_lost_its_grant_is_refused`: no `DeploymentObservation` and no minted unit written.
    - `test_an_exact_observation_retry_after_withdrawal_answers_the_stored_row`.
  - In `test_machine_activation_api.py`:
    - `test_candidates_for_an_ungranted_repository_are_refused`: 422 with `body["error"]["code"] == "release_repository_not_granted"`.
    - `test_candidates_for_a_blank_repository_are_still_empty`.

- [ ] **Step 2: Run them to verify they fail**

Run: `PGTZ=UTC .venv/bin/pytest tests/services/test_release_artifacts.py tests/services/test_deployment_observations.py tests/api/test_machine_activation_api.py -q`
Expected: the new tests FAIL; existing ones pass.

- [ ] **Step 3: Implement the three call sites,** then update `CLAUDE.md`.

- [ ] **Step 4: Fix the fixtures.** Run the whole suite; for each failure caused by a missing target or grant, give the fixture's unit a `target_repository` matching the `source_repository` it binds and call `grant_service(..., "rollouts")`. Never relax the check to pass a test.

Run: `PGTZ=UTC make check`
Expected: PASS, with the collected-test count read from the output.

- [ ] **Step 5: Commit**: `git commit -m "Release Part 2: binding, observations and candidates read the rollouts grant"`

---

### Task 3: Both clients treat the refusal as a missing answer

**Files:**
- Test: `tests/activation_sweep/test_bind.py`, `tests/image_release/test_cli.py`

No production code changes: the sweep already reports an orchestrator refusal as `refused`, which `has_findings` counts, so the launcher exits 3 and its dead-man check pings `/fail`; `image-release` exits `EXIT_INCOMPLETE` on a finding. These tests pin that a grant refusal takes that path.

- [ ] **Step 1: Write the tests**
  - `test_a_bind_refused_for_its_grant_makes_the_pass_incomplete` (sweep): a fake binder whose `bind` raises the client's refusal error carrying `release_repository_not_granted`; the unit's outcome is `refused` and `has_findings(summaries)` is `True`.
  - `test_a_bind_refused_for_its_grant_exits_incomplete` (image-release): the bind call answers 422 with that code; the CLI exits `EXIT_INCOMPLETE` and its JSON names the code.

- [ ] **Step 2: Run them.** They pass without code changes. If either fails, stop and report: the spec's claim that no client change is needed would be wrong.

- [ ] **Step 3: Commit**: `git commit -m "Release Part 2: pin both clients' handling of a grant refusal"`

---

## Deploy and live proof

1. Merge after the final review. Check that no rotation unit is `ready` or claimed (`scratchpad/active.sh`), and that no dispatched run is live, before the orchestrator restarts.
2. Deploy with `scripts/deploy_orchestrator.py`. Its own `image-release bind` runs against the new check for `AlobarQuest/orchestrator`, which holds `rollouts`: that is the positive live proof.
3. Read `openapi.json` for the unchanged routes, and confirm the next `com.devon.activation-sweep` pass exits 0 or 2, never 3, with no `release_repository_*` refusal in its log.
4. Check whether `sds-canary` holds a machine-local or release binding. If it does, ask Devon before withdrawing its `rollouts` to see a live refusal. If not, report that the refusal has tests only.

## Live proof

Recorded 2026-11-03: deployed as `f99815a-rel2-amd64` (#600) and `80a4223-rel3-amd64` (#601), each with the digest matched and no migration. Each deploy's own `image-release bind` read the orchestrator's candidates under the new check and got all four units, though none needed a new binding. Production refuses the candidates read for `AlobarQuest/factory-runner`, which lacks `rollouts`, with `409 release_repository_not_granted`, and serves `AlobarQuest/orchestrator`'s. A refused bind write and a refused deployment observation have tests only: no deploy needed a new binding, and `sds-canary` holds no `rollouts` grant or binding to withdraw. `GET /api/v1/observations` has three filtered callers, not the two the spec measured: `landing_ledger`'s `read_landings` reads it too, and `test_release_edges.py` states all three.
