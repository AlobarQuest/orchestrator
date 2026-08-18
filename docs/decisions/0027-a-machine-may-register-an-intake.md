# ADR-0027 — A machine may register an intake

- **Status:** Accepted
- **Date:** 2026-08-18
- **Decided by:** Devon
- **Settles:** the question ADR-0026 deferred — whether the orchestrator may admit machine-proposed
  work without a human act
- **Relates to:** ADR-0006 (browser-only human surfaces), ADR-0025 (approval by policy), ADR-0026
  (the signal→work record)

## Decision

**`register_package_intake` may be reached by a machine.** The `_require_human(actor)` guard is
removed for this path, and the carry built on 2026-08-18 completes rather than stopping at a
printed payload for a human to paste.

## Why — the guard was protecting a transcription, not a judgment

Devon, 2026-08-18: **a human has never actually created an intake package. Always AI.**

That is the whole argument. `_require_human` checks `actor.role is ActorRole.HUMAN`, and intake is
browser-only, so every intake in production was *transmitted* by a human in a browser. But the
**content** was authored by an AI every time, and the human act was pasting `emit-intake-payload`
JSON into a textarea (`web.py::create_intake` takes `payload: Annotated[str, Form(min_length=1)]`).

So the gate has been asking a human to retype a machine's work and calling that review. It is the
manual hop this programme treats as a clot rather than a gate — and it was costing the carry its
last step for no judgment gained.

## What still gates the lane — the intake was never the last checkpoint

Removing this does **not** remove a human from the path to production. After intake there remain:

1. **Decomposition approval** — human-only; `_require_decision_actor` raises for any non-human, and
   it is reachable only through the `/review` GUI.
2. **Authority envelope approval** — a human click per unit, bound to the exact fingerprint, naming
   the target repository, capabilities, change class and budget.
3. **Dispatch admission** — eight terms, including reach, change window and the target-repository
   allowlist.

And *before* intake, under ADR-0026, a human approves the change record in change-manager — which is
the decision that matters and the one this programme deliberately kept.

So the human moves from **four touchpoints to three**, and the one removed is the only one where the
human was transcribing rather than deciding.

## What replaces it: attribution, not absence

The intake must record that a machine registered it and **which approved change record authorised
it**. ADR-0026 decision 3 already requires the revision to carry the change record id; this makes
that carry the accountability record as well as the traceability one.

A machine-registered intake with no such reference is the fail-open to guard against — it would be
canonical work with no decision behind it, which is exactly what the human act used to (weakly)
prevent.

## Open for the build, not decided here

**Which credential registers the intake.** `orchestrator-system` (SYSTEM) already performs canonical
mutation — `commands/ready` and dispatch — so it is the natural fit and needs no registry change.
A distinct carrier actor would be cleaner attribution but requires a merged security-standards
commit plus an image rebuild, because `agent_id` resolves against a registry bundle baked into the
image. **Prefer SYSTEM unless the build finds a reason otherwise; `source_system` on the observation
and the change record id on the revision already carry provenance.**

Note ADR-0026 decision 6 (OBSERVER gains propose) is unaffected. Proposing and registering are
different verbs, and OBSERVER must not gain the second.

## Consequences

- **The carry completes.** An approved change-manager work proposal becomes an orchestrator intake
  with no human transcription step.
- **Phase-3 exit criterion 2** becomes reachable end to end, since the lane no longer stops at a
  printed payload.
- **ADR-0006 is narrowed, not overturned.** Browser-only remains right for *decisions* — the
  decomposition and authority approvals. It was over-applied to a transcription.
- The next honest question is whether the *decomposition* approval is also a transcription in
  disguise. It is not — it is a human reading a proposed breakdown and its envelope — but it should
  be asked deliberately rather than assumed, on the same evidence this decision rested on.
