# Admission Part 1: every signal has a home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Work waiting at an admission gate for 7 days raises one keyed alert, and a rotation's failed policy approval raises one too.

**Architecture:** `execution-status` serves non-unit rows, and `execution-watch` gains two keys. It lands in two pull requests, so history can't fire in the deploy window.

**Spec:** `docs/superpowers/specs/2026-11-01-admission-layer-design.md`, Part 1. Devon's answers: an undecomposed package with no future alerts once and is closed by hand; a sent-back proposal keeps alerting; the threshold stays `dead_letter_stalled_approval_seconds`.

## Global Constraints

- Keys: `admission-waiting:<subject>`, `admission-policy-not-applied:<proposal>`. `<subject>` includes its kind.
- The server serves `overdue`. The lane holds no threshold for these.
- A rejected proposal stops raising.
- A `draft` unit with a pending dependency is skipped.
- `policy_not_applied` is recorded for SYSTEM rotation refusals only, skipping `proposer_not_system`, keyed once per proposal, and raised only while `proposed`.
- An older lane reading the new rows must not fail: the client tolerates non-unit rows before the route serves them.

## Review Focus

- A row a client can't parse must not raise `execution-watch-unread` across a deploy.
- A subject under threshold, seeded, that becomes overdue stays silent; a new subject that becomes overdue alerts.
- A proposal approved or rejected resolves its alert on the next pass.
- A second `policy_not_applied` for one proposal doesn't conflict.
- `owned_elsewhere` keeps a bump's and a rotation's wait with their lanes.

---

### Task 1: Pull request A

**Files:** `src/orchestrator/services/reporting/execution_status.py`, `pending_decisions.py`, the response schema, `src/orchestrator/services/intake/decomposition.py`, `src/execution_watch/decide.py`, `cli.py`, `client.py`, and tests under `tests/execution_watch/`, `tests/services/`.

- [ ] Write failing tests for every Global Constraint and Review Focus line.
- [ ] Extract `pending_decisions`' queries for staged intakes, breakdowns, and proposals into functions returning ids and times, and keep its output unchanged.
- [ ] Add `subject_kind`, `subject_id`, `entered_at`, `overdue`, and `policy_not_applied` to the row, and make `unit_id` and the unit flags nullable.
- [ ] Record `decomposition.policy_not_applied` for rotation refusals, with a reason at each return.
- [ ] In the lane: tolerant parsing, new conditions, raise on presence, gate on overdue in `firing`, extend `owned_elsewhere` and its test. While `ABSORB_ADMISSION = True`, each pass adds every present `admission-*` key to `known.json` and sends none.
- [ ] Run `make check`. Commit.

### Task 2: Land A, then pull request B

- [ ] Review, mutate, merge, deploy, and confirm a pass absorbs and fires nothing.
- [ ] Delete `ABSORB_ADMISSION` and its branch. Run `make check`. Merge. Confirm a pass reads `0 firing`.

## Live proof

Recorded 2026-11-01: deployed as `d953942-adm1-amd64` and `40801f1-adm2-amd64`, each with the digest matched and no migration. Production absorbed 3 existing waits: package `f921c842` (July 30), and proposals `8f7132f8` (August 3) and `28b39732` (August 4). After the absorb switch was deleted, scheduled passes read 5 raised, 0 firing. No admission alert has fired on a new subject, and alert-hub's D1 quota was exhausted during this deploy. A refused proposal for an ungranted repository has tests only until a real event.

