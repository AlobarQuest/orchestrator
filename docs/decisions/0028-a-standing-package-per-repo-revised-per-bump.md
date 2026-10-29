# ADR-0028 — A standing package per repository, revised per bump

- **Status:** Accepted
- **Date:** 2026-08-19
- **Decided by:** Devon
- **Relates to:** ADR-0016 (native auto-merge for routine updates, the factory for the rest),
  ADR-0025 (approval by policy), ADR-0026 (the signal→work record), ADR-0027 (a machine may
  register an intake)

## Context

ADR-0016 assigns the non-routine dependency remainder — major-version and requirement-range bumps
the auto-merge cascade correctly refuses — to the factory. Measured 2026-08-19 that is 18–24 open
pull requests. Nothing picks them up, and the lane that would has just been completed from
*approved package + work record* onward.

**What blocked it was the package.** A dependency-update package today pins the exact versions:

```yaml
title: Bump httpx2 to 2.9.1 in orchestrator
  from_version: 2.7.0
  to_version: 2.9.1
```

So there is no reusable template, and twenty-four bumps meant twenty-four packages, each scaffolded,
named and separately approved. That is not a lane; it is the same toil in a different costume.

Devon, 2026-08-19: *"shouldn't we have a somewhat generic 'this is a dependency bump per Dependabot'
lane that does not require each bump to be named explicitly each time?"*

## Decision

**1. A standing package per (repository, ecosystem), revised per bump.**

The package identity is stable — the specific bump lives in a **revision**. `intent_packages revise`
already exists for this, and revisions are already the unit that carries content changes and their
own lineage approval.

This also fits ADR-0026 exactly rather than by coincidence: the **revision** is where
`change_record_id` lives, so one revision per bump preserves *one cause, one revision*. A package
serving many bumps would make the cause ambiguous at precisely the join G1 exists to protect.

**2. A revision of a standing dependency-update package is approved by policy conformance, not by a
per-item human act.**

This is ADR-0025's mechanism one layer earlier. A human pre-decides *this repository, this
ecosystem, these update types*; a conformant revision is approved by that policy, and the chain
records the **policy version** rather than a person — which is re-derivable in a way a click is not.

## Why the human approval is not required here — and why it is not being removed

The lineage of `orchestrator-httpx2-bump` reads `actor: claude-code-interactive`,
`approver: devon`. Devon, 2026-08-19: **the AI ran the approve command with his name.** So the
per-revision "named human approval" has already been a machine act carrying a human's name — the
same discovery that settled ADR-0027 for intake.

**But the conclusion here is deliberately narrower than ADR-0027's.** Devon: *"That doesn't mean the
gate is useless, it just is not needed yet. Not for the stage of usability we are at."*

So this ADR does **not** find the gate worthless. It finds it **not yet warranted**, at a stage where
the estate is proving a lane rather than running one at scale. The design consequence is the whole
point of decision 2:

**It must be a POLICY, never a removal.** Raising the bar later — requiring a named human for a
repository, an ecosystem, a change class, or all of them — is then a new policy version, one clause,
the way deploy policy widened to `brain`. Had the guard simply been deleted, restoring it would be a
rebuild, and the decision to restore would never be prompted.

## What still gates the lane

Nothing here touches the checkpoints that remain, and they are the reason this is safe:

1. **The human approves the work record in change-manager** — ADR-0026 kept this, and it is the
   decision that matters.
2. **Decomposition approval** — human-only, GUI-only. Devon, 2026-08-18: *"It allows me to see the
   basic commands being run … I can check if they are in the range of what I would think should be
   happening."* A reasonableness check, and a real one.
3. **Authority envelope approval** — a human click per unit.
4. **Dispatch admission** — eight terms.

A policy-approved revision reaches a human three more times before any code is written.

## Consequences

- **Tier C becomes a lane rather than 24 bespoke acts.** A producer revises the standing package and
  writes a work record; the human approves the record; the carry does the rest.
- **The standing packages must be authored once, per repository and ecosystem.** That is real work
  and it is not free — but it is once, not per bump.
- **The approval policy is a new artifact** and inherits the discipline of the deploy policy: total
  coverage, refusal-only, versioned, and read per call rather than cached.
- **This does not make signals become work.** The producer here is mechanical because a
  cascade-refused bump *is* its own work statement. Signals needing diagnosis remain unserved, and
  that gap is unchanged by this decision.

## Amendment 1 — 2026-10-28 (bridge layer Part 3)

**Status:** Accepted (Devon's decisions in `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`,
Part 3).

**The producer creates the standing package.** A failed Dependabot bump in an inert repository that
the SDS member record grants `dependency_updates`, with no standing package for its dependency, gets
one: `bump_proposer` runs intent-packages' `create-standing` (a draft at revision 1 already carrying
the bump), then the same transition, policy approval, hash, commit, publish and proposal as a
revision. The package's `standing: true` is still the author's statement; `create-standing` is that
author. Consequence 2 above ("authored once, per repository and ecosystem") is now done by the
producer for npm, pip and uv.

**Merging is in scope.** A created package carries no `human_review` criterion and no "merging is
left for a person" text, so the factory's landing lane may merge the unit's pull request. Its reach
stays `[source_repository]`.

**What a person still decides.** "What still gates the lane" item 1 changes: change-manager's work
policy approves a conforming record (ADR-0025 amendment 1), and a person approves every other one.
The producer alerts, rather than creates, for a bump it cannot route: an ecosystem with no tooling
profile, a requirement range its tooling's mutation can't carry (any range in pip or npm; in uv
anything but `>=` and `==`), a dependency already covered by a package in another ecosystem
(discovery holds one package per repository and dependency), and every docker bump. Docker bumps
alert whether or not they pass only because the inert landing policy excludes docker today; a
permitted ecosystem's passing bump is the cascade's. Each holds one alert-hub alert, sender
`sds-bridge`, keyed `dependency-bump:<repository>:<ecosystem>:<dependency>`, resolved by the first writing pass that no longer raises it: its pull request merged or closed, or (in an ecosystem the cascade lands) a re-run passed. A re-run that fails again but hasn't settled holds the key, neither resolved nor fired again. A repository still in the lane that a pass didn't read (GitHub could not answer, in scope through a standing package but not declared inert, or outside a `--repository` run) keeps its keys; a repository that left the lane (its `dependency_updates` grant withdrawn, or no longer declared inert with no standing package) has its keys resolved by the next writing pass that reaches its alerts (a pass with no repository in the lane stops first and sends nothing).
Repositories that deploy on landing stay out of the lane. Holds (amendment 2) were the
precondition for admitting them, not the admission: widening the lane to them is its own
decision.

**Residual: the envelope's dependency is not bound to the package's.** The known-good command
shapes bound each command's grammar, not which dependency it names: no shape ties the dependency in
a unit's mutation command to the package's `profile_fields.package`, so `uv add 'other>=1'` is
recognised for an envelope generated from an `httpx2` package. Today the human decomposition
approval covers it, because a person approves the units and their commands. Binding the two (the
mutation's dependency equal to the package's) is a precondition for graduating decomposition
approval.

## Amendment 2 — 2026-10-28 (bridge layer Part 4: holds)

**Status:** Accepted (Devon's decisions in `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`,
Part 4; rulings in `docs/superpowers/plans/2026-10-28-bridge-p4-holds.md`).

**Every bump path honours the member record's holds** (ADR-0058 amendment 2), through one shared
matcher: `services/membership/holds.py` in the orchestrator and `sds_members/holds.py` for the
lanes, held equal by `tests/services/test_hold_agreement.py`. A bump is held when its release is
greater than the ceiling's, or when either can't be parsed.

- **Both landers** refuse a Dependabot pull request that moves a held dependency past its ceiling
  (`landing_version_ceiling_exceeded`, an exception: it clears only when a person lifts the hold).
  A dependency list that can't be read in a repository with holds is
  `landing_dependency_list_unreadable`, a finding. A grouped update is refused when any member
  exceeds a hold. A fork's upstream sync is not a bump and is exempt.
- **The close act.** An acting lander pass closes a held pull request with one public comment
  through `POST /api/v1/held-pr-close`, which re-composes the lane's admission and closes only on
  the ceiling code, an update-bot author and the lane's own population.
- **The factory merge** refuses a dependency-update unit whose revision's bump breaches a hold
  (`merge_version_ceiling_exceeded`) or can't be read in a repository with holds
  (`merge_dependency_bump_unreadable`). **The bump proposer** skips a held bump
  (`ceiling-exceeded`) and **the work carrier** refuses a held record (`version_ceiling_exceeded`).
- **For uv the factory's version is a floor.** The uv mutator asks for `>=<to_version>` and its
  verifier is `uv lock --check`, so what lands can pass any ceiling. On the three factory paths
  (merge, proposer, carrier) a uv bump breaches every hold on its dependency, whatever its version
  (`factory_breaches`, one rule per matcher copy). Until intent-packages' uv mutator locks the
  exact version, which would let uv compare like npm and pip, a held uv dependency is never
  updated by the factory. Locking it changes the profile's fingerprints and is its own decision.
- **Holds do not widen the bump proposer's lane.** Repositories that deploy on landing (brain,
  change-manager among them) stay out of automatic bumps. Holds were the precondition for
  admitting them; admitting them needs a `live_estate` known-good pattern, the grant's reach
  and the change window, and is its own change and decision.
- **Every path is classified.** `tests/architecture/test_hold_paths.py` lists each merge path
  (every `MERGE_EXEMPT_PATHS` entry) and each propose path as honouring the matcher, covered by an
  honouring gate, or not a bump, and fails on an unclassified merge path.

**Residuals,** each accepted:

1. A grouped update's title or a requirement-range title gives no single version, so the
   version falls back to the commit's `updated-dependencies` block, which can lag a later
   rewrite of the branch.
2. The landers read the one commit the update bot authored. A person's commit pushed on top of
   Dependabot's is not read, so a version it sets is not compared. That commit is chosen by
   its author login alone (`author_login == UPDATE_BOT_LOGIN`, `terms._update_bot_message`),
   not by the pull request author term's verified-bot check (`author_is_bot`).
3. A pre-release of the ceiling's own release (`2.0.54rc1` against `2.0.54`) is admitted: the
   comparison is on the numeric release only.
4. The carrier's hold refusal is a finding, alarming each pass while the record stays approved,
   where the landers treat a hold as an exception. This diverges from the landers' ruling and is
   to be revisited beside the concurrent carrier work.
5. A dependency-update package whose tooling the factory path can't map (a non-standing
   `*-bump` package) is refused as unreadable in a repository with holds.
6. For npm and pip, nothing cross-checks the version in an envelope's `mutation_commands` against
   `profile_fields.to_version`; they agree because the profile derives both from the same
   arguments.
7. A hold's ecosystem must be the one Dependabot uses for that repository: a `pip` hold covers
   nothing in a uv-managed repository.
8. The close path (the PATCH to closed, as the dispatch App) is untested live until the first hold
   is placed.
9. A hold covers only an update that names the dependency (Dependabot's `updated-dependencies`
   list, the factory's `profile_fields`). A transitive move through a lockfile is never compared:
   an update of fastapi that moves a held starlette, or a uv re-resolve, is not held.
10. An estate lander pass asks only about pull requests whose change record is approved, so a held
    pull request whose record isn't approved is never asked about and never closed. The hold still
    stops it merging, since the admission refuses it whatever the record says.
11. A docker or github_actions bump, for which the factory has no tooling, is skipped by the bump
    proposer under any hold on its dependency, whatever its version: no version is read on that
    branch, and the hold answers the alert it would otherwise raise.

## Amendment 3 — 2026-10-28 (bridge layer: repositories that deploy)

**Status:** Accepted (Devon's decisions in `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`,
Part 3, "Coverage that widens: Repositories that deploy"; Rulings 1, 2, 3 and 5 of
`docs/superpowers/plans/2026-10-28-bridge-widen-deploying.md`). This is the decision amendment 2
deferred ("Holds do not widen the bump proposer's lane … is its own change and decision").

**The lane covers repositories that deploy.** `bump_proposer`'s lane is every repository the
member record grants `dependency_updates` that change-manager's landing policy declares, inert or
deploying, or that a standing package targets. A policy whose deploying population (the top-level
`repositories`) is empty or absent, or whose `landing` block decides by update type, stops the pass
(exit 2); it never shrinks the lane to inert in silence. A repository in neither population is
`not-declared-landing`. Measured 2026-10-28, the widened lane adds exactly brain and change-manager. The requirement is
the bump proposer's alone, because it is the one reader that widens on that data: the shared parser
reads the deploying half permissively, so the inert lander runs unchanged on a document without it,
and `membership_check` reports an absent or empty deploying population as a gap, never as "nothing
deploys".

**Reach for a deploying target is `[live_estate]` (Ruling 1).** `create-standing --reach
live_estate` writes it; an inert target keeps `[source_repository]`. This supersedes amendment 1's
"Its reach stays `[source_repository]`" for deploying targets. Only `live_estate` known-good
patterns are needed, because `authority_refusals` asks a pattern for every declared reach member,
and dispatch's `estate_refusal` passes, since it refuses only a redeploying target whose reach
omits `live_estate`. Cost if wrong: the reach no longer names the source repository; members only
narrow, and the target is still in the envelope.

**Which repositories deploy has one producer-side source and one enforcing one (Ruling 2).** The
bump proposer chooses the reach from change-manager's landing policy, the same document
change-manager's work policy reads. App Brain's `default-branch-landing`, read by dispatch
(`estate_refusal`) and by merge (`_landing_terms`), enforces it. If the two disagree, nothing
lands: a package marked `live_estate` for a repository App Brain calls inert gets more restraint
(the change window, a longer lease), and a `source_repository` package for one App Brain says
redeploys is refused `reach_contradicts_estate` at dispatch, visible on the unit. They agreed when
measured. No `membership_check` leg reads App Brain, because no lane holds an App Brain credential:
dispatch is the net. `membership_check` does report a deploying repository the member record does
not grant `dependency_updates`, beside the inert row.

**The grant accepts either exact reach (Ruling 3).** intent-packages' dependency-update grant
(schema 3) carries `reach_options`, `[["source_repository"], ["live_estate"]]`, and passes a
package whose reach equals one option exactly, so `[]`, a superset and a mixed set are refused.
intent-packages does not decide which option fits a target; App Brain at dispatch does.

**Each repository's own block decides what the landing party still has (Ruling 5).** The inert
block's `excluded_ecosystems` (docker) for an inert repository, the `landing` block's (github_actions)
for a deploying one. A passing bump in a deploying repository stays with the estate lander and never
becomes a package; only a failed one, or one in an excluded ecosystem, is this lane's. A repository
in both populations (which change-manager's disjointness test refuses) is treated as deploying, the
more restrained reach. The `landing` block names no authors, so the proposer's update-bot check
still reads the inert block only.

What still gates the merge in these repositories is recorded in ADR-0025 amendment 2.
