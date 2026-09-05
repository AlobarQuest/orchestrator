# ADR-0041 — A subject with no ecosystem is not an unreadable one

- **Status:** Accepted
- **Date:** 2026-09-05
- **Decided by:** Devon
- **Relates to:** ADR-0038 (the orchestrator merges the cascade's subjects; the landing policy is
  one document change-manager holds), ADR-0009 (reach is DECLARED, never inferred — R8), the
  standing ruling of 2026-09-05 that upstream's problems stay upstream
- **Serves:** step 3 of `~/docs/software-delivery-system/2026-09-05-upstream-sync-through-sds-plan.md`

## Decision

**`landing_ecosystem_unreadable` conflates two different answers, and only one of them is a
refusal.** The inert lane gains a third answer — *this subject has no ecosystem* — and which
subjects those are is **DECLARED in the landing policy, never inferred from a branch name**.

Concretely: the policy's inert block gains a set naming the permitted authors whose pull requests
are **not** ecosystem-scoped. An author absent from that set is ecosystem-scoped, so it must
produce a readable ecosystem or be refused exactly as today. **Dependabot's behaviour does not
change by a single term.**

## The problem, measured 2026-09-05

`ecosystem_exclusion_term` calls `ecosystem_of(pull.head_ref)`, which reads the second segment of
`dependabot/<ecosystem>/<rest>`. Both upstream-sync branches are named plainly **`upstream-sync`**,
so it returns `None` and the term refuses with `LANDING_ECOSYSTEM_UNREADABLE`.

The function itself is honest and says so: *"None for any name that is not that shape. What that
means is the caller's to decide, and it decides refuse."* The defect is in the caller's reading,
not in the reader.

And the caller's own comment explains why it refuses, which is exactly right for the case it was
written for: *"a name this cannot read is never 'the bot named no ecosystem', it is this program
failing to read what the exclusion is about."* That reasoning holds for a **Dependabot** branch
whose shape it cannot parse — something is wrong and refusing is correct.

**It does not hold for a subject that has no ecosystem at all.** An upstream sync is not a
dependency bump; it is somebody else's release, wholesale. Asking which package ecosystem it
belongs to is not a question it failed to answer — it is the wrong question.

## This is the estate's own recurring shape, for the FOURTH time

CLAUDE.md already records three: `repo.protection` reporting `violation` for private repositories
on a plan that does not offer the feature; ADR-0015 asking the same of `runner.caller` on a repo
declared not-a-factory-target; the traceability chain's `conditions` hop, which can only be
populated by something going wrong. Each was a check reporting *not met* where the truth was *not
applicable*.

The rule that came out of those is the one applied here: **whenever a check reports a binary
verdict over a population, ask whether some members can never satisfy it for reasons that are facts
about the world rather than defects.** A pull request that is not a dependency bump is such a
member.

## Why DECLARED and not inferred

The tempting shortcut is to read it from the branch: a `dependabot/` prefix means ecosystem-scoped,
anything else does not. It is one line and needs no policy change.

**Rejected, on ADR-0009's R8:** an inferred value trades a loud failure for a quiet one. Under that
shortcut any branch name that is not `dependabot/...` silently skips the ecosystem bound — so the
exclusion, which exists to BOUND what may land, is switched off by a branch name. The bound would
be defeated by the thing it is meant to constrain naming itself differently, which is the fail-open
shape this repository keeps finding.

Declaring it in the policy costs a field and a version bump, and puts the fact where ADR-0038 put
every other bound: in the one document change-manager holds, that its readers ask rather than
transcribe.

**The direction of the default is the safety.** Absent from the set means ecosystem-scoped, which
means the current refusal. A policy version that forgets to declare a new author gets today's
behaviour, not a hole.

## What this does NOT decide

- **Whether these two repositories may land unattended at all.** That is step 4's policy version
  and remains a separate human act. This ADR only stops a term from refusing them for a reason
  that is not true of them.
- **What gates a sync.** Steps 1 and 2 answered that: both repositories now carry a required check
  that reports on every pull request (`build and test`; `hardening and syntax`). Before those, a
  sync pull request read `CLEAN` because nothing could fail.
- **The install step.** Landing is not installing, and the estate has never mutated the operator's
  machine. That is its own decision.

## Consequences

- A new refusal disappears from a population it was never true of, and no refusal is weakened for
  the population it was written for.
- The policy document gains one field, so its version number moves — and per ADR-0038 one version
  covers both populations, so the deploying lane's landings will be attributed to the new number
  without their rules having changed. That is a known and recorded consequence of one holder, not
  a defect introduced here.
- **A test must prove the default direction**, not merely the new path: an author absent from the
  declared set, on a branch with no readable ecosystem, must still refuse. Without that, the
  change reads as correct while having switched the bound off for everyone.
