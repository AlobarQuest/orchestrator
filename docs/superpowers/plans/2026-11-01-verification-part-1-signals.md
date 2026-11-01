# Verification Part 1: every signal has a home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** A unit of any class stuck in `submitted` or `verifying` raises one keyed alert, and a verify refusal is a keyed alert, not a log line.

**Architecture:** `execution-status` serves a new field, `execution-watch` gains one key, and the verifier lane gains `KeyedAlerts`. History is absorbed in two pull requests, as in Admission Part 1.

**Spec:** `docs/superpowers/specs/2026-11-01-verification-layer-design.md`, Part 1. Devon's answer: the stall alert covers post-deploy units too.

## Global Constraints

- Key: `verification-stalled:<unit>`, raised on presence and gated on overdue.
- `verification_overdue` uses `dead_letter_stalled_verification_seconds`, passed in by the route.
- `verification-stalled` is an exception to `decide.py`'s rotation skip.
- The server deploys before the lane reads the field.
- A failed alert-token fetch never stops a rotation. An unread queue holds every key.
- A per-unit refusal exits as a finding, so one cause raises either `/fail` or an alert, not both.

## Review Focus

- A healthy unit passing through `submitted` in minutes raises nothing.
- A rotation unit stuck in `verifying` alerts, and every other rotation condition stays skipped.
- An old lane reads the new field without failing across the deploy.
- A failed alert-hub send doesn't fail the verify pass.
- The verifier launcher's exit codes still match `sds_deadman_finish` for rotations.

---

### Task 1: Pull request A

- [ ] Write failing tests for each constraint and Review Focus line.
- [ ] Add the field and pass the setting. Add the condition, and add absorb behavior as `ABSORB_VERIFICATION = True`.
- [ ] Add `KeyedAlerts` to the verifier lane, and its alert-token fetch to the launcher. Move its edge row: add `SDS_BRIDGE_ALERT_BEARER` and the alert-hub route.
- [ ] Run `make check`. Commit.

### Task 2: Land A, then pull request B

- [ ] Review, mutate, merge, deploy, and confirm a pass absorbs and fires nothing.
- [ ] Delete `ABSORB_VERIFICATION`. Merge. Confirm `0 firing`.
