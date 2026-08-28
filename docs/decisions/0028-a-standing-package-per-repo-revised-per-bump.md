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
