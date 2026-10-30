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

## Amendment 1 — 2026-10-28 (bridge layer Part 3)

**Status:** Accepted (Devon's decisions in `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`,
Part 3; Rulings 1-2 of `docs/superpowers/plans/2026-10-28-bridge-p3-automatic-bumps.md`).

Policy approval reaches the `work` pipeline, for one shape of record only. change-manager approves a
`work` record by its work policy, as actor `work-policy`, when the record is a standing
dependency-update package: `change_class` `dependency-update`, `package_source_repository`
`AlobarQuest/intent-packages`, risk `caution`, and a `target_repository` on change-manager's own
inert list. Every other `work` record (a rotation, an app change, a deploying repository, a record
missing either field) stays pending for a person.

- **Inert repositories only (Ruling 1).** No repository whose default-branch landing redeploys is
  reached; that waits for holds (Part 4).
- **Keyed on the inert list, not the grant (Ruling 2).** change-manager can't read the SDS member
  record. `membership_check` reports any inert repository the record does not grant
  `dependency_updates`, and dispatch admission checks the grant again, so a record for a repository
  whose grant was withdrawn is approved and then refused at admission.
- **The source-level property is unchanged.** `work` is not added to the sources approved wholesale:
  a source-wide policy would make rotation and app-change records unapprovable by anyone. The policy
  is per record.

The producer is `bump_proposer`, which sends `target_repository` and `change_class` on every
proposal. Neither field is asserted, so a replay that adds them to an earlier pending record changes
no stored payload and lets the policy approve it.

## Amendment 2 — 2026-10-28 (bridge layer: repositories that deploy)

**Status:** Accepted (Devon's decisions in `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`,
Part 3, "Coverage that widens: Repositories that deploy"; Rulings 6 and 7 of
`docs/superpowers/plans/2026-10-28-bridge-widen-deploying.md`).

change-manager's work policy now approves a standing dependency-update record whose
`target_repository` is in its inert **or deploying** population; one in neither is refused
`target_repository_not_in_policy`. The deploying population is the policy's top-level
`repositories` (brain and change-manager, measured 2026-10-28). The producer is still
`bump_proposer`, which now creates packages for deploying repositories with reach `[live_estate]`
(ADR-0028 amendment 3).

**"The risk to watch" is amended, not ignored (Ruling 6).** That section feared that a
machine-authored change to a redeploying repository would reach production unread unless its
package carried a human-judgment criterion. A standing dependency-update package carries none, so
for these records the human-AC lever is unused by construction. Two human gates still stand
between the change and production:

1. **The decomposition approval.** A person approves the units and their commands before any work
   starts (`docs/operations/driving-a-unit.md`).
2. **`pr_merge_admission`'s authority term.** The factory merges only when a human authority
   approval is bound to the unit's exact current fingerprint
   (`PackageRepository.exact_authority_approval(unit)`, `pr_merge_admission.py` module docstring
   and `admission_for`). The term is unconditional: the known-good substitute that dispatch
   admission allows (ADR-0011) has no counterpart at merge. The `live_estate` known-good patterns
   (factory-policy.toml) therefore remove only the dispatch-time approval, as they already did for
   inert repositories.

The merge is also gated by the approved `factory-delivery` change record `change_proposer` files
for the pull request, and by the `live_estate` change window. Graduating either human gate is left
to the Admission and Landing passes; this amendment graduates neither.

**The change window stays `00:00–23:59` (Ruling 7).** That is the build-phase widening (ADR-0012
amendment 1). When it narrows back to the night, dispatch and merge for these repositories become
night-only. This is recorded here, not changed.

## Amendment 3 — 2026-10-29 (the estate lander sets factory records aside)

**Status:** Accepted (records what the code does; no new decision).

The boundary "The estate lander is unaffected" no longer describes the lander. Policy version 4
approves `factory-delivery`, and the lander selects subjects on status alone, so it would otherwise
take a factory pull request into a lane that asks nothing about the work unit, the verifier-decided
criteria or the authority fingerprint. The lander selects subjects by `_LANE_CLASSES` in
`src/estate_lander/cli.py`, which holds only `dependency-update`. Factory records, and any class
the lander doesn't know, are set aside and counted as deferrals by class. The factory lane
(`pr_merge_admission`) lands them.

## Amendment 4 — 2026-10-30 (Landing Part 4: a standing bump lands without a click)

**Decided by Devon, 2026-10-29 and 2026-10-30** (dependency bumps need no human approval; Landing
Part 4, decision 1). This graduates amendment 2's two human gates for standing dependency-update
units whose package stays inside the repository, and leaves them standing for a `live_estate`
package. ADR-0006 amendment 2 graduates the decomposition decision. This amendment graduates the
other gate, `pr_merge_admission`'s authority term.

The term now reads "a bound approval, or the policy path". A human approval bound to the unit's
exact fingerprint stays sufficient for every change class. Without one, the policy path meets the
term only when all of these hold:

- the envelope's `change_class` is `dependency-update`;
- the revision's approver starts `policy:dependency-update@`, its profile is `dependency-update`,
  `profile_fields.standing` is true, and its declared reach is exactly `source_repository`. These
  are the decomposition gate's own conditions (`bump_policy.standing_bump_refusal`);
- `human_authority_gate` reports no refusals and only dependency-update known-good patterns
  recognise the envelope, judged against the policy as loaded at the merge, so a policy narrowed
  since dispatch refuses;
- the member record grants `dependency_updates` on the repository;
- `ORCHESTRATOR_BUMP_MERGE_ENABLED` is on.

Every other term is unchanged: a completed unit, verifier-decided criteria from observed evidence,
no open reconciliation condition, the merge capability, the adjudicated head, the estate's answer
and the holds. A repository the estate routes still needs its approved change record and window,
whatever reach the package declared. An inert landing's term is as it was.

**The switch governs the policy path alone.** The no-off-switch ruling in `pr_merge.py` said it was
void if a scheduled caller was proposed, and the bump driver is one. With the switch off, a unit the
path would otherwise admit refuses `merge_policy_path_off`, and that code alone, so the lane reads it
as waiting. A unit outside the path still refuses `authority_approval_not_bound`, since the switch
isn't what holds it. A landing on a bound approval never asks the switch.

**The record.** "Policy lifted the gate" and "a person approved" stay different records: the act
writes `authority.human_gate_not_required` with `at: merge`, the patterns, the policy version and
source and the fingerprint, in the transaction that records the merge, and
`authority_approval_id` stays null. The admission answer serves the same names and writes nothing.

The residual ADR-0006 amendment 2 accepts applies here too: `approved_by` and `standing` on a SYSTEM
intake are attested by the caller.
