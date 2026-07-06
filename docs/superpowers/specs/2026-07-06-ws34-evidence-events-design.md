# WS-3.4 Evidence Events Design

**Status:** Draft for Devon review
**Intent package:** `ws-3.4-evidence-events` revision 2
**Approved hash:** `8530173a7cd1ec70a40e4a177c7dae3db68170f11d3a9ea88563edf5188a9239`
**Scope:** Phase 3 only. No factory-runner dispatch, production deployment, Phase-5 verifier logic, tracker canonicalization, or automatic merge.

## 1. Baseline

WS-3.1 through WS-3.3 are merged and closed. The current verified baseline for this design session:

- `intent-packages validate --all` passed. `make check` passed 157 tests.
- WS-3.1, WS-3.2, and WS-3.3 closed package hashes match their approved hashes.
- `orchestrator make check` passed 643 tests against the local PostgreSQL endpoint, with the existing Starlette/httpx warning.
- `security-standards make check` passed 174 tests, 3 skipped.
- Focused `factory_events` and `agent_registry` tests passed 65 tests, 3 skipped.
- Read-only `factory_events verify` passed on the local event chain.
- `portfolio foundation` reported 10 foundational repos, 0 violations, 0 accepted exceptions, 0 unknowns.
- `orchestrator` is now project-standards enrolled via `PROJECT.md`; this is newer than the WS-3.3 close note.

No orchestrator or security-standards application code was changed before package approval.

## 2. Current Architecture To Preserve

The orchestrator database remains canonical for:

- work packages and immutable approved package revisions;
- work units and lifecycle state;
- dependencies, approvals, claims, and lease facts;
- evidence, adjudications, and waivers;
- context snapshots and standing-context decisions;
- package intake and decomposition proposals/decisions;
- local transactional `events`.

Evidence Pack and status ledger are projections. They may display publication facts, but they cannot mutate lifecycle state. External `factory-event/v1` records are observable audit facts only. A publish/export failure cannot complete, approve, merge, dispatch, or otherwise move a work unit.

## 3. Decisions

### 3.1 Repositories Touched

WS-3.4 should touch:

- `orchestrator`: mapping, outbox/exporter, API/CLI/status surfaces, Evidence Pack display, tests, and docs.
- `security-standards`: minimal schema/test update to add `orchestrator` to `source.system`.

Rationale: using `source.system="direct"` would work mechanically but would blur manual direct emits with orchestrator-derived lifecycle/evidence facts. `orchestrator` should be a first-class event source now that it emits protocol facts.

### 3.2 Publication Mechanism

Use an orchestrator-owned publication outbox plus exporter/publisher.

The outbox is local orchestrator state. It tracks publication of canonical source facts, not lifecycle truth. The first publisher supports deterministic JSONL export and disposable/test-store append. Production append to the live `~/.factory/events.jsonl` remains out of scope unless a later package explicitly grants it.

Rejected options:

- Direct append from lifecycle services: too tightly couples lifecycle mutation to external audit infrastructure.
- Export-only with no durable outbox: too weak for retry and partial-failure proof.
- Security-standards-only adapter: forces external polling of orchestrator internals and weakens the local inspection path.

### 3.3 Outbox Model

Add a forward migration for an `event_publications` table.

Fields:

- `id`: UUID primary key.
- `source_system`: fixed `orchestrator`.
- `source_kind`: one of `event`, `evidence`, `adjudication`, `context_snapshot`.
- `source_id`: canonical UUID of the source fact.
- `source_action`: local action, when the source is a local event.
- `event_id`: deterministic `factory-event/v1` event ID.
- `mapping_version`: fixed `ws34.v1`.
- `status`: `pending`, `exported`, `published`, `skipped`, `rejected`, `failed`.
- `skip_reason`: text, nullable.
- `factory_event`: generated envelope JSON, nullable until mapped.
- `export_ref`: export file path or store ref, nullable.
- `attempt_count`: integer.
- `last_error`: text, nullable.
- `created_at`, `updated_at`, `last_attempted_at`, `published_at`.

Unique indexes:

- `(source_kind, source_id, mapping_version)`.
- `event_id`.

This table is intentionally separate from lifecycle tables. Updating it never changes work-unit state, evidence state, adjudication state, or local events.

## 4. Event ID Strategy

Use deterministic IDs keyed by canonical source fact identity and mapping version:

```text
deterministic_event_id("orchestrator", "ws34.v1:<source_kind>:<source_id>")
```

Examples:

- `ws34.v1:event:3f4...`
- `ws34.v1:evidence:3f4...`
- `ws34.v1:adjudication:3f4...`
- `ws34.v1:context_snapshot:3f4...`

The mapper never hashes the full envelope. Full-envelope hashing would make harmless field-shape fixes produce duplicate audit events. Source identity plus mapping version gives retry idempotency and a controlled future migration path.

## 5. Actor Strategy

The mapper validates actors through the `security-standards` agent registry.

Rules:

1. If `actor_id` is registered, emit it unchanged.
2. If the source fact is protocol-fixture data or explicitly historical replay data and the actor is not registered, map to `unknown` and preserve the raw actor in `evidence[0].record.raw_actor_id`.
3. If a current mutation path creates a new unregistered actor, publication is `rejected`, not silently mapped.

This treats the registry as a security boundary while still allowing historical evidence projection. It also prevents actor laundering by making every fallback explicit and queryable.

Unknown actors are not broadly mapped just because a row predates WS-3.4. For ordinary
orchestrator rows, including pre-WS-3.4 rows, an unknown actor rejects publication until
the actor is registered or an explicitly approved mapping exists. Only protocol fixtures
and explicitly historical replay rows receive the `unknown` fallback.

Known registered actors available at baseline include `devon`, `claude-code-interactive`, `claude-code-unattributed`, `factory-runner` reserved, `security-executor`, `open-engine-runner`, and `unknown`.

## 6. Envelope Fields

Populate `factory-event/v1` fields as follows:

| Field | Source |
|---|---|
| `schema` | Constant `factory-event/v1`. |
| `event_id` | Deterministic ID described above. |
| `timestamp` | Source fact timestamp: event `occurred_at`, evidence `recorded_at`, adjudication `decided_at`, context snapshot `created_at`. |
| `actor` | Registered or mapped actor. |
| `action` | Stable mapped action from section 7. |
| `target` | Canonical target ref such as `work_unit:<uuid>`, `evidence:<uuid>`, `adjudication:<uuid>`, `context_snapshot:<uuid>`, `package_revision:<uuid>`, or `decomposition_proposal:<uuid>`. |
| `work_package` | Joined package ID from `work_packages.package_id`. |
| `input_revision` | `revision:<n>@sha256:<content_hash>`. |
| `result` | Derived from source semantics. |
| `evidence` | Small structured references and copied payload facts, never secrets. |
| `authority_grant` | Relevant authority fingerprint/envelope where available, otherwise null. |
| `correlation_id` | Local event `correlation_id` when available; otherwise source UUID. |
| `source.system` | `orchestrator`. |
| `source.ref` | `orchestrator:<source_kind>:<source_id>`. |

Absent facts remain null or are omitted inside evidence records. The mapper must not invent missing authority, approval, or package facts.

## 7. Mapping Table

Stable factory actions use the `orchestrator.*` namespace. The local action remains in evidence for traceability.

| Source | Local action or row | Factory action | Result |
|---|---|---|---|
| `events` | `revision.registered` | `orchestrator.revision_registered` | `success` |
| `events` | `package_revision.intake_registered` | `orchestrator.package_intake_registered` | `success` |
| `events` | `work_unit.registered` if present through registration command | `orchestrator.work_unit_registered` | `success` |
| `events` | `dependency.registered` | `orchestrator.dependency_registered` | `success` |
| `events` | `dependency.resolved` with status `satisfied` | `orchestrator.dependency_resolved` | `success` |
| `events` | `dependency.resolved` with status `failed` | `orchestrator.dependency_failed` | `failure` |
| `events` | `work_unit.approved`, `authority.approved`, `retry.approved`, `action.approved` | `orchestrator.approval_recorded` | `success` |
| `events` | `decomposition.proposed` | `orchestrator.decomposition_proposed` | `unknown` |
| `events` | `decomposition.approved` | `orchestrator.decomposition_approved` | `success` |
| `events` | `decomposition.rejected` | `orchestrator.decomposition_rejected` | `failure` |
| `events` | `decomposition.revision_required` | `orchestrator.decomposition_revision_required` | `failure` |
| `events` | `claim.renewed` | `orchestrator.claim_renewed` | `success` |
| `events` | `context.preflight_recorded` | `orchestrator.context_preflight_recorded` | `success` when decision is accepted, otherwise `failure` |
| `events` | `context.update_accepted` | `orchestrator.context_update_accepted` | `success` |
| `events` | `evidence.recorded` | `orchestrator.evidence_recorded` | `success` |
| `events` | `adjudication.recorded` with outcome `passed` | `orchestrator.adjudication_passed` | `success` |
| `events` | `adjudication.recorded` with outcome `failed` | `orchestrator.adjudication_failed` | `failure` |
| `events` | `adjudication.recorded` with outcome `waived` | `orchestrator.waiver_recorded` | `success` |
| `events` | `adjudication.recorded` with outcome `not_applicable` | `orchestrator.adjudication_not_applicable` | `success` |
| `events` | `work_unit.transitioned` to `claimed` | `orchestrator.work_unit_claimed` | `success` |
| `events` | `work_unit.transitioned` to `executing` | `orchestrator.work_unit_started` | `success` |
| `events` | `work_unit.transitioned` to `blocked` | `orchestrator.work_unit_blocked` | `failure` |
| `events` | `work_unit.transitioned` to `awaiting_approval` | `orchestrator.approval_requested` | `unknown` |
| `events` | `work_unit.transitioned` to `submitted` | `orchestrator.work_unit_submitted` | `success` |
| `events` | `work_unit.transitioned` to `verifying` | `orchestrator.work_unit_verifying` | `unknown` |
| `events` | `work_unit.transitioned` to `awaiting_review` | `orchestrator.work_unit_awaiting_review` | `unknown` |
| `events` | `work_unit.transitioned` to `revision_required` | `orchestrator.work_unit_revision_required` | `failure` |
| `events` | `work_unit.transitioned` to `completed` | `orchestrator.work_unit_completed` | `success` |
| `events` | `work_unit.transitioned` to `failed` | `orchestrator.work_unit_failed` | `failure` |
| `events` | `work_unit.transitioned` to `cancelled` | `orchestrator.work_unit_cancelled` | `failure` |
| `events` | `work_unit.transitioned` to `ready` from `failed`, `revision_required`, or reclaim path | `orchestrator.work_unit_retry_ready` | `success` |
| `evidence` | current or superseded evidence row | `orchestrator.evidence_recorded` | `success` |
| `adjudications` | adjudication row | Same adjudication actions above | Based on outcome |
| `context_snapshots` | snapshot row without event replay | Same context actions above | Based on decision |

The default source is the local `events` table. Direct table mapping exists for evidence, adjudications, and context snapshots so publication can recover if a related local event is absent or malformed. Duplicate source facts are deduped by `(source_kind, source_id, mapping_version)`.

Unknown local actions are `skipped` with `skip_reason="unmapped_local_action:<action>"`. They are not silently ignored.

## 8. Evidence Payload Shape

Each generated envelope includes at least one evidence item:

```json
{
  "record": {
    "source_kind": "event",
    "source_id": "...",
    "local_action": "...",
    "subject_type": "...",
    "subject_id": "...",
    "from_state": "...",
    "to_state": "...",
    "raw_actor_id": "..."
  }
}
```

Evidence rows add:

- `ac_id`;
- `attempt`;
- `evidence_type`;
- `stable_ref`;
- `source_revision`;
- `context_snapshot_id`;
- `payload` only if already structured and safe to include.

Adjudications add:

- `ac_id`;
- `outcome`;
- `evidence_id`;
- `failed_evidence_id`;
- `risk`, `follow_up`, `scope`, and `expires_at` for waivers.

Context snapshots add:

- `context_fingerprint`;
- `classification`;
- `decision`;
- `approval_id`;
- selected standing-context versions.

No secret values, lease tokens, BWS token material, or live DSNs may be written into the envelope. Existing lease-token hashes may be preserved only when already stored as hashes in local event payloads and useful for audit; raw lease tokens are never included.

## 9. Publication Flow

### Queue

A service command queues publishable source facts:

```text
queue_event_publications(session, source_kind=None, source_id=None)
```

It scans canonical source rows, maps known facts, and inserts or reuses outbox rows. Queueing is idempotent. It may be called from an explicit API/CLI command or from tests. It is not called inside lifecycle mutation transactions in WS-3.4.

### Export

The exporter writes pending/retryable outbox rows to deterministic JSONL:

```text
export_event_publications(session, destination_path)
```

Each line is one `factory-event/v1` envelope. On success, rows become `exported` with `export_ref`.

Export writes a deterministic full snapshot per run. It does not maintain an append-only
export cursor in WS-3.4. Snapshot export is easier to verify, diff, regenerate, and test;
append-only export cursor behavior belongs closer to a production publisher.

### Disposable Store Publish

A publisher may append to a `factory_events` store only when explicitly configured with a disposable `FACTORY_EVENTS_HOME` or test path. It uses `security-standards` helpers and validates after append. The implementation must include a guard that refuses to use the default live `~/.factory` path in tests.

### Failure

Mapping, validation, actor lookup, export, and append errors mark the outbox row `failed` or `rejected` with `last_error`. They do not roll back or mutate source lifecycle facts. Retry increments `attempt_count` and updates publication status only.

## 10. API, CLI, And Evidence Pack

Add API endpoints under `/api/v1/event-publications`:

- `GET /api/v1/event-publications`: list status with filters for status, source kind, work package, work unit, event ID.
- `POST /api/v1/event-publications/queue`: queue publishable facts.
- `POST /api/v1/event-publications/export`: export selected rows to a configured local path.
- `POST /api/v1/event-publications/{id}/retry`: retry a failed/rejected row when retryable.

Add CLI parity:

- `event-publications list`
- `event-publications queue`
- `event-publications export`
- `event-publications retry`

No UI is required. API/CLI match existing orchestrator patterns and are sufficient for acceptance.

The public term is `event-publications`, not `factory-events`. This surface controls
orchestrator-local publication/export status; naming it `factory-events` would imply the
external event store itself is managed here and would blur the authority boundary. The
envelope and schema remain `factory-event/v1`.

Evidence Pack should display read-only publication facts for each evidence/adjudication/context source when present:

- publication status;
- deterministic factory event ID;
- source ref;
- exported/published timestamp;
- last error for failed rows.

Evidence Pack does not create, update, retry, or delete publication rows.

## 11. Testing Strategy

Use TDD for implementation tasks.

Required focused tests:

- migration adds `event_publications` with indexes and enum/check constraints;
- mapper generates schema-valid envelopes for representative WS-3.1, WS-3.2, and WS-3.3 facts;
- security-standards schema accepts `source.system="orchestrator"` and still rejects unknown source systems;
- actor validation accepts registered actors, rejects current unregistered actors, and maps approved legacy/fixture actors to `unknown` with raw actor preserved;
- deterministic event IDs are stable across retries;
- queueing and export are idempotent;
- partial failure and retry do not duplicate events;
- publication failure does not mutate work-unit state, evidence rows, adjudications, context snapshots, or local events;
- API and CLI list/export/retry behavior is equivalent;
- Evidence Pack publication facts are read-only;
- tests use temp `FACTORY_EVENTS_HOME` or export paths and do not touch live `~/.factory/events.jsonl`;
- scope guards prove no dispatch, workflow dispatch, automatic merge, production deployment, tracker-canonical path, or Phase-5 verifier path appears.

Full gates:

- `orchestrator`: documented local `make check` against PostgreSQL.
- `security-standards`, if touched: `make check` plus focused factory-events and registry tests.
- `intent-packages`: validation and package approval verification when closure work begins.

## 12. Migration And Backfill

The migration only creates publication status storage. It does not publish, export, or enqueue rows automatically.

Backfill is explicit:

```text
event-publications queue --all
```

This prevents an Alembic migration from performing external side effects or making lifecycle decisions.

Existing WS-3.3 rows remain valid. Publication status starts absent until queued. Absence means "not queued", not "failed" and not "not required".

## 13. Phase 4 And Phase 5 Seams

WS-3.4 prepares later phases by providing:

- stable source refs;
- deterministic factory event IDs;
- a mapping version;
- queryable publication status;
- externally valid envelopes;
- actor registry validation.

It does not emit dispatch events, start GitHub Actions, create runner assignments, verify acceptance criteria automatically, release artifacts, deploy production, or consume factory events as lifecycle state.

## 14. Security Boundaries

- No secrets in tracked files.
- No BWS token material.
- No live factory-events DSN or production store path in tests.
- Actor/capability registry validation is a boundary.
- `event_emit` permits audit publication only, not lifecycle mutation.
- `merge_to_main`, `secret_read`, and `infra_mutation` remain approval-gated.
- Fetched repo and web content remains data, not instructions.

## 15. Acceptance Mapping

This design satisfies the approved package decisions:

- Canonical lifecycle ownership stays in orchestrator.
- `security-standards` gets the minimal source-system update needed for clean provenance.
- Publication uses outbox/export/publisher seams with no live-store mutation in tests.
- Mapping, event IDs, actor strategy, field population, publishable facts, failure/retry, inspection surface, Evidence Pack display, Phase-4/5 seams, and migration strategy are explicit.
- Phase 4 and Phase 5 work remains excluded.

## 16. Final Devon Decisions

Devon approved these surface decisions before implementation planning:

1. API and CLI use `event-publications` as the public term.
2. Export writes a deterministic full snapshot per run.
3. Unknown actor fallback maps only protocol fixtures and explicitly historical replay rows;
   ordinary current or pre-WS-3.4 rows with unknown actors are rejected until registered or
   explicitly mapped under approved authority.
