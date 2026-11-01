# Execution Part 5: rules into tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Fewer prose rules, each survivor backed by a test that fails when the rule breaks.

**Spec:** `docs/superpowers/specs/2026-11-01-execution-layer-design.md`, Part 5.

---

### Task 1: Writer tests

**Files:** Create `tests/architecture/test_execution_writers.py`.

- [ ] Using `ast`, as `test_membership_writers.py` does, pin: `claim_lease` has exactly `claim_unit`, `renew_claim`, and `reclaim_expired_claim` as callers; `work_units.version` has exactly its three writers; `release_claim` has its listed callers; `POST /work-units/{id}/dispatch` is dispatch's one caller.
- [ ] Mutate each once by adding a caller in a scratch copy, and confirm failure. Commit.

### Task 2: Mutation over the enforced rules

- [ ] For each of the 15 rules classed enforced in recon, mutate the code in a scratch copy, and run its named test. A survivor gets a stronger test. Report the count and every survivor.

### Task 3: Reduce the prose

- [ ] Move the three history rules to `docs/history/claude-md-invariants-archive.md`: `runner_attempt` and `attempt_count` independent, the reusable workflow note, and transaction ownership only if a test now enforces it (otherwise keep it).
- [ ] Shorten each rule a test now enforces to one line that names the test. Never delete knowledge: superseded text moves to the archive.
- [ ] Re-measure the layer card: lines, invariants, per thousand. Run `make check`. Commit.
