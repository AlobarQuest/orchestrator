# WS-3.3 Adversarial Architecture Review

**Design reviewed:** `docs/superpowers/specs/2026-07-06-ws33-protocol-smoke-runtime-semantics-design.md`  
**Intent package:** `ws-3.3-protocol-smoke-runtime-semantics`, revision 1  
**Approved hash:** `7829f22bfa30630a906d75131c84bc018c5dac3ceac7b933b7c9b46d23e5047a`  
**Review date:** 2026-07-06  
**Verdict:** Pass after design amendments below.

## Findings

### F1 - Blocking: Closed Phase-2 package handling contradicted WS-3.2 intake rules

**Risk:** The original design said WS-3.3 should register and decompose both closed Phase-2 packages, but WS-3.2 deliberately accepts only `approved` status for executable package intake. Running closed packages through the existing path would either fail or weaken the WS-3.2 invariant that closed/historical packages cannot become executable work by accident.

**Resolution:** The design now requires an explicit `protocol_fixture` intake mode for chain-verified closed packages. It records the closed status at fixture intake time, preserves approved hash/source/approval facts, and prevents fixture intake from becoming a general execution path for closed packages.

### F2 - Blocking: Standalone preflight could become a stale authorization token

**Risk:** A worker could pass standalone preflight, the unit or context could change, and a later claim/start could reuse the old result. That would create a time-of-check/time-of-use hole in the claim and execute gates.

**Resolution:** The design now states that claim and start must re-evaluate preflight inside the same database transaction under lock. The standalone endpoint is diagnostic only and is not an authorization token.

### F3 - Important: Context snapshots were not bound to claim attempt

**Risk:** Without `claim_id` and `attempt` on context snapshots, an old accepted snapshot could be replayed across attempts after reclaim, undermining stale-token and old-owner rejection.

**Resolution:** The design now adds `claim_id` and `attempt` to `context_snapshots`, requires attempt consistency when claim-bound, and links claim/start/evidence context to the active attempt.

### F4 - Important: Evidence context inference was under-specified

**Risk:** The original design said evidence should use the execution snapshot, but only stored execution snapshot IDs in event payloads. That would make evidence context inference ambiguous and hard to enforce.

**Resolution:** The design now adds `claims.execution_context_snapshot_id`, and worker-submitted evidence must use the current claim's execution snapshot unless the request supplies an equivalent accepted snapshot for the same unit, claim, actor, and attempt.

### F5 - Important: Authority expansion behavior was too loose

**Risk:** "Invalidates readiness or requires approval" left too much implementation discretion. A loose interpretation could let an authority-expanding update continue while merely marking readiness stale.

**Resolution:** The design now distinguishes before-claim and after-claim behavior. Before claim, authority expansion rejects readiness for that worker context. After claim, start/evidence is rejected or routed to `awaiting_approval` until a named human approval is recorded for the exact context fingerprint.

### F6 - Moderate: Status-ledger projection could be over-audited

**Risk:** A `status_ledger.projected` event for every read would turn a read-only inspection endpoint into audit noise and might imply the ledger is a first-class lifecycle event source.

**Resolution:** The design keeps status-ledger projection read-only and only mentions `status_ledger.projected` as optional for material projection reads. Implementation planning should avoid write-on-read unless a specific acceptance criterion requires it.

## Residual Risks

- The `protocol_fixture` intake mode must be tightly named, documented, and architecture-guarded. This is the highest implementation-risk area because it intentionally creates a second intake purpose.
- Context comparison depends on deterministic normalization and clear version comparison. The implementation plan must specify exact comparison helpers and tests for missing, stale, same-scope, narrower, and authority-expanding contexts.
- API/CLI-only status ledger is sufficient for WS-3.3, but Devon may later want a UI. That remains outside WS-3.3 unless an acceptance criterion fails without it.

## Scope Check

The amended design still excludes:

- factory-runner dispatch;
- GitHub Actions worker execution;
- production deployment;
- Coolify mutation;
- external `factory-event/v1` publication;
- tracker-canonical lifecycle state;
- automatic merge;
- autonomous intent or decomposition approval;
- Phase-5 independent verifier logic;
- brain learning or promotion loops.

## Required Implementation-Plan Constraints

The implementation plan must include tests for:

- closed package fixture intake rejected by the executable path and accepted only by the explicit fixture path;
- preflight rechecked inside claim/start transactions;
- context snapshots bound to claim ID and attempt;
- evidence context tied to the active execution snapshot;
- authority-expanding context rejected before claim and blocked after claim until named human approval;
- no status-ledger mutation routes;
- no `factory_events` external publisher dependency.

