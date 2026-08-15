# ADR-0008 — Runner-brief compatibility is measured on DECLARED FIELDS, not on parseability

**Status:** Accepted · **Date:** 2026-08-01 · **Workstream:** WS-P2.23

## Context

On 2026-07-30 the orchestrator began serving an `enrichment` key on the runner brief. factory-runner's
`RunnerBrief` was `extra="forbid"`, and every caller in the estate was pinned to a runner predating
that field. **Every dispatch in the estate died at brief-parse for a full day and nothing noticed.**
The conformance check `runner.caller` reported `[ok]` throughout, because it compares SHAs and never
asks whether the pinned runner can parse what production serves.

WS-P2.23 addressed this in three parts:

- **A** — the reusable workflow installs its own revision (`job.workflow_sha`), so a caller's pin, the
  workflow, and the CLI are one commit by construction.
- **B** — the orchestrator's CI fails a pull request that adds a brief field the pinned runner cannot
  accept.
- **C** — `RunnerBrief` becomes `extra="allow"` and **records** undeclared keys, so an escape is
  neither fatal nor invisible.

Part B needs a definition of "cannot accept." The handoff said *"assert the orchestrator's
`RunnerBriefResponse` fields are all acceptable to it"*, which is ambiguous between two readings.

## Decision

**Compatibility is measured against the fields the consumer DECLARES, not against whether the brief
would parse.**

A field the runner does not declare is a field it cannot use. Part B fails on that.

## Why the alternative was rejected — and why it is dangerous rather than merely wrong

The other reading of "acceptable" is *would the model parse this payload?* **After Part C ships, that
is always true**, because `extra="allow"` accepts anything. So a Part B built on parseability would be
**permanently green** — and Part C, whose purpose is to make an escape survivable, would have silently
switched off the guard whose purpose is to prevent the escape.

Two guards addressing the same failure, where one disables the other, and neither reports it.

That was not caught by reasoning. It was caught by **B2 — the requirement to prove the guard fires in
both directions.** A parseability-based check cannot be made to red by adding a field, so the
proof step fails and the defect surfaces before merge. This is the clearest case yet for the standing
rule that a guard built to detect a failure must be demonstrated failing.

## The distinction that makes both parts coherent

**Part C protects the run. Part B protects the feature.**

| | Guards against | Failure it prevents |
|---|---|---|
| **C** — `extra="allow"` + record | the *run* dying | an estate-wide outage from an additive change |
| **B** — declared-field comparison | the *capability* silently not existing | shipping a brief field no deployed runner can consume |

Without B, an added field would be tolerated by every runner and used by none — the orchestrator
would serve enrichment that nothing reads, and the only signal would be C's recorded note. Without C,
a gate escape is an outage. They are complementary precisely because they measure different things.

## Consequences

- Part B's implementation reads the pin from `.github/workflows/factory-runner-pilot.yml`, **asserts
  the workflow at that revision still installs itself** rather than assuming Part A holds, and then
  compares declared field sets. The intermediate assertion matters: it fails loudly if a future edit
  reintroduces a literal install pin.
- The accepted-field set is **derived** from factory-runner at the pinned SHA, never hand-maintained.
  A hand-maintained copy is a second vocabulary and would drift — the defect class this repo has
  documented four separate instances of.
- Adding a brief field now requires bumping and re-pinning the runner first. That ordering was stated
  in prose in CLAUDE.md from WS-P2.12 onward and enforced by nothing; it is now mechanical.
- A related false belief was corrected in the same workstream: CLAUDE.md asserted the runner is
  installed *"fresh per run from its default branch, so merge-first suffices."* **It has never been
  installed from a branch.** That belief is why the enrichment addition was thought safe, and it is
  the root of the outage above.

## Alternatives considered

- **Parseability** — rejected above. Nullified by Part C.
- **Strengthen `runner.caller` into a compatibility check** (parse a live production brief with the
  pinned runner's model at onboard time). Discriminating, and proven to work during WS-P2.22 — but it
  detects rather than prevents, needs production credentials inside the conformance kit, and requires
  a live unit to fetch a brief for. Rejected in favour of prevention at build time.
- **Keep `extra="forbid"`** so drift stays loud. Rejected: it guards only the *safe* case, since an
  old runner cannot use a field it does not know about, while a renamed or removed field is caught by
  required-field validation independently of `extra`. It converted "I'll ignore this" into "every run
  in the estate dies."
