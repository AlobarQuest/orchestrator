# WS-5.1 Verifier Design

**Status:** Approved scope, implementation pending
**Intent package:** `ws-5.1-verifier` pending registration in this repo session
**Scope:** Phase 5 WS-5.1 only. No release immutability, artifact digest binding, post-deploy unit creation, tracker canonicalization, brain learning or promotion, graduation automation, automatic merge, or automatic deployment.

## 1. Baseline

The verified implementation baseline is orchestrator `main` at `b5fa2959deeb2a28387860e261e29493ce0518d6`.

- `SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check` passed with 710 tests.
- `cd /Users/devon/Projects/project-standards && uv run portfolio foundation` reported `violations=0 accepted=0 unknown=0`.
- Production `https://sds.alobar.net` returned 200 for `/health/live`, `/health/ready`, and `/openapi.json`.
- Missing M2M credentials against `/api/v1/status-ledger` returned 401.
- The BWS helper at `/Users/devon/Projects/vps-backup/bws-token.sh` can source `BWS_ACCESS_TOKEN` for this shell without printing secret values.

Existing orchestrator facts to preserve:

- Work-unit lifecycle truth lives in the orchestrator database.
- Workers can submit evidence but cannot record adjudications.
- `completed` already requires `completion_satisfied=True`.
- Completion satisfaction is derived from current terminal adjudications for required acceptance criteria mapped to the work unit by approved decomposition.
- Satisfying outcomes are `passed` and `not_applicable`; `waived` satisfies only through the existing waiver guard.
- Evidence and adjudications are append-only with supersession semantics.
- Evidence, adjudication, context, and lifecycle rows already publish local events and can be projected through the status ledger and event publication layer.

## 2. Design Summary

WS-5.1 adds a verifier service and authenticated API command:

```text
POST /api/v1/work-units/{unit_id}/verify
```

The verifier is a lifecycle client and evidence evaluator. It loads a submitted or verifying work unit, its approved package revision, the active approved decomposition, required acceptance criteria mapped to that unit, and current recorded evidence/adjudication state. It records verifier findings as ordinary orchestrator evidence and records verifier decisions as ordinary adjudications. It then drives the existing lifecycle to `completed`, `revision_required`, or `awaiting_review`.

The verifier does not create a second canonical status model. The initial implementation should not add persistence for verifier runs. A verification attempt can be reconstructed from local events, verifier evidence rows, adjudication rows, and the resulting lifecycle transition. Add a run table only if a future read API needs unreconstructable run metadata.

## 3. Evaluator Inputs

Each criterion evaluation consumes:

- the `PackageAcceptanceCriterion` row: `ac_id`, `condition`, `evidence_type`, `evidence`, and `approver`;
- the current terminal evidence row for the same revision, work unit, and AC ID;
- the current terminal adjudication row for the same revision, work unit, and AC ID;
- the approved decomposition mapping for the work unit;
- the work unit state and version.

Recorded evidence is trusted only as bounded structured data already accepted by the orchestrator. External systems such as GitHub, CI logs, production probes, infra lane tools, and web pages are evidence sources only when their normalized facts, stable references, hashes, or small summaries have already been recorded as orchestrator evidence.

WS-5.1 does not call GitHub, change-manager, infraops, trackers, brains, or production infrastructure.

## 4. Evaluator Registry

Implement a deterministic evaluator registry keyed by `PackageAcceptanceCriterion.evidence_type`.

Initial categories:

- `test`, `tests`, `pytest`, `runner.verification`, and `gate.summary`: pass when current evidence payload has an explicit successful result, such as `status: passed`, `status: success`, `conclusion: success`, or `exit_code: 0`; fail when payload has explicit failure, such as `status: failed`, `status: failure`, `conclusion: failure`, or non-zero `exit_code`.
- `security.scan` and `security_scan`: pass when current evidence payload has no block or warn findings, such as `block: 0` and `warn: 0`, or equivalent uppercase keys; fail when block or warn count is non-zero.
- `github.checks` and `github.check_run`: pass when current evidence payload reports all referenced check conclusions as `success`, `neutral`, or `skipped`; fail when any conclusion is `failure`, `cancelled`, `timed_out`, or `action_required`.
- `health.probe` and `production.health`: pass when current evidence payload reports every probe HTTP status in the 200-299 range; fail when any required probe is outside that range.
- `infra_lane.final`: pass when current evidence payload reports `status: completed` and includes a stable final evidence reference; fail when status is `failed` or `cancelled`.
- `human.review`, `code_review`, `judgment`, `manual`, and unknown evidence types: judgment-only, route to `awaiting_review`.

Malformed payloads, missing current evidence, stale supersession chains, unrecognized deterministic status values, or insufficient facts fail closed for deterministic evidence types.

## 5. Adjudication Behavior

For each required AC mapped to the unit:

- deterministic pass records a verifier adjudication with outcome `passed` and `evidence_id` pointing at the evidence that proved the criterion;
- deterministic fail records verifier finding evidence and a verifier adjudication with outcome `failed`;
- missing, malformed, stale, untrusted, or insufficient deterministic evidence records verifier finding evidence and outcome `failed`;
- judgment-only criteria do not receive a fabricated pass or fail from the verifier; the unit routes to `awaiting_review`;
- `not_applicable` may be recorded only when the approved package/decomposition explicitly identifies the criterion as not applicable to this unit.

Existing adjudication supersession remains the only correction mechanism. A verifier rerun with new evidence supersedes the previous current adjudication for that AC through the existing `record_adjudication` path.

## 6. Lifecycle Behavior

Allowed input states are `submitted` and `verifying`.

The verifier should:

1. Transition `submitted -> verifying` before evaluating, using the existing lifecycle service.
2. Evaluate every required mapped AC.
3. If every required AC has a satisfying current verifier adjudication, transition to `completed`.
4. If one or more deterministic ACs fail or fail closed, transition to `revision_required`.
5. If one or more required ACs require judgment and no deterministic failure exists, transition to `awaiting_review`.

The existing completion guard remains authoritative. If the verifier attempts `completed` without satisfying adjudications, the lifecycle guard must reject the transition.

## 7. Idempotency

The API command carries the normal `idempotency_key` and `expected_version`.

The verifier derives child idempotency keys from the parent key:

- `{parent}:transition:verifying`
- `{parent}:evidence:{ac_id}`
- `{parent}:adjudication:{ac_id}`
- `{parent}:transition:{target}`

Replaying the same verify command returns the same effective result without duplicating evidence, adjudications, or lifecycle transitions. A verifier rerun with a new parent key may supersede prior adjudications based on newer current evidence.

## 8. Failure Handling

Verifier infrastructure failure must not complete a unit.

If the verifier can still write safely, it records bounded `verifier.infrastructure_failure` evidence and transitions to the least-authoritative safe state allowed by the current lifecycle:

- `awaiting_review` when the failure prevents a trustworthy deterministic decision;
- `revision_required` when the failure establishes evidence is unusable or insufficient.

If database mutation itself fails, the request returns an error and leaves the unit uncompleted.

## 9. API Response

The verify route returns a structured response with:

- `unit_id`;
- `state`;
- `version`;
- `result`: `completed`, `revision_required`, `awaiting_review`, or `failed`;
- `evaluations`: one row per required AC with `ac_id`, `evidence_type`, `status`, `outcome`, `evidence_id`, `adjudication_id`, and `reason`.

This response is a command result, not canonical lifecycle truth. The work-unit row, events, evidence, and adjudications remain canonical.

## 10. Security And Secrets

WS-5.1 introduces no new secret, M2M credential, or runtime env file.

If a future verifier automation credential is introduced, it must use the existing BWS-managed M2M pattern:

- fetch by stable UUID at runtime;
- declare the UUID in `.bws-secrets.toml`;
- store only token hashes in `ORCHESTRATOR_M2M_CREDENTIALS`;
- assign non-worker role through `ORCHESTRATOR_M2M_ROLES`;
- never write raw tokens to tracked files, prompts, logs, package YAML, evidence, PR bodies, or generated artifacts;
- run the security scanner in any repo touched by credential or runtime config changes.

## 11. Tests

Focused tests must prove:

- deterministic pass records adjudication and completes when all mapped ACs satisfy;
- deterministic fail records verifier finding/adjudication and moves to `revision_required`;
- missing deterministic evidence fails closed and moves to `revision_required`;
- judgment-only criteria route to `awaiting_review`;
- replay does not duplicate evidence, adjudications, or lifecycle transitions;
- completion guard integration still rejects completion without satisfying adjudications;
- workers cannot invoke verifier adjudication behavior.

## 12. Non-Goals

Do not implement:

- release immutability or artifact digest binding;
- post-deploy verification unit creation;
- production deployment;
- GitHub dispatch changes;
- local-heavy runtime changes;
- change-manager or infraops mutation behavior;
- tracker lifecycle truth;
- brain learning/promotion;
- graduation automation;
- automatic merge.
