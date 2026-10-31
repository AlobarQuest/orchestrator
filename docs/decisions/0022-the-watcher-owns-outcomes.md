# ADR-0022 — The watcher owns outcomes

- **Status:** Accepted
- **Date:** 2026-08-13
- **Decided by:** Devon
- **Relates to:** ADR-0019 (increment 2 built the watcher), ADR-0020, ADR-0021, and Phase-3
  exit criterion 1

  **INCREMENT 1 DONE 2026-08-13** — change-manager #55 (`9ee5b433`, which deployed itself; rollout
  green through *Verify the new revision is live*, production confirmed serving it) and
  orchestrator #163. **Production record 52 settled on the first watcher pass after the deploy**,
  by a REPLAY that wrote no observation row: `approved → resolved`, actor `rollout-observation`,
  *"landed and its rollout was observed to succeed with production reporting the merged commit;
  settled against that observation rather than by any caller's decision."* Its chain now reads
  proposed → approved by policy → re-approved under v2 → rollout observed → settled. The executor
  is still refused, replayed against the deployed build with the FULL bearer: `claim`, `outcome`
  and `handoff` all 409 *"no authorized executor"*, `approve` 409 *"approved by policy conformance,
  not by a caller"*, and its own `?status=approved` query returns 0 deploy rows.

  **THIS DOCUMENT'S URGENCY CLAIM WAS WRONG, and it was wrong in the direction that matters.**
  It says the estate-landing agent "would have reported *something could not be measured* on every
  run from that night onward" because record 52 stayed `approved`. Measured from the agent's own
  first launchd log, before any code was written: record 52 was already classified **`settled`**
  (`estate_lander/cli.py`'s `_SETTLED` catches `landing_already_recorded` and
  `landing_pull_request_not_open`) and contributed no finding. The exit 3 comes from three OTHER
  pull requests held on real conditions. Confirmed live after the deploy: record 52 has left the
  report entirely — *3 considered, 0 settled* where it was *4 considered, 1 settled* — and the exit
  code is **unchanged at 3**. Settling it was right for this document's own lifecycle argument, and
  for no urgency reason. What would move that exit code is a decision about whether a pull request
  held on `landing_pace_exhausted` or `landing_outside_change_window` is a finding at all.

  **THE SECOND HALF CANNOT FIRE, and the remaining condition this document names is not the real
  one.** It says the criterion closes when a factory landing goes into a repository that deploys.
  It does not: the watcher only sees rollouts that have a deploy change record, the sole producer
  of those records refuses non-bot pull requests, and factory-runner opens pull requests with a
  **User** account. So no factory landing can have a record for the watcher to look at — which is
  also why the factory lane into `change-manager` is itself blocked at `change_record_absent`.
  Devon's ruling: ship the capability with the limit named, and open the producer gap as work
  (backlog P1 `6a98cb85fbae`). **Phase-3 exit criterion 1 is NOT claimed closed.**

  **Whoever picks that P1 up: the bot filter is not simply wrong.** It exists so a human-opened
  pull request does not get an auto-proposed record, which is sound, and it is keyed on account
  **type** deliberately rather than on a `[bot]` login suffix. A factory pull request is neither
  human nor Dependabot. So the question is *which authors a deploy record may be proposed for, and
  on what positive fact they are recognised* — a work-unit reference in the body, a label, or
  opening as the Dispatch App — not a filter to loosen. The Dispatch App option is the only one
  that makes the identity **true** rather than making the filter looser, and it is also the one
  that touches `FACTORY_PR_TOKEN`, which has broken twice.

  **Adversarial review returned four kills across three reviewers, two of them found
  independently by more than one.** The sharpest was measured against a migrated database rather
  than argued: the unit-scoped observation copied the landing ledger's non-content-addressed
  `source_reference`, which is right for a commit on a branch and wrong for a rollout — the first
  re-run would have raised `observation_conflict`, with no supersession route, wedging the hourly
  pass at exit 3 **permanently** while the successful attempt was never attributed to the unit.
  The estate's own rule about copying a pin (*transfers the MECHANISM, not the PROPERTY*),
  rediscovered in a third artifact. Also killed: the launcher's two BWS identities collapsed into
  one whenever `BWS_ACCESS_TOKEN` was already exported, and **no** value of it worked; and this
  document's safety argument for letting an `observe` credential move a status rested on
  `deploy_watcher recheck` re-deriving the settled-on facts, while `workflow_attestation` — the
  field the strong-form clause tests, caller-supplied and unvalidated — was absent from what that
  command compared. 42/42 mutations killed across both repositories on the final trees.
  Report: `~/docs/software-delivery-system/2026-08-13-watcher-closes-the-loop-build-report.md`.

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

> **Superseded — read the increment-1 annotation at the top of this file.** The urgency this
> section asserts was measured to be false: record 52 was already classified `settled` by the
> estate-landing agent and contributed no finding. The closure below was still right, for this
> document's lifecycle argument and not for any urgency reason.

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

## Boundaries

- **The watcher still reports and does not act.** Closing a record on an observed fact is
  recording an outcome, not remediating one. A rollback remains a mutation and remains out.
- **It closes on a FACT, never on absence.** "Production reported the merged commit" is a fact;
  "I could not read it" is not, and must settle nothing.
- **A unit-scoped observation is emitted only where a unit genuinely exists.** An estate landing
  has none, and inventing one to fill the hop would make the chain carry a fiction.

## Amendment 1, 2026-10-31 — a superseded failed rollout retires its record

**Decision (Devon, 2026-10-31):** a machine may close a landed record whose failed rollout a later
green rollout has passed. This narrows the rule above that a failed rollout settles nothing: the
failed run still never settles a record, and a later green run ahead of it now retires one.

Nine records had stayed `approved` for this reason alone (items 78, 79, 89, 94, 104, 115, 117, 118
and 125; `brain#59` to `#89`). Each landed with `verdict=failed` and later rollouts passed.

How it works:

- `deploy_watcher` calls `POST /api/items/{id}/deploy-supersession`, in change-manager's `observe`
  scope. The route is separate from `deploy-retirement`, which belongs to `propose`: adding that one
  to `observe` would let the watcher assert `pull_request_closed_unmerged`.
- The watcher states coordinates: the pull request, the superseding run and its commit. It sends no
  status, and the server's only outcome is `resolved`.
- The server refuses unless its own reduced observation exists, isn't a success and names a
  different commit. It does not check that the run was green or that the commit is later. The
  watcher carries that condition, so it calls only with what `superseded_by` established (ADR-0044's
  four clauses), for a merged pull request, in a non-dry pass, after the observation is recorded.
- A merge divergence retires nothing: the rows disagree about which landing they describe.
- A 409 is a finding, `change_manager_refused_the_retirement`.

The boundary above still holds in its substance: the watcher records an outcome and remediates
nothing. A rollback remains a mutation and remains out. `resolved` is terminal, so a wrong retirement
can't be undone by a machine, which is why every doubt in the predicate withholds the excuse.
