# ADR-0029 — Both record sources close both directions

- **Status:** Accepted
- **Date:** 2026-08-20
- **Decided by:** Devon
- **Relates to:** ADR-0022 (the parent — the deploy source's success direction), ADR-0019
  increment 5b (its failure direction), ADR-0026 (the work record), ADR-0027 (the carry)

## Context

A change record stands for something that should happen. When it has happened, or has become
impossible, something must close it — or the record goes on asking for work that is done, and
"what has been approved but not yet built?" has no correct answer.

The `deploy` source closed both directions and the `work` source had neither:

| direction | deploy source | work source |
|---|---|---|
| the subject went away | `deploy_retirement.py` (ADR-0019 inc 5b) | — |
| the change happened | `deploy_settlement.py` (ADR-0022) | — |

Measured 2026-08-19 on production record **61** (`infraops-mcp-server-npm-eslint` revision 1): its
work unit completed and `infraops-mcp-server#81` merged, and the record still read `approved` the
next morning. Its whole machine lifecycle — unit registered to completed — took **5 minutes 33
seconds**; nothing closed the record in the fifteen hours after.

Two consequences. The carry re-selects the record on every pass forever, and once
`intent-packages`' `main` moves past the commit its idempotency key was minted against, the
orchestrator answers 409 `idempotency key belongs to a different operation` — correctly, and
permanently, so the pass reports a finding every morning. That is the loud half. The quiet half is
the reason to fix it: an approved-and-done record is indistinguishable from an approved-and-waiting
one.

## Decision

**A machine may close a `work` record on the one fact that its work was built**, through
`POST /api/items/{item_id}/work-retirement` — `work` records only, observation vocabulary
`{work_unit_completed}`, outcome `resolved`, already-terminal is a replay rather than an error.

**The rule that picks the mechanism is: who holds the fact, and can the deciding server derive
it?** That is the general statement this document exists to make, so the next pipeline does not
re-derive it:

- The deciding server can derive the fact → **no route**. It computes the outcome from
  coordinates it already holds and refuses them as caller fields. That is a settlement.
- Only the caller can see it → **a route**, narrow in three ways at once: one source, a closed
  observation vocabulary whose outcome cannot be chosen, and one reachable status. That is a
  retirement.

The work source's success direction is **retirement-shaped even though its direction is
settlement's**, and that is the non-obvious part. change-manager cannot derive "the work unit
completed": it has no orchestrator egress, by design, and giving it one would make a foreign
service's outage a refusal to record decisions a person had already made. So the fact must be
declared, and the shape follows the derivability rather than the direction.

**Why a machine may move a status a human set.** ADR-0019 increment 5b's argument, unchanged:
the difference is DIRECTION. Every reachable outcome removes permission. A caller that lied would
stop work that was going to be carried anyway and could not cause any — the record becomes
terminal, `identity` stays held so no fresh proposal can take it, and `resolved` is not a status
the carry selects on. Availability, not authority.

**Completion, not settlement, and the vocabulary has one member because of it.** ADR-0022 chose
the strong form of its fact (`revision_confirmed`, refusing `rollout_unverified`); the analogue
here is choosing the *narrow* fact. A `failed` unit may still be retried, so closing on "no longer
in flight" would terminate a record whose work is live. A `cancelled` unit is a human's decision
and the matching record decision stays a human's — that is what happened to record 60, set to
`wontfix` by hand. `resolved` rather than `wontfix` because the work was done, which is not a risk
anybody accepted.

**All units of the revision must be completed, and there must be at least one.** A record names a
package revision, which may decompose into more than one unit, so the rule is stated rather than
left to whatever an implementation does first. The orchestrator derives it — see below — and names
the boolean `all_units_completed` rather than anything that reads as a synonym for "settled",
because the narrowness is the point.

### The orchestrator derives the fact; the producer only relays it

The completion fact lives in the orchestrator and nowhere else, and until now nothing could get
from a change record back to it: there is no listing route and no lookup by `change_record_id`.
`GET /api/v1/change-records/{change_record_id}/work` closes that, answering with the matching
revisions, their units and their states, **and** the derived boolean.

The states travel alongside the verdict deliberately. That is not redundancy — it is the evidence
for the verdict, and it is what makes a wrong answer diagnosable rather than merely wrong.

ADR-0022 is **silent** on this choice and should not be cited for it. There, the deciding server
derived the fact and refused it as a caller field; here the deciding server cannot derive it under
either design, which is the whole reason this is a route. What selects the derived verdict is
narrower: the rule belongs to the system that owns the units, so no producer can get the reduction
wrong and no *second* producer can implement it differently. There is one today; the entry gates
exist because there will be more.

### The component that goes and gets it

ADR-0022 gave closure to the component that already held the fact. The work lane has no such
component — nothing local holds unit completion — so one had to exist. `work_watcher` is a
distinct module with its own change-manager client allowlisting exactly the retirement route, run
from the carry's launcher as a phase rather than as a fourth scheduled job.

Remit is a property of the **module and its asserted client surface, not of the process** — the
pattern `bump_proposer/change_manager.py` already records, where two programs hold the same scope
and each asserts a narrower surface. So `work_carrier/change_manager.py` keeps its "no write path
at all" claim intact, and the widening is confined to the new module.

**The retirement phase runs BEFORE the carry**, and that ordering is load-bearing. The carry
selects on `status=approved`; if it read the listing first, a finished record would still be in it,
would be re-registered, would draw the 409 the change exists to remove, and only then be retired.
Retirement-first means the record leaves the queue before the listing is read.

### The scope

The route joins `propose`, alongside the deploying-merge retirement, rather than taking a scope of
its own. **This widens two credentials that already exist** — `change_proposer`'s and
`bump_proposer`'s — which is precisely why the grant is recorded here rather than left inferable
from a docstring: a widening with no new scope has nothing an operator would notice.

A fourth scope was considered and rejected as machinery that buys process isolation on top of a
confinement the module allowlists already provide, at the cost of a secret, a Coolify env write and
a credential nobody holds until somebody mints it.

## Consequences

- The work lane's records close themselves on the one fact that they are done. Record 61 is the
  first subject.
- `propose` is a wider scope than it was. Confinement now rests on per-module allowlists, which is
  a property asserted in code and tested, not on the scope table alone.
- The `cancelled` / `wontfix` direction stays human, deliberately. Record 59, which reached a
  terminal state it should not have, stays as it is: back-dating a judgment about it is the mistake
  ADR-0014 names.
- Nothing un-retires. A record retired against a completed unit whose work is later found wanting
  is a person's decision to reopen, and `reactivate` is guarded to `wontfix` — a known limit, not
  one this document changes.
