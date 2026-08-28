# ADR-0026 — The signal→work record

- **Status:** Accepted
- **Date:** 2026-08-18
- **Decided by:** Devon
- **Settles:** the Phase-3 plan's entry gates **G1** (signal→work correlation) and **G2**
  (proposed-package lifecycle), which that plan requires be settled *once, before the first
  adapter*
- **Relates to:** ADR-0019 (deploying merges route through change-manager), ADR-0021 (two signal
  ledgers), ADR-0025 (approval by policy)
- **Design:** `~/docs/software-delivery-system/2026-08-18-g1-g2-design.md`

## The architecture this rests on, stated by Devon 2026-08-18

**change-manager is the estate's human review surface** — where a thing goes when it needs a person
to decide. **SDS is the execution engine.** change-manager is a *consumer* of SDS, not a peer store,
and once something is approved there, **one lane to effect it is SDS**.

That makes ADR-0019 the architecture rather than a special case, and it names the end state:
**change-manager will eventually feed SDS intake, and the two become one Operations Factory.**

It also explains the programme's shape. change-manager was built to the point of *"we approved the
work — now can AI DO the work?"*, and SDS was the answer to that question. Building it is why
change-manager stopped where it did. **It is partially built and not in daily use**; it has a
dashboard, an item detail page and an action route, so it can take decisions, but nothing carries an
approved item onward.

## Context — what is measurably absent

- **`observations` has no foreign key to anything.** It records that something happened, connected
  to no work.
- **`TraceabilityAnchor` has no observation anchor** (`work_unit`, `revision`, `artifact_digest`,
  `commit`, `pr`, `environment`). You can ask what a work unit caused; you cannot ask what a
  *signal* caused. The observation *hop* additionally filters on `subject_type="work_unit"`, so
  nearly every observation is invisible to the chain by construction.
- **Nothing carries an approved change-manager item into SDS.** The first durable record of intended
  work in the orchestrator is a package intake, reachable only by a human pasting
  `emit-intake-payload` JSON into a textarea (`web.py::create_intake`).

The Phase-3 plan's warning is why this is an ADR and not a build: *"an adapter could work exactly as
designed while the phase's required traceability demonstration remained impossible."*

## Decision

**1. The decision-needing item is a change-manager record. The orchestrator holds everything after
approval.**

This follows the architecture above rather than overriding ADR-0021 — that ADR's test (*a signal
needing a decision belongs in change-manager*) gives the right answer here, and an earlier draft of
this ADR got it wrong by placing the proposal in the orchestrator.

Two arguments in that draft were bad and are recorded so they are not repeated. It claimed
change-manager *cannot* hold this because it has no outbound HTTP — a **choice, not a constraint**.
And it objected that the lifecycle would span two systems — which ADR-0019 already does, on purpose,
in the estate's best-working lane.

**2. The correlation is keyed on the observation `id`.**

Not `(source_system, source_reference)`. That pair is the natural business key and is
unique-enforced, but a **run-keyed reference changes every run by design** — that is what stops a
re-runnable producer wedging on `observation_conflict`. Keying the correlation on it would make a
re-posted observation a different cause. The `id` is stable, opaque, and immune to the
reference-shape decisions each producer makes for its own reasons.

**3. The link is carried on the package revision, and each side carries a reference at the join.**

The plan's hard clause is *"how it survives normalization and revisioning."* The revision is the
first durable artifact a human approves and the thing revisioning creates, so a new revision
**inherits the originating reference explicitly rather than by accident.**

Because the chain now threads two systems, the join is explicit on both sides:

```
observation (orchestrator, fact)
  → change record (change-manager, decision)   — carries the observation id
  → human approves
  → intake → revision (orchestrator)           — carries the change record id
  → decomposition → work unit → PR → landing
```

That is the shape the deploy lane already uses, where the orchestrator joins change-manager on
`(repository, pull_request_number)`.

**4. A withdrawal may invalidate a change record. It never rewrites history past approval.**

Note this cannot happen today: observations are append-only, with no supersession model and no
delete route. Two cases are distinguished, and conflating them was an earlier draft's error:

- **Superseded by a later fact** — a backup fails Monday and succeeds Tuesday. Nothing was
  withdrawn; Monday's failure happened and the work it caused was correct. The chain keeps saying
  so. This is the common case.
- **The signal was wrong** — a producer bug. Then it depends where the work reached:

| state | effect |
|---|---|
| change record not yet approved | **invalidate it** — nothing has been committed |
| approved, unit in flight | the **unit's** lifecycle handles it (cancel), not the observation's |
| work already landed | **nothing** — the chain records what happened |

*"Withdraw the signal, unwind the work"* is the intuitive answer and it is wrong for everything past
approval.

**5. The human decides in change-manager. The missing piece is the path from an approved item into
SDS intake — and that path is this work's deliverable.**

An earlier draft proposed building a second admission surface in the orchestrator's `/review`. That
was wrong twice over: it duplicates the surface change-manager exists to be, and it was justified by
calling that surface finished, which it is not.

What is actually missing is **the carry** — nothing takes an approved change-manager item and
produces an orchestrator intake. Today intake is a human pasting JSON. Building that carry is the
first concrete step of change-manager and SDS becoming one Operations Factory.

**A machine approving its own proposal is deliberately not decided here.** It would be the first
automated path into canonical work — a standing-authority decision of the same weight as ADR-0025 —
and this decision produces the evidence on which to make that one.

**6. OBSERVER gains propose; no new role.**

`orchestrator-drift-reporter` already holds OBSERVER, whose entire write surface is
`POST /api/v1/observations`, confined by `_confine_observer` keyed on the route template — one
allowlist, one place to change. A second observe-and-propose role would be two places to get wrong,
and the row already records `source_system`, so the credential need not carry provenance.

## What this deliberately does NOT do

**It produces no diagnosis.** These are the record and the lifecycle. The thing that reads a refusal
code and concludes *"add `extend-exclude` to `brain`"* is a **producer**, and no producer of that
kind exists or is specced — on 2026-08-17 that step was performed by a person, in conversation, and
nothing recorded that it happened.

So this makes a signal-driven lane **expressible and traceable**. It does not make one
**autonomous**. Stated plainly because "G1/G2 shipped" will otherwise be heard as "signals now
become work."

## Consequences

- **Phase-3 exit criterion 2** becomes demonstrable: a proposal lane whose full signal→release chain
  is answerable by the traceability query.
- **Tier C** (the 18–24 major and requirement-range bumps ADR-0016 assigns to the factory) and
  **Tier D** (the three `upstream-sync` pull requests open since 2026-06-29) become buildable.
- The traceability chain gains an **observation anchor**, and the observation hop must stop being
  unit-scoped-only, or the chain still cannot carry the signal that caused the work.
- **change-manager resumes**, in the direction it was always headed: the approved item now has
  somewhere to go.
