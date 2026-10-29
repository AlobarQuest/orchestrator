# Landing: bumps land on the grant, and a bad one comes back out

- **Date:** 2026-10-29
- **Layer:** `landing` in `layers.toml` (`make layer L=landing`)
- **Programs:** `inert_lander`, `estate_lander`, the shared `lander` core, `services/landing/` and its
  routes
- **Decisions it builds on:** ADR-0016, 0018, 0019, 0020, 0023, 0024, 0028 (amendments 2 and 3),
  0034 to 0038, 0041, 0044, 0045, 0058
- **Status:** Approved by Devon, 2026-10-29

## Intent

The SDS is meant to maintain Devon's projects the way an IT team would. For dependencies, Devon's
part ends when he adds a repository to the SDS and grants it the `dependency_updates` service. From
then on, every bump in that repository reaches production with no further human approval, and the
SDS keeps a record that it happened. If the SDS itself can see that a bump broke something, the bump
comes back out of production, the dependency is held at the last version that worked, and Devon gets
an alert.

Today the routine half of that already works. The other half doesn't exist. A bump that fails its
checks becomes factory work and then waits on two clicks and several hand-run steps. Nothing takes a
bad bump back out. Nothing in this layer tells anyone when it's stuck.

This design brings Landing to the four outcomes Devon set for every layer:

1. **Every signal has a home:** an observation, a change-manager record, or alert-hub (ADR-0021
   amendment 1).
2. **It works on what Devon chose:** scope comes from the SDS member record and its per-service
   grants.
3. **Its records can be trusted:** nothing it writes changes meaning later or rests on a value the
   outside world can move.
4. **It has clear edges:** inputs, outputs and boundaries are named and enforced by tests.

## What was measured

Recon on 2026-10-29 at `dee947de` read the code, both lander logs, GitHub, and the orchestrator
through the observer bearer. Nothing was written. Window: 2026-09-29 to 2026-10-29.

**The "native cascade" no longer exists.** ADR-0038 removed the Dependabot auto-merge workflows from
every repository on 2026-08-31. ADR-0016, 0018 and 0035 are superseded. Every landing goes through
one of three orchestrator routes, and the Dispatch App performs every merge.

| Path | What and when | Scope comes from | Gates | Measured |
|---|---|---|---|---|
| Inert lane: `inert_lander` → `POST /api/v1/inert-pr-merge` | Dependabot and fork upstream-sync PRs into 11 repositories, hourly at :35 | change-manager's `inert_landing` list | Author, ecosystem (docker excluded), green checks, head current, holds | 57 landed (39 Dependabot, 18 fork syncs); median 0 to 1.4 h from open to merge |
| Estate lane: `estate_lander` → `POST /api/v1/estate-pr-merge` | Dependabot PRs into brain and change-manager, 02:15 to 05:15 | Approved `dependency-update` change records | The inert terms, plus the record, the `live_estate` window, the rollout-workflow pin and a pace of one landing per repository per window | 40 landed; median 47 h (brain) and 32 h (change-manager), mostly the pace |
| Factory merge: `POST /work-units/{id}/pr-merge` | A completed unit's PR | The unit | Verifier-decided completion, the merge capability, **a human approval bound to the exact fingerprint in every repository** (`pr_merge_admission.py:300, 328`), holds; plus the record and window where landing redeploys | 2 merges (orchestrator#378, project-standards#56), minutes after opening. No scheduled caller, so a session called it. |

Branch updates (estate 40, inert 27) and closing a held PR (never run; no hold placed yet) are acts
inside the two lander paths. The lander logs and GitHub agree exactly: 97 landings, none by a person,
none armed for auto-merge.

**Where the hand work is.** orchestrator#506 and #508 have failed `Quality` for 4.4 days. The bump
proposer made packages, change-manager's work policy approved records 170 and 171, and the carrier
registered intakes on 2026-10-28. Then nothing: no program runs `factory decompose`, decomposition
approval is human-only, and after that a session must dispatch, call `/verify` and call `pr-merge`,
and Devon must approve the merge. Nobody has been told.

**Findings against the four outcomes:**

- **Signal home.** Lander findings reach only `~/Library/Logs/*-landing.log`. Both launchers declare
  exit 3 as their finding code, so `sds_deadman_finish` pings success for it. Nothing reaches
  alert-hub.
- **Scope.** No landing path reads a grant. All three admissions read holds from the member record
  and nothing else from it. `membership_check` requires each repository on the landing policy's lists
  to hold `dependency_updates`, but not the reverse, and it only reports. Fork syncs land with no
  grant term.
- **Records.**
  - Two rules pick "the update bot's commit": the landing ledger and bump proposer read `commits[0]`
    with a verified signature (#544, #545); `terms.py::_update_bot_message` takes the one
    Dependabot-authored commit anywhere, unverified. They disagree on an unverified commit or on two
    bot commits.
  - When the remote refuses a landing and the confirming read also fails, `lane_act.py:328-341`
    writes a terminal `refused` row. Its comment says the landing ledger settles it; no code does.
    Not observed in the window.
  - Approved records stay approved after their PR lands, so the estate lander re-reads a growing set
    each pass (10 of 16 were `settled` on 2026-10-29).
- **Edges.**
  - Both lander `orchestrator_client` modules accept any non-GET method as a write. The bridge lanes
    were narrowed to POST in #533.
  - The SYSTEM role has no route confinement, so the landers' bearer can reach `pr-merge` and every
    other SYSTEM route.
  - The estate lander borrows `change_proposer`'s change-manager client.
  - Nothing checks that target repositories carry no auto-merge workflow. project-standards'
    `sds_checks.py:264-265` still flags `auto-merge-off` as a violation, and
    `docs/operations/onboarding-a-target.md:158` still says to enable `allow_auto_merge`.
- **Stale text.** ADR-0019 records increments only to 5b, though ADR-0045 cites an "Increment 6".
  ADR-0019 and ADR-0020 give the window as 02:00 to 06:00. ADR-0024 says one predicate serves acting
  and reporting. ADR-0025 says factory records join the estate report. `landing-lanes.md` says
  `enforce_admins` is on for factory-runner alone. `driving-a-unit.md` #141 says `decompose` can't
  emit a merge-granting envelope. The bridge spec and `landing-lanes.md` say "cascade" for the inert
  lane.

Historical, not current: 158 "unreadable" verdicts from GitHub 403s cluster on 2026-10-21, and the
fatal passes are BWS read failures; the last of both was 2026-10-23. brain#102, a bridge-spec lead, is
merged.

## Decisions Devon made

Recorded 2026-10-29, in this conversation:

1. **A dependency bump needs no further human approval.** The approval is adding the repository to
   the SDS and granting it `dependency_updates`. This covers Dependabot's own PRs and the factory's
   fixes for failed bumps.
2. **A record that it happened.** Every bump that lands, and every one that comes back out, is
   recorded.
3. **"An issue with the bump" is something the SDS itself knows:** a failed SDS check, a failed
   deploy, or anything else the system is aware is broken because of the bump. When one occurs, the
   bump doesn't stay in production, and an alert is raised.
4. **Problems found later are a rollback decision,** made by a person, not automatic.
5. **The removal is a revert, landed the way the bump landed,** so `main` stays equal to production.
6. **After an automatic revert, the SDS places the hold itself,** with no confirmation.
7. **The hold is an ordinary ceiling at the last known good version** (the version before the bump),
   and it stays until someone lifts it.
8. **The post-landing watch and the revert are built into the landers,** not a separate process.
9. **Where a repository's rollback plan targets the image (brain), the SDS re-points the image
   first, then lands the revert.**

## Goals

- In every repository granted `dependency_updates`, a bump lands with no human act, whether
  Dependabot's PR passes or the factory has to fix it, unless a hold applies.
- Every landing path checks the grant, and the grant is the authority that replaces the per-unit
  approvals for dependency-update work.
- When the SDS observes that a landed bump broke something, the bump is reverted, the dependency is
  held at the version before it, and Devon gets one alert naming the hold and how to lift it.
- Every bump that lands and every revert is recorded in the landing ledger and the orchestrator,
  joinable from the signal to the deploy.
- Every finding a lander makes reaches alert-hub.
- One test states every lander's outbound surface.

## Non-goals

- Graduating any gate for work other than `dependency-update`. App changes keep their clicks.
- Reverting for problems found later (decision 4).
- Rolling back Docker base-image bumps. They stay excluded from landing (ADR-0023).
- Rolling back a fork upstream sync. It isn't a dependency bump, and fork-sync has its own security
  review. *Claude's call.*
- Narrowing the `live_estate` window back to night. That happens when the SDS goes into operation.
- Rotation units, which never land.
- Locking an exact version in intent-packages' uv mutator (its own backlog item). Until then, the
  factory never fixes a held uv dependency, which is acceptable because a held dependency is frozen.

## Anti-goals

These would make the work a failure even if every test passed:

- **A revert loop.** The SDS never reverts a revert, never re-lands what it reverted, and stops with
  an alert if a revert itself fails.
- **A revert that removes more than the bump.** The revert's change must be exactly the inverse of
  the bump's merge. If it isn't (a later commit touched the same lines), the SDS doesn't merge it,
  and alerts.
- **A widened hold writer.** The automatic hold is the one exception to "only a person confirming on
  `/review` writes the member tables". It may place only a ceiling, only on a dependency the SDS
  itself reverted, only at the version that bump moved from, and never lift or change any hold.
- **A grant that widens itself.** Scope comes only from grants Devon confirmed. No lander discovers a
  repository and adds it.
- **Blaming the wrong bump.** A revert happens only when the failure is tied to one landing's merge
  commit. When it can't be tied to one bump, the SDS alerts and doesn't revert.
- **Alert noise.** One alert per subject, keyed and resolved when the subject clears, never one per
  pass.
- **New machinery where a pattern exists.** Alerts reuse `rotation_worker/notify.py`'s keyed-alert
  pattern and the outcome alarm; holds reuse the member record's ceilings; the revert lands through
  the existing lander paths.

## Constraints

These invariants in the orchestrator's `CLAUDE.md` govern the work:

- Known-good patterns and `[admission]` lists in `factory-policy.toml` are standing authority:
  changing one is a release, and the dependency-update budget stays equal in its five places
  (`scripts/check_profile_budget_agreement.py`).
- Only `services/membership/proposals.py` writes the member tables, enforced by
  `test_membership_writers.py`. The automatic hold changes this rule, and the rule's text and test
  change with it.
- Every path that lands or proposes a bump honours holds through the shared matcher, classified by
  `tests/architecture/test_hold_paths.py`. A revert path is classified there too.
- Landing-ledger observations are content-addressed and immutable: a change to what the ledger
  derives must handle every stored row whose facts would move.
- `UnitPrMerge` records only the orchestrator's own merges; a landing's commit comes from the
  ledger's `landing` observation.
- Branch updates use `update-branch` with `expected_head_sha`, one Dependabot branch per repository
  at a time (ADR-0045).
- Whether landing redeploys is read from App Brain's `default-branch-landing` route, never derived.
- A new route joins the inventories in `test_scope_guards.py` and, if it is an ingress POST,
  `tests/idempotency/test_matrix.py`. Word guards (ADR-0051) apply to code under
  `src/orchestrator/`.
- The scheduled lanes run the main tree's working copy; all edits happen in a worktree. Admission
  terms run in the deployed image, so deploy order matters: a lander change that relies on a new term
  waits until production serves it (read `openapi.json`).
- Every parsed `bws` call passes `--color no`; secrets are fetched in-process and never echoed.

## The design

### Part 1: Findings get a home, and the edges get pinned

**Findings.** The landers keep their dead-man checks, which already route to alert-hub, because
94% of the last 300 inert passes held something (the current streak is 166), so a `/fail` per held
pass would alert once and then say nothing new. Instead one keyed alert-hub alert fires per PR held
past 24 hours, and it resolves when the PR stops being held (a repository GitHub couldn't list
holds its alerts rather than resolving them), so #506 would have alerted. The threshold is a plain
int with a real default (`docs/method-lessons.md` #32).

**Edges.**

- Both lander clients accept POST only, with surface tests that send PUT, PATCH and DELETE.
- The estate lander gets its own read-only change-manager client instead of `change_proposer`'s.
- `tests/architecture/test_landing_edges.py` (or a row in the bridge's table) states each lander's
  orchestrator routes, change-manager routes and bearer.
- *Claude's call:* confine the landers' SYSTEM bearer server-side only if it can be done without a
  second SYSTEM identity; otherwise record it as a residual. A dedicated lander identity is an
  M2M-registry change (security-standards) and a separate decision.

**No auto-merge, anywhere.** `membership_check` gains a row: no member repository carries an
auto-merge workflow or has `allow_auto_merge` on. project-standards' `sds_checks.py` stops flagging
`auto-merge-off`, and `onboarding-a-target.md` says to turn it off.

**Docs.** Fix the stale text listed in "What was measured", as ADR amendments where an ADR is wrong.

### Part 2: The grant is the authority

Every landing admission gains a grant term, read from the member record the way dispatch admission
already does (`services/execution/target_grant.py`):

| Path | Grant |
|---|---|
| Inert lane, Dependabot PR | `dependency_updates` |
| Inert lane, fork upstream sync | `security_patches` and `dependency_updates`, the pair `membership_check` already requires of forks (`consistency.py:125-126`) |
| Estate lane | `dependency_updates` |
| Factory merge | by change class, as `target_grant.py` maps it |

A missing grant refuses with a named reason. `membership_check` compares in both directions: a
granted repository that no landing path reaches is a finding too.

The landing policy's `inert_landing` and deploying lists stay where they are. They state a fact about
the repository (whether landing redeploys), not a grant.

### Part 3: A bad bump comes back out

Built into the landers (decision 8). Applies to every bump the SDS lands: Dependabot PRs today, and
factory fixes once Part 4 ships. It ships before Part 4, because it is the control that replaces
Devon's click.

**Watch.** Each lander pass also looks at its recent landings that are still within a watch period
(*the period is the builder's call*). A landing is **bad** when one of these is tied to its merge
commit:

- **A failed SDS check:** a check run on the merge commit on the default branch concluded failure.
  No code reads this today: the landing ledger reads jobs at the PR head before the merge, and
  `workflow_runs_at` reads whole runs. Part 3 adds the read. A repository whose checks don't run on
  push to its default branch has no such signal, and `membership_check` reports it.
  *Claude's call:* a failure counts only if it persists on one rerun of the failed job and the
  parent commit's same check passed. One flaky run must not cost a revert and a standing hold.
- **A failed deploy:** the rollout the deploy watcher observed for that merge commit failed. For a
  Dependabot landing the watcher writes this to the change-manager record (`changes.observe`); for a
  factory unit, to an orchestrator observation (`_observe_unit`). The lander reads both.
- **Anything else the SDS knows:** a post-deploy verifier criterion for the unit fails. The builder
  adds any further signal only where it ties to the merge commit.

The estate pace of one landing per repository per window stays: it is what lets a failed deploy name
one bump. *Claude's call.*

**Revert.** For a bad landing, the lander asks the orchestrator for a revert. The orchestrator:

1. Opens a revert PR for the bump's PR as the Dispatch App. *How (GitHub's revert-PR mutation or a
   commit built through the git data API) is the builder's call;* either way the PR's change must be
   exactly the inverse of the bump's merge, checked before it can land.
2. Records the revert, bound to the original landing, the failure that triggered it, and the revert
   PR.

A revert PR lands through the same lane as its bump, as a new subject class with its own terms:

- It must be a revert the orchestrator itself recorded, and exactly the inverse.
- Its checks must pass.
- It is exempt from the pace and the change window, because it restores the version production ran
  before. *Claude's call.*
- It is never itself watched for reverting. If it fails, the lander alerts and stops.

**A revert in a repository where landing redeploys** goes through change-manager like the bump
did (ADR-0019): the orchestrator files a revert record, and change-manager's policy approves it
automatically, the way its work policy approves a standing bump. That is a change-manager change in
Part 3. The revert's own rollout is then watched like any other. *Claude's call,* because Devon's
decision 5 says the revert lands the way the bump landed.

**Where the rollback plan targets the image, the image goes back first** (Devon's decision 9).
brain has no lockfile, so a rebuild after a revert can resolve different dependencies than
production ran; its transcribed rollback plan (`change_proposer/criteria.py::_ROLLBACKS`) names the
image as the target, and `docs/operations/auto-merge-rollback.md` prescribes re-point, then revert.
For such a repository the SDS:

1. Re-points the application to the image it ran before the bump: the image bound to the bump's
   parent commit (`image_release`'s binding), never a tag that can move.
2. Confirms production serves that image's revision before going on, and alerts and stops if it
   doesn't.
3. Then opens and lands the revert as above, so `main` matches production again.

The re-point is a new kind of act for the SDS: changing what Coolify runs, not only merging. The
word guards (ADR-0051) forbid it in `src/orchestrator/`, so it runs on the lander side through a
confined client (ADR-0050's `estate_clients` pattern), with a credential that can re-point only the
member applications whose plan says image. *The credential's shape and where it lives are the
builder's call,* under the BWS rules; it is registered like any other SDS credential.

**A bump the SDS reverted is never landed again,** whether or not a hold could be placed: every
landing admission refuses a bump to a dependency version the SDS has reverted. This closes the loop
even when the hold can't be written.

**Hold.** When the revert lands, the orchestrator places a ceiling hold on each dependency the bump
moved, at the version it moved from, with the ecosystem and directory as Dependabot spelled them. A
grouped Dependabot PR gets one hold per dependency. *Claude's call:* the from-version is read from
the PR's own metadata (Dependabot's body table and the merge's diff of the manifest). When a
dependency's from-version can't be read, the revert still lands and no hold is placed for that
dependency; the alert says so, and the never-again term still blocks the reverted version. The hold's reason names the revert. This is the
one automatic writer of the member tables:

- It lives in `services/membership/` beside the person's path, and `test_membership_writers.py`
  names it and the one route that reaches it.
- It refuses unless the orchestrator's own revert record for that landing exists and has landed.
- It never replaces an existing hold. If a hold already exists on that key, it leaves it and says so.

**Alert.** One keyed alert-hub alert per revert: what was reverted, why, the hold placed, and how to
lift it (`sds-members stage` a lift, then confirm on `/review`). It resolves when the hold is lifted.

**Record.** The landing ledger already records both landings; the revert's ledger basis names the
revert rule. The orchestrator's revert record joins the bump's landing, the failure, the revert
landing and the hold.

### Part 4: A bump unit runs with no hand work

Builds on Parts 2 and 3. For a unit whose change class is `dependency-update`, in a repository
granted `dependency_updates`:

- **Decomposition.** A registered dependency-update intake is decomposed by a program, not a session.
  `factory decompose` (intent-packages) is deterministic for this profile. *Which program runs it is
  the builder's call* between the carrier, right after it registers the intake, and the lander, which
  already walks the rest of this list.
- **Decomposition approval.** Today it is HUMAN-only (`decomposition.py::_require_decision_actor`),
  every unit's readiness needs `decomposition_approved_by` and `decomposition_approved_at`, and no
  precedent exists for skipping it: rotation units still get a human decomposition approval, and
  `authority.human_gate_not_required` lifts only the authority gate. So this is a new graduation.
  *Claude's call on the mechanism:* a named policy actor approves the decomposition (as
  change-manager's `work-policy` approves a record) when the decomposition comes from a standing
  dependency-update package in a granted repository and its envelope matches a known-good pattern.
  It records Devon's decision 1 as its basis. ADR-0006 (human gates are browser-only) gets an
  amendment for this one class. Precondition, from ADR-0028 amendment 1, built in this part: the
  envelope's mutation is bound to the package's dependency (`profile_fields.package`), so a
  decomposition can't bump something the package didn't name. That touches intent-packages and is a
  standing-authority release.
- **Dispatch, verify and merge.** The lander dispatches a ready bump unit and calls `pr-merge` when
  the unit completes. `/verify` is VERIFIER-only (`verifier.py:44`) and the landers hold SYSTEM, so
  *Claude's call:* the existing verifier lane (`rotation-verifier`, already VERIFIER) widens to verify
  bump units too, rather than the landers gaining a second identity. Scheduling `pr-merge` gets the
  same off switch the inert route has, since the route's own docstring voids the no-off-switch
  ruling once a scheduled caller is proposed.
- **Merge approval.** `pr_merge_admission` accepts, in place of the exact-fingerprint human approval,
  a dependency-update known-good recognition plus the `dependency_updates` grant. Every other change
  class still needs the human approval.

The ADR-0025 amendment 2 gates are graduated by an ADR amendment that cites Devon's decision 1.

### Part 5: Records stay true

- One rule picks the update bot's commit: fetch-metadata's (`commits[0]`, bot author, verified),
  shared by the hold term, the ledger and the bump proposer, with an agreement test.
- The terminal `refused` row is settled: the lander re-asks once the ledger shows whether the PR
  landed, or the branch is removed in favour of a retryable refusal. *Builder's call.*
- The estate lander skips records whose PR has already landed. *Whether that's a filter in the lander
  or the record's retirement in change-manager is the builder's call.*

## Order

1. Part 1: alerts first, so the rest is watched.
2. Part 2: the grant term, which Part 4's authority rests on.
3. Part 3: revert and hold, live on Dependabot's own landings first.
4. Part 4: the graduation, which removes the remaining clicks.
5. Part 5: any time.

Each part gets its own plan, approved by Devon, then subagent-driven execution, a whole-branch review,
a deploy and a live pass. Part 3's live pass needs a real bad bump, so it may be proven on a
deliberately broken bump in a disposable member repository. *How it's proven is the builder's call.*

## Left to the builder

- How the alert threshold and watch period are set (plain ints with real defaults).
- How a revert PR is built, given it must be checked to be the exact inverse.
- Which program runs `factory decompose`.
- How the terminal `refused` row is settled, and how settled records stop being re-read.
- Whether the grant check reuses `target_grant.py` directly or a shared copy for the lanes, held equal
  by `test_target_grant.py`.
