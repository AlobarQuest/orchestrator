# ADR-0020 — The factory closes its own loop

- **Status:** Accepted and **IMPLEMENTED** — closed 2026-08-10 (WS-P3.7, five increments).
  Proven end to end: `intent-packages` commit `b3f1522f` merged by `alobar-sds-dispatch[bot]`
  at 14:00:28Z, the last human act being Devon's authority approval five minutes earlier, and
  recorded in the landing ledger as `factory-approved-no-deploy` with the claim checked
  against the orchestrator's durable record rather than asserted.
- **Date:** 2026-08-08
- **Decided by:** Devon
- **Reverses:** the merge prohibition, for a bounded class. **Relates to:** ADR-0016 (native
  auto-merge), ADR-0018 (the cascade), ADR-0019 (SDS-initiated deploys route through
  change-manager)

## Decision

**A work unit whose acceptance criteria were satisfied may have its own pull request merged by
the factory, when merging deploys nothing.** The landing is recorded with a new permission
basis, Devon's name for it:

> **`factory-approved-no-deploy`**

The name carries the constraint. It cannot be applied to a merge that deploys without saying
something false, which is the property a basis name should have.

## The program exit criterion this reversed, restated 2026-08-11

The Phase-2 program's exit criterion #8 read **"No automated merge path exists"** and was HELD
throughout. This ADR reversed it deliberately, for a bounded class — so after the factory merged
its own pull request on 2026-08-10 the criterion was neither met nor failing; it had stopped
describing what the estate intends.

Devon restated it 2026-08-11:

> **No UNAUTHORISED merge path exists: every automated merge names the basis that permitted it,
> and a landing with no basis is a reported finding.**

That is the property the landing ledger enforces and can prove, where the original could only be
held by absence. It is now **MET**, and the guards forbidding an unauthorised path were
strengthened rather than loosened in the same workstream — WS-P3.7 Increment 1 closed the one real
hole and shipped its exemption mechanism empty.

## Why this is a reversal, and why it is not a small one

The merge is the one step the factory has been forbidden to take, in four places:

- `merge_to_main` appears in package authority only under `requires_approval`.
- It is **not** in `RUNNER_CAPABILITIES`, so no authority envelope can grant it.
- `test_ws34_scope_guards` forbids the literals `gh pr merge`, `git push origin main` and
  `merge_to_main` anywhere in the source tree.
- `test_no_automatic_merge` forbids merge, deploy and push-to-main from every workflow file.

ADR-0016 additionally rejected reaching the same outcome through the GraphQL
`enablePullRequestAutoMerge` mutation, which contains no forbidden string and would have passed
the guard untouched: *"enabling auto-merge is causing a merge; passing a check by renaming the
verb is the validated-as-a-name-ignored-as-a-permission failure this estate keeps finding."*
That reasoning stands and applies to this change too — if the prohibition is lifted, it is
lifted **openly**, by amending the guards, never by finding a verb they do not cover.

## Why it is defensible now and was not a week ago

The framing that made it obvious (Devon, 2026-08-08) is that this is an SDS question, not a
GitHub-mechanics one. By the time the factory opens a pull request the change carries: an
approved intent package with declared acceptance criteria; an authority envelope a human
approved, bound to its exact fingerprint and write-once; eight admission terms including reach,
change class and target repository; named-check evidence **observed from GitHub** rather than
asserted by the runner; and a verifier that adjudicates the criteria against that evidence.

That is a far better-evidenced change than any Dependabot pull request, and the evidence is
about *this specific change* rather than about a category it belongs to. Several of those
properties are days old: observed-not-asserted named checks (WS-P2.20), the closed verifier
bypass (WS-P2.32), the landing ledger and its detectors (WS-P3.6). The prohibition was correct
when none of them existed.

## The constraint that makes it safe, and it is narrower than "criteria met"

**The merge is currently the only point at which a human sees the RESULT.** Authority approval
happens before the work exists — it approves the authority to act, not the outcome. Removing the
merge gate therefore makes the acceptance criteria the sole specification of "did this do the
right thing."

So the factory may merge only when **every acceptance criterion was resolved deterministically
from observed evidence, with no human adjudication.** If any criterion required a human to
decide, a human is already in the loop and the merge is theirs to make. This is checkable rather
than aspirational: `floor_for()` separates criteria that may resolve deterministically from those
that must reach a human, and `decided_by` records which actually did.

Stated as the estate's own idiom: the factory may close the loop exactly when it never had to ask.

## What must change

1. **Amend the guards openly.** `test_no_automatic_merge` and `test_ws34_scope_guards` must gain
   a named, reasoned exception rather than a workaround — and the exception must be narrow enough
   that it cannot cover a deploying merge.
2. **A capability.** Merging must be an authority term a human approves per unit, in the envelope,
   the way every other capability is. It must not be an ambient property of the factory.
3. **The refusal.** A unit whose target repository deploys on merge is refused, by reading the
   landing classification the estate already has — never by a repository list.
4. **The basis.** `factory-approved-no-deploy` joins the ledger's permission vocabulary, carrying
   the unit id, the package revision, the criteria and how each resolved. Recording it as `human`
   would be false, and recording it as `auto_merge_rule` would be false in a different way.

## The boundary

**Anything that deploys is out of scope** and stays out until ADR-0019's change-manager routing
exists. That is not a sequencing convenience — it is the same boundary the basis name encodes.
`change-manager` and `brain` are the two repositories affected; both are already excluded from
the Dependabot lane for the same reason.

## What this does not remove

The human authority approval stays, and it is the gate that matters: it is where a person decides
what the factory may do, to which repository, with what budget. This decision removes a *second*
human touch on the same change — one applied after the fact, to a result the acceptance criteria
already specify, and which Devon has been explicit adds nothing he can meaningfully exercise.

## Risk accepted, stated plainly

The quality of acceptance criteria becomes load-bearing in a way it was not. Today a weak
criterion is caught by the human at the merge; afterwards it is not caught at all. Criteria are
authored by an agent and approved by a human at intake, so the gate moves earlier rather than
disappearing — but it moves to a point where the work does not yet exist and the criterion is
harder to judge. That is the real cost of this decision and it should be revisited if a weak
criterion ever admits a bad change.
