# ADR 0052 — An unworked decomposition approval can be superseded

**Date:** 2026-10-03
**Status:** Accepted (Devon, 2026-10-03: SDS 1.1 item 2b-2/R5; scope DRAFT or READY, cancelled
units free their keys, the proposal stays `approved`)

## Context

Approving a decomposition proposal creates the package revision's work units, and nothing could
undo it. `ApprovedDecomposition.superseded_at` existed but nothing wrote it, and a second approval
of the same revision is refused with `decomposition_already_approved`. A wrong approval cost a whole
new package revision and a fresh set of approvals. Two software-delivery packages were left unusable
that way in July (WS-P2.12), and the rule that every unit's commands are dry-run twice before
approval exists because of it.

The simplification review of 2026-07-31 decided that approval must be reversible (decision 2b-2),
and scoped it to approvals whose work hasn't started (ruling R5). Rolling back started work is a
different mechanism and isn't part of this decision.

## Decision

A human may supersede an approved decomposition while none of its units has been worked.

- **Who and where.** A human, from the decomposition proposal's page in `/review`, giving a reason.
  The orchestrator offers the control only while the preconditions hold.
- **Preconditions.** The approval is the revision's active one, and every unit it created is in
  `draft` or `ready`, has `attempt_count` 0, has no claim, and has no dispatch record in
  `dispatched` or `failed`. A dispatch is recorded before a claim, so such a record means a run may
  be about to claim; a failed trigger call can still have reached GitHub. A `skipped` or `blocked`
  record ran nothing and doesn't count (`services/execution/run_activity.py`).
- **Effects, in one transaction.** The approval's `superseded_at`, `superseded_by` and
  `supersession_reason` are set; each of its units moves to `cancelled`; one
  `decomposition.superseded` event is written. The proposal stays `approved`: the approval row is
  the record that it no longer stands. Envelopes are untouched, since nothing rewrites a unit.
- **Retiring a unit.** `DRAFT → CANCELLED` and `READY → CANCELLED` are human edges that need the
  `decomposition_superseded` transition guard. Like every guard, it is read from the database: it
  holds only when the approval that registered the unit (whose id is
  `uuid5(proposal_id, unit_key)`) is superseded. So the ordinary cancel action still can't cancel a
  `draft` or `ready` unit: a stranded `ready` unit stays inert, and is still retired by letting it
  fail first. The legal-edge count goes from 30 to 32.
- **Re-approval.** With no active approval, the revision takes a new proposal and approval as
  before. Unit keys are unique per revision among units that aren't cancelled, so a re-approval may
  reuse the superseded units' keys; the factory derives its keys deterministically and relies on
  that.

## Consequences

- One wrong approval costs a supersession and a new proposal, not a new package revision.
- The dry-run-twice rule loses its reason for units that haven't been worked. It still holds once a
  unit is claimed, because started work can't be superseded.
- Units from the new approval need their own authority approvals, unless the envelope matches a
  known-good pattern (ADR-0011).
- A key lookup by `(revision, unit_key)` must skip cancelled units wherever more than one unit can
  hold the key: `register_approved_unit` does, and so does intent-packages' `factory`
  (intent-packages #104). Readers that join a unit's key to the revision's active decomposition (the
  runner brief, the verifier's criteria, `required_ac_ids`) show a retired unit the new approval's
  mappings; a retired unit is terminal, so nothing acts on that.
