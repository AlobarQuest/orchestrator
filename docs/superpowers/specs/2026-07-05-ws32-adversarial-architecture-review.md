# WS-3.2 Adversarial Architecture Review

**Date:** 2026-07-05  
**Design reviewed:** `docs/superpowers/specs/2026-07-05-ws32-package-intake-decomposition-design.md`  
**Intent package:** `ws-3.2-package-intake-decomposition`, revision 1  
**Approved hash:** `84c929bc0860b6a585a62ec02fa35d9cdf89fce84773660aea1e383d955689df`

## Verdict

Approved to proceed to implementation planning after the design clarification committed
with this review. No blocking issue remains.

The review found two blocking issues in the first proposed design and both are resolved:
direct WS-3.1 unit registration would have bypassed proposal approval for intaken
revisions, and approved-decomposition state could not safely live on append-only
`work_package_revisions`.

The other main risk was overstating what WS-3.2 can verify at API time. The design now
states that the supported executable intake path is CLI source verification followed by
API registration, while the API enforces submitted facts, idempotency, conflicts,
approved package status, and recorded verification limitations.

## Findings

### F1 — Important: API intake could be misread as independent package verification

**Risk:** The design originally said the API accepts normalized package facts. Read
literally, that could imply the API itself proves the package YAML, git source commit,
approval ledger, and hash chain. It does not. If the implementation relied on caller
assertions while claiming full verification, an authenticated caller could submit false
facts and the orchestrator would appear to have mechanically prevented unapproved intake
when it had only enforced provided fields.

**Resolution:** The design now has an explicit trust-boundary paragraph in section 3.1
and adds a verification-mode rejection rule in section 3.2. WS-3.2's supported operator
path is CLI source verification. The API records `verification_mode =
caller_attested_cli_verified` and `verification_limitations`, rejects unsupported modes,
and restricts executable intake registration to registered human operators until a
separately approved system producer exists.

**Implementation requirement:** Tests must prove unsupported or absent verification mode
is rejected. Documentation and response payloads must not claim stronger verification
than `caller_attested_cli_verified`.

### F2 — Important: Closed package fixtures could accidentally weaken executable intake

**Risk:** The spec wants the two closed Phase-2 packages and closed WS-3.1 package as
fixtures/examples. If implementation lets closed packages through the normal executable
intake path to make those fixtures convenient, it would contradict WS-3.2's rejection of
non-executable revisions.

**Resolution:** The design now states that closed historical packages used in tests must
go through fixture helpers that do not call the executable-intake service path, except
when specifically testing closed-package rejection.

**Implementation requirement:** There must be at least one test that a closed package is
rejected by executable intake.

### F3 — Important: Append-only revision rows complicate additive migrations

**Risk:** `work_package_revisions` is append-only in WS-3.1. Adding new nullable columns
is safe; adding non-null columns without defaults or trying to update existing rows is
not. The design's "extend work_package_revisions" direction must not break existing
rows or append-only triggers.

**Resolution:** Implementation planning must use nullable or server-defaulted additions,
or put new intake-only facts in a separate table keyed to `work_package_revisions`.

**Implementation requirement:** Migration tests must upgrade from the existing WS-3.1
schema and must not require updating old revision rows.

### F4 — Blocking resolved: direct WS-3.1 unit registration bypasses decomposition proposals

**Risk:** WS-3.1 exposes `POST /api/v1/revisions/{revision_id}/work-units`, backed by
`register_approved_unit`. That route stamps decomposition approval fields directly and
does not check proposal state, AC disposition, retained AC rationale, or one-active
decomposition. Leaving it unrestricted would let callers create Draft units for a
WS-3.2-intaken package revision without approving a decomposition proposal.

**Resolution:** The design now states that direct unit registration remains only for
legacy/manual WS-3.1 registrations. Revisions registered through WS-3.2 executable intake
must reject direct unit creation; proposal approval is the only Draft-unit creation path
for those revisions.

**Implementation requirement:** Add an `intake_source` or equivalent revision fact.
Tests must prove direct unit registration is rejected for WS-3.2-intaken revisions while
existing WS-3.1 tests remain supported for legacy/manual revisions.

### F5 — Blocking resolved: approved-decomposition marker cannot live on append-only revisions

**Risk:** The design originally allowed the one-active-decomposition marker to live on
`work_package_revisions`. That table is append-only in WS-3.1, and decomposition approval
happens after intake. Updating the revision row would violate existing persistence
invariants.

**Resolution:** The design now requires a separate `approved_decompositions` table with
a partial unique index enforcing one active approved decomposition per package revision.

**Implementation requirement:** Do not update `work_package_revisions` during proposal
approval. Migration and persistence tests must preserve append-only behavior.

### F6 — Important: package source repository must not become dual source of truth

**Risk:** WS-3.1 stores `source_repository` on `work_packages`. Duplicating
`source_repository` on `work_package_revisions` would create ambiguity over whether the
repository is package identity or revision source fact.

**Resolution:** The design now keeps `source_repository` on `work_packages` and uses
`source_path` plus `source_commit` as revision source facts under that repository.

**Implementation requirement:** Conflict tests must preserve the existing repository
identity rule and must not introduce a second repository field on revision rows.

### F7 — Important: AC disposition needs canonical projected AC rows

**Risk:** Proposal mapping tables need a canonical AC set to validate against. Keeping
ACs only inside `enforcement_snapshot` JSON would make total-disposition validation
fragile and easy to bypass.

**Resolution:** The design now adds immutable `package_acceptance_criteria` rows keyed
by package revision and AC ID. Mapping and retained-AC rows reference these projected ACs.

**Implementation requirement:** Approval must re-check total AC disposition under lock
against the immutable package AC rows.

### F8 — Important: Work-unit creation must not bypass WS-3.1 registration events

**Risk:** Proposal approval creates multiple Draft units and dependencies. A batch
implementation could insert `work_units` directly for convenience, bypassing
`register_approved_unit` behavior, idempotency, authority fingerprinting, constraints, or
events.

**Resolution:** The design requires using the existing unit-registration path or a shared
internal service that preserves the same invariants.

**Implementation requirement:** Tests must assert approved proposal activation produces
the same Draft state, decomposition approval facts, dependency rows, and event semantics
as existing unit registration.

### F9 — Moderate: Retained package-level ACs are easy to confuse with waivers

**Risk:** A retained package-level AC means "not assigned to a work unit yet." It does
not mean passed, failed, waived, or not applicable. If UI/API language uses "waived" or
"satisfied" for retained ACs, WS-2.4's silent-waiver problem can reappear.

**Resolution:** The design explicitly states retained package-level ACs are not waivers
and do not mean passed.

**Implementation requirement:** Schema/API/UI labels should use `retained_package_level`
or equivalent wording, not `waived`.

### F10 — Moderate: Proposal supersession must be human-only and rare

**Risk:** One active decomposition per package revision is correct, but supersession can
become a loophole if agents can replace approved decompositions.

**Resolution:** The design makes supersession a named human action with a reason.

**Implementation requirement:** Supersession, if implemented in WS-3.2, must require
human actor and leave original proposal, units, and events auditable. If replacement of
already-created work units is not implementable safely in WS-3.2, the endpoint should be
omitted and second approvals should be rejected.

## Scope Guard Confirmation

The reviewed design does not authorize:

- factory-runner dispatch;
- GitHub Actions worker execution;
- production deployment or Coolify mutation;
- Phase-5 verifier decisions;
- external `factory-event/v1` publication;
- standing-context preflight, skill-subscription semantics, or status ledger;
- tracker-canonical state;
- automatic merge;
- autonomous intent or decomposition approval.

## Required Plan Adjustments

The implementation plan must include:

1. A migration strategy that respects append-only `work_package_revisions`.
2. Verification-mode validation and limitations recording.
3. Closed-package rejection tests separate from closed-package fixtures.
4. Proposal approval through existing registration/dependency/event paths.
5. UI/API terminology that distinguishes retained ACs from waivers.
6. A deliberate decision to either implement human-only supersession safely or defer it.
7. Direct unit-registration rejection for WS-3.2-intaken revisions.
8. A separate `approved_decompositions` table.
9. Immutable package AC projection rows and FK-backed AC mapping/retention.
