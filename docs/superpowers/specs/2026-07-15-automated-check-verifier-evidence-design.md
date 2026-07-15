# Automated Check Verifier Evidence Design

**Status:** Approved for implementation by Devon on 2026-07-15

**Goal:** Let the existing submitted WS-6.4 revision-4 AC-006 unit receive independent, post-CI evidence and deterministic verifier adjudication without changing the approved package, rerunning the worker, or weakening the human merge gate.

## Problem

`intent-packages` legally describes AC-006 with `evidence_type: automated_check`. Orchestrator's verifier registry does not recognize that criterion vocabulary, so it returns `judgment_required` before inspecting evidence. The worker's current `runner.pr.opened` evidence was correctly recorded before GitHub Quality completed and therefore cannot prove the named check, exact head, Ruff, Pyright, or 105-test requirements.

The existing worker evidence route cannot add post-CI facts after submission because it requires an active claim in `claimed` or `executing`. The expired-claim recovery route has different semantics. Orchestrator already has verifier-only append-and-supersede persistence, but no public command exposes it.

## Design

Add one verifier-only post-CI evidence command. It reuses `append_verifier_evidence`, resolves the actual unit attempt server-side, and supersedes the current AC evidence head. It does not add a table, migration, background service, GitHub credential, or outbound GitHub call.

The command accepts a bounded `verifier.github.named_check` record containing:

- the package revision and AC identity;
- repository, pull-request number and URL;
- exact pull-request head SHA;
- check name, conclusion, run ID, and run URL;
- bounded expected-versus-observed assertions extracted by the independent verifier from the named check output.

Before storing the row, Orchestrator must prove:

- the caller has the `verifier` role;
- the unit is `submitted` or `verifying`;
- the revision and AC belong to that unit's approved decomposition mapping;
- the repository equals `constraints.target_repository` in the unit authority;
- the pull-request number and head equal the canonical `UnitPrBinding`;
- the head equals `verification_read_head_sha` for the unit's current attempt;
- the check name and stable references are non-empty;
- every assertion is bounded and has explicit expected and observed values.

The command must fail closed without writing evidence when any binding is missing or mismatched. Its idempotency key replays the same row and rejects conflicting reuse. The verifier evidence row supersedes the earlier worker row through the existing append-only chain.

## Evaluation

`automated_check` remains judgment-routed when there is no trusted `verifier.github.named_check` evidence. This preserves behavior for existing multi-criterion and legacy units that have only worker evidence.

When current evidence is `verifier.github.named_check`, the evaluator is deterministic:

- `success` is the only passing check conclusion; `neutral` and `skipped` do not pass;
- an explicit failing GitHub conclusion returns `failed`;
- repository, PR, armed head, check identity, run identity, or assertion defects return `failed_closed`;
- every expected value must equal its observed value;
- the passing adjudication points to the verifier-authored evidence row.

For WS-6.4 AC-006, the verifier will record assertions for the dispatch target, exact PR head, Quality check identity, Ruff success, zero Pyright errors, and exactly 105 passing tests. The evaluator does not parse the criterion's prose. The verifier role is responsible for translating the approved prose into bounded expected-versus-observed facts; Orchestrator independently validates their equality and canonical PR bindings.

## Lifecycle

The repair preserves the existing sequence:

1. Record post-CI verifier evidence while the unit remains `submitted`.
2. Invoke the existing `/verify` command with a fresh idempotency key.
3. The verifier evaluates AC-006, records a `passed` adjudication referencing the new evidence, and moves the unit to `completed`.
4. Devon reviews and merges or closes Change Manager PR #26 personally.

The current verifier must not be invoked before this repair is deployed because `awaiting_review` is terminal for replay under the existing service contract.

## Security And Trust Boundary

Workers cannot invoke the new command and cannot submit the reserved `verifier.github.named_check` type through the worker evidence route. The endpoint accepts bounded normalized facts, not logs or credentials. Existing M2M verifier authentication remains the authority boundary.

## Testing

Tests must prove:

- verifier evidence supersedes the pre-CI worker row and enables completion;
- exact repository, PR number, armed head, check name, successful conclusion, and assertion equality are required;
- missing binding, stale head, malformed facts, `neutral`, `skipped`, and explicit failure cannot pass;
- worker and system actors cannot record verifier evidence;
- worker evidence cannot impersonate the reserved verifier evidence type;
- replay creates no duplicate evidence;
- legacy `automated_check` without trusted post-CI evidence still routes to `awaiting_review`.

## Non-Goals

- No GitHub polling or webhook consumer.
- No new CI harness or background verifier service.
- No factory-runner change.
- No package revision or decomposition change.
- No database migration.
- No automatic pull-request merge.
- No generic structured acceptance-language redesign.
