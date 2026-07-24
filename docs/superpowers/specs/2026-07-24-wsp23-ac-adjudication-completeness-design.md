# WS-P2.3 — AC Adjudication Completeness (Design)

**Date:** 2026-07-24
**Workstream:** WS-P2.3 (Wave 1). Closes program exit criteria #3 (every AC has a canonical
outcome + evidence) and #4 (waivers structurally approved and auditable), and the Wave-1 exit
item "no unit completes with an unadjudicated AC."
**Status:** Approved design, pre-implementation.

## Context

The completion gate already exists and is fail-closed. A unit cannot reach `completed` through
any lifecycle path unless every required AC carries exactly one satisfying canonical terminal
adjudication (`services/lifecycle.py::_current_terminal_is_satisfied`, gated at
`kernel/transitions.py` on the `→ completed` edge for all roles). **WS-P2.3 does not rebuild the
gate.** The gate's inputs are only satisfiable out-of-band today; this workstream makes them
satisfiable *in-band* and makes waivers *auditable*.

Two real gaps:

1. **No human AC-adjudication form in `/review`.** `_authorize_outcome`
   (`services/evidence.py:659`) lets only a VERIFIER record `passed`/`failed`/`not_applicable`
   and only a HUMAN record `waived`. `/review` has **no form/route** to record any adjudication
   (`templates/unit.html:29-30` displays them read-only). So a `judgment_required` AC — the kind
   that needs human judgment — has no in-band path to a canonical `passed`.
2. **Waiver metadata is only partly constraint-backed.** The DB `CHECK`
   (`persistence/models.py:432`) enforces, for `waived`: `failed_evidence_id` present +
   non-empty `rationale`/`risk`/`follow_up`. But `risk` is free text (not a "risk class"), and
   there is no independent audit of structurally-thin waivers.

**Out of scope (deferred boundary):** the `automated_test → judgment_required` evaluator gap is
a separate, larger P1 workstream (the deterministic-evaluator rebuild). This design touches
neither `evaluate_criterion` nor the type sets, so
`tests/services/test_criterion_evidence_vocabulary.py` stays green.

## Decisions (settled with Devon, 2026-07-24)

- **A — authorization model: narrow, static predicate.** A HUMAN may record
  `passed`/`not_applicable` **iff the criterion's `evidence_type ∈ JUDGMENT_TYPES`** (the
  intrinsic classification), *not* the dynamic "currently evaluates to judgment_required" — the
  dynamic form has a hole (`automated_check` evaluates to `judgment_required` only while its CI
  evidence is absent, which would let a human pass a CI-owned AC in that window). The static
  predicate structurally cannot touch a deterministic/verifier-owned AC.
- **B — form outcomes: `pass / not_applicable / waive`.** Per-AC `failed` is deferred (YAGNI —
  it never contributes to completion, overlaps the existing unit-level `revision_required`
  reject, and introduces a parked non-satisfying terminal).
- **C — waiver hardening: risk-class enum (structural) + thin-waiver audit (reporting).** No
  `decided_by_role` column — the decider's role is already captured on the `adjudication.recorded`
  event and HUMAN-only waivers are already service-enforced, so a column + CHECK would be
  defense-in-depth for value already covered.

## Design

### 1. Authorization change (Decision A)

Extend `_authorize_outcome` (`services/evidence.py:659`) to receive the AC's `evidence_type`:

```
waived                                  → HUMAN (any AC)                     # unchanged
passed/failed/not_applicable + VERIFIER → allowed                            # unchanged
passed/not_applicable + HUMAN           → allowed iff evidence_type ∈ JUDGMENT_TYPES
everything else (incl. HUMAN + failed)  → role_forbidden
```

`record_adjudication` fetches the `PackageAcceptanceCriterion` for `(revision_id, ac_id)` (a
one-row query) and passes its `evidence_type`. Verifier post-deploy synthetic ACs never reach
the HUMAN branch (they are blocked earlier by `_validated_subject`'s
`post_deploy_verifier_required`, and the verifier branch does not consult the field), so a
`None` lookup is safe. `JUDGMENT_TYPES` is imported from `services/verifier_evaluators`.

### 2. Adjudication form + route (Decisions A + B)

- **Route:** `POST /review/units/{unit_id}/adjudication` on the `orchestrator-review`
  (forward-auth) router. Mirrors `approve_authority` / `resolve_reconciliation_condition`
  exactly: `_human(actor)`, `_require_form(...)`, then `record_adjudication(...)` (which owns and
  commits its transaction), raising on `DomainError`, redirecting on success.
- **Per-AC forms**, one per required AC, keyed by `ac_id` for CSRF + idempotency — mirrors the
  reconciliation-conditions pattern (`unit.html:42`, `condition_csrf_tokens` /
  `condition_idempotency_keys`). This is deliberate: a single shared idempotency key across ACs
  would make a second submission replay as a duplicate of the first.
- **GET handler** loads the required criteria + each AC's current evaluation (read-only reuse of
  `load_required_criteria` + `current_evidence` + `evaluate_criterion`) so each form shows the
  AC's status and offers only the outcomes it permits: a judgment-type AC → `pass` / `N-A` /
  `waive`; a deterministic AC → `waive`-only (a human accept-risk on a failed deterministic AC is
  legitimate and already authorized). The server is the enforcer regardless of what the UI shows.
- **Fields:** `ac_id`, `outcome`, `rationale`, `expected_version`, `csrf_token`,
  `idempotency_key`, `confirm`. For `waived`: `failed_evidence_id`, `risk` (dropdown),
  `follow_up`, optional `expires_at`. **`scope` is not exposed** — a scoped waiver silently fails
  the completion gate (`lifecycle.py:541`), so the form only ever writes full waivers.

### 3. Risk-class enum (Decision C / Option 1)

- Kernel constant `WAIVER_RISK_CLASSES = ("low", "medium", "high", "critical")` — single source
  of truth (proposed vocabulary; confirm in review).
- Alembic migration adds `CHECK (risk IS NULL OR risk IN (...))` on `adjudications` (cheap:
  near-empty ledger, no backfill).
- Service belt-and-suspenders: `_validate_adjudication_fields` rejects an out-of-vocab `risk` for
  `waived` with a clean `waiver_invalid` `DomainError`; the CHECK is the structural backstop for
  any non-service write path. Form renders `risk` as a `<select>`.

### 4. Thin-waiver audit (Decision C / Option 3)

- New reporter in `services/consistency.py` mirroring `_completion_findings` (`:147`) — read-only,
  no gate. Flags **current (non-superseded) waivers that are structurally thin**: primarily
  *expired* waivers (`expires_at ≤ now` — accepted risk that outlived its window; the gate
  already rejects these for completion, but nothing proactively surfaces them), plus any waiver
  whose `risk` is outside `WAIVER_RISK_CLASSES` (defense for legacy/backfilled rows). The four
  core fields are already CHECK-enforced, so the audit covers the service-only dimensions.

### 5. Gate and boundary safety

- A human-recorded `passed` is an ordinary `Adjudication` row → flows through the unchanged
  `_current_terminal_is_satisfied`. No gate edit; the gate stays the sole arbiter of `→ completed`.
- It is `record_adjudication`, **not** a `commands/{command}` transition → not miscounted by
  WS-P2.2's improvisation counter as an operator override.
- **Boundary respected:** `evaluate_criterion` and the type sets are untouched. `automated_test`
  stays in `JUDGMENT_TYPES` (still evaluates to `judgment_required`), so
  `test_criterion_evidence_vocabulary.py:39` stays green. A human *can* now pass an
  `automated_test` AC — correctly: `automated_test` is judgment-requiring by design and the human
  **is** the judgment. This resolves a `judgment_required` AC in-band; it does not reclassify it.

## Testing (TDD; extend existing invariant tests, do not weaken them)

- `tests/services/test_adjudications.py`: HUMAN pass of a judgment AC succeeds; HUMAN pass of a
  `test` / `security.scan` / `automated_check` AC → `role_forbidden`; HUMAN `failed` →
  `role_forbidden`; existing verifier-only rules unchanged.
- `tests/services/test_lifecycle_guards.py`: a human-recorded `passed` satisfies the *same*
  completion gate; a unit with an unadjudicated judgment AC still cannot complete.
- `tests/services/test_waivers.py`: `risk` outside `WAIVER_RISK_CLASSES` rejected (service +
  CHECK); existing waiver structural requirements unchanged.
- `tests/services/test_consistency.py`: the thin-waiver audit reports an expired waiver and stays
  silent on a healthy one.
- New web test: drive `POST /review/units/{id}/adjudication` end-to-end — asserts persistence via
  `expire_all()` + re-read (reachability + commit discipline, per the WS-P2.1 lesson).
- `tests/services/test_criterion_evidence_vocabulary.py`: unchanged and green (boundary guard).

## Migration / deploy

- One Alembic migration (risk-class CHECK). No data backfill (near-empty ledger).
- Merged ≠ deployed: the new `/review/.../adjudication` route 404s until the image is rebuilt and
  redeployed to `sds.alobar.net`. Production dispatch stays `false` (re-enabling is an independent
  Devon decision).

## Definition of done

1. `/review` per-AC human adjudication form + route (forward-auth, `_human` + CSRF, commits),
   recording a canonical `passed` / `not_applicable` / `waived` gated by the A-static predicate;
   completion gate unchanged.
2. Risk-class enum (migration + CHECK + service validation + form dropdown) and the thin-waiver
   audit reporter.
3. Boundary respected: `test_criterion_evidence_vocabulary.py` unchanged and green.
4. New + existing invariant tests green; `make check` green (collected count read; `ruff format`
   run, not just `ruff check`); `/code-review`; independent adversarial review; Devon merges; a
   Wave-1 progress/closeout note in `~/docs/software-delivery-system/`.

## Deferred (candidates for the later simplification / follow-on pass)

- Per-AC `failed` (Decision B), if AC-granular rejection provenance is ever needed.
- `decided_by_role` structural constraint (Decision C / Option 2), if a code path ever writes
  adjudications outside `record_adjudication`.
- Broader reviewer role/capability (Decision A / Option ii), if humans ever need to adjudicate
  deterministic ACs.
