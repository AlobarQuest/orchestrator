# WS-P2.8 — Follow-up Scheduling — Design

**Date:** 2026-07-28
**Status:** Approved by Devon (brainstorm, 2026-07-28). Ready for implementation planning.
**Repo:** `AlobarQuest/orchestrator` (plus one small additive step in `AlobarQuest/infraops-mcp-server`).
**Program:** Phase-2 Wave 2 (LEGIBLE), final workstream.
**Provenance:** D-MVP-4 (`2026-07-10-phase6-exit-checklist.md`) — "Follow-up scheduling (`follow_up` →
timed observation units)", the last unbuilt clause of the original Phase-6 scope.
**Handoff:** `~/docs/software-delivery-system/2026-07-27-wsp28-follow-up-scheduling-handoff-prompt.md`
(read its CORRECTIONS block first — six inherited premises were wrong, four of them materially).
**Deploy checklist:** GAP-5 in `~/docs/software-delivery-system/2026-07-28-wave12-gap-closure-workplan.md`.

## 1. Scope

> Packages with `follow_up` get timed work units. Deterministic due-computation, human-discharged,
> no new collectors, no scheduler inside the orchestrator.

The inherited scope sentence said "observation units" and the Phase-3 plan calls this "the sole
sanctioned observation→work bridge". **Under the chosen design it reads no observations at all** —
see §14.3. The planning docs have been corrected; this spec uses the accurate framing throughout.

## 2. Decisions taken in brainstorming (2026-07-28)

| # | Question | Decision |
|---|---|---|
| D1 | Where does the due time come from? | **The orchestrator computes it.** The package declares *whether* (`follow_up.required`); a bounded config constant decides *when*. No intent-packages schema change. |
| D2 | What is a follow-up unit, and how does it discharge? | Born **`AWAITING_REVIEW`** with one generated judgment-typed AC. A human adjudicates it and presses Complete in `/review`. |
| D3 | What bounds the minting pass? | The WS-5.3 `_post_deploy_work_unit` template: deterministic `uuid5`, self-minted envelope with no mutation authority, capability in `ORCHESTRATOR_ONLY_CAPABILITIES`. |
| D4 | What invokes it? | A **SYSTEM `/api` route**, called by a launcher — not an out-of-process runner, not a clause of the detect pass. |
| D5 | Terminal discipline for an undischarged unit? | **Surface only, no new lifecycle edge.** `dead_letter._stalled_approvals` already covers `awaiting_review`. |
| D6 | Anchor for the due computation? | All units of the revision terminal, **and at least one `COMPLETED`** — a fully-cancelled revision must not mint. |
| D7 | Recurrence? | **One follow-up per revision, forever.** Recurrence is WS-P2.10's, with the schema tightening. |

## 3. Architecture

Three parts. No thread, no loop, no scheduler anywhere in `src/`.

```
intake                    persist                     mint                      discharge
──────                    ───────                     ────                      ─────────
package.yaml              work_package_revisions      POST /api/v1/             /review/units/{id}
  follow_up: {…}   ──▶      .follow_up (JSONB)  ──▶   follow-ups/mint     ──▶     adjudicate AC
                                                       (SYSTEM)                   press Complete
                          due predicate (pure)        uuid5 → WorkUnit
                          all-terminal + ≥1 completed  AWAITING_REVIEW
                          + N days elapsed
```

The scheduling lives in the **data** (a computed due time), never in a timer.

## 4. Data model

One new column and one migration.

```python
# persistence/models.py :: WorkPackageRevision
follow_up: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
```

Migration `0020_wsp28_follow_up` (head is `0019_wsp27_tracker_recon`; the id is 20 chars, inside the
32-char `alembic_version.version_num` limit). Nullable with no server default — a NULL means "this
revision predates the column", which is exactly the forward-only semantics of §14.1, and is
distinguishable from `{"required": false, …}`.

**Why a column and not `enforcement_snapshot`.** `enforcement_snapshot` is what was *enforced* at
intake; `follow_up` is a scheduling input read by a query. A first-class column is queryable
(`follow_up->>'required' = 'true'`) without unpacking JSON in the hot path, and it keeps
`enforcement_snapshot`'s meaning intact — that field already has an open accuracy defect (the
Evidence Pack shows the live envelope rather than the snapshot) and should not accrete new duties.

### 4.1 Intake path

`package_sources.py::_intake_payload` currently enumerates the surviving fields (lines ~524-551) and
does not include `follow_up`. Add it as a top-level payload key. Then carry it through
`PackageIntakeRegistration` (api/schemas.py) → `PackageIntakeCommand` → `register_package_intake`.

Both the `/api` route and the `/review/intakes` form share that model and service (ADR-0006), so the
browser path gets it for free — no second validation path.

### 4.2 Validation

Mirror the intent-packages closed map (`intent_packages/schema.py:160-167`) exactly:

```
required      bool        mandatory
revisit_when  str | null  mandatory key
signals       list[str]   mandatory
owner         str | null  mandatory key
```

Unknown keys rejected. A payload whose `follow_up` is absent is accepted and stored NULL (older
emitters); a payload whose `follow_up` is present but malformed is a named `DomainError` at intake,
**not** a 500 — per the invariant that only `DomainError` and `APIAuthenticationError` have
registered handlers, so any stdlib exception escaping a route is an unhandled 500.

The four field names are a cross-boundary vocabulary. Register them in `VOCABULARY_REGISTRY`
(`tests/architecture/test_cross_boundary_vocabulary.py`) keyed to their source of truth,
`intent_packages/schema.py`'s `follow_up` `MapSpec`. This is a genuine cross-boundary mirror, so it
is **registered**, not marked `# not-a-vocabulary`.

## 5. The due predicate

A pure function. No I/O inside it; the caller supplies the facts and `now`.

A revision is follow-up-due when **all** of:

1. `revision.follow_up` is non-NULL and `follow_up["required"] is True`;
2. the revision has **at least one** work unit in `COMPLETED`;
3. **every** work unit of the revision is settled — `COMPLETED` or `CANCELLED`, and specifically
   **not `FAILED`** (see §5.1);
4. `now >= anchor + follow_up_due_after_days`, where `anchor = max(entered-settled)` across those
   units;
5. the revision has no existing follow-up unit.

Clause 2 is D6: a revision whose every unit was cancelled shipped nothing, and there is no outcome
to revisit.

### 5.1 `FAILED` blocks, and that consequence is owned here

`FAILED` is **not terminal**. `(FAILED, READY)` and `(FAILED, CANCELLED)` are both legal edges, so a
`FAILED` unit is an *unsettled resting state* — its disposition (retry or retire) has not been taken.
A revision with one lingering `FAILED` unit therefore does not mint, and that is the correct call: the
package's outcome is not yet knowable, so there is nothing to schedule a revisit of.

But the consequence must be stated, because it is silent and unbounded: **nothing in this system
surfaces a `FAILED` unit as needing disposition.** `dead_letter` reports `FAILED` units, but reporting
is not resolution, and a revision parked behind one will never mint — not late, never. The pass makes
this legible rather than fixing it:

- a **distinct** skipped reason, `unsettled_failed_unit`, never folded into `units_in_flight`. A
  reason code that says "still working" when the truth is "abandoned and nobody decided" is the kind
  of mislabel that costs a later session an afternoon;
- the skip carries the offending unit ids, so the operator's next action is obvious;
- a test plants a revision with one `FAILED` unit alongside completed ones and asserts
  `skipped: unsettled_failed_unit`, not a mint and not `units_in_flight`.

Resolving a stuck `FAILED` unit is the operator's existing job (`FAILED → READY` to retry, or
`FAILED → CANCELLED` to retire — the path GAP-3 exercised in production on 2026-07-28). WS-P2.8 does
not automate that disposition and must not appear to.

**Entered-terminal is read from the event ledger**, not from `work_units.updated_at`:

```sql
MAX(Event.occurred_at)
  WHERE Event.subject_type = 'work_unit'
    AND Event.subject_id  = <unit id>
    AND Event.to_state IN ('completed', 'cancelled')
```

This is the `slo_report._queue_age` pattern. A DB trigger (`set_work_unit_updated_at`, migration
0001) rewrites `updated_at` on *every* UPDATE, so it cannot be back-dated and any test that "ages" a
row by writing it is silently testing nothing. Tests exercise this by shrinking
`follow_up_due_after_days`, never by ageing rows.

### 5.2 The self-exclusion, which is not optional

The minted follow-up unit **is a unit of its own revision**. Without care:

- while it sits in `AWAITING_REVIEW`, clause 3 is false and the revision is not due (harmless);
- once a human completes it, clause 3 is true again and the revision looks due a second time.

So clause 5 short-circuits the whole evaluation, and it is **derived from the units themselves** —
any unit carrying `follow_up_review` means already-minted, whatever state it is in. That is one
mechanism, not two.

An earlier draft of this section also filtered those units out of clauses 2-4. That filter was
**unreachable**: whenever such a unit is present, clause 5 fires first and the filtered clauses never
run. It was mandated here, it shipped with a test that passed whether or not the filter existed, and
the review caught it (2026-07-28). Untestable protection is a defect in this system, not depth —
the same judgement WS-P2.15 applied when it deleted `is_expansion()`. The `uuid5` id remains the
structural backstop against an actual double-mint.

### 5.3 Configuration

```python
# config.py
follow_up_due_after_days: int = Field(default=30, ge=0, le=365)
```

A plain `int` with **no off value**, bounded at both ends. This follows
`dead_letter_stalled_approval_seconds` deliberately: its predecessor
(`dispatch_human_gate_age_out_seconds: int | None = None`) defaulted to `None`, and that `None` is
precisely why the age-out it configured sat unwired and invisible for an entire workstream. The cap
is the point — a large value silences the mechanism as effectively as `None` ever did. `0` merely
means "due as soon as the work is terminal", which is maximally on, and is what the production
demonstration (§16.2) uses so it needs no waiting.

## 6. The minted unit

```
id                         uuid5(NAMESPACE_URL, f"sds:follow-up:{revision_id}")
unit_key                   f"follow-up:{revision_id}"
work_package_revision_id   revision.id
state                      AWAITING_REVIEW
title                      f"Follow-up review: {revision title}"
outcome                    "Revisit: {revisit_when}\nSignals:\n- {signal}\n- {…}\nOwner: {owner}"
required_capability        "follow_up_review"
authority                  normalize_authority({
                             "capabilities": {"follow_up_review": "allowed"},
                             "budgets": {"max_attempts": 1}}).normalized()
authority_fingerprint      authority_fingerprint(that envelope)
authority_approval_id      NULL
decomposition_approved_by  <the SYSTEM actor's own actor_id>
decomposition_approved_at  now
max_attempts               1
```

Notes, each load-bearing:

- **`decomposition_approved_by` is self-attested.** The CHECK `ck_work_units_approved_beyond_draft`
  requires both approval columns non-NULL for any state other than `draft`. `_post_deploy_work_unit`
  solves this the same way; this is the established precedent for a system-minted unit that never
  had a decomposition.
- **The envelope is built through `normalize_authority(...)` and stored as `.normalized()`.** Storing
  a raw dict makes `normalized()` a non-fixed-point on re-read and the re-derived fingerprint
  disagrees with the minted one — the bug documented at `deployment_observations.py:243-247`. Do not
  pass an `unknown_fields` key.
- **`authority_approval_id` stays NULL by design.** The unit carries no mutation authority and is
  never claimed, so there is nothing for a human to approve. Readiness and admission are never
  consulted, because the unit is born past them.
- **The prose is carried verbatim** into `outcome`. That is the actual judgment the package author
  wrote; the factory schedules the prompt, the human answers the question (§14.2).
- `state` is assigned at construction, not reached by an edge — the same shape as
  `_post_deploy_work_unit` minting into `SUBMITTED`. The system is asserting "this needs human
  judgment", which is true by construction here (§7) and is not a success claim.

### 6.1 New capability

Add `follow_up_review` to `ORCHESTRATOR_ONLY_CAPABILITIES` (`capability_vocabulary.py:49`) —
**not** to `CAPABILITY_VOCABULARY["runner"]`, which is byte-pinned across this repo and
`factory-runner` via `tests/fixtures/runner_authority_envelope.json` and `CONTRACT_SHA256`. Adding to
the orchestrator-only set touches no cross-repo contract, which is exactly why that set exists.

### 6.2 Events

One `Event` per minted unit, `action="follow_up_unit.created"`, in the same transaction as the
`WorkUnit` insert. The pass commits once at the end; the mint helpers flush.

## 7. Required criteria, and the adjudication carve-out

`load_required_criteria` (`services/verifier_criteria.py:15`) resolves in order: generated
post-deploy criteria → the approved decomposition's AC mapping for this `unit_key` → the revision's
full `enforcement_snapshot["acceptance_criteria"]`.

A follow-up unit has no decomposition mapping, so **without a new branch it would fall through to
every AC in the original package** and the human would be re-adjudicating the entire package. So add
a `_generated_follow_up_criteria` branch, parallel to `_generated_post_deploy_criteria`, returning
exactly one in-memory criterion:

```
ac_id         "follow-up-review"
condition     "The follow-up questions declared by the package were answered."
evidence_type "observation"
evidence      the revisit_when prose, or the default sentence below when null
approver      the declared follow_up.owner, or revision.approved_by when null
```

`revision.approved_by` is verified to exist and to be a safe fallback: `WorkPackageRevision.approved_by`
is a NOT NULL `String` (`persistence/models.py:159`) additionally covered by the CHECK
`ck_work_package_revisions_required_text`, which requires `approved_by <> ''`. So the fallback can
never itself be empty.

**Every nullable field needs a fallback.** `revisit_when` and `owner` are both `str | null` in the
schema, and `signals` may be empty — so `{"required": true, "revisit_when": null, "signals": [],
"owner": null}` is a *valid* declaration that would otherwise produce an empty `outcome` and an empty
criterion `evidence`. That matters twice: `PackageAcceptanceCriterion`'s CHECK requires `condition`,
`evidence` and `approver` all non-empty (these criteria are in-memory, like the post-deploy ones, so
the CHECK does not fire — but the values must still be legible to a human), and an empty `outcome`
gives the reviewer nothing to act on. Fallbacks:

- `revisit_when` null → `"No revisit condition was declared; confirm whether this outcome still holds."`
- `signals` empty → omit the Signals block entirely rather than render an empty heading
- `owner` null → `revision.approved_by`

A test covers the fully-degenerate declaration explicitly.

`observation` is already in `JUDGMENT_TYPES` and already accepted by the intake vocabulary gate
(`SUPPORTED_CRITERION_EVIDENCE_TYPES`). No vocabulary migration, no dangling new type. It routes to
`judgment_required` in `evaluate_criterion`, and — critically — `_authorize_outcome` keys the human
`passed` on the **static declared** `evidence_type` being in `JUDGMENT_TYPES`, which is what unlocks
the human adjudication.

### 7.1 The asymmetry, stated deliberately

`_validated_subject` (`services/evidence.py:554`) rejects an `ac_id` that is not in
`enforcement_snapshot["acceptance_criteria"]`, with one carve-out: generated **post-deploy** subjects
are allowed only when `allow_generated_post_deploy=True`, which only the verifier command passes.
That is the standing invariant *"generated post-deploy acceptance criteria are verifier-owned; public
adjudication must reject generated post-deploy AC IDs."*

**The follow-up AC needs the opposite.** It must be publicly adjudicable by a HUMAN through
`/review`, and must never be verifier-owned. So:

- add a `_is_generated_follow_up_subject` predicate and permit it through `_validated_subject`
  **without** any `allow_*` flag;
- define `FOLLOW_UP_AC_ID = "follow-up-review"` in **one** place, next to `POST_DEPLOY_AC_IDS`
  (`services/lifecycle.py:41`), whose comment already warns against a second copy;
- `web.py`'s `is_judgment` computation must include it so the `/review` form offers Passed /
  Not applicable, and `POST_DEPLOY_AC_IDS` filtering must not accidentally hide it.

A test asserts both directions explicitly: a HUMAN may adjudicate `follow-up-review` and may **not**
adjudicate a `post-deploy-*` id. The two carve-outs point opposite ways and a future reader will
assume they are the same; the test is what makes that assumption fail loudly.

## 8. The invoker

```
POST /api/v1/follow-ups/mint          SYSTEM only
```

A pure database read plus an append-only write. No outbound call, no loop, invoked on demand — the
ADR-0002 amendment shape, and the reason this is a route rather than a new runner package:
`reconciliation_runner/client.py:7-10` exists specifically to forbid a runner-species process from
minting units ("It would be setting canonical lifecycle state while calling itself a reporter"), and
`reconciliation_detection.py:8` states "It also never writes `work_units` and never transitions." A
new out-of-process runner would contradict the first; folding this into the detect pass would
contradict the second.

Because it lives in-process, **no `OUTBOUND_ALLOWLIST` entry is needed** — there is no new HTTP
client anywhere in `src/`.

Request carries `idempotency_key` for the pass itself (audit and replay of the pass). It has **no
single subject**, so `expected_version` carries no meaning here: follow the `ObservationCommandModel`
precedent and accept `None` or `0` only, rejecting anything else with a named `DomainError`. The
per-unit idempotency that actually matters is structural — the `uuid5` id — so re-running the pass
under a *fresh* key still mints nothing new. Task 1 of increment 3 verifies this against the
idempotency-matrix guard before the route is written, since a mismatch there is a CI-breaker rather
than a design change.

Response:

```json
{ "minted": [ {"work_unit_id": "…", "work_package_revision_id": "…", "due_at": "…"} ],
  "skipped": [ {"work_package_revision_id": "…", "reason": "not_yet_due"} ],
  "considered": 12 }
```

`skipped` reasons are a closed set: `not_yet_due`, `not_required`, `no_completed_unit`,
`units_in_flight`, `unsettled_failed_unit`, `already_minted`, `declaration_malformed`.
`units_in_flight` means units are still moving; `unsettled_failed_unit` means one stopped and nobody
decided (§5.1). They are deliberately distinct and must not be merged. If this set is ever CHECK-pinned,
build the SQL with an explicit join, **not** `f"col IN {TUPLE!r}"` — a single-element tuple's repr
carries a trailing comma and is a Postgres syntax error.

### 8.1 CLI and launcher

- `orchestrator mint-follow-ups` — a thin HTTP client command, like every other orchestrator CLI
  command. Tested through the **entrypoint** with `CliRunner`, not by calling the core function: a
  lone Typer command collapses to top level, so the real invocation is what must be asserted.
- `scripts/run-follow-up-mint.sh` — the `run-tracker-reconciliation.sh` shape: `set -euo pipefail`,
  BWS values fetched at runtime by stable UUID via `~/Projects/vps-backup/bws-token.sh`, `exec` the
  console script, one pass, exit. The credential is **`orchestrator-system`**, and the reason it may
  not be `orchestrator-drift-reporter` is §9.1 — that is a correctness constraint, not a preference.

## 9. Wiring — the part that stops this being the fourth unwired pass

**This is a required deliverable, not a nicety.** As of 2026-07-28:

```
scripts/run-tracker-projection.sh       exists — scheduled by nothing
scripts/run-tracker-reconciliation.sh   exists — scheduled by nothing
src/reconciliation_runner/              exists — no launcher, still reads a fixture file
~/Library/LaunchAgents/                 zero plists reference any of them
```

The one external pass producing real production data is WS-P3.0's, and the only reason is that it
hooked into `com.devon.infra-drift.plist`, which already runs at 03:00 and already holds an
orchestrator credential. A follow-up that comes due and waits for someone to remember is the same
defect class as GAP-1 (a guard merged and wired to nothing), which this program just spent a session
closing.

So: **a small additive step in `infraops-mcp-server`'s `drift-audit.sh`**, mirroring the WS-P3.0
pattern's *shape* — non-fatal, fail-open, a counted WARN on failure, touching neither the drift loop's
exit code nor its Healthchecks ping, Resend digest, change-manager sync, or security-drift step.

### 9.1 It must NOT reuse the drift-reporter identity

The mint call uses **`orchestrator-system`** (BWS `221a48d5-3f29-4898-b300-b4820140c880`, credential
key id `orchestrator-system`), fetched at runtime. `drift-audit.sh` keeps
`orchestrator-drift-reporter` for its own observation POST. The two calls in the same script use two
different credentials, deliberately.

Reusing `drift-reporter` would be wrong on the axis that matters most here. That actor's registry
profile is *observes and proposes, never mutates* — and minting a work unit is **canonical mutation**.
Because `ActorContext(identity.actor_id, role)` means every event is attributed to that `agent_id`
**forever**, borrowing it would permanently stamp canonical lifecycle creation onto an identity whose
whole purpose is that it does not do that. WS-P3.0 spent a BWS secret and two Coolify env writes
specifically to avoid borrowing an unrelated identity; spending its identity here would undo that.

Both credentials already exist in production and both already carry role `system` — verified
2026-07-28 against the running container:
`{"orchestrator-drift-reporter": "system", "orchestrator-system": "system", "orchestrator-verifier": "verifier"}`.
**No new credential, no env write, no restart is required for the wiring.**

This does **not** make the orchestrator schedule anything. The orchestrator still has no loop; an
external, already-scheduled operator job invokes a route. That is the ADR-0002/0003 posture honoured,
not bypassed.

## 10. Folded-in fixes (handoff item #4)

Two real defects in `tracker_projection_adapter/cli.py::reconcile`, fixed here rather than described:

1. **One bad item aborts the whole pass.** There is no `try`/`except` in the loop;
   `TodoistProjector._get` raises on any non-404 ≥400, so a single 401/429/500 on item 3 of 50
   discards the two already-collected observations and never reaches
   `report_tracker_reconciliation`. Fix: per-item `try`/`except` with a counted skip, returning
   `{"reported": n, "skipped": m}` — the ADR-0002 discipline.
2. **The idempotency key is the constant `"tracker-detect-pass"`** (`cli.py:152`), so every pass ever
   run shares one key. Compare the reconciliation runner's `f"reconcile-detect:{pass_id}"`. Fix: a
   per-pass key.

Both are differentially verified — reverting either reddens its test.

## 11. The guard story

What bounds what the pass may mint, and how each bound is proved:

| Bound | Mechanism | Proof |
|---|---|---|
| Only declared follow-ups | The query's sole source is `follow_up->>'required' = 'true'` on an approved revision | Plant revisions with `required: false` and with NULL; assert zero mints |
| Never twice | `uuid5(revision_id)` + existing `UniqueConstraint(work_package_revision_id, unit_key)` | Run the pass twice; assert one unit, `skipped: already_minted` |
| No mutation authority | Frozen envelope constant | Assert `minted.capabilities.keys() ∩ RUNNER_CAPABILITIES == ∅`, no `constraints.target_repository`, no `allowed_commands` |
| Cannot be worked by a runner | `follow_up_review ∉ RUNNER_CAPABILITIES` | Cross-repo fixture untouched; admission would independently refuse on `target_repository_missing` and `capability_not_enabled` |
| Never mints for cancelled-only work | Clause 2 of §5 | Plant an all-cancelled revision; assert `skipped: no_completed_unit` |
| Never mints behind an undecided failure | Clause 3 of §5 | Plant a `FAILED` unit beside completed ones; assert `skipped: unsettled_failed_unit`, **not** `units_in_flight` and not a mint |
| One bad row cannot stop the pass | Per-item `try`/`except` + counted skip | Plant a malformed declaration between two good revisions; assert both good ones mint |
| The envelope is write-once | Existing `test_authority_write_once` | Update `CONSTRUCTION_SITES` deliberately (§12) |

## 12. Architecture guards this trips

All are whole-repo scans that only a full `make check` runs — a per-task loop will look green and
still break CI.

1. **`test_authority_write_once.py::test_the_named_construction_sites_still_exist`** — this adds a
   **third** `WorkUnit(...)` construction site. The test exists to force re-verification that the new
   site assigns the envelope once and never mutates it; do that, then update `CONSTRUCTION_SITES`.
   Do not weaken the test.
2. **`test_scope_guards.py::test_production_post_route_inventory_is_explicit`** — add
   `/api/v1/follow-ups/mint` to the POST set literal. This is set equality, not a word guard.
3. **`test_cross_boundary_vocabulary.py`** — register the `follow_up` field-name vocabulary (§4.2).
4. **`test_unreachable_guards.py`** — every new public service function needs a real production
   caller in the same increment. "A test calls it" is explicitly not a caller.
5. **`test_ws32_scope_guards.py` / `test_ws33_scope_guards.py`** — the new module may not contain the
   bare tokens `dispatch`, `deploy`, or `merges` **anywhere in `src/orchestrator/`, docstrings
   included**. Note the tokenizer matches whole tokens only (`deployment` and `dispatches` do not
   match). Reach for synonyms; do not request an allowlist entry.
6. **JSON-schema-per-success-response** — the mint response needs a response model. No
   `NON_JSON_SUCCESS_PATHS` entry is needed (it returns JSON).
7. **`test_wsp21_invariant_scan.py`** — nothing to do. The pass is in-process; no new HTTP client.

## 13. Testing

TDD per task, focused tests in the **foreground**.

- **Pure due predicate** — table-driven over the five clauses, including the all-cancelled case (D6),
  the lingering-`FAILED` case with its own reason code (§5.1), and the self-exclusion (§5.2). Time is
  a parameter; nothing sleeps and nothing ages a row.
- **Mint service** — idempotency across two passes; the frozen envelope; the fingerprint round-trip
  (`normalize_authority(stored) == minted` fingerprint); per-item isolation with a poisoned row
  between two good ones.
- **Persistence** — the intake payload carries `follow_up`; malformed declarations raise a named
  `DomainError` (not a 500); the browser form and the `/api` route agree because they share the
  model.
- **Criteria + adjudication** — a follow-up unit resolves to exactly one required AC, not the
  package's whole set; a HUMAN may adjudicate `follow-up-review`; a HUMAN may **not** adjudicate a
  `post-deploy-*` id (the asymmetry, §7.1).
- **Lifecycle** — a minted unit completes via adjudicate-then-Complete; an undischarged one appears
  in `dead_letter` as `stalled_approval` (exercised by shrinking the threshold, never by ageing).
- **CLI** — `CliRunner` through the real entrypoint.
- **Adapter fixes** — differential: revert either fix and its test reddens.

Never run two pytest suites against the test database concurrently — the fixtures drop and recreate
`orchestrator_test`, and a background run plus a foreground run produce a spray of unrelated
failures on a tree that is green when run alone.

## 14. Limitations — named, owned, and not to be discovered later

### 14.1 Forward-only. There is no backfill and none is possible.

Revisions intaken before this ships have `follow_up = NULL`, and the declaration is **not
recoverable**: the package YAML is never stored, only the derived intake payload, and `follow_up` was
not in it. So no pre-existing revision can ever mint.

That includes **this workstream's own package**, whose intake happens before its own deploy. WS-P2.8
cannot be dogfooded on itself. The production demonstration (§16.2) therefore requires a *new*
package intaken after the deploy.

### 14.2 Every package gets the same timer.

`revisit_when` is prose and is not parsed. A package saying "immediately after the PR merges" and one
saying "the next quarterly drill cadence" both get `follow_up_due_after_days`. The author's actual
judgment is carried into the unit's `outcome` for the human to read, and is otherwise not honoured.

**Owner: WS-P2.10**, together with the intent-packages schema tightening. That tightening is not free
— `MapSpec` requires every key present, so a new field breaks all existing packages' validation, and
editing their YAML changes `package_hash` and invalidates their lineage approvals. It needs
optional-key support in the schema walker first.

### 14.3 This is a declared-follow-up→work bridge, not an observation→work bridge.

**WS-P2.8 reads no observation at any point.** The trigger is a human-approved package declaration;
the timing is orchestrator policy; the discharge is a human. The `Observation` table is never
queried.

That is the right design — observations still never create work — but the inherited label is
load-bearing in planning documents, and WS-P3.3 was scheduled expecting an aging-signal seam here.
The corrections are recorded in the handoff's CORRECTIONS block (item 4), the Phase-2 plan's WS-P2.8
note, and the Phase-3 plan's prerequisite row. Phase-3 sources ride this mechanism by **declaring
follow-ups in their proposed packages** — they never acquire minting authority of their own.

### 14.4 Non-foreclosure, positively stated

The mechanism is *declared intent + deterministic due predicate + idempotent mint*. Nothing in it is
specific to any source. A future source becomes able to schedule work by getting a package approved
that declares a follow-up — which routes through the existing human gates unchanged. Checked in the
final adversarial review.

## 15. Boundary — what this does not do

- No collectors. It fetches nothing.
- No scheduler, thread, or loop inside the orchestrator (`test_wsp21_invariant_scan`).
- No new lifecycle edge. `(AWAITING_REVIEW, CANCELLED)` stays absent; retirement uses the existing
  public path `AWAITING_REVIEW →(HUMAN) REVISION_REQUIRED →(SYSTEM) READY →(SYSTEM) FAILED →(HUMAN)
  CANCELLED`.
- No intent-packages change, no observation-vocabulary migration, no new evidence type.
- Nothing touches merge paths, deployment paths, or tracker sources. Dispatch stays off.

## 16. Delivery — two sessions

The build, its reviews, and a three-payload production deploy do not fit one session honestly. WS-P2.5
and WS-P2.7 each took a session for the build alone.

### 16.1 Session 1 — build, review, merge (this session)

Increments:

| # | Scope |
|---|---|
| 1 | Persist `follow_up`: column, migration `0020_wsp28_follow_up`, intake payload, validation, vocabulary registration |
| 2 | Due predicate + mint service + `follow_up_review` capability + generated-AC branch + the human-adjudicable carve-out |
| 3 | Route + response model + CLI + launcher + `docs/operations/follow-up-scheduling.md` + ADR-0007 |
| 4 | Wiring: the `drift-audit.sh` additive step (`infraops-mcp-server`) |
| 5 | Folded-in `reconcile()` fixes (per-item fail-open, per-pass idempotency key) |

**Done when:** subagent-driven TDD with a two-stage review per task; a final adversarial whole-branch
review with kills budgeted; `make check` green on a **clean tree** (Postgres + `SECURITY_STANDARDS_DIR`
+ a migrated DB — read the collected-test count, since exit 0 with 0 collected is a passing
`make check`); `/code-review`; ADR-0007 written; PRs merged by Devon.

ADR-0007 records: system-side minting is bounded to declared follow-ups; the orchestrator supplies
timing, never intent; the mechanical guarantees of §11; and that
`docs/operations/observation-ingestion.md`'s non-goal "create follow-up work units" is superseded
**only** for package-declared follow-ups — observations themselves still create nothing.

### 16.2 Session 2 — deploy, GAP-5, demonstration, closeout (dedicated)

The deploy carries **three payloads** and is not a routine swap.

1. **Build via the `Release image` workflow** (`workflow_dispatch`), not a hand-rolled `docker buildx`
   — the manual recipe survives as fallback and differential baseline. amd64. Verify the running
   container's `RepoDigest` equals the pushed digest after Coolify reports finished. *Merged is not
   deployed, and reported-finished is not running.*
2. **Migrate first.** Migration `0020` before the image swap.
3. **GAP-5 step 2 — record the release-artifact binding** against the *approved revision*. Production
   validates `package_revision_hash` and rejects a synthetic one; the local drill passes only because
   its seeded revision matches by construction.
4. **GAP-5 step 3 — record the deployment observation** with `environment: "production"` (the
   2026-07-27 drill used `"drill"`, which is why it did not close this). It needs an
   `ActorRole.SYSTEM` actor, and **`orchestrator-system` is one** — a standing SYSTEM credential
   (BWS `221a48d5-3f29-4898-b300-b4820140c880`). Use it directly.

   **There is no temporary-credential sequence, no env write, and no restart for this step.** An
   earlier draft of this spec carried one, inherited from a stale CLAUDE.md bullet asserting "the
   standing M2M credential is worker-role". That bullet conflated `orchestrator-system` with
   `factory-runner-github` — the one credential with no `ORCHESTRATOR_M2M_ROLES` entry. Verified
   2026-07-28 against the running container:
   `{"orchestrator-drift-reporter": "system", "orchestrator-system": "system", "orchestrator-verifier": "verifier"}`.
   The bullet is corrected in this branch. Deleting this sequence removes session 2's only
   outage-shaped step.
5. **GAP-5 step 4 — run the traceability query end-to-end** on that release, retain the output, and
   deliberately retire the post-deploy unit — one left `submitted` raises `deploy_split_brain` on
   every future detect pass.
6. **GAP-5 step 5 — smoke the `/review` intake form.** Confirmed absent from production as of
   2026-07-28: the running image `8da4af3-wsp27inc2-amd64` has `web.py` at 29,354 bytes with no
   `intakes/new` and no `intake_new.html` template. A `302` on that URL proves nothing — it is
   Authentik's forward-auth redirect, which fires before the app is consulted.
7. **The production demonstration.** Because §14.1 makes the pass return an empty list against all
   existing data, WS-P2.8 is otherwise deployed-but-never-exercised — the exact status the
   gap-closure workplan exists to attack. So:
   - author a small package declaring `follow_up.required: true`;
   - **intake it through the `/review` intake form** — this is the *first* real use of that form and
     smoke-tests GAP-6 in the same pass, retiring the devtools `fetch()` for good;
   - decomposition → authority approval → walk its unit to `COMPLETED`;
   - **restart 1 of 2** — set `ORCHESTRATOR_FOLLOW_UP_DUE_AFTER_DAYS=0` and restart, so the unit is
     due the moment it settles rather than in 30 days;
   - run the mint pass (`orchestrator-system` credential);
   - confirm exactly one unit minted in `AWAITING_REVIEW`; adjudicate its AC and Complete it in
     `/review`;
   - re-run the pass and confirm it mints nothing (`skipped: already_minted`) — the idempotency
     property demonstrated against production, not only in tests;
   - **restart 2 of 2** — remove the env override and restart, restoring the 30-day default.

   These are the **only two production restarts in session 2**, and neither is outage-shaped: the
   variable is a bounded `int` with a default, so a failed write leaves the default in force rather
   than failing startup closed. Confirm the value from inside the container after each restart.
8. **Run the exit-criteria attestation** (GAP-1's workflow) against the new production state.
9. **The Wave-2 closeout note.** It declares **three of four** exit clauses — traceability ✅, tracker
   two-way ✅, follow-up scheduling ✅ — and marks **Evidence Pack as closing via GAP-4**, not via
   this workstream. That is the gap-closure workplan's own position (no PR in the org has ever
   carried an Evidence Pack; the last factory-created PR predates the capability by two days), and it
   is not a failure of WS-P2.8. The handoff's "Wave 2 closes when this ships" is corrected in its
   CORRECTIONS block, item 5.

**Wave 3 starts when** WS-P2.8 is complete and GAP-1 through GAP-6 are done — the point at which the
Wave-2 exit statement is *demonstrated* rather than shipped.
