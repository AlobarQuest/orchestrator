# ADR-0009 — Policy is keyed on REACH: a declared, multi-valued set, not on `change_class`

**Status:** Accepted · **Date:** 2026-08-01 · **Workstream:** WS-P2.18 (Increment 1)

## Context

WS-P2.18 replaces four scattered policies with one versioned artifact. Two of them — the change
window and the per-key lease duration — must be keyed on something, and the human gate needs the
same fact to display. The spec (§3) required that key be decided once, before anything is built,
because getting it wrong means rebuilding two policies and a display field.

The obvious candidate is `change_class`, which already exists on the authority envelope and already
routes model selection in `routing-policy.toml`. It is the wrong key.

## Decision

**Policy is keyed on `reach`: what the work touches when it runs.** It is

1. **declared** by the package author in a top-level `reach` list on `package.yaml`, never inferred;
2. **a set**, not a single value;
3. drawn from a **closed four-member vocabulary** owned by one module,
   `src/orchestrator/reach_vocabulary.py`;
4. **validated at intake** and carried verbatim into the enforcement snapshot; and
5. **absent-means-unknown**, never absent-means-nothing.

The vocabulary:

| Member | What it means |
|---|---|
| `source_repository` | Writes land in a git repository and nowhere else. Nothing outside it changes until something separately acts on the result. |
| `live_estate` | Something already serving is changed: a Coolify application or database, the VPS, DNS, or the orchestrator itself. |
| `external_system` | State changes in a service outside the estate — a secrets console, a registrar, a task tracker, a listing service — which this estate cannot put back on its own. |
| `operator_machine` | The work runs on, or writes to, the operator's own machine: its keychain, its scheduled jobs, its agent configuration, its local checkouts. |

A unit may be admitted only when **every** member of its package's reach is permitted. Composition
is intersection-of-permission, so adding a member can only ever narrow admission.

## Why `change_class` is the wrong key — measured, not argued

Validated against the real population of 24 authored packages (`tests/fixtures/reach_census.json`,
taken 2026-08-01), not against examples:

- **`software-delivery` is not one reach, it is three.** Ten of its twelve packages reach only a
  repository. `ws-2.4-brain-approver-gate` also restarts four running MCP services (it is the one
  package in the whole population whose release target is non-null — the other eleven are null).
  `ws-2.3-intent-authoring-skill-v2` targets `claude-control-plane` — the `~/.claude` repository,
  where landing a commit *is* changing the operator's machine, with no release step in between.
  Same class, three blast radii.
- **`non-software-operational` is not one reach either.** `wsp213-bws-machine-token-rotation`
  reaches an external console *and* the operator's keychain and scheduled jobs, while
  `ws-2.4-historical-listing-launch` reaches neither, producing an evidence pack into a local
  repository and publishing nothing.
- Five of the 24 packages reach two places at once. A single-valued key forces a false answer on
  every one of them, and the false answer is always the *less* restrictive one, because the
  repository half is the part that is easy to see.

The spec's §3 table asserts that software-delivery work touches "a repo's `main`" and needs no
window. That is true of eleven packages and false of two, and the two it is false about are exactly
the ones a window exists to catch.

## Why declared, not inferred

R8. Every inferable signal in the population is unreliable in the same direction — toward
permissive. The software-delivery release-target field is null on twelve of thirteen packages
including ones that plainly reach a running service; `profile` cannot distinguish the two
`non-software-operational` cases; the repository name cannot know that one repository is the
operator's machine. An inference built on any of these is a quiet wrong answer. A declaration is a
loud one when it is wrong, and it is a commitment somebody made.

## Why a set

Because the population contains sets. See above. The secondary benefit is that composition is
trivially fail-closed: a package that reaches two places must satisfy both policies, and no
ordering or precedence rule is needed.

## Why absence is unknown

Fourteen packages predate this field and their YAML is hashed into lineage approvals, so it cannot
be edited. Absence is therefore a permanent state of the record. Treating it as an empty set would
read as "reaches nothing" — the most permissive possible claim — for exactly the packages nobody
has ever classified. `reach_from_snapshot` returns `None`, `reach_statement` returns `None`, and the
human gate keeps rendering its existing explicit unknown.

## Where the vocabulary lives, and why there is only one copy

`intent-packages` accepts the `reach` key and checks its **shape** (a non-empty list of non-empty
strings). It deliberately does **not** enumerate the members. Membership is owned solely by
`reach_vocabulary.py`.

This repo has paid three times for a vocabulary with two copies. Here both drift directions are
loud and neither is silent: a member the orchestrator does not know fails intake with a named
`reach_invalid` error, and a key the authoring side does not accept fails `factory validate`. There
is nothing silent between the repositories, so there is nothing to byte-pin.

## Relationship to the existing "what it affects" fact

WS-P2.17 already renders "what it affects" at both gates, as an open-map read-back of the authority
envelope's constraints in the author's own words. Reach does not replace it and is not a fourth
fact. The two are the same question at different resolutions: reach is the closed classification
policy can be keyed on, the envelope half is the specifics. The gate renders reach first, because
it is the sentence that survives being skimmed.

One consequence is a real improvement at the intake gate, where "what it affects" was previously
always an explicit unknown — no units exist yet, so no target repository has been chosen. A package
that declares its reach has answered the question before decomposition begins.

## Alternatives considered

- **Key on `change_class`.** Rejected: measured above. It is also already load-bearing for model
  routing, and overloading it would couple two unrelated policies.
- **A single-valued `reach`.** Rejected: five of 24 real packages need two members, and the forced
  answer is always the permissive one.
- **A separate `orchestrator_self` member** for self-update. Rejected: the orchestrator is part of
  the live estate and the spec (§7) already keys self-update on **reach and lane**. A member whose
  only distinguishing property is expressed by a second dimension is a duplicate of that dimension.
- **A `nothing`/read-only member.** Rejected: the one candidate package
  (`ws-2.4-historical-listing-launch`) still writes its evidence pack on the operator's machine, and
  work that reaches nothing needs no window, no lease and no gate. A member no package needs is
  decoration.
- **Reuse `profile_fields.blast_radius`** (the infrastructure-change profile's existing
  `single-app` / `shared-service` / `portfolio-wide`). Rejected: it exists on one profile of five,
  it grades *how much* of the estate is touched rather than *which kind of thing*, and it cannot
  express the operator's machine at all. It answers a different question and remains useful for it.
- **A first-class column** on `work_package_revisions`, as WS-P2.8's `follow_up` has. Rejected for
  now: `follow_up` needed a column because a scheduled pass queries revisions by it. Every reach
  consumer already holds the revision, so the snapshot carry the spec called for is sufficient, and
  it needs no migration.

## Consequences

- Increments 4 and 5 read reach through `reach_from_snapshot`; that is the single reader.
- An unknown reach must fail toward the most restrictive policy at every consumer. Increment 1
  establishes this only for the display, where the existing explicit-unknown rendering already does
  it; each later consumer owes its own proof.
- A new member is a vocabulary change in one module plus a census entry, and the census asserts every
  member is load-bearing — a member no real package needs cannot be added silently.
