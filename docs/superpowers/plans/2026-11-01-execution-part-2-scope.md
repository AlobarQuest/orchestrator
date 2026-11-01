# Execution Part 2: scope comes from the member record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** A withdrawn grant stops new factory work at the claim.

**Architecture:** One refusal in `claim_unit`, one term in `_readiness_eligibility_error`, and one fact in `dead_letter`, all sharing `target_grant.py`.

**Spec:** `docs/superpowers/specs/2026-11-01-execution-layer-design.md`, Part 2.

## Global Constraints

- A refusal at `claim_unit` writes nothing and spends no attempt.
- Rotation units and units with no `target_repository` skip the check.
- `renew_claim` stays ungated.
- Refusal code: `target_repository_not_granted`.
- Devon accepted the cleanup: a dispatched unit refused at claim stays `ready` until a person fails and cancels it.

## Review Focus

- A replayed claim with the same idempotency key after a refusal answers the refusal, not `idempotency_conflict`.
- A rotation unit's lapsed claim through `release_expired_claim` never meets the grant term.
- A bump on a repository with `dependency_updates` but no `app_changes` still claims.
- A retired member refuses.
- `dead_letter`'s requeue advice doesn't name a requeue that refuses.

---

### Task 1: The refusal

**Files:** Modify `src/orchestrator/services/execution/target_grant.py`, `services/execution/dispatch.py`, `services/lifecycle/claims.py`, `services/reporting/dead_letter.py`. Test `tests/services/test_claim_grant.py`.

- [ ] Write failing tests, one per Review Focus line, plus `test_a_withdrawn_grant_refuses_the_claim_and_writes_nothing`, `test_a_unit_with_no_target_repository_claims`, `test_reclaim_parks_a_withdrawn_unit_failed`, and `test_requeue_advice_omits_a_withdrawn_repository`.
- [ ] Move `TARGET_REPOSITORY_NOT_GRANTED` and the change-class reader to `target_grant.py`, and add `unit_grant_refusal(session, unit, revision) -> DomainError | None`. Keep dispatch's behavior.
- [ ] Call it in `claim_unit` between `confinement_refusal` and `policy_authority_gate`. Add the code to `_ELIGIBILITY_CODES` and `_eligibility_error`, and the term to `_readiness_eligibility_error`. Add the fact to `dead_letter._requeue_target_action`.
- [ ] Run `make check` with `PGTZ=UTC`. Commit.

### Task 2: Land and prove

- [ ] Whole-branch review and mutation pass. Deploy. Prove a granted claim path by `membership_check` and the deploy's own read. A live refusal needs a withdrawal, which needs Devon.
- [ ] Update `CLAUDE.md`'s dispatch rule with the claim.
