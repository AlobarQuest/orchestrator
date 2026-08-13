# ADR-0022 — The watcher owns outcomes

- **Status:** Accepted
- **Date:** 2026-08-13
- **Decided by:** Devon
- **Relates to:** ADR-0019 (increment 2 built the watcher), ADR-0020, ADR-0021, and Phase-3
  exit criterion 1

## Decision

**The rollout watcher owns outcomes.** Two consequences, decided together because they are the
same division of labour:

1. **The watcher closes a change record when its landing succeeds.** The producer proposes, and
   retires what can no longer happen; the watcher records what did happen and settles the record.
2. **The watcher produces a unit-scoped observation when the landing it observed belongs to a work
   unit.**

## Why the watcher rather than the producer

**It already holds the fact.** Minutes after the estate's first autonomous deploying landing on
2026-08-13, the watcher recorded against change record 52:

```
verdict=success  production_reached=yes  attests=revision_confirmed
```

`revision_confirmed` is the strong form — production was asked and reported the merged commit,
rather than a workflow merely concluding green. Nothing else in the estate establishes that.

Giving the closure to the producer would mean **two components reading GitHub for the same fact
for the same purpose**, and the weaker of the two doing the ruling. The producer's remit is what
*may* happen; the watcher's is what *did*.

## The defect that forced the first half

Record 52 landed `change-manager#50` — merged `2ba9f7f2`, deploy green, production serving it —
and **stayed `approved`**. So the estate-landing agent kept considering a closed pull request,
whose terms answered `landing_mergeability_unknown` and `landing_head_not_current_with_base`, and
unknowns drive **exit 3**. The agent would have reported *"something could not be measured"* on
every run from that night onward.

**A permanently red signal is one nobody reads** — the estate's own stated reason for the landing
ledger's seven-day window. The control built to watch autonomous landings would have gone deaf on
its second night, and it was found by running it rather than by reading it.

Note the shape, because it recurs here: **the fact needed already existed and was already
observed.** This was never a missing capability, only a missing consumer — the same class as an
ingress with no caller, and the reason this estate asks *who calls this, and when*.

## Why the second half closes something nothing else closes

Phase-3 criterion 1 requires a real release's traceability chain to **carry a real observation**,
and `services/traceability.py` filters the observation hop on `subject_type="work_unit"`.

Measured 2026-08-12: **509 observations are repo-scoped, 39 service, 1 deployment, and 4
work-unit — every one of those four written by `orchestrator-system`, none by an external
producer.** Every unconnected producer in the census is estate-, service- or repo-scoped, so
connecting all of them adds nothing to that number. The 2026-08-06 plan warned this "does not
close as a byproduct of anything"; it was right.

**An ADR-0020 factory landing has a work unit.** An ADR-0019 estate landing does not — which is
exactly why the 2026-08-13 landing left the hop empty. So the watcher, observing a *factory*
landing's rollout, is the one producer that can honestly emit a unit-scoped observation: it is
already there, already authenticated, and already knows the unit.

## Correction, 2026-08-13 — the second half cannot fire, and the blocker is structural

Adversarial review of the implementing increment found the limit stated above is **incomplete, in
the direction that matters**. This ADR said the unit-scoped observation awaits a factory landing
into a repository that deploys. That reads as sequencing. It is not:

- the watcher only observes rollouts that have a **deploy change record**;
- the only producer of those records refuses any non-bot author
  (`src/change_proposer/cli.py:202`, keyed on account **type**, deliberately);
- factory-runner opens pull requests with a PAT on a **user** account, so GitHub reports
  `type: "User"` — measured on `intent-packages#66`, the one factory landing.

So no factory pull request can receive a deploy record, and the observation can never fire **no
matter which repository the factory lands into**.

**The same gap blocks something this ADR was not about:** the ADR-0020 factory lane into
`change-manager` dies at `change_record_absent` for exactly this reason — ADR-0019 increment 3
declined to exclude `change-manager` from the factory lane, and the lane is nonetheless closed.
Nobody had identified that.

**The filter is not simply wrong.** It exists so a human-opened pull request does not get an
auto-proposed record, which is sound. A factory pull request is neither human nor Dependabot, so
the open question is *which authors a deploy record may be proposed for, and on what positive fact
they are recognised* — not a filter to loosen. Backlogged P1 `34fbb845bc92` against
`change-manager`.

**The capability ships anyway, with the blocker named and dated.** The estate's rule against
shipping an ingress with no caller exists so a gap is *named* rather than silent; a P1 naming the
exact blocker is that naming. Holding the code instead would leave a branch rotting against a fast
main and leave the gap undocumented — and it would not have found the ADR-0020 consequence, which
only surfaced because someone tried to build on it.

## Boundaries

- **The watcher still reports and does not act.** Closing a record on an observed fact is
  recording an outcome, not remediating one. A rollback remains a mutation and remains out.
- **It closes on a FACT, never on absence.** "Production reported the merged commit" is a fact;
  "I could not read it" is not, and must settle nothing.
- **A unit-scoped observation is emitted only where a unit genuinely exists.** An estate landing
  has none, and inventing one to fill the hop would make the chain carry a fiction.
