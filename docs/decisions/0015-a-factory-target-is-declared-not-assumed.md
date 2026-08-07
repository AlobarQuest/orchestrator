# ADR-0015 — A factory target is declared, not assumed; and the runner may not maintain itself

- **Status:** Accepted; **partially reversed 2026-08-07 — see the amendment at the end.**
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
