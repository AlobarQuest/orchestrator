# WS-3.4 Adversarial Architecture Review

**Design reviewed:** `docs/superpowers/specs/2026-07-06-ws34-evidence-events-design.md`
**Intent package:** `ws-3.4-evidence-events` revision 2
**Approved hash:** `8530173a7cd1ec70a40e4a177c7dae3db68170f11d3a9ea88563edf5188a9239`
**Review status:** Blocking issues resolved in the design before implementation planning.

## Review Method

This review challenged the design against the approved WS-3.4 boundary:

- external `factory-event/v1` records must not become lifecycle authority;
- publication failure must fail closed without mutating lifecycle state;
- event IDs must be idempotent by canonical source fact;
- actor registry validation must not launder unknown identities;
- tests must not touch the live factory-events store;
- no Phase 4 dispatch, Phase 5 verifier logic, production deployment, tracker canonicalization, or automatic merge may enter the design.

## Findings

### F1 - Blocking resolved: source provenance cannot hide under `direct`

**Risk:** Reusing `source.system="direct"` would satisfy the current schema but would make orchestrator-derived facts indistinguishable from manual direct emits unless every consumer parsed `source.ref` conventions. That would create an audit ambiguity at exactly the boundary WS-3.4 is meant to clarify.

**Resolution:** The design requires a minimal `security-standards` update adding `orchestrator` as an allowed `source.system`, with schema tests. Orchestrator mappings then emit `source.system="orchestrator"` and stable `source.ref="orchestrator:<source_kind>:<source_id>"`.

**Residual risk:** Any downstream consumer hard-coding the previous enum may need an update. This is acceptable because `factory-event/v1` is owned by `security-standards`, and the schema/test change makes the new source explicit.

### F2 - Blocking resolved: publication must not occur inside lifecycle transactions

**Risk:** If lifecycle services append to the external store during state transitions, event-store failure could block or roll back canonical lifecycle changes, and success could be mistaken as part of lifecycle authority.

**Resolution:** The design uses an explicit outbox queue command. Lifecycle transactions continue writing only canonical orchestrator rows and local events. Queue/export/publish updates only `event_publications`.

**Residual risk:** Operators must remember to queue/export. This is acceptable for WS-3.4 because the goal is wired evidence publication semantics, not production automation.

### F3 - Blocking resolved: broad legacy actor fallback would launder identity

**Risk:** Mapping every unknown pre-WS-3.4 actor to `unknown` would hide real registry drift and could normalize bad current actor IDs merely because rows were old.

**Resolution:** Unknown actor fallback is limited to protocol fixtures and explicitly historical replay rows. Ordinary current or pre-WS-3.4 rows reject publication until the actor is registered or explicitly mapped under approved authority. Raw actor IDs are preserved when fallback is allowed.

**Residual risk:** Some older real rows may initially reject publication. That is useful drift evidence, not a failure.

### F4 - Important resolved: deterministic IDs must not include mutable envelope shape

**Risk:** Hashing full envelopes would produce different event IDs when non-authoritative formatting or evidence-shape details change, causing duplicates for the same source fact.

**Resolution:** Event IDs are deterministic by `("orchestrator", "ws34.v1:<source_kind>:<source_id>")`. Mapping version gives a controlled migration seam if a future package intentionally changes semantics.

**Residual risk:** A future mapping version can create a second event for the same source fact. That must be explicitly approved as a new semantic projection.

### F5 - Important resolved: export behavior must not smuggle in production cursor semantics

**Risk:** Append-only export would require cursor/watermark semantics, partial-file recovery, duplicate-line handling, and operator decisions that are closer to a production publisher than a Phase-3 proof.

**Resolution:** WS-3.4 export writes deterministic full snapshots. This keeps tests simple, diffable, and repeatable while preserving event-ID idempotency.

**Residual risk:** Large exports may become inefficient later. That is a Phase-4/5 operational concern, not a WS-3.4 blocker.

### F6 - Important resolved: outbox status must not be confused with evidence status

**Risk:** Displaying publication status in Evidence Pack could make readers treat `published` as "accepted" or "verified".

**Resolution:** Evidence Pack displays publication facts as read-only audit projection: status, factory event ID, source ref, timestamp, and last error. It does not expose queue/export/retry actions and does not affect adjudication outcome.

**Residual risk:** Copy/UI labels must stay precise. The implementation plan should include tests that failed publication does not alter adjudication or work-unit state.

### F7 - Important resolved: test isolation must be structural, not convention

**Risk:** Tests that import `factory_events.store` could accidentally append to live `~/.factory/events.jsonl` if `FACTORY_EVENTS_HOME` is not isolated.

**Resolution:** The design requires temp `FACTORY_EVENTS_HOME` or export paths and tests proving the default live path is not touched. The publisher refuses default `~/.factory` in tests or unconfigured local runs.

**Residual risk:** A human can still intentionally run a publisher against live config later. That requires a future approved package or explicit authority path.

### F8 - Moderate resolved: mapping coverage can rot as local event actions evolve

**Risk:** Future local event actions could be added without publication mapping, silently reducing audit coverage.

**Resolution:** Unknown local actions become `skipped` rows with explicit skip reasons, and mapping coverage tests assert known protocol-relevant actions are mapped or intentionally skipped.

**Residual risk:** Skipped rows may accumulate. That is visible operator work and can feed later package revisions.

### F9 - Moderate resolved: `published` versus `exported` must stay distinct

**Risk:** A JSONL export could be mistaken for live append-only store publication.

**Resolution:** The outbox status enum separates `exported` from `published`. Snapshot export sets `exported`; disposable store append sets `published`. Live production publication remains out of scope.

**Residual risk:** External docs and CLI output must preserve this distinction.

## Scope Guard Check

The design does not introduce:

- factory-runner dispatch;
- GitHub Actions `workflow_dispatch`;
- production deployment or Coolify mutation;
- Phase-5 independent verifier logic;
- tracker-canonical state;
- automatic merge;
- worker-controlled completion;
- brain learning or promotion.

## Verdict

Proceed to implementation planning. The design is mechanically sound for WS-3.4 if the plan keeps tasks sliced around:

1. minimal `security-standards` source-system update;
2. orchestrator outbox schema;
3. pure mapper and actor validation;
4. queue/export/retry services;
5. API/CLI/read-only Evidence Pack surfaces;
6. isolation and scope-guard tests.
