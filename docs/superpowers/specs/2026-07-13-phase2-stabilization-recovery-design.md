# Phase 2 Stabilization Recovery Design

**Date:** 2026-07-13
**Status:** approved 2026-07-13

## Goal

Restore one trustworthy, evidence-backed program state before any further Phase 2 work or
infrastructure mutation, then execute every item in the July 12 remediation order without changing
its dependency constraints. The first checkpoint determines whether PR #52's production-drill
subsystem should be kept, narrowed, or reverted; later remediation phases receive separate plans
instead of becoming one unsafe "fix the factory" workstream.

## Problem Statement

The July 12 remediation correctly paused forward Phase 2 work after finding that production did
not serve the recovery routes used to mark program criteria complete. The following session then
implemented PR #52 before closing the original production baseline. That change added a dedicated
production-drill aggregate, three migrations, runtime observations, fixed scenario controls, a
production runner, and new startup credential requirements.

PR #52 is merged and CI-green, but it is not deployed and has never produced production evidence.
Its implementation is therefore neither rejected nor accepted as the production solution. The
program needs an explicit architectural checkpoint before creating credentials, provisioning a
new observer boundary, or deploying the new startup contract.

## Verified Baseline

The following facts were independently verified on 2026-07-13:

- Remote `main` is `2fa9195375060d0d61845c95eca3b83fb8b50cec`, the merge of the Task 6 handoff.
- PR #52 merged at `1f0a2369a33d706673bec4ebe2dda87754b9dbe7` after two terminal-success Quality checks.
- Those checks reported 1,374 passed and one skipped test, plus clean Ruff, formatting, Pyright,
  migrations, and image build.
- `https://sds.alobar.net/health/ready` returns HTTP 200 with `{"status":"ok"}`.
- Live OpenAPI contains the six WS-P2.1/WS-P2.15 recovery surfaces named by remediation item 0.2:
  `recover-evidence`, `dead-letter`, `requeue`, `reconciliation/detect`, `consistency-check`, and
  `pr-binding`.
- Live OpenAPI does not contain any `runtime-observations` or `production-drills` route. PR #52 is
  not deployed.
- The live route inventory cannot prove the serving image tag, digest, commit, migration head, or
  container identity.
- The production-drill evidence document does not exist, and no production drill run has been
  evidenced or HUMAN-closed.
- The constrained read-only runtime observer required by PR #52 is specified but not provisioned.
- The original checkout's unexplained deletion of the infrastructure-session boundary was prior-
  agent residue. The committed boundary is restored and the checkout is clean.
- The isolated stabilization worktree starts from remote `main`; 258 architecture tests pass.

## State Model

Every program claim uses exactly one of these states:

1. **Implemented:** code exists on a reviewed branch or `main`.
2. **Verified:** deterministic checks passed for the exact commit.
3. **Deployed:** the running artifact identity and migration state are recorded.
4. **Production-proven:** the live acceptance behavior passed and its evidence is retained.
5. **Program-complete:** the governing scorecard cites that retained production evidence.

No later state may be inferred from an earlier state. In particular, merge or CI success is not
deployment evidence, and route presence alone is not proof that a recovery scenario works.

## Decision

Use a repository-only stabilization checkpoint before any infrastructure session.

The checkpoint preserves PR #52 while auditing it against the original remediation requirement.
It does not deploy or provision the subsystem and does not assume that merged work must survive.
The checkpoint ends with one explicit disposition:

- **Keep:** the current subsystem is the smallest mechanically sound way to obtain the required
  production proof.
- **Narrow:** the subsystem's core boundary is sound, but code or infrastructure not required by
  the acceptance contract is removed before deployment.
- **Revert:** the subsystem introduces more authority, operational coupling, or failure surface
  than the production proof warrants. Revert commits preserve its history for later reuse.

Devon approves the disposition before an implementation plan is written for deployment or revert.

## Relationship To The July 12 Remediation Order

`docs/superpowers/plans/2026-07-12-remediation-order.md` remains the authoritative defect list and
dependency order. This design does not replace or renumber it.

The repository-only PR #52 review is a **recovery preflight inside the open Phase 0 boundary**, not a
new remediation phase and not the factory program's original Phase 0. It exists because the prior
session changed `main` after the remediation order was written: deploying current `main` now also
deploys an unproven production-drill subsystem and a new fail-closed startup contract. The preflight
decides what `main` must contain before remediation item 0.1 can honestly complete.

After the preflight, work continues in this exact sequence:

| Sequence | Authoritative remediation scope | Planning boundary |
|---|---|---|
| 1 | Phase 0 items 0.1-0.5 | Finish production truth, drills, scorecard rebaseline, and the executable production-attestation guard in a separately authorized infrastructure session plus its repository closeout. |
| 2 | Phase 1 items 1.1-1.2 | Fix factory-runner workspace exclusion and `local-heavy-renew`; test the real HTTP adapter. |
| 3 | Phase 2 items 2.1-2.4 | Ship the per-mapped-AC writer, evidence-row evaluator, command-aware result capture, and evidence vocabulary mapping as one atomic workstream. |
| 4 | Ongoing meta-fix | Pull the WS-P2.2 improvisation counter forward immediately after Phase 2. |
| 5 | Phase 3 items 3.1-3.2 | Obtain Devon's HUMAN-path decision, then implement only that selected browser/CLI boundary. |
| 6 | Phase 4 items 4.1-4.6 | Execute WS-P2.16 in its fixed internal order, including local-heavy coverage. |
| 7 | Phase 5 items 5.1-5.3 | Resolve `ac_id`, authority projection, and self-discovering vocabulary coherence as their own workstream. |
| 8 | Phase 6 items 6.1-6.5 | Remove the five bounded ergonomics improvisations without widening runner authority. |

Each row receives its own specification or implementation plan before code changes. Completing one
row never implies completion of the next.

### Load-Bearing Constraints Preserved

- Nothing in Phases 1-6 starts until Phase 0 is production-proven and rebaselined.
- Phase 1 lands before later factory runs because its defects fire on every local-heavy run.
- Phase 2 items 2.1-2.4 ship whole; fixing evidence vocabulary alone can halt every multi-AC unit.
- The improvisation counter follows Phase 2 rather than waiting for the rest of WS-P2.2.
- Phase 3 stops for Devon's decision before implementation.
- Phase 4 executes 4.1 through 4.6 in order; the submit guard cannot precede vocabulary enforcement
  and a real factory-runner PR-binding writer.
- The hosted runner's deliberate narrowness is not treated as a defect.
- Infrastructure mutation, repository investigation, and CI triage remain separate sessions.

## Stabilization Components

### 1. Truth Reconciliation

Build a single status matrix from live evidence, repository history, CI, and retained artifacts.
Correct stale claims in the Phase 2 scorecard and remediation documents without rewriting their
historical findings. Each corrected claim records its evidence date and state-model level.

The current expected remediation baseline is:

- 0.1: **partially satisfied** — the earlier recovery surface is deployed, but current `main` is not.
- 0.2: **satisfied for the six originally named routes** — live OpenAPI contains them.
- 0.3: **open** — five production drills have not been proven.
- 0.4: **open** — criteria #5, #7, and #13 have not been rebaselined against fresh evidence.
- 0.5: **open** — no executable scorecard-to-production attestation guard exists.

### 2. PR #52 Requirements Trace

Map every production-drill production module, migration, route, credential, and external capability
to a specific acceptance requirement. Classify each element as required, defensive, accidental,
or unrelated. An element without a concrete requirement is a narrowing or revert candidate.

The review starts from the original required outcome: prove recovery behavior against the deployed
control plane without private SQL, worker-supplied runtime provenance, generic host execution, or
HUMAN impersonation. It does not start from the implementation's current shape.

### 3. Mechanical Soundness Review

Review the PR #52 diff against `e4bfb13`, the portfolio code standards, and the repository's known
invariants. The review must cover:

- production startup and rollback when new credential configuration is absent or inconsistent;
- migration upgrade, downgrade, immutability, and partially applied deployment behavior;
- separation of observer, drill SYSTEM, HUMAN, and infrastructure authority;
- whether the observer boundary can be provisioned without root SSH, Docker-socket exposure,
  caller-selected targets, or mutation capability;
- transactionality and idempotency of scenario creation, failure, restart, and closeout;
- isolation of synthetic records from ordinary queues and operator views;
- whether public adapters, rather than mocked service functions, exercise every required path;
- file and function responsibility, especially the 1,630-line production-drill service;
- suppression comments, duplicated contracts, stale reports, and tests that cannot fail.

The review plants representative real defects where safe: remove a required route from a temporary
test subject, break a client call, and alter a required credential mapping. The relevant guard must
fail for the expected reason. Production code is not mutated merely to perform the audit.

### 4. Disposition Gate

The audit produces a short decision record containing:

- the minimum production-proof contract;
- requirement-to-code traceability;
- blocking and non-blocking findings;
- estimated keep, narrow, and revert work;
- rollback and deployment prerequisites;
- the recommended disposition and rejected alternatives.

No infrastructure mutation follows automatically. Devon reviews and approves the disposition.

### 5. Planning Boundary

After approval, create one implementation plan for the recovery preflight and selected repository
changes. Deployment, credential provisioning, observer provisioning, production drill execution,
and the controlled restart remain a separate infrastructure-mutation session with fresh explicit
authorization. After Phase 0 closes, create the Phase 1 plan; continue one dependency-bounded plan at
a time through the sequence above.

Remediation Phase 1 and forward program Phase 2 work remain blocked until remediation Phase 0 is
honestly closed.

## Verification Strategy

The stabilization audit uses cumulative evidence:

1. Record the exact base and head commits and the live OpenAPI hash.
2. Re-run focused adapter and architecture suites sequentially.
3. Run the full PostgreSQL-backed `make check` gate and inspect the collected count.
4. Run the portfolio code-standards review on the PR #52 diff.
5. Run the security scanner and require zero BLOCK findings.
6. Run the planted-defect checks and record that each guard fails for the intended predicate.
7. Have two independent reviewers examine different failure lenses: halt/rollback risk and
   predicate/delivery soundness.
8. Reconcile all findings into the disposition record; do not average conflicting conclusions.

DB-backed suites run sequentially because fixtures recreate the shared test schema.

## Non-Goals

- No production deployment, restart, credential creation, BWS mutation, Coolify mutation, or
  observer provisioning.
- No execution of production drills.
- No implementation of remediation Phases 1-6 or WS-P2.16 inside the recovery preflight; they remain
  mandatory later planning boundaries in this design.
- No rewrite of PR #52 history and no deletion of its evidence.
- No claim that the MVP or later Phase 2 work is production-proven without retained evidence.
- No broad orchestrator refactor unrelated to the selected disposition.

## Deliverables

The stabilization checkpoint produces:

1. corrected program/remediation status documents;
2. a PR #52 requirement-to-code trace;
3. a code-quality and mechanical-soundness review with planted-defect evidence;
4. a keep/narrow/revert decision record approved by Devon;
5. a detailed implementation plan for the approved disposition;
6. an updated handoff that preserves the fresh infrastructure-session boundary.

After that checkpoint, the program produces one reviewed plan and one retained evidence package for
each remaining remediation row in the sequence table.

## Success Criteria

The checkpoint is complete only when:

- every load-bearing status claim cites current evidence and uses the state model;
- the exact production/runtime gap is documented without inferring artifact identity from routes;
- every PR #52 production component traces to a requirement or is marked for removal;
- full checks, security scan, standards review, and planted-defect tests have recorded outcomes;
- Devon has approved keep, narrow, or revert;
- the next implementation plan contains no infrastructure mutation mixed with repository work;
- all items 0.1-6.5 and the improvisation counter have an explicit planning boundary and none are
  silently absorbed into the PR #52 disposition.
