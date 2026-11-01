# Execution Part 3: records stay true Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** The two `max_attempts` values can't disagree unseen, and the `CLAUDE.md` rule says what's true.

**Spec:** `docs/superpowers/specs/2026-11-01-execution-layer-design.md`, Part 3. Part 1 already covers the `dispatched` record with no claim.

---

### Task 1: Pin the ceiling

**Files:** Test `tests/services/test_max_attempts_ceiling.py`. Modify `CLAUDE.md`.

- [ ] Write `test_a_policy_approved_unit_stays_within_its_envelope_budget` and `test_a_proposal_above_the_budget_is_refused`, against both branches at `decomposition.py:349,462`.
- [ ] Correct `CLAUDE.md`: the column is the enforced cap, and the envelope's value is a ceiling on the policy branches only.
- [ ] Commit.
