# WS-3.3 - Protocol Smoke Tests and Runtime Protocol Semantics Design

**Intent package:** `ws-3.3-protocol-smoke-runtime-semantics`, revision 1  
**Approved hash:** `7829f22bfa30630a906d75131c84bc018c5dac3ceac7b933b7c9b46d23e5047a`  
**Status:** Approved by Devon; amended by adversarial review resolutions before implementation planning

## 1. Boundary

WS-3.3 proves that the merged WS-3.2 orchestrator runtime protocol is mechanically usable after approved package intake and human-approved decomposition.

This work is additive. It does not dispatch workers, publish external `factory-event/v1` events, deploy to production, mutate Coolify, merge PRs, make trackers canonical, or build Phase-5 verifier logic. It also does not turn `change-manager` into the generic orchestrator.

The deliverable is a tested local runtime protocol: a worker can claim, renew, execute, block, resume, request approval, submit evidence, and release control through the orchestrator; a verifier or human can review and complete through declared lifecycle edges; lease expiry recovers safely; standing-context versions are enforced and recorded; same-scope updates are distinct from authority expansion; Devon can inspect worker status through a read-only projection.

## 2. Current Architecture

WS-3.1 supplies:

- canonical work-unit state machine and transition authority;
- atomic, attempt-aware claims with expiring leases;
- worker evidence submission without worker-controlled completion;
- adjudication and waiver storage;
- local transactional events;
- API, CLI, and minimal human review surfaces.

WS-3.2 supplies:

- approved package intake from `intent-packages`;
- immutable package revision facts and normalized package projections;
- non-canonical decomposition proposals;
- human-only decomposition decisions;
- Draft work-unit creation through the existing lifecycle path;
- attributable local events for intake and decomposition.

WS-3.3 should not replace these surfaces. It should add runtime protocol semantics around them and then smoke-test the integrated behavior through public API/CLI paths.

## 3. Design Decisions

### D1 - Smoke Fixture Strategy

Use a mix:

1. **Synthetic protocol package fixture:** a small test package under orchestrator test fixtures that validates like a real approved software-delivery package. It has enough proposed units to exercise every runtime lifecycle path deterministically.
2. **Closed Phase-2 package fixtures:** register and decompose `ws-2.4-ci-evidence-control` and `ws-2.4-historical-listing-launch` as package-intake/decomposition acceptance examples through an explicit `protocol_fixture` intake mode.

The synthetic package drives exhaustive smoke paths. The two Phase-2 packages prove domain-neutral intake/decomposition against real historical contracts, but WS-3.3 must not execute their real work. The listing package remains universal-domain; no listing-specific profile is added.

Closed packages must not be smuggled through the existing executable package-intake path, because WS-3.2 intentionally accepts only `approved` status for executable intake. WS-3.3 adds a narrow fixture-only mode that accepts a chain-verified closed package revision for local protocol proof while marking the resulting units as non-executable acceptance examples unless a test explicitly moves a synthetic fixture unit through runtime states. The mode preserves approved hash/source/approval facts and records that the package was closed at fixture intake time.

### D2 - Preflight Gate Placement

Gate both **claim** and **execute**, with different meanings.

- Claim preflight answers: "May this worker acquire this unit with its declared standing context?"
- Execute preflight answers: "Has the worker's context drifted since claim in a way that changes authorization or reproducibility?"

This prevents queue capture by an unqualified or stale worker and catches context drift after a long claim/renew window. The execute gate should accept an unchanged claim-time context, a same-scope updated context, or a newly approved authority-expanding context. It rejects missing, stale, and unapproved authority-expanding context.

Preflight must be re-evaluated inside the same database transaction as claim and start. The standalone preflight endpoint is diagnostic only; it may create a snapshot, but a later claim/start cannot trust that snapshot without rechecking the current work-unit state, actor, authority envelope, package-required context, and supplied context fingerprint under lock.

### D3 - Standing-Context Fields

WS-3.3 standing context contains:

- `code_standards_version`;
- `security_standards_version`;
- `project_standards_version`;
- `foundation_contract_version` when declared by the target repo;
- `agent_id`;
- `authority_profile`;
- `runtime_name`;
- `runtime_version`;
- `skill_bundle_id`;
- `skill_bundle_version`;
- `package_required_context`, copied from approved package/decomposition metadata when present;
- `capabilities`, as normalized capability terms.

Version sources:

- code/project/security standard versions come from their repo `STANDARD_VERSION` files or declared frontmatter where already available;
- identity and authority profile come from the security-standards registry bundle already used by orchestrator identity tests;
- runtime and skill bundle are worker-submitted but recorded with actor attribution and checked against package/work-unit requirements;
- package-required context comes from the intaken package projection or decomposition metadata when present.

### D4 - Context Storage

Add an immutable `context_snapshots` table and link it from protocol records.

Each snapshot stores:

- actor ID and role;
- work-package revision ID and work-unit ID;
- claim ID and attempt when the snapshot is bound to a claim or execution attempt;
- normalized context JSON;
- context fingerprint;
- classification: `same_scope`, `authority_expanding`, `missing_required`, `stale`, or `accepted`;
- decision: `accepted`, `rejected`, or `requires_approval`;
- optional approval ID;
- created event ID.

Claims record `claim_context_snapshot_id`. Claims also record `execution_context_snapshot_id` once start succeeds for that attempt. Execute/start events include the same ID in the event payload. Evidence records get a nullable `context_snapshot_id` column and must use the current claim's execution snapshot for worker-submitted evidence unless the request supplies an equivalent accepted snapshot for the same unit, claim, actor, and attempt. This gives verifiers a stable way to prove which context the worker used without trusting free-form evidence payload text.

### D5 - Same-Scope vs Authority-Expanding Updates

Same-scope updates are allowed when all are true:

- capability set is unchanged or narrower;
- authority profile is unchanged or narrower;
- package-required context is still satisfied;
- standards versions are equal or newer within the same declared standard major version;
- skill bundle identity is unchanged or an approved replacement with no new capability terms;
- the approved package authority envelope permits the resulting capabilities.

Authority-expanding updates include:

- adding a capability term;
- moving a capability from prohibited to allowed or requires-approval;
- changing to a broader authority profile;
- changing worker runtime or skill bundle in a way that introduces new authority;
- losing a required standard or package context;
- changing package-required context after approval.

Authority expansion must not execute under the existing claim. Before claim, authority expansion rejects readiness for that worker context. After claim, authority expansion puts the unit on the existing `awaiting_approval` path or rejects start/evidence until a named human approval is recorded against the work unit and the exact context fingerprint.

### D6 - Skill-Subscription Model

Model skill/context update semantics as **work-unit authority-envelope facts**, not as a marketplace, persona system, or package-global subscription service.

The approved package and decomposition define the authority envelope. A worker's preflight supplies its concrete runtime/skill/context. The orchestrator compares the concrete runtime against the envelope for each claim or execute action. Same-scope updates can flow under the existing approval. Authority-expanding updates require a new human approval for that unit.

### D7 - Status Ledger Shape and Surface

The status ledger is a read-only projection from canonical tables and local events.

Fields:

- worker or agent ID;
- current work unit ID, key, title, and state;
- current claim ID, attempt, owner, acquired time, renewed time, lease expiry, and expiry status;
- last heartbeat or event time;
- current blockers from dependencies and `blocked` events;
- pending human approvals;
- latest evidence submission;
- latest adjudication;
- last failure event and failure reason;
- latest context snapshot classification and decision.

Expose:

- `GET /api/v1/status-ledger`;
- optional filters: `actor_id`, `work_unit_id`, `state`, `include_inactive`;
- CLI command: `orchestrator status-ledger --json` with equivalent filters.

Do not build UI in WS-3.3 unless design review finds API/CLI insufficient for a required acceptance criterion. Based on the current package, API/CLI is sufficient.

### D8 - Human Worker vs Factory Runner

WS-3.3 models a human or interactive agent worker using the same worker protocol, but it does not dispatch a worker.

Tests and CLI examples may simulate a worker actor calling API/CLI commands. They must not call GitHub Actions, `workflow_dispatch`, factory-runner code, or external publication. This distinction keeps the runtime protocol testable now while preserving WS-4 runner dispatch as a separate implementation.

### D9 - WS-3.4 Preparation

Local event names and payloads should be stable and explicit:

- `context.preflight_recorded`;
- `context.update_accepted`;
- `context.update_requires_approval`;
- `context.update_rejected`;
- `status_ledger.projected` only if a material projection read needs audit evidence;
- existing lifecycle/evidence/reclaim events retain their current semantics.

WS-3.3 does not publish external `factory-event/v1`. It only keeps local event payloads structured enough for WS-3.4 to map them later.

### D10 - Migration Strategy

Add forward-only Alembic migrations:

- create `context_snapshots`;
- add nullable context references to `claims` and `evidence`;
- keep existing rows valid with null context references;
- avoid rewriting WS-3.1/WS-3.2 events;
- build projections from existing events plus new WS-3.3 events.

Existing tests must continue to pass without requiring context for legacy direct WS-3.1 paths unless those tests enter the WS-3.3 protocol-smoke path.

## 4. Data Model

### `context_snapshots`

Columns:

- `id`;
- `work_package_revision_id`;
- `work_unit_id`;
- `claim_id`;
- `attempt`;
- `actor_id`;
- `actor_role`;
- `context`;
- `context_fingerprint`;
- `classification`;
- `decision`;
- `approval_id`;
- `created_at`;
- `event_id`;
- `idempotency_key`.

Constraints:

- `classification` enum: `accepted`, `same_scope`, `authority_expanding`, `missing_required`, `stale`;
- `decision` enum: `accepted`, `rejected`, `requires_approval`;
- `(work_unit_id, idempotency_key)` unique;
- `approval_id` required when decision is `accepted` for an authority-expanding update;
- when `claim_id` is present, `attempt` must match the linked claim attempt.

### Claim and Evidence Links

Add:

- `claims.context_snapshot_id`;
- `claims.execution_context_snapshot_id`;
- `evidence.context_snapshot_id`.

These are nullable for existing rows and legacy tests. WS-3.3 protocol smoke tests should assert they are present for worker paths that claim, start, and submit evidence.

## 5. Public API and CLI

### API

Add:

- `POST /api/v1/work-units/{unit_id}/preflight`;
- `GET /api/v1/work-units/{unit_id}/context-snapshots`;
- `GET /api/v1/status-ledger`.

Modify:

- `POST /api/v1/work-units/{unit_id}/claim` accepts inline `standing_context` and creates an accepted claim-bound snapshot in the same transaction.
- `POST /api/v1/work-units/{unit_id}/commands/start` accepts inline `standing_context` or a previously recorded snapshot, re-evaluates it under lock, and records an execution snapshot for the active claim attempt.
- `POST /api/v1/work-units/{unit_id}/evidence` records the current execution context or explicitly provided context snapshot.

Design preference: support inline context on claim/start for ergonomic CLI use, while internally storing a snapshot and returning its ID. A separate preflight endpoint remains useful for dry-run diagnosis but is not an authorization token.

### CLI

Add:

- `preflight`;
- `list-context-snapshots`;
- `status-ledger`.

Extend:

- `claim --context @context.json`;
- `start --context @context.json`;
- `append-evidence` infers the active execution context for the supplied attempt and token, or accepts an explicit equivalent `context_snapshot_id`.

## 6. Protocol Smoke Suite

Smoke tests should run against public API and CLI transport where practical.

Required paths:

- package intake -> approved decomposition -> Draft unit creation;
- Draft -> Ready;
- Ready -> Claim with preflight;
- claim renew;
- Claimed -> Executing with execute preflight;
- Claimed/Executing -> Blocked -> Ready;
- Claimed/Executing -> Awaiting Approval -> approval recorded -> Ready;
- Executing -> Submitted with evidence;
- Submitted -> Verifying -> Completed through verifier/human roles;
- Submitted/Verifying -> Revision Required -> Ready;
- Claimed/Executing -> Failed -> human retry authorization -> Ready;
- lease expiry -> reclaim -> stale credential rejection.

Smoke assertions:

- workers cannot complete canonically;
- workers cannot approve intent or decomposition;
- old claim tokens cannot mutate reclaimed units;
- preflight context appears in context snapshots, claim/evidence links, and local events;
- status ledger reflects each meaningful protocol state without mutating anything.

## 7. Status-Ledger Semantics

The ledger must never write lifecycle state. It may read:

- `work_units`;
- `claims`;
- `dependencies`;
- `approvals`;
- `evidence`;
- `adjudications`;
- `events`;
- `context_snapshots`.

It computes current status at request time. If later performance demands persisted projections, that is a future workstream; WS-3.3 does not need a projection table.

## 8. Phase-2 Control Packages

WS-3.3 should register and decompose both closed Phase-2 packages as local acceptance examples:

- `ws-2.4-ci-evidence-control`;
- `ws-2.4-historical-listing-launch`.

They should use package source facts from `intent-packages/main` and preserve approved hash/source/approval facts. Their decompositions can be minimal and explicitly non-executing:

- one or more work units representing protocol replay only;
- all real-world/external action remains prohibited;
- listing-launch remains universal-domain.

The exhaustive runtime smoke suite should not execute these packages' real work; it should use the synthetic protocol package.

Implementation note: the existing WS-3.2 executable intake path remains `approved`-only. The closed Phase-2 path must be named and tested as fixture/protocol acceptance intake, with architecture guards preventing it from becoming a general way to execute closed packages.

## 9. Risks and Controls

| Risk | Control |
|---|---|
| Smoke tests use private services and do not prove public protocol | Require API/CLI paths for smoke tests; private service tests are allowed only below public-contract tests |
| Preflight records context but does not enforce it | Claim and execute gates both call the same context policy evaluator |
| Same-scope update rule becomes hidden policy | Store classification and decision events with actor attribution |
| Status ledger becomes lifecycle authority | No POST/PATCH/DELETE ledger routes; architecture test rejects ledger mutation routes |
| Phase-2 fixtures execute real work | Mark them as protocol acceptance examples and keep external action prohibited |
| WS-3.4 leaks into WS-3.3 | Local events only; no `factory_events` publisher dependency or external store write |

## 10. Verification

Required local checks:

- focused migration tests;
- focused context policy tests;
- focused protocol smoke tests;
- API/CLI parity tests;
- status-ledger projection tests;
- architecture scope guards;
- full local `make check` against the documented PostgreSQL endpoint;
- final diff review against code standards.

Remote check:

- GitHub `Quality` must pass on the exact PR head before Devon reviews for merge.

## 11. Follow-Up Outside WS-3.3

- WS-3.4 maps local events and evidence to external `factory-event/v1`.
- WS-4 adds factory-runner dispatch.
- WS-5 adds independent verifier logic.
- Phase 6 adds tracker projections and governed learning.
