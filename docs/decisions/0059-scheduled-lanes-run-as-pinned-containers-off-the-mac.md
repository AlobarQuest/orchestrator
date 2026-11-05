# ADR-0059 — Scheduled lanes run as pinned containers off the Mac, one BWS identity per lane

- **Status:** Proposed
- **Date:** 2026-11-05
- **Decided by:** not yet decided. HQ's three open questions are marked **Open (Q1–Q3)** in the
  text that follows.
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
  `finish` passes, and no lane runs code the served orchestrator doesn't. This replaces
  `activate-checkout.sh`'s fast-forward of the main tree. A moved lane never reads a working copy.
- **The lanes that need other repositories clone them at the start of each pass.** These lanes
  are the bump driver (intent-packages and its venv, project-standards, security-standards), both
  bridge proposers (intent-packages; the rotation proposer also needs infraops-mcp-server), the
  work carrier (package checkouts) and fork hardening (the forks). Each clone is shallow, at
  `main`, into a container-local directory that's discarded with the container, so a container
  holds nothing that can't be rebuilt.
- **A lane's kill switch stays the variable it is today** (ADR-0046). Stopping one service stops
  one lane.

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
  its container with `env -i PATH=… HOME=… BWS_ACCESS_TOKEN=…`. A dry run reads every secret, and
  a deliberately revoked token fails, tested with an empty `HOME` because `bws` caches sessions per
  token id (workspace invariant #23).
- **Every GitHub credential moves to BWS.** No moved lane reads `gh auth token`. Reads use one
  read-only fine-grained token in BWS, with a direct grant per lane identity. A write needs a
  separate decision: see "Who pushes" (**Open, Q1**).
- **`.bws-secrets.toml` and `.cred-consumers.toml` gain each lane identity and each secret it
  reads**, in the same change as the lane's move. The credential registry gains each lane token
  as a credential. Who rotates it is **Open, Q3**.
- **This ADR creates no identity.** Each increment that moves a lane creates that lane's account
  and grants.

### 3. The alarms stay on the same Healthchecks checks

- **Every lane keeps its check name.** `sds-deadman.sh` finds a check by exact name through the
  Management API, so a lane that runs elsewhere pings the same check. The check's history carries
  across the move.
- **Each lane identity gets a direct read grant on the Healthchecks key** (`51509d5e-…`), which
  today only the broad account reads. Without it, the lane pings nothing and the check goes down
  after its grace (ADR-0021 amendment 3).
- **Each check's schedule and grace are re-checked against the container's crontab** at cut-over.
  Every check without one is provisioned through `sds_upsert_check`, which closes the "5 of 20
  installers" gap the runtime pass left.
- **The outcome alarm and the dead-man switch keep their semantics.** The lane code doesn't
  change, so `--finding` and `--told` keep their meanings and `test_sds_deadman.py` still holds
  them.

### 4. What stays on the Mac

Two agents stay, both because their subject is the Mac itself:

- **The tool installer** installs binaries and the Claude plugin on this machine.
- **The Keychain rotation instance** rotates BWS machine tokens held in this machine's Keychain
  (ADR-0055 amendment 12).

**Open (Q2): the machine-activation sweep.** Both of its passes read `~/Projects/<member>` on this
machine, so it can't move. It acts on no member, so the phase bar allows it to stay. The year plan
names only the two agents in the preceding list.

**The VM rotation executor** isn't a launch agent, but it runs on the Mac (OrbStack) and acts on
members. ADR-0055 decision 2 built it to "move unchanged to a VPS". Increment 7 in the
"Increments" section moves it. Its decision 2 and T1 residual are amended in that increment's own
amendment to ADR-0055, not here.

### 5. Classification of the 20 launch agents

| Launch agent | Disposition | Reason |
|---|---|---|
| `activation-sweep` | **Open (Q2)** | Measures this machine's working copies, and both passes read local checkouts. |
| `bump-driver` | Moves | Dispatches and merges bump units as SYSTEM, acting on members. |
| `bump-proposer` | Moves | Pushes standing-package revisions to intent-packages `main` (Q1). |
| `change-proposer` | Moves | Reads pull requests and writes change records, with no local state. |
| `deploy-watcher` | Moves | Observes rollouts through APIs only. |
| `estate-landing` | Moves | Lands pull requests and re-points production, acting on members. |
| `execution-watch` | Moves | Reads execution status and raises alerts, with no local state. |
| `fork-harden` | Moves | Pushes forks' `hardened` branches (Q1). Its clone cache is rebuildable. |
| `fork-sync-watch` | Moves | Reads fork sync state through APIs only. |
| `inert-landing` | Moves | Lands update-bot pull requests, acting on members. |
| `landing-ledger` | Moves | Files observations from GitHub reads, with no local checkouts. |
| `pin-watcher` | Moves | Reads callers' runner pins through APIs only. |
| `revision-watcher` | Moves | Reads Coolify and App Brain through APIs only. |
| `rotation-decomposer` | Moves | Proposes decompositions as SYSTEM, with no local state. |
| `rotation-proposer` | Moves | Writes package revisions (Q1). Runs infraops's `cred-findings` from a clone. |
| `rotation-worker` | Stays | The Keychain rotation instance: its store is this machine's Keychain. |
| `sds-membership-check` | Moves | Compares the member record with declarations through APIs only. |
| `tool-installer` | Stays | Installs tools on this machine, so its subject is the Mac. |
| `unit-verifier` | Moves | Runs `/verify` as VERIFIER, with no local state. |
| `work-carrier` | Moves | Registers intakes from package clones, with no other local state. |

No agent retires under this ADR unless Q2's answer retires the sweep. Seventeen move, two stay,
and one is open.

The rotation proposer reads `~/.config/infra-drift/cred-consumers.list` through infraops's CLI.
Its increment must find that file's source and carry it as data in the clone or in BWS. A file that
exists only on the Mac would keep the lane tied to it.

### 6. Migration order, cut-over, rollback and the week bar

#### Increments

1. **The lane image and `sds-lanes`, with no lane.** Build the lane image in `Release image` and
   create the compose resource with one placeholder service that runs `--dry-run` on a schedule
   and holds no credential. Teach the deploy command to write the `sds-lanes` tag.
2. **One observe-only lane first: `execution-watch`.** It has one identity, two secrets, no local
   state and the shortest interval (15 minutes), so its check shows a failure fastest. It proves
   the identity, the `env -i` start, the dead-man ping and the cut-over procedure.
3. **The other observe-only lanes:** landing ledger, pin watcher, deploy watcher, revision
   watcher, fork-sync watch, membership check, unit verifier and rotation decomposer, one at a time.
4. **The proposers and carrier, which need clones:** change proposer, work carrier, then bump and
   rotation proposers once Q1 is answered.
5. **The member-acting lanes:** inert landing, then estate landing, then the bump driver, then
   fork hardening (Q1). Each moves outside its landing window. Estate landing sends its Coolify
   token only over the tailnet today, so its increment decides how the container reaches Coolify
   without widening that.
6. **Retire the Mac's lane plumbing:** the moved plists and their installers,
   `activate-checkout.sh`'s use by lanes, and the two Keychain items' lane role. The Keychain items
   themselves are retired only once no remaining reader names them.
7. **The VM rotation executor moves to the VPS** under its own ADR-0055 amendment. This increment
   is last because it moves the root of the rotation design.

Each increment's plan records which repositories it changes and which identities it creates.

#### Cut-over for one lane

1. Create the lane's BWS identity and grants, and prove them under `env -i` in the lane's container
   with `--dry-run`. The container's scheduler is disabled.
2. Pick a cut-over time between two scheduled passes. Boot out the Mac agent with `launchctl
   bootout gui/<uid>/com.devon.<lane>`, and keep its plist.
3. Enable the container's schedule. Its first pass pings the same check.
4. Verify that the check's last ping came from the container and that the Mac agent isn't loaded.
   The lane's own output in the container log must match the Mac log's last pass.

The Mac agent and the container never run the same lane at once. Two landers, or two carriers,
would race on the same records.

#### Rollback for one lane

Disable the container's schedule, then `launchctl bootstrap` the kept plist. The Mac agent runs
from the main tree with the Keychain identities it had, which stay valid until increment 6. A
rollback takes no new credential and changes no record, because the lane code is identical in both
places.

#### The week bar

The Mac can't be literally off while two agents stay on it. The bar is measured as follows:
for 7 consecutive days, with every moved lane's Mac agent booted out, each moved lane's
Healthchecks check records no down event and no `/fail` caused by the move. The week also contains
at least one window where the Mac is asleep or powered off for 24 hours or longer, during which
each check's pings continue on schedule. The evidence is each check's ping log for the week, read
through the read-only Healthchecks key, and the Mac's power log for the off window.

## Consequences

- The lanes stop depending on the Mac being on, logged in and awake, and on its working copy.
- The VPS holds every moved lane's BWS token as a Coolify environment variable. Anyone with root
  on the VPS, or with a Coolify token holding `read:sensitive`, can read them. This is the same
  boundary the orchestrator's own secrets have today.
- Each lane's reach narrows from one of two shared identities to its own secrets. A leaked lane
  token exposes one lane's secrets, not every SDS bearer.
- A lane runs only code the served orchestrator runs. A lane fix that reaches production needs an
  orchestrator release, as any other orchestrator change does.
- Today's lane invariants that name the main tree or launchd move to their container
  equivalents. The relevant ones are "the scheduled lanes run the main tree's working copy", the
  launcher-credential rules, and the PATH rule for `uv`. Each increment rewrites the invariant it
  makes untrue.

## Dependencies

- **ADR-0039.** Moving a lane changes where it runs, not what it carries. ADR-0039's measurement
  names producers by module, not by host, so this ADR doesn't change it. If Q1's answer gives a
  machine identity write access to an authoring repository (intent-packages), the ADR-0039
  amendment that follows this ADR must restate who authors a package revision. That amendment is
  written separately.
- **ADR-0055.** Q3 and increment 7 each need an ADR-0055 amendment.

## Open questions

- **Q1: who pushes.** Three moved lanes push as Devon's `gh` login today: the bump proposer and the
  rotation proposer push intent-packages `main`, and fork hardening pushes forks' `hardened`
  branches.
- **Q2: the activation sweep.** Stay as a third Mac exception, retire it, or rebuild it.
- **Q3: who rotates a lane's BWS token** once it's a Coolify variable and no longer a Keychain item.
