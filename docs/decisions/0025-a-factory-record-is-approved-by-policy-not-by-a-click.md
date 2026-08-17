# ADR-0025 — A factory record is approved by policy, not by a click

- **Status:** Accepted
- **Date:** 2026-08-17
- **Decided by:** Devon
- **Extends:** ADR-0019 (deploying merges route through change-manager), ADR-0020 (the factory
  closes its own loop)

## Decision

**Deploy policy gains a version admitting `change_class: factory-delivery`.** A change record for a
factory-opened pull request is approved by conformance to that policy, as a Dependabot record
already is. **There is no per-record human approval.**

## What was actually being decided

Not "should a machine be trusted", but **where a human's judgment attaches**. A factory pull
request has already passed four human approvals before a line of it exists:

1. the intent package, through the audited CLI that emits a chain event before writing the ledger;
2. package intake — `_require_human(actor)`, a browser act;
3. the decomposition, human-only through `/review`;
4. the authority envelope per unit, bound to its exact fingerprint, naming the target repository,
   capabilities, change class and budget.

Every one is about **authority to do work**. None is about **what got written** — the diff does not
exist yet at any of them.

## Why a per-record approval was rejected

**`change-manager` has no GitHub egress.** No HTTP client of any kind. So a record approval
*structurally cannot* show a human what changed — not "does not today", but cannot, and that is the
same property that makes every change-specific term a *landing* condition evaluated by the
orchestrator instead.

And the record's other fields do not vary. Measured on the three records that landed —
`change-manager` items 51, 52 and 53, three different dependency bumps — the acceptance criteria and
rollback plan are **byte-identical** (`sha 999bbf4fa5d0` / `67b76e8cb455`). A human approving factory
records would be clicking a form whose only moving field is a pull request number.

So the approval on offer is not a review of the change; it is a re-ratification of the repository's
deployment terms, which a human already ratified once when the policy was written. Devon: *"my gut
says this is a performative approval, if done by a human."* A control that is structurally
uninformative gets clicked through, the same way a permanently-red signal stops being read.

## What is given up, stated plainly

Not a review — **a person standing at the last gate**. After this, nobody is prompted before a
machine-authored change reaches production.

**The replacement is the human-judgment acceptance criterion, and it is strictly better placed.** A
package whose work warrants reading carries one; the unit is then disqualified from the autonomous
landing lane by construction — `verifier_decided_completion`'s fifth disqualifier throws out a unit
where *"a human who decided anything at all here was in the loop"* — and a person merges it. That
puts the human at the moment the diff exists, and the verifier forces the adjudication rather than
offering it.

## A constraint discovered while deciding, which narrows the grant

**Acceptance criteria and rollback plans are keyed by REPOSITORY, not by change class**
(`Mapping[str, tuple[str, ...]]`), while `change_classes` is a flat set. So `factory-delivery`
necessarily inherits the same two criteria `dependency-update` has: the rollout's production step
concluded success, and production reported the merged commit within 600 seconds.

That is defensible — both are statements about the **deployment mechanism**, equally true whatever
changed. But it means **there is no way to require more verification of factory work than of a
lockfile bump** without restructuring the policy to key criteria on `(repository, change_class)`.
HQ asserted during the discussion that per-class criteria were available; they are not, and the
decision was taken with the correction in hand.

## The risk to watch

**The human-AC lever only works if it is used.** If no factory package targeting a redeploying
repository ever carries a human-judgment criterion, this decision means every machine-authored
change reaches production unread and the control exists only on paper.

That is an **authoring convention**, not a policy term, and it is deliberately left open here — a
sensible default would be that a factory package targeting a redeploying repository carries a human
AC unless its author deliberately omits it. Decide it against a real factory record rather than in
the abstract.

## Boundaries

- **This admits a class, not a repository.** The policy's `repositories` set is unchanged; a
  factory record for a repository not in it still refuses.
- **Every landing condition still applies** — freshness, the rollout pin, the change window, the
  pace limit. Policy approval is one admission term among several, and the weakest kind: it means
  *no objection*, never *go ahead*.
- **The estate lander is unaffected.** It asks only about `approved` records
  (`_ASK_ABOUT = {"approved"}`), so factory records entering that state join the same nightly
  report as any other, with the same suppression rules.
