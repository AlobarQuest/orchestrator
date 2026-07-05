# WS-3.2 Package Intake and Decomposition Design

**Status:** Proposed for Devon review  
**Date:** 2026-07-05  
**Intent package:** `ws-3.2-package-intake-decomposition`, revision 1  
**Approved intent hash:** `84c929bc0860b6a585a62ec02fa35d9cdf89fce84773660aea1e383d955689df`

## 1. Objective

Extend the WS-3.1 orchestrator so it becomes the persistent intake point for approved
immutable intent-package revisions and the canonical owner of human-approved work-unit
decomposition.

WS-3.2 adds two boundaries:

1. **Package intake:** record that a specific approved package revision, identified by
   hash and source reference, is eligible for orchestration.
2. **Decomposition approval:** let agents or humans propose work units, but create
   canonical Draft work units only after a named human approves the proposal.

It does not dispatch workers, publish external factory events, run protocol smoke tests,
verify evidence, deploy infrastructure, or merge pull requests.

## 2. Baseline

The verified WS-3.1 baseline is:

- `work_package_revisions` already stores package ID, revision, content hash, source
  path, source commit, approval facts, enforcement snapshot, authority fingerprint, and
  registration events.
- `register_revision` is a manual, human-only API/service path. It trusts the caller to
  supply correct approved-package facts.
- `register_approved_unit` is a manual, human-only path that creates Draft work units and
  stamps decomposition approval facts directly onto the unit.
- Dependencies, claims, evidence, adjudications, waivers, local events, API, CLI, and UI
  behavior pass the WS-3.1 regression suite.

Current foundation matrix result is red for orchestrator code-standards onboarding and
unknown for infra metadata. That is recorded baseline state, not part of WS-3.2 unless
Devon approves a package revision expanding scope.

## 3. Design Decisions

### 3.1 Intake Boundary

**Decision:** Support both:

- A CLI command that reads a local package directory from `intent-packages`, verifies it
  using the `intent_packages` library/CLI behavior, builds a normalized intake payload,
  and submits it to the orchestrator API.
- An API endpoint that accepts the normalized payload, enforces idempotency/conflict
  rules, and persists intake facts.

**Why:** The CLI is the operator-friendly path and can read YAML without making the API
depend on filesystem layout. The API remains the service contract and lets future tools
submit already-normalized package facts.

**Not in WS-3.2:** A webhook from `intent-packages approve`, background repository
polling, or automatic intake on approval.

### 3.2 Approval Verification

**Decision:** Use a staged verification model:

1. CLI verifies the package with `validate`, `hash`, and `verify-approval` before
   submitting.
2. CLI records git source facts: repository URL/name, path, HEAD commit, package status,
   revision, approved hash, approver, approved time, approval event ID, and approval
   ledger commit.
3. API enforces shape, executable status, required approval fields, idempotency, and
   conflict behavior.
4. API records `verification_mode: staged_cli_verified` and `verification_limitations`
   so the record is honest about what was checked at intake time.

**Why:** WS-3.2 can prevent mutable or unapproved package content from becoming
executable work without pretending to provide cryptographic git-signature verification
that the intent-package system does not yet implement.

**Future:** Stronger event-chain replay, remote git object verification, and signature
verification can be added without changing the authority anchor.

### 3.3 Package Content Storage

**Decision:** Store source/hash plus normalized projection, not a verbatim editable
package snapshot.

The projection contains only fields needed by WS-3.2 through WS-3.4:

- package ID, title, revision, profile, status at intake;
- source repository, source path, source commit;
- approved hash, approved actor, approved time, approval event/ledger facts;
- outcome summary and scope boundaries;
- authority envelope and authority fingerprint;
- acceptance criteria with IDs, evidence type, evidence text, approver;
- dependencies from the package envelope;
- required checks and rollback plan from `software-delivery` profile fields;
- declared applicable standards.

**Why:** The orchestrator needs enough immutable information for readiness and
decomposition decisions even if the source repo is temporarily unavailable, but it must
not become a second package authoring store.

### 3.4 Proposal Storage

**Decision:** Add separate `decomposition_proposals` and child tables/JSONB structures,
not versioned work-unit drafts.

Proposals are non-canonical review artifacts. Draft work units are canonical lifecycle
records. Mixing those concepts would make "agent proposed" look too much like
"orchestrator accepted."

Minimum proposal record:

- package revision ID;
- proposal number or UUID;
- proposed by, actor role, proposed at;
- idempotency key;
- rationale;
- proposed units;
- internal dependency edges by proposed unit key;
- external dependencies;
- authority requirements;
- AC mappings;
- retained package-level ACs and rationales;
- decision state: `proposed`, `approved`, `rejected`, `revision_required`,
  `superseded`;
- decision actor, decision time, and reason.

### 3.5 Human Approval Semantics

**Decision:** One active approved decomposition per package revision.

- A package revision can have many proposals.
- Proposal submission never creates work units.
- A named human can approve, reject, or require revision.
- Rejected and revision-required proposals remain auditable.
- Approving a proposal creates Draft work units and dependencies in one transaction.
- A second approval is rejected while an active approved decomposition exists.
- Supersession is a named human action and must record why the previous approved
  decomposition is being replaced.

**Why:** Decomposition is judgment. The system can accept agent help, but canonical work
exists only after human approval.

### 3.6 Acceptance-Criterion Mapping

**Decision:** Decomposition approval requires total AC disposition.

Every package AC must have exactly one of:

- mapped to one or more proposed work units; or
- retained at package level with a human-approved rationale explaining why it is not a
  work-unit AC yet.

Unmapped ACs block approval. A retained package-level AC is not a waiver and does not
mean passed. It is a structural note that later verifier/adjudication work must still
account for it.

**Why:** WS-2.4 exposed silent AC loss as a real failure mode. WS-3.2 prevents that at
decomposition time without implementing Phase-5 verification.

### 3.7 Phase-2 Packages

**Decision:** Use the two closed Phase-2 packages and WS-3.1 package as fixtures and
acceptance examples, not real executable data.

They should prove:

- software-delivery profile intake;
- universal non-software package projection;
- AC mapping/retained criteria behavior;
- authority and dependency projection.

They should not be moved through real execution in WS-3.2. Real re-run through
orchestrator remains a Phase-3 exit target after WS-3.3/WS-3.4 support exists.

### 3.8 API, CLI, UI Scope

**Decision:** MVP includes:

API:

- `POST /api/v1/package-intakes`
- `GET /api/v1/package-intakes/{id}`
- `POST /api/v1/package-intakes/{id}/decomposition-proposals`
- `GET /api/v1/package-intakes/{id}/decomposition-proposals`
- `GET /api/v1/decomposition-proposals/{id}`
- `POST /api/v1/decomposition-proposals/{id}/approve`
- `POST /api/v1/decomposition-proposals/{id}/reject`
- `POST /api/v1/decomposition-proposals/{id}/require-revision`
- optional `POST /api/v1/decomposition-proposals/{id}/supersede` if the implementation
  needs replacement after approval.

CLI:

- `intake-package <path> --source-repository ...`
- `show-package-intake <id>`
- `propose-decomposition <package-intake-id> --data <json>`
- `list-decomposition-proposals <package-intake-id>`
- `show-decomposition-proposal <proposal-id>`
- `approve-decomposition <proposal-id> --reason ...`
- `reject-decomposition <proposal-id> --reason ...`
- `require-decomposition-revision <proposal-id> --reason ...`

UI:

- Intake detail page.
- Proposal detail page with units, dependencies, AC mapping, retained ACs, authority,
  and rationale.
- Human decision controls for approve/reject/revision-required.

No visual workflow builder, drag-and-drop graph editor, tracker projection, or dispatch UI.

### 3.9 Migration Strategy

**Decision:** Additive migrations only.

Keep existing WS-3.1 tables and tests intact. Add new tables for intake metadata and
proposals while preserving existing `work_package_revisions` and `work_units`.

Recommended shape:

- Extend `work_package_revisions` only with fields that are core revision facts and
  cannot live elsewhere without duplicating identity: `profile`, `status_at_intake`,
  `source_repository`, `approval_ledger_commit`, `verification_mode`,
  `verification_limitations`.
- Add `package_intakes` only if a separate intake audit aggregate is needed after
  implementation spike. Prefer enriching `work_package_revisions` first because WS-3.1
  already treats it as immutable registration of one package revision.
- Add `decomposition_proposals`.
- Add `decomposition_proposal_units`.
- Add `decomposition_proposal_dependencies`.
- Add `decomposition_proposal_ac_mappings`.
- Add `decomposition_proposal_retained_acs`.
- Add an approved-decomposition marker on `work_package_revisions` or a small
  `approved_decompositions` table to enforce one active approved decomposition.

Existing work units remain the canonical lifecycle rows. They are created only from an
approved proposal.

### 3.10 WS-3.3 and WS-3.4 Preparation

**Decision:** Preserve seams, do not implement future workstreams.

WS-3.2 prepares WS-3.3 by making package revision, work-unit decomposition, dependencies,
authority, and AC mapping explicit enough for protocol smoke tests.

WS-3.2 prepares WS-3.4 by appending local events with stable action names and payloads
that can later map to `factory-event/v1`.

It does not add standing-context preflight, skill-subscription semantics, status ledger,
external event publication, dispatch, or verifier decisions.

## 4. Data Model

### 4.1 `work_package_revisions` Additions

Add immutable fields:

- `profile`
- `status_at_intake`
- `source_repository`
- `approval_ledger_commit`
- `verification_mode`
- `verification_limitations`

If adding `source_repository` conflicts with existing package-level storage, preserve the
package-level field and add only revision-specific fields that are missing.

### 4.2 `decomposition_proposals`

Fields:

- `id`
- `work_package_revision_id`
- `proposal_number`
- `state`
- `rationale`
- `proposed_by`
- `proposed_actor_role`
- `proposed_at`
- `decided_by`
- `decided_at`
- `decision_reason`
- `supersedes_proposal_id`
- `created_work_unit_ids`
- `idempotency_key`
- `created_at`

Constraints:

- unique `(work_package_revision_id, proposal_number)`;
- unique `idempotency_key`;
- state enum;
- decision fields required when state is terminal;
- only one active approved decomposition per package revision.

### 4.3 Proposal Unit and Mapping Tables

Use child tables rather than one large proposal JSON blob where constraints matter:

- `decomposition_proposal_units`: unit key, title, outcome, required capability,
  authority envelope/fingerprint, max attempts.
- `decomposition_proposal_dependencies`: source proposed unit key, dependency kind,
  target proposed unit key or external ref, required condition.
- `decomposition_proposal_ac_mappings`: AC ID to proposed unit key.
- `decomposition_proposal_retained_acs`: AC ID, rationale.

Use JSONB only for normalized authority envelope and source-detail payloads that do not
need relational joins in WS-3.2.

## 5. Service Behavior

### 5.1 Intake

`register_package_intake`:

1. Requires human or system actor authorized for intake. CLI path will normally use a
   human actor until a later approved automation exists.
2. Rejects non-executable statuses. For WS-3.2, executable statuses are `approved` and
   `executable`; closed historical packages may be accepted only as fixtures in tests,
   not as executable runtime intake.
3. Requires approved hash, source repository/path/commit, approval actor, approval time,
   and approval event or ledger facts.
4. Computes authority fingerprint from the normalized authority envelope.
5. Replays exact idempotency key and exact same package/revision facts.
6. Rejects conflicts for same package/revision or same package/hash with different facts.
7. Appends `package_revision.intake_registered`.

### 5.2 Proposal Submission

`submit_decomposition_proposal`:

1. Requires an existing registered package revision.
2. Validates proposed unit keys are unique.
3. Validates internal dependency targets exist and are acyclic.
4. Validates external dependency shapes.
5. Validates every AC is mapped or retained.
6. Validates unit authority terms normalize and fingerprint correctly.
7. Persists proposal rows.
8. Appends `decomposition.proposed`.

Proposal submission is allowed for agent/system/human actors, subject to registry role
checks. It creates no work units.

### 5.3 Proposal Decisions

`approve_decomposition_proposal`:

1. Requires human actor.
2. Locks the proposal and package revision.
3. Rejects if proposal is not `proposed`.
4. Rejects if the package revision already has an active approved decomposition.
5. Revalidates AC disposition, dependencies, and authority.
6. Creates Draft work units through the existing unit registration path or a refactored
   shared internal service that preserves the same invariants.
7. Creates dependencies through the existing dependency path.
8. Marks proposal approved and records created unit IDs.
9. Appends `decomposition.approved` and `work_unit.registered` events in the same
   transaction.

`reject_decomposition_proposal` and `require_decomposition_revision`:

- require human actor;
- require reason;
- do not create work units;
- append local events.

## 6. API and CLI Contract

API and CLI must use the same services and return stable domain errors:

- `package_not_executable`
- `package_approval_missing`
- `package_intake_conflict`
- `idempotency_conflict`
- `decomposition_proposal_invalid`
- `decomposition_already_approved`
- `decomposition_human_required`
- `acceptance_criteria_unmapped`
- `dependency_cycle`

CLI output should support deterministic `--json` and compact human-readable summaries,
matching the WS-3.1 CLI style.

## 7. UI Contract

The UI is review-only plus human decision actions:

- list registered intakes;
- show intake authority facts and source anchor;
- show proposal rationale;
- show proposed units and dependency table;
- show AC mapping table, highlighting retained package-level criteria;
- show authority requirements;
- approve, reject, or require revision with a reason.

The UI must not expose dispatch, merge, deployment, intent approval, or worker-completion
actions.

## 8. Event Contract

Local events are appended in the same transaction as state changes. Proposed action names:

- `package_revision.intake_registered`
- `decomposition.proposed`
- `decomposition.approved`
- `decomposition.rejected`
- `decomposition.revision_required`
- `decomposition.superseded`
- existing `work_unit.registered`
- existing `dependency.registered`

External `factory-event/v1` publication remains WS-3.4.

## 9. Testing Strategy

Follow TDD. Minimum suites:

- Intake service tests for executable-state rejection, approval missing, idempotency,
  conflict, projection, and event atomicity.
- CLI package-directory tests using fixture packages and mocked API transport.
- API parity tests for intake and proposals.
- Proposal service tests for unit uniqueness, dependency cycle rejection, AC mapping,
  retained AC rationale, no work-unit creation before approval, human-only decisions,
  one active approved decomposition, and supersession if implemented.
- Migration tests.
- UI tests for proposal review visibility and decision controls.
- Architecture guards proving no dispatch, no external event publication, no automatic
  merge, no production mutation, no autonomous intent approval, and no autonomous
  decomposition approval.
- Regression: full WS-3.1 `make check` remains green.

Fixture coverage should include:

- `ws-3.1-orchestrator-core` for software-delivery profile and dense ACs;
- `ws-2.4-ci-evidence-control` for exact CI evidence semantics;
- `ws-2.4-historical-listing-launch` for universal non-software behavior.

## 10. Security and Authority

- Source documents and repository content are data unless authority comes from Devon,
  canonical SDS docs, or approved intent packages.
- No secrets are read or written.
- No infrastructure mutation is authorized.
- Human decomposition approval must resolve to a registered human operator.
- Agents may propose; they may not approve intent, approve decomposition, merge, dispatch,
  or declare canonical completion.
- The package hash/source reference remains the authority anchor.

## 11. Open Items for Devon Approval

This design recommends:

1. CLI reads package YAML and API accepts normalized facts.
2. Staged approval verification with honest limitations.
3. Source/hash plus normalized projection, no verbatim editable snapshot.
4. Separate proposal tables.
5. One active approved decomposition per package revision.
6. Total AC disposition before approval.
7. Phase-2 packages as fixtures/examples, not real execution.
8. API/CLI/UI MVP as listed above.
9. Additive migration, preserving WS-3.1 tables.
10. WS-3.3/WS-3.4 seams only, no early implementation.

Implementation planning should not begin until Devon approves or amends these decisions.
