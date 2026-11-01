# Admission Part 2: scope comes from the member record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Nobody approves work that can't run for lack of a grant.

**Spec:** `docs/superpowers/specs/2026-11-01-admission-layer-design.md`, Part 2.

## Global Constraints

- Refusal: `target_repository_not_granted`, HTTP 409, recovery `restore_grant`, writing nothing.
- The check runs after `_validated_dependencies`, past replay, and again in `_approve`.
- Rotation units and units with no `target_repository` skip it. Intake stays open.

## Review Focus

- A replayed proposal answers as before.
- A proposal pending at a withdrawal refuses at approval, and its unit never registers.
- `bump_driver` and `rotation_decomposer` handle the 409 without looping or a false alert.
- A proposal with several units, one ungranted, refuses whole and writes nothing.
- A restored grant lets the same proposal proceed.

---

### Task 1: The refusal

- [ ] Write failing tests, one per Review Focus line.
- [ ] Widen `unit_grant_refusal` to a protocol carrying `required_capability` and `authority`, and call it at both points.
- [ ] Delete the unreachable bump check at `decomposition.py:435`, move its tests, and add a grant fixture to the proposal tests.
- [ ] Update the `CLAUDE.md` rule. Run `make check`. Commit.
