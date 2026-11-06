# ADR-0059 — Scheduled lanes run as pinned containers off the Mac, one BWS identity per lane

- **Status:** Proposed
- **Date:** 2026-11-05
- **Decided by:** Devon decided Q1 to Q5 on 2026-11-05 (HQ decisions D-3 to D-7) and Q6 to Q10 on
  2026-11-06 (HQ decisions D-8 to D-11, D-13 and D-14), all recorded in "Devon's decisions". The
  rest of the ADR is proposed and not yet accepted.
- **Relates to:** ADR-0055 (the rotation executor's isolated runtime and decisions 2 and 4,
  amendments 6, 12 and 15), ADR-0058 (lanes read their scope from the member record), ADR-0039
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
machine. Agents that other repositories own also run on it; decision 5 classifies the ones that
touch the SDS ("Agents outside this repository"). Nineteen of the twenty share the first three
Mac dependencies, and sixteen of those also share the fourth. The twentieth, `rotation-worker`
(the Keychain rotation instance), shares only the first: it runs the executor from a pinned commit
under its install's `__BASE__`, never the main tree, reads its own bearer from the Keychain, and
fetches through `RepoMirror` with no GitHub credential (`scripts/com.devon.rotation-worker.plist`).
The four dependencies are:

- **launchd** starts it, in Mac-local time (America/New_York).
- **The main tree's working copy and `.venv`** are its code. Every launcher except the two bridge
  proposers and the rotation worker sources `~/.claude/bin/activate-checkout.sh`, which
  fast-forwards and syncs the main tree before it runs.
- **Two Keychain items** hold its BWS identities: `Claude`/`BWS_ACCESS_TOKEN_SDS` (the narrow
  `sds-operator` account) and `Claude`/`BWS_ACCESS_TOKEN_VPS_BACKUP` (the broad shared account).
  `scripts/sds-deadman.sh` reads the Healthchecks Management API key (BWS `51509d5e-…`) through the
  broad one.
- **Devon's interactive `gh` login** is the GitHub credential of sixteen of the nineteen. Thirteen
  launchers fall back to `gh auth token` for their GitHub variable, fork hardening uses
  `gh auth git-credential`, the fork-sync watch runs `gh api` on the stored login
  (`src/fork_sync/gh.py`), and the rotation proposer pushes on whatever credential the packages
  checkout's git finds. The other three (activation sweep, execution watch and rotation
  decomposer) use no GitHub credential. No lane has a GitHub credential of its own.

The Lane runtime pass (2026-11-04, PR #609) left these to this phase: the plists and installers,
the Keychain reads and the two-identity scheme, `activate-checkout.sh` and the main-tree working
copy, and provisioning every lane's check from its installer
(`docs/superpowers/specs/2026-11-04-runtime-layer-design.md`, "Left for Phase 1"). It kept the lane
code and the alarms so that they carry over. This ADR changes every moved lane's launcher and
`scripts/sds-deadman.sh`, and no lane module beyond the docstrings decision 2 and the increments
name.

Every lane's console script is already in the orchestrator image. `[project.scripts]` in
`pyproject.toml` declares all of them, and the Dockerfile installs the project into `/app/.venv`.

The pattern to apply is the rotation executor's (ADR-0055 decision 2 and increment 4): a
deterministic program in an isolated runtime, holding only its own identity, started by an init
system with only `PATH` and `HOME` set. The lanes keep two departures from it. A lane's container
starts the launcher with `PATH`, `HOME`, its one secret (`BWS_ACCESS_TOKEN`) and a fixed list of
non-secret variables (decision 1, "`lane-run`"). And eleven lanes keep state between passes that
can't be rebuilt from a read (alert memory, first-sighting clocks, the bump driver's pass lock), so
each of those lanes keeps it in a volume of its own (decision 1, "Lane state").

## Decision

### 1. The runtime: one container per lane, from a lane image pinned to the served orchestrator

- **Each moved lane is one service of a single Coolify compose resource, `sds-lanes`, on the
  production VPS beside the orchestrator.** `sds-lanes` is a Coolify *service* resource of type
  `docker-compose`, not an application: infraops creates one with `coolify_create_service`
  (`docker_compose_raw`), replaces its compose with `coolify_update_service`
  (`docker_compose_raw`, sent as a `PATCH /services/{uuid}`), and writes its variables with
  `coolify_create_service_env` and `coolify_update_service_env` (infraops-mcp-server
  `src/tools/services.ts` and `src/tools/env-vars.ts`). The orchestrator's own
  `coolify_update_application` path doesn't apply to it. Each service runs its lane's launcher
  under a scheduler inside its own container, with `TZ=America/New_York`, so every schedule keeps
  its current wall-clock time. That matters for the time-windowed lanes: estate landing lands bumps
  only from 02:00 to 06:00 New York time.
- **The schedule is tracked in this repository**, one crontab line per lane beside its launcher,
  the way each plist is tracked today. The lane image bakes it in, so a schedule change is a
  reviewed commit and a new image, as a plist change is now.
- **The lane image is built `FROM` the orchestrator image at the same commit.** It adds only what
  the lanes need beyond the orchestrator. The tools are `git`, `gh`, `uv`, `node`, the `bws` CLI,
  `curl` with `ca-certificates` (every ping goes through `curl`: `scripts/sds-deadman.sh` and
  `sds_upsert_check` in `scripts/sds-install.sh`, and the runtime stage is `python:3.14-slim`
  with no `apt-get` step), and the scheduler binary. The files come from this repository. The
  orchestrator `Dockerfile`'s runtime stage holds the venv, `src`, `registry-bundle.json` (built
  in the builder stage by `scripts/build_registry_bundle.py`), the three security-standards trees
  from the `registry` build context under `/app/security-standards`, `alembic.ini` and
  `migrations`, and no launcher. It also sets `ORCHESTRATOR_REVISION`, `PATH`,
  `ORCHESTRATOR_REGISTRY_BUNDLE` and `SECURITY_STANDARDS_DIR`, and ends with `EXPOSE 8000`, a
  `HEALTHCHECK` that requests `/health/live` on port 8000, and `CMD` running `uvicorn`. The lane
  image runs no uvicorn, so it replaces `CMD` with its entrypoint, and the inherited `HEALTHCHECK`
  would report every lane container unhealthy unless the lane image replaces it (how Coolify
  treats an unhealthy service container is verified before increment 1). So the lane image adds the
  `scripts/run-*.sh` launchers, the four `scripts/sds-*.sh` helpers they source,
  `scripts/sds_scope.py` (which `run-landing-ledger.sh` and `run-fork-harden.sh` run as
  `.venv/bin/python -m scripts.sds_scope` from `REPO_ROOT`), the tracked crontab, and the
  `lane-run` command (cut-over step 1). It copies the launchers, the helpers and
  `sds_scope.py` to `/app/scripts/`. It also creates each `/var/lib/sds/<lane>` state directory
  (decision 1, "Lane state") owned by `orchestrator`: the orchestrator image ends
  `USER orchestrator`, so the lane image does its installs and that `mkdir` as root, then returns
  to `USER orchestrator`. Each launcher sets `REPO_ROOT` to its own
  directory's parent, so in the image `REPO_ROOT` is `/app`, and `$REPO_ROOT/.venv/bin/<lane>` is
  the orchestrator image's `/app/.venv/bin/<lane>`, with no launcher edit. The image carries no
  `~/.claude/bin/activate-checkout.sh`, so a launcher that sources it takes its missing-helper
  branch, which prints one `[activation]` line and updates nothing. The `Release image` workflow
  builds the lane image beside the orchestrator image from the same 40-character sha, so a lane
  image and an orchestrator image built by the same run hold the same lane code. A `sha-` tag is
  derivable, not immutable: a rebuild re-points it, and the digest is the identity
  (`docs/operations/deploy.md`, "`sha-<full-sha>` is DERIVABLE, not IMMUTABLE"). So `prepare`
  reads the lane image's pushed digest from the same run's verify step, which increment 1 makes
  log it, as it reads the orchestrator's, and records it in its state file. The lane phases carry
  that digest, and `lanes-finish` checks every container runs it (following bullets).
- **A lane's image is pinned to the served orchestrator tag, and the deploying session moves it.**
  After the orchestrator's `finish` passes, `scripts/deploy_orchestrator.py` prints the lane phases'
  first command, and the deploying session writes the same tag to `sds-lanes` by the lane phases
  (following bullets). A lane runs either the served tag or, until the session's quiet-slot
  redeploy, the tag the orchestrator served until that release, and no other. An orchestrator
  rollback keeps that true the same way: it also runs the lane phases with `--tag` set to the
  rollback target, so the lanes follow the orchestrator back. This replaces
  `activate-checkout.sh`'s fast-forward of the main tree. A moved lane never reads a working copy.
- **The deploy command never calls Coolify; it checks, prints the calls, and verifies.** Its
  docstring states the rule this keeps: every Coolify change goes through the infraops MCP tools,
  which a local program can't call, so `prepare` prints the exact `coolify_update_application` and
  `coolify_deploy` calls and the deploying session runs them. HQ is that session, as the one
  production actor. Increment 1 gives `sds-lanes` changes the same shape, in three phases of the
  same program. Each phase takes one change, `--tag <tag>`, `--schedule <lane>=<true|false>` or
  `--add <lane> --tag <tag>`, and a state file. An `--add` names the tag whose tracked compose
  carries the lane's service, which must be the served orchestrator tag, because a new service
  arrives with the commit that adds its launcher and crontab line. Every run writes the whole
  compose file, so a tracked compose edit deploys with the tag that carries it:
  `coolify_update_service` sends the whole file as `docker_compose_raw`, so no run writes less
  (that the next `coolify_deploy` applies it is verified before increment 1). The compose a run
  writes is the tracked file at the tag it deploys (the named tag for `--tag` and `--add`, the
  running tag for `--schedule`), and its state file records that compose exactly. The compose it
  replaces is the one the last finished run's state file recorded, the file Coolify was last given,
  never the tracked file at the running tag. No phase creates `sds-lanes`, and none yet records the
  first compose a later run replaces (verify before increment 1).
  - **`lanes-prepare`** waits for a quiet slot (following bullet), then records the change, the
    compose it writes, and the values they replace, and prints, exactly, the session's calls: the
    writes (the whole compose through `coolify_update_service`, and the variable through
    `coolify_update_service_env`: `SDS_LANES_IMAGE_TAG` for `--tag` and `--add`, the lane's schedule
    variable for `--schedule`), then `coolify_deploy` on `sds-lanes`, then `lanes-finish`. It prints
    every service whose compose entry differs from the recorded compose, so the session's write
    names each edit it applies. A service is added only by `--add` for that lane: a run whose
    compose adds any service but the `--add` lane's is refused. It prints the inverse writes too
    (the recorded compose, and the replaced variable value), for `lanes-finish` to name if the
    redeploy fails. The old tag or schedule value, and each stateful service's volume (the following
    list), are what the session reads from the running containers and passes in, as `prepare` takes
    `--coolify-health-check-enabled`. The session reads the tag with `vps_docker_ps`, a schedule
    value with `vps_exec` running `printenv LANE_SCHEDULE_ENABLED` in that service's container, and
    a volume and an image digest with `vps_exec` running `docker inspect` with a `--format` that
    prints only the container's mounts or its image, so no read lists another variable's value.
  - **`lanes-recheck`** repeats the two quiet-slot reads. The session runs it, then the writes and
    the redeploy, with no other step between: the writes are staged only if this exits 0, and
    immediately before the redeploy. A non-zero exit sends the session back to `lanes-prepare`.
  - **`lanes-finish`** takes the session's reads of the redeployed containers, for every service,
    not only the changed one:
    - its image's digest, read container to image to repository digest as
      `docs/operations/deploy.md` reads the orchestrator's, which must equal the lane image digest
      the release recorded (preceding bullet). The tag alone isn't the check;
    - its `LANE_SCHEDULE_ENABLED`;
    - for each of the eleven stateful services (decision 1, "Lane state"), the source of the mount
      at `/var/lib/sds/<lane>`, which must be the volume name recorded for that service. Coolify's
      `parse()` of a stored compose definition can double-prefix a volume's name and start the
      service on a new, empty volume while its health stays green (workspace invariant #4), so a
      green redeploy proves nothing about the volume. An added service has no recorded volume; its
      `lanes-finish` records the one it reads, and the cut-over copies into that one.

    It exits 0 only if they show the change and no other. Otherwise, or if the redeploy failed, it
    exits 2 and prints the inverse writes and a second `coolify_deploy`, and the session runs them,
    then `lanes-finish --restored` with fresh reads, which exits 0 only if the containers show the
    replaced values again. That restore is the session's act, which the command names and then
    checks.

  No phase holds a Coolify credential, and none needs one.
- **A routine tag update redeploys `sds-lanes` only in a quiet slot.** Coolify's deploy endpoint
  (`POST /deploy`) takes resources by `uuid` or `tag`, and its restart endpoint
  (`POST /services/{uuid}/restart`) takes the service resource's `uuid` and a `latest` flag. Neither
  names a service within a compose resource (Coolify API reference, "Deploy" and "Restart Service",
  read 2026-11-05). So every tag update recreates every lane's container at once, and a running pass
  is stopped midway. The lane phases therefore apply the cut-over's two checks to the whole resource
  before the session redeploys: the time is outside estate landing's 02:00 to 06:00 New York window,
  and no moved lane's check has `started` set to `true`. `started` is the boolean field of each
  check in the Healthchecks Management API's check listing (`GET /api/v3/checks/`, API docs "List
  Existing Checks"), read just before the redeploy. The API docs show `started` only in their
  example responses and don't describe it, so increment 1 verifies, before it relies on the field,
  that the self-hosted version returns it in the listing and sets it to `true` from a `/start` ping
  until the pass's result ping. Every scheduled pass pings `/start` first, so `started` is the one
  signal every lane already sends. A check whose `status` is also `down` doesn't hold the deploy,
  because its pass stopped without a final ping and the down alert already reports it. The deploy
  command runs where it runs today, in the deploying session on the Mac. It reads the listing with
  the read-only Healthchecks key (BWS `f07b1368-…`, in `Rotation / Healthchecks`), through a direct
  read grant to `sds-operator` that increment 1 adds (Q5, decided). Until both checks pass,
  `lanes-prepare` waits and reads again, for at most 6 hours: long enough to outlast the 4-hour
  landing window and a pass already running when it closes. At 6 hours it raises an alert through
  alert-hub and exits with its "condition unmet" code (2). The session has written nothing, so
  `sds-lanes` stays as it was until HQ re-runs `lanes-prepare` or redeploys by hand. Nothing is
  staged in `sds-lanes` while the command waits: the session writes the compose and any tag or
  service variable (the following bullets) only after `lanes-recheck` exits 0, immediately before
  its redeploy call. So a wait that times out leaves nothing staged, and no later redeploy can apply
  a change silently. If the redeploy fails after the writes, `lanes-finish` names the inverse writes
  and the session restores the values at once. The residual: a pass that starts between
  `lanes-recheck`'s read and the redeploy is still stopped, as `recheck` before an orchestrator swap
  accepts a unit claimed after it. The P2 backlog item in which `deploy_orchestrator.py` holds the
  bump-driver launch agent during a deploy is re-scoped by this rule once the bump driver moves.
- **Until the lanes' redeploy, the old lane code runs against the new orchestrator.** A
  release's orchestrator is served from `finish`, but its lanes stay on the previous tag until
  the session's quiet-slot redeploy: at most 6 hours when `lanes-prepare` finds a slot, and until
  HQ acts when it times out. So every release must keep the previous release's lane code working
  against its orchestrator for that interval: a route or response field a lane reads can't be
  removed or changed incompatibly in the release that stops the lane needing it.
- **The lanes that need other repositories clone them at the start of each pass.** These lanes
  are the bump driver (intent-packages, project-standards, security-standards), both
  bridge proposers (intent-packages; the rotation proposer also needs infraops-mcp-server), the
  work carrier (package checkouts) and fork hardening (the forks). Each clone is shallow, at
  `main`, into a directory the pass discards. A clone carries no venv, so the bump driver's
  launcher builds intent-packages' venv in its clone with `uv sync` each pass, for the `factory`
  command it puts on `PATH`; it points uv's cache (`UV_CACHE_DIR`) and uv's managed Pythons
  (`UV_PYTHON_INSTALL_DIR`) into the lane's state volume (following bullet), because each pass
  has a new `HOME`, so a pass downloads only what changed. Three Mac checkout variables point at
  these clones in the container, and each launcher sets its own from the pass's clone directory,
  so none joins `lane-run`'s list: `BUMP_PROPOSER_PACKAGES_CHECKOUT` (both bridge proposers) at
  the intent-packages clone, `ROTATION_PROPOSER_INFRAOPS_CHECKOUT` at the infraops-mcp-server
  clone, and `WORK_CARRIER_CHECKOUT_ROOT` at the directory that holds the work carrier's package
  clones, each as `<root>/<repo>` (`work_carrier/cli.py`). On the Mac the plists set them to
  `~/Projects` paths. The other caches a lane keeps are
  rebuildable and may start empty: the bump driver's `mirrors` (in its state directory, so in its
  volume), fork hardening's bare caches (`fork_harden/cli.py`, `DEFAULT_CACHE` under `HOME`) and
  the `RepoMirror` roots of the rotation decomposer and rotation proposer (`~/.cache/<program>`,
  `rotation_worker/git_mirror.py`). The last two sit under `lane-run`'s new `HOME`, so each pass
  refetches them; increment 5 may point fork hardening's `--cache` into a volume if that refetch
  is too slow.
- **Lane state: each lane that keeps state has a named volume, and its state variable points at
  it.** Eleven moving lanes keep files between passes that a pass can't rebuild from a read. Each
  file sits on the Mac in the lane's source directory, under
  `~/Library/Application Support/sds/`, or under the lane's `*_STATE_DIR` variable when it's set.
  The source directory is the lane's name except for two lanes, whose modules name another
  (`unit_verifier/alerts.py`, `work_watcher/alerts.py`):

  | Lane | Variable | Mac source directory | Files | If it starts empty |
  |---|---|---|---|---|
  | `bump-driver` | `BUMP_DRIVER_STATE_DIR` | `sds/bump-driver` | `alerts.json`, `pass.lock`, `mirrors/` | Alerts the Mac fired never resolve. The lock and mirrors rebuild. |
  | `bump-proposer` | `BUMP_PROPOSER_STATE_DIR` (the launcher sets it) | `sds/bump-proposer` | `alerts.json` | Alerts the Mac fired never resolve. |
  | `estate-landing` | `LANDER_STATE_DIR` | `sds/estate-landing` | `held-since.json`, `alerts.json` | Alerts never resolve, and every held pull request's 24-hour clock restarts, so its held alert fires a day late. |
  | `execution-watch` | `EXECUTION_WATCH_STATE_DIR` | `sds/execution-watch` | `alerts.json`, `first-seen.json`, `known.json` | Every `--submit` pass refuses without `known.json` (exit 1, a `/fail`), and `--seed` would re-baseline. Alerts never resolve, and stuck-unit clocks restart. |
  | `inert-landing` | `LANDER_STATE_DIR` | `sds/inert-landing` | `held-since.json`, `alerts.json`, `revert-alerts.json` | As estate landing, and revert alerts never resolve. |
  | `pin-watcher` | `PIN_WATCHER_STATE_DIR` | `sds/pin-watcher` | `alerts.json` | Alerts the Mac fired never resolve. |
  | `revision-watcher` | `REVISION_WATCHER_STATE_DIR` | `sds/revision-watcher` | `alerts.json` | Alerts the Mac fired never resolve. |
  | `rotation-decomposer` | `ROTATION_DECOMPOSER_STATE_DIR` | `sds/rotation-decomposer` | `alerts.json`, `approval-since.json` | Alerts never resolve, and approval-wait clocks restart. |
  | `rotation-proposer` | `ROTATION_PROPOSER_STATE_DIR` | `sds/rotation-proposer` | `alerts.json`, `approval-since.json` | Alerts never resolve, and approval-wait clocks restart. |
  | `unit-verifier` | `VERIFIER_STATE_DIR` | `sds/verifier` | `alerts.json` | Alerts the Mac fired never resolve. |
  | `work-carrier` | `WORK_WATCHER_STATE_DIR` (the launcher sets it) | `sds/work-watcher` | `alerts.json` | Alerts the Mac fired never resolve. |

  "Never resolve" is `KeyedAlerts.sync` (`src/alert_hub_client/__init__.py`): it sends `resolved`
  only for keys its state file remembers, so an empty file orphans every alert the Mac left
  firing. The other six moving lanes (change proposer, deploy watcher, fork hardening, fork-sync
  watch, landing ledger and membership check) keep no state beyond the rebuildable caches of the
  preceding bullet. Each of the eleven gets one named volume in the tracked compose file,
  `sds-lanes-<lane>-state`, mounted at `/var/lib/sds/<lane>` in its own service alone, and that
  service's compose entry sets the lane's variable to that path. The name Docker gives the volume
  depends on how Coolify renders compose volumes (verify before increment 1), so `lanes-finish`
  checks each mount's source against the name recorded for it (preceding bullets), never against
  the tracked name. The lane image creates `/var/lib/sds/<lane>` owned by `orchestrator`, the user
  every lane process runs as; whether a new volume takes that directory's owner is also verified
  before increment 1. So no lane starts empty: the
  cut-over copies the Mac's files into the volume (cut-over step 2), and a rollback copies them
  back. A volume outlives every redeploy, so the state survives a tag update as it survives a
  launchd restart today.
- **The bump driver's overlap protection holds in the volume.** `bump-driver` takes an exclusive
  `flock` on `pass.lock` in its state directory and does nothing if another pass holds it
  (`src/bump_driver/cli.py`). In the container that file is on the lane's volume, a directory on
  the VPS's own filesystem, and `flock` is the host kernel's lock. So it excludes a scheduled
  pass and a `lane-run` exec in the same container, and an old and a new container that share the
  volume across a redeploy. A stopped container's process releases the lock as it exits, so no
  stale lock outlives a redeploy. As on the Mac, the lock never spans hosts: the cut-over and
  rollback order (section 6) keep the Mac and the container from running the lane at once.
- **`lane-run` passes the launcher a fixed, tracked list of variables, and one secret.** Every
  container run of a launcher goes through `lane-run`, the scheduler's and a one-off exec alike.
  So the scheduler must run as `orchestrator`, not root, and must start `lane-run` with the
  container's environment, from which `lane-run` copies the token and the listed variables.
  Debian's `cron` does neither: it runs as root and starts jobs with a minimal environment.
  Increment 1 picks a scheduler that meets both requirements.
  It starts the launcher with exactly these, and no other:
  - `PATH`, set by `lane-run` to the image's directories, and `HOME`, a new empty directory per
    run, so `bws` holds no cached session (workspace invariant #23);
  - `BWS_ACCESS_TOKEN`, copied from the service's environment: the run's only secret;
  - `TZ`, for log timestamps (estate landing's window is `ZoneInfo("America/New_York")` in
    `estate_lander/cli.py`, so it doesn't depend on it);
  - `SDS_DEADMAN_PING_URL_UUID` (decision 3), a BWS UUID and not a secret;
  - the lane's state variable from the preceding table, each of the ten names passed only when
    the service's environment sets it.

  `LANE_SCHEDULE_ENABLED` isn't passed: the entrypoint reads it at container start, and no
  launcher reads it. The list lives in `lane-run`'s source. An increment that gives a launcher a
  new non-secret variable adds it to the list in the same change. No secret is ever added: a
  launcher reads every secret from BWS through the token.
- **A lane's kill switch stays the variable it is today** (ADR-0046). The four switches
  (`ORCHESTRATOR_DISPATCH_ENABLED`, `ORCHESTRATOR_ESTATE_LANDING_ENABLED`,
  `ORCHESTRATOR_INERT_LANDING_ENABLED` and `ORCHESTRATOR_BUMP_MERGE_ENABLED`) are the orchestrator's
  variables, not the lanes', so setting one to `false` stops one lane's acting through the
  orchestrator and leaves `sds-lanes` untouched. Setting one isn't free: it's an orchestrator
  environment change and restart, which must not happen while a dispatched run is live. They
  cover only what acts through the orchestrator: dispatch (the bump driver), estate landing,
  inert landing and the bump merge (the bump driver). The proposers, the work carrier, fork
  hardening and the rotation decomposer, which proposes decompositions to the orchestrator as
  SYSTEM, have no switch, and the observe-only lanes change no record. The switches are the
  only per-lane stop that needs no `sds-lanes` redeploy. No Coolify API call stops one service of
  `sds-lanes`, so any stop through Coolify, like any redeploy, is resource-wide and follows the
  quiet-slot rule.
- **Each service's schedule is switched by one variable, `LANE_SCHEDULE_ENABLED`, in that
  service's environment alone.** The lane image's entrypoint reads it once, at container start.
  When it's `true`, the entrypoint installs the lane's tracked crontab line and starts the
  scheduler. When it's anything else, or absent, the entrypoint starts the scheduler with an empty
  table, so the container stays running, runs no pass on its own, and can still be entered for a
  one-off run (cut-over step 2). A service first starts with it `false` (the following bullet).
  Changing it is a Coolify variable change plus a redeploy of `sds-lanes`, because the entrypoint
  reads it only at start. That redeploy is resource-wide, so it follows the quiet-slot rule: the
  session runs the lane phases with `--schedule <lane>=<value>`, with the same two reads and the
  same 6-hour maximum as a tag update. A schedule change therefore takes effect in the next quiet
  slot, at most 6 hours after it's asked for when the wait finds one, and when HQ acts if the wait
  times out.
- **A per-service value is a distinctly named resource variable, mapped in the compose file.**
  Coolify holds one variable set per compose resource, not one per service. So each service's
  `BWS_ACCESS_TOKEN` and `LANE_SCHEDULE_ENABLED` are resource variables named for the lane
  (`<LANE>_BWS_ACCESS_TOKEN` and `<LANE>_LANE_SCHEDULE_ENABLED`, for example
  `EXECUTION_WATCH_BWS_ACCESS_TOKEN`), and the tracked compose file maps each to the plain name in
  that service's `environment` entry and in no other service's. The image tag is one more resource
  variable, `SDS_LANES_IMAGE_TAG`, which every service's `image:` names, so a tag update writes that
  one variable beside the whole compose (preceding bullets). Verify before increment 1 that Coolify
  substitutes a resource variable in a compose `image:` field, and that a resource variable reaches
  only the containers whose compose entry maps it. If Coolify injects every resource variable into
  every service's container, every lane container would hold every lane's token, which defeats one
  identity per lane, and increment 1 stops for a decision.
- **Adding a lane's service is itself a resource-wide redeploy.** A new service is a compose change,
  and Coolify applies a compose change only by redeploying the whole resource, which stops every
  moved lane's running pass. So a service is added only by the lane phases with `--add <lane>`,
  under the same quiet-slot rule, the same two reads and the same 6-hour maximum as a tag update.
  The session writes the compose that adds the service only after `lanes-recheck` exits 0, as the
  quiet-slot bullet says. Before that, the lane's two resource variables are set: the session
  writes its schedule variable, `false`, and the VM rotation executor writes its BWS token from the
  executor's inbox (Q8, decided). They're inert
  until then, because no compose entry maps them. The token is a secret, so it never travels as an
  infraops tool argument, which the transcript records. The command can't read Coolify's variable
  listing, and the session must not either, because that listing carries every variable's value
  (`value`, `real_value`), every lane's token included. Nor may the session make any other read
  that can return a variable's real value: `coolify_get_service` on `sds-lanes`, whose response may
  carry a variable's real value, and any read like it. The session's own writes return a response
  too. infraops redacts every tool's response by default (`src/utils/register-sanitized.ts`), and
  that redaction is best-effort (workspace invariant #24), so what `coolify_update_service` and
  `coolify_update_service_env` return for `sds-lanes` is verified before increment 1.
  Its reads of `sds-lanes` are the container reads the lane phases name. So
  the guarantee that a lane's
  container never starts scheduled is built, not read:
  - The added service's compose entry maps its schedule as
    `LANE_SCHEDULE_ENABLED: ${<LANE>_LANE_SCHEDULE_ENABLED:-false}`, so an unset variable starts it
    unscheduled, and `lanes-prepare` refuses an `--add` whose tracked entry maps it any other way.
  - The session passes the value it wrote, `--schedule-written false`, and `lanes-prepare`
    refuses any other.
  - `lanes-finish` takes the new container's `LANE_SCHEDULE_ENABLED` and whether its
    `BWS_ACCESS_TOKEN` is non-empty (the session runs `test -n` in the container and prints no
    value). It refuses, and names the inverse write, the recorded compose, which removes the
    service, unless the container is unscheduled and holds a token.

  A container that starts without its token runs no pass, because it's unscheduled, and the
  identity proof (cut-over step 1) fails on it.

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
- **The token reaches the container as one environment variable, `BWS_ACCESS_TOKEN`**, in that
  service's environment alone, mapped from the lane's `<LANE>_BWS_ACCESS_TOKEN` resource variable
  (decision 1; verify before increment 1). A lane's launcher reads every secret it needs from BWS
  by UUID at the start of each pass, as it does now, through `scripts/sds-bws.sh`. The token is
  never in the image or the repository, and no value passes through a process argument.
- **Each identity is proven before its lane cuts over, by direct reads of every secret.** A dry
  run isn't the proof, because the acting lanes fetch some secrets only when they act: the change
  proposer's propose token only when submitting (`run-change-proposer.sh`), the bump proposer's
  likewise (`run-bump-proposer.sh`), and the work carrier's alert and propose tokens only when
  registering (`run-work-carrier.sh`). So the proof runs `lane-run --read <uuid>...` in the lane's
  container (cut-over step 1). That mode starts no launcher. Under the same start as a pass
  (`lane-run`'s list, with `BWS_ACCESS_TOKEN` the only secret and a new empty `HOME`, because
  `bws` caches sessions per token id, workspace invariant #23), it runs one
  `bws secret get <uuid> --color no` per UUID, discards each value, and prints only `ok` or
  `refused` beside each UUID. The UUIDs are every secret `.bws-secrets.toml` lists for the lane:
  each must read `ok`. The manifest is flat today (one `[[secret]]` per UUID, no reader field), so
  increment 2 gives each entry the lane names that read it, after checking the security
  scanner's manifest schema admits the field. The same mode shows the token is narrow, not only
  sufficient: a UUID the lane doesn't read must be `refused`. It names another lane's secret
  UUID from `.bws-secrets.toml`, or, for the first lane, a secret only the broad account reads.
  And a deliberately revoked token reads nothing. A `--dry-run` of the launcher follows, to show
  the launcher's own path works in the container.
- **Every GitHub credential moves to BWS.** No moved lane reads `gh auth token` or Devon's stored
  `gh` login. A lane reaches GitHub in three ways, and each has one defined path:
  - **HTTP API reads.** The *API-variable set* is the twelve moving lanes that hold their own
    GitHub variable, which their Python client reads: bump driver, bump proposer, change
    proposer, deploy watcher, estate landing, inert landing, landing ledger, membership check,
    pin watcher, revision watcher, unit verifier and work carrier. The variable is
    `<LANE>_GITHUB_TOKEN` (`BUMP_DRIVER_GITHUB_TOKEN`, `DEPLOY_WATCHER_GITHUB_TOKEN` and the rest),
    with two other spellings: `SDS_MEMBERS_GITHUB_TOKEN` for the membership check and
    `ROTATION_VERIFIER_GITHUB_TOKEN` for the unit verifier. Each launcher feeds that variable,
    instead of its `gh auth token` fallback, with a read token it mints each pass from the lanes'
    read-only App (Q6 and Q9, decided).
  - **`gh` commands.** The fork-sync watch has no such variable: it reads GitHub through `gh api`
    on the stored login (`src/fork_sync/gh.py`). Its launcher mints its read token the same way
    and exports it as `GH_TOKEN`, which `gh` reads in place of a stored login.
  - **git: clones, fetches and pushes.** Three lanes push through git's credential: fork
    hardening and the rotation proposer, which have no GitHub variable, and the bump proposer,
    whose variable serves only its API reads. Fork hardening reaches the forks through
    `gh auth git-credential` on Devon's login (`run-fork-harden.sh`, `fork_harden/cli.py`). The
    two proposers publish with `PUBLISH_COMMAND = "git push origin main"`
    (`bump_proposer/standing.py`), on whatever credential the packages checkout's git finds.
    Decision 1's per-pass clones need a git credential too, whether or not the repository is
    private.
- **git gets its credential from the environment, as the bump driver's mirrors already do.** A
  new launcher helper, `scripts/sds-git.sh`, defines `sds_git_credential`. It takes the lane's
  token from a named variable and exports git's environment configuration:
  `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`, and
  `GIT_CONFIG_COUNT` with `GIT_CONFIG_KEY_<n>` and `GIT_CONFIG_VALUE_<n>` pairs. One pair sets
  `http.https://github.com/.extraheader` to `AUTHORIZATION: basic` and the base64 of
  `x-access-token:<token>`. That's the shape `Mirrors.environment` in `src/bump_driver/mirror.py`
  builds for the bump driver's own git commands. The helper computes it with shell builtins and
  stdin, never a process argument. Every remote stays a bare `https://github.com/` URL. So the
  token is never in argv and never in any `.git/config`, as this decision promises, and
  git sends it only to `github.com`. A git process holds the token only in its environment,
  which every process of the pass already shares through the lane's own variable. For the two
  proposers, two more pairs set `user.name` and `user.email` to the "SDS lanes" App's `[bot]`
  identity. The extraheader authenticates the push but doesn't author the commit, and
  `GIT_CONFIG_GLOBAL=/dev/null` removes the global identity their commits take today. Without
  these pairs, Q1's "its commits are the App's" would be false. Fork hardening needs neither: it
  passes its own `GIT_AUTHOR_*` identity, `fork-harden`, to `commit-tree`
  (`fork_harden/harden.py`, `IDENTITY`). The launcher calls the helper once,
  before its first clone. Checked against each lane:
  - **Bump proposer and rotation proposer.** `standing._run` passes no `env`, so the
    `git push origin main` in `standing.publish` inherits the helper's variables, and its commit
    takes the App's identity. The modules don't change. Both launchers change in increment 4:
    they mint the "SDS lanes" App's installation token and call the helper with it.
  - **Fork hardening.** `fork_harden/git.py` runs git with `os.environ` plus two variables of its
    own (`GIT_NO_REPLACE_OBJECTS` and `GIT_TERMINAL_PROMPT`), so it inherits the helper's variables
    for fetch and push. In increment 5 its launcher mints the "SDS lanes" App token and calls the
    helper, and the "operator's own git credentials" text in `run-fork-harden.sh`'s header and
    `fork_harden/cli.py`'s docstring is rewritten. Its code doesn't change.
  - **Bump driver.** `Mirrors` builds its own git environment from `BUMP_DRIVER_GITHUB_TOKEN`,
    and its generator's environment deliberately holds no token. It doesn't change. Its
    launcher's decision 1 clones call the helper with the same token.
  - **Work carrier.** Its launcher's decision 1 clones call the helper with the lane's own GitHub
    variable, in increment 4.
  - **Change proposer.** It clones nothing (`scripts/run-change-proposer.sh`,
    `src/change_proposer/`), so it doesn't call the helper. Its GitHub variable serves only its API
    reads.
  - **Rotation decomposer and rotation proposer mirrors.** Both read the credential registries
    through `RepoMirror` (`rotation_decomposer/cli.py`, `rotation_proposer/cli.py`), whose git
    environment is a fixed allowlist: `PATH`, `HOME` and three git settings
    (`rotation_worker/git_mirror.py`, `_run`). The helper's variables never reach it, so those
    fetches carry no credential. That's fine only while every repository they mirror is public, as
    the four registry repositories are (decision 5). A private one needs `RepoMirror` changed, by
    its own decision.
  - **Lanes that don't clone** don't call the helper.
- **Every moved lane's launcher changes in that lane's increment; a module changes only where an
  increment names it.** Each launcher replaces its Keychain read with the container's
  `BWS_ACCESS_TOKEN`, its `gh auth token` fallback with a BWS read, and, if it clones, adds
  decision 1's clone and the helper call. Five moving launchers (estate landing, inert landing,
  pin watcher, revision watcher and unit verifier) reach the Keychain through
  `scripts/sds-token.sh`, so that read changes with them. Increment 2 changes
  `scripts/sds-deadman.sh` (decision 3). Each launcher keeps its Mac path working until
  increment 6, so a rollback needs no new credential: the Mac agent runs its launcher on the
  Keychain path, and the container runs the same launcher on the container path.
- **A read-only lane mints a downscoped read token from the lanes' read-only App each pass (Q6 and
  Q9, decided).** The read-only App is a second GitHub App, beside the "SDS lanes" App, with read
  permissions only; its GitHub name is set when Devon creates it. The *read-only set* is the twelve
  lanes whose GitHub credential only reads: bump driver, change proposer, deploy watcher, estate
  landing, fork-sync watch, inert landing, landing ledger, membership check, pin watcher, revision
  watcher, unit verifier and work carrier. It's the API-variable set with the fork-sync watch (which
  reads through `gh`) in place of the bump proposer (which also pushes). Each pass's mint request
  names only that lane's repositories and only the read permissions its GitHub calls use, so each
  lane's token is narrow although the App is shared. No fine-grained token exists. The bump
  proposer's `BUMP_PROPOSER_GITHUB_TOKEN` feeds `GitHubReader` (`bump_proposer/cli.py`,
  `read_pending_updates`) over the members' repositories, which only reads, so it's fed the same
  way, by a second, read-only mint beside its push token. Both landers read through `GitHubReader`
  and land through the orchestrator's dispatch App, not with this token. The repositories these
  lanes read through Devon's login today are the members' repositories, the member record's
  repository, intent-packages, the standards repositories the lanes clone, and, for the fork-sync
  watch, factory-runner (whose `.cred-consumers.toml` lists the forks, `fork_sync/onboarding.py`
  `REGISTRY_REPO`) and each fork it lists. Devon decided where the read-only App is installed: on
  the members' repositories, the member record's repository and the standards repositories (Q6),
  and on factory-runner and each fork (Q9). The read lanes also read intent-packages, and no
  decision covers installing the read-only App there: that install is pending Devon, as a new HQ
  decision, and it blocks increment 3, as D-14 did. Until he decides, no read lane reads
  intent-packages with the write-capable "SDS lanes" key. The "SDS lanes" App stays installed only
  on intent-packages and the forks, as Q1 decided, so no read lane holds a key that can write.
  Increment 3, which moves the first lane that reads GitHub (the landing ledger), lists the exact
  set from the member record, the lanes' clone lists and that registry, and Devon installs the
  read-only App on those alone. Verify before increment 3 whether factory-runner, which is public,
  needs the install at all (Q9). The fork-sync watch also reads each fork's
  upstream's public releases (`fork_sync/watch.py`); verify before increment 3 that an installation
  token reads an upstream's public releases without the read-only App installed on the upstream, or
  that increment decides. The read permissions the mints draw on are Metadata, Contents (files,
  commits, compares and clones), Pull requests, Issues (the fork-sync watch's read of each fork's
  open review alert), Actions (workflow runs and jobs) and Administration (the unit verifier's read
  of `main`'s `required_status_checks`, in `unit_verifier/required_checks.py`), so the read-only App
  holds each of them as read, and no write permission. Verify before increment 3 that an
  installation-token mint narrowed by repository and permission returns a token that refuses
  everything outside the request. A lane that needs more than its read mint gets it by its own
  decision.
- **The three pushing lanes push as the "SDS lanes" GitHub App (Q1, decided).** `bump-proposer`,
  `rotation-proposer` and `fork-harden` push with an installation token of a new GitHub App, with
  `contents: write`, minted naming only intent-packages or the forks, the only repositories it's
  installed on. The push token reaches git only through `sds_git_credential` (the preceding
  bullets); none of the three feeds it to an HTTP API client. The bump proposer's own
  `BUMP_PROPOSER_GITHUB_TOKEN` comes from its separate read mint (preceding bullet). Each pass mints
  its tokens from an App's private key, which lives in BWS and is read by UUID, with a direct read
  grant to each lane identity that mints from that App, and to no other. The "SDS lanes" App's key
  is read by the three pushing lanes. The read-only App's key is read by the twelve of the read-only
  set and by the bump proposer, for its read mint, thirteen in all (Q9). So the bump proposer reads
  both keys, and fifteen lane identities read one or both. The tokens are short-lived and never
  stored. Both keys are created in increment 3, which moves the first lane that mints, by Devon,
  because only an App's settings page generates a GitHub App private key (GitHub Docs, "Managing
  private keys for GitHub Apps"). The bump driver needs no GitHub write scope: its token only reads
  pull request state and makes shallow clones (`src/bump_driver/client.py`,
  `src/bump_driver/mirror.py`), and it dispatches and merges through the orchestrator as SYSTEM. Its
  variable is fed from its read mint.
- **`.bws-secrets.toml` and `.cred-consumers.toml` gain each lane identity and each secret it
  reads**, in the same change as the lane's move. The credential registry gains each lane token and
  each lane's Healthchecks ping URL (decision 3) as a credential, and each App's private key, the
  "SDS lanes" App's and the read-only App's, as two more. Once ADR-0059 increment 7 moves the VM
  rotation executor to the VPS, it carries out each lane token's rotation between Devon's mint and
  his revoke (Q3, decided). Both App keys join ADR-0055's hand-rotated set, as the
  `alobar-sds-rotation` App's key did (Q4 and Q9, decided): Devon rotates each on its App's settings
  page, and no executor handles either. Each is registered as class `github-app-private-key`, whose
  interval in infraops's rotation policy (`CLASS_POLICY` in `src/security-drift/cred-rotation.ts`)
  is 365 days, so both keys have the same interval, and Devon rotates both in the same sitting (Q9).
  The minted tokens are short-lived and never stored, so none is registered.
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
- **The narrowest credential is the check's UUID ping URL, stored in BWS per lane.** A UUID ping URL
  identifies one check, and a request to it is a ping and nothing else: success, `/start`, `/fail`,
  `/log` or an exit status (Healthchecks Pinging API docs, "Endpoints" and the "Using UUID"
  sections). It can't list, read, change, pause or delete any check. A ping key with a slug is
  rejected: it's the project's key, so it pings any check in the project by slug, and with
  `create=1` it creates checks (Pinging API docs, "UUIDs and Slugs"). Each lane's ping URL is one
  BWS secret with a direct read grant to that lane's identity alone, read through the lane's own
  `BWS_ACCESS_TOKEN` like its other secrets. The per-lane ping-URL secret and its per-lane read
  grant are this ADR's proposal, created by the increment that moves each lane. The name lookup
  moves to install time: in the increment that moves a lane, Devon or an interactive session on the
  Mac runs `sds_upsert_check` through the broad account, as interactive installers do today, to
  create or update the check. HQ doesn't run it: HQ's identity is its own BWS token
  (`BWS_ACCESS_TOKEN_SDS_HQ`), and no decision grants HQ the broad account. The URL reaches its BWS
  secret as a lane token does (Q8, decided): Devon copies it from the check's page and pastes it
  into the VM executor's inbox by hand, and the executor writes the lane's ping-URL secret. That
  paste is Devon's step in each lane's cut-over (step 1), one URL per moved lane, one at a time,
  from increment 2 on; no machine path carries a ping URL (Q10, decided). No session stores it,
  because `bws secret create` and `bws secret edit` take the value as an argument. Verify before
  increment 1, on the self-hosted version: that a UUID ping URL needs no other credential and does
  nothing beyond pinging, and the URL's base (`PING_ENDPOINT`).
- **What changes in `sds-deadman.sh`, in increment 2** (the first lane whose container pings).
  `sds_deadman_resolve` gains a first path: when `SDS_DEADMAN_PING_URL_UUID` is set, it reads the
  URL with `sds_bws_value` and the lane's own token, and lists no check. `SDS_DEADMAN_PING_URL_UUID`
  is a service variable tracked in the `sds-lanes` compose file, and `lane-run` passes it to the
  launcher (decision 1). Its value is the lane's ping-URL secret UUID from `.bws-secrets.toml`,
  not a secret. The name-lookup path through the broad account
  stays for the lanes still on the Mac, and increment 6 removes it with their plumbing. The helper
  still reports and never gates: a URL it can't read logs one reason and disables that run's pings.
  `sds_deadman_ping` moves its URL to curl's stdin config, as `sds_outcome_ping` already does,
  because the URL is a secret and must not be a process argument. `install-fork-harden-launchd.sh`'s
  private upsert is replaced by `sds_upsert_check` when fork hardening moves.
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
  (`sweep` and `bind`) read `~/Projects/<member>`. It changes no member's repository or
  deployment. Its `bind` pass does write orchestrator records about members, as SYSTEM through
  `activation_sweep/binding_client.py`: a release-artifact binding for a completed unit whose
  landing this machine has pulled, and the deployment observation that follows it. Each record
  states what this machine's working copy holds, so it can be written only here. Devon decided it
  stays (Q2). It remains ADR-0030's only control over ADR-0031.

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
| `execution-watch` | Moves | Reads execution status and raises alerts. Its alert memory, first sightings and `known.json` move to its volume. |
| `fork-harden` | Moves | Pushes forks' `hardened` branches, as the "SDS lanes" App (Q1). Its clone cache is rebuildable. |
| `fork-sync-watch` | Moves | Reads fork sync state through APIs only, with its minted read token as `GH_TOKEN` (Q6). |
| `inert-landing` | Moves | Lands update-bot pull requests, acting on members. |
| `landing-ledger` | Moves | Files observations from GitHub reads, with no local checkouts. |
| `pin-watcher` | Moves | Reads callers' runner pins through APIs only. |
| `revision-watcher` | Moves | Reads Coolify and App Brain through APIs only. |
| `rotation-decomposer` | Moves | Proposes decompositions as SYSTEM. Its alert memory and approval-wait clocks move to its volume. |
| `rotation-proposer` | Moves | Pushes package revisions to intent-packages `main`, as the "SDS lanes" App (Q1). Runs infraops's `cred-findings` from a clone. |
| `rotation-worker` | Stays | The Keychain rotation instance: its store is this machine's Keychain. |
| `sds-membership-check` | Moves | Compares the member record with declarations through APIs only. |
| `tool-installer` | Stays | Installs tools on this machine, so its subject is the Mac. |
| `unit-verifier` | Moves | Runs `/verify` as VERIFIER. Its alert memory moves to its volume. |
| `work-carrier` | Moves | Registers intakes from package clones. Its alert memory moves to its volume; its `portfolio.json` input is decided in increment 4 (following paragraphs). |

No agent retires under this ADR. Seventeen move and three stay. The eleven moving lanes that keep
state keep it in their own volumes (decision 1, "Lane state").

The rotation proposer's `cred-findings` (infraops's `security-drift-cli.js`, tracked in its
`dist/`) reads two Mac files in `~/.config/infra-drift/`. `cred-consumers.list` is hand-maintained
and names four registries by absolute path: the `.cred-consumers.toml` of the orchestrator,
change-manager, factory-runner and infraops-mcp-server working copies. Those four repositories are
public, so the lane can clone them at `main`. `cred-rotation-state.json` is mutable state, written
by infraops's `record-rotation` and `resolve-exposure` subcommands, and it lives only on the Mac.
The rotation proposer's increment must carry the list as data in a clone and decide where the
state file lives. A file that exists only on the Mac would keep the lane tied to it.

The work carrier has a Mac input of the same kind. Its workability judgment reads the portfolio
scan's `~/.portfolio/portfolio.json` (`work_carrier/workability.py`, `DEFAULT_PORTFOLIO`, or
`WORK_CARRIER_PORTFOLIO`), which the Mac's portfolio scan writes. In a container the file is
absent (and a one-time copy goes stale past `PORTFOLIO_MAX_AGE_HOURS`), the `capable` term answers
UNKNOWN, and an UNKNOWN never lets a record be carried, so every record would be held. The work
carrier's increment must decide where the scan's output comes from before the lane moves; if it's a
file, `WORK_CARRIER_PORTFOLIO` joins `lane-run`'s list.

#### Agents outside this repository

These launch agents run on the Mac, and other repositories own them. Devon decided their scope
on 2026-11-06 (HQ D-9): change-window and infra-drift move off the Mac as increments 8 and 9, and
factory-events and SDS HQ itself are outside this phase. None is a Mac exception: the exceptions
are decision 4's three.

| Launch agent | Owner | What it does | Acts on a member | Disposition |
|---|---|---|---|---|
| `change-window` | infraops-mcp-server (`scripts/change-window.sh`, `scripts/com.devon.change-window.plist.template`) | Daily at 04:00, runs change-manager's `run-window` CLI from infraops's working copy, which executes approved change requests with the prod and dev Coolify tokens in its environment, and sends the digest to alert-hub. The Keychain BWS identity it shares with infra-drift (`BWS_ACCESS_TOKEN_INFRA_DRIFT`) reads both Coolify tokens, the Anthropic key and the change-manager bearer. | Yes: it executes approved changes. | Moves, increment 8 (D-9). |
| `infra-drift` | infraops-mcp-server (`scripts/drift-audit.sh`, `scripts/com.devon.infra-drift.plist.template`) | Daily at 03:00, the drift audit (ADR-0055's drift reporter): audits prod and dev Coolify, auto-applies safe remediations, syncs App Brain, pushes escalations to change-manager, posts observations as the drift reporter, mints due follow-up units as SYSTEM (read through `sds-operator`), and runs the Mac's security scan with its guarded `chmod` fixes. Its credential scan reads the same `cred-consumers.list` and rotation state as the rotation proposer. It doesn't write the list. | Yes: remediations, change records and minted units. | Moves, increment 9 (D-9). |
| `factory-events` | security-standards (`scripts/factory-events-nightly.sh`, deployed to `~/.claude/bin/`) | Daily at 03:30, adapts events from this Mac's high-power audit log and from change-manager into the factory events chain (`FACTORY_DB_DSN`), verifies the chain and its anchor, ships it, and reports one `chain:factory-events` observation. It reads the change-manager bearer through the broad Keychain identity. | No member: it writes only the events chain and one observation. One input is this Mac's audit log. | Outside this phase (D-9). |
| `sds-hq` | sds-hq (`launchd/`) | Every 15 minutes, and when `state/trigger` changes, runs one SDS HQ cycle (`sds-hq cycle`, the planning agent): headless Claude Code sessions that dispatch workers into git worktrees of member repositories, which push branches and open pull requests. | Yes, through its workers. | Outside this phase (D-9). |
| `sds-hq-inbox` | sds-hq (`launchd/`) | Every 60 seconds, runs `sqlite3 -readonly` over this Mac's Messages database and writes the latest messages of two chats to `state/imessage-inbox.json`. | No. It reads a local database read-only and writes one local file, so it acts on nothing; its subject is this Mac's Messages database. | Outside the phase bar, which covers lanes that act on a member. Not an exception. |

### 6. Migration order, cut-over, rollback and the week bar

#### Verify before increment 1

Increment 1 confirms each of these on the production Coolify, Docker and self-hosted
Healthchecks before it relies on it. A fact it can't confirm stops the increment for a decision:

- Coolify substitutes a resource variable in a compose `image:` field, and a resource variable
  reaches only the containers whose compose entry maps it (decision 1).
- How Coolify names a compose file's volumes, and whether it honors a volume's `name:` or
  `external` setting (decision 1, "Lane state").
- Whether a new named volume takes the owner of the lane image's `/var/lib/sds/<lane>`
  directory (decision 1, "Lane state").
- The Healthchecks check listing returns `started`, set to `true` from a `/start` ping until the
  pass's result ping (decision 1).
- A UUID ping URL needs no other credential and does nothing beyond pinging, and the URL's base
  (`PING_ENDPOINT`) (decision 3).
- `coolify_update_service` with `docker_compose_raw` replaces the whole stored compose of a
  `docker-compose` service, the next `coolify_deploy` runs that compose, and the write doesn't
  take the `parse()` path that can double-prefix a volume (workspace invariant #4). Also what
  that write's and `coolify_update_service_env`'s responses carry for `sds-lanes` (decision 1).
  If the compose can't be replaced this way, no infraops tool replaces it, and increment 1 stops
  for a decision.
- `coolify_create_service` creates `sds-lanes`: infraops sends its `docker_compose_raw` as given,
  while `coolify_update_service` base64-encodes it, so which form Coolify's create endpoint
  accepts is verified. No lane phase creates `sds-lanes`; increment 1 decides who runs the create
  and how the first compose is recorded as the one the first lane phase run replaces.
- Where the last finished run's state file lives, so that the next run, from any session, reads
  the compose Coolify was last given (decision 1).
- How Coolify treats a service container whose `HEALTHCHECK` reports unhealthy, and what the lane
  image sets in place of the inherited one (decision 1).
- `pushed_digest()` in `scripts/deploy_orchestrator.py` refuses a run whose log holds more than
  one distinct `DIGEST:` line, so the lane image's digest must be logged on a line its pattern
  doesn't match and read by a parser change in the same increment (decision 1).
- An orchestrator rollback runs the lane phases with `--tag` at the rollback target, whose tracked
  compose lacks every service a later `--add` added. Written whole, that compose removes each lane
  cut over since, and the lane then runs nowhere, because its Mac agent is booted out. Increment 1
  decides how a rollback past an `--add` keeps each cut-over lane running (decision 1).

#### Increments

1. **The lane image and `sds-lanes`, with no lane.** Build the lane image in `Release image` and
   create the compose resource, after confirming "Verify before increment 1", with one placeholder
   service. The placeholder runs the lane image's entrypoint with no `LANE_SCHEDULE_ENABLED`, so its
   scheduler has an empty table and it runs no launcher, holds no token and maps no resource
   variable but `SDS_LANES_IMAGE_TAG`. It mounts one named volume, on which increment 1 verifies
   volume naming and ownership. Add the deploy command's three
   lane phases (`lanes-prepare`, `lanes-recheck`, `lanes-finish`) for a tag update, a schedule
   change and an added service: each waits for a quiet slot, writes the whole compose and records
   it, prints the session's exact infraops calls and their inverse, and verifies the session's reads
   of the redeployed containers (image digest, schedule and state volume), and an `--add` refuses a
   compose entry whose schedule doesn't default to `false` (decision 1). Add `lane-run` with its
   variable list and its `--read` mode (decisions 1 and 2). Give `sds-operator` its direct read
   grant on the read-only Healthchecks key (Q5), and add `f07b1368-…` to `.bws-secrets.toml` in the
   same change: the BWS secret-handling standard has a repository's manifest name every UUID it
   consumes, and the security scanner reports manifest drift.
2. **One observe-only lane first: `execution-watch`.** It has one identity, three secrets (the
   observer bearer, the alert token and its ping-URL secret), no GitHub credential, and state that
   must carry over (decision 1, "Lane state"), so it exercises the state copy first. Its interval
   (15 minutes) is the shortest of the lanes with no GitHub credential, so its check shows a failure
   fastest of the lanes that can move before any GitHub read credential exists. The unit verifier's
   10-minute interval is shorter, but it reads GitHub, so it waits for increment 3. It proves the
   identity, `lane-run`'s fixed start (decision 1), the dead-man ping and the cut-over procedure. It
   also writes the ADR-0055 amendment that lifts decision 4 for lane tokens and ping URLs, so the VM
   executor writes each from its inbox (Q8 for lane tokens, Q10 for ping URLs). As built, the inbox
   won't bind a first lane token or a ping URL: `bind` in `rotation_worker/inbox.py` binds a value
   only to a rotation that waits on a person's mint, and only when `same_identity` finds it for the
   same account as an old value that still answers, and neither has an old value. So the amendment
   extends the executor, and increment 2 waits on it. Verify before increment 2, in
   `rotation_worker` and on the VM instance:
   - The executor's Coolify client only `PATCH`es `/services/{uuid}/envs` and never `POST`s one
     (`rotation_worker/coolify.py`, `permits`), so a new `<LANE>_BWS_ACCESS_TOKEN` variable must
     exist before the executor writes it. No actor creates it yet.
   - The one adapter that writes a Coolify service variable, `postgres_role`, requires its
     `coolify-env` destination to have `redeploy` true and then calls `restart()`
     (`adapters/postgres_role.py`), which is `POST /services/{uuid}/restart`
     (`rotation_worker/coolify.py`). On `sds-lanes` that restarts every lane and bypasses the
     quiet-slot rule (decision 1), so increment 2's destination needs `redeploy` false, and the
     extension must honor it. The BWS machine-token adapter hosts no destination
     (`adapters/bws_machine_token.py`, `hosts_destinations = False`).
   - `other_mints` refuses to bind while two rotations wait on a person's mint
     (`rotation_worker/inbox.py`), so Devon pastes a lane token and its ping URL one after the
     other, each after the previous one binds.
   - Devon's hand paste of a ping URL (Q10) binds only to a rotation that waits on a person's mint
     (`rotation_worker/inbox.py`), so something must make the executor wait on that paste. If the
     extension does that through a registered credential, note that infraops's `CLASS_POLICY`
     (`src/security-drift/cred-rotation.ts`) has no class for a ping URL, a UUID URL that can't be
     rotated, so registering one (Consequences) needs a new class.
   - Before increment 7, an early or leak rotation of a lane token (Consequences) has no scheduled
     trigger: which actor proposes the rotation unit for an unscheduled rotation.
   - For a leaked lane token, whether Devon revokes it before or after the quiet-slot redeploy that
     gives the container the new token: before leaves the lane holding a dead token until that
     redeploy, and after leaves the leaked token valid for up to the redeploy's 6-hour wait.
   - Answered: the executor has a destination that writes a Coolify service variable and one that
     writes a BWS secret. `coolify-env` takes `resource_type` (`rotation_worker/destinations.py`),
     and the client builds `/api/v1/services/{uuid}/envs` for `service`
     (`rotation_worker/coolify.py`, lines 63-65); `bws-secret` is a destination kind in
     `destinations.py` too.
3. **The other observe-only lanes:** landing ledger, pin watcher, deploy watcher, revision watcher,
   fork-sync watch, membership check and unit verifier, one at a time. Before the landing ledger
   moves, Devon creates both Apps with decision 2's permissions: the read-only App, installed on the
   repositories the read-only set reads, and the "SDS lanes" App, installed only on intent-packages
   and the forks. He stores each App's private key in BWS (Q1, Q6, Q9). Each launcher then mints its
   lane's read token from the read-only App (decision 2). The increment changes
   `scripts/run-fork-sync-watch.sh` to mint that token and export it as `GH_TOKEN`, and rewrites
   that launcher's CREDENTIALS header comments to match. It also rewrites the `gh`-login wording in
   `src/fork_sync/gh.py` (the module docstring's "operator's own `gh` login and git credentials")
   and `src/fork_sync/watch.py` (its docstring's "the operator's `gh` login"). The code of both
   modules doesn't change.
4. **The proposers and carrier:** change proposer, work carrier, the rotation decomposer, then bump
   and rotation proposers. None is observe-only and none has a switch: the decomposer proposes
   decompositions as SYSTEM. This increment adds `scripts/sds-git.sh` (decision 2) with the first
   lane that clones, and changes the three launchers that clone (work carrier, bump proposer and
   rotation proposer) to clone through it. The change proposer clones nothing, and the decomposer's
   mirror reads need no credential. The bump and rotation proposers' launchers mint the "SDS lanes"
   App's push token for git (the App exists from increment 3); `bump_proposer/standing.py` doesn't
   change. Verify before increment 4: both bridge proposers run intent-packages' lifecycle through
   the checkout's own `.venv/bin/python` (`bump_proposer/standing.py`, `_interpreter`, which
   `rotation_proposer/standing.py` reaches through `standing.lifecycle`), so each needs a venv in
   its clone, which decision 1 builds only for the bump driver.
5. **The member-acting lanes:** inert landing, then estate landing, then the bump driver, then
   fork hardening, which pushes as the "SDS lanes" App (Q1) through `scripts/sds-git.sh`; its
   launcher's header and `fork_harden/cli.py`'s docstring are rewritten, and its code doesn't
   change. Each moves outside its landing
   window. Estate landing sends its Coolify token only over the tailnet today, so its increment
   decides how the container reaches Coolify without widening that.
6. **Retire the Mac's lane plumbing:** the moved plists and their installers,
   `activate-checkout.sh`'s use by lanes, and the two Keychain items' lane role. The Keychain items
   themselves are retired only once no remaining reader names them. This increment starts only
   after the week bar has passed, because it ends the rollback path.
7. **The VM rotation executor moves to the VPS** under its own ADR-0055 amendment. This increment
   is last because it moves the root of the rotation design. A lane token the executor rotates
   reaches a container only at its next start, because the entrypoint and `lane-run` read the
   service's environment as it was then. Every start is a resource-wide `sds-lanes` redeploy
   (decision 1). Bitwarden has no machine path to create or revoke an access token
   (`rotation_worker/adapters/bws_machine_token.py`, ADR-0055 amendment 12), so Devon mints each
   new lane token and revokes the old one, and the executor does what's between, taking the token
   from its inbox as it has since increment 2 (Q8). So the amendment must order each lane-token
   rotation as follows: Devon mints the new token, the executor writes it to the lane's
   `<LANE>_BWS_ACCESS_TOKEN`, redeploys under the quiet-slot rule (the lane
   phases' two reads; the amendment decides whether the executor, which holds its own Coolify
   token, runs them or hands the redeploy to the deploying session), verify the new token in the
   lane's container (`lane-run --read` over every secret `.bws-secrets.toml` lists for the lane,
   each `ok`), and only then does Devon revoke the old token. Revoking before that redeploy
   would leave the lane's container holding a dead token until the next quiet slot.
8. **`com.devon.change-window` moves off the Mac** (D-9). infraops-mcp-server owns it:
   `scripts/change-window.sh`, `scripts/com.devon.change-window.plist.template` and
   `scripts/install-change-window-launchd.sh`. It runs change-manager's `run-window` from
   infraops's working copy, which executes approved change requests with the prod and dev Coolify
   tokens. Its identity question: it shares the Keychain BWS identity `BWS_ACCESS_TOKEN_INFRA_DRIFT`
   with infra-drift, so it needs its own, and how a container reaches the dev Coolify on the Mac's
   OrbStack VM is that increment's to decide, as estate landing's Coolify reach is increment 5's.
9. **`com.devon.infra-drift` moves off the Mac** (D-9). infraops-mcp-server owns it:
   `scripts/drift-audit.sh`, `scripts/com.devon.infra-drift.plist.template` and
   `scripts/install-drift-launchd.sh`. It is the drift audit that feeds the rotation proposer. Its
   identity questions: its own BWS identity in place of the shared one, and the SYSTEM bearer it
   reads through `sds-operator` to mint follow-up units. Its scan of this Mac's security posture
   has the Mac as its subject, so that increment decides what of it stays. It reads the same
   `~/.config/infra-drift/` list and rotation state as the rotation proposer (decision 5).

Each increment's plan records which repositories it changes and which identities it creates.

#### Cut-over for one lane

1. Create the lane's BWS identity and grants. Set the lane's two resource variables: the VM executor
   writes its token from its inbox after Devon mints it (Q8), and the session writes its schedule
   variable `false`. After the token binds, Devon copies the lane's ping URL from its check's page
   into the inbox by hand, and the executor writes the lane's ping-URL secret (Q10). Then add the
   lane's service by the lane phases with `--add` in a quiet slot (decision 1). Then prove the
   identity in the lane's container: `lane-run --read` over every secret `.bws-secrets.toml` lists
   for the lane (each `ok`), one UUID it doesn't read (`refused`), and the launcher's `--dry-run`
   (decision 2). The service runs with `LANE_SCHEDULE_ENABLED=false`, so the container runs no pass
   on its own. Every container run in steps 1 and 2 is a one-off exec: the deploying session,
   through infraops's `vps_exec`, runs `docker exec` into the running service container, and runs
   the lane image's `lane-run` command with the launcher's arguments. `lane-run` starts the launcher
   with `BWS_ACCESS_TOKEN` as its one secret, copied from the service's own environment, beside
   `PATH`, a new empty `HOME` and its fixed list of non-secret variables (decision 1), without the
   token in a process argument. So the run holds the lane's identity and no other, and the token
   never appears in the session's command or transcript. The scheduler's crontab line runs the same
   command. Then capture the lane's week-bar baseline (its flips and ping log, "The week bar"),
   before any run in step 2, because some of those runs ping the lane's real check. The ping log
   needs the Management API key, so Devon or an interactive session captures the baseline through
   the broad account, not HQ ("The week bar").
2. Pick a cut-over time between two scheduled passes. In that gap, for a lane with state (decision
   1, "Lane state"), first copy its Mac state directory into its volume: the session reads each
   file, which holds alert keys and timestamps and no secret, and writes it with infraops's
   `vps_write_file` under the volume's host mount point (`docker volume inspect` on the volume
   `lanes-finish` recorded). `vps_write_file` writes as root, so the session then sets each copied
   file's owner to `orchestrator` and its mode to the Mac file's mode. The bump driver's `pass.lock`
   and `mirrors` aren't copied. Then run the lane's reporting mode (the invocation that changes no
   record) on the Mac and in the container within the same minute. Keep both outputs for step 4. A
   reporting run touches the real check or not by lane (`scripts/sds-deadman.sh`, "A dry run must
   not count as a pass"):
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

   A ping from step 2 lands after the baseline and before the week, so it counts in neither. Then
   confirm no pass is running: `launchctl print gui/<uid>/com.devon.<lane>` shows no running PID,
   and for the bump driver no process holds its `pass.lock` (the only lane with a pass lock).
   `bootout` stops a running pass midway. Then boot out the Mac agent with
   `launchctl bootout gui/<uid>/com.devon.<lane>`, and keep its plist. Then compare the Mac's
   state files with the volume's by SHA-256, and check each volume file's owner is `orchestrator`
   and its mode is the Mac file's. If any differs, replace the volume's files with the Mac's and
   set owner and mode again: until the bootout the Mac's are the lane's state.
3. Set the service's `LANE_SCHEDULE_ENABLED` to `true` by the lane phases with `--schedule`,
   which the session redeploys in the next quiet slot (decision 1). Until that redeploy, neither
   host runs the lane on a schedule, which is the safe direction. The gap is at most 6 hours when
   `lanes-prepare` finds a slot. At its 6-hour timeout it exits 2, the variable stays `false`, and
   the gap lasts until the session acts: HQ re-runs the lane phases or redeploys by hand. If the gap
   outlasts the check's grace, the check goes down; that down event falls before the week starts,
   so it counts in neither the baseline nor the week. The container's first scheduled pass pings
   the same check.
4. Verify that the check's last ping came from the container and that the Mac agent isn't loaded.
   Compare the two reporting runs from step 2 on exactly three things: the exit code, the set of
   record identifiers the lane reported (pull request numbers, unit ids, package names, record
   ids), and, for each identifier, its finding kind or proposed action. Nothing else in the output
   is compared, since timestamps, durations, run and request ids, temporary paths, hostnames and
   log prefixes differ by nature. If any of the three differs, the cut-over has failed, and the
   lane rolls back ("Rollback for one lane"). A scheduled
   pass isn't the comparison, because the Mac's last pass may already have acted on a record that
   the container's first pass then finds done.

The Mac agent and the container never run the same lane at once. Two landers, or two carriers,
would race on the same records.

#### Rollback for one lane

Set the service's `LANE_SCHEDULE_ENABLED` to `false` by the lane phases with `--schedule`, and
wait for `lanes-finish` to pass on the session's quiet-slot redeploy. For the bump driver, also
confirm no process in the container holds `pass.lock` (`vps_exec` running `flock -n` on it in the
service's container). For a lane with state, copy its volume's files back into the Mac's state
directory with infraops's `vps_read_file`. Then `launchctl bootstrap` the kept plist. The Mac
agent is bootstrapped only after the redeploy, so the two never run the lane at once.

The wait is at most 6 hours when `lanes-prepare` finds a slot, and lasts until HQ acts when it times
out. During it the container keeps running the lane on its schedule. For that interval, the ADR-0046
switches stop what acts through the orchestrator: `ORCHESTRATOR_ESTATE_LANDING_ENABLED` or
`ORCHESTRATOR_INERT_LANDING_ENABLED` for a lander, and `ORCHESTRATOR_DISPATCH_ENABLED` and
`ORCHESTRATOR_BUMP_MERGE_ENABLED` for the bump driver. Each is an orchestrator restart, so it's set
only while no dispatched run is live. No switch covers the bump and rotation proposers, the work
carrier, fork hardening or the rotation decomposer (which proposes decompositions as SYSTEM), and
the observe-only lanes change no record. For those, a rollback that can't wait for the quiet slot is
HQ's decision to redeploy `sds-lanes` by hand at once, accepting that it stops every running pass
midway.

The Mac agent runs from the main tree with the Keychain identities it had, which stay valid until
increment 6. A rollback takes no new credential and changes no record, because each launcher keeps
its Mac path until increment 6 and the lane modules are the same in both places (decision 2).

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
  check's status changes"; verify on the self-hosted instance before increment 2, whose cut-over
  captures the first baseline). A
  lane's down baseline is the flips its check has from the 4 weeks before cut-over, or from the
  check's creation if that's more recent. A check with no flips in that span has a baseline of
  zero down events.
- **`/fail` pings** come from the check's ping log (`/api/v3/checks/<uuid>/pings/`), which returns
  only a check's most recent pings, up to the account's ping-log limit (100 for a free account in
  the Healthchecks API docs, "List check's logged pings"; verify the self-hosted instance's limit
  before increment 2). Every pass sends two pings, `/start` and its result, so at a limit of 100 a
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
decision 4, and the VM rotation executor, which increment 7 moves only after increment 6. Their
checks going down is expected. The agents outside this repository (decision 5) go quiet too; this
week measures the lanes of increments 2 to 5 only.

So this week covers the lanes of increments 2 to 5, and it can't cover the executor: increment 6
requires it passed, and increment 7 follows increment 6. The executor gets a second week of its
own, run the same way after increment 7: 7 consecutive days with the Mac off or asleep, over which
its `rotation-heartbeat:<store kind>` alert-hub schedule for the moved instance never fires
(`docs/operations/rotation-executor.md`; each finished pass sends that key as `resolved`, so the
schedule fires only when passes stop). Increment 7's ADR-0055 amendment states that bar as its
own exit. Increments 8 and 9 each run a week of the same kind for the agent they move, stated in
that increment's plan.

**The increments alone don't reach the phase's done-when.** Its first half is that no lane acting on
a member runs on the Mac. After increment 7, change-window and infra-drift still act on members from
the Mac until increments 8 and 9 move them. SDS HQ also acts on members, through its workers, and
stays on the Mac: Devon placed it outside this phase (D-9). The phase is done when increments 8 and
9 have moved those two and each week has passed. Neither the rotation proposer's
`cred-consumers.list` and rotation state nor any other moved lane's input may still live on the Mac
by then (decision 5). Every other `com.devon.*` launch agent is an observe or infra job or a
personal app that acts on no member, so it's outside the bar and stays where it is, as
`sds-hq-inbox` does. The vps-backup agents are infra jobs (their plists and scripts, read
2026-11-06): `com.devon.vps-backup` pulls database dumps and `/data/coolify` from the hosts over SSH
into the NAS and syncs the NAS to Google Drive, `com.devon.hetzner-snapshot` creates and prunes
snapshots of one Hetzner server (`SERVER_ID` in its script) through the Hetzner API, and
`com.devon.ovh-backup-check` only reads the OVH API. Each also reports one `external_run`
observation about its own run to the orchestrator, a record about the run, not about a member's
repository or deployment; the one that feeds a moved lane is `portfolio-scan`, whose output the work
carrier reads (decision 5).

The evidence is each moved check's flips and ping log for the week, its baseline as captured at
cut-over, and the Mac's power log (`pmset -g log`) covering the 7 days. The read-only Healthchecks
key reads flips. The ping log needs the Management API key (`51509d5e-…`), because `/pings/` isn't
among the endpoints the Healthchecks API docs list for a read-only key (verify on the self-hosted
instance before increment 2, whose cut-over step 1 reads it for the first baseline). Devon or an
interactive session on the Mac reads it through the broad account, as with the install-time upsert
(decision 3); HQ doesn't, because no decision grants HQ the broad account. No lane holds it.

## Consequences

- The lanes stop depending on the Mac being on, logged in and awake, and on its working copy.
- The VPS holds every moved lane's BWS token as a Coolify environment variable. Anyone with root
  on the VPS, or with a Coolify token holding `read:sensitive`, can read them. This is the same
  boundary the orchestrator's own secrets have today.
- Each lane's reach narrows from one of two shared identities to its own secrets. A leaked lane
  token exposes one lane's secrets, not every SDS bearer, and no lane can change or delete any
  Healthchecks check. Three lane tokens read the "SDS lanes" App's private key and thirteen read
  the read-only App's (Q9; the bump proposer's reads both), so a leaked token among them exposes
  the key or keys it reads, and with each key what the following key bullet names.
- Each moved lane's ping URL is a per-check secret, which the header of `sds-deadman.sh` argues
  against: one secret per check can be copied and rotated in only some places. Here each URL is
  stored once in BWS and read by UUID, so no copy exists to go stale, and an API key rotation
  doesn't touch it. The cost is that a UUID ping URL can't be rotated: Healthchecks assigns the
  UUID at creation and it can't be changed (Pinging API docs, "UUIDs and Slugs"). A leaked ping URL
  lets its holder fake one lane's passes, so that lane's check can't report it stopping; it can't
  reach any other check. Its remedy is to recreate that check and store the new URL, which
  starts a new check history. The credential registry records each ping URL as a credential
  with that remedy, in the increment that creates it.
- A lane runs only code the served orchestrator runs, except until the lanes' quiet-slot
  redeploy: for up to 6 hours after `finish` when the wait finds a slot, and until HQ acts when it
  times out, lanes run the previous release's code against the new orchestrator, so each release
  keeps that code compatible (decision 1). A lane fix that
  reaches production needs an orchestrator release, as any other orchestrator change does.
- Today's lane invariants that name the main tree or launchd move to their container
  equivalents. The relevant ones are "the scheduled lanes run the main tree's working copy", the
  launcher-credential rules, and the PATH rule for `uv`. Each increment rewrites the invariant it
  makes untrue.
- By Q8's and Q10's decisions, the VM rotation executor handles every lane's BWS token (Q8) and ping
  URL (Q10) from increment 2, when increment 2's ADR-0055 amendment lifts decision 4 for them. It
  can't mint or revoke a lane token: those are Devon's acts (ADR-0055 amendment 12). Devon mints and
  revokes; the executor transits and writes. A compromised executor could then read every staged
  lane token and write any lane's Coolify variable. It could also overwrite any lane's ping-URL
  secret, which blinds that lane's alarm: the lane's pings stop reaching its check.
- Lane tokens are registered and unrotated from the first lane identity (increment 2) until ADR-0059
  increment 7, which moves the executor that rotates them (Q3). That window is bounded: it must not
  outlast the credential registry's rotation interval for a lane token. If the increments would run
  past that interval, HQ asks Devon to rotate the lane tokens before the interval passes; the
  increments don't extend it. An early or leak rotation before increment 7 takes the path a first
  lane token takes from increment 2 (Q8): Devon mints the new token and pastes it into the VM
  executor's inbox, and the executor writes the lane's `<LANE>_BWS_ACCESS_TOKEN`. Once the old token
  is revoked it no longer answers, so the inbox can't bind the new one, and it's staged by hand
  (`rotation-worker stage`, `docs/operations/rotation-executor.md`, "Mint and stage a value by
  hand").
- Each App's private key reaches what its installation reaches, with its permissions, because a
  minted token is narrowed by its request but the key isn't. By Q1's and Q9's decisions, the
  "SDS lanes" App is installed only on intent-packages and the forks, so a leaked write-capable key
  lets its holder mint a token with `contents: write` on those repositories alone, and on no member
  repository, until the key is deleted on the App's settings page. The read-only App is installed
  on every repository the read-only set reads, member repositories included, with read
  permissions only, so its leaked key reads all of them and writes none. The cost of the split is
  a second App for Devon to create, install and rotate (Q9). Both keys are hand-rotated (Q4, Q9),
  so their rotation depends on Devon, as the rotation App's key does, and he rotates both in the
  same sitting. ADR-0055's hand-rotated set names who rotates its members, not how often, so the
  interval comes from the credential registry: each key is registered with a rotation interval in
  increment 3, which creates both, as a lane token is. If the keys would outlast that interval, HQ
  asks Devon to rotate them before the interval passes. Increment 3 also states how the keys' due
  date is reported without a rotation package being proposed for a key no executor handles.
- intent-packages `main` requires three status checks under classic branch protection with
  `enforce_admins` off (read 2026-11-05), which is what lets Devon's admin push land directly today.
  The "SDS lanes" App isn't an admin, so its direct push would be refused until intent-packages
  admits it, for example as a bypass actor of a ruleset that replaces the classic rule (verify
  before increment 4). That change is Devon's, in increment 4, and it doesn't add a required review:
  intent-packages requires none. Each fork's `hardened` branch forbids only force-push and deletion
  (ADR-0057 decision 7), so fork hardening's fast-forward push needs no change.
- Package revision commits on intent-packages `main` become the App's (`<app>[bot]`), not
  `AlobarQuest`'s. Increment 4 checks every reader that keys on a package revision commit's author
  before the bump proposer moves; `landing_ledger` reads a `[bot]` suffix as a machine basis.

## Dependencies

- **ADR-0039.** Moving a lane changes where it runs, not what it carries. ADR-0039's measurement
  names producers by module, not by host, so this ADR doesn't change it. ADR-0039 doesn't state
  who authors a package revision, and a machine already writes them: the bump proposer pushes
  standing-package revisions on Devon's login today. Q1 changes only the identity those commits
  carry, from `AlobarQuest` to the "SDS lanes" App's `[bot]`. So this ADR proposes no ADR-0039
  amendment; increment 4's check of every reader that keys on a revision commit's author
  (Consequences) covers the change.
- **ADR-0055.** Two amendments. Increment 2's lifts decision 4, as amendment 12 left it, for lane
  tokens and ping URLs, so that the VM executor writes them from its inbox (Q8 for lane tokens, Q10
  for ping URLs). Increment 7's moves
  the VM executor to the VPS, where it carries out each lane token's rotation between Devon's mint
  and his revoke (Q3). Each takes the next free amendment number when it's written; amendment 18
  is the last at `origin/main` on 2026-11-06.

## Devon's decisions

Devon decided Q1 to Q5 on 2026-11-05 and Q6 to Q10 on 2026-11-06. Q9 also records D-14, which
he decided on 2026-11-06.

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

Q6 (2026-11-06) widened the installation to every repository the read-only set reads, and Q9
(the same day) restored Q1's scope: the "SDS lanes" App is installed only on intent-packages and
the forks, and the read lanes mint from a second, read-only App.

### Q2: the machine-activation sweep stays on the Mac (HQ D-4)

Option (a): `activation-sweep` stays on the Mac as a third exception, beside the tool installer
and the Keychain rotation instance. It observes the Mac, and the Mac is where what it measures
lives. The rejected options were to retire it, which would leave ADR-0031's activation helper with
no control, and to rebuild it to bind from GitHub, which is new code that still leaves the sweep on
the Mac. This departs from the year plan's two-exception text; HQ updates the year plan.

### Q3: the VM executor rotates lane BWS tokens (HQ D-5)

Option (a): once ADR-0059 increment 7 moves the VM rotation executor to the VPS, it carries out each
lane's BWS token rotation. Bitwarden has no machine path to create or revoke an access token
(ADR-0055 amendment 12), so Devon mints each new lane token and revokes the old one, and the
executor does what's between: it takes the staged token, writes the lane's Coolify variable,
redeploys and verifies (increment 7). It already writes Coolify variables with its own Coolify
token. ADR-0055 decision 4, as amendment 12 left it, says a BWS consumer token may be handled only
by an executor instance on the machine that already holds it, and never by the VM. Q8 lifts decision
4 for lane tokens earlier, at increment 2, so increment 7 only moves the executor. The cost is
accepted, stated as Q8 corrected it: Devon mints each token and revokes the old one, and the
executor transits and writes it, so a compromised executor could read every staged lane token, write
any lane's Coolify variable, and overwrite any lane's ping-URL secret. Until increment 7, lane
tokens are registered and unrotated. The rejected options were the hand-rotated set, which conflicts
with Devon's "SDS can not do an all human rotation" (2026-10-21), and a new confined rotation
instance, which would add a third orchestrator identity.

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
checks with it. The same change adds `f07b1368-…` to this repository's `.bws-secrets.toml`. A
read-only key may list checks (Healthchecks API docs, "List Existing Checks"), which is all the
read needs. The Management API key (`51509d5e-…`) stays readable only by the
broad account; no lane identity reads it (decision 3).

### Q6: read-only lanes mint read tokens from the "SDS lanes" App (HQ D-8)

Option (c), decided by Devon on 2026-11-06 (HQ D-8): each lane of the read-only set (twelve lanes,
decision 2) mints a downscoped installation token from the "SDS lanes" App each pass, naming only
that lane's repositories and read permissions. Devon decided on 2026-11-06 (HQ D-14) that the bump
proposer's API reads are fed by a read mint too, from the read-only App (Q9). No fine-grained token
exists, so none is created, registered or rotated. The App is installed on every repository those
lanes read, which widens Q1's install scope beyond intent-packages and the forks. D-8 asked Devon
whether he accepts the App installed for read-only use on members' repositories, and he chose (c).
Q9 changes which App the read lanes mint from: a second, read-only App, so the "SDS lanes" App keeps
Q1's install scope. The key stays hand-rotated (Q4). The rejected options were one read-only
fine-grained token per lane, which costs Devon one token to create and rotate per lane, and one
shared read-only token with the union of the lanes' repositories and permissions, which gives every
lane Administration read and Issues read, and whose leaked copy reads everything every one of those
lanes reads.

### Q7: change-window and infra-drift move as later increments (HQ D-9)

Decided by Devon on 2026-11-06 (HQ D-9): `com.devon.change-window` and `com.devon.infra-drift`,
both owned by infraops-mcp-server, move off the Mac as increments 8 and 9 (decision 5).
`com.devon.factory-events` and SDS HQ itself stay outside this phase.

### Q8: lane tokens reach their stores through the VM executor (HQ D-10)

Option (b), decided by Devon on 2026-11-06 (HQ D-10): the VM rotation executor writes each lane
token into its `<LANE>_BWS_ACCESS_TOKEN` variable from its inbox, so no lane token is an infraops
tool argument or a process argument. Devon mints every lane token (ADR-0055 amendment 12) and pastes
each value into the inbox. This lifts ADR-0055 decision 4 for lane tokens at increment 2, in that
increment's ADR-0055 amendment, instead of at increment 7. Each lane's ping URL takes the same inbox
path into its BWS secret by Q10's decision, so the same amendment lifts decision 4 for ping URLs.
The rejected options were Devon pasting each value into the Coolify UI, and a new infraops tool that
takes a BWS UUID and writes the value server-side. Increment 1 still moves no secret: its
placeholder service holds no credential.

### Q9: the read lanes mint from a second, read-only App (HQ D-11 and D-14)

Option (b), decided by Devon on 2026-11-06 (HQ D-11): the twelve lanes of the read-only set mint
their read tokens from a second GitHub App with read permissions only, installed on the repositories
those lanes read, member repositories included. The "SDS lanes" App keeps `contents: write` and is
installed only on intent-packages and the forks, for the three pushing lanes, as Q1 decided. So a
leaked write-capable key no longer reaches any member repository. The cost is accepted: a second App
to create, install and rotate. Devon also decided on 2026-11-06 (HQ D-14) two read-only extensions.
The read-only App is installed on factory-runner and each fork as well as the repositories Q6 named
(the members' repositories, the member record's repository and the standards repositories), and
increment 3 lists the exact set and checks whether factory-runner, which is public, needs the
install at all. And the bump proposer reads the read-only App's key for its read mint, a thirteenth
reader beside the twelve lanes of the read-only set. No decision covers installing the read-only App
on intent-packages, which the read lanes also read; that install is pending Devon (decision 2). The
read-only App's private key joins ADR-0055's hand-rotated set by D-6's rule (Q4), on the same
hand-rotation interval as the "SDS lanes" App's key, and Devon rotates both in the same sitting.
Creating and installing the read-only App is Devon's act, as for the "SDS lanes" App. The rejected
option was (a), one App for both, whose leaked key could mint `contents: write` on every member
repository it's installed on.

### Q10: Devon pastes each lane's ping URL by hand (HQ D-13)

Decided by Devon on 2026-11-06 (HQ D-13): he copies each moved lane's Healthchecks ping URL from
its check's page into the VM rotation executor's inbox by hand, one at a time, as his step in that
lane's cut-over from increment 2 on, and the executor writes the lane's ping-URL secret (Q8). The
rejected option was a machine path, a new inbox write that takes no value as an argument, which
would add mechanism to the executor in increment 2.
