# ADR-0059 — Scheduled lanes run as pinned containers off the Mac, one BWS identity per lane

- **Status:** Proposed
- **Date:** 2026-11-05
- **Decided by:** Devon decided Q1 to Q5 on 2026-11-05 (HQ decisions D-3 to D-7), recorded in
  "Devon's decisions". The rest of the ADR is proposed and not yet accepted.
- **Relates to:** ADR-0055 (the rotation executor's isolated runtime and decisions 2 and 4,
  amendments 6 and 12), ADR-0058 (lanes read their scope from the member record), ADR-0039
  (out-of-process producers carry only structured facts), ADR-0021 amendments 2 and 3 (the
  dead-man and outcome alarms), ADR-0030 and ADR-0031 (the machine-activation sweep), ADR-0046
  (the lane kill switches)
- **Build mode:** construction mode
  (`~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`).

## Context

The year plan's Phase 1 is "Move the lanes off the Mac". Its bar: no lane that acts on a member
runs on the Mac, and a lane survives the Mac being off for a week. No ADR or roadmap item planned
this, so the phase starts here.

Twenty launch agents (`scripts/com.devon.*.plist`) run the SDS's scheduled lanes on the operator
machine. Each one shares four Mac dependencies:

- **launchd** starts it, in Mac-local time (America/New_York).
- **The main tree's working copy and `.venv`** are its code. Every launcher except the two bridge
  proposers and the rotation worker sources `~/.claude/bin/activate-checkout.sh`, which
  fast-forwards and syncs the main tree before it runs.
- **Two Keychain items** hold its BWS identities: `Claude`/`BWS_ACCESS_TOKEN_SDS` (the narrow
  `sds-operator` account) and `Claude`/`BWS_ACCESS_TOKEN_VPS_BACKUP` (the broad shared account).
  `scripts/sds-deadman.sh` reads the Healthchecks Management API key (BWS `51509d5e-…`) through the
  broad one.
- **Devon's interactive `gh` login** (`gh auth token` or `gh auth git-credential`) is its GitHub
  credential. No lane has a GitHub credential of its own.

The Lane runtime pass (2026-11-04, PR #609) left these to this phase: the plists and installers,
the Keychain reads and the two-identity scheme, `activate-checkout.sh` and the main-tree working
copy, and provisioning every lane's check from its installer
(`docs/superpowers/specs/2026-11-04-runtime-layer-design.md`, "Left for Phase 1"). It kept the lane
code and the alarms so that they carry over unchanged.

Every lane's console script is already in the orchestrator image. `[project.scripts]` in
`pyproject.toml` declares all of them, and the Dockerfile installs the project into `/app/.venv`.

The pattern to apply is the rotation executor's (ADR-0055 decision 2 and increment 4): a
deterministic program in an isolated runtime, holding only its own identity, started by an init
system with only `PATH` and `HOME` set, and holding nothing that can't be rebuilt.

## Decision

### 1. The runtime: one container per lane, from a lane image pinned to the served orchestrator

- **Each moved lane is one service of a single Coolify compose resource, `sds-lanes`, on the
  production VPS beside the orchestrator.** Each service runs its lane's launcher under a
  scheduler inside its own container, with `TZ=America/New_York`, so every schedule keeps its
  current wall-clock time. That matters for the time-windowed lanes: estate landing lands bumps
  only from 02:00 to 06:00 New York time.
- **The schedule is tracked in this repository**, one crontab line per lane beside its launcher,
  the way each plist is tracked today. The lane image bakes it in, so a schedule change is a
  reviewed commit and a new image, as a plist change is now.
- **The lane image is built `FROM` the orchestrator image at the same commit.** It adds only what
  the lanes need beyond the orchestrator: `git`, `gh`, `uv`, `node`, the `bws` CLI and the
  scheduler binary. The `Release image` workflow builds it beside the orchestrator image from the
  same 40-character sha, so a lane image and an orchestrator image with the same tag run the same
  lane code.
- **A lane's image is pinned to the served orchestrator tag and updated by the deploy command.**
  `scripts/deploy_orchestrator.py` writes the same tag to `sds-lanes` after the orchestrator's
  `finish` passes. A lane runs either the served tag or, while the deploy command waits for a
  quiet slot (following bullet), the tag the orchestrator served until that release, and no
  other. This replaces `activate-checkout.sh`'s fast-forward of the main tree. A moved lane never
  reads a working copy.
- **A routine tag update redeploys `sds-lanes` only in a quiet slot.** Coolify's deploy endpoint
  (`POST /deploy`) takes resources by `uuid` or `tag`, and its restart endpoint
  (`POST /services/{uuid}/restart`) takes the service resource's `uuid` and a `latest` flag.
  Neither names a service within a compose resource (Coolify API reference, "Deploy" and
  "Restart Service", read 2026-11-05). So every tag update recreates every lane's container at
  once, and a running pass is stopped midway. The deploy command therefore applies the
  cut-over's two checks to the whole resource before it redeploys: the time is outside estate
  landing's 02:00 to 06:00 New York window, and no moved lane's check has `started` set to `true`.
  `started` is the boolean field of each check in the Healthchecks Management API's check listing
  (`GET /api/v3/checks/`, API docs "List Existing Checks"), read just before the redeploy. The API
  docs show `started` only in their example responses and don't describe it, so increment 1
  verifies, before it relies on the field, that the self-hosted version returns it in the listing
  and sets it to `true` from a `/start` ping until the pass's result ping. Every
  scheduled pass pings `/start` first, so `started` is the one signal every lane already sends. A
  check whose `status` is also `down` doesn't hold the deploy, because its pass stopped without a
  final ping and the down alert already reports it. The deploy command runs where it runs today,
  in the deploying session on the Mac, inside `finish`. It reads the listing with the read-only
  Healthchecks key (BWS `f07b1368-…`, in `Rotation / Healthchecks`), through a direct read grant
  to `sds-operator` that increment 1 adds (Q5, decided). Until both checks pass, the deploy
  command waits and reads again, for at most 6 hours: long enough to outlast the 4-hour landing
  window and a pass already running when it closes. At 6 hours the deploy command raises an alert
  through alert-hub, exits with its "condition unmet" code (2) and leaves `sds-lanes` on its
  previous tag, and HQ decides whether to re-run it or redeploy by hand. The residual: a pass that
  starts between the read and the redeploy is still stopped. The P2 backlog item in which
  `deploy_orchestrator.py` holds the bump-driver launch agent during a deploy is re-scoped by this
  rule once the bump driver moves.
- **While the deploy command waits, the old lane code runs against the new orchestrator.** A
  release's orchestrator is served from `finish`, but its lanes stay on the previous tag until
  the quiet slot, up to the 6-hour maximum. So every release must keep the previous release's lane
  code working against its orchestrator for that interval: a route or response field a lane reads
  can't be removed or changed incompatibly in the release that stops the lane needing it.
- **The lanes that need other repositories clone them at the start of each pass.** These lanes
  are the bump driver (intent-packages and its venv, project-standards, security-standards), both
  bridge proposers (intent-packages; the rotation proposer also needs infraops-mcp-server), the
  work carrier (package checkouts) and fork hardening (the forks). Each clone is shallow, at
  `main`, into a container-local directory that's discarded with the container, so a container
  holds nothing that can't be rebuilt.
- **A lane's kill switch stays the variable it is today** (ADR-0046). The four switches
  (`ORCHESTRATOR_DISPATCH_ENABLED`, `ORCHESTRATOR_ESTATE_LANDING_ENABLED`,
  `ORCHESTRATOR_INERT_LANDING_ENABLED` and `ORCHESTRATOR_BUMP_MERGE_ENABLED`) are the orchestrator's
  variables, not the lanes', so setting one to `false` stops one lane's acting through the
  orchestrator and leaves `sds-lanes` untouched. Setting one isn't free: it's an orchestrator
  environment change and restart, which must not happen while a dispatched run is live. They
  cover only what acts through the orchestrator: dispatch (the bump driver), estate landing,
  inert landing and the bump merge (the bump driver). The proposers, the work carrier and fork
  hardening have no switch, and the observe-only lanes change no record. The switches are the
  only per-lane stop that needs no `sds-lanes` redeploy. No Coolify API call stops one service of
  `sds-lanes`, so any stop through Coolify, like any redeploy, is resource-wide and follows the
  quiet-slot rule.
- **Each service's schedule is switched by one variable, `LANE_SCHEDULE_ENABLED`, set on that
  service alone.** The lane image's entrypoint reads it once, at container start. When it's
  `true`, the entrypoint installs the lane's tracked crontab line and starts the scheduler. When
  it's anything else, or absent, the entrypoint starts the scheduler with an empty table, so the
  container stays running, runs no pass on its own, and can still be entered for a one-off run
  (cut-over step 2). A service is created with it `false`. Changing it is a Coolify variable
  change on that service plus a redeploy of `sds-lanes`, because the entrypoint reads it only at
  start. That redeploy is resource-wide, so the deploy command performs it under the quiet-slot
  rule: increment 1 teaches the deploy command to set one service's variable and redeploy with
  the same two reads and the same 6-hour maximum as a tag update. A schedule change therefore
  takes effect in the next quiet slot, at worst 6 hours after it's asked for.

**Rejected: Coolify scheduled tasks.** A Coolify scheduled task runs a command inside a running
container of an application or service. The lanes would share one container and its environment,
which defeats one identity per lane. The schedule would also live in Coolify and not in the
repository.

**Not considered: a separate VM per lane.** Neither the year plan nor this phase calls for one.
One container per lane already gives each lane its own process, filesystem and environment.

### 2. One narrow BWS identity per lane, replacing every Keychain read

- **Each moved lane gets its own BWS machine account**, named `sds-lane-<lane>`, with a direct
  read grant on exactly the secrets its launcher fetches today and nothing else. This follows
  ADR-0055 amendment 6: grants on secrets, not on projects. Today's two Keychain identities stop
  being any lane's identity. The narrow-versus-broad split, and the two-identity trap the
  launchers guard against, end with them: one lane has one token.
- **The token reaches the container as one environment variable, `BWS_ACCESS_TOKEN`**, set on that
  service alone in `sds-lanes`. A lane's launcher reads every secret it needs from BWS by UUID at
  the start of each pass, as it does now, through `scripts/sds-bws.sh`. The token is never in the
  image or the repository, and no value passes through a process argument.
- **Each identity is proven before its lane cuts over.** The proof is the lane's launcher, run in
  its container through `lane-run` (cut-over step 1), which starts it with only `PATH`, `HOME` and
  `BWS_ACCESS_TOKEN` set. A dry run reads every secret, and
  a deliberately revoked token fails, tested with an empty `HOME` because `bws` caches sessions per
  token id (workspace invariant #23). The proof also shows the token is narrow, not only
  sufficient: under the same `env -i` start with an empty `HOME`, reading a secret this lane
  doesn't fetch is refused. The refused read names another lane's secret UUID from
  `.bws-secrets.toml`, or, for the first lane, a secret only the broad account reads.
- **Every GitHub credential moves to BWS.** No moved lane reads `gh auth token`. Each lane keeps
  its own `<LANE>_GITHUB_TOKEN` variable (`BUMP_DRIVER_GITHUB_TOKEN`, `DEPLOY_WATCHER_GITHUB_TOKEN`,
  `CHANGE_PROPOSER_GITHUB_TOKEN`, `BUMP_PROPOSER_GITHUB_TOKEN` and the rest), and its launcher
  feeds that variable from BWS instead of `gh auth token`. A read-only lane's variable is fed from
  one read-only fine-grained token in BWS, with a direct grant per lane identity. That token is
  this ADR's proposal for increment 3, which moves the first lane that reads GitHub (the landing
  ledger), and it's created there. It feeds the eleven lanes whose GitHub variable only reads:
  bump driver, change proposer, deploy watcher, estate landing, inert landing, landing ledger,
  membership check, pin watcher, revision watcher, unit verifier and work carrier. Both landers
  read through `GitHubReader` and land through the orchestrator's dispatch App, not with this
  token. It reads the repositories those lanes read through Devon's login today: the members'
  repositories, the member record's repository, intent-packages and the standards repositories
  the lanes clone. Increment 3
  lists them from the member record and the lanes' clone lists, and selects only those. Its
  permissions are read-only, and only the ones the lanes' GitHub calls use: Metadata, Contents
  (files, commits, compares and clones), Pull requests, Actions (workflow runs and jobs) and
  Administration (the `required_status_checks` reads in `deploy_watcher`, `landing_ledger` and
  `unit_verifier`). It has no write permission, and no later increment widens it: a lane that
  needs more gets its own credential by its own decision.
- **The three pushing lanes push as the "SDS lanes" GitHub App (Q1, decided).** `bump-proposer`,
  `rotation-proposer` and `fork-harden` feed their GitHub variable with an installation token of a
  new GitHub App, installed only on intent-packages and the forks with `contents: write`. Each
  pass mints the token from the App's private key, which lives in BWS and is read by UUID, with a
  direct read grant to each of the three lanes' identities and to no other. The token is
  short-lived and never stored. The key is created in the increment that moves the first pushing
  lane, by Devon, because only the App's settings page generates a GitHub App private key
  (GitHub Docs, "Managing private keys for GitHub Apps"). The bump driver needs no GitHub write
  scope: its token only reads pull request state and makes shallow clones
  (`src/bump_driver/client.py`, `src/bump_driver/mirror.py`), and it dispatches and merges through
  the orchestrator as SYSTEM. Its variable is fed from the read-only token.
- **`.bws-secrets.toml` and `.cred-consumers.toml` gain each lane identity and each secret it
  reads**, in the same change as the lane's move. The credential registry gains each lane token
  as a credential, and the "SDS lanes" App's private key as one more. The VM rotation executor
  rotates each lane token once ADR-0059 increment 7 moves the executor to the VPS (Q3, decided).
  The App key joins ADR-0055's hand-rotated set, as the `alobar-sds-rotation` App's key did
  (Q4, decided): Devon rotates it on the App's settings page, and no executor handles it.
- **This ADR creates no identity.** Each increment that moves a lane creates that lane's account
  and grants.

### 3. The alarms stay on the same Healthchecks checks

- **Every lane keeps its check.** A moved lane pings the same check, so the check's history
  carries across the move.
- **A lane holds only its own check's ping URL, never the Management API key.** Today
  `sds-deadman.sh` uses the Management API key (`51509d5e-…`, `healthchecks_selfhosted_api_key`
  in `.bws-secrets.toml`) for one operation: `sds_deadman_resolve` lists every check
  (`GET /api/v3/checks/`) to find the lane's by exact name and take its `ping_url`. The only other
  use is at install time: `sds_upsert_check` in `scripts/sds-install.sh`, and the private copy in
  `install-fork-harden-launchd.sh`, create or update a check (`POST /api/v3/checks/`). Nothing
  pauses a check. Every ping, `/start`, success and `/fail` with or without a report body, needs
  only the ping URL. The read-only key can't replace the Management key for the lookup, because
  the Management API omits `ping_url` (and `uuid`) from every response to a read-only key
  (Healthchecks Management API docs, "List Existing Checks" and "Get a Single Check"). The
  Management key manages every check in the project, so it's no lane's credential.
- **The narrowest credential is the check's UUID ping URL, stored in BWS per lane.** A UUID ping
  URL identifies one check, and a request to it is a ping and nothing else: success, `/start`,
  `/fail`, `/log` or an exit status (Healthchecks Pinging API docs, "Endpoints" and the "Using
  UUID" sections). It can't list, read, change, pause or delete any check. A ping key with a slug
  is rejected: it's the project's key, so it pings any check in the project by slug, and with
  `create=1` it creates checks (Pinging API docs, "UUIDs and Slugs"). Each lane's ping URL is
  one BWS secret with a direct read grant to that lane's identity alone, read through the lane's
  own `BWS_ACCESS_TOKEN` like its other secrets. The name lookup moves to install time: the
  increment that moves a lane runs `sds_upsert_check` from the deploying session on the Mac,
  through the broad account as today, reads the check's `ping_url` from the upsert response
  (today the response is discarded), and stores it as the lane's ping-URL secret without printing
  it. Storing the secret is that increment's act. Verify before increment 1, on the self-hosted
  version: that a UUID ping URL needs no other credential and does nothing beyond pinging, that
  the upsert response carries `ping_url`, and the URL's base (`PING_ENDPOINT`).
- **What changes in `sds-deadman.sh`, in increment 2** (the first lane whose container pings).
  `sds_deadman_resolve` gains a first path: when the service's environment names the lane's
  ping-URL secret UUID, it reads the URL with `sds_bws_value` and the lane's own token, and lists
  no check. The name-lookup path through the broad account stays for the lanes still on the Mac,
  and increment 6 removes it with their plumbing. The helper still reports and never gates: a URL
  it can't read logs one reason and disables that run's pings. `sds_deadman_ping` moves its URL
  to curl's stdin config, as `sds_outcome_ping` already does, because the URL is a secret and
  must not be a process argument. `install-fork-harden-launchd.sh`'s private upsert is replaced
  by `sds_upsert_check` when fork hardening moves.
- **Each check's schedule and grace are re-checked against the container's crontab** at cut-over.
  Every moved lane already has a check. The runtime pass measured 19 lane checks on 2026-11-04, one
  per `run-*.sh` launcher, each matching its plist's schedule
  (`docs/superpowers/specs/2026-11-04-runtime-layer-design.md`, "Measurements, 2026-11-04"). The
  20th launch agent, `rotation-worker`, has no `run-*.sh` launcher, and it stays. The 17 lanes that
  move are the 19 launchers less `activation-sweep` and `tool-installer`, so each has one of the 19
  checks. The gap is only in which installers create their check: 5 of the 20 call
  `sds_upsert_check` (bump proposer, change proposer, fork-sync watch, membership check and work
  carrier). Each lane's increment provisions its check from the lane's own setup through
  `sds_upsert_check`, which closes that gap.
- **The outcome alarm and the dead-man switch keep their semantics.** Only where the helper reads
  its ping URL changes, so `--finding` and `--told` keep their meanings and `test_sds_deadman.py`
  still holds them.

### 4. What stays on the Mac

Three agents stay, all because their subject is the Mac itself:

- **The tool installer** installs binaries and the Claude plugin on this machine.
- **The Keychain rotation instance** rotates BWS machine tokens held in this machine's Keychain
  (ADR-0055 amendment 12).
- **The machine-activation sweep** measures this machine's working copies, and both of its passes
  (`sweep` and `bind`) read `~/Projects/<member>`. It acts on no member, so the phase bar allows
  it to stay. It remains ADR-0030's only control over ADR-0031 (Q2, decided).

The year plan's Phase 1 text names only the first two. Keeping the sweep departs from that text by
Devon's decision; HQ updates the year plan.

**The VM rotation executor** isn't a launch agent, but it runs on the Mac (OrbStack) and acts on
members. ADR-0055 decision 2 built it to "move unchanged to a VPS". ADR-0059 increment 7, in the
"Increments" section, moves it. Its decision 2 and T1 residual are amended in that increment's own
amendment to ADR-0055, not here.

### 5. Classification of the 20 launch agents

| Launch agent | Disposition | Reason |
|---|---|---|
| `activation-sweep` | Stays | Measures this machine's working copies, and both passes read local checkouts (Q2). |
| `bump-driver` | Moves | Dispatches and merges bump units as SYSTEM, acting on members. |
| `bump-proposer` | Moves | Pushes standing-package revisions to intent-packages `main`, as the "SDS lanes" App (Q1). |
| `change-proposer` | Moves | Reads pull requests and writes change records, with no local state. |
| `deploy-watcher` | Moves | Observes rollouts through APIs only. |
| `estate-landing` | Moves | Lands pull requests and re-points production, acting on members. |
| `execution-watch` | Moves | Reads execution status and raises alerts, with no local state. |
| `fork-harden` | Moves | Pushes forks' `hardened` branches, as the "SDS lanes" App (Q1). Its clone cache is rebuildable. |
| `fork-sync-watch` | Moves | Reads fork sync state through APIs only. |
| `inert-landing` | Moves | Lands update-bot pull requests, acting on members. |
| `landing-ledger` | Moves | Files observations from GitHub reads, with no local checkouts. |
| `pin-watcher` | Moves | Reads callers' runner pins through APIs only. |
| `revision-watcher` | Moves | Reads Coolify and App Brain through APIs only. |
| `rotation-decomposer` | Moves | Proposes decompositions as SYSTEM, with no local state. |
| `rotation-proposer` | Moves | Pushes package revisions to intent-packages `main`, as the "SDS lanes" App (Q1). Runs infraops's `cred-findings` from a clone. |
| `rotation-worker` | Stays | The Keychain rotation instance: its store is this machine's Keychain. |
| `sds-membership-check` | Moves | Compares the member record with declarations through APIs only. |
| `tool-installer` | Stays | Installs tools on this machine, so its subject is the Mac. |
| `unit-verifier` | Moves | Runs `/verify` as VERIFIER, with no local state. |
| `work-carrier` | Moves | Registers intakes from package clones, with no other local state. |

No agent retires under this ADR. Seventeen move and three stay.

The rotation proposer reads `~/.config/infra-drift/cred-consumers.list` through infraops's CLI.
Its increment must find that file's source and carry it as data in the clone or in BWS. A file that
exists only on the Mac would keep the lane tied to it.

### 6. Migration order, cut-over, rollback and the week bar

#### Increments

1. **The lane image and `sds-lanes`, with no lane.** Build the lane image in `Release image` and
   create the compose resource with one placeholder service that runs `--dry-run` on a schedule
   and holds no credential. Teach the deploy command to write the `sds-lanes` tag and to wait for
   a quiet slot, and give `sds-operator` its direct read grant on the read-only Healthchecks key
   (Q5).
2. **One observe-only lane first: `execution-watch`.** It has one identity, two secrets, no local
   state and the shortest interval (15 minutes), so its check shows a failure fastest. It proves
   the identity, the `env -i` start, the dead-man ping and the cut-over procedure.
3. **The other observe-only lanes:** landing ledger, pin watcher, deploy watcher, revision
   watcher, fork-sync watch, membership check, unit verifier and rotation decomposer, one at a time.
4. **The proposers and carrier, which need clones:** change proposer, work carrier, then bump and
   rotation proposers. Before the bump proposer moves, Devon creates the "SDS lanes" App, installs
   it on intent-packages and the forks, and stores its private key in BWS (Q1).
5. **The member-acting lanes:** inert landing, then estate landing, then the bump driver, then
   fork hardening, which pushes as the "SDS lanes" App (Q1). Each moves outside its landing
   window. Estate landing sends its Coolify token only over the tailnet today, so its increment
   decides how the container reaches Coolify without widening that.
6. **Retire the Mac's lane plumbing:** the moved plists and their installers,
   `activate-checkout.sh`'s use by lanes, and the two Keychain items' lane role. The Keychain items
   themselves are retired only once no remaining reader names them. This increment starts only
   after the week bar has passed, because it ends the rollback path.
7. **The VM rotation executor moves to the VPS** under its own ADR-0055 amendment. This increment
   is last because it moves the root of the rotation design.

Each increment's plan records which repositories it changes and which identities it creates.

#### Cut-over for one lane

1. Create the lane's BWS identity and grants, and prove them under `env -i` in the lane's container
   with `--dry-run`. The service runs with `LANE_SCHEDULE_ENABLED=false` (decision 1), so the
   container runs no pass on its own. Every container run in steps 1 and 2 is a one-off exec: the
   deploying session, through infraops's `vps_exec`, runs `docker exec` into the running service
   container, and runs the lane image's `lane-run` command with the launcher's arguments.
   `lane-run` starts the launcher with only `PATH`, `HOME` (a new empty directory) and
   `BWS_ACCESS_TOKEN` in its environment, the token copied from the service's own environment:
   decision 2's `env -i` start, without the token in a process argument. So the run holds the
   lane's identity and no other, and the token never appears in the session's command or
   transcript. The scheduler's crontab line runs the same command. Then capture the lane's
   week-bar baseline (its flips and ping log, "The week bar"), before any run in step 2, because
   some of those runs ping the lane's real check.
2. Pick a cut-over time between two scheduled passes. In that gap, run the lane's reporting mode
   (the invocation that changes no record) on the Mac and in the container within the same
   minute. Keep both outputs for step 4. A reporting run touches the real check or not by lane
   (`scripts/sds-deadman.sh`, "A dry run must not count as a pass"):
   - **It pings nothing** for a launcher run with `--dry-run` (deploy watcher, execution watch,
     landing ledger, pin watcher, revision watcher and unit verifier), and for an outcome-alarm
     launcher run without its acting flag, which resolves no ping URL (bump driver, bump
     proposer, change proposer, rotation decomposer and work carrier).
   - **It pings the real check** for estate landing, inert landing and the rotation proposer,
     whose reporting mode is the bare invocation, a residual `sds-deadman.sh` names.
   - **There is no reporting mode** for the fork-sync watch and the membership check: every pass
     changes no record and pings. Their comparison is the ordinary pass, run on both hosts, and
     it pings the real check. Fork hardening's launcher always passes `--push`, so its comparison
     runs `fork-harden run` without `--push` directly, outside the launcher, and pings nothing.

   A ping from step 2 lands after the baseline and before the week, so it counts in neither.
   Then confirm no pass is running:
   `launchctl print gui/<uid>/com.devon.<lane>` shows no running PID, and for the bump driver no
   process holds its `pass.lock` (the only lane with a pass lock). `bootout` stops a running pass
   midway. Then boot out the Mac agent with `launchctl bootout gui/<uid>/com.devon.<lane>`, and keep
   its plist.
3. Set the service's `LANE_SCHEDULE_ENABLED` to `true` through the deploy command, which redeploys
   `sds-lanes` in the next quiet slot (decision 1). Until that redeploy, neither host runs the
   lane on a schedule, which is a gap of at most 6 hours and is the safe direction. If the gap
   outlasts the check's grace, the check goes down; that down event falls before the week starts,
   so it counts in neither the baseline nor the week. The container's first scheduled pass pings
   the same check.
4. Verify that the check's last ping came from the container and that the Mac agent isn't loaded.
   Compare the two reporting runs from step 2. They must agree on the exit code, the set of
   record identifiers the lane reported (pull request numbers, unit ids, package names, record
   ids), and each record's finding kind or proposed action. Timestamps, durations, run and request
   ids, temporary paths, hostnames and log prefixes differ by nature and are excluded. A scheduled
   pass isn't the comparison, because the Mac's last pass may already have acted on a record that
   the container's first pass then finds done.

The Mac agent and the container never run the same lane at once. Two landers, or two carriers,
would race on the same records.

#### Rollback for one lane

Set the service's `LANE_SCHEDULE_ENABLED` to `false` through the deploy command, wait for its
quiet-slot redeploy to finish, then `launchctl bootstrap` the kept plist. The Mac agent is
bootstrapped only after the redeploy, so the two never run the lane at once.

The worst case is 6 hours, the quiet-slot maximum, during which the container keeps running the lane
on its schedule. For that interval, the ADR-0046 switches stop what acts through the orchestrator:
`ORCHESTRATOR_ESTATE_LANDING_ENABLED` or `ORCHESTRATOR_INERT_LANDING_ENABLED` for a lander, and
`ORCHESTRATOR_DISPATCH_ENABLED` and `ORCHESTRATOR_BUMP_MERGE_ENABLED` for the bump driver. Each is
an orchestrator restart, so it's set only while no dispatched run is live. No switch covers the bump
and rotation proposers, the work carrier or fork hardening, and the observe-only lanes change no
record. For those, a rollback that can't wait for the quiet slot is HQ's decision to redeploy
`sds-lanes` by hand at once, accepting that it stops every running pass midway.

The Mac agent runs from the main tree with the Keychain identities it had, which stay valid until
increment 6. A rollback takes no new credential and changes no record, because the lane code is
identical in both places.

Rollback is available for every moved lane until increment 6, which removes the plists and the
Keychain items' lane role. Increment 6 requires a passed week bar, so no lane loses its rollback
before the week has shown the move holds.

#### The week bar

The bar is the year plan's: a lane survives the Mac being off for a week. It's measured as
follows: for 7 consecutive days the Mac is off or asleep, and every moved lane's Mac agent is
booted out. Over those 7 days, each moved lane's Healthchecks check records no down event and no
`/fail` that is absent from that lane's pre-move baseline. A down event fails the bar unless the
baseline has a down event too, and a `/fail` fails it unless the baseline has a `/fail` with the
same finding.

The baseline is captured at the lane's cut-over and kept as evidence, because Healthchecks
removes the history it's drawn from before the week runs:

- **Down events** come from the check's flips (`/api/v3/checks/<id>/flips/`), which Healthchecks
  keeps for the current month and the two full months before it (Healthchecks API docs, "List
  check's status changes"; verify on the self-hosted instance before the increment). A
  lane's down baseline is the flips its check has from the 4 weeks before cut-over, or from the
  check's creation if that's more recent. A check with no flips in that span has a baseline of
  zero down events.
- **`/fail` pings** come from the check's ping log (`/api/v3/checks/<uuid>/pings/`), which returns
  only a check's most recent pings, up to the account's ping-log limit (100 for a free account in
  the Healthchecks API docs, "List check's logged pings"; verify the self-hosted instance's limit
  before the increment). Every pass sends two pings, `/start` and its result, so at a limit of 100 a
  daily lane keeps about 7 weeks and an hourly lane about 2 days. A lane has a `/fail` baseline only
  if its ping log, read at cut-over, reaches back 4 weeks. At a limit of 100 that's the seven daily
  lanes (bump proposer, fork hardening, landing ledger, membership check, pin watcher, rotation
  proposer and work carrier), and not the ten that run more often.
- **A lane whose ping log doesn't reach back 4 weeks is held to zero `/fail` for the week.**
  Pinging its check longer on the Mac can't give it a baseline, because the log keeps no more
  than its limit however long the lane runs. Zero `/fail` needs no history, so it keeps the bar
  objective. It's also the lane's ordinary contract: a delivered finding pings success
  (`--finding`, `--told`), so a `/fail` means a pass that couldn't do its job.

What stays on the Mac goes quiet that week and is excluded from the bar: the three agents in
decision 4, and the VM rotation executor until ADR-0059 increment 7 moves it. Their checks going
down is expected. Neither the rotation proposer's `cred-consumers.list` nor any other moved lane's
input may still live on the Mac by then (decision 5).

The evidence is each moved check's flips and ping log for the week, its baseline as captured at
cut-over, and the Mac's power log (`pmset -g log`) covering the 7 days. The read-only Healthchecks
key reads flips. The ping log needs the Management API key (`51509d5e-…`), because `/pings/` isn't
among the endpoints the Healthchecks API docs list for a read-only key (verify on the self-hosted
instance before the increment). HQ reads it from the deploying session on the Mac through the
broad account, as the install-time upsert does. No lane holds it.

## Consequences

- The lanes stop depending on the Mac being on, logged in and awake, and on its working copy.
- The VPS holds every moved lane's BWS token as a Coolify environment variable. Anyone with root
  on the VPS, or with a Coolify token holding `read:sensitive`, can read them. This is the same
  boundary the orchestrator's own secrets have today.
- Each lane's reach narrows from one of two shared identities to its own secrets. A leaked lane
  token exposes one lane's secrets, not every SDS bearer, and no lane can change or delete any
  Healthchecks check.
- Each moved lane's ping URL is a per-check secret, which the header of `sds-deadman.sh` argues
  against: one secret per check can be copied and rotated in only some places. Here each URL is
  stored once in BWS and read by UUID, so no copy exists to go stale, and an API key rotation
  doesn't touch it. The cost is that a UUID ping URL can't be rotated: Healthchecks assigns the
  UUID at creation and it can't be changed (Pinging API docs, "UUIDs and Slugs"). A leaked ping URL
  lets its holder fake one lane's passes, so that lane's check can't report it stopping; it can't
  reach any other check. Its remedy is to recreate that check and store the new URL, which
  starts a new check history. The credential registry records each ping URL as a credential
  with that remedy, in the increment that creates it.
- A lane runs only code the served orchestrator runs, except while the deploy command waits for a
  quiet slot: for up to 6 hours after `finish`, lanes run the previous release's code against the
  new orchestrator, so each release keeps that code compatible (decision 1). A lane fix that
  reaches production needs an orchestrator release, as any other orchestrator change does.
- Today's lane invariants that name the main tree or launchd move to their container
  equivalents. The relevant ones are "the scheduled lanes run the main tree's working copy", the
  launcher-credential rules, and the PATH rule for `uv`. Each increment rewrites the invariant it
  makes untrue.
- By Q3's decision, the VM rotation executor gains write reach over every lane's BWS identity once
  ADR-0059 increment 7 lifts ADR-0055 decision 4 for lane tokens. A compromised executor could then
  re-issue any lane's token.
- Lane tokens are registered and unrotated from the first lane identity (increment 2) until
  ADR-0059 increment 7. That window is bounded: it must not outlast the credential registry's
  rotation interval for a lane token. If the increments would run past that interval, HQ decides
  how to proceed before the interval passes; the increments don't extend it.
- By Q1's decision, the "SDS lanes" App's private key reaches only what the App's installation
  reaches: intent-packages and the forks, with `contents: write`. A leaked key lets its holder push
  to intent-packages `main` and to each fork's branches until the key is deleted on the App's
  settings page. It reaches no other repository, unlike the dispatch App, whose installation is
  deliberately wider than its work. The key is hand-rotated (Q4), so its rotation depends on
  Devon, as the rotation App's key does. ADR-0055's hand-rotated set names who rotates its
  members, not how often, so the interval comes from the credential registry: the key is
  registered with a rotation interval in the increment that creates it, as a lane token is. If
  the key would outlast that interval, HQ asks Devon to rotate it before the interval passes.
  That increment also states how the key's due date is reported without a rotation package
  being proposed for a key no executor handles.
- intent-packages `main` requires three status checks under classic branch protection with
  `enforce_admins` off (read 2026-11-05), which is what lets Devon's admin push land directly
  today. The App isn't an admin, so its direct push would be refused until intent-packages admits
  it, for example as a bypass actor of a ruleset that replaces the classic rule (verify before
  increment 4). That change is Devon's, in increment 4, and it doesn't add a required review:
  intent-packages requires none. Each fork's `hardened` branch forbids only force-push and deletion
  (ADR-0057 decision 7), so fork hardening's fast-forward push needs no change.
- Package revision commits on intent-packages `main` become the App's (`<app>[bot]`), not
  `AlobarQuest`'s. Increment 4 checks every reader that keys on a package revision commit's author
  before the bump proposer moves; `landing_ledger` reads a `[bot]` suffix as a machine basis.

## Dependencies

- **ADR-0039.** Moving a lane changes where it runs, not what it carries. ADR-0039's measurement
  names producers by module, not by host, so this ADR doesn't change it. Q1's answer gives a
  machine identity, the "SDS lanes" App, write access to an authoring repository
  (intent-packages), so ADR-0039 needs an amendment that restates who authors a package revision.
  That amendment is written separately, before the bump proposer moves.
- **ADR-0055.** ADR-0059 increment 7's ADR-0055 amendment moves the VM executor to the VPS and,
  by Q3's decision, lifts decision 4 for lane tokens so that the executor rotates them.

## Devon's decisions

Devon decided all five questions on 2026-11-05.

### Q1: the pushing lanes push as a new "SDS lanes" GitHub App (HQ D-3)

Option (c), decided by Devon on 2026-11-05 (HQ D-3): a new "SDS lanes" GitHub App, installed only on
intent-packages and the forks with `contents: write`, pushes for `bump-proposer`,
`rotation-proposer` and `fork-harden` once they leave his `gh` login. Its commits are the App's
(`<app>[bot]`), so the authorship they record is true, and the App reaches only the repositories it
pushes to. The rejected options were a pull request instead of a push, which changes two proposers,
delays each revision by one landing and retires the direct-push rule for these lanes
(`PUBLISH_COMMAND` in `bump_proposer/standing.py`, which the bump proposer runs and the rotation
proposer runs through `standing.publish`; fork hardening doesn't use it, and the tool installer's
own copy in `tool_installer/plugin.py` stays on the Mac), and one write-scoped fine-grained PAT per
pushing lane, which keeps a scheduled machine pushing as Devon. The App's cost is accepted: a new
App, its key in BWS, the intent-packages branch-protection change, and a check of every reader that
keys on a package revision commit's author (Consequences). Creating the App is a later increment's
work and Devon's act; this ADR plans it.

### Q2: the machine-activation sweep stays on the Mac (HQ D-4)

Option (a): `activation-sweep` stays on the Mac as a third exception, beside the tool installer
and the Keychain rotation instance. It observes the Mac, and the Mac is where what it measures
lives. The rejected options were to retire it, which would leave ADR-0031's activation helper with
no control, and to rebuild it to bind from GitHub, which is new code that still leaves the sweep on
the Mac. This departs from the year plan's two-exception text; HQ updates the year plan.

### Q3: the VM executor rotates lane BWS tokens (HQ D-5)

Option (a): once ADR-0059 increment 7 moves the VM rotation executor to the VPS, it rotates each
lane's BWS token. It already writes Coolify variables with its own Coolify token. ADR-0055
decision 4, as amended, says a BWS consumer token may be handled only by an executor instance on
the machine that already holds it, and never by the VM. ADR-0059 increment 7's ADR-0055 amendment
lifts decision 4 for lane tokens. The cost is accepted: the executor gains write reach over every
lane's identity. Until that amendment, lane tokens are registered and unrotated. The rejected
options were the hand-rotated set, which conflicts with Devon's "SDS can not do an all human
rotation" (2026-10-21), and a new confined rotation instance, which would add a third orchestrator
identity.

### Q4: the "SDS lanes" App's private key is hand-rotated (HQ D-6)

Option 1: the App's private key joins ADR-0055's hand-rotated set, like the `alobar-sds-rotation`
App's key. Q3's answer, rotation by the VM executor, doesn't fit it: GitHub generates an App
private key only on the App's settings page (Credentials, Key pairs, **New key**), and GitHub Docs,
"Managing private keys for GitHub Apps", documents no API that creates one. The rejected option was
Devon minting the key and the executor taking it from its inbox
(`docs/operations/rotation-executor.md`, "A person's mint is taken from the inbox"), which needs
a new adapter.

### Q5: the deploy command reads lane checks with the read-only Healthchecks key (HQ D-7)

The quiet-slot wait (decision 1) runs in the deploy command on the Mac, which reads BWS as the
narrow `sds-operator` identity. Increment 1 gives `sds-operator` a direct read grant on the
read-only Healthchecks key (BWS `f07b1368-…`, `Rotation / Healthchecks`), and the wait reads lane
checks with it. A read-only key may list checks (Healthchecks API docs, "List Existing Checks"),
which is all the read needs. The Management API key (`51509d5e-…`) stays readable only by the
broad account; no lane identity reads it (decision 3).
