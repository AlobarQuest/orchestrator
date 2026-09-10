# ADR-0015 — A factory target is declared, not assumed; and the runner may not maintain itself

- **Status:** Accepted; **partially reversed 2026-08-07 and reinstated in full 2026-08-17 —
  see the two amendments at the end.** Decision 2 shipped 2026-08-17; nothing here is outstanding.
- **Date:** 2026-08-04
- **Workstream:** Wave-3 closeout (follows WS-P2.37)
- **Supersedes:** nothing. **Relates to:** the conformance kit's `runner.caller` check
  (`project-standards`), and the `repo.protection` four-outcome fix (project-standards PR #14).

## Context

The conformance kit's `runner.caller` check requires every onboarded repository to host
`.github/workflows/factory-runner-pilot.yml`, pinned to factory-runner's declared
`RECOMMENDED_CALLER_PIN`. A caller workflow is the *only* thing that makes a repository
dispatchable: the orchestrator fires `workflow_dispatch` at that file, and the reusable workflow
then runs inside the target repository.

So the check is not really about conformance. **It asks "can the factory send work into this
repo?" and reports the answer as a repo defect.** Applied uniformly across an estate, that
converts an unmade scope decision into a standing violation — and a standing violation invites
some future session to helpfully resolve it by adding a caller, which decides the scope question
by satisfying a checklist.

At Wave-3 close, six of eight candidate repositories were admission-clean. The two that were not —
`factory-runner` and `project-standards` — failed only because they host no caller. The bar was
≥5, so nothing was under pressure, and the question could be decided on its merits.

## Decision

**1. Neither `factory-runner` nor `project-standards` gets a caller workflow.**

**2. Being a factory target is a property a repository DECLARES, and a repository that declares
itself not-a-target must read `not-applicable` on `runner.caller` — never `violation`.**

The kit already has the vocabulary: `matrix.py` carries `pass | violation | unknown |
not-applicable`, introduced when `repo.protection` learned that a private repo on a plan without
branch protection is a procurement fact rather than a repo defect. "Deliberately not a factory
target" is the same shape, and it gets the same treatment. Without this, the decision does not
survive its own recording — the kit keeps reporting a defect, and the reasoning has to be
re-litigated every time someone reads a sweep.

## Why factory-runner specifically

factory-runner *is* the runner: its reusable workflow executes every factory job in the estate.
Giving it a caller creates a structural self-reference, not merely an aesthetic one.

The reusable workflow installs the CLI at `job.workflow_sha` — its own commit — and
`RECOMMENDED_CALLER_PIN` **lags by one commit by design**, because a commit cannot name its own
SHA. A factory run changing factory-runner would therefore check out the new code and execute it
through a *previous* copy of the harness. **Any change to the harness ships through the harness it
is changing:** a fix to claim logic, prompt construction, or finalize would be delivered by the
version that still carries the bug.

This is not hypothetical. WS-P2.36 hit the same shape incidentally — a package containing a
validation fix could not satisfy its own criterion, because the criterion was snapshotted before
the fix ran, and the workstream had to be split into two steps. Here the property is structural
rather than incidental.

The blast radius is also asymmetric. factory-runner is the one repository where a bad merge stops
**every** dispatch in the estate, including the dispatch that would repair it. Recovery is by hand
in that case regardless — so automation buys least precisely where it costs most.

**The eventual resolution is a second, distinct runner whose purpose is maintaining the primary
one** (Devon, 2026-08-04). That is the standard bootstrap answer — a compiler is built by a
different compiler than the one it becomes — and it breaks the self-reference cleanly rather than
tolerating it. Recorded here so factory-runner's exclusion reads as a deferred design, not a
permanent shrug.

## Why project-standards, for a different reason

`project-standards` owns the conformance kit — `portfolio onboard`, the readiness schema, the
checks themselves. The discomfort is governance-shaped rather than technical: the artifact that
*measures* the estate's conformance would become modifiable by the process it measures, so a
factory run could in principle loosen a check and make the estate look better without being
better. That risk is remote and fully gated (human authority approval plus a human merge), and it
is **not** the reason for this decision.

The actual reason is simpler and was stated plainly: project-standards will be maintained through a
different mechanism, beginning manual and automated over time. There is no self-reference problem
to solve — only a scope choice, now made. The honest record is that it had never been decided
before, rather than that it had been considered and refused.

## Consequences

- Criterion #2 stands at **6 of 8**, and 8 of 8 is not a goal. Two repositories are permanently
  outside the factory's reach by choice.
- The kit needs the declaration mechanism and the `not-applicable` path (below). **Until that
  ships, every sweep will report two `runner.caller` violations that are decisions, not defects** —
  read them as such, and do not fix them by adding callers.
- Should factory-runner's dependency or remediation load ever become burdensome by hand, that is
  the trigger to build the second runner, not to reverse this.

## Implementation note

The declaration belongs in `PROJECT.md` frontmatter alongside `delivery_profile:`, which the kit
already reads — repo-local and self-describing, rather than a list inside the kit that the affected
repository cannot see. The check then reports `not-applicable` with the declared reason. This is a
`project-standards` change and, per the decision above, a manual one.

---

## Amendment, 2026-08-07 — `project-standards` becomes a factory target

**Decision 1 is reversed for `project-standards` and stands unchanged for `factory-runner`**
(Devon, 2026-08-07). Decision 2 — that being a target is *declared*, and that a repo which
declares itself not-a-target must read `not-applicable` rather than `violation` — is untouched,
and is now needed for exactly one repository instead of two.

**This is the reversal the original anticipated, not a contradiction of it.** The record above
says plainly that `project-standards` was excluded by "a scope choice, now made", explicitly not
for the governance reason it raises and dismisses, and it names the expected trajectory —
"maintained through a different mechanism, beginning manual and automated over time." Three days
later the choice was made differently. Devon's reasoning, recorded because it inverts the usual
one: *"My initial hesitation was a general sensitivity because it handles so much work. But that's
the same reason to get into governance rather than leave it out."*

The governance-shaped discomfort the original raised — that the artifact measuring the estate's
conformance becomes modifiable by the process it measures — is unchanged and was already judged
remote and fully gated (human authority approval plus a human merge). It is now a live property
rather than a hypothetical one, and worth re-reading if the gates are ever narrowed.

**`factory-runner`'s exclusion is untouched and is not the same kind of thing.** It is
structural: the reusable workflow installs its own commit, `RECOMMENDED_CALLER_PIN` lags by one
by construction, so any change to the harness would ship through the harness it is changing. The
answer there remains a second, distinct runner — not the allowlist.

**Consequences that changed.**

- Criterion #2's "6 of 8" becomes 7 of 8, and 8 of 8 remains not a goal — one repository is
  permanently outside the factory's reach by choice.
- The kit's `not-applicable` path is still unbuilt and still needed, now for `factory-runner`
  alone. Until it ships, one `runner.caller` violation is a decision rather than a defect.
- The implementation note's `PROJECT.md` frontmatter declaration is now moot for
  `project-standards` and still open for `factory-runner`.

**Executed the same day.** Caller workflow at the recommended pin (`0e047df5`, byte-identical to
the other six), four Actions secrets set from BWS by UUID, and the repository added to
`ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` alongside `security-standards`,
`change-manager`, `brain` and `infraops-mcp-server`.

**One prerequisite was discovered by probing and is not in this ADR's original mechanics:** a
factory target must also appear in `FACTORY_PR_TOKEN`'s fine-grained repository access list. All
three documented steps were complete and the probe run still died in 35 seconds at
`actions/checkout` with a 403. That list is a settings-page property of the account holding the
PAT; no API extends it. See the corresponding invariant in `CLAUDE.md`.

---

## Amendment, 2026-08-17 — reinstated in full, and decision 2 stopped being a plan

**Amendment 1 is itself reversed. `project-standards` is not a factory target** (Devon,
2026-08-17, reaffirming the original decision). Decision 1 stands as first written, for both
repositories. Decision 2 is needed for two repositories again rather than one — and on the same
afternoon it was built.

**Amendment 1 is left exactly as it was written.** It is a correct record of what was decided on
2026-08-07, and per ADR-0014 a decision does not become wrong because a later one replaced it.
What follows records what changed, not what should have been thought.

**What was executed — all of it inside twenty seconds on 2026-08-17.**

- **`project-standards#23`** (merged 20:35:52Z, `b9634564`) removed the caller workflow; that
  file was the entire diff. Its body: *"Devon reaffirmed ADR-0015 on 2026-08-17:
  project-standards is a prerequisite for the SDS and is maintained through a different
  mechanism, not by the orchestrator."* That is the original's own reasoning, restated.
- **`project-standards#24`** (20:35:56Z, `6980d97`) shipped **decision 2 and the implementation
  note above, as specified**: `factory_target: <bool>` in `PROJECT.md` frontmatter, read by
  `runner.caller`, which reports `not-applicable` with the declared reason.
- **`factory-runner#56`** (20:36:12Z, `b299183e`) declared its own `factory_target: false`,
  citing this ADR.
- The repository was removed from `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES`.

**Verified 2026-09-10**, because an amendment asserting a reversal should be measured rather than
recalled. `project-standards`' default branch hosts one workflow (`quality.yml`) and no caller;
`factory-runner`'s hosts `factory-runner.yml` and `quality.yml` and no caller; both declare
`factory_target: false` in `PROJECT.md` with a reason naming this ADR; and the allowlist read
from inside the running orchestrator container is exactly five entries — `intent-packages`,
`security-standards`, `change-manager`, `brain`, `infraops-mcp-server` — with
`project-standards` absent.

**Consequences.**

- **The "until that ships" consequence, in both the original and amendment 1, is discharged.**
  No sweep reports a `runner.caller` violation that is a decision rather than a defect.
- `_declared_non_target` also closed the inverse this ADR never named: **a repository that
  declares itself a non-target while still hosting a caller stays a `violation`**, because it is
  dispatchable against its own declaration. A declaration may turn a violation into
  `not-applicable`; it may never turn a failure into a pass. `project-standards` sat in exactly
  that contradiction for the ten days between the two amendments.
- **Criterion #2's arithmetic changed shape, not just value, and this amendment deliberately
  supplies no new count.** `not-applicable` satisfies admission
  (`ADMISSION_SATISFYING = (pass, not-applicable)`), so a declared non-target is admission-clean
  *and* out of the factory's reach. "6 of 8" and "7 of 8" were both proxies for how many
  repositories the factory could reach; that is now a separate question with a separate answer,
  and the answer to it is **five**, measured above. "8 of 8 is not a goal" was always about
  reach, and it stands. No sweep was re-run for this amendment.
- The implementation note is closed **for the kit**. What remains open is a different consumer
  the note did not contemplate: the orchestrator has no checkout, so a repo-local declaration
  cannot reach *dispatch admission*, which still consults a hand-maintained environment variable.
  That is the third of the three disagreeing answers, and it is tracked as a programme decision
  rather than here.

**On the prediction, because the record disagrees with itself.**

The original says a standing violation *"invites some future session to helpfully resolve it by
adding a caller, which decides the scope question by satisfying a checklist."* Something close to
that happened on 2026-08-07, and the estate's two accounts of it do not agree. Both are recorded
here so the next reader does not have to re-derive them.

- **The later account** — `project-standards#23`'s body, since repeated in the orchestrator's
  `CLAUDE.md` — is that the caller *"was added on 2026-08-07 (`6aeff6f`) by an onboarding sweep
  that did not check the decision."*
- **The artifacts do not support "did not check".** `6aeff6f`'s own message (11:51:34-04:00)
  names this ADR, distinguishes its two exclusions, reverses one and explicitly leaves the other,
  and says *"Devon made the scope choice differently on 2026-08-07, which is what the ADR
  anticipated."* Amendment 1 was committed eight minutes later and quotes his reasoning. The
  decision was read, and the reversal was ratified.
- **What the artifacts do support is the pressure this ADR named.** `8de11eb`'s message records
  the occasion: Devon added five repositories to
  `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` that day, and *"four needed only the
  allowlist entry; project-standards needed a caller workflow too."* The repository was carried
  in on a batch, and the caller followed from the batch rather than from a fresh scope decision.

So the mechanism was momentum rather than an unread checklist — a weaker claim than the later
account and a stronger one than nothing. **Read it as an argument for decision 2, not against
it.** For those ten days every readiness document anyone consulted showed a defect where a
decision had been made, and the cheapest way to clear a defect is to satisfy it. That this
particular reversal was consulted and ratified is what kept it honest; nothing in the mechanism
required that, which is the whole reason the declaration had to be built.
