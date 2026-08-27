# ADR-0033 — A producer that writes a revision also publishes it

- **Status:** Accepted
- **Date:** 2026-08-27
- **Decided by:** Devon
- **Relates to:** ADR-0028 (a standing package per repository, revised per bump), ADR-0020 (the
  factory closes its own loop), ADR-0019 (SDS-initiated production deploys route through
  change-manager)

## Decision

**`bump_proposer` pushes the revision commit it writes.** `standing.commit` gains a push to the
authoring repository's default branch, and
`tests/architecture/test_wsp21_invariant_scan.py::MERGE_EXEMPT_PATHS` gains a third entry for
`src/bump_proposer/standing.py`, openly and with its reason — the door's own contract.

The grant is bounded to what the producer can already do: **a revision of a package whose author
declared `profile_fields.standing = true`, whose target repository is a member of the current
`approval-policy.toml` grant, and whose shape that policy checks exactly.** The producer cannot
approve the change record it writes (its change-manager bearer is propose-scoped by construction),
cannot create work, and cannot dispatch.

## Context — the two candidates put the identical commit on `main`

ADR-0028's producer commits a revision to the authoring checkout and never pushes
(`standing.commit`: *"Never pushes; the branch is somebody else's to move"*). That was correct
while a person invoked the producer and could push in the same breath. Scheduling it (2026-08-27,
`com.devon.bump-proposer`, 06:50 daily) severs the two: the pass commits unattended, and **nobody
is told to push.** The commit sha is not even in the pass's own output — `cli.py:239` discards
`commit()`'s return.

Three candidates were considered when the scheduler shipped. **(b)** — commit on a branch, push,
open a pull request — was rejected on measurement: its only claimed precedent does not exist.
Record 61's intake `source_commit` is a post-merge commit on `main`, and the three revision commits
on `origin/tier-c-approval-policy` were never any intake's provenance. **(c)** — change nothing —
was kept at the time because record 62's `source_commit` is a commit this producer wrote onto local
`main`, after which the lane completed end to end. This ADR takes **(a)**.

**What decided it is that (a) and (c) put the SAME commit on `main`.** The only difference is who
performs the act and when.

## Why the human push was not a gate, and could not become one by accident

Nothing tells anyone to push, and the pass does not report what it committed. A step nobody is
assigned and nobody can see is not review; it is delay of unbounded length. So the authority being
granted here is **not** "may a machine put unreviewed content on `main`" — that is already what
happens. It is "may the machine finish the act it already performs, rather than leaving it
half-done for a person who was never told."

Three costs of leaving it half-done, each measured:

- **The revision has no CI at all** until someone pushes. `intent-packages` `main` carries
  `enforce_admins: false`, `strict: false` and three required contexts, so a direct push **succeeds
  and the checks report afterwards — whoever performs it.** (a) therefore surrenders no gate that
  (c) possessed; it obtains the same post-hoc verdict, sooner and reliably. This is the clause most
  likely to be misread: pushing does not bypass a gate here, because there was never a gate on this
  path to bypass.
- **`source_commit` names a commit only one machine holds.** `emit-intake-payload` records it as a
  bare `git rev-parse HEAD`, so the traceability chain's `commit` hop is unresolvable elsewhere for
  the whole window.
- **Local `main` diverges.** Unpushed commits while `origin` advances leave the authoring checkout
  needing manual reconciliation — and the carry, the activation sweep and every other lane read
  that checkout. This is (c)'s slow burn.

**Be precise about what makes it bite, because the obvious answer is wrong.** Widening
`approval-policy.toml`'s grant does NOT multiply the passes that commit: the producer's scan scope
is derived from the standing packages on disk (`bump_proposer/cli.py:336`), not from the grant, so
a wider grant with no new packages still looks at one repository and still commits nothing. What
multiplies unattended commits is **standing-package coverage**, which is a separate and still-open
decision. The widening is its precondition — a package targeting a repository outside the grant
would be minted and then refused at approval — not its trigger. So this ADR is not urgent because
of the widening; it is taken now because the scheduler already severed the commit from the push,
and every pass that ever does commit from here leaves work half-done.

## Blast radius, and the control that already exists

An unattended commit can turn `intent-packages` `main` red. Two things bound it. `strict: false`
means required checks bind a pull request's own head, so a red default branch does not block other
merges. And that repository carries the daily scheduled `Quality` run added 2026-08-15 — verified
green on five consecutive days at this decision — so a bad push is reported within a day by a
control nobody has to build.

## Consequences

- **The exemption is taken openly.** The guard's own message requires it: *"add it to
  `MERGE_EXEMPT_PATHS` with a reason — openly, never by rewording."* The push must be spelled
  `git push origin main`, the literal `MERGE_ACTIONS` scans for. Reaching for `HEAD:main` or any
  other spelling to avoid the entry is precisely the evasion that comment forbids, and it would
  leave the estate's one register of who may write to a default branch silently incomplete.
- **This is the third entry and the first that is not about landing a pull request.** The two
  existing entries each rest on an Accepted ADR and neither's justification carries over: there is
  no work unit, no envelope a human approved, and no change record. What stands in their place is
  the policy that checks the revision's shape exactly, the `standing = true` declaration only a
  human author can make, a target repository named in that policy's grant, and a default branch the
  estate reports as `inert`.
- **Attribution stays untrue, and this ADR does not fix it.** The producer's commits are authored
  `AlobarQuest` because that is the checkout's git identity; the message says *"Written by
  bump-proposer (ADR-0028)"*, so the marking is true and the identity is not. Pushing extends that
  to the push, which uses the `gh` credential helper — Devon's own. This is the same shape as
  factory-runner opening pull requests with `FACTORY_PR_TOKEN` and GitHub reporting
  `type: "User"`. Giving the producer its own committer identity is cheap and separable; it is
  named here so it is a known residual rather than a discovery.
- **A push failure must not be silent.** The pass's exit-code interface is the whole of what a
  scheduled run reports, and `launchd` discards it. A push that fails leaves exactly the state (c)
  produced, so it is a finding, not a warning.
- **Nothing about the approval path changes.** The record is still created `pending` by
  construction, a person still approves it, and the decomposition and authority envelope after that
  are unchanged. This ADR moves one `git` invocation.
