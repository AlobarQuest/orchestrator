# ADR-0010 — The policy artifact: a separate file in this repo, read per request, that can only refuse

**Status:** Accepted · **Date:** 2026-08-01 · **Workstream:** WS-P2.18 (Increment 2)
**Supersedes nothing. Builds on:** ADR-0009 (reach is the key).

## Context

WS-P2.18 replaces four scattered policies — the authority known-good pattern, the change window,
the per-key lease, and self-update — with one versioned artifact keyed on `reach` (ADR-0009).
Increment 2 ships the container and its guarantees; Increments 3–5 ship the four behaviours.

Three questions had to be answered before any of that code could be written, and the code is
downstream of all three.

---

## Decision 1 — a new artifact, not an extension of `routing-policy.toml`

**`routing-policy.toml` lives in `AlobarQuest/intent-packages`, not here.** The orchestrator has
never read it: `grep -rn routing-policy src/` returns nothing. It is consulted by `factory
decompose` in that repository, and its rows key on `surface` and `change_class` to choose a
**model**.

Extending it would mean the orchestrator reading, at request time, a file owned by another
repository and checked out by neither. That is not a shortcut past the standing hazard — *two
vocabularies that must agree with nothing checking they do* — it is a fifth instance of it, in the
place where the failure is quietest, because a policy file that fails to arrive reads as a policy
that permits.

The two artifacts key on **disjoint dimensions**: `change_class` selects a model, `reach` decides
whether work may run at all. Nothing about them must agree, so there is no drift surface. Folding
one into the other would have manufactured the obligation rather than discharged it.

R3's *"following the existing `routing-policy.toml` precedent"* is honoured as **precedent of
form**, and every element of that form is carried over:

| `routing-policy.toml` | `factory-policy.toml` |
|---|---|
| one versioned document, `version = N` | same |
| per-row `rationale` + `decided` | same |
| an explicit editing contract in the header | same |
| *"a change-class absent here is a hard error"* — no implicit default | total coverage: every reach member has exactly one row, enforced by the loader |
| *"never an inline override anywhere else"* | enforced by test: no rationale of this artifact appears in any `src/**/*.py` |

**Consequence: the artifact keys on nothing but reach.** The handoff warned that a schema keying on
change class must treat the null case as first-class (three change classes; five delivery profiles;
`infrastructure-change` and `non-software-operational` carry `change_class = None`). That trap does
not arise, because change class is not a key here. Execution locus — `local-heavy` in
`routing-policy.toml` — is likewise **not** smuggled in: it is a real second dimension, it is
currently unmodelled, and if a later increment needs it (spec §7's self-deploy keys on "reach AND
lane") it must be named as a second key in an additive schema version, not folded into reach.

## Decision 2 — where it lives, and what changing it costs *(the architectural one)*

**`src/orchestrator/factory-policy.toml`, beside the module that reads it, resolved by
`Path(__file__).parent` — and re-read on every call, never cached.**

The cost of a policy change has two components that are usually conflated, and separating them is
the whole decision:

1. **Getting new bytes onto the running process.** Today: an image build and a release, and a
   release restarts the orchestrator.
2. **Making the process notice bytes it already has.** This design: **zero**. There is no cache and
   nothing is read at start-up, so an edit is in force at the next call.

Only component 2 is Increment 2's to decide, and it is decided in favour of no-restart. Component 1
is a property of how the image ships. It is also the *reversible* half: a later increment that
wants edits without a release adds a path setting and mounts the file, and because the loader
already re-reads per call, that change needs no restart and no code here. The half that is hard to
retrofit — the discipline of never holding a parsed policy across requests — is settled now.

**Stated explicitly, as the handoff requires: today, changing policy requires a release, and a
release restarts the orchestrator.** The documented hazard is exactly this — closing a bounded
window restarted the process while a run was live, the runner's `finalize-run` met a 503, and
because `fail-run` fails the same way the unit stranded in `executing` with its attempt spent. The
coding action takes ~40 seconds end to end, so there is no safe gap to aim for.

> **Operational rule.** Change the policy artifact only when no run is live — all three of: the
> Actions run concluded, the unit out of `executing`, and cost-actuals recorded. This is the same
> rule the bounded-window close already imposes; the artifact inherits it and adds nothing new. It
> is bounded in practice by Devon's stated model, *"set the window once"*.

### Why not database-backed

It is the obvious answer to "changing it must not need a restart" and it is the wrong one here.

- It removes the **release**, not the restart, and the release is component 1 — which the
  read-per-call design already isolates and a mounted file already solves, at a fraction of the
  cost.
- It moves policy **out of code review**. A versioned artifact whose diffs are reviewable is the
  property that makes "one versioned artifact" worth anything; a row in a table is invisible to
  every gate this repo has.
- It is a workstream, not an increment: a migration, a write route (which trips two exact route
  inventories and the idempotency matrix), an audit trail, and a human editing surface — none of
  which Increment 2 could ship with a caller, and all of which would have to be built before the
  first policy behaviour existed to justify them.

### Why nothing is cached

A cache is the mechanism by which a policy change silently fails to take effect. This surface is
consulted at admission, which is measured in runs per day; parsing a two-kilobyte document is not a
cost worth trading correctness for. The `@lru_cache` on `get_settings` is the shape being
deliberately avoided — it is exactly why the off-switch needs a restart today.

## Decision 3 — how the artifact expresses "deny"

**Its only expressible output is a refusal. There is no permission in the schema and no boolean in
the module.**

`FactoryPolicy.refusals_for(reach)` returns a tuple of reasons policy objects. An empty tuple means
*"this policy raises no objection"* — which is a strictly weaker claim than *"go ahead"*, because
permission is the conjunction of every admission check and policy is one term in it.

The permissive reading is therefore **unexpressible**, not discouraged:

| Input | Answer |
|---|---|
| reach absent or empty | `("reach_undeclared",)` |
| a member outside the vocabulary | `("reach_unrecognised",)` |
| a member with no row | `("reach_not_in_policy",)` |
| a malformed document | `DomainError("factory_policy_invalid")` — no policy exists at all |
| an unknown schema version | `DomainError("factory_policy_version_unsupported")` |

Composition over a reach set is the **union** of its members' refusals, which is ADR-0009's
intersection-of-permission stated in the only vocabulary this artifact has. Adding a member can
only lengthen the result; no code path removes an element. That is checked over every
subset/superset pair rather than on an example.

**Version compatibility is an exact set, not a floor.** `SUPPORTED_SCHEMA_VERSIONS = {1}`; a
version above *or* below it is a named failure. Forward compatibility — accepting a higher version
by ignoring fields it does not know — is rejected outright: an older process meeting a newer
document would silently ignore whatever narrowing the new version introduced, which is the
permissive reading of a version skew. A new version is a coordinated change: the loader learns it
in the same commit that ships the document at it *and* the code that reads its new field.

**A malformed artifact is loud, and loud is also restrictive.** The loader raises rather than
returning a degraded policy, because a silently-empty policy is a policy that objects to nothing.
Both codes map to **503** in `main.py` (following `csrf_unavailable`): this is a fault in the
process's own configuration, not a conflict with the caller's request.

## Decision 4 — R4: the hard off-switch outranks policy, structurally

`ORCHESTRATOR_DISPATCH_ENABLED=false` remains the one-line, fail-closed answer to *"is the factory
stopped?"* Policy may only narrow what it permits. That is guaranteed by construction, in two
independent ways rather than by a later increment remembering to check the switch first:

1. **Policy has no permission to grant.** It contributes to a refusal list it can only add to. No
   value it can hold removes `dispatch_disabled` from that list.
2. **Policy cannot see the switch.** `factory_policy.py` imports nothing from `orchestrator.config`
   and names no setting. It is not that it declines to override the switch; it has no access to it.

Both are pinned by tests, and the first is pinned by a detector that is itself shown firing on a
deliberately permission-shaped control — otherwise "no permission found" is satisfied by a detector
that finds nothing.

## What the rows deliberately do not contain

The rows carry `rationale` and `decided`, and no policy fields. **Each of the four policies lands
its field in the increment that ships the code reading it**, as an additive schema-version bump.

This is the repo's own standing rule — *every task lands its mechanism and at least one production
caller in the same commit* — applied to data. A `lease_seconds = 900` in this file today would be a
second copy of the fifteen minutes that still lives in `services/claims.py`, with nothing reading
it and nothing keeping the two equal; that is not a smaller version of the single-source guarantee
in §4.4, it is a violation of it. "Define now, wire later" has bitten this repository three times
as code, and it is the same defect as data.

What Increment 2 *is* the single source of is the **set of reach values policy speaks about, and
why**. That is enforced, not asserted: the loader requires exactly one row per `REACH_VOCABULARY`
member and rejects a row for a member the vocabulary does not know — so the artifact is a pinned
projection of the vocabulary rather than a second copy of it, and a new reach member added without
a policy row stops the document loading rather than falling through to something lenient.

## Consequences

- Increments 3–5 read policy through `load_factory_policy()` and ask it only what it refuses. None
  of them may add a function that returns a permission; the guard will red.
- Increment 4 composes `refusals_for(...)` into dispatch admission **after** the existing checks,
  and the composition is a union. Note that reach is undeclared on every package authored before
  ADR-0009, so a refusal keyed on `reach_undeclared` would halt the factory the day it binds:
  Increment 4 owns that transition and must decide it deliberately, not inherit it.
- Changing policy requires a release, hence a restart. See the operational rule above.
- A second key (execution locus / lane) is a schema version bump, and must be named as a key rather
  than folded into reach.

## Alternatives considered

- **Extend `routing-policy.toml`.** Rejected: different repository, never read here, disjoint key.
  Measured above.
- **A second TOML in `intent-packages` beside the first.** Rejected for the same reason plus one
  more: the consumer is the orchestrator's request path, and a policy the orchestrator cannot read
  without a network hop or a vendored copy is a policy that fails open when the copy goes stale.
- **Database-backed policy.** Rejected: solves the wrong half, at the cost of reviewability.
  Measured above.
- **Environment variables**, as the off-switch and the two allowlists are today. Rejected: it is
  precisely the design whose restart-to-change property stranded a unit, it cannot carry a
  rationale or a decided date, and four policies keyed on four reach members is not a shape an
  env var expresses.
- **Cache the parsed artifact, invalidated on `stat`.** Rejected as premature: it buys nothing
  measurable and reintroduces the one failure mode this design exists to remove.
- **A `permitted: bool` per row, with the caller checking the off-switch first.** Rejected: that is
  R4 by convention. The moment a permission is expressible, precedence depends on every future
  caller remembering an ordering rule, and this programme's recurring defect is exactly the guard
  that depends on someone remembering.
