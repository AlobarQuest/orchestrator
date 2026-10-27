# SDS membership and services: design

- **Date:** 2026-10-26
- **Status:** Approved by Devon, 2026-10-26
- **Builds on:** `2026-10-18-sds-target-onboarding-design.md` (approved), which this design generalizes
- **Related:** ADR-0015 (factory targets), ADR-0021 and its amendment 1 (signal homes), ADR-0030
  (activation on the operator machine), `docs/operations/onboarding-a-target.md`

## Intent

The SDS is becoming a Dark Operations Factory: a system that maintains projects, many of them
vibe-coded, with what an IT team would provide. Devon's words: "a 'Dark Operations Factory' I can use
to maintain projects, likely vibe coded projects, with the benefits of an IT team like
functionality."

Today the SDS has no single answer to "which projects does it look after, and what does it do for
each?" A survey on 2026-10-26 found 21 separate mechanisms answering "which repositories", five
of them hand-written lists, with the forks listed four different ways. Readiness checking covers one
service end to end (factory work on an inert repository), and nothing can say, per project, whether
its backups are configured and monitored, whether it's monitored at all, or how it deploys.

This design gives the SDS one membership record per project, with an explicit grant per service, and
makes every lane derive its scope from that record.

## Decisions Devon made

These came from the 2026-10-26 design conversation.

- **One list of members, with per-project service grants.** Membership is one list. What varies per
  project is which services the SDS provides there. There are no membership tiers.
- **Membership is explicitly granted, never assumed.** No lane discovers a project and adopts it. A
  project in an unknown state of development, or one that will never be in the SDS, is untouched until
  Devon says "now add this one". A declaration a session commits on Devon's decision counts as his
  grant; the requirement is that the grant is explicit, not that a particular person types it.
- **Seven services:** dependency updates, security patches, user-requested app changes, backups,
  monitoring, deploys, and credential rotation. Rotation was added after the builder check found
  that the rotation machinery the SDS already runs (ADR-0054, ADR-0055) fit none of the first six.
- **Services switch on one at a time.** A member can have some services granted while others are
  still being remediated.
- **Backups are part of the SDS.** For every member with data, the SDS must know that a backup
  configuration exists and that it's monitored. How it gets there is a design question (see "Backups").
- **change-manager is part of the SDS:** its human change-control layer.
- **Coolify and GitHub are platforms the SDS works through,** not parts of it.
- **The forks, `factory-runner` and `project-standards` are members.** The SDS syncs, hardens, lands
  and installs the forks; `factory-runner` and `project-standards` receive inert-lane landings.
- **`factory-runner` and `project-standards` are to get the app-changes service.** For
  `factory-runner`, this reverses ADR-0015's "the runner may not maintain itself": callers pin the
  runner to a full commit (verified 2026-10-26), so a merge to the runner changes nothing until a
  caller's pin moves. Each still goes through readiness before the grant (see "Phase 1 seed").
- **`vps-backup` is not a member for now.** Devon plans to split it into two backup systems, one
  for the SDS and one for his personal machine and data; today's combined system stays outside the
  SDS until that split.
- **The member record lives in the orchestrator's database.** App Brain is Devon's own portfolio
  memory and won't hold client project data. A separate SDS Brain for user-facing questions may come
  later and is out of scope here.
- **The orchestrator's self-update model stays as it is.** Lanes run the main tree and pull it before
  each pass; production changes only through `scripts/deploy_orchestrator.py`.

## Goals

- One authoritative answer to "which projects are SDS members, and which services does each have".
- Every lane that acts on or reports about repositories derives its scope from that answer.
- An onboarding flow that assesses a project against each service, produces remediation work, and
  grants each service when its readiness checks pass, with Devon's explicit yes.
- Per member, the links that tie a project together across systems (repository, Coolify app,
  databases and backup targets, health endpoints and checks), so "is this service in place for this
  project?" is answerable.
- Room for SDS clients later: other people's projects, each with an owner.

## Non-goals

- A client-facing dashboard, client accounts or client access to change-manager. Devon expects these
  eventually; this design only keeps them possible (see "Owner").
- An SDS Brain.
- Changing how the orchestrator updates itself.
- Building every service's readiness checks at once. Phase 1 is the record; services follow one at a
  time (see "Phases").

## Anti-goals

The design has failed, even if it works technically, if:

1. **A lane acts on a project nobody granted.** Discovery may report a candidate; it never enrolls one.
2. **An existing lane silently loses coverage.** Moving a lane to the member record must never shrink
   what it covers without that showing up as a reported difference.
3. **A grant claims a service is in place when it isn't.** A grant follows passing readiness checks,
   and a service whose checks later fail is reported, not left looking healthy.
4. **Onboarding breaks a member's app.** The October 18 design's "behavior and appearance are
   preserved" bar still applies to every remediation.

## The member record

Stored in the orchestrator's database, one row per member, keyed by the repository's GitHub
`full_name` as GitHub spells it.

**Identity**

- `repository`: canonical `owner/name`. A member is a repository; one repository may serve several
  apps (`brain` serves four), which the links carry.
- `owner`: who the project belongs to. Every current member is Devon's. This is the hook for clients
  later; nothing in this design branches on it yet.
- `state`: `onboarding`, `member`, or `retired`, with timestamps and the approval that set each.

**Links** (filled in by onboarding, checked by readiness)

- the apps the repository serves, each with its Coolify application UUID and environment, or "not
  hosted";
- its data stores and their backup target labels in vps-backup, or "stateless";
- its health endpoints, and the Healthchecks or alert-hub identifiers that watch it;
- each app's deploy mechanism, using the vocabulary in `landing-lanes.md` (CI webhook, repository
  webhook, host git integration, manual). Deploy mechanism belongs to an app, not a repository:
  `orchestrator` is both a hosted app deployed by hand and a working copy the lanes run from;
- whether it has a working copy on the operator machine, and whether that working copy is what
  runs (machine-local activation, ADR-0030).

**Service grants**, one per service, each with a state:

| State | Meaning |
|---|---|
| `not_assessed` | Readiness for this service hasn't been run |
| `remediating` | Readiness found gaps; remediation work exists |
| `granted` | Readiness passed and Devon approved the grant |
| `not_applicable` | Devon recorded that the service doesn't apply, with a reason (for example, backups for a stateless project) |
| `withdrawn` | Previously granted; withdrawn by Devon or by a failing re-check, with the reason |

Every state change records who made it and why. `granted`, `not_applicable` and `withdrawn` by Devon
are human approvals in the orchestrator's existing approval model (Claude's call; see "Calls made").

## The seven services

Each service names what "granted" lets the SDS do, which lanes act under it today, and what its
readiness must prove. Readiness detail for services after phase 1 is designed when that service's
phase starts; the table states the bar.

| Service | Machinery that acts under it | Readiness must prove |
|---|---|---|
| Dependency updates | Dependabot, the inert lane, the bump proposer, the dependency-remediation lane | Dependabot is configured for the project's ecosystems and its pull requests arrive; CI runs real tests; the landing path for its updates is declared |
| Security patches | Dependabot security updates, fork-sync's security review, fork-harden | Security alerts and updates are on; a vulnerability scan exists for the project's ecosystems (none does today outside the forks) |
| App changes | The factory: intake, dispatch, factory-runner, the landing lanes | Everything the October 18 readiness command checks today |
| Backups | vps-backup, reported as `recovery_floor` observations | Every data store is a backup target, or the project is recorded as stateless; each target's runs are observed per target; a restore test covers it |
| Monitoring | Healthchecks, alert-hub, the revision watcher | A health endpoint exists and something outside the app checks it; failures reach alert-hub |
| Deploys | change-manager's deploy policy, the deploy watcher, the estate lander, the activation sweep for machine-local projects | The deploy mechanism is recorded; a landing that deploys is watched to an outcome; a machine-local project's working copy is swept (ADR-0030 treats that activation as a deploy) |
| Credential rotation | The rotation proposer, the rotation executor's two instances, the rotation verifier, and the `.cred-consumers.toml` registries; infraops decides when a credential is due | Every credential the project issues or holds is in BWS, referenced by UUID in a `.bws-secrets.toml` manifest; each one the SDS rotates has a registry entry naming its keeper and every copy, with `rotated_by_sds` set; every scheduled reader has a machine read; the security scanner reports no BLOCK findings |

For credential rotation, the grant belongs to the member whose credential it is, not to every
repository whose code reads it (Claude's call). A registry's `runtime-fetch` rows name scripts that
fetch a credential from BWS when they run; no value is stored in those repositories. The rows exist
so the rotation executor waits until each reader has run on the new value before it retires the old
one, so a reader may sit in any repository, member or not. Today's registries name readers in three
non-members: `vps-backup` (its backup scripts post observations with the drift-reporter bearer),
`github-to-bitbucket-mirror` (the mirror's GitHub and Bitbucket tokens), and
`factory-validation-kit-restart-recovery`. The guard checks that every `rotated_by_sds` credential
belongs to a member with rotation granted, and never that every reader's repository is a member.
This changes nothing that runs today.

## Onboarding

The October 18 process is kept and generalized:

1. **Devon adds the project:** an explicit act that creates the member record in state `onboarding`.
   Nothing else can create a member record. Discovery mechanisms (the portfolio scan, the revision
   watcher's census of undeclared apps, pin_watcher's repository sweep) may report a candidate and
   never create one.
2. **The readiness command assesses each service** and sorts every failing requirement by owner, as
   today: repository work (a build handoff), Devon's tasks (a task list), or onboarding actions. Gaps
   that belong in the SDS or a supporting system (for example a new vps-backup target) are owned as
   onboarding actions or as work in that system.
3. **Remediation runs** per the October 18 process. Once a project has the app-changes service, its
   remediation can itself be factory work.
4. **When a service's checks are clean, Devon approves its grant.** The onboarding procedure for that
   service then makes the configuration changes and proves the service works, the way the October 18
   canary proves factory work.
5. **The member moves to `member`** when Devon says so; services not yet granted stay in their state.

Readiness can be rerun at any time against a member. A granted service whose checks fail is reported
(anti-goal 3); whether that withdraws the grant automatically is left to each service's phase.

## How lanes read scope

**Lanes that act select by grant; lanes that only observe select by membership and links**
(Claude's call). A lane that changes something (lands, dispatches, installs, rotates) acts only where
its service is granted. A lane that only measures and reports (the activation sweep, the revision
watcher, the landing ledger) may cover every member whose links say the measurement applies, granted
or not, because observing a member acts on nothing and reporting an ungranted gap is useful. So the
activation sweep covers members with a working copy, and the revision watcher covers members' apps
with a revision-stamped health endpoint, without monitoring being granted.

- The orchestrator serves the member record read-only to the machine roles that need it, as members
  filtered by service grant and by link (for example "members with deploys granted whose deploy
  mechanism is machine-local").
- `scripts/sds_scope.py` becomes the one client lanes use, keeping its rule: incomplete answers are
  said out loud (exit 3 with the gaps), never implied by a shorter list.
- Lanes move over one at a time. A lane's move is complete when its old source and the member record
  produce the same population, or the differences are reported and Devon has ruled on each
  (anti-goal 2).
- **Per-repository configuration stays where it is.** Lists that carry more than membership (the
  rollout workflow transcriptions, tool_installer's tool definitions, fork-sync workflows) keep their
  data; a guard checks that every repository they name is a member with the matching service
  granted. The mapping (Claude's call): the rollout transcriptions are deploys; fork-sync is
  dependency updates and security patches (an upstream release is a dependency of the fork, and its
  review is a security gate); tool_installer is deploys, because installing on the operator machine
  is a machine-local activation (ADR-0030).

## Relationship to the existing declarations

Two explicit grants exist today, and both stay authoritative through phase 1:

- `factory-target.toml` in each repository: the app-changes grant. Dispatch admission reads it from
  the target's default branch.
- change-manager's landing policy: the inert population. Its version is stamped into every inert-lane
  merge (`SDS-Inert-Landing-Policy: 12`), and the landing ledger audits each merge against that exact
  version. That trail must be preserved.

In phase 1 the member record mirrors them, and a consistency check reports any disagreement. Whether
the record later becomes the source those declarations are generated from, or stays a mirror checked
against them, is decided after phase 1, with the agreement data in hand (see "Left open").

## Phases

The order after phase 1 is Claude's call, ordered by risk; Devon can reorder it.

1. **The member record, and lanes reading it.** Create the record, seed it with today's members and
   the services they effectively have, move the hand-written scope lists to it, and add the
   consistency check against the two declarations. No new readiness checks.
2. **Backups.** A gap fails silently and costs data.
3. **Monitoring.** No per-app uptime check exists anywhere.
4. **Deploys.** The largest change: readiness currently refuses redeploying repositories.
5. **Security patches.** Adds the missing vulnerability scanning.
6. **Dependency updates, credential rotation, then app changes.** All three largely work; their
   phases fold their existing checks into the per-service readiness model.

### Phase 1 seed

Members: the 13 repositories the landing ledger covers today (the six factory targets, `factory-runner`,
`project-standards`, and the forks `rtk`, `claude-octopus`, `rea`, `n8n-as-code`, `n8n-manager`).

Seeded grants record what already runs, so no lane loses coverage on the move:

- **app changes:** granted for the six repositories declaring `factory_target = true`;
- **dependency updates:** granted for the inert population and for repositories where the bump
  proposer or Dependabot landing already acts;
- **deploys:** granted for `change-manager` and `brain` (watched deploys), for the machine-local
  repositories the activation sweep binds, and for the forks tool_installer installs;
- **security patches** for the forks (fork-sync review and fork-harden);
- **credential rotation:** granted for the members that own a credential a registry marks
  `rotated_by_sds`;
- **everything else:** `not_assessed`.

`factory-runner` and `project-standards` are seeded `remediating` for app changes, not `granted`,
because neither has passed readiness and `factory-runner` declares `factory_target = false`
(Claude's call). Their onboarding runs the October 18 procedure, which already covers inert
repositories, and can proceed alongside phase 1. For `factory-runner` it ends by flipping its
declaration and amending ADR-0015 to record the reversal and the pin evidence. Until then, the
declaration stays authoritative and the consistency check reports no disagreement.

Seeded grants are marked as seeded, not assessed, so a later readiness run can confirm or correct them.
The seed also fills the links the moved lanes need: working-copy presence and machine-local
activation for the sweep (from the activation sweep's lists and App Brain's landing answers), and
apps with their health endpoints for the revision watcher (from its subject list). Devon approves
the seed once, as a whole, before it's applied.

### Phase 1 lanes to move

The hand-written scope lists: the activation sweep's checkouts and bindable set, the landing ledger
(already derived; it moves to the record), fork_harden's fork list, and revision_watcher's subjects.
The other mechanisms in the 2026-10-26 inventory are either declarations (kept, see above),
discovery that only reports, or per-repository configuration (guarded, see "How lanes read scope").

## Backups

The survey found that vps-backup's targets are hand-written lines in three scripts, results are
reported per host rather than per target, and nothing joins a target to a repository. Phase 2 must
answer, per member, "configured and monitored" from data rather than prose. At minimum that needs:

- a machine-readable list of backup targets, each naming the member it belongs to;
- per-target results in the run's observation, so one failing database names its project;
- the restore test's list derived from the same target list, so it can't drift.

Devon plans to split `vps-backup` into an SDS backup system and a personal one. Phase 2 builds on
the SDS half, and whether that half becomes a member is decided then. Until the split, phase 2 can
start only with the target list, since that's needed under either system.

## Constraints

- Every orchestrator invariant in `CLAUDE.md` applies. In particular: new routes join the route
  inventories, an ingress POST route joins the idempotency matrix, migration ids stay within 32
  characters, OBSERVER stays confined to observations, and the word guards apply to new code.
- Secrets never enter prompts, tool arguments, logs or records.
- No lane may enroll a project (anti-goal 1). Writes are split (Claude's call):
  - **Only a human approval** may create a member, change its `state`, or set a grant to `granted`,
    `not_applicable` or `withdrawn`.
  - **The system role** may set `not_assessed` and `remediating`, record readiness results, and
    report a granted service whose checks fail. It may never grant, and automatic withdrawal is
    decided in each service's phase.
  - A guard test asserts both halves, in the way `_confine_observer` confines OBSERVER: one positive
    rule naming the routes each side may write, with unknown routes refused.
- Scope reads fail closed: a lane that can't read the record reports itself incomplete.
- Changes to the orchestrator reach production only through `deploy_orchestrator.py`, and lanes pick
  them up from the main tree as today.

## Calls made

These are Claude's calls, not Devon's decisions; check them while reading.

- **Grants are approvals in the orchestrator,** through the existing `/review` approval model, not
  change-manager records. Devon left the location of "now add this one" open; change-manager stays
  the place for change control of the work itself.
- **A member is a repository,** with apps as links, because every lane today keys on repositories
  and `brain` serves four apps from one.
- **The phase order after phase 1** (backups, monitoring, deploys, security patches, dependency
  updates, app changes), by risk.
- **Phase 1 seeds grants from what already runs,** marked as seeded, rather than assessing every
  member before any lane moves.
- **The activation sweep counts as deploy machinery** for machine-local projects, following
  ADR-0030's framing of activation as a deployment.
- **The two existing declarations stay authoritative through phase 1.**
- **Acting lanes select by grant; observing lanes by membership and links.**
- **Deploy mechanism is a property of each app,** and machine-local activation a property of the
  repository's working copy.
- **The mapping of per-repository lists to services,** including tool_installer as deploys.
- **The write split:** humans grant; the system role assesses and reports.
- **A rotation grant belongs to the credential's owner,** and copies may live in non-members.
- **Rotation sits in phase 6** with dependency updates, because it already runs.
- **`factory-runner` and `project-standards` start app changes as `remediating`,** onboarded by the
  October 18 procedure, rather than seeded as granted.

## Left open

For Devon:

- After phase 1: whether the member record becomes the source the two declarations are generated
  from, or stays a mirror checked against them.
- At phase 2: whether the SDS half of `vps-backup` becomes a member.
- **Recovering from a broken runner, without hand work.** Because callers pin the runner, a bug on
  the runner's `main` is fixed by ordinary factory work, run by the pinned, working runner. The
  remaining case is a broken runner whose pin has already moved to the callers. Devon wants a
  mechanism for that case so it isn't fixed by hand. A likely shape: roll the callers' pin back to
  the last good commit (the factory already lands caller-pin changes), then let the restored runner
  dispatch the fix. Design it as its own piece of work before `factory-runner`'s app-changes grant
  is approved, and have the ADR-0015 amendment reference it.

- Further services beyond the seven. Devon listed 34 candidates on 2026-10-27; they are recorded,
  grouped by area, in `docs/roadmap.md`, "Potential additional scope".

For the builder:

- Table and route names, the approval mechanics within the existing model, and the CLI or page
  through which Devon adds a member and approves grants.
- How the consistency check reports (an observation, a finding kind, or a report section).
- How the seed is applied and reviewed.
