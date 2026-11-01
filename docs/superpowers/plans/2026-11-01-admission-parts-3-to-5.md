# Admission Parts 3 to 5: records, edges, and rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Close the stale rule, state the layer's edge, and back its rules with tests that fail when a rule breaks.

**Spec:** `docs/superpowers/specs/2026-11-01-admission-layer-design.md`, Parts 3 to 5.

## Global Constraints

- Superseded text moves to `docs/history/claude-md-invariants-archive.md`, never deleted.
- Rule count isn't the measure here, because splitting raises it. The measure is rules with a test that failed when the rule broke.
- Mutations run on a scratch copy.

---

### Task 1: Records

- [ ] Move the mistyped-capability-level rule to the archive, after a test shows a mistyped level refused at ingress.

### Task 2: Edges

- [ ] Create `tests/architecture/test_admission_edges.py`: `services/intake/`, `factory_policy.py`, and the reach, capability, change-window, and conformance modules build no client and import no HTTP library; the policy file is the one file read; and a table lists each vocabulary copy with what holds it equal, including capabilities, reach, change classes, and the dependency-update budget.
- [ ] Mutate each row once. Commit.

### Task 3: Rules

- [ ] Write tests for `runner_authority_violation`'s four asking points and `classify_context_update`.
- [ ] Split the standing-bump, `pr-merge` authority, `allowed_commands`, and `verify_commands` rules into one rule each, naming a test, without changing meaning.
- [ ] Run a mutation pass over the enforced admission rules. A survivor gets a stronger test. Report the count and every survivor.
- [ ] Run `make check`. Commit.
