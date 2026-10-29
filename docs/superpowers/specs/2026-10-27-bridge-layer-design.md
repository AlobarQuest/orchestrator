# Proposers and carry: the bridge layer, hardened

- **Date:** 2026-10-27
- **Layer:** `bridge` in `layers.toml` (`make layer L=bridge`)
- **Programs:** `bump_proposer`, `change_proposer`, `work_carrier`, `work_watcher`
- **Decisions it builds on:** ADR-0019, ADR-0025 to ADR-0029, ADR-0033, ADR-0039, ADR-0058
- **Status:** Approved 2026-10-27. Parts 1 to 4 and the widening to repositories that deploy
  shipped by 2026-10-29: Part 1 #533, Part 2 #537, Part 3 #538 (with change-manager#142 and
  intent-packages#154), Part 4 #540, the widening #541 (with change-manager#143 and
  intent-packages#156), and its follow-ups #542, #544 and #545. Part 5 (verified carry) remains.

## Intent

The SDS is meant to maintain Devon's projects the way an IT team would. For dependencies, that means
one thing: **keep everything updated, and don't update only when there's a problem.** Devon hears
about a dependency only when something goes wrong.

The bridge layer is where a signal becomes work. Today it reaches almost nothing. Its lanes run, but
each one covers a hand-kept list of repositories, and what they find goes to a log file nobody reads.
On 2026-10-27 the first factory unit on `project-standards` had to be authored and walked through by
hand because no lane in this layer could deliver it.

This design brings the layer to the four outcomes Devon set for every layer:

1. **Every signal has a home:** an observation, a change-manager record, or alert-hub (ADR-0021
   amendment 1).
2. **It works on what Devon chose:** scope comes from the SDS member record and its per-service
   grants.
3. **Its records can be trusted:** nothing it writes changes meaning later or rests on a value the
   outside world can move.
4. **It has clear edges:** inputs, outputs and boundaries are named and enforced by tests.

## What was measured

Recon on 2026-10-27 read the code, the lane logs, change-manager (read bearer) and the member record
(observer bearer). Nothing was written.

| Lane | Schedule | Scope today | Measured |
|---|---|---|---|
| `bump_proposer` | daily 06:50 | Standing packages in intent-packages: three, all `infraops-mcp-server` (eslint, typescript, zod). The approval-policy grant (ADR-0028 decision 2) names only that repository. | 58 logged passes, no new proposal. orchestrator#506 and #508 have failed `Quality` for two days in an inert, granted repository; no lane picked them up. |
| `change_proposer` | hourly | `ROLLOUT_WORKFLOWS` ∩ `criteria._ROLLBACKS` in code: brain and change-manager. | 93 `deploy` records: 79 resolved, 14 approved. Nine are failed rollouts production moved past, kept `approved` by Devon's decision in ADR-0044; five are today's open pull requests. |
| `work_carrier` | daily 07:05 | Opt-in from each target's `factory-target.toml`; capability and permissions from the `factory` block of `~/.portfolio/portfolio.json`; rotation packages from the hard-coded `STANDING_OPERATIONAL_PACKAGES` (13 ids). | Of 21 `work` records ever, it carried one code-change lineage (infraops zod, four revisions); the rest were rotations. `project-standards` has an empty `factory` block, so the carrier would hold any record for it and exit 2. |
| `work_watcher` | first in the carrier's pass | Approved `work` records. | Retires completed records; reports stale ones only. |

The member record grants `dependency_updates` to 13 repositories, `app_changes` to 7 and `rollouts`
to 10. None of the four lanes reads it.

Cross-cutting findings:

- **Findings have no home.** Each lane exits 3 on a finding, and `sds_deadman_finish` sends
  Healthchecks a *success* ping for exit 3. The finding lives only in a log.
- **No central edge test.** Each lane pins its own change-manager route regex in its own tests.
  Nothing states, in one place, which routes and scopes each lane may use.
- **`membership_check` misses the bridge's own lists:** `_ROLLBACKS`, the standing packages'
  repositories and the approval-policy grant.
- **Two carried facts are taken on trust:** `source_commit` is the local checkout's `HEAD`, never
  checked as pushed; `change_record_id` is stored without checking change-manager.
- **`portfolio.json` has no age limit.** The carrier prints its age and acts on it whatever it is.
- **Update paths that end with nobody:** failed bumps in repositories that deploy (the proposer skips
  anything not inert), requirement-range bumps (unclassifiable), and Docker (excluded from the
  cascade, covered by no package).
- **Stale text:** `docs/operations/package-intake.md:108-109` says a person retires a carried record;
  `tests/change_proposer/test_change_proposer.py:98` and the `OUTBOUND_ALLOWLIST` entry for
  `change_proposer` describe a surface that grew.

## Decisions Devon made

Recorded 2026-10-27, in this conversation:

1. **The member record is the authority for scope.** This closes the question ADR-0058 decision 6 left
   open after phase 1: the record is the **source**, not a mirror checked against the declarations.
   Declarations it replaces are generated from it or retired. Per-repository configuration (the
   rollout workflow transcriptions) stays as guarded configuration.
2. **A `dependency_updates` grant covers every dependency in the repository.** There is no
   per-dependency opt-in.
3. **Dependency bumps are automatic.** A passing bump merges through the cascade, as today. A failing
   bump goes to the factory with no human click. Devon is told only when the factory can't make a
   bump pass.
4. **Holds.** When a bump breaks something and is rolled back, the dependency is held: no update path
   moves it beyond the held version until Devon says it's OK.
5. **Rollback is out of scope here.** It will be built later, in another session.
6. **App changes are out of scope here.** Devon is building from the bottom up, and the "a user needs
   an app changed" workflow is at the top.

Two consequences follow from decision 3 and are part of this design. Devon may object to either:

- **Known-good envelope.** `factory-policy.toml` gains a known-good pattern covering dependency-update
  work in any repository granted `dependency_updates`, so the envelope needs no click. Today the only
  dependency-update pattern is uv bumps in change-manager.
- **The factory merges its own fix.** `decompose` in intent-packages emits `github.pr.merge` for
  dependency-update work, and the standing package's "merging is left for a person" exclusion goes.

## Goals

- Every repository granted `dependency_updates` stays current with no human act, except where a hold
  applies.
- A failed bump in a granted repository becomes a registered intake, with an approved record, with no
  human click, whether the repository is inert or deploys.
- Devon receives exactly one alert per bump the factory cannot make pass (its unit fails, or its
  ecosystem has no factory tooling), and the alert resolves when the bump lands or is superseded.
- Every bridge lane takes its scope from the member record.
- Every finding a bridge lane makes reaches alert-hub.
- Every fact the carrier registers is verified, not trusted.
- One test states every bridge lane's outbound surface.

## Non-goals

- Rolling back a bump that broke something (decision 5).
- Graduating the decomposition-approval and merge-approval gates, and a driver that walks a unit
  from intake to merge. They belong to the Admission, Execution and Landing passes (see Part 3).
- Proposing app changes (decision 6). The carrier still carries an app-change record correctly when
  one exists.
- Security alerts that have no pull request. They belong to the `security_patches` service. *Claude's
  call.*
- The carrier's rotation allowlist (`STANDING_OPERATIONAL_PACKAGES`). Rotation packages have no
  target repository, so selecting them by a per-repository grant needs its own design. It's a lead
  for the Rotation layer pass. *Claude's call.*
- The nine ADR-0044 records that stay `approved`. That was decided; this design leaves them alone.
- brain#102, clean with an approved deploy record for five days and unmerged. It's a lead for the
  Landing layer pass.

## Anti-goals

These would make the work a failure even if every test passed:

- **A grant that widens itself.** Scope comes only from grants Devon confirmed on `/review`. No lane
  discovers a repository or a dependency and adds it.
- **A hold that some path ignores.** If any path can still merge a held dependency past its ceiling,
  the hold is worse than none, because Devon believes it.
- **Alert noise.** One alert per stuck bump, not per attempt, pass or lane. Alerts that fire daily for
  the same thing get muted, and then the real one is missed.
- **Silent narrowing.** An unreadable member record must stop a lane loudly (`sds_scope` exits 3),
  never shrink its population to whatever it could read.
- **New machinery where a pattern exists.** Alerts reuse the existing patterns; scope reuses
  `sds_members`; holds live in the member record, not a new store.

## Constraints

These invariants in the orchestrator's `CLAUDE.md` govern the work:

- Standing-authority changes (`factory-policy.toml` known-good patterns, the approval-policy grant,
  `[admission]` lists) are releases, and the dependency-update budget must stay equal in its five
  places (`scripts/check_profile_budget_agreement.py`).
- Dispatch admission reads `factory-target.toml` from the target's default branch. Decision 1 moves
  that to the member record, and the carrier and admission must move together.
- The capability vocabulary has four copies; adding `github.pr.merge` to `decompose`'s output touches
  intent-packages' `CAPABILITIES`. Grep the portfolio first.
- An envelope's verifier must exercise what the change could break; use the profile's generated
  command list.
- A package the factory lands must carry no `human_review` criterion.
- In change-manager, a field added to an asserted tuple 409s on every stored row; pass new fields as
  explicit keyword arguments.
- Observations and landing-ledger rows are content-addressed and immutable: any change to what a
  producer derives must handle every stored row whose facts would move.
- The scheduled lanes run the main tree's working copy. All edits happen in a worktree.
- Every parsed `bws` call passes `--color no`; secrets are fetched in-process and never echoed.

## The design

### Part 1: Findings get a home, and the edges get pinned

**Findings.** Each bridge lane's findings reach alert-hub. Two patterns already exist:

- The Healthchecks outcome alarm with the `alert-hub` channel (`run-membership-check.sh`): a finding
  pings `/fail` with the report as its body.
- The keyed alert-hub alert that resolves itself (`src/rotation_worker/notify.py`): one alert per
  subject, deduplicated by key, resolved when the subject clears.

Lane-level findings (a 409 on a frozen record, an unreadable rollout transcription, a stale record)
use the first. Today all three bridge launchers declare exit 3 as their finding code
(`sds_deadman_arm … --finding 3`), so `sds_deadman_finish` pings success for it. The launchers'
exit codes must be remapped so that a finding, and an unreadable member record (`sds_scope` exits 3),
both reach `/fail`. *How the codes are remapped is the builder's call;* each launcher's header states
its codes, and the scheduled launchers don't share meanings for 2 and 3. The per-bump "the factory can't make this pass" alert (Part 3) uses the second, keyed
on repository and dependency. *Which lane sends the per-bump alert is the builder's call;* the
`work_watcher` already reads every approved `work` record and its units' state.

**Edges.** One architecture test states, per bridge lane, the change-manager routes it may call, the
scope its bearer carries, and the orchestrator routes it may call. The per-package route tests stay;
the new test fails if any of them widens without the table changing.

**Membership coverage.** `membership_check` compares the remaining bridge configuration with the
record: `_ROLLBACKS` against `rollouts`, and any standing dependency-update package's repository
against `dependency_updates`.

**Docs.** Fix the three stale passages listed in "What was measured".

### Part 2: The member record is the source

| Lane | Selects by |
|---|---|
| `bump_proposer` | `dependency_updates`, and no longer the standing packages' repositories or the approval-policy grant |
| `work_carrier` | the grant matching the record's change class: `dependency-update` → `dependency_updates`; any other code change → `app_changes` |
| `work_watcher` | the same records the carrier carries |
| `change_proposer` | `rollouts` ∩ repositories with a transcribed rollout workflow |
| dispatch admission | the same change-class mapping as the carrier, replacing the read of `factory-target.toml` |

*Claude's call:* the grant follows the change class, because `dependency_updates` is granted to 13
repositories and `app_changes` to 7; selecting dependency work by `app_changes` would propose a bump
and then refuse to carry it. The carrier's rotation allowlist (`STANDING_OPERATIONAL_PACKAGES`) is left
for the Rotation layer pass; see "Non-goals".

Lanes read the record through `sds_members` (or `sds_scope` in a launcher). An unreadable record stops
the pass with exit 3 and an alert, never a smaller population.

The declarations this replaces:

- **`factory-target.toml`** in each target repository: retired, or generated from the record. *Builder's
  call,* recorded in an ADR-0058 amendment; either way, nothing reads it as authority.
- **The approval-policy grant** in intent-packages' `approval-policy.toml` (`target_repositories` for
  the dependency-update profile): generated from the record's `dependency_updates` grant, or replaced
  by a check against the record. *Builder's call;* nothing hand-edits it again.
- **The landing policy's inert list** in change-manager: stays where it is, since it states which
  repositories are inert, which is a fact about the repository, not a grant. `membership_check`
  already requires every inert repository to be granted `dependency_updates`; the reverse doesn't
  hold, since brain and change-manager are granted but deploy.

The carrier's capability and permission checks stop reading `portfolio.json`. They read the same
facts live, or the conformance kit's own result with a stated age limit. *Builder's call;* the
requirement is that a stale answer refuses rather than authorizes.

### Part 3: Dependency updates are automatic

The path, for a bump in a repository granted `dependency_updates`:

1. **Passing bump:** the cascade merges it, as today. In a repository that deploys, the existing
   policy-approved `deploy` record and the estate lander carry it. No change.
2. **Failed bump:** the bump proposer picks it up. If no standing package exists for that repository
   and dependency, the proposer creates one from the profile's template, then revises, approves by
   policy, publishes (ADR-0033) and proposes, exactly as it does for an existing package.
3. **The record is approved by policy.** ADR-0025 approves `factory-delivery` records by deploy
   policy; this extends policy approval to `work` records for failed dependency bumps in granted
   repositories. No click.
4. **The envelope matches known-good.** The new `factory-policy.toml` pattern recognizes the
   dependency-update envelope for any granted repository. No click.
5. **The factory fixes and merges.** `decompose` emits `github.pr.merge`. In a repository that
   deploys, the factory's pull request gets its `factory-delivery` deploy record from
   `change_proposer`, as today.

**Where this layer stops (Devon, 2026-10-27).** This design ends at a registered intake with an
approved record, reached with no human click. Graduating the two gates below for dependency-update
work, and building the driver that walks a unit from intake to merge, belong to the Admission,
Execution and Landing layer passes. Decision 3's goal ("a failed bump is fixed and merged with no
click") is their decided target. Until those passes land, a failed bump's unit waits for the two
approvals and a person to drive it. The steps 4 and 5 policy changes still ship here, so those passes
start from envelopes and grants that already permit the work.

The builder check found that steps 4 and 5 don't reach "no click" on their own. Two human gates
remain in code, both by design:

- Decomposition approval accepts only a human actor
  (`services/intake/decomposition.py::_require_decision_actor`).
- Merge admission requires a human authority approval bound to the unit's exact fingerprint, whatever
  known-good says (`pr_merge_admission`, refusing `authority_approval_not_bound`).

Nothing scheduled runs decompose, dispatch, `/verify` or `POST /work-units/{id}/pr-merge`, and the
pr-merge route records that a scheduled closer "is its own increment with its own decision"
(ADR-0020). Making failed bumps fully automatic means graduating those two gates for
dependency-update work and building a driver that walks a unit from intake to merge.
6. **If the factory can't make it pass,** the unit fails within its budget, and Devon receives one
   keyed alert for that repository and dependency. It resolves when a later bump lands or the
   dependency is held.

Coverage that widens:

- **Repositories that deploy.** The proposer stops skipping repositories that aren't inert. Among
  members, these are exactly brain and change-manager (13 members, 11 on the inert list), and both
  have transcribed rollout workflows, so `change_proposer` already gives their factory pull requests
  a `factory-delivery` deploy record. Reach for those packages is `live_estate`, not
  `source_repository`, so the known-good pattern and `factory-policy.toml`'s reach rows must admit
  it, including the `live_estate` change window.
- **The approval-policy grant** for the dependency-update profile widens beyond
  `target_repositories` (Part 2): its `reach` gains `live_estate`, and `merge_to_main` moves from
  `authority_requires_approval` to `authority_allowed`.
- **Requirement-range bumps** (for example, `>=84.0.0` to `>=84.2.1`). The proposer classifies them
  instead of dropping them.
- **Ecosystems without factory tooling.** intent-packages' dependency-update profile has tooling for
  `npm`, `pip` and `uv` only. A failed bump in any other ecosystem (Docker, `github-actions`) gets the
  same per-bump alert as a bump the factory couldn't fix, so it's never silent. *Claude's call:*
  adding tooling for those ecosystems is later work, taken on when the alerts show it's needed.
  Passing Docker bumps stay excluded from the cascade unless the builder finds a safe way to admit
  them.

What stays the same: a standing package per repository and dependency remains the unit the factory
works from (ADR-0028), and each bump is a revision of it. Only who creates it changes.

### Part 4: Holds

A hold is a version ceiling on one dependency in one repository: "orchestrator's sqlalchemy stays at
or below 2.0.54." It isn't a skip of one bad version.

- **Where it lives:** in the member record, beside the repository's grants.
- **Who places it:** a session stages a hold through `sds-members stage`; Devon confirms it on
  `/review`, like any other change to the record. When rollback exists, it stages the hold as part of
  rolling back.
- **Who lifts it:** only Devon, by confirming its removal on `/review`.
- **What identifies it:** repository, ecosystem, directory and dependency name, plus the ceiling
  version. *Claude's call,* matching how Dependabot identifies an update.
- **What honors it:** every path that can propose or merge a bump to that dependency. At least:
  - the bump proposer refuses to propose past the ceiling;
  - the cascade and every lander refuse to merge a bump past the ceiling.

  *The builder enumerates every merge and propose path* and pins that list in a test, so a new path
  that ignores holds fails the build.
- **A held bump that arrives anyway** is refused, not merged, and its pull request is closed with a
  comment naming the hold. Dependabot doesn't reopen a closed pull request for the same version.
- **Dependabot's own `ignore` setting is not used.** *Claude's call:* writing a hold into each
  repository's `dependabot.yml` would need write access no bridge lane has, and in brain and
  change-manager each write would redeploy. Refusing and closing gives the same result from the
  record alone.

### Part 5: The carrier's facts are verified

- **`source_commit`:** the carrier confirms the commit exists on the package repository's remote
  default branch before registering. An unpushed commit is refused.
- **`change_record_id`:** the orchestrator confirms the record exists in change-manager, is approved,
  and names the same package and revision, before it accepts a machine intake.
- **Workability:** as Part 2, with no reliance on `portfolio.json`'s age.

## Order

The phases are ordered by how much hand work and risk each removes, and each one ships on its own:

1. **Part 1, findings and edges.** Smallest and safest. Without it, every later failure is silent.
2. **Part 2, scope from the record.** Needed before Part 3, so the proposer's population is the
   grant.
3. **Part 3, automatic dependency updates up to intake.** Removes the most hand work this layer owns.
   Its standing-authority changes (known-good, policy approval of `work` records, the widened
   approval-policy grant, `decompose` emitting merge) are releases.
4. **Part 4, holds.** Must ship before or with Part 3 reaching repositories that deploy, so a bad
   bump that lands automatically can be stopped from recurring.
5. **Part 5, verified carry.** Small, and can run in parallel with any phase after 1.

## Left to the builder

- Which lane sends the per-bump alert, and its key format.
- Whether `factory-target.toml` is retired or generated; whether the approval-policy grant is
  generated or replaced by a check.
- How the carrier reads capability and permissions without `portfolio.json`.
- How passing Docker bumps are handled.
- The full list of hold-honoring paths, pinned in a test.
- How the work splits into pull requests and across repositories (orchestrator, intent-packages,
  change-manager, each target's `dependabot.yml`).
- The ADR text: an ADR-0058 amendment for decision 1, and an ADR (or ADR-0028 amendment) for
  decisions 2 to 4.
