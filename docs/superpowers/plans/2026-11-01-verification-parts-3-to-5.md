# Verification Parts 3 to 5: records, edges, and rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Correct the stale text, state the layer's edge, and back its rules with tests that fail when a rule breaks.

**Spec:** `docs/superpowers/specs/2026-11-01-verification-layer-design.md`, Parts 3 to 5.

---

### Task 1: Records

- [ ] Correct `dead_letter.py`, `config.py`, `docs/operations/verifier.md`, and `docs/operations/credentials.md`.

### Task 2: Edges

- [ ] Move `github_checks.py` to `ConfinedClient`: a route predicate, a named User-Agent, `X-GitHub-Api-Version` pinned, redirects off, the `total_count` refusal kept, the token per request, and an injected transport in tests. A malformed repository is a refusal.
- [ ] Create `tests/architecture/test_verification_edges.py`, and move the lane's row there. Pin sort-and-stop for non-bumps, and that unfiled checks never reach `/verify`.
- [ ] Mutate each row. Run `make check`. Commit.

### Task 3: Rules

- [ ] Add the test that named evidence is accepted only on `automated_check`.
- [ ] Open the five rules whose tests were seen by name, and strengthen any that doesn't assert its rule.
- [ ] Run a mutation pass over the enforced rules, and report the count and every survivor.
- [ ] Move history to the archive, and name each kept rule's test. Run `make check`. Commit.
