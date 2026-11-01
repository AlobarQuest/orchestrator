# Rotation Part 4: one test states the edges — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One test states every rotation lane's outbound surface, so a widening fails it.

**Architecture:** `tests/architecture/test_rotation_edges.py` reuses `Edge`, `disagreements` and `PLATFORM_EDGES` from the bridge and landing tables, and adds columns for providers and processes. Stale text is corrected and `required_checks.py` moves layer.

**Tech Stack:** Python 3.14, pytest, `ast`.

**Spec:** `docs/superpowers/specs/2026-10-31-rotation-layer-design.md`, Part 4. Devon approved this part's plan in advance on 2026-10-31.

## Global Constraints

- Worktree `.worktrees/rotation-p2`, branch `rotation-p4-edges`, database `orchestrator_test_rotation_p2`.
- No behavior change. A surface the test finds wider than the lane needs is reported, and narrowed only if nothing calls it.
- Bridge and landing rows keep working unchanged.
- The table is read from the running predicates, never a copy of them.

## Review Focus

- A worker predicate built per instance: capture the real one, not a stand-in.
- A launcher UUID added without a table row: the test fails.
- A subprocess spelled in a string: `node`, `git push`, `ssh`, `security`, and the child-process BWS imports are each pinned.
- A borrowed client: a widening in `bump_proposer` fails this table.
- A new module in a rotation lane importing an HTTP client: the population test fails.

---

### Task 1: The edge table

**Files:**
- Create: `tests/architecture/test_rotation_edges.py`
- Modify: `src/rotation_worker/adapters/bws_machine_token.py` (the child-process import names become a named constant)

**Interfaces:**
- Consumes: `Edge`, `_captured_permits`, `disagreements` from `test_bridge_edges.py`; `PLATFORM_EDGES` shape from `test_landing_edges.py`. Move anything needed into a shared test helper rather than copying it.
- Produces: tables `EDGES` (four lanes: orchestrator, change-manager, GitHub, alert-hub, bearers), `PLATFORM_EDGES` (Coolify, Healthchecks, Azure log host), `PROVIDER_EDGES` (one row per adapter), and `PROCESSES` (`node`, `git`, `ssh`, `security`, `bitwarden_sdk`).

Tests:
- `test_each_lane_accepts_exactly_its_routes_among_the_corpus`, per lane, through the lane's real `permits`.
- `test_each_platform_reach_is_exactly_its_routes`, `test_each_provider_reach_is_exactly_its_routes`.
- `test_each_lane_runs_exactly_its_processes`, by AST over `subprocess`, `run`, and named constants, including `bws_machine_token`'s child-process imports.
- `test_each_launcher_fetches_exactly_its_bearers`, for the proposer, decomposer, and verifier, and `test_the_worker_names_its_identities_in_config`.
- `test_the_proposer_uses_only_the_named_part_of_bump_proposer`, by AST over its calls into that package.
- `test_the_table_is_the_layer_population`.
- `test_control_a_widened_predicate_fails_the_table`, one control per table.

- [ ] **Step 1: Write the tests; run them; list every disagreement in the report before changing anything.**
- [ ] **Step 2: Resolve each disagreement: a table row for a real need, or a narrowed predicate for one nothing calls.**
- [ ] **Step 3: Run `tests/architecture`, pass. Step 4: Mutate each predicate and table, reporting the count and survivors. Step 5: Commit.**

### Task 2: Stale text and the layer

**Files:**
- Modify: `src/rotation_verifier/client.py` comment if stale, `tests/rotation_proposer/test_client_surface.py` docstring, `tests/architecture/test_wsp21_invariant_scan.py` comments, `tests/architecture/test_external_content_observes_only.py` verifier row, `layers.toml`, `docs/operations/architecture-guards.md`

- [ ] **Step 1: Correct the text the recon listed:** the decomposer's "borrows no sibling lane's client", the proposer test's "two confined clients", the verifier allowlist's "three paths", the verifier's `CLASSIFICATION` row, and the worker's allowlist comment.
- [ ] **Step 2: Move `src/rotation_verifier/required_checks.py` and `tests/rotation_verifier/test_bump_units.py` to the `landing` layer** in `layers.toml`, with no code move, and confirm `make layer L=rotation`.
- [ ] **Step 3: Add the test to `architecture-guards.md`. Run `make check`, read exit and totals from the log. Commit.**

### Task 3: Review, merge, proof

- [ ] **Step 1: Whole-branch review, then `/code-review`, then one fix wave.**
- [ ] **Step 2: Confirm no rotation unit is active. Merge. If `src/rotation_worker` changed, reinstall both instances, confirm the revision, and confirm a pass ends in a heartbeat.**
- [ ] **Step 3: Record the result, update both artifacts, and list every ruling.**

The test is the proof: this part has no separate live behavior.
