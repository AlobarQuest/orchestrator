# G1 + G2 — the signal→work contract

**Design document, 2026-09-18.** Decided with Devon in session. Settles the Phase-3 plan's entry
gates **G1** (the signal→work correlation contract) and **G2** (the proposed-package lifecycle) as a
written contract, and makes the one running lane conform to it.

- **Relates to:** ADR-0026 (the signal→work record), ADR-0027 (a machine may register an intake),
  ADR-0028 (a standing package per repository, revised per bump), ADR-0039 (external content may
  observe but never propose), ADR-0021 (two signal ledgers)
- **Precedes:** G3 (the minimum proposal-envelope trust contract), then WS-P3.2 (change-manager
  app-conformance handoff → proposed package). Each gets its own spec, plan and implementation cycle.

## 1. Why this exists, and what it is not

The programme plan records G1 and G2 as *"demonstrated once, not yet contracted"* — a second producer
has a working example to copy and no specification to conform to. This document is that
specification, plus the change that makes the existing producer satisfy it.

It is **not** a new design. ADR-0026 decided six things on 2026-08-18 and every one of them stands.
What this adds is the written contract, the parts of ADR-0026 that were never built, and a mechanical
check that holds both sides of the boundary together.

**Scope note, dated rather than permanent.** Today an observation is a *cause* a decision names, not
a source of work: ADR-0039 rules that external content may observe and never propose. That boundary
is expected to move as the estate grows the capability. It is recorded here so the contract's first
clause is not misread as reopening it, and ADR-0039 is where a change to it gets decided.

## 2. The measurement

ADR-0026's six decisions, against what exists on 2026-09-18:

| ADR-0026 decision | built? |
|---|---|
| 1. The decision-needing record lives in change-manager | yes (ADR-0028) |
| 2. The correlation is keyed on the observation `id`, referenced from both sides of the join | **neither half** |
| 3. The link is carried on the package revision | yes — `change_record_id` |
| 4. A withdrawal may invalidate a record, never rewrites history past approval | prose only; no mechanism exists |
| 5. The carry from an approved record into intake | yes — `work_carrier` |
| 6. OBSERVER gains propose | **moot** — decision 1 moved proposing into change-manager |

Three measurements support the decision-2 row:

- **The observing lanes and the proposing lanes are disjoint sets.** Seven lanes post observations
  (`activation_sweep`, `deploy_watcher`, `landing_ledger`, `pin_watcher`, `reconciliation_runner`,
  `revision_watcher`, `tool_installer`). Two lanes propose change records (`bump_proposer` →
  `work`, `change_proposer` → `deploy`). No lane is in both sets.
- **change-manager's `change_items` has no observation reference.** Its `deploy_observations` table
  is a different subject — ADR-0022 rollout facts — not a link to a causing observation.
- **`bump_proposer`'s proposal payload is** `package_id`, `package_revision`,
  `package_source_repository`, `risk`, `reasoning`, `actor`. The cause is prose in `reasoning`.

So the chain today begins at a change record, and G1's subject — the canonical relationship binding
an *originating observation* to the work it causes — is neither contracted nor demonstrated.

Two further facts bound what the contract can require:

- **The traceability observation hop is unit-scoped.** `services/traceability.py` filters on
  `subject_type="work_unit"` and the unit id, so an observation about a repository — what every
  signal producer emits — cannot appear in the chain it caused.
- **`TraceabilityAnchor.kind` has no `observation` and no `change_record` member.** The chain is
  answerable from the work end and not from the signal end.

## 3. The contract

Seven clauses, covering both gates because G1 and G2 are one record.

**C1 — cause.** Every machine-proposed change record names the observation that caused it, by
observation id. This is ADR-0026 decision 2 restated as a requirement.

**C2 — producer duty, and its order.** A producer that proposes first observes: it posts the fact as
an observation, then proposes naming that observation. The order is load-bearing. The observation is
the durable fact and the record is a decision about it, so a record naming an observation that does
not exist is a dangling cause with no repair — observations are append-only, with no supersession
model and no delete route.

**C3 — carry.** The link is carried forward at each durable artifact, never reconstructed by joining
backward. A package revision carries both `change_record_id` and `originating_observation_id`, and a
new revision inherits both explicitly rather than by accident. C3 reaches only as far as work does: a
`deploy`-source record never becomes a package revision, so for that lane the chain ends at the
record, which C1 still requires to name its cause.

**C4 — lifecycle.** A proposed record's states are change-manager's: `pending` → `approved` →
`resolved` | `wontfix`, with `resolved` and `wontfix` terminal. `PROPOSED_SOURCES` carries the three
properties that distinguish a proposed record from a derived one — asserted once rather than
re-derived, no authorized executor, withheld from an unfiltered listing. The contract names these
rather than inventing them.

**C5 — withdrawal.** A withdrawal may invalidate a record before approval. It never rewrites history
past approval: a unit in flight is handled by the unit's own lifecycle, and landed work stands. This
is a rule awaiting a mechanism — see §8 item 2 — and is recorded as such.

**C6 — query.** The chain is answerable from both ends. The originating observation reaches a chain
through the **intent** hop. The observation hop stays unit-scoped.

**C7 — authority.** Proposing happens in change-manager under its `propose` scope. ADR-0026 decision
6 — *"OBSERVER gains propose"* — is **retired**: it became moot when decision 1 placed proposing in
change-manager, and an accepted-and-unbuilt decision invites a future session to build a propose
route nobody needs.

### Reach of the contract

C1 binds **every machine-proposed change record**, both proposed sources. One uniform rule means a
second producer conforms without judging its own case, which is the judgment an entry gate exists to
remove. The alternative considered and rejected — requiring an observation only where the cause is
not otherwise addressable — leaves each producer classifying its own cause, and a wrong
classification is silent.

## 4. The record and the join

**change-manager side.** `change_items` gains `originating_observation_id`, and both `WorkChangeIn`
and `DeployChangeIn` gain the field. change-manager stores it without verifying it: it has no egress
and cannot ask whether the observation exists, exactly as it cannot verify the package locator a work
record names. `WorkChangeIn`'s own docstring states the principle — *"a registrar that inferred what
it was told could not later be checked against the thing that told it."*

**Orchestrator side.** `PackageIntakeRegistration` and `work_package_revisions` gain
`originating_observation_id` beside `change_record_id`, and `TraceabilityIntentHop` declares it.

Carrying the id onto the revision as well as onto the record is a deliberate extension of ADR-0026
decision 3, and the slot was left open for it. That field's own comment reads: *"It belongs on the
intent hop because the revision is where the link is stored — an observation would not do, because
the observation hop filters on `subject_type="work_unit"`, so a revision-scoped observation never
reaches any chain."* Every branch of `resolve_anchors` is a local database query and the orchestrator
has no egress, so without the second carry, *"what work did this signal cause?"* needs a hop across a
boundary the orchestrator cannot cross.

**Ordering is forced.** Both change-manager schemas are `extra="forbid"`, so a producer that sends
the field before change-manager declares it gets HTTP 422 and proposes nothing. change-manager ships
first; the producers follow. This is the same merge-first rule factory-runner already imposes, in a
repository that has never carried a cross-repo pin.

## 5. The producer duty, and the retrofit

`bump_proposer` posts an observation before it proposes, and passes that observation's id on the
proposal.

**Vocabulary.** `subject_type: "repo"` fits. Neither `OBSERVATION_SOURCE_SYSTEMS` nor
`OBSERVATION_TYPES` has a member for this producer, so each gains one, with a written rationale for
why the near miss is wrong — the pattern every preceding lane followed (`pin_watcher` over `github`,
`tool_installer` over `machine_activation`). The cost is a migration touching both the model's CHECK
constraints and the frozen copy inside the migration, since migrations inline the vocabulary rather
than importing it.

**Intake replay.** `_matches_legacy_shape` compares a stored event against the expected payload and
carries three legacy exemptions, each firing **only when the stored event actually lacks the key** —
its docstring explains that an unconditional exemption breaks an event that legitimately carries it.
`originating_observation_id` is a fourth clause built the same way, scoped for the same reason:
intakes registered before the key existed.

**The carry.** `work_carrier` hands `change_record_id` to `emit-intake-payload` and then refuses a
payload whose value disagrees with the record. The observation id travels the same path and draws the
same refusal, so a carry cannot file work under the wrong cause.

## 6. The query

`_parse_traceability_anchor` builds anchors from `_ANCHOR_BUILDERS` under an exactly-one-anchor rule.
An `observation` kind is one builder, one query parameter, and one branch in `resolve_anchors`
resolving through the revision to its units. `TraceabilityIntentHop` gains the field in the same
change: a FastAPI `response_model` silently drops any key it does not declare, so the model and the
service move together or the consumer receives nothing.

**The observation hop stays unit-scoped, deliberately.** The originating observation reaches a chain
through the intent hop, never by loosening `subject_type="work_unit"`. That filter is what keeps the
hop meaning *observations about this unit*; widening it to catch the cause would put every
repository-scoped observation into every chain touching that repository. This is stated in the
contract because the intent hop's own comment shows it is exactly where a later reader reaches for
the wrong fix.

## 7. Enforcement

One script, following `scripts/check_brief_consumer_compatibility.py`: fetch `app/schemas.py` from
change-manager at `main` through the GitHub contents API, AST-parse `WorkChangeIn` and
`DeployChangeIn`, and fail if either fails to declare a field the orchestrator's producers send or
`work_carrier` reads. change-manager is a public repository, so `github.token` reaches it.

The parse is pinned against the local models the way the brief check pins its parser against
`RunnerBriefResponse.model_fields`, so a parser that stopped agreeing with pydantic is caught before
it can vet anything wrongly.

**Where it runs.** A step in the existing `Quality` job. This creates no new required status context,
and so avoids the paired-rename trap the `Runner consumer compatibility` job already carries. The
trade: the existing pattern isolates such checks into their own jobs, which buys diagnosis rather
than availability — those jobs are required contexts too — so folded into `Quality`, a GitHub API
failure reds the main gate and the log is what says why.

**The residual, recorded in the script.** It vets what `main` declares, not what production serves:
`change-mgr.alobar.net/openapi.json` answers 302 behind forward auth, so the deployed surface is not
readable. change-manager redeploys on push to `main`, so the two converge in minutes, and a producer
sending the field inside that window gets a 422. This is the estate's *merged is not deployed* rule
pointed at the check itself.

## 8. What the contract ships knowing

1. **`change_proposer`'s deploy lane is non-conformant.** It posts no observation and carries no id.
   Named here rather than discovered later, and visible to the check from the day it ships.
2. **C5 has no mechanism.** Observations are append-only with no supersession model and no delete
   route, so nothing can invoke a withdrawal. The clause states a rule, not behaviour.
3. **Decision 6 is retired**, not deferred.
4. **No `change_record` anchor.** The observation anchor answers the signal question; a record anchor
   is a sibling nobody has asked for, and a surface added on speculation is a surface to maintain.
5. **G3 and WS-P3.2** are the next two cycles, in that order, each with its own spec.

## 9. Decisions taken in this design

| # | decision |
|---|---|
| 1 | The contract ships **with the one running lane made to conform** — `bump_proposer` posts the observation, change-manager carries the reference, traceability gains the anchor. A contract no producer satisfies is a fourth accepted-and-unbuilt item |
| 2 | C1 binds **every machine-proposed change record**, not only those whose cause is otherwise unaddressable |
| 3 | The observation id is carried **onto the revision as well as onto the record**, so the chain is answerable from the signal end without crossing a boundary |
| 4 | Enforcement is a **document plus a cross-repo field check**, following the estate's five existing check scripts, inside the `Quality` job |
