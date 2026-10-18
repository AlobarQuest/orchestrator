# SDS target onboarding: design

- **Date:** 2026-10-18
- **Status:** Approved by Devon 2026-10-18; plan 1 (readiness command) built in project-standards PR #49
- **First repository:** `AlobarQuest/RentVSBuyGA`
- **Runbook it automates:** `docs/operations/onboarding-a-target.md`

## Intent

Onboarding a repository as an SDS factory target is AI-heavy today. A session reads the runbook,
improvises each step and records what it learned as prose. The results vary, and a lesson from one
onboarding reaches the next only if a later session happens to read it.

This design replaces that with one repeatable process. A deterministic command decides whether a
repository is ready and produces the work needed to make it ready. A separate deterministic
procedure onboards a ready repository once Devon says yes. AI does only the work that needs
judgment, inside a repository, from a generated handoff document. Devon does only the work that
needs him, as a task worker with generated instructions. Each onboarding's lessons become code in
the command, so the next onboarding inherits them.

## The process

1. **Devon runs the readiness command** against a repository checkout.
2. **The command measures every requirement** for being a target: the portfolio standards checks,
   every step in the runbook, and the anti-goal guards in the "Acceptance bar" section. It sorts
   each failing requirement by owner:
   - **Repository work:** changes inside the target repository that need judgment (for example,
     writing a test suite). These go into a generated **build handoff document**.
   - **Devon's tasks:** steps only Devon can do, such as editing a GitHub settings page or making a
     decision. These go into a generated **task list**.
   - **Onboarding actions:** deterministic steps the onboarding procedure performs. They aren't
     gaps; the command lists them as the plan.
3. **Devon gives the handoff to a build agent** in the target repository with "Read this and
   proceed to making this repo ready to be moved to SDS." The agent works until the readiness
   command, run by the agent, comes back clean for its share.
4. **Devon completes his tasks** and records each as done (see "Devon's tasks").
5. **Devon runs the readiness command again.** When everything is clean, the command says the
   repository is ready, lists the onboarding actions it will take, and asks whether to onboard.
6. **On yes, the onboarding procedure runs** the actions in a fixed order, then reruns the
   readiness command and a canary unit as its proof (see "Acceptance bar").

The readiness command at step 1 and step 5 is the same command with the same checks. "Ready" has
one definition.

## Goals

- One command that answers "can this repository be onboarded?" deterministically, with the same
  answer for the same state.
- A build handoff document generated from check results, complete enough that a build agent with no
  other context can make the repository ready.
- Devon's tasks generated with instructions and context, and onboarding refused until each is done.
- A deterministic onboarding procedure that performs every automatable runbook step the same way
  every time.
- Lessons captured as code: a new check, better fix text, or a new onboarding step.

## Non-goals

- Automating the steps that need Devon. They're tasks today; when Devon automates one, it moves
  into the onboarding procedure without changing the process.
- Using the factory itself to make a repository ready. intent-packages' `factory create
  --from-readiness` turns a readiness result into factory work, but the factory can't work in a
  repository that isn't a target yet.
- An AI-written handoff. The document is generated from check data. Where a fix needs judgment, the
  handoff says what "done" means and leaves the how to the build agent.
- A Todoist or other external task integration (Claude's call; see "Calls made").

## Anti-goals

The process has failed, even if every check passes, if either of these happens:

1. **Readiness work breaks the functionality or appearance of a website the repository serves.**
2. **Once onboarded, SDS doesn't work with the repository.**

## Acceptance bar

These requirements answer the anti-goals.

**Behavior and appearance are preserved.** The build handoff requires the build agent to:

- capture a baseline before changing anything: screenshots of the site's key pages at desktop and
  phone widths, and the responses of its API routes and health endpoint;
- capture the same after its changes, from a local build of the branch;
- write both, with a comparison, to an evidence file in the repository the readiness command
  checks for;
- treat any visible or behavioral difference as a failure unless the handoff named it as intended.

The readiness command checks that the evidence exists and records no unexplained difference. It
can't judge screenshots; the evidence makes the comparison reviewable. Onboarding never deploys
anything, and the readiness work must not deploy either.

**SDS works with the repository.** Onboarding isn't complete when the settings are applied. It's
complete when:

1. the caller probe answers `404 work_unit_not_found` (runbook step 6);
2. the readiness command reruns clean;
3. a **canary unit** runs end to end: a small documentation change, authored from a fixed
   template, admitted, dispatched, verified by the repository's own tests, and landed by SDS, with
   its landing recorded in the landing ledger.

The kit already names the canary as the step after readiness ("a docs-only canary through the
factory is the separate final step"), and nothing runs it today. Its human gates are Devon's tasks
(see "Calls made").

## Where it lives

Claude's call, from what exists: extend the conformance kit in `~/Projects/project-standards`,
which already has `portfolio onboard <repo>`, the readiness schema (`portfolio-readiness/v1`), and
most of the checks (`onboard_checks.py`, `factory_checks.py`, `dispatch_app.py`). Building a new
tool would duplicate them.

- `portfolio onboard <repo>` stays the readiness command. It gains the checks it lacks (see
  "Checks to add"), writes the handoff and task list when there are gaps, and asks the onboarding
  question when there are none.
- The onboarding procedure is a separate entry point so it can be run, and tested, on its own, and
  so a "yes" can't be scripted past by accident. Its name is left to the builder.

## Readiness command

### Checks to add

The kit measures most of the standards and part of the factory steps today. It must also measure:

| Requirement | Runbook step | Note |
|---|---|---|
| `factory-target.toml` declares `true` with a reason, on the default branch | 1 | read as admission reads it, from GitHub |
| Caller workflow at `RECOMMENDED_CALLER_PIN` with exactly the `work_unit_id` input | 2 | `runner.caller` covers the pin today |
| Repository named in both `.cred-consumers.toml` inventories | 3 | orchestrator and factory-runner |
| Inert or redeploys decided and recorded in App Brain | 7 | App Brain also needs an app record |
| The recorded answer is `inert` | 8 | `redeploys` is refused; see "Scope" |
| Branch protection and merge settings exactly as step 9 lists | 9 | stricter than `repo.protection` today |
| Before-and-after evidence for a repository that serves a site | anti-goal 1 | |
| Devon's recorded task completions | all | see "Devon's tasks" |

Some of these belong to onboarding rather than readiness. For example, the declaration is
something onboarding writes. The readiness command reports those as planned onboarding actions,
not gaps, before onboarding, and as checks after it.

### Fixes to existing checks

Found while surveying RentVSBuyGA on 2026-10-18:

- `repo.protection`'s fix suggests `required_approving_review_count=1`, which the runbook forbids
  (a solo account can't approve its own pull requests).
- `ci.executed` reports "gh run list failed (check gh auth)" when the repository has no workflows
  and `gh run list` succeeds with an empty answer.
- The `factory.*` checks report out of scope unless `PROJECT.md` declares `delivery_profile`
  (`in_q2_scope`). ADR-0015 calls that an interim proxy. Readiness must measure them for any
  repository being onboarded.

### Every check owns its handoff text

Each check carries, alongside its status:

- its **owner**: repository work, Devon's task, or onboarding action;
- **why it matters**: one or two sentences a build agent or Devon can act on without the runbook;
- **what done means**: the observable condition the check tests;
- **how to fix it**, where that's deterministic (a command), or the constraints on fixing it,
  where it needs judgment.

The handoff document and task list are rendered from these fields and nothing else, so the same
gaps always produce the same document. Improving an instruction means editing the check.

## Build handoff document

Rendered into the target repository's checkout (path left to the builder; it must not be committed
by the onboarding procedure). It contains:

1. what SDS onboarding is, in one paragraph, and the instruction to work until the readiness
   command is clean for the repository-work checks;
2. the repository's facts the command gathered (language and tooling, deploy shape, current CI,
   the before-and-after evidence requirement);
3. each failing repository-work check, with its why, done, and fix fields;
4. the standards that apply, by path: `~/Developer/code-standards/STANDARDS.md`, the security
   scanner command, and the instruction to query Code Brain before adding a cross-cutting pattern;
5. the anti-goals, stated as hard constraints;
6. the exact command to rerun, and what clean looks like.

## Devon's tasks

Each task has instructions, context, and a way to mark it done. Where a check can confirm a task
(for example, the caller probe confirms `FACTORY_PR_TOKEN`'s access list), the check is the
completion signal and Devon's mark is only a prompt to recheck. Where nothing can confirm it (a
decision), Devon's recorded answer is the completion, and it's stored where the next readiness run
reads it. The storage location is left to the builder.

Tasks for RentVSBuyGA as surveyed:

- Decide whether to onboard it at all. It's a low-activity app.
- Decide inert or redeploys. Coolify builds it from source with a read-only deploy key and nothing
  triggers a deploy on push; production runs an April 2026 commit. A landing is inert in practice,
  but the next manual deploy ships whatever is on `main`.
- Add it to `FACTORY_PR_TOKEN`'s repository access list on GitHub's settings page.
- Approve the canary unit's authority at `/review`.

## Onboarding procedure

Runs only after a clean readiness result and Devon's explicit yes. **Devon decided (2026-10-18)
that a yes he gives in chat counts:** a session may pass it to the procedure, for example as a
confirmation flag, and the onboarding record notes that the yes came through a session. The
procedure must still refuse without an explicit confirmation, so it can't run by default. It
performs these actions, each idempotent, in this order:

1. Commit `factory-target.toml` and the caller workflow (from project-standards' template, pinned
   to `RECOMMENDED_CALLER_PIN`) to the target repository.
2. Add the repository to both `.cred-consumers.toml` inventories.
3. Set the four Actions secrets from their sources (runbook step 3), fetching and setting each in
   one process so no value enters a transcript or argument.
4. Record the App Brain landing answer from Devon's decision.
5. Apply branch protection and merge settings (step 9), then read them back.
6. Run the caller probe.
7. Rerun the readiness command.
8. Author, approve (Devon's task), dispatch and land the canary unit; confirm the landing ledger
   recorded it.

How actions 1 and 2 reach `main` in each repository (direct push, or a pull request the procedure
merges) is left to the builder, within each repository's protection rules. Any action that fails
stops the procedure with what was done and what wasn't; a rerun resumes, because every action is
idempotent.

## Capturing lessons

Every onboarding writes a record: the readiness results before and after, Devon's task answers,
each action's outcome, and any surprise. A surprise is anything that went wrong that no check
predicted. Every surprise gets one of these, in the same change that resolves it:

- a new check, or a stricter existing one;
- better why, done or fix text on an existing check;
- a new or changed onboarding action.

The record lists which. Where the record is kept is left to the builder.

## Constraints

- Secrets never appear in a prompt, a tool argument, a log or the record (global CLAUDE.md, and
  the orchestrator memory on secrets in tool arguments). `gh secret set` reads stdin only without
  `--body`.
- The process must not deploy anything.
- The readiness command never mutates; only the onboarding procedure does, and only after yes.
- Every orchestrator invariant the runbook cites still applies; in particular, branch protection
  never sets a required review count, and the required check is a job name verified against a real
  pull request.
- `make check` in the orchestrator needs Postgres, so it can't be any envelope's verifier; the
  canary's verifier must be a command a hosted runner can run.

## Calls made

These are Claude's calls, not Devon's decisions; check them while reading:

- The process extends `portfolio onboard` in project-standards rather than adding a new tool.
- Devon's tasks appear in the command output and a task file, not in Todoist.
- The handoff is generated from check fields, never written by AI.
- Onboarding is a separate entry point from the readiness command, and refuses without an explicit
  confirmation.
- **Every human gate the canary meets is one of Devon's tasks**, and the procedure is resumable
  across them. The canary meets several: registering its intake (a machine may register one only
  by naming an approved change record, driving-a-unit.md #241), approving its decomposition, and
  approving its unit's authority. Its package carries no `human_review` criterion, so completion is
  verifier-decided and needs no gate. Action 8 is therefore several resumable steps, each waiting
  on a task, not one step. An HQ session may drive these `/review` gates for Devon under the
  build-session pre-authorization, recorded in the onboarding record.
- **The canary uses the existing `software-delivery` change class, not a new docs class**, which
  would be a standing-authority change. It makes a small documentation edit, and its verifier is the
  repository's own test command, the one `ci.executed` proved runs and collects tests. That makes
  the canary prove the repository's CI works under SDS, not only that a file landed.
- **Phase is read from the repository, not stored.** Each onboarding action has a check that
  reports it done or not done. Before any is done, the command reports them as the plan; once any
  is done, it reports each as a check, so a half-onboarded repository reads as "resume onboarding",
  never as ready. Ready means every repository-work check and every task is clean; onboarding
  actions may be in any state, because each is idempotent.
- **The readiness command measures the `factory.*` checks for the repository it's run on, whatever
  `PROJECT.md` declares.** The nightly sweep's scoping (`in_q2_scope`) is unchanged, so other
  repositories gain no new findings (ADR-0015's noise concern).
- **The build agent merges its own readiness pull request** once the repository's Quality job
  passes on it and the before-and-after evidence records no unexplained difference. Devon makes no
  pull requests, and the target has no protection yet. The handoff states this as the agent's
  closing step.

## Left to the builder

- Command and file names, the handoff's location, and where task answers and records are stored.
- How onboarding's commits reach `main` in each repository.
- How the before-and-after evidence is captured and compared, within the acceptance bar.
- How the canary's intent package is authored and driven through intake, decomposition and
  dispatch, using the existing `factory` CLI and `driving-a-unit.md`.

## Scope: inert repositories only

**Devon decided (2026-10-18) that the first version onboards inert repositories only.** A
repository is inert when merging to its default branch changes nothing already running until
someone deploys separately. It redeploys when a merge triggers a deploy on its own, through a
workflow on push, a repository webhook, or the host's git integration.

- The readiness command checks Devon's recorded inert-or-redeploys answer. A `redeploys` answer is
  a clear refusal naming the reason ("redeploying repositories aren't supported yet"), not a gap
  for the build agent or a task for Devon.
- Runbook step 8 (deploy policy and `deploy_watcher`) is out of scope. Supporting it later adds
  onboarding actions and checks; the process doesn't change shape.
- Recording `inert` is still a judgment Devon makes as a task, with the three triggers listed in
  the task's instructions. The survey found none firing for RentVSBuyGA, so it qualifies.
