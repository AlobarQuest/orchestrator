# ADR-0026 — The signal→work record

- **Status:** Accepted
- **Date:** 2026-08-18
- **Decided by:** Devon
- **Settles:** the Phase-3 plan's entry gates **G1** (signal→work correlation) and **G2**
  (proposed-package lifecycle), which that plan requires be settled *once, before the first
  adapter*
- **Relates to:** ADR-0021 (two signal ledgers), ADR-0025 (approval by policy)
- **Design:** `~/docs/software-delivery-system/2026-08-18-g1-g2-design.md`

## Context

Three things are measurably absent, and together they make a signal-driven lane inexpressible:

- **`observations` has no foreign key to anything.** It records that something happened, connected
  to no work.
- **`TraceabilityAnchor` has no observation anchor** (`work_unit`, `revision`, `artifact_digest`,
  `commit`, `pr`, `environment`). You can ask what a work unit caused; you cannot ask what a
  *signal* caused. The observation *hop* additionally filters on `subject_type="work_unit"`, so
  nearly every observation is invisible to the chain by construction.
- **Nothing exists where a machine-made proposal could live before intake.** The first durable
  record of intended work is a package intake, and `register_package_intake` opens with
  `_require_human`.

The Phase-3 plan's warning is the reason this is an ADR and not a build: *"an adapter could work
exactly as designed while the phase's required traceability demonstration remained impossible."*

## Decision

**1. A proposal is a first-class orchestrator record.**

ADR-0021's test — *a signal needing a decision belongs in change-manager* — points elsewhere, and
is not followed here. **The reason is ownership, not capability.** A first draft argued
change-manager *could not* hold it because it has no outbound HTTP; Devon corrected that as a
choice rather than a constraint. The load-bearing argument: a proposal is **pre-work**, and every
other pre-work record — intake, revision, decomposition — already lives in the orchestrator.
Splitting the lifecycle across two systems would make the correlation record span a boundary
neither owns, which is precisely what G1 exists to prevent.

**2. The correlation is keyed on the observation `id`.**

Not `(source_system, source_reference)`. That pair is the natural business key and is
unique-enforced, but a **run-keyed reference changes every run by design** — that is what stops a
re-runnable producer wedging on `observation_conflict`. Keying the correlation on it would make a
re-posted observation a different cause. The `id` is stable, opaque, and immune to the
reference-shape decisions each producer makes for its own reasons.

**3. The link is carried on the package revision.**

The plan's hard clause is *"how it survives normalization and revisioning."* A link living only on
the proposal breaks at the first revision. The revision is the first durable artifact a human
approves and the thing revisioning actually creates, so a new revision **inherits the originating
observation id explicitly rather than by accident.**

**4. A withdrawal may invalidate a PROPOSAL. It never rewrites history past admission.**

Note first that this cannot happen today: observations are append-only, with no supersession model
and no delete route. Two cases are distinguished, and conflating them was the first draft's error:

- **Superseded by a later fact** — a backup fails Monday and succeeds Tuesday. Nothing was
  withdrawn; Monday's failure happened and the work it caused was correct. The chain keeps saying
  so. This is the common case.
- **The signal was wrong** — a producer bug. Then it depends where the work reached:

| state | effect |
|---|---|
| proposal not yet admitted | **invalidate it** — nothing has been committed |
| admitted, unit in flight | the **unit's** lifecycle handles it (cancel), not the observation's |
| work already landed | **nothing** — the chain records what happened |

*"Withdraw the signal, unwind the work"* is the intuitive answer and it is wrong for everything
past the proposal.

**5. A human admits a proposal, and the admission surface is built as part of this work.**

The states are `proposed → (admitted | rejected | withdrawn | superseded)`, and `admitted` is the
only one that produces an intake.

**The surface clause is load-bearing and was nearly missed.** Measured: `web.py::create_intake`
takes `payload: Annotated[str, Form(min_length=1)]` — the intake surface is a textarea into which
`emit-intake-payload` JSON is pasted by hand. So "a human admits it" today means a **transcription
step**, and shipping a proposal record without a surface would leave the machine doing the work and
a human retyping it: the manual hop this programme treats as a clot rather than a gate.

Because the proposal is already a record, admission becomes a button on it in `/review`, and the
human sees the proposal rather than a payload blob.

**A machine admitting its own proposal is deliberately not decided here.** It would be the first
automated path into canonical work — a standing-authority decision of the same weight as ADR-0025.
The difference between it and this decision is one guard, and this decision produces the evidence
on which to make that one.

**6. OBSERVER gains propose; no new role.**

`orchestrator-drift-reporter` already holds OBSERVER, whose entire write surface is
`POST /api/v1/observations`, confined by `_confine_observer` keyed on the route template — one
allowlist, one place to change. A second observe-and-propose role would be two places to get wrong,
and the row already records `source_system`, so the credential need not carry provenance.

## What this deliberately does NOT do

**It produces no diagnosis.** These are the record and the lifecycle. The thing that reads a
refusal code and concludes *"add `extend-exclude` to `brain`"* is a **producer**, and no producer
of that kind exists or is specced — on 2026-08-17 that step was performed by a person, in
conversation, and nothing recorded that it happened.

So this makes a signal-driven lane **expressible and traceable**. It does not make one
**autonomous**. Stated plainly because "G1/G2 shipped" will otherwise be heard as "signals now
become work."

## Consequences

- **Phase-3 exit criterion 2** becomes demonstrable: a proposal lane whose full signal→release
  chain is answerable by the traceability query.
- **Tier C** (the 18–24 major and requirement-range bumps ADR-0016 assigns to the factory) and
  **Tier D** (the three `upstream-sync` pull requests open since 2026-06-29) become buildable.
- The traceability chain gains an **observation anchor**, and the observation hop must stop being
  unit-scoped-only or the chain still cannot carry the signal that caused the work.
