# ADR-0034 — The cascade and the factory split on outcome, not on update type

- **Status:** Accepted
- **Date:** 2026-08-28
- **Decided by:** Devon
- **Relates to:** ADR-0016 (native auto-merge for routine updates, the factory for the rest),
  ADR-0018 (the auto-merge gate is a cascade, not a disjunction), ADR-0023 (a base-image bump is
  not a routine update)

## Decision

**A Dependabot pull request whose required checks pass is routine, whatever its update type or
absence of one.** The cascade arms on it. **The factory takes what fails** — the bumps where code
must change before the diff is correct.

**The one exclusion is principled rather than enumerated: exclude where the required checks do not
exercise what changed.** Today that is exactly one case, `docker`, and it is ADR-0023's own
reasoning stated as a rule instead of an instance — `quality.yml` builds the image and never runs
it, so a package that installs cleanly and fails at import on a removed module passes everything.

Devon, 2026-08-28: *"The intention had been for things that can't happen via GitHub Dependabot
should go to the factory."* That set and *"its required checks fail"* are the same set. A green
pull request is one Dependabot accomplished; the diff exists and the repository's own tests pass
on it.

## Why the built mechanism diverged from the stated intent

ADR-0016 is titled *"native auto-merge for routine updates, the factory for the rest"*, and the
Phase-3 plan says the **failures** are the factory's differentiated value. But what was built
splits on update **type**. Those coincide for a major that breaks and diverge for every major or
range that does not.

**Measured 2026-08-28 across the six SDS repositories — thirteen open Dependabot bumps:**

- **Ten have fully green checks.**
- **Ten of the thirteen are requirement-range bumps** — `setuptools`, `uvicorn`, `pyjwt`,
  `pydantic-settings`, `greenlet`, `fastmcp`. Every one green.
- Three are anything else: `zod` (mixed), `dependabot/fetch-metadata` (mixed), and
  `python 3.12→3.14-slim` (red, already excluded by ADR-0023).

**A requirement range can never be admitted by any rule about update types.** It states no single
delta, so `update_type_of` returns nothing — its own docstring calls such a bump *"correctly
unlandable by this lane"*. Ten of thirteen are that shape, so no tuning of a type-based rule
reaches any of them, ever. They are permanently stuck by construction rather than by policy.

**CORRECTED 2026-08-28, during the build, and the truth is a stronger argument than the draft.**
This paragraph originally said those ten would each travel a standing package, a revision, a change
record, two human approvals, a dispatch and a coding agent. **They would not, and never could.**
`bump_proposer._consider` refuses a title stating no single delta — `unclassifiable` — *before*
`rule.permits` is consulted, and `bump_of` returns `None` for every requirement range (measured
against all ten real titles: `_version(">=83.0.0")` fails on `isdigit`).

So the ten were reachable by **neither** mechanism: not the cascade, whose rule keys on a delta they
do not state, and not the factory, which discards them one guard earlier. They were stuck in the gap
*between* two lanes, which is why they have simply accumulated. The change's value is that it makes
ten permanently-unreachable bumps landable — not that it saves them an expensive trip.

**The factory's live queue today is therefore ONE, not the two or three an earlier draft of this ADR
said:** `zod` #71. `fetch-metadata` parses but is already permitted by `major_ecosystems`, and the
docker bump is `unclassifiable` for the same reason the ranges are.

## What the update-type rule was actually doing

**Both mechanisms already gate on the checks passing.** The cascade arms auto-merge and GitHub
merges only when required checks pass; the landing lane refuses on `landing_checks_not_clean`. The
update-type condition sits on top of that gate and says nothing about whether the bump works. It is
a risk-appetite knob, and this ADR sets it deliberately rather than leaving it as a structural
wall.

**The residual risk, stated so it is decided rather than inherited.** `semver-major` means the
author *declares* a breaking change; green CI means *this repository's tests do not detect one*.
Those differ exactly where the tests are incomplete — which is equally true of the patch and minor
bumps this estate has auto-merged for weeks, at a smaller blast radius. The exposure is test
coverage, and it is not new; what changes is how much of it is reached.

## Consequences

- **The transcription and the ledger's vocabulary move in the SAME operation as the rule.** This is
  the consequence most likely to be skipped, and skipping it forfeits the point.
  `landing_ledger/rules.py` transcribes each gate revision **by git blob sha** and fails closed on
  an unknown one: *"a blob sha with no entry is not 'probably fine': it is a rule nobody
  classified, deciding landings."* Change six gates without moving the transcription and two things
  break at once — `bump_proposer` refuses every repository with `gate-not-transcribed`, and the
  landing ledger can no longer say **why** a bump was permitted. Devon, 2026-08-28: *"I also need a
  level of traceability, even if the orchestrator does not do the work."* That traceability is the
  `auto_merge_rule` permission basis, and it is derived from the transcription. The window where
  the record would be missing is the window where the new rule does the most novel merging.
- **The recorded basis needs a value for a landing with no delta.** A cascade landing records
  `update_type` among eleven fields. Ten of the thirteen open bumps have no update type at all, so
  without a way to express *"permitted because its required checks passed, no version delta
  stated"*, the first landings under this rule arrive as records the ledger cannot explain and its
  audit reports them as findings. The basis is the evidence for the permission, not decoration.
- **The `Rule` shape changes, not only its values.** `rules.py` says its revisions *"genuinely
  differ in SHAPE and this is a transcription rather than a policy"*, and its `permits` is a
  disjunction over update types and ecosystems. A rule that permits anything not excluded is a
  fourth shape. Transcribe what the new bytes say; do not reword an existing entry.
- **Six repositories converge on one gate revision.** They run three today — `72391c0f`
  (orchestrator, carrying the docker exclusion), `e849b3a8` (intent-packages, security-standards,
  project-standards) and `a4a4b8da` (infraops-mcp-server, factory-runner). Carrying the exclusion
  in all six makes it a single blob and one transcription entry; it is inert in the five that
  declare no docker ecosystem.
- **This reopens the arming-credential question on a changed fact, and does not settle it.** The
  2026-08-25 ruling left the cascade on `secrets.GITHUB_TOKEN`, reasoning that it *"drains roughly
  one item per Dependabot cycle, so daily verification is already close to per-merge cadence."* A
  wider rule drains faster, and a `GITHUB_TOKEN`-armed merge fires no `push` event, so `main` goes
  unverified until the next daily run. The ruling's own reopen trigger was a bad cascade merge
  reaching `main` and sitting there; the fact that has changed is the drain rate, measured on
  2026-08-27 as `intent-packages` `main` sitting red for 31 hours with the failure reported within
  minutes and nobody subscribed.
- **Deploying repositories are a separate act.** `change-manager` and `brain` carry no cascade;
  their equivalent knob is `deploy_policy.py`'s `LandingConditions.update_types`, currently
  `{patch, minor}`. The same relaxation applies there with one exclusion its own rationale already
  argues for — a bump that changes the rollout workflow is exercised for the first time during the
  rollout it is meant to gate. That is deploy policy version 5, taken after this has run.
- **The factory keeps exactly what it was built for.** Its queue becomes the bumps whose checks
  fail: `zod`, where the MCP SDK's `server.tool()` signature shifts under zod 4, is the archetype —
  and today it is the whole of that queue.
  **`_consider` must move with this rule or the queue becomes ZERO.** Its skip condition is
  `rule.permits(...)`, which under the new rule is true of nearly everything, so the one bump the
  factory exists for would be handed to a cascade that cannot merge it. The predicate must also ask
  whether the checks have CONCLUDED in failure — never merely "not green", since `PendingUpdate.checks`
  holds only jobs that have concluded, so an empty set means *not yet judged* and treating that as a
  failure would sweep every Dependabot pull request into the factory the minute it opened. Standing-package coverage stops being the bottleneck it appeared to
  be on 2026-08-27, and the scaffolding decision taken that day is held rather than reversed —
  nothing about it was wrong except its urgency.
