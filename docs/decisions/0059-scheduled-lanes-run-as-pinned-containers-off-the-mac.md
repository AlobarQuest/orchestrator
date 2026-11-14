# ADR-0059 — Scheduled lanes run as pinned containers off the Mac, one BWS identity per lane

- **Status:** Accepted, 2026-11-06
- **Date:** 2026-11-05
- **Decided by:** Devon decided Q1 to Q5 on 2026-11-05 (HQ decisions D-3 to D-7) and Q6 to Q11 on
  2026-11-06 (HQ decisions D-8 to D-11, D-13, D-14 and D-16 to D-18), all recorded in "Devon's
  decisions". Devon accepted the ADR on 2026-11-06 (HQ D-16, Q11) on HQ's clean Opus review. Devon
  decided D-19 and D-20 on 2026-11-06, recorded in Amendment 1 (one Coolify compose resource per
  lane, and the three cases in which HQ deletes a lane's resource). Devon decided D-24 to D-28 on
  2026-11-07, recorded in ADR-0055 amendment 19 and followed here by Amendment 2 (how lane tokens
  and ping URLs are issued and reissued). Devon's D-31 C (2026-11-07), recorded in ADR-0055
  amendment 21, superseded D-28: a ping URL binds by its shape and a read-only lookup of its check.
- **Relates to:** ADR-0055 (the rotation executor's isolated runtime and decisions 2 and 4,
  amendments 6, 12, 15 and 19), ADR-0058 (lanes read their scope from the member record), ADR-0039
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

- **Each moved lane is its own Coolify compose resource, `sds-lane-<lane>`, with one service, on
  the production VPS beside the orchestrator (Amendment 1).** Coolify writes every variable of a
  resource into every container of it, so one resource per lane is what gives each lane its own
  variables. Each `sds-lane-<lane>` is a Coolify *service* resource of type `docker-compose`, not an
  application: infraops creates one with `coolify_create_service` (`docker_compose_raw` alone,
  which the tool base64-encodes, as fixed in infraops-mcp-server #164, `32a13046`; the fix is live
  only after Devon syncs and reconnects infraops, and increment 1 checks it, "Verify before
  increment 1" and Q11), replaces its compose with `coolify_update_service`
  (`docker_compose_raw`, sent as a `PATCH /services/{uuid}`), and writes its variables with
  `coolify_create_service_env` and `coolify_update_service_env` (infraops-mcp-server
  `src/tools/services.ts` and `src/tools/env-vars.ts`). The orchestrator's own
  `coolify_update_application` path doesn't apply to it. Each lane's service runs its launcher
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
  that digest, and `lanes-finish` checks the lane's container runs it (following bullets).
- **A lane's image is pinned to the served orchestrator tag, and the deploying session moves it.**
  After the orchestrator's `finish` passes, `scripts/deploy_orchestrator.py` prints the lane phases'
  first command, and the deploying session writes the same tag to each lane's resource by the lane
  phases (following bullets). A lane runs either the served tag or, until the session's quiet-slot
  redeploy of its resource, the tag the orchestrator served until that release, and no other. An
  orchestrator rollback isn't `prepare`'s procedure: `migrations_between` refuses a served commit
  that isn't an ancestor of the built one (`scripts/deploy_orchestrator.py`), deliberately
  (`docs/operations/deploy.md`). Each lane follows the orchestrator back by `lanes-prepare --tag`
  on its own resource, or, for a target older than the lane's first image, goes back to the Mac
  ("Rollback for one lane"). Until it does, it runs code newer than the served orchestrator's
  (Amendment 1). This replaces
  `activate-checkout.sh`'s fast-forward of the main tree. A moved lane never reads a working copy.
- **The deploy command never calls Coolify; it checks, prints the calls, and verifies.** Its
  docstring states the rule this keeps: every Coolify change goes through the infraops MCP tools,
  which a local program can't call, so `prepare` prints the exact `coolify_update_application` and
  `coolify_deploy` calls and the deploying session runs them. HQ is that session, as the one
  production actor. Increment 1 gives a lane resource's changes the same shape, in three phases of
  the same program. Each phase acts on one lane's resource: it takes the lane, `--lane <lane>`, one
  change, `--tag <tag>`, `--schedule <true|false>`, `--add --tag <tag>` or `--token` (Amendment 2,
  the token-change mode, which follows ADR-0055 amendment 19 item 6), and a state file. An
  `--add` names the tag whose tracked files carry the lane's compose, which must be the served
  orchestrator tag, because a lane arrives with the commit that adds its launcher, crontab line and
  compose file. Every run writes the lane's whole compose file, so a tracked compose edit deploys
  with the tag that carries it: `coolify_update_service` sends the whole file as
  `docker_compose_raw`, so no run writes less (that the next `coolify_deploy` applies it is
  verified before increment 1). The compose a run writes is the lane's tracked file at the tag it
  deploys (the named tag for `--tag` and `--add`, the running tag for `--schedule`), and its state
  file records the file it sent. Coolify normalizes what it stores (`Yaml::dump(Yaml::parse(…))`),
  so no phase compares the stored text with that file byte for byte (Amendment 1). The compose it
  replaces is the one the lane's last finished run's state file recorded (the newest one
  `lanes-finish` passed for that lane, as the following "No release starts" bullet defines), the
  file Coolify was last given, never the tracked file at the running tag. No phase creates a lane's
  resource: HQ creates each through infraops, each as its own announced production act, unless HQ
  finds a reason it shouldn't, and then HQ tells Devon (Q11). No phase yet records the first
  compose a resource's first lane-phase run replaces (verify before increment 1).
  - **`lanes-prepare`** waits for the lane's quiet slot (following bullet), then records the change,
    the compose it writes, and the values they replace, and prints, exactly, the session's calls:
    the writes (the whole compose through `coolify_update_service`, and one variable:
    `SDS_LANES_IMAGE_TAG` through `coolify_update_service_env` for `--tag`, and through
    `coolify_create_service_env` for `--add`, and `LANE_SCHEDULE_ENABLED` through
    `coolify_update_service_env` for `--schedule`; no variable for `--token`, whose compose is the
    lane's tracked file at its running tag, as for `--schedule`, because the VM rotation executor
    has already written the token, Amendment 2), then `coolify_deploy` on `sds-lane-<lane>`, then
    `lanes-finish`. An `--add` creates the tag variable because Coolify's parser makes no variable
    row for a `${VAR}` in `image:` (the verify report's item 1), and `coolify_update_service_env` is
    a `PATCH` of `/services/{uuid}/envs` (infraops `src/tools/env-vars.ts`, 478) that Coolify
    answers with 404 when the key has no row (`ServicesController.php`, 1238-1240, at
    `v4.0.0-beta.473`). It prints how the compose entry differs from the recorded compose, so the
    session's write names each edit it applies. It prints the inverse too, for `lanes-finish` to
    name if the redeploy fails: for `--tag` and `--schedule`, the recorded compose and the replaced
    variable value; for an `--add`, deleting the lane's resource, which HQ performs as its own
    production act under D-20 (Amendment 1; "Adding a lane creates its
    resource"). The old tag or schedule value, and a stateful
    lane's volume (the following list), are what the session reads from the running container and
    passes in, as `prepare` takes `--coolify-health-check-enabled`. The session reads the tag with
    `vps_docker_ps`, a schedule value with `vps_exec` running `printenv LANE_SCHEDULE_ENABLED` in
    the lane's container, and a volume and an image digest with `vps_exec` running `docker inspect`
    with a `--format` that prints only the container's mounts or its image, so no read lists
    another variable's value.
  - **`lanes-recheck`** repeats the two quiet-slot reads (one for the placeholder, following
    bullet). The session runs it, then the writes and
    the redeploy, with no other step between: the writes are staged only if this exits 0, and
    immediately before the redeploy. A non-zero exit sends the session back to `lanes-prepare`.
  - **`lanes-finish`** takes the session's reads of the redeployed container, the one service of
    the lane's resource:
    - its image's digest, read container to image to repository digest as
      `docs/operations/deploy.md` reads the orchestrator's, which must equal the lane image digest
      the release recorded (preceding bullet). The tag alone isn't the check;
    - its `LANE_SCHEDULE_ENABLED`;
    - for each of the eleven stateful lanes (decision 1, "Lane state"), the source of the mount at
      `/var/lib/sds/<lane>`, which must be the volume name recorded for that lane. Coolify's
      `parse()` of a stored compose definition can double-prefix a volume's name and start the
      service on a new, empty volume while its health stays green (workspace invariant #4), so a
      green redeploy proves nothing about the volume. An added lane has no recorded volume; its
      `lanes-finish` records the one it reads, and the cut-over copies into that one.

    It exits 0 only if they show the change and no other. Otherwise, or if the redeploy failed, it
    exits 2 and prints the inverse. For `--tag` and `--schedule` that's the inverse writes and a
    second `coolify_deploy`, and the session runs them, then `lanes-finish --restored` with fresh
    reads, which exits 0 only if the container shows the replaced values again. For an `--add` the
    inverse is the delete of the lane's resource alone, with no second `coolify_deploy`: nothing is
    left to redeploy or to read in a container. HQ runs the delete, and `lanes-finish --restored`
    then takes the session's two reads that the resource is gone (`coolify_list_services`, whose
    default summary carries only each resource's `uuid`, `name`, `status` and `fqdn`, no longer
    lists `sds-lane-<lane>`, and `vps_docker_ps` shows no container of it), exits 0 only if both
    show it gone, and closes the run. Coolify's delete only queues the deletion, so the session
    re-reads both for up to 10 minutes before it passes them in, and a resource still listed then
    fails the check. A `--token` run passes on other reads and closes another way (Amendment 2):
    it passes when the image digest, schedule and volume are unchanged, the container's
    `BWS_ACCESS_TOKEN` is non-empty, and `lane-run --read` reads every listed secret `ok`; a
    mismatch exits 2, and its `lanes-finish --restored` writes nothing and closes the run as
    failed, because its compose is the one the lane already ran. That restore is the session's
    act, or for an `--add` HQ's
    delete, which the command names and then checks.

  No phase holds a Coolify credential, and none needs one.
- **A routine tag update redeploys a lane's resource only in its quiet slot.** Coolify's deploy
  endpoint (`POST /deploy`) takes resources by `uuid` or `tag`, and its restart endpoint
  (`POST /services/{uuid}/restart`) takes the service resource's `uuid` and a `latest` flag
  (Coolify API reference, "Deploy" and "Restart Service", read 2026-11-05). Each lane is its own
  resource (Amendment 1), so a tag update, schedule change or restart recreates only that lane's
  container, and that lane's running pass is stopped midway. The lane phases therefore apply the
  cut-over's two checks to the lane before the session redeploys its resource: the time is outside
  estate landing's 02:00 to 06:00 New York window, and the lane's own check doesn't have `started`
  set to `true`. No other lane's check holds it. For `sds-lane-placeholder` alone, named, the quiet
  slot is the landing-window check only, because the placeholder runs no launcher and has no
  Healthchecks check, as `lanes-finish` skips the token check for it alone (increment 1); its
  `lanes-recheck` repeats that one read. `started` is the boolean field of each
  check in the Healthchecks Management API's check listing (`GET /api/v3/checks/`, API docs "List
  Existing Checks"), read just before the redeploy. The API docs show `started` only in their
  example responses and don't describe it, so increment 1 verifies, before it relies on the field,
  that the self-hosted version returns it in the listing and sets it to `true` from a `/start` ping
  until the pass's result ping. Every scheduled pass pings `/start` first, so `started` is the one
  signal every lane already sends. A check whose `status` is also `down` doesn't hold the deploy,
  because its pass stopped without a final ping and the down alert already reports it. The deploy
  command runs where it runs today, in the deploying session on the Mac. It reads the listing with
  the read-only Healthchecks key (BWS `f07b1368-…`, in `Rotation / Healthchecks`), through a direct
  read grant to `sds-operator` that increment 1 adds (Q5, decided). No single process waits for the
  slot: an HQ cycle is capped at 1200 seconds (`cycle_limit_s` in sds-hq's `config.toml`). The
  first `lanes-prepare` run for a change records a deadline 6 hours later, as an absolute UTC time
  in the lane's run file, keyed by the change's `change_key`, long enough to outlast the 4-hour
  landing window and a pass already running when it closes. A run that finds either check unmet
  exits with its "condition unmet" code (2), and HQ re-runs `lanes-prepare` on a later cycle. A
  re-run for the same change resumes against the recorded deadline; a run for another change to
  the same lane supersedes it and records its own. The run that finds the deadline passed marks the
  run `timed_out`, prints one line naming the lane, the change and the deadline, and exits 4, its
  "timed out" code (Amendment 1). The session that runs the deploy command raises the alert through
  its own channel; the deploy command sends no alert and reads no alert-hub token. A re-run after
  that records a new deadline. The session has written nothing to the lane's resource, so it stays
  as it was until HQ re-runs `lanes-prepare` or redeploys by hand. The session
  writes nothing to the lane's resource while the command waits: it writes the compose and any tag
  or schedule variable (the following bullets) only after `lanes-recheck` exits 0, immediately
  before its redeploy call. So a wait that times out leaves nothing staged, and no later redeploy
  can apply a change silently, except in a `--token` run (Amendment 2): the VM rotation executor's
  `PATCH` writes the lane's `<LANE>_BWS_ACCESS_TOKEN` before `lanes-prepare` runs, so the new token
  is already staged while the command waits, and any later redeploy of the lane (by hand, `--tag`
  or `--schedule`) loads it without `--token`'s reads. Increment 2 verifies that case (Amendment
  2's "Verify in increment 2", the `--tag` run after the executor's `PATCH`; amendment 19's "Open
  before building", `--token` sub-item 4). If a `--tag`
  or `--schedule` redeploy fails after the writes, `lanes-finish` names the inverse writes and the
  session restores the values at once. For an
  `--add` the inverse is the delete of the lane's resource, HQ's act under D-20 (the lane-phase
  bullets and "Adding a lane creates its resource"). The residual: a pass that starts
  between `lanes-recheck`'s read and the redeploy is still stopped, as `recheck` before an
  orchestrator swap accepts a unit claimed after it. The P2 backlog item in which
  `deploy_orchestrator.py` holds the bump-driver launch agent during a deploy is re-scoped by this
  rule once the bump driver moves.
- **Until a lane's redeploy, its old code runs against the new orchestrator.** A
  release's orchestrator is served from `finish`, but each lane stays on the previous tag until
  the session's quiet-slot redeploy of its resource: at most 6 hours when `lanes-prepare` finds a
  slot, and until HQ acts when it times out. So every release must keep the previous release's lane
  code working against its orchestrator for that interval: a route or response field a lane reads
  can't be removed or changed incompatibly in the release that stops the lane needing it.
- **No release starts while a lane lags or a lane-phase run is unfinished.** Each lane-phase run
  has its own state file, the command's own record, which isn't a write to the lane's resource, and
  each lane's runs form their own chain (Amendment 1). A run is *unfinished* from its
  `lanes-recheck` exit 0, after which the session writes to the lane's resource, until
  `lanes-finish` passes it (or `lanes-finish --restored`, after a failed redeploy). A run whose
  `lanes-recheck` never exited 0, such as a waiting, timed-out or refused `lanes-prepare`, wrote
  nothing to the lane's resource, so it isn't unfinished, and the lane's next `lanes-prepare`
  supersedes it. "The lane's last finished run's state file" is the newest one `lanes-finish`
  passed for that lane; a run that wrote nothing never replaces it. Increment 1 makes `prepare` read
  the file of each lane registered at the time of the check and refuse when one shows its lane on a
  tag other than the served one, or when any lane-phase run is unfinished. A lane is registered
  while this repository's `.cred-consumers.toml`, read at `main` as the VM rotation executor reads
  its registry (`src/rotation_worker/registry.py`), has the lane token's entry, whose destination is
  the lane's resource (Amendment 1, item 3). A registered lane with no passed `--add` (no finished
  run) holds no release, so the check skips it, and that isn't an error. The run files are a
  persisted record that outlives the lane's resource, so the registry, not the run files, names the
  lanes the check covers: removing a lane's entry, the last step of "Rollback for one lane", drops
  the lane from the check. `sds-lane-placeholder` has no token and no entry, so the check covers it
  by name until increment 2 retires it, and treats it as any lane: skipped until its `--add` passes,
  then refused when its last finished run shows a tag other than the served one. `prepare` admits
  no `--ref` at a lane's commit: `migrations_between` refuses a served commit that isn't an
  ancestor of the built one, because a rollback isn't `prepare`'s procedure
  (`scripts/deploy_orchestrator.py`; `docs/operations/deploy.md`, deliberately), and that refusal
  stays (Amendment 1). Its refusal names the lane and the two exits: re-run that lane's phases with
  `--tag` at the served tag, or roll that lane back. It refuses and doesn't wait, so the deploy
  gains no blocking wait. `lanes-prepare` refuses while a run of its lane is unfinished, so two runs
  that write one lane's resource never overlap. So a lane never runs code older than the previous
  release's, however long a timed-out redeploy waits.
- **The lanes that need other repositories clone them at the start of each pass.** These lanes
  are the bump driver (intent-packages, project-standards, security-standards), both
  bridge proposers (intent-packages; the rotation proposer also needs infraops-mcp-server, which
  is public, so its clone needs no token), the work carrier (package checkouts) and fork hardening
  (the forks). Each clone is shallow, at
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
  preceding bullet. Each of the eleven gets one named volume in its own lane's tracked compose
  file, `sds-lanes-<lane>-state`, mounted at `/var/lib/sds/<lane>` in the lane's one service, and
  that service's compose entry sets the lane's variable to that path. Coolify prefixes each volume's
  name with the uuid of the lane's own resource, known only after the create, as
  `<resource-uuid>_<slug>` (the verify report's item 2; verify before increment 1), so
  `lanes-finish` checks each mount's source against the name recorded for it (preceding bullets),
  never against the tracked name. The lane image creates `/var/lib/sds/<lane>` owned by
  `orchestrator`, the user every lane process runs as; whether a new volume takes that directory's
  owner is also verified before increment 1. So no lane starts empty: the cut-over copies the Mac's
  files into the volume (cut-over step 2), and a rollback copies them back. A volume outlives every
  redeploy, so the state survives a tag update as it survives a launchd restart today.
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
  orchestrator and leaves every lane's resource untouched. Setting one isn't free: it's an
  orchestrator environment change and restart, which must not happen while a dispatched run is live.
  They cover only what acts through the orchestrator: dispatch (the bump driver), estate landing,
  inert landing and the bump merge (the bump driver). The proposers, the work carrier, fork
  hardening and the rotation decomposer, which proposes decompositions to the orchestrator as
  SYSTEM, have no switch, and the observe-only lanes change no record. The switches are the only
  per-lane stop that needs no redeploy of a lane's resource. A stop or restart through Coolify names
  one lane's resource, so it stops that lane alone (Amendment 1), and like any redeploy it follows
  that lane's quiet-slot rule.
- **Each lane's schedule is switched by one variable, `LANE_SCHEDULE_ENABLED`, in that lane's
  own resource alone.** The lane image's entrypoint reads it once, at container start.
  When it's `true`, the entrypoint installs the lane's tracked crontab line and starts the
  scheduler. When it's anything else, or absent, the entrypoint starts the scheduler with an empty
  table, so the container stays running, runs no pass on its own, and can still be entered for a
  one-off run (cut-over step 2). A service first starts with it `false` (the following bullet).
  Changing it is a Coolify variable change plus a redeploy of the lane's resource, because the
  entrypoint reads it only at start. That redeploy stops the lane's running pass, so it follows the
  quiet-slot rule: the session runs the lane phases with `--lane <lane> --schedule <value>`, with
  the same two reads and the same 6-hour maximum as a tag update. A schedule change therefore takes
  effect in the next quiet slot, at most 6 hours after it's asked for when the wait finds one, and
  when HQ acts if the wait times out.
- **Each lane's values are its own resource's variables (Amendment 1).** Coolify holds one
  variable set per compose resource and writes every variable of it into every container of it, so
  each lane is its own resource: a lane's variables reach its own container and no other lane's.
  Each resource holds the lane's `<LANE>_BWS_ACCESS_TOKEN` (for example
  `EXECUTION_WATCH_BWS_ACCESS_TOKEN`), which the lane's tracked compose file maps to the plain name
  `BWS_ACCESS_TOKEN` in its one service's `environment` entry, the lane's `LANE_SCHEDULE_ENABLED`,
  and the lane's own `SDS_LANES_IMAGE_TAG`, which the service's `image:` names, so a tag update
  writes that one variable beside the lane's whole compose (preceding bullets). Docker Compose fills
  `image:` from the `.env` Coolify writes, and Coolify's parser makes no variable for `image:`, so
  the `--add` creates `SDS_LANES_IMAGE_TAG` (`coolify_create_service_env`) before the resource's
  first start (the verify report's item 1), and every later `--tag` updates it.
  A lane token never goes through infraops's service env tools, whose responses carry the written
  row's `value` (the verify report's item 6). The VM rotation executor writes it, into the lane's
  own resource (Q8), with `redeploy` false: it never redeploys the lane, and the container takes
  the value at a lane phases' redeploy in the lane's quiet slot (Amendment 2).
- **Adding a lane creates its resource (Amendment 1).** Adding a lane changes no other lane's
  compose: HQ creates the lane's resource, `sds-lane-<lane>`, through infraops from the lane's
  tracked compose at the served tag, without `instant_deploy`, so no container starts (Q11). Its
  first start is the lane phases' `--add`, under the same quiet-slot rule, the same two reads and
  the same 6-hour maximum as a tag update. The session writes the compose and the tag variable only
  after `lanes-recheck` exits 0, as the quiet-slot bullet says. Before that, the resource's two lane
  variables are set: the session writes `LANE_SCHEDULE_ENABLED`, `false`, and the VM rotation
  executor writes the lane's BWS token into the resource from the executor's inbox (Q8, decided).
  They're inert until then, because no container of the resource has started. The token is a
  secret, so it never travels as an infraops tool argument, which the transcript records. The
  command can't read Coolify's variable listing, and the session must not either, because that
  listing carries every variable's value (`value`, `real_value`), the lane's token included. Nor may
  the session make any other read that can return a variable's real value: `coolify_get_service` on
  a lane's resource, whose response may carry a variable's real value, and any read like it. The
  session's own writes return a response too. infraops redacts every tool's response by default
  (`src/utils/register-sanitized.ts`), and that redaction is best-effort (workspace invariant #24):
  a service env write's response carries the written row's `value` (the verify report's item 6),
  which is why no lane token goes through those tools. Its reads of a lane's resource are the
  container reads the lane phases name. So the guarantee that a lane's container never starts
  scheduled is built, not read:
  - The lane's compose entry maps its schedule as
    `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}`, so an unset variable starts it
    unscheduled, and `lanes-prepare` refuses an `--add` whose tracked entry maps it any other way.
  - The session passes the value it wrote, `--schedule-written false`, and `lanes-prepare`
    refuses any other.
  - `lanes-finish` takes the new container's `LANE_SCHEDULE_ENABLED` and whether its
    `BWS_ACCESS_TOKEN` is non-empty (the session runs `test -n` in the container and prints no
    value). It refuses unless the container is unscheduled and holds a token. For
    `sds-lane-placeholder` alone, named, it skips the token check, because the placeholder holds
    no token (increment 1).

  A container that starts without its token runs no pass, because it's unscheduled, and the
  identity proof (cut-over step 1) fails on it.

  When `lanes-finish` refuses an `--add`, or its redeploy fails, the inverse it names is the delete
  of the lane's resource alone, with no second `coolify_deploy` (the lane-phase bullets). The delete
  is HQ's own announced production act, framed exactly as the create: `prod announce`, the
  `coolify_delete_service` call, a check at the source that the resource is gone, and
  `prod verified`. Its authority is D-20 (Amendment 1); D-16 doesn't cover
  it. The delete removes the resource's volumes: infraops's tool calls `DELETE /services/{uuid}`
  with no query (`coolifyDelete('/services/${uuid}')`, `src/tools/services.ts` line 326 at
  infraops-mcp-server `32a13046`), and Coolify's `delete_volumes` defaults to `true`
  (`ServicesController.php`, 737 and 789, at `v4.0.0-beta.473`). Then `lanes-finish --restored`
  confirms the resource is gone and closes the run.

**Rejected: Coolify scheduled tasks.** A Coolify scheduled task runs a command inside a running
container of an application or service. The lanes would share one container and its environment,
which defeats one identity per lane. The schedule would also live in Coolify and not in the
repository.

**Not considered: a separate VM per lane.** Neither the year plan nor this phase calls for one.
One container per lane already gives each lane its own process, filesystem and environment.

### 2. One narrow BWS identity per lane, replacing every Keychain read

- **Each moved lane gets its own BWS machine account**, named `sds-lane-<lane>`, with a direct
  read grant on exactly the lane's `.bws-secrets.toml` entries and nothing else: the secrets its
  launcher fetches today less the Healthchecks Management API key, which every launcher reads
  today through `sds_deadman_resolve` and no lane may hold (decision 3), plus its ping-URL secret
  (decision 3) and the private key of each App it mints from (the following bullets). This follows
  ADR-0055 amendment 6: grants on secrets, not on projects. Today's two Keychain identities stop
  being any lane's identity. The narrow-versus-broad split, and the two-identity trap the
  launchers guard against, end with them: one lane has one token.
- **The token reaches the container as one environment variable, `BWS_ACCESS_TOKEN`**, in the
  lane's own container, mapped from the `<LANE>_BWS_ACCESS_TOKEN` variable of the lane's own
  resource, `sds-lane-<lane>`, whose variables reach no other lane's container (decision 1,
  Amendment 1). A lane's launcher reads every secret it needs from BWS
  by UUID at the start of each pass, as it does now, through `scripts/sds-bws.sh`. The token is
  never in the image or the repository, and no value passes through a process argument. Beside the
  variable, each lane token keeps a stored copy, its keeper, in `Rotation / Keepers` on the VM, so
  the token isn't only in transit through the executor (Amendment 2; D-24, a standing answer for
  every later lane token).
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
    private, except the rotation proposer's clone of infraops-mcp-server: that repository is
    public, so the clone needs no credential, and increment 4 verifies it works anonymously.
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
  the members' repositories, the member record's repository and the standards repositories (Q6,
  HQ D-8, whose repositories D-14's and D-17's wording names again), on factory-runner and each
  fork (Q9, HQ D-14), and on intent-packages (Q9, HQ D-17). The
  "SDS lanes" App stays installed only on intent-packages and the forks, as Q1 decided. The
  read-only App is installed on both too, and the read lanes read them with the read-only key, so
  no read lane holds a key that can write.
  Increment 3, which moves the first lane that reads GitHub (the landing ledger), lists the exact
  set from the member record, the lanes' clone lists and that registry, and Devon installs the
  read-only App on those alone. Verify before increment 3 whether factory-runner, which is public,
  needs the install at all (Q9). The fork-sync watch also reads each fork's
  upstream's public releases (`fork_sync/watch.py`); verify before increment 3 that an installation
  token reads an upstream's public releases without the read-only App installed on the upstream, or
  that increment decides, and any answer that widens a credential goes back to Devon first. The
  read permissions the mints draw on are Metadata, Contents (files, commits, compares and clones),
  Pull requests, Issues (the fork-sync watch's read of each fork's
  open review alert), Actions (workflow runs and jobs) and Administration (the unit verifier's read
  of `main`'s `required_status_checks`, in `unit_verifier/required_checks.py`), so the read-only App
  holds each of them as read, and no write permission. That list is HQ's derivation from the
  lanes' code. Devon confirmed the list on 2026-11-06 (HQ D-18: "Yes"). Verify before
  increment 3 that an installation-token mint narrowed by repository and permission returns a token
  that refuses
  everything outside the request. A lane that needs more than its read mint gets it by its own
  decision.
- **The three pushing lanes push as the "SDS lanes" GitHub App (Q1, decided).** `bump-proposer`,
  `rotation-proposer` and `fork-harden` push with an installation token of a new GitHub App, with
  `contents: write`, minted naming only intent-packages or the forks, the only repositories it's
  installed on. Whether fork hardening's push also needs `workflows: write` is verified before
  increment 5 (increment 5), and this ADR grants nothing more. The push token reaches git only
  through `sds_git_credential` (the preceding bullets); none of the three feeds it to an HTTP API
  client. The bump proposer's own `BUMP_PROPOSER_GITHUB_TOKEN` comes from its separate read mint
  (preceding bullet). Each pass mints its tokens from an App's private key, which lives in BWS and
  is read by UUID, with a direct read grant to each lane identity that mints from that App, and to
  no other. The "SDS lanes" App's key is read by the three pushing lanes. The read-only App's key is
  read by the twelve of the read-only set and by the bump proposer, for its read mint, thirteen in
  all (Q9). So the bump proposer reads both keys, and fifteen lane identities read one or both. The
  tokens are short-lived and never stored. Both keys are created in increment 3, which moves the
  first lane that mints, by Devon, because only an App's settings page generates a GitHub App
  private key (GitHub Docs, "Managing private keys for GitHub Apps"). The bump driver needs no
  GitHub write scope: its token only reads pull request state and makes shallow clones
  (`src/bump_driver/client.py`, `src/bump_driver/mirror.py`), and it dispatches and merges through
  the orchestrator as SYSTEM. Its variable is fed from its read mint.
- **`.bws-secrets.toml` and `.cred-consumers.toml` gain each lane identity and each secret it
  reads**, in the same change as the lane's move. The credential registry gains each lane token and
  each lane's Healthchecks ping URL (decision 3) as a credential, and each App's private key, the
  "SDS lanes" App's and the read-only App's, as two more. Each lane token's entry names its
  destination, `<LANE>_BWS_ACCESS_TOKEN` in the lane's own resource by that resource's uuid, which
  the VM rotation executor's registry needs (Amendment 1). Once ADR-0059 increment 7 moves the VM
  rotation executor to the VPS, it carries out each lane token's rotation between Devon's mint and
  his revoke (Q3, decided). Before then it carries out a lane token's first issue and any early or
  leak reissue the same way, from its inbox (Amendment 2). Both App keys join ADR-0055's
  hand-rotated set, as the `alobar-sds-rotation` App's key did (Q4 and Q9, decided): Devon rotates
  each on its App's settings page, and no executor handles either. Each is registered as class
  `github-app-private-key`, whose interval in infraops's rotation policy (`CLASS_POLICY` in
  `src/security-drift/cred-rotation.ts`) is 365 days, so both keys have the same interval, and Devon
  rotates both in the same sitting (Devon's D-11 addendum, 2026-11-06).
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
  grant are this ADR's proposal, created by the increment that moves each lane: in that increment's
  cut-over, the VM root creates the secret in `Rotation / Keepers` with the keeper command, holding
  the inbox's empty marker, before the lane's account is granted it (Amendment 2). The name lookup
  moves to install time: in the increment that moves a lane, Devon or an interactive session on the
  Mac runs `sds_upsert_check` through the broad account, as interactive installers do today, to
  create or update the check. HQ doesn't run it: HQ's identity is its own BWS token
  (`BWS_ACCESS_TOKEN_SDS_HQ`), and no decision grants HQ the broad account. The URL reaches its BWS
  secret as a lane token does (Q8, decided): Devon copies it from the check's page and pastes it
  into the VM executor's inbox by hand, and the executor writes the lane's ping-URL secret. The
  executor binds the paste by its shape and by the read-only key's lookup of its check beside a
  random UUID's, never by a ping (ADR-0055 amendment 21; D-31 C, which superseded D-28). The lookup
  proves the check exists in the key's project and the UUID's first half, so cut-over step 4 is
  still what proves the full URL (Amendment 2). That paste is Devon's step in each lane's cut-over
  (step 1), one URL per moved lane, one at a time, from increment 2 on; no machine path carries a
  ping URL (Q10, decided). No session stores it, because `bws secret create` and `bws secret edit`
  take the value as an argument. Verify before increment 1, on the self-hosted version: that a UUID
  ping URL needs no other credential and does nothing beyond pinging, and the URL's base
  (`PING_ENDPOINT`).
  - **Narrowed by ADR-0055 amendment 23 (2026-11-10).** No person copies or pastes a ping URL,
    and the inbox doesn't carry one: before the claim, the VM rotation executor reads the lane
    check's `ping_url` from the Management key's check listing, by the name the ping URL's
    registry row gives in `healthchecks_check`, and writes the lane's ping-URL secret. The
    executor's root reads the Management key by a direct grant; the lane still holds only its own
    check's ping URL, never the key.
- **What changes in `sds-deadman.sh`, in increment 2** (the first lane whose container pings).
  `sds_deadman_resolve` gains a first path: when `SDS_DEADMAN_PING_URL_UUID` is set, it reads the
  URL with `sds_bws_value` and the lane's own token, and lists no check. `SDS_DEADMAN_PING_URL_UUID`
  is a variable tracked in the lane's own compose file (Amendment 1), and `lane-run` passes it to
  the launcher (decision 1). Its value is the lane's ping-URL secret UUID from `.bws-secrets.toml`,
  not a secret. The name-lookup path through the broad account stays for the lanes still on the Mac,
  and increment 6 removes it with their plumbing. The helper still reports and never gates: a URL it
  can't read logs one reason and disables that run's pings. `sds_deadman_ping` moves its URL to
  curl's stdin config, as `sds_outcome_ping` already does, because the URL is a secret and must not
  be a process argument. `install-fork-harden-launchd.sh`'s private upsert is replaced by
  `sds_upsert_check` when fork hardening moves.
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
| `change-window` | infraops-mcp-server (`scripts/change-window.sh`, `scripts/com.devon.change-window.plist.template`) | Daily at 04:00, runs `run-window` from infraops-mcp-server's own `change-mgr-cli` (`src/cli/change-mgr-cli.ts`, `src/change-manager/run-window.ts`) in infraops's working copy, which executes approved change requests with the prod and dev Coolify tokens in its environment, and sends the digest to alert-hub. The Keychain BWS identity it shares with infra-drift (`BWS_ACCESS_TOKEN_INFRA_DRIFT`) reads both Coolify tokens, the Anthropic key and the change-manager bearer. | Yes: it executes approved changes. | Moves, increment 8 (D-9). |
| `infra-drift` | infraops-mcp-server (`scripts/drift-audit.sh`, `scripts/com.devon.infra-drift.plist.template`) | Daily at 03:00, the drift audit (ADR-0055's drift reporter): audits prod and dev Coolify, auto-applies safe remediations, syncs App Brain, pushes escalations to change-manager, posts observations as the drift reporter, mints due follow-up units as SYSTEM (read through `sds-operator`), and runs the Mac's security scan with its guarded `chmod` fixes. Its credential scan reads the same `cred-consumers.list` and rotation state as the rotation proposer. It doesn't write the list. | Yes: remediations, change records and minted units. | Moves, increment 9 (D-9). |
| `factory-events` | security-standards (`scripts/factory-events-nightly.sh`, deployed to `~/.claude/bin/`) | Daily at 03:30, adapts events from this Mac's high-power audit log and from change-manager into the factory events chain (`FACTORY_DB_DSN`), verifies the chain and its anchor, ships it, and reports one `chain:factory-events` observation. It reads the change-manager bearer through the broad Keychain identity. | No member: it writes only the events chain and one observation. One input is this Mac's audit log. | Outside this phase (D-9). |
| `sds-hq` | sds-hq (`launchd/`) | Every 15 minutes, and when `state/trigger` changes, runs one SDS HQ cycle (`sds-hq cycle`, the planning agent): headless Claude Code sessions that dispatch workers into git worktrees of member repositories, which push branches and open pull requests. | Yes, through its workers. | Outside this phase (D-9). |
| `sds-hq-inbox` | sds-hq (`launchd/`) | Every 60 seconds, runs `sqlite3 -readonly` over this Mac's Messages database and writes the latest messages of two chats to `state/imessage-inbox.json`. | No. It reads a local database read-only and writes one local file, so it acts on nothing; its subject is this Mac's Messages database. | Outside the phase bar, which covers lanes that act on a member. Not an exception. |

### 6. Migration order, cut-over, rollback and the week bar

#### Verify before increment 1

Increment 1 confirms each of these on the production Coolify, Docker and self-hosted
Healthchecks before it relies on it. A fact it can't confirm stops the increment for a decision:

- Answered by D-19 (Amendment 1): a resource variable reaches every container of its resource, so
  each lane is its own resource. Docker Compose substitutes `SDS_LANES_IMAGE_TAG` in `image:` from
  the `.env` Coolify writes, so the variable must exist before a resource's first start (the
  verify report, `docs/superpowers/reports/lanes-inc-1-verify-report.md`, item 1). The residual
  production check, made with a harmless non-secret variable before the first lane token is written:
  a variable of one lane's resource doesn't reach another lane's container (decision 1).
- How Coolify names a compose file's volumes, and whether it honors a volume's `name:` or
  `external` setting (decision 1, "Lane state"). The verify report's item 2 reads Coolify as
  naming each volume `<resource-uuid>_<slug>` with the lane resource's own uuid, known only after
  the create, and honoring neither; increment 1 confirms it on the placeholder's volume across two
  deploys.
- Whether a new named volume takes the owner of the lane image's `/var/lib/sds/<lane>`
  directory (decision 1, "Lane state").
- The Healthchecks check listing returns `started`, set to `true` from a `/start` ping until the
  pass's result ping (decision 1).
- A UUID ping URL needs no other credential and does nothing beyond pinging, and the URL's base
  (`PING_ENDPOINT`) (decision 3).
- `coolify_update_service` with `docker_compose_raw` replaces the whole stored compose of a lane's
  resource, the next `coolify_deploy` runs that compose, and the write doesn't take the `parse()`
  path that can double-prefix a volume (workspace invariant #4). Coolify normalizes the compose it
  stores (`Yaml::dump(Yaml::parse(…))`, the verify report's item 6), so the lane phases record the
  file they sent and never compare the stored text with it byte for byte. Also what that write's
  and `coolify_update_service_env`'s responses carry for a lane's resource (decision 1): an env
  write's response carries the written row's `value`, so a lane token never goes through
  infraops's service env tools. If the compose can't be replaced this way, no infraops tool
  replaces it, and increment 1 stops for a decision.
- `coolify_create_service` creates a lane's resource. At Coolify `v4.0.0-beta.473` the create
  endpoint refuses `type` beside `docker_compose_raw` and accepts the compose only base64-encoded
  (Q11). infraops-mcp-server #164 (squash `32a13046`) fixed the tool to match: it takes exactly one
  of `type` or `docker_compose_raw`, sends `type` only when given, and base64-encodes the compose
  (`src/tools/services.ts`, 213 and 234-239, at `32a13046`). The fix is live on the Mac: Devon
  synced the infraops checkout on the Mac and reconnected infraops on 2026-11-06, and HQ confirmed
  that the checkout contains `32a13046` and that the live `coolify_create_service` schema takes
  exactly one of `type` or `docker_compose_raw`. Increment 1 checks the live tool against Coolify
  before HQ's first create. No lane phase creates a resource: HQ runs each create through infraops
  (Q11), and increment 1 decides only how a resource's first compose is recorded as the one its
  first lane-phase run replaces.
- Where each lane-phase run's state file lives, so that the next run for a lane, from any session,
  reads the compose Coolify was last given for that lane's resource and finds a waiting or
  unfinished run, and `prepare` and `lanes-prepare` read them with no new credential (decision 1).
  Each lane has its own chain of runs (Amendment 1). The verify report's item 8 sets out two
  places, a fixed directory on the Mac outside every repository and a tracked file in the sds-hq
  repository, and recommends the first; increment 1's plan settles it.
- `prepare` refuses when the last finished run of any lane registered at the time of the check (the
  lane token's entry in `.cred-consumers.toml` at `main`, plus `sds-lane-placeholder` until
  increment 2 retires it) shows it on a tag other than the served one, or any lane-phase run is
  unfinished (its `lanes-recheck` exited 0 and `lanes-finish` hasn't passed it), and its message
  names re-running that lane's phases with `--tag` and rolling that lane back. `prepare` admits no
  `--ref` at a lane's commit: `migrations_between` refuses a rollback, deliberately (Amendment 1). A
  lane whose entry "Rollback for one lane" removed isn't checked, although its run files remain. A
  registered lane with no passed `--add` (no finished run), the placeholder included, holds no
  release and is skipped, not refused. `lanes-prepare` refuses on an unfinished run of its lane,
  and a run that wrote nothing to the lane's resource (a waiting, timed-out or refused
  `lanes-prepare`) doesn't block a re-run, which supersedes it (decision 1, "No release starts while
  a lane lags").
- The quiet-slot wait survives across HQ cycles: `lanes-prepare` keeps its deadline, an absolute
  UTC time, in the lane's run file, keyed by the change's `change_key`, and a re-run for the same
  change resumes against it, instead of one process waiting 6 hours. A run that finds the deadline
  passed marks the run `timed_out` and exits 4 (decision 1, "A routine tag update redeploys a
  lane's resource only in its quiet slot").
- How Coolify treats a service container whose `HEALTHCHECK` reports unhealthy, and what the lane
  image sets in place of the inherited one (decision 1).
- `pushed_digest()` in `scripts/deploy_orchestrator.py` refuses a run whose log holds more than
  one distinct `DIGEST:` line, and one whose `SHA_TAG:` lines name another image. So the lane
  image's verify step logs `LANE_DIGEST`, `LANE_SHA_TAG` and `LANE_REVISION`, never `DIGEST:` or
  `SHA_TAG:`, and the same increment reads `LANE_DIGEST:` by a parser change (decision 1, the
  verify report's item 13).

#### Increments

1. **The lane image and `sds-lane-placeholder`, with no lane.** Build the lane image in
   `Release image`, its verify step logging `LANE_DIGEST`, `LANE_SHA_TAG` and `LANE_REVISION`
   (Amendment 1). The image's scheduler is supercronic `v0.2.51`, downloaded as the
   `supercronic-linux-amd64` release asset and checked against its sha256 digest, never `:latest`,
   and the entrypoint starts it as `exec supercronic -json <crontab>` under `USER orchestrator`.
   Once the infraops-mcp-server fix (#164, `32a13046`) is live on the Mac and checked against
   Coolify (Q11), HQ creates the placeholder's resource, `sds-lane-placeholder`, through infraops,
   after confirming "Verify before increment 1". It has one service, which runs the lane image's
   entrypoint and whose compose entry maps `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}`
   like any lane's, so its scheduler has an empty table and it runs no launcher. It holds no token
   and maps no resource variable but `LANE_SCHEDULE_ENABLED` and `SDS_LANES_IMAGE_TAG`. Its first
   start is an ordinary `--add` with `--schedule-written false` (decision 1, "Adding a lane creates
   its resource"), and it shows how supercronic treats an empty crontab, unless the entrypoint
   writes a comment-only crontab and checks it with `supercronic -test`. It mounts one named volume,
   on which increment 1 verifies volume naming and ownership. The lane phases treat it as a lane
   with no token and no state: `lanes-finish` reads its image digest and its
   `LANE_SCHEDULE_ENABLED`, which must be `false`, and skips only the token check, for the
   placeholder by name. Its quiet slot is the landing-window check only, by name, because it has
   no Healthchecks check, so `lanes-prepare` and `lanes-recheck` read no check listing for it
   (decision 1, the quiet-slot bullet). `prepare`'s lag check covers it by name, because it has no
   registry entry: it skips it until its `--add` passes, and then compares its last finished run's
   tag with the served one, as for any lane. It stays until increment 2, which retires it. It isn't
   one of the eleven stateful lanes, so its mount isn't among the volume checks. Add the deploy
   command's three lane phases (`lanes-prepare`, `lanes-recheck`, `lanes-finish`), each acting on
   one lane's resource, for a tag update, a schedule change and an added lane: `lanes-prepare` waits
   for the lane's quiet slot across runs and exits 4 when the wait times out, and the phases print
   the whole compose write and record the file they sent, print the session's exact infraops calls
   and their inverse, and verify the session's reads of the redeployed container (image digest,
   schedule and state volume), and an `--add` refuses a compose entry whose schedule doesn't default
   to `false` (decision 1). Add `lane-run` with its variable list and its `--read` mode (decisions 1
   and 2). Give `sds-operator` its direct read grant on the read-only Healthchecks key (Q5), and add
   `f07b1368-…` to `.bws-secrets.toml` in the same change: the BWS secret-handling standard has a
   repository's manifest name every UUID it consumes, and the security scanner reports manifest
   drift.
2. **One observe-only lane first: `execution-watch`.** It has one identity, three secrets (the
   observer bearer, the alert token and its ping-URL secret), no GitHub credential, and state that
   must carry over (decision 1, "Lane state"), so it exercises the state copy first. Its interval
   (15 minutes) is the shortest of the lanes with no GitHub credential, so its check shows a failure
   fastest of the lanes that can move before any GitHub read credential exists. The unit verifier's
   10-minute interval is shorter, but it reads GitHub, so it waits for increment 3. It proves the
   identity, `lane-run`'s fixed start (decision 1), the dead-man ping and the cut-over procedure.
   Once `lanes-finish` has passed the `--add` of `sds-lane-execution-watch`, increment 2 retires
   `sds-lane-placeholder`: a release whose `prepare` stops naming it in the lag check is served
   first, and only then HQ deletes the placeholder's resource and its volume, as its own announced
   production act under D-20 (Amendment 1), framed as the delete in decision
   1's "Adding a lane creates its resource". The placeholder has no token to revoke and no registry
   entry to remove. It also writes the ADR-0055 amendment that lifts decision 4 for lane tokens and
   ping URLs, so the VM executor writes each from its inbox (Q8 for lane tokens, Q10 for ping URLs).
   That amendment is ADR-0055 amendment 19 (merged at `d485d051`, accepted in PR #628), which
   Amendment 2 follows here. As built, the inbox won't bind a first lane token or a ping URL: `bind`
   in `rotation_worker/inbox.py` binds a value only to a rotation that waits on a person's mint, and
   only when `same_identity` finds it for the same account as an old value that still answers, and
   neither has an old value. So the amendment extends the executor, and increment 2 waits on it.
   Verify before increment 2, in `rotation_worker` and on the VM instance:
   - Answered by ADR-0055 amendment 19, "What the code settles" (Amendment 2): the compose parse
     creates the row at create. The executor's Coolify client only `PATCH`es
     `/services/{uuid}/envs` and never `POST`s one (`rotation_worker/coolify.py`, `permits`), so a
     lane's `<LANE>_BWS_ACCESS_TOKEN` variable must exist in its own resource, `sds-lane-<lane>`,
     before the executor writes it, and the executor's registry needs that resource's uuid
     (Amendment 1). Coolify's parser makes a row with no value for each `${VAR}` with no default
     that a compose entry maps in `environment:`, when the create parses it, and a later parse
     doesn't overwrite the executor's value (the verify report,
     `docs/superpowers/reports/lanes-inc-1-verify-report.md`, items 1 and 7; amendment 19). The
     residual check is the first lane's create: the executor's `PATCH` answering 2xx confirms the
     row, and a 404 fails the issue unit with `CoolifyRefused` (amendment 19, "Open before
     building", "The token's row at create").
   - Answered by ADR-0055 amendment 19 item 6 (Amendment 2): the one adapter that writes a Coolify
     service variable, `postgres_role`, requires its `coolify-env` destination to have `redeploy`
     true and then calls `restart()` (`adapters/postgres_role.py`), which is
     `POST /services/{uuid}/restart` (`rotation_worker/coolify.py`). On a lane's resource that
     recreates the lane's container outside the quiet-slot rule (decision 1), stopping a running
     pass. So a `bws-lane-token` destination with `redeploy` true is refused before a claim, its
     distribute step writes the variable with one `PATCH` and never calls `restart()`, and the
     lane's container takes the new token at a lane phases' redeploy in its quiet slot. The BWS
     machine-token adapter hosts no destination (`adapters/bws_machine_token.py`,
     `hosts_destinations = False`); amendment 19 item 2 gives lane tokens their own class and type,
     `bws-lane-token`, whose provider hosts the lane's `coolify-env` destination.
   - Answered by ADR-0055 amendment 19 item 8 (Amendment 2): `other_mints` refuses to bind while
     two rotations wait on a person's mint (`rotation_worker/inbox.py`). The order is a mechanism:
     HQ makes the lane's ping-URL row due only after the lane token's `store` unit completes, so the
     ping URL's issue unit doesn't exist while the token's waits. If both wait at once, both binds
     are refused and nothing is written.
   - Answered by ADR-0055 amendment 19 items 2, 3 and 5, and its "What the code settles"
     (Amendment 2): Devon's hand paste of a ping URL (Q10) binds to an issue, a rotation's first
     half, which runs when the lane's ping-URL secret holds only the inbox's empty marker. The ping
     URL is registered with a new infraops class, `healthchecks-ping-url`, beside a new
     `bws-lane-token` class. The paste binds by its shape and by a read-only lookup of its check,
     never by a ping (ADR-0055 amendment 21; D-31 C, which superseded D-28): the URL is exactly the
     self-hosted instance's ping base followed by one UUID, no other keeper of its type holds it,
     and the read-only key finds its check by `unique_key` while refusing a random UUID's. The
     lookup reaches only the UUID's first half, so cut-over step 4 proves the full URL.
   - Answered by D-25 (ADR-0055 amendment 19 item 10; Amendment 2): before increment 7, the
     rotation proposer starts an early or leak reissue of a lane token, from the infraops findings
     it already reads (`cred.exposure-rotate` or `cred.rotation-requested`), and Devon's revision
     approval is the only person step.
   - Answered by D-26 (ADR-0055 amendment 19 item 10; Amendment 2): after a leak, Devon revokes the
     old token first, and the lane fails its passes on a dead token until the `--token` redeploy in
     its next quiet slot, which its dead-man check reports. Otherwise, Devon revokes after the
     `--token` run's `lanes-finish` passes and `lane-run --read` reads every listed secret `ok` on
     the redeployed container.
   - Answered: the executor has a destination that writes a Coolify service variable and one that
     writes a BWS secret. `coolify-env` takes `resource_type` (`rotation_worker/destinations.py`),
     and the client builds `/api/v1/services/{uuid}/envs` for `service`
     (`rotation_worker/coolify.py`, lines 63-65); `bws-secret` is a destination kind in
     `destinations.py` too.
3. **The other observe-only lanes:** landing ledger, pin watcher, deploy watcher, revision watcher,
   fork-sync watch, membership check and unit verifier, one at a time. Before the landing ledger
   moves, Devon creates both Apps with decision 2's permissions: the read-only App, installed on the
   members' repositories, the member record's repository, the standards repositories,
   intent-packages, factory-runner (if the verify item finds it needs the install) and each fork
   (Q6, Q9), and the "SDS lanes" App, installed only on intent-packages and the forks. He stores
   each App's private key in BWS (Q1, Q6, Q9). Each launcher then mints its lane's read token from
   the read-only App (decision 2). The increment changes
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
   its clone, which decision 1 builds only for the bump driver. Also verify that the rotation
   proposer's clone of infraops-mcp-server, which is public, works anonymously, with no token.
5. **The member-acting lanes:** inert landing, then estate landing, then the bump driver, then
   fork hardening, which pushes as the "SDS lanes" App (Q1) through `scripts/sds-git.sh`; its
   launcher's header and `fork_harden/cli.py`'s docstring are rewritten, and its code doesn't
   change. Each moves outside its landing
   window. Estate landing sends its Coolify token only over the tailnet today, so its increment
   decides how the container reaches Coolify without widening that. Verify before increment 5
   whether fork hardening's `hardened` push needs the "SDS lanes" App's `workflows: write`: the push
   carries any change `main` made to the fork's `.github/workflows/`, and GitHub refuses a push
   that creates or updates a workflow file from a credential without the workflows permission
   (`docs/operations/credentials.md`, #127, records the refusal for tokens). If it does, the
   widening goes to Devon before increment 5, and nothing is granted until he decides.
6. **Retire the Mac's lane plumbing:** the moved plists and their installers,
   `activate-checkout.sh`'s use by lanes, and the two Keychain items' lane role. The Keychain items
   themselves are retired only once no remaining reader names them. This increment starts only
   after the week bar has passed, because it ends the rollback path.
7. **The VM rotation executor moves to the VPS** under its own ADR-0055 amendment. This increment
   is last because it moves the root of the rotation design. A lane token the executor rotates
   reaches a container only at its next start, because the entrypoint and `lane-run` read the
   container's environment as it was then. Every start is a redeploy of the lane's own resource,
   `sds-lane-<lane>`, which recreates that lane's container alone (decision 1, Amendment 1).
   Bitwarden has no machine path to create or revoke an access token
   (`rotation_worker/adapters/bws_machine_token.py`, ADR-0055 amendment 12), so Devon mints each new
   lane token and revokes the old one, and the executor does what's between, taking the token from
   its inbox as it has since increment 2 (Q8). So the amendment must order each lane-token rotation
   as follows: Devon mints the new token, the executor writes it to `<LANE>_BWS_ACCESS_TOKEN` in the
   lane's resource, the resource is redeployed under the quiet-slot rule (the lane phases' two
   reads, on that lane's check), the new token is verified in the lane's container (`lane-run
   --read` over every secret `.bws-secrets.toml` lists for the lane, each `ok`), and only then does
   Devon revoke the old token. Revoking before that redeploy would leave the lane's container
   holding a dead token until the next quiet slot, which after a leak is the accepted order (D-26).
   The executor never redeploys a lane (ADR-0055 amendment 19 item 6; Amendment 2): until
   increment 7 the deploying session redeploys, by the lane phases' `--token` mode, and increment
   7's own ADR-0055 amendment decides whether the executor, which holds its own Coolify token,
   runs the redeploy or hands it to the deploying session. If it gives the executor the redeploy,
   it restores a machine read before the revoke; otherwise it states how decision 3's R10 rule is
   met (amendment 19 item 7).
8. **`com.devon.change-window` moves off the Mac** (D-9). infraops-mcp-server owns it:
   `scripts/change-window.sh`, `scripts/com.devon.change-window.plist.template` and
   `scripts/install-change-window-launchd.sh`. It runs `run-window` from infraops-mcp-server's
   own `change-mgr-cli` (`scripts/change-window.sh` calls `dist/cli/change-mgr-cli.js`) in
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

1. Create the lane's BWS identity and grants. Once the orchestrator serves the release that
   carries the lane's launcher, crontab line and compose file, HQ creates the lane's resource,
   `sds-lane-<lane>`, through infraops from that compose, without `instant_deploy`, so nothing
   starts (Q11, Amendment 1). Set the resource's two lane variables: the VM executor writes its
   token from its inbox after Devon mints it (Q8), and the session writes its schedule variable
   `false`. After the token binds, Devon copies the lane's ping URL from its check's page into the
   inbox by hand, and the executor writes the lane's ping-URL secret (Q10). The token and the ping
   URL take ADR-0055 amendment 19 item 8's order (Amendment 2): the VM root runs the keeper command
   for the lane's ping-URL secret and the lane token's keeper, each holding the inbox's empty
   marker; HQ lands the lane's registry rows naming them, in two changes, the token's row due for
   its issue and the ping URL's not yet due; the identity's creator grants the lane's account its
   secrets, the ping-URL secret included; Devon mints the token and pastes it into the inbox, and
   the executor stores it and writes `<LANE>_BWS_ACCESS_TOKEN`; after the token's `store` unit
   completes, HQ makes the ping URL's row due, and Devon pastes the URL. These three, the
   identity, its token write and the ping URL, are in place before the lane's `--add`. The lane has
   no lane-phase run until its `--add` passes, so it holds no release. Then deploy the lane's
   resource by the lane phases with `--add` in a quiet slot (decision 1). Then prove the
   identity in the lane's container: `lane-run --read` over every secret `.bws-secrets.toml` lists
   for the lane (each `ok`), one UUID it doesn't read (`refused`), and the launcher's `--dry-run`
   (decision 2). The service runs with `LANE_SCHEDULE_ENABLED=false`, so the container runs no pass
   on its own. Every container run in steps 1 and 2 is a one-off exec: the deploying session,
   through infraops's `vps_exec`, runs `docker exec` into the lane's running container, and runs
   the lane image's `lane-run` command with the launcher's arguments. `lane-run` starts the launcher
   with `BWS_ACCESS_TOKEN` as its one secret, copied from the service's own environment, beside
   `PATH`, a new empty `HOME` and its fixed list of non-secret variables (decision 1), without the
   token in a process argument. The container's environment holds only its own resource's
   variables (Amendment 1). So the run holds the lane's identity and no other, and the token
   never appears in the session's command or transcript. The scheduler's crontab line runs the same
   command. Then capture the lane's week-bar baseline (its flips and ping log, "The week bar"),
   before any run in step 2, because some of those runs ping the lane's real check. The ping log
   needs the Management API key, so Devon or an interactive session captures the baseline through
   the broad account, not HQ ("The week bar").

   ADR-0055 amendment 23 narrows this step's ping URL: once HQ makes the ping URL's row due, the
   executor reads the URL from the Healthchecks check listing and writes the lane's ping-URL
   secret. Devon pastes nothing, and the ping URL's issue raises no mint alert.
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
3. Set the lane resource's `LANE_SCHEDULE_ENABLED` to `true` by the lane phases with
   `--schedule true`, which the session redeploys in the lane's next quiet slot (decision 1). Until
   that redeploy, neither host runs the lane on a schedule, which is the safe direction. The gap is
   at most 6 hours when `lanes-prepare` finds a slot. At its 6-hour timeout it exits 4, the variable
   stays `false`, and the gap lasts until the session acts: HQ re-runs the lane phases or redeploys
   by hand. If the gap outlasts the check's grace, the check goes down; that down event falls before
   the week starts, so it counts in neither the baseline nor the week. The container's first
   scheduled pass pings the same check.
4. Verify that the check's last ping came from the container and that the Mac agent isn't loaded.
   That read is also the only proof of the full ping URL. The executor bound it by its shape and by
   a read-only lookup of its check (ADR-0055 amendment 21, under D-31 C, which superseded D-28;
   Amendment 2), and the lookup reaches only the UUID's first half: a wrong URL shows as the lane's
   own check going overdue, and the cut-over fails. Compare the two reporting runs from step 2 on
   exactly three things: the exit code, the set of record identifiers the lane reported (pull
   request numbers, unit ids, package names, record ids), and, for each identifier, its finding kind
   or proposed action. Nothing else in the output is compared, since timestamps, durations, run and
   request ids, temporary paths, hostnames and log prefixes differ by nature. If any of the three
   differs, the cut-over has failed, and the lane rolls back ("Rollback for one lane"). A scheduled
   pass isn't the comparison, because the Mac's last pass may already have acted on a record that
   the container's first pass then finds done.

The Mac agent and the container never run the same lane at once. Two landers, or two carriers,
would race on the same records.

#### Rollback for one lane

Set the lane resource's `LANE_SCHEDULE_ENABLED` to `false` by the lane phases with
`--schedule false`, and wait for `lanes-finish` to pass on the session's quiet-slot redeploy of that
resource. For the bump driver, also confirm no process in the container holds `pass.lock`
(`vps_exec` running `flock -n` on it in the lane's container). For a lane with state, copy its
volume's files back into the Mac's state directory with infraops's `vps_read_file`, then check the
copy as cut-over step 2 checks its own: compare the Mac's copied-back files with the volume's by
SHA-256, and check each Mac file's owner is the Mac user the agent runs as and its mode is the
volume file's. If any differs, copy that file again and set owner and mode again; the copy-back is
checked only when all match. Then `launchctl bootstrap` the kept plist. The Mac agent is
bootstrapped only after the redeploy, so the two never run the lane at once. Then confirm one Mac
run of the lane finishes and pings the lane's check.

The wait is at most 6 hours when `lanes-prepare` finds a slot, and lasts until HQ acts when it times
out. During it the container keeps running the lane on its schedule. For that interval, the ADR-0046
switches stop what acts through the orchestrator: `ORCHESTRATOR_ESTATE_LANDING_ENABLED` or
`ORCHESTRATOR_INERT_LANDING_ENABLED` for a lander, and `ORCHESTRATOR_DISPATCH_ENABLED` and
`ORCHESTRATOR_BUMP_MERGE_ENABLED` for the bump driver. Each is an orchestrator restart, so it's set
only while no dispatched run is live. No switch covers the bump and rotation proposers, the work
carrier, fork hardening or the rotation decomposer (which proposes decompositions as SYSTEM), and
the observe-only lanes change no record. For those, a rollback that can't wait for the quiet slot is
HQ's decision to redeploy the lane's resource by hand at once, accepting that it stops that lane's
running pass midway, and no other lane's.

The last step retires the lane's resource, once the lane is back on the Mac (Amendment 1). It's
three acts, in this order, and none starts before the one before it is confirmed:

1. Devon revokes the lane's BWS access token, his act. HQ confirms the revoke with the read
   decision 2 already names for a revoked token: `lane-run --read` through `vps_exec` in the lane's
   container, which is still running, under a new empty `HOME`, over every UUID
   `.bws-secrets.toml` lists for the lane, each of which must read `refused`. Until Devon revokes
   it, the resource and the registry entry stay, so the token is never live and untracked.
2. HQ deletes `sds-lane-<lane>` through infraops, as its own announced production act under D-20
   (Amendment 1), framed exactly as the create: `prod announce`, the
   `coolify_delete_service` call, a check at the source that the resource is gone
   (`coolify_list_services` no longer lists it, and `vps_docker_ps` shows no container of it), and
   `prod verified`. Coolify's delete only queues the deletion, so the check re-reads both for up
   to 10 minutes until they show it gone; if they don't, the check fails and HQ tells Devon. D-16
   doesn't cover the delete. The delete removes the resource's volumes and its executor-written
   token variable: infraops's tool calls `DELETE /services/{uuid}` with no query
   (`coolifyDelete('/services/${uuid}')`, `src/tools/services.ts` line 326 at infraops-mcp-server
   `32a13046`), and Coolify's `delete_volumes` defaults to `true` (`ServicesController.php`, 737
   and 789, at `v4.0.0-beta.473`).
3. HQ's pull request to `main` removes the lane token's entry from `.cred-consumers.toml`, the
   executor's registry, and merges. That drops the lane from `prepare`'s lag check (decision 1, "No
   release starts while a lane lags"). Between the delete and the merge, an executor write to the
   deleted resource fails without harm: its `PATCH /services/{uuid}/envs` names a resource that no
   longer exists, so Coolify refuses it and nothing is written.

The lane's BWS secret and its Healthchecks check stay, because the Mac agent pings the same check.
Moving the lane again is a new cut-over, with a new resource, a new volume name and a new token
write.

A lane's `--tag` rollback refuses a tag older than the lane's first image, because that image has
no launcher for the lane. That lane goes back to the Mac first, by this procedure, whose last
three acts delete its resource and then remove its registry entry, so the lane no longer holds any
release (Amendment 1).

The Mac agent runs from the main tree with the Keychain identities it had, which stay valid until
increment 6. A rollback takes no new credential and changes no orchestrator record, because each
launcher keeps its Mac path until increment 6 and the lane modules are the same in both places
(decision 2).

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
- The VPS holds every moved lane's BWS token as a Coolify environment variable, each in its own
  lane's resource, so a lane's container holds its own token and no other lane's (Amendment 1).
  Anyone with root on the VPS, or with a Coolify token holding `read:sensitive`, can read them all.
  This is the same boundary the orchestrator's own secrets have today.
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
- A lane runs only code the served orchestrator runs, except until its own quiet-slot redeploy:
  for up to 6 hours after `finish` when the wait finds a slot, and until HQ acts when it times out,
  that lane runs the previous release's code against the new orchestrator, so each release keeps
  that code compatible (decision 1). After an orchestrator rollback, which isn't `prepare`'s
  procedure, each lane runs code newer than the served orchestrator's until its own `--tag`
  rollback passes or, below its first image, it goes back to the Mac. A lane fix that reaches
  production needs an orchestrator release, as any other orchestrator change does.
- An orchestrator release waits until HQ resolves a lane timeout: `prepare` refuses while any lane
  lags the served tag or any lane-phase run is unfinished (decision 1), so one lane's timed-out
  redeploy holds every later release until HQ re-runs that lane's phases or rolls that lane back.
- Today's lane invariants that name the main tree or launchd move to their container
  equivalents. The relevant ones are "the scheduled lanes run the main tree's working copy", the
  launcher-credential rules, and the PATH rule for `uv`. Each increment rewrites the invariant it
  makes untrue.
- By Q8's and Q10's decisions, the VM rotation executor handles every lane's BWS token (Q8) and ping
  URL (Q10) from increment 2, when increment 2's ADR-0055 amendment lifts decision 4 for them. It
  can't mint or revoke a lane token: those are Devon's acts (ADR-0055 amendment 12). Devon mints and
  revokes; the executor transits, keeps and writes. Each lane token also keeps a stored copy, its
  keeper, in `Rotation / Keepers` on the VM (D-24, ADR-0055 amendment 19 item 1; Amendment 2), so
  the VM root holds every lane token at rest, beside every other rotated credential's keeper. A
  compromised executor could then use any lane's token and write any lane's token variable, in that
  lane's own resource (Amendment 1). It could also overwrite any lane's ping-URL secret, which
  blinds that lane's alarm: the lane's pings stop reaching its check.
- Lane tokens are registered from their first issue (increment 2), and none is rotated by age
  before ADR-0059 increment 7, which moves the executor that rotates them (Q3). A lane token's row
  is rotated by SDS from its first issue, so the age rule applies: at the registry's 365-day
  interval for `bws-lane-token`, a token first issued in November 2026 isn't due by age until
  November 2027 (ADR-0055 amendment 19 item 9; Amendment 2). That window is bounded: it must not
  outlast the credential registry's rotation interval for a lane token. If the increments would run
  past that interval, HQ asks Devon to rotate the lane tokens before the interval passes; the
  increments don't extend it. An early or leak reissue before increment 7 takes the path a first
  lane token takes from increment 2 (Q8), started by the rotation proposer from the infraops
  findings it already reads, an exposure (`cred.exposure-rotate`) or a requested rotation
  (`cred.rotation-requested`) recorded on the lane token's row (D-25, ADR-0055 amendment 19 item
  10; Amendment 2). Devon's revision approval is the only person step that starts it. Devon mints
  the new token and pastes it into the VM executor's inbox, and the executor carries out the
  reissue from its inbox and writes `<LANE>_BWS_ACCESS_TOKEN` in the lane's own resource,
  `sds-lane-<lane>`. The inbox binds the new token by its declared reach, not by the old token, so
  it binds after a leak's early revoke too, and no hand stage is needed (amendment 19 items 4 and
  10). After a leak Devon revokes the old token first; otherwise he revokes after the lane phases'
  `--token` run passes `lanes-finish` and `lane-run --read` (D-26). Until increment 7, the reissue
  retires the old token on HQ's report of those lane-phase reads, not on a machine
  `verify_consumers` check, for lane tokens only (D-27, amendment 19 item 7).
- Each App's private key reaches what its installation reaches, with its permissions, because a
  minted token is narrowed by its request but the key isn't. By Q1's and Q9's decisions, the
  "SDS lanes" App is installed only on intent-packages and the forks, so a leaked write-capable key
  lets its holder mint a token with `contents: write` on those repositories alone, and on no member
  repository, until the key is deleted on the App's settings page. The read-only App is installed
  on every repository the read-only set reads, member repositories included, with read
  permissions only, so its leaked key reads all of them and writes none. The cost of the split is
  a second App for Devon to create, install and rotate (Q9). Both keys are hand-rotated (Q4, Q9),
  so their rotation depends on Devon, as the rotation App's key does, and he rotates both in the
  same sitting (D-11 addendum). ADR-0055's hand-rotated set names who rotates its members, not
  how often, so the interval comes from the credential registry: each key is registered with a
  rotation interval in increment 3, which creates both, as a lane token is. If the keys would
  outlast that interval, HQ asks Devon to rotate them before the interval passes. Increment 3 also
  states how the keys' due date is reported without a rotation package being proposed for a key no
  executor handles.
- intent-packages `main` requires three status checks under classic branch protection with
  `enforce_admins` off (read 2026-11-05), which is what lets Devon's admin push land directly today.
  The "SDS lanes" App isn't an admin, so its direct push would be refused until intent-packages
  admits it, for example as a bypass actor of a ruleset that replaces the classic rule (verify
  before increment 4). That change is Devon's, in increment 4, and it doesn't add a required review:
  intent-packages requires none. Each fork's `hardened` branch forbids only force-push and deletion
  (ADR-0057 decision 7), so fork hardening's fast-forward push needs no branch-protection change.
  Whether it needs the App's `workflows: write` is verified before increment 5 (increment 5).
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
  for ping URLs), each into that lane's own resource, `sds-lane-<lane>` (Amendment 1). Increment
  7's moves the VM executor to the VPS, where it carries out each lane token's rotation between
  Devon's mint and his revoke (Q3). Each takes the next free amendment number when it's written.
  Increment 2's is ADR-0055 amendment 19 (merged at `d485d051`, accepted in PR #628), which
  Amendment 2 follows; increment 7's takes the next free number after it.

## Devon's decisions

Devon decided Q1 to Q5 on 2026-11-05 and Q6 to Q11 on 2026-11-06. Q9 also records D-14, D-17
and D-18, and Amendment 1 records D-19 and D-20, which he decided on 2026-11-06. ADR-0055
amendment 19 records D-24 to D-28, which he decided on 2026-11-07, and Amendment 2 follows them.
ADR-0055 amendment 21 records D-31 C, which he decided on 2026-11-07 and which superseded D-28.

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
the forks, and the read lanes mint from a second, read-only App, which is installed on
intent-packages too (Q9, HQ D-17).

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
redeploys and verifies (increment 7). The executor never redeploys a lane before increment 7, and
increment 7's own ADR-0055 amendment decides whether it takes the redeploy over from the deploying
session (ADR-0055 amendment 19 item 6; Amendment 2). It already writes Coolify variables with its
own Coolify token. ADR-0055 decision 4, as amendment 12 left it, says a BWS consumer token may be
handled only by an executor instance on the machine that already holds it, and never by the VM. Q8
lifts decision 4 for lane tokens earlier, at increment 2, so increment 7 only moves the executor.
The cost is accepted, stated as Q8 corrected it: Devon mints each token and revokes the old one, and
the executor transits and writes it, so a compromised executor could read every staged lane token,
write any lane's Coolify variable, and overwrite any lane's ping-URL secret. Until increment 7, lane
tokens are registered and none is due by age, and the executor carries out only the early and leak
reissues the rotation proposer starts (D-25, ADR-0055 amendment 19 items 9 and 10; Amendment 2).
By D-24 (amendment 19 item 1) each lane token also keeps a stored copy in `Rotation / Keepers`, so
the executor holds it at rest, not only in transit. The rejected options were the hand-rotated
set, which conflicts with Devon's "SDS can not do an all human rotation" (2026-10-21), and a new
confined rotation instance, which would add a third orchestrator identity.

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
tool argument or a process argument. By Amendment 1 (D-19) that variable is in the lane's own
resource, so the executor writes it with `PATCH /services/{uuid}/envs` on `sds-lane-<lane>`, and its
registry holds each lane's resource uuid; its authority gains nothing else. Devon mints every lane
token (ADR-0055 amendment 12) and pastes each value into the inbox. This lifts ADR-0055 decision 4
for lane tokens at increment 2, in that increment's ADR-0055 amendment, instead of at increment 7.
That amendment is ADR-0055 amendment 19, which also gives each lane token a stored copy in
`Rotation / Keepers` (D-24, its item 1; Amendment 2), so a lane token is kept at rest on the VM,
not only in transit through the inbox.
Each lane's ping URL takes the same inbox path into its BWS secret by Q10's decision, so the same
amendment lifts decision 4 for ping URLs. The rejected options were Devon pasting each value into
the Coolify UI, and a new infraops tool that takes a BWS UUID and writes the value server-side.
Increment 1 still moves no secret: its placeholder resource, `sds-lane-placeholder`, holds no
credential.

### Q9: the read lanes mint from a second, read-only App (HQ D-11, D-14 and D-17)

Option (b), decided by Devon on 2026-11-06 (HQ D-11): the twelve lanes of the read-only set mint
their read tokens from a second GitHub App with read permissions only, installed on the repositories
those lanes read, member repositories included. The "SDS lanes" App keeps `contents: write` and is
installed only on intent-packages and the forks, for the three pushing lanes, as Q1 decided. So a
leaked write-capable key no longer reaches any member repository. The cost is accepted: a second App
to create, install and rotate. Devon also decided on 2026-11-06 (HQ D-14) two read-only extensions.
The read-only App is installed on factory-runner and each fork as well as the repositories Q6 (D-8)
named and D-14's and D-17's wording names again (the members' repositories, the member record's
repository and the standards repositories), and
increment 3 lists the exact set and checks whether factory-runner, which is public, needs the
install at all. And the bump proposer reads the read-only App's key for its read mint, a thirteenth
reader beside the twelve lanes of the read-only set. Devon also decided on 2026-11-06 (HQ D-17) a
third read-only extension: the read-only App is installed on intent-packages, which the read lanes
also read, so they read it with the read-only key and not the "SDS lanes" App's. The read-only
App's private key joins ADR-0055's hand-rotated set by D-6's rule (Q4). Devon confirmed on
2026-11-06 (HQ D-18: "Yes") the read-only App's permission list: Administration, Issues, Actions,
Pull requests, Contents and Metadata, all read-only, and nothing else. By Devon's D-11 addendum
in the interactive session on 2026-11-06 ("For D-11, I concur with your recommendation"), it
rotates on the same interval as the "SDS lanes" App's key, and Devon rotates both in the same
sitting.
Creating and installing the read-only App is Devon's act, as for the "SDS lanes" App. The rejected
option was (a), one App for both, whose leaked key could mint `contents: write` on every member
repository it's installed on.

### Q10: Devon pastes each lane's ping URL by hand (HQ D-13)

Decided by Devon on 2026-11-06 (HQ D-13): he copies each moved lane's Healthchecks ping URL from
its check's page into the VM rotation executor's inbox by hand, one at a time, as his step in that
lane's cut-over from increment 2 on, and the executor writes the lane's ping-URL secret (Q8). The
rejected option was a machine path, a new inbox write that takes no value as an argument, which
would add mechanism to the executor in increment 2.

### Q11: Devon accepts the ADR, and HQ creates the lanes' resources (HQ D-16)

Decided by Devon on 2026-11-06 (HQ D-16): "A. Accept ADR-0059 after one fresh clean Opus review on
the Accepted bar, once the D-14 recording PR merges. HQ creates the sds-lanes resource through
infraops, unless there is a reason it should not, and if so HQ tells Devon." So the ADR is accepted
on HQ's clean Opus review of its Accepted text. Amendment 1 (D-19) replaces the one `sds-lanes`
resource with one resource per lane, so HQ creates each lane's resource, `sds-lane-<lane>`, and
increment 1's `sds-lane-placeholder`, through infraops (`coolify_create_service`), each as its own
announced production act. If HQ finds a reason it shouldn't, it tells Devon instead of creating the
resource. At Coolify `v4.0.0-beta.473`, Coolify's create endpoint refuses a request that carries
both `type` and `docker_compose_raw` and accepts `docker_compose_raw` only base64-encoded
(`app/Http/Controllers/Api/ServicesController.php`, 347-351 and 578-595). infraops-mcp-server
#164 (squash `32a13046`) fixed the tool to match: it takes exactly one of `type` or
`docker_compose_raw`, sends `type` only when given, and base64-encodes the compose
(`src/tools/services.ts`, 213 and 234-239, at `32a13046`). The fix is live on the Mac: Devon synced
the infraops checkout on the Mac and reconnected infraops on 2026-11-06, and HQ confirmed that the
checkout contains `32a13046` and that the live `coolify_create_service` schema takes exactly one of
`type` or `docker_compose_raw`. Increment 1 checks the live tool against Coolify before HQ's first
create. Increment 1 decides only how a resource's first compose is recorded as the one its first
lane-phase run replaces ("Verify before increment 1").

## Amendment 1 (2026-11-06): one Coolify compose resource per lane

- **Status:** Accepted on merge. HQ merges it only after a clean Opus whole-branch review (HQ
  D-12, limit 3). The ADR's own status is unchanged.
- **Decided by:** Devon, on 2026-11-06 (HQ D-19 and D-20).
- **Relates to:** decision 1 (the runtime), "Verify before increment 1", increments 1, 2 and 7,
  the cut-over and rollback, Q8 and Q11

### Why

Increment 1's read-only verify refuted the first item of "Verify before increment 1": a resource
variable doesn't reach only the containers whose compose entry maps it. At Coolify
`v4.0.0-beta.473`, each start writes every variable of a service resource into one `.env` file in
its work directory (`app/Models/Service.php`, 1588-1600: every row of the resource's
`environment_variables()`, unfiltered), and the parser adds `env_file: .env` to every service of the
compose, with no opt-out (`bootstrap/helpers/parsers.php`, 2694-2702). So with one `sds-lanes`
resource, every lane container would hold every lane's `<LANE>_BWS_ACCESS_TOKEN`. Decision 1 named
that outcome as the one that defeats one identity per lane and stops increment 1 for a decision
(the verify is `docs/superpowers/reports/lanes-inc-1-verify-report.md`, worker W-28, items 1, 2, 6,
7, 9, 10, 11, 13 and 14).

HQ asked Devon (D-19) to pick one of three: (a) one Coolify compose resource per lane, so each lane
has its own variables; (b) keep lane tokens out of Coolify variables; (c) accept that every lane can
read every token. Devon answered **A** (2026-11-06T20:23 UTC, Slack).

HQ then asked Devon (D-20) whether HQ may delete a lane's own Coolify resource, which also deletes
its volume, as an announced production act, only in three cases: undoing a failed first start of a
lane, retiring the placeholder lane once the first real lane runs, and the last step of moving a
lane back to the Mac (after its state is copied back and checked, one Mac run pings its check, and
Devon has revoked the lane's token). Devon answered **Yes** (2026-11-06, Slack and the interactive
session). D-20 covers those three deletes and no other.

### What changes

1. **One resource per lane.** Each moved lane is its own Coolify compose resource,
   `sds-lane-<lane>`, with one service. Each resource has its own variables: its own
   `SDS_LANES_IMAGE_TAG`, its own `<LANE>_BWS_ACCESS_TOKEN` and its own `LANE_SCHEDULE_ENABLED`.
   Each lane has its own chain of lane-phase runs and its own quiet slot, which reads only that
   lane's Healthchecks check beside the landing window. A tag update, schedule change or restart
   recreates only that lane's container. Increment 1's placeholder is its own resource,
   `sds-lane-placeholder`; it has no Healthchecks check, so for it alone, by name, the quiet slot is
   the landing-window check only.
2. **HQ creates each resource (D-16 under D-19).** HQ creates each lane's resource through infraops
   (`coolify_create_service`), each as its own announced production act. infraops-mcp-server #164
   (squash `32a13046`) fixed that tool for a raw-compose service at this Coolify version (Q11). The
   fix is live on the Mac: Devon synced the infraops checkout on the Mac and reconnected infraops
   on 2026-11-06, and HQ confirmed that the checkout contains `32a13046` and that the live
   `coolify_create_service` schema takes exactly one of `type` or `docker_compose_raw`. Increment 1
   checks the live tool against Coolify before HQ's first create.
3. **The executor writes each token into its lane's resource (D-10 (b) under D-19).** The VM
   rotation executor writes each lane's token into that lane's own resource
   (`PATCH /services/{uuid}/envs` on `sds-lane-<lane>`), so its registry needs each lane's resource
   uuid. Its authority gains nothing else.
4. **`prepare` admits no `--ref` lag, and the rollback refusal stays.** `migrations_between` refuses
   a served commit that isn't an ancestor of the built one (`scripts/deploy_orchestrator.py`,
   361-381), deliberately (`docs/operations/deploy.md`, line 77). `prepare` doesn't admit a `--ref`
   at the lanes' commit. An orchestrator rollback isn't `prepare`'s procedure, and each lane rolls
   back by `lanes-prepare --tag` on its own resource.
5. **A compose entry per release and a rollback compose no longer apply.** Adding a lane creates its
   resource and changes no other lane's compose, so the rule that a release carries at most one new
   lane's compose entry, and the question of how a rollback compose keeps each lane cut over since,
   are deleted with their verify items. One rule replaces them: a lane's `--tag` rollback refuses a
   tag older than the lane's first image, because that image has no launcher for it, and that lane
   goes back to the Mac first ("Rollback for one lane"). That procedure checks the state it copies
   back to the Mac by SHA-256, owner and mode before it bootstraps the Mac agent, as the cut-over
   checks its copy. After the checked copy-back and one Mac run that has finished and pinged the
   lane's check, that procedure ends in three ordered acts: Devon revokes the lane's access
   token and HQ confirms the revoke; HQ deletes the lane's resource, `sds-lane-<lane>`, with its
   volumes (Coolify's `delete_volumes` defaults to `true`) and its executor-written token variable;
   then HQ's pull request removing the lane token's entry from `.cred-consumers.toml`, the
   executor's registry, merges. Until Devon revokes the token, the resource and the entry stay.
   Each delete of a lane's resource, whether this one, the inverse of a failed `--add` or the
   placeholder's retirement, is HQ's own announced production act, framed as the create
   (`prod announce`, the act, a check at the source, `prod verified`), under D-20; D-16 doesn't
   cover it. The lane's BWS secret and its Healthchecks check
   stay. Moving the lane again is a new cut-over, with a new resource, a new volume name and a new
   token write. `prepare`'s lag check reads persisted per-lane run files, which outlive the
   resource, so it is scoped to the lanes registered at the time of the check, and removing the
   entry drops the lane from it. A registered lane with no passed `--add` holds no release and is
   skipped. `sds-lane-placeholder` has no entry: the check covers it by name until increment 2.
   Its first start is an ordinary `--add` with `--schedule-written false`, and `lanes-finish` skips
   only the token check for it, by name. Increment 2 retires it once `sds-lane-execution-watch` is
   verified and a release whose `prepare` no longer names it is served, by the delete alone.
6. **The timeout takes no new credential.** `lanes-prepare` records the 6-hour deadline as an
   absolute UTC time in the lane's run file, keyed by the change's `change_key`. When the deadline
   has passed, it marks the run `timed_out`, prints one line, and exits 4, its "timed out" code. The
   session that runs the deploy command raises the alert through its own channel. The deploy command
   sends no alert and reads no alert-hub token.
7. **The lane digest's log lines.** The lane image's verify step logs `LANE_DIGEST`, `LANE_SHA_TAG`
   and `LANE_REVISION`, never `DIGEST:` or `SHA_TAG:`, because `pushed_digest` refuses a run whose
   log holds more than one distinct `DIGEST:` (`scripts/deploy_orchestrator.py`, 477-487).
8. **The scheduler.** supercronic `v0.2.51`, downloaded in the lane image build as the
   `supercronic-linux-amd64` release asset and checked against its sha256 digest, never `:latest`,
   started by the entrypoint as `exec supercronic -json <crontab>` under `USER orchestrator`. How it
   treats an empty crontab is a production check in increment 1, or the entrypoint writes a
   comment-only crontab and checks it with `supercronic -test`.
9. **Compose writes and token writes.** Coolify normalizes a PATCHed compose
   (`Yaml::dump(Yaml::parse(…))`, `ServicesController.php`, 993), so the lane phases record the
   file they sent and never compare Coolify's stored text with it byte for byte. A lane token never
   goes through infraops's service env tools, whose responses carry the written row's `value`.

### What stays

Every rule of decision 1 that isn't about the shared resource stays: the image pinned to the served
orchestrator, the three phases and their inverse writes, the state volumes and their recorded names,
the quiet-slot conditions, `lane-run`'s list, and the bans on reading a resource's variable listing
and on `coolify_get_service`. Amendment 1 adds no credential, scope, permission, BWS grant or
Healthchecks key. The D-7 grant (Q5) stays as it is.

### Passages this amendment changed

Decision 1's runtime, pinning, phases, quiet-slot, lag, lane-state, kill-switch, schedule,
per-resource-variable and adding-a-lane bullets; decision 2's token bullet and its
`.bws-secrets.toml` bullet; decision 3's `sds-deadman.sh` bullet; "Verify before increment 1";
increments 1, 2 and 7; the cut-over and "Rollback for one lane", which gains its delete step; the
Consequences on the VPS's tokens, the lanes' lag, the release hold and the executor; Dependencies on
ADR-0055; Q8 and Q11.

## Amendment 2 (2026-11-07): lane tokens and ping URLs as ADR-0055 amendment 19 issues them

- **Status:** Accepted on merge. HQ merges it only after a clean Opus whole-branch review (HQ
  D-12, limit 3). The ADR's own status is unchanged.
- **Decided by:** Devon, on 2026-11-07 (HQ D-24 to D-28), recorded in ADR-0055 amendment 19
  (merged at `d485d051`, PR #626, and accepted in PR #628). This amendment follows that one and
  decides nothing of its own.
- **Relates to:** decision 1 (the lane phases, the per-resource-variable bullet), decision 2 (the
  token bullet and the `.cred-consumers.toml` bullet), decision 3 (the ping-URL secret),
  increments 2 and 7, the cut-over, the Consequences on the executor and on lane-token rotation,
  Dependencies on ADR-0055, Q3 and Q8

### Why

Increment 2 writes the ADR-0055 amendment that lifts decision 4 for lane tokens and ping URLs
(increment 2; Dependencies, ADR-0055). That amendment is ADR-0055 amendment 19. It extends the VM
rotation executor to issue a first lane token and a first ping URL from its inbox, and it changes
how a lane token is reissued and retired before increment 7. Devon decided its five questions on
2026-11-07: D-24 (each lane token keeps a stored copy: "Keep a stored copy"), D-25 (the rotation
proposer starts an early or leak reissue: "The proposer as recommended"), D-26 (the order of the
revoke: "I approve the proposed answer"), D-27 (the departure from decision 3's R10 rule, for lane
tokens only: "Accepted") and D-28 (a ping URL binds by shape alone: "No Key"). Devon's D-31 C
(2026-11-07, ADR-0055 amendment 21) later superseded D-28: a ping URL binds by its shape and by a
read-only lookup of its check.

Several passages of this ADR then contradicted amendment 19: the lane phases had no mode that
redeploys a lane after a token write, increment 7 left the redeploy's actor open, increment 2's
verify list held questions amendment 19 answers, and the Consequences said a leaked lane token is
staged by hand and that lane tokens are unrotated until increment 7. This amendment matches the
ADR to amendment 19 and cites amendment 19's items for each change. Where amendment 19 leaves a
point to increment 2's code, this amendment carries it as an increment-2 verify item and states no
behavior for it.

### What changes

1. **The lane phases gain a token-change mode, `--token` (amendment 19 item 6 and "Open before
   building").** As accepted, the phases take only `--tag`, `--schedule` and `--add`, and
   `lanes-finish` passes only "the change and no other", so none redeploys a lane after the
   executor writes a reissued token. Increment 2 adds `--token`, built and verified before
   `execution-watch` holds a real token, so a leak's path works from the first lane. Its
   definition, from amendment 19 item 6:
   - **What it writes.** The lane's compose at its running tag, as `--schedule` does, and no
     variable, because the executor has written the token. The command prints the calls and the
     deploying session makes them (`docs/operations/deploy.md`, "The lane phases"): the existing
     `coolify_update_service` and `coolify_deploy`, under the same quiet-slot reads as every other
     mode.
   - **What `lanes-finish` checks.** It passes when the image digest, the schedule and the volume
     are unchanged, the container's `BWS_ACCESS_TOKEN` is non-empty, and `lane-run --read` reads
     every secret the lane lists `ok`. The `lane-run --read` result is a new `lanes-finish` input:
     `finish_mismatches` (`scripts/deploy_orchestrator.py`) checks only the digest, schedule, token
     presence and volume.
   - **How a failed run closes.** A mismatch at `lanes-finish` exits 2 and leaves the run
     `unfinished`, and an `unfinished` run refuses the lane's next `lanes-prepare` and every
     orchestrator release (`require_lanes_current`). `--token` has nothing to undo, because its
     compose is the one the lane already ran, so its `lanes-finish --restored` form makes no
     inverse write: it closes the run as failed, with the mismatches recorded as its reason in the
     run file, and the run never becomes the lane's record. The lane can then take a retry
     `--token`, a `--tag`, or a rollback to the Mac. HQ records the failure in its report of the
     reads (amendment 19 item 7), and the reissue's revoke stays unresolved.

   The rest of the mode is settled in increment 2's code ("Verify in increment 2").
2. **The executor never redeploys a lane (amendment 19 item 6).** A `bws-lane-token` destination
   with `redeploy` true is refused before a claim, and its distribute step writes the variable with
   the client's one `PATCH` and never calls `restart()`. The lane's container takes the new token
   only at its next start, which is a lane phases' redeploy in that lane's quiet slot. Until
   increment 7, the deploying session runs that redeploy by `--token`. Increment 7's own ADR-0055
   amendment decides whether the executor runs the redeploy or hands it to the deploying session;
   if it gives the executor the redeploy, it restores a machine read before the revoke, and
   otherwise it states how decision 3's R10 rule is met (amendment 19 item 7).
3. **Increment 2's open questions are answered.** Each of increment 2's verify items is marked
   answered, with its source:
   - the token variable's row: the compose parse creates it at create, confirmed by the first
     lane's create ("What the code settles" and "Open before building");
   - `redeploy` false: item 6;
   - two waiting mints: item 8, an order HQ makes by when it makes the ping URL's row due;
   - what makes the executor wait on a ping URL's paste: items 2, 3 and 5, an issue plan on a
     keeper that holds the inbox's empty marker, under a new `healthchecks-ping-url` class;
   - the ping URL's bind: by shape alone, never by a ping, proven at cut-over step 4 (D-28, item 5;
     ADR-0055 amendment 21, under D-31 C, replaced it with the shape and a read-only lookup of the
     check, and step 4 proves the full URL);
   - the actor for an early or leak reissue: the rotation proposer (D-25, item 10);
   - the leak revoke order: after a leak Devon revokes first; otherwise after the `--token` run's
     `lanes-finish` passes and `lane-run --read` reads every listed secret `ok` (D-26, item 10).
4. **Early and leak reissue, and retirement, before increment 7 (amendment 19 items 4, 7, 9 and
   10).** A lane token's row is rotated by SDS from its first issue, so the age rule applies, and
   at 365 days none is due by age before November 2027 (item 9). Before increment 7 the rotation
   proposer starts an early or leak reissue from the infraops findings it already reads
   (`cred.exposure-rotate` or `cred.rotation-requested`), Devon's revision approval is the only
   person step, and the executor carries the reissue out from its inbox (D-25, item 10). The new
   token binds by its declared reach, so it binds after a leak's early revoke, and no hand stage
   is needed (items 4 and 10). Until increment 7, a reissue retires the old token on HQ's report of
   the lane phases' reads, not on a machine `verify_consumers` check, for lane tokens only (D-27,
   item 7). The leak path still ends in revoke and reissue.
5. **Each lane token keeps a keeper in `Rotation / Keepers` (D-24, amendment 19 item 1).** ADR-0059
   accepted the token's transit through the executor (Q3, Q8); D-24 adds the value at rest on the
   VM, and it is a standing answer for every later lane token. Decision 2's token bullet, Q3, Q8 and
   the Consequences on the executor carry the cross-reference.
6. **Who creates a lane's ping-URL secret: the VM root, with the keeper command, in the increment
   that moves the lane (amendment 19 items 1, 3 and 8).** Amendment 19 item 1 says the keeper
   command creates the ping-URL secret in `Rotation / Keepers`, and its item 8 step 1 has the root
   run that command in the lane's cut-over, holding the inbox's empty marker. Decision 3 says the
   increment that moves each lane creates the secret. Both hold: the increment's cut-over is where
   the root runs the command. Decision 3 now says so.
7. **The cut-over takes amendment 19 item 8's order.** Cut-over step 1 now names the keeper
   command, the two registry-row changes, the grants (the ping-URL secret included, while it holds
   the marker), the token's mint and bind, and HQ making the ping URL's row due only after the
   token's `store` unit completes. Step 4's read that the check's last ping came from the container
   is also the proof of the ping URL (D-28; under ADR-0055 amendment 21 it's the proof of the full
   URL, since the lookup reaches only the UUID's first half).

### Verify in increment 2

Each item is verified in increment 2, before its code merges. The first five are the sub-items of
amendment 19's "Open before building" item for the `--token` mode, the sixth is its "A pass on a
dead token and the quiet slot" item, and the last two come from the reviews of PR #626.
Amendment 19's other "Open before building" items (the ping URL's check read, since answered by
ADR-0055 amendment 21 under D-31 C, the token's row at create, what `_REACH` lists for a lane
account, and what makes a first issue due) are increment 2's too.

- **The mode's own `change_key` (increment 2)**, beside `tag:`, `add:` and `schedule:`.
- **Its `lanes-finish --restored` form (increment 2)**, which closes a failed run with no inverse
  write (What changes, item 1).
- **The `lane-run --read` result as a `lanes-finish` input (increment 2)** (What changes, item 1).
  Increment 2's code settles its form.
- **A `--tag` run after the executor's `PATCH` (increment 2)** also loads the new token, without
  `--token`'s reads, so the revoke's precondition needs those reads re-run on that container.
- **`--token` refuses `sds-lane-placeholder` (increment 2)**, which holds no token.
- **A pass on a dead token and the quiet slot (increment 2).** Whether a lane pass that fails on a
  revoked token leaves its check `started`, which would hold off the leak's `--token` redeploy (the
  quiet slot waits while `started` is `true` and `status` isn't `down`).
- **A closed failed `--token` run has a distinct status (increment 2).** `lanes-finish` closes an
  `unfinished` run as `finished` or `restored` (`scripts/deploy_orchestrator.py` line 1017 at
  `d485d051`; line 1016 holds the run file's other statuses, `waiting`, `refused`, `timed_out` and
  `superseded`). A failed `--token` run restored nothing, so it closes with a status of its own, not
  `restored`, which `last_finished` and `prepare`'s lag check (`require_lanes_current`) ignore, as
  they ignore `restored` today (`last_finished` takes only `finished`). The failure stays visible:
  the closing call doesn't report the run as a success, and HQ reports it.
- **`lanes-finish`'s failure message fits the mode (increment 2).** On a mismatch, `lanes-finish`
  prints the run's inverse and raises "Run the inverse printed on stderr now" (`_lanes_finish`). A
  `--token` run has no inverse write, so its message names its own close: `lanes-finish --restored`,
  which records the mismatches and closes the run as failed.

The ping-URL secret's creator isn't carried: amendment 19's text settles it (What changes, item 6).

### What stays

Every rule of decision 1 that isn't about the token-change mode stays: the image pinned to the
served orchestrator, the three phases and their inverse writes for `--tag`, `--schedule` and
`--add`, the quiet-slot conditions (the quiet-slot bullet's "nothing staged" sentence, qualified for
`--token`), the 6-hour deadline, the lag check, and the bans on reading a resource's variable
listing and on `coolify_get_service`. Devon still mints every lane token and revokes every old one
(amendment 19 item 9; ADR-0055 amendment 12). Increment 7 still moves the VM executor under its own
ADR-0055 amendment. Amendment 2 adds no credential, scope, permission, BWS grant or Healthchecks
key: the lane-token keepers are amendment 19's, by D-24, and this amendment only cross-references
them.

### Passages this amendment changed

The header's `Decided by` and `Relates to`; decision 1's lane-phase bullet (the `--token` change,
`lanes-prepare`'s writes and `lanes-finish`'s close), its quiet-slot bullet (the "nothing
staged" sentence, qualified for `--token`) and its per-resource-variable bullet;
decision 2's token bullet and its `.cred-consumers.toml` bullet; decision 3's ping-URL secret
bullet; increment 2 (its ADR-0055 amendment and its verify items, each marked answered) and
increment 7; cut-over steps 1 and 4; the Consequences on the executor and on lane-token rotation;
Dependencies on ADR-0055; the "Devon's decisions" introduction, Q3 and Q8.

## Amendment 3 (2026-11-10): the fork-sync watch and fork hardening stay on the Mac

- **Status:** Accepted, 2026-11-10. Devon accepted this text on gate G-22: "G-22: accept 1. yes
  2. No 3. No". He sent it from Slack on 2026-11-10 and restated it in an interactive session at
  about 15:40 UTC, which is the record; the answers are recorded under "Open before building".
  Acceptance followed a clean Opus whole-branch review of PR #648 at `01ca2782` (HQ D-12, limit
  3). Devon decided the question on 2026-11-10 (HQ D-35, option 1): "I think the
  fork-sync and fork-harden should stay on the mac. It is really not part of SDS as much as it is a
  way for me to minimize supply chain risks to my development environment." Then: "Yes, record it
  as a decision with option 1". The amendment takes effect when Devon accepts this text; HQ
  doesn't merge it. The ADR's own status is unchanged.
- **Decided by:** Devon, on 2026-11-10 (HQ D-35).
- **Relates to:** decision 1 (the clone and cache bullet, the lane-state bullet, the kill-switch
  bullet), decision 2 (the GitHub-credential, git-helper, read-mint and pushing-lane bullets),
  decision 3 (the `sds-deadman.sh` and check-provisioning bullets), decisions 4 and 5, increments
  3, 5 and 6, the cut-over's reporting-mode step, "Rollback for one lane", the week bar, the
  Consequences on key readers and on the forks' branch protection, Q1, Q6 and Q9

Line numbers in this amendment are this file's at orchestrator `531d42c2`, before the amendment
was appended. The amendment appends only: no passage before it is rewritten, so each line cited
here still holds the text it names. Where this amendment and an earlier passage disagree, this
amendment governs once it's accepted.

### Context

Decision 5 classifies `fork-harden` (line 773) and `fork-sync-watch` (line 774) as Moves:
fork hardening into increment 5, pushing the forks' `hardened` branches as the "SDS lanes" App
(Q1), and the fork-sync watch into increment 3, reading through a read token minted from the
read-only App (Q6, Q9). Decision 5 classifies `tool-installer` as Stays because "its subject is
the Mac" (line 783).

HQ asked Devon (D-35) whether both lanes stay on the Mac instead. Option 1, the one he chose, reads:
"Reclassify both as Stays in an ADR-0059 amendment, on tool-installer's grounds: their subject is
this Mac's tool chain, a personal supply-chain control for Devon's development environment. Phase
1's bar ('no lane that acts on a member runs on the Mac') excludes lanes whose subject is this
Mac's tool chain. The forks stay members, so the landing lanes keep their Dependabot pull
requests."

The last sentence of that option needs one correction, which HQ confirmed against the sources on
2026-11-10. The forks' update pull requests aren't Dependabot's. None of the five forks (`rtk`,
`claude-octopus`, `rea`, `n8n-as-code` and `n8n-manager`) carries `.github/dependabot.yml`,
because `fork-sync baseline` removes upstream's whole `.github/`
(`docs/operations/onboarding-a-fork.md`, line 16). Their update pull requests are the fork-sync
lane's rolling sync pull requests, authored by `fork-secure-upstream-sync[bot]`, which the inert
landing lane lands (`onboarding-a-fork.md`, step 10; change-manager `app/deploy_policy.py`, lines
612-630). A Dependabot pull request on a fork fails the required `security review` check, which
keeps it from landing (`onboarding-a-fork.md`, lines 507-509; ADR-0056, line 93). Decision item 4
states the corrected form.

The two lanes and the tool installer form one chain on this Mac. The fork-sync lane brings each fork
up to its upstream's newest release, the inert landing lane lands it, fork hardening rebuilds each
fork's `hardened` branch from its landed `main` at 03:00, and the tool installer installs from
`hardened` at 03:15 (`scripts/run-fork-harden.sh`, header; ADR-0042; ADR-0057). The fork-sync
watch says whether any fork in that chain is stuck (`scripts/run-fork-sync-watch.sh`, header).
What the chain protects is the set of tools installed on this machine.

### Decision

1. **Both lanes are reclassified as Stays, on the tool installer's grounds.** Their subject is this
   Mac's tool chain: they are Devon's supply-chain control over the tools he installs on his
   development machine, not SDS work on its members' production. Decision 5's two rows read:

   | Launch agent | Disposition | Reason |
   |---|---|---|
   | `fork-harden` | Stays | Hardens the forks this machine installs tools from, so its subject is this Mac's tool chain (Amendment 3, D-35). |
   | `fork-sync-watch` | Stays | Watches the sync chain of the forks this machine installs tools from, so its subject is this Mac's tool chain (Amendment 3, D-35). |

   Fifteen launch agents move and five stay. The moving lanes are the 19 `run-*.sh` launchers
   less `activation-sweep`, `tool-installer`, `fork-harden` and `fork-sync-watch`. The five that
   stay are decision 4's three (the tool installer, the Keychain rotation instance and the
   machine-activation sweep) and these two. The eleven stateful lanes are unchanged, because
   neither of the two keeps state (decision 1, line 351). Of the six stateless moving lanes, four
   remain: change proposer, deploy watcher, landing ledger and membership check. Decision 4's
   opening sentence, "Three agents stay, all because their subject is the Mac itself" (line 741),
   reads as five, and these two are added to its list.

2. **What leaves increments 3 and 5.** Each passage that moves, re-identifies or re-credentials
   either lane is withdrawn for that lane. The lane keeps what it does on the Mac today. Where
   the passage also covers other lanes, it still applies to them.

   **Fork hardening.**

   | Passage | Disposition |
   |---|---|
   | Decision 1, the lane image copies `scripts/sds_scope.py`, "which `run-landing-ledger.sh` and `run-fork-harden.sh` run" (line 107) | Withdrawn for fork hardening. The image carries `sds_scope.py` for the landing ledger. On the Mac, `run-fork-harden.sh` runs it from the main tree, as it does today. |
   | Decision 1, fork hardening clones the forks each pass (lines 309-310), its bare caches (line 323), and "increment 5 may point fork hardening's `--cache` into a volume" (lines 326-327) | Withdrawn. Fork hardening keeps its Mac cache under `HOME` (`fork_harden/cli.py`, `DEFAULT_CACHE`). |
   | Decision 1, kill switches: fork hardening among the lanes with no switch (lines 399-401) | Still applies on the Mac. Fork hardening has no ADR-0046 switch; the passage no longer names a moved lane. |
   | Decision 2, "Three lanes push through git's credential" and fork hardening's `gh auth git-credential` (lines 535-538) | Two moving lanes push: the bump and rotation proposers. Fork hardening keeps `gh auth git-credential` on Devon's login (`run-fork-harden.sh`, `fork_harden/cli.py`). |
   | Decision 2, "Fork hardening needs neither" (lines 559-561) and the "Fork hardening" bullet: increment 5 mints the "SDS lanes" App token, calls `sds_git_credential`, and rewrites the launcher header and `fork_harden/cli.py`'s docstring (lines 567-571) | Withdrawn. No mint, no `scripts/sds-git.sh` call, and no rewrite: the header and docstring describe the Mac credential, which stays. |
   | Decision 2, "The three pushing lanes push as the 'SDS lanes' GitHub App", naming `fork-harden` and the forks (lines 637-640), and the `workflows: write` verify before increment 5 (lines 640-641) | Two pushing lanes, `bump-proposer` and `rotation-proposer`, minting only for intent-packages. The `workflows: write` verify is withdrawn: fork hardening doesn't push as the App. |
   | Decision 2, key readers: "The 'SDS lanes' App's key is read by the three pushing lanes" and "fifteen lane identities read one or both" (lines 646-649) | Two lane identities read the "SDS lanes" App's key; the read-only App's key is read by twelve (eleven of the read-only set and the bump proposer); thirteen read one or both. |
   | Decision 3, `install-fork-harden-launchd.sh`'s private upsert "is replaced by `sds_upsert_check` when fork hardening moves" (lines 723-724), and each lane's increment provisioning its check through `sds_upsert_check` (lines 733-734) | Withdrawn for fork hardening. No increment replaces the private upsert. |
   | Decision 4, decision 5's row (line 773) and "Seventeen move and three stay" (line 787) | Replaced by decision item 1. |
   | "Agents outside this repository": "None is a Mac exception: the exceptions are decision 4's three" (lines 811-812) | The exceptions are five: decision 4's three and these two. |
   | Increment 3, the "SDS lanes" App installed on "intent-packages and the forks" (line 1009) | Increment 3 created both Apps on 2026-11-09 (`.cred-consumers.toml`, `lanes-push-app-private-key`, and the HQ gate G-17 it cites), so this is done. The forks' place in the install is the first "Open before building" item. |
   | Increment 5, "then fork hardening, which pushes as the 'SDS lanes' App (Q1) through `scripts/sds-git.sh`", its rewrites (lines 1029-1032), and the `workflows: write` verify (lines 1035-1039) | Withdrawn. Increment 5 is three lanes: inert landing, then estate landing, then the bump driver. |
   | Cut-over step 2, fork hardening's comparison run `fork-harden run` without `--push` (lines 1135-1136) | Withdrawn: no cut-over. |
   | "Rollback for one lane", fork hardening among the lanes no switch covers (lines 1189-1190) | Still true; the passage no longer names a moved lane. |
   | The week bar's seven daily lanes, naming fork hardening (lines 1262-1264) | Six daily moving lanes have a `/fail` baseline: bump proposer, landing ledger, membership check, pin watcher, rotation proposer and work carrier. Nine run more often, not ten. |
   | Consequences, "Three lane tokens read the 'SDS lanes' App's private key and thirteen read the read-only App's" (lines 1319-1320) | Two and twelve. |
   | Consequences, the forks' `hardened` branch protection and the `workflows: write` verify (lines 1394-1396) | The branch-protection sentence still holds on the Mac (ADR-0057 decision 7). The verify is withdrawn. |
   | Q1, the App pushes for `fork-harden` "once they leave his `gh` login" (lines 1428-1429) | Q1's condition doesn't arise for fork hardening: it doesn't leave Devon's `gh` login. Q1 stands for the two proposers. |

   **The fork-sync watch.**

   | Passage | Disposition |
   |---|---|
   | Decision 2, the "`gh` commands" bullet: its launcher mints its read token and exports it as `GH_TOKEN` (lines 532-534) | Withdrawn for the watch, which keeps `gh api` on Devon's stored login (`src/fork_sync/gh.py`). The membership check, which moves, also reads through `gh api` (`src/membership_check/cli.py`, line 167), so increment 3's `GH_TOKEN` export in `run-membership-check.sh` still stands (`lanes-inc-3-verify-report.md`, lines 318-321 and 426-427). |
   | Decision 2, the read-only set of twelve, "with the fork-sync watch (which reads through `gh`) in place of the bump proposer" (lines 599-603) | The read-only set is eleven: bump driver, change proposer, deploy watcher, estate landing, inert landing, landing ledger, membership check, pin watcher, revision watcher, unit verifier and work carrier. It's the API-variable set less the bump proposer. The API-variable set stays twelve. |
   | Decision 2, the repositories read "for the fork-sync watch, factory-runner ... and each fork it lists" (lines 610-613), and the verify of an upstream's public releases (lines 623-626) | The watch's reads stay on Devon's login. The upstream-releases verify is withdrawn. The read-only App's installation on factory-runner and the forks stands, because all six are members (the live record; seed `docs/operations/membership-seed.toml`, lines 165-269), the landing ledger reads every member (`docs/superpowers/reports/lanes-inc-3-verify-report.md`, lines 227-232), and the membership check reads each member's `.cred-consumers.toml` and reads factory-runner (same report, lines 307-308). |
   | Decision 2, Issues read, "the fork-sync watch's read of each fork's open review alert" (lines 628-629) | No moving lane needs Issues read: the increment 3 verify found the fork-sync watch the only lane that does (`lanes-inc-3-verify-report.md`, lines 297-298). The permission's place is the second "Open before building" item. |
   | Increment 3's list of seven lanes, naming the fork-sync watch (lines 1004-1006), and its changes to `scripts/run-fork-sync-watch.sh`, its CREDENTIALS header, `src/fork_sync/gh.py` and `src/fork_sync/watch.py` (lines 1011-1016) | Withdrawn. Increment 3 is six lanes: landing ledger, pin watcher, deploy watcher, revision watcher, membership check and unit verifier. The launcher, header and docstrings describe the Mac credential, which stays. |
   | The increment 3 verify report's follow-up, "run item 2's three-call probe ... before the fork-sync watch moves" (`lanes-inc-3-verify-report.md`, line 429) | Withdrawn: the watch doesn't move. |
   | Cut-over step 2, "There is no reporting mode for the fork-sync watch" (lines 1133-1135) | Withdrawn for the watch; still applies to the membership check. |
   | Q6 and Q9, the read lanes, fork-sync watch included, mint from the read-only App (lines 1496-1560) | Q6 and Q9 stand for the eleven. The watch keeps its current credential. |

   Neither lane gets a new credential, and neither loses one. Each keeps what it holds today on
   the Mac. Whether either should later trade Devon's `gh` login for a narrower Mac credential
   isn't decided here (the third "Open before building" item).

3. **Phase 1's bar excludes lanes whose subject is this Mac's tool chain.** The bar is "no lane
   that acts on a member runs on the Mac" (Context, line 23). Fork hardening pushes the forks'
   `hardened` branches, and the forks are members, so read as written the bar would move it. The
   bar excludes it: a lane whose subject is this Mac's tool chain stays, whatever member
   repository it reads or writes to serve that chain. The tool installer writes only to this
   machine, and the fork-sync watch only reads. Three lanes are of that kind:
   `tool-installer`, `fork-sync-watch` and `fork-harden`. The Keychain rotation instance and the
   machine-activation sweep stay on decision 4's own grounds (this machine's Keychain, this
   machine's working copies). The year plan's Phase 1 text (sds-hq `plan/year-plan.md`) names only
   the tool installer and the Keychain rotation instance as staying. Its wording follows in a
   separate change, which Devon makes. This ADR doesn't edit it.

4. **Membership is unchanged.** The five forks stay members. The authority is the live member
   record (`GET /api/v1/members`); its seed, `docs/operations/membership-seed.toml`, shows each
   fork as `state = "member"` with both grants (for `rtk`, lines 197-208). The forks keep their
   `dependency_updates` and `security_patches` grants, which the membership check requires of every fork on fork-sync's list
   (`src/membership_check/consistency.py`, lines 143-144). So the inert landing lane keeps landing
   their update pull requests: on the forks, those are the fork-sync lane's sync pull requests,
   authored by `fork-secure-upstream-sync[bot]` (change-manager `app/deploy_policy.py`, lines
   612-630). The forks carry no Dependabot configuration, and a Dependabot pull request on a fork
   fails `security review` (Context). The landing ledger keeps observing their landings, because it
   reads every `onboarding` and `member` repository (`scripts/sds_scope.py`, `ledger`). The
   membership check keeps comparing `fork_harden` `FORKS` and fork-sync's fork list with the grants
   (`consistency.py`, lines 142-144). Inert landing, the landing ledger and the membership check
   all move, in increments 5 and 3, so the forks' landings are landed and observed off the Mac.

5. **What still applies on the Mac.** Both lanes keep what decision 3 requires of every lane,
   on the Mac's path:
   - **The same checks.** Fork hardening pings `sds-fork-hardening` and the watch
     `sds-fork-sync-watch`. Both remain outcome alarms through `sds_outcome_begin` and
     `sds_outcome_end` (`scripts/CLAUDE.md`), and both are liveness checks while up (ADR-0021
     amendments 2 and 3). `sds-deadman.sh`'s name-lookup path through the broad account
     (decision 3, line 719) is what resolves their ping URLs, as it does for the tool installer and
     the activation sweep. That path reports and never gates: a URL it can't read logs one reason,
     and the check goes down after its grace, so a failure is heard, not swallowed.
   - **The outcome semantics.** Every non-zero exit pings `/fail` with the report as the body
     (each launcher's `EXIT CODES` block, held by `test_sds_deadman.py`).
   - **No per-lane BWS identity.** Decision 2's per-lane accounts, ping-URL secrets and the VM
     executor's paste path (Q8, Q10) are for moved lanes. These two keep the Keychain items and the
     main tree's working copy and `.venv`, as the other Mac agents do.
   - **The week bar.** Five agents go quiet that week and are excluded: decision 4's three and these
     two. While the Mac is off, the fork-sync stuck-lane alarm is silent, while inert landing,
     having moved, still lands the forks' sync pull requests on the VPS. A stuck fork goes unseen
     for that week. The tool installer, the chain's consumer, isn't running either, so no
     unhardened version reaches this machine in that time.

6. **Code impact, as later cleanup.** These files name the two lanes as moving. None is edited by
   this amendment. They change together, in one later change, after Devon accepts it:
   - `scripts/deploy_orchestrator.py`, lines 951-973: `LANE_CHECKS` holds rows for `fork-harden`
     and `fork-sync-watch`. Its comment, "The two launchers that stay on the Mac (ADR-0059 decision
     4) have no row", reads "four". `LANES` (line 974) follows the table.
   - `tests/scripts/test_deploy_lanes.py`, line 821: `STAYS_ON_THE_MAC` gains both lanes.
     `test_the_check_names_are_the_launchers` holds `LANE_CHECKS` equal to the launchers less that
     set, so the two edits land in one change.

   These stay true as written: both launchers' headers and their CREDENTIALS text
   (`scripts/run-fork-harden.sh`, lines 25-30; `scripts/run-fork-sync-watch.sh`, lines 30-32), the
   docstrings of `src/fork_sync/gh.py`, `src/fork_sync/watch.py` and `src/fork_harden/cli.py`,
   `install-fork-harden-launchd.sh`, the `.cred-consumers.toml` rows for `run-fork-harden.sh`
   (lines 104-106 and 379-381), `.bws-secrets.toml`, and `docs/operations/deploy.md`, which names
   neither lane. Each describes the Mac path, which stays. The App key rows in
   `.cred-consumers.toml` (`lanes-push-app-private-key`, `lanes-read-app-private-key`) name no fork
   lane as a reader.

### Consequences

- Two fewer lanes move, so two fewer lane identities, lane resources, ping-URL secrets and
  cut-overs. Increment 3 has six lanes and increment 5 three.
- No lane that moves pushes to a fork. The "SDS lanes" App's write reach on the forks serves no
  lane. Until Devon acts on the first "Open before building" item, a leaked push key still mints
  `contents: write` on the forks, as the Consequences on line 1376 say.
  That item is done (G-24, 2026-11-10): the forks have left the installation.
- The two lanes keep reading through Devon's `gh` login and the Keychain items. The bar's exclusion
  is of their subject, not their credential. Increment 6 retires "the moved plists and their
  installers" (line 1040), so their own plists stay. What else increment 6 keeps for them is the
  fourth "Open before building" item.
- The two lanes stay on the ground decision 4 already gives the tool installer, that the Mac is
  the lane's subject, read as this Mac's tool chain. Any later lane that claims it states it in its
  own decision 5 row.

### Open before building

1. **The "SDS lanes" App's installation on the forks.** Q1 installed the App on intent-packages and
   the forks because fork hardening pushed to them, and Q1's rule is that the App "reaches only the
   repositories it pushes to". After this amendment no moved lane pushes to a fork. Whether the
   forks leave the App's installation is Devon's call, on the App's settings page. It narrows a
   credential this ADR decided, so this amendment proposes it and doesn't make it. Before he
   decides, read the installation's repository list (`lanes-push-app-private-key`, installation
   `180257673`): the increment 3 verify lists six repositories (`lanes-inc-3-verify-report.md`,
   lines 210-213).
   **Answered (G-22, 2026-11-10): yes.** The forks leave the "SDS lanes" App's installation
   (`180257673`). Removing them is a later act on the App's settings page; this amendment doesn't
   remove them.
   **Done (G-24, 2026-11-10):** Devon removed the five forks on the App's settings page; the
   installation's repository list is `AlobarQuest/intent-packages` alone. Recorded from his reply
   "G-24: done"; the `gh` login can't read that list (HTTP 403), so no machine check confirms it.
2. **The read-only App's Issues permission.** No moving lane needs Issues read. D-18 confirmed the
   permission list, and dropping Issues is Devon's call on the App's settings page, as for item 1.
   The read-only App's installation on factory-runner and the forks doesn't change (decision item
   2).
   **Answered (G-22, 2026-11-10): no.** The read-only App keeps its Issues read permission.
3. **A narrower Mac credential for the two lanes.** Both keep Devon's `gh` login, which this ADR
   doesn't change for any Mac agent. Whether either gets a narrower credential on the Mac is a
   later decision, with nothing proposed here.
   **Answered (G-22, 2026-11-10): no.** Neither fork lane gets a narrower Mac credential than
   Devon's `gh` login.
4. **Increment 6 keeps the Mac plumbing the staying lanes use.** Increment 6 retires "the moved
   plists and their installers, `activate-checkout.sh`'s use by lanes, and the two Keychain items'
   lane role" (lines 1040-1043). Decision 3 says increment 6 removes the name-lookup path "with
   their plumbing" (lines 719-720). Four `run-*.sh` launchers stay and use all three:
   `run-activation-sweep.sh`, `run-tool-installer.sh`, `run-fork-harden.sh` and
   `run-fork-sync-watch.sh`, each sourcing `activate-checkout.sh` and `sds-deadman.sh`. The two
   earlier Stays already needed this, so the gap predates this amendment. Increment 6's plan
   names what it keeps for these four, and it removes the name-lookup path only once no staying
   launcher resolves its ping URL by name.

### Passages this amendment changes

None is rewritten, because the amendment appends only. Once it's accepted, it governs these
passages, cited by line at `531d42c2`: the clone, cache, lane-state and kill-switch bullets of
decision 1 (107, 309-310, 323-327, 351-352, 399-401); decision 2's `gh` commands, git, helper,
read-only-set, install, permission and pushing-lane bullets (532-541, 559-561, 567-571, 599-629,
637-649); decision 3's upsert and check-provisioning text (719-724, 728-734); decision 4 (741-754);
decision 5's two rows and its count (773, 774, 787); "Agents outside this repository" (811-812); increments 3, 5 and 6 (1004-1016, 1029-1043);
cut-over step 2 (1133-1136); "Rollback for one lane" (1189-1190); the week bar (1262-1264,
1271-1275); the Consequences (1319-1320, 1376, 1394-1396); Q1 (1428-1429), Q6 (1496-1510) and Q9
(1536-1560).

## Amendment 4 (2026-11-11): the pin watcher and the membership check list members and public repositories

- **Status:** Accepted, 2026-11-11. Devon accepted this text on gate G-34, replying "accept"
  from Slack on 2026-11-11. Acceptance followed a clean Opus whole-branch review of PR #659 at
  `30b0e2e2` (HQ D-12, limit 3). It records Devon's answer to HQ decision D-33, given on
  2026-11-09 (sds-hq `state/decisions.md`). The question: "Two lanes, the pin watcher and the
  membership check, find repositories by listing every repository you own (GET /user/repos), which
  a GitHub App token can't call. How do they list repositories once they run off the Mac? (A)
  Install the read-only App on all your repositories ... (B) List only the repositories the App is
  installed on (the 14 members) ... (C) List your public repositories through GitHub's public
  listing, plus the members. That needs no token and no wider install, but private non-member
  repositories drop out of both sweeps." Devon's answer: "C". It also follows the standing answer
  recorded the same day (sds-hq `standing-answers.md`, 2026-11-09): "Rule: design for repositories
  in other owners' accounts and organizations, not only `AlobarQuest`. Code finds repositories
  through what each GitHub App installation covers (and, where a design lists public repositories,
  those of each installation's account), never through an owner name written into the code." The
  ADR's own status is unchanged.
- **Decided by:** Devon, on 2026-11-09 (HQ D-33, option C).
- **Relates to:** decision 2 (the read-only App bullet), decision 5's `pin-watcher` and
  `sds-membership-check` rows, increment 3, cut-over step 2, the Consequences on the read-only
  App's key, Q6, Q9, and Amendment 3's table rows that name the membership check

Line numbers in this amendment are this file's at orchestrator `8ac53534`, before the amendment
was appended. The amendment appends only: no passage before it is rewritten, so each line cited
here still holds the text it names. Where this amendment and an earlier passage disagree, this
amendment governs.

### Context

The pin watcher and the membership check find repositories through one function,
`repositories()` in `src/pin_watcher/github.py` (lines 146-166). It calls
`GET /user/repos?affiliation=owner`, drops archived repositories, and raises when it reaches its
page ceiling (`MAX_PAGES`, 20 pages of 100) instead of returning a truncated list. The pin
watcher sweeps that list for caller workflows (`sweep`, `src/pin_watcher/measure.py`, lines
96-113). The membership check reads each listed repository's `factory-target.toml` and adds the
member repositories the list lacks (`_declarations`, `src/membership_check/cli.py`, lines 53-62).
The same list, `owned`, also feeds the check's landing-policy spelling and gaps (`_landing`, lines
67-86, through `canonical`) and every `_spelled` call (lines 89-92 and 176-192).

Both launchers feed that function the token `gh auth token` prints, Devon's login
(`scripts/run-pin-watcher.sh`, lines 99-110; `scripts/run-membership-check.sh`, lines 80-83).
Decision 2 replaces that with a read token each lane mints from the read-only App (lines 521-531,
597-636). The increment 3 verify found that GitHub doesn't let an installation token call
`/user/repos` (`docs/superpowers/reports/lanes-inc-3-verify-report.md`, lines 18-24 and 243-257,
quoting GitHub Docs, "List repositories for the authenticated user"). On an App token the pin
watcher exits incomplete every pass, and the membership check records the listing as a gap and
shrinks its factory-target sweep to the members (same report, lines 315-317). At the verify, the
account held 65 non-archived owned repositories, 33 private and 32 public, and the read-only App
was installed on the 14 members.

Devon chose option C: the members, plus the public repositories. The standing answer adds that
the code learns the accounts from the App's installations, never from an owner name in code.

### Decision

1. **The listing.** Both lanes list one population: the members' repositories plus the public,
   non-archived repositories of each installation account. It's computed in four steps:

   1. **The members' repositories** are the member record's `onboarding` and `member`
      repositories, as `sds_members.populations.observed` gives them and the landing ledger
      already reads them (`scripts/sds_scope.py`, `ledger`). They come from
      `GET /api/v1/members` on the orchestrator, read with the lane's observer bearer
      (`orchestrator-observer`, which both lanes already hold). A retired member is in no lane's
      population (`populations.py`), so it's listed only if the fourth step lists it as public.
      Today the membership check passes every member, retired included, to `_declarations`
      (`cli.py`, lines 161-162). Keeping a private retired member would be a gap every pass: the
      mint can't name a repository the installation may no longer cover, and a declaration the
      token can't see reads as unreadable, never as absent
      (`_confirm_absence`, `src/membership_check/declaration.py`, lines 336-358).
   2. **The installation accounts** are the distinct owners of those repositories, compared
      without case. No owner name is written in code. They're the read-only App's installation
      accounts because every member is inside that App's installation: in the container, a read
      lane's mint names the members' repositories, and the mint refuses a repository outside the
      installation (`src/estate_clients/github_app_mint.py`, lines 7-10). On the Mac it rests on
      decision 2's install rule, which installs the App on the members' repositories (lines
      620-622). The record lists no
      account, so an installation account that owns no member isn't listed (the first "Open
      before building" item).
   3. **Each account's type** comes from `GET /users/{login}`, whose `type` is `User` or
      `Organization`.
   4. **Each account's public repositories** come from the endpoint for its type:
      `GET /users/{login}/repos?type=owner` for a user account, and
      `GET /orgs/{org}/repos?type=public` for an organization. Both lists are paged with
      `per_page=100`. On 2026-11-11, an unauthenticated probe of a generic user and a generic
      organization found that `GET /users/{login}/repos` also serves an organization's
      repositories, while `GET /orgs/{login}/repos` answers 404 for a user. The design still uses
      each type's documented endpoint, so it doesn't depend on behavior GitHub doesn't document.

   The code keeps a listed repository only when it's `private: false` and `archived: false`. It
   tests both fields itself and doesn't rely on what an endpoint returns for a given token. On the
   Mac, the token is Devon's login, which can see more than an installation token can. The
   explicit filter makes the population the same on both hosts. The result is the union of the
   members' repositories and the public listing, with names compared without case and GitHub's
   spelling kept. Both lanes use the whole union: the pin watcher sweeps it, and the membership
   check's `owned` (the factory-target sweep, `canonical`, and every `_spelled` call) is the
   union, never the public part alone. A private member such as `sds-canary` therefore keeps its
   spelling, and the landing policy can name it without a gap.

   These rules are unchanged:

   - An archived repository that isn't a member is excluded from both lanes, as today.
   - An archived member stays in the membership check's population, as today's `extra` keeps it
     (`cli.py`, lines 61-62). Its declaration is still read. Dropping it would turn its unread
     declaration into a false "does not declare factory_target = true" disagreement (`check`,
     `consistency.py`, lines 115-122).
   - The pin watcher excludes an archived member, as today's listing does: nothing can be
     dispatched into an archive (`repositories()`'s docstring). It learns that a public member
     is archived from the public listing's `archived` field. For each member the public listing
     didn't return, it reads `GET /repos/{owner}/{repo}` with its minted token (Metadata, inside
     its mint, because the repository is a member). An unreadable answer is unreadable for that
     repository (`result.unreadable`), never a silent drop.
   - The page walk has a ceiling for each account (`MAX_PAGES` of `PER_PAGE`). Reaching it
     raises; it never returns a truncated list.
   - Any failure is a gap, never a clean pass. Each of the following makes the whole listing
     unreadable, never a sweep of the members alone: an unreadable member record, a population
     with no member (so no account), an account whose type or page can't be read, or a ceiling
     reached. The pin watcher then exits 3 (`INCOMPLETE`, `src/pin_watcher/cli.py`, lines
     116-123), on a `--dry-run` too. The membership check records one gap and exits 3, as
     `_declarations` does today (`cli.py`, lines 56-60).

2. **Credentials.** Every call goes out with the lane's own GitHub token. That's its minted read
   token in the container and Devon's `gh` login on the Mac until increment 6 (lines 594-596). The
   lanes make these calls:

   | Call | Token | Permission |
   |---|---|---|
   | `GET /api/v1/members` (orchestrator) | The lane's observer bearer: `ORCHESTRATOR_API_TOKEN` for the pin watcher (`run-pin-watcher.sh`, lines 36 and 73-76), `SDS_MEMBERS_OBSERVER_TOKEN` for the membership check | Read access to the member record, which the observer identity already has |
   | `GET /users/{login}` | The lane's GitHub token | None. GitHub Docs, "Endpoints available for GitHub App installation access tokens", lists it, and "Permissions required for GitHub Apps" names no permission for it. |
   | `GET /users/{login}/repos`, `GET /orgs/{org}/repos` | The lane's GitHub token | Metadata read ("Permissions required for GitHub Apps": both rows read `read`, `UAT, IAT`). GitHub grants Metadata with every installation token, and every read lane's mint requests it (`docs/operations/deploy.md`, "A lane's GitHub token"). |
   | `GET /repos/{owner}/{repo}`, the pin watcher only, for each member the public listing didn't return | The lane's GitHub token | Metadata read. The repository is a member, so it's inside the mint. |
   | Per listed repository: the pin watcher's caller file, the membership check's `factory-target.toml` | The lane's GitHub token | Contents read, already in each lane's mint. A public non-member's file is a public read, which needs no install (`lanes-inc-3-verify-report.md`, item 3, lines 113-150). |

   The token is sent even though the listing calls work without one. An unauthenticated client is
   held to GitHub's anonymous rate limit, and the pin watcher reads one file for each listed
   repository each pass.

   Each lane's mint names the members' repositories (step 1) with the read permissions its calls
   already use. It never names a public non-member, which isn't in the installation, so the mint
   would refuse it. The design grants nothing new: no new permission (the read-only App keeps
   D-18's six), no new installation or wider install, no new BWS secret, and no new lane
   identity. Each lane already reads the observer bearer it uses for the member record: on the Mac
   both read it from BWS (`f793576f-…`), and each lane's container reads it from BWS with the lane's token, as the deploy watcher's does
   (`docs/operations/deploy.md`).

3. **What drops out.** A private repository that isn't a member is in neither sweep. One code path
   computes the population on the Mac and in the container, so it drops out on both hosts once the
   new listing lands, before either lane moves. Each lane loses one kind of report:

   - **The pin watcher** no longer reads a private non-member's caller workflow
     (`caller_pin`, `src/pin_watcher/github.py`, lines 169-182, through `sweep`, `measure.py`,
     lines 101-112). A caller there that isn't at the recommendation gets no observation row
     (`_file_rows`, `cli.py`, lines 60-75) and no `pin-behind:<repository>` alert
     (`src/pin_watcher/alerts.py`, `classify`). The lost signal is small. The factory dispatches
     only to a repository the member record grants `app_changes` or `dependency_updates`, so it
     can't dispatch into a private non-member, whichever runner the caller pins (the root
     `CLAUDE.md`, the grant invariant; `services/execution/target_grant.py`). One side effect: the
     first pass under the new listing resolves any open `pin-behind` alert for such a repository,
     with the title "Caller pin is at the recommendation". `KeyedAlerts.sync` resolves a
     remembered key that is neither firing nor unread (`src/alert_hub_client/__init__.py`, lines
     254-275), and a repository that isn't listed is neither.
   - **The membership check** no longer reads a private non-member's `factory-target.toml`
     (`factory_targets`, `src/membership_check/consistency.py`, lines 42-54). So a private
     non-member that declares `factory_target = true` no longer produces the disagreement
     "`<repository>`: its factory-target.toml names it, but the member record does not grant
     app_changes" (`check`, `consistency.py`, lines 112-114, through `_require`, lines 78-84). A
     landing population or configuration list that names a private non-member still produces its
     own `_require` disagreement, because those lists come from their own sources, not from the
     listing. It also produces a `canonical` gap for the landing policy (lines 57-67). That gap's
     text, "isn't an active repository of this account (archived, renamed or not owned)", is the
     fifth item's to reword.

   - **A private retired member** is now in neither sweep, because the members' part is the
     `onboarding` and `member` repositories (decision item 1, first step). For the membership
     check, the loss is the same disagreement as for a private non-member: a retired member is
     granted nothing (`_granted`, `consistency.py`, lines 70-75), so a private retired member
     that still declares `factory_target = true` reported it until now, through `/user/repos`
     and `extra`.

   Option C accepts these losses. Neither lane can see such a repository again unless it becomes a
   member, or until Devon decides to widen the install (D-33's option A), which this amendment
   doesn't do.

4. **What it changes in the ADR.** No passage names `/user/repos` or the account listing. Each of
   the following passages relies on it, or on Devon's login, for these two lanes' population.

   | Passage | Disposition |
   |---|---|
   | Decision 2, "Each launcher feeds that variable, instead of its `gh auth token` fallback, with a read token it mints each pass from the lanes' read-only App" (lines 529-531) | Stands. For the pin watcher and the membership check it holds only once decision item 1's listing has replaced `/user/repos`, because on a minted token the old listing fails. |
   | Decision 2, "Each pass's mint request names only that lane's repositories" (lines 603-605) | Stands. For these two lanes "that lane's repositories" are the members' repositories (decision item 2). The public non-members they also read are public reads, outside the mint. |
   | Decision 2, "The repositories these lanes read through Devon's login today are the members' repositories, the member record's repository, intent-packages, the standards repositories the lanes clone" (lines 609-613) | Incomplete for these two lanes: today they also read every repository the account owns. After this amendment they read the members' repositories and the installation accounts' public repositories, and no private non-member. |
   | Decision 2, "Increment 3 ... lists the exact set from the member record, the lanes' clone lists and that registry, and Devon installs the read-only App on those alone" (lines 620-622) | Stands. The two lanes add nothing to the install set: their public non-member reads need no install. |
   | Decision 2, "Verify before increment 3 that an installation-token mint narrowed by repository and permission returns a token that refuses everything outside the request" (lines 632-635) | The verify answered it for public resources (`lanes-inc-3-verify-report.md`, item 3): the narrowing doesn't stop public reads. This design depends on that answer, which the second "Open before building" item confirms live. |
   | Decision 5, `pin-watcher`, "Reads callers' runner pins through APIs only" (line 783) | Stands. Its population is decision item 1's. |
   | Decision 5, `sds-membership-check`, "Compares the member record with declarations through APIs only" (line 788) | Stands. Its population is decision item 1's. |
   | Increment 3, the observe-only lanes, "pin watcher ... membership check ... one at a time", and "Each launcher then mints its lane's read token from the read-only App" (lines 1010-1017) | Stands, with a precondition: the listing change in decision item 5 lands and runs on the Mac before either lane's cut-over. |
   | Cut-over step 2, the pin watcher's reporting run "with `--dry-run`" (lines 1137-1138) | Stands. The paired runs compare like with like only when both hosts run the new listing, which the precondition in the preceding row provides. Before that, the Mac run lists private non-members that the container can't see. |
   | Cut-over step 2, "There is no reporting mode for the fork-sync watch and the membership check" (line 1143), as Amendment 3 left it for the membership check | Stands for the membership check, with the same precondition. |
   | Consequences, "The read-only App is installed on every repository the read-only set reads, member repositories included ... so its leaked key reads all of them" (lines 1388-1390) | Narrowed in meaning: the two lanes also read public non-members, which the App isn't installed on. A leaked key adds nothing there that anonymous access lacks, beyond the installation's rate limit. |
   | Q6, "The App is installed on every repository those lanes read" (lines 1512-1513), and Q9, "installed on the repositories those lanes read, member repositories included" (lines 1549-1550) | Stand for private reads. The public non-members these two lanes read are outside the install, by D-33. |
   | Amendment 3, decision item 2, the "`gh` commands" row: increment 3's `GH_TOKEN` export in `run-membership-check.sh` still stands (line 1992) | Stands. That export serves the `gh api` fork-list read (`cli.py`, line 167), not the listing. |
   | Amendment 3, decision item 2, the read-only set of eleven, membership check and pin watcher included (line 1993) | Stands. Neither lane leaves the set, and neither gains a credential. |
   | Amendment 3, decision item 2, "the membership check reads each member's `.cred-consumers.toml` and reads factory-runner" (line 1994) | Stands. Both reads are of members. |
   | Amendment 3, decision item 2, increment 3's six lanes, pin watcher and membership check included (line 1996) | Stands, with the precondition in the increment 3 row. |
   | Amendment 3, decision item 2, cut-over step 2 "still applies to the membership check" (line 1998) | Stands, with the same precondition. |

5. **The code that follows, as later work.** None of these files is edited by this amendment.
   They change in one later change, in increment 3, before the pin watcher or the membership check
   moves:

   - `src/pin_watcher/github.py`: `repositories()` (lines 146-166) takes the members'
     repositories and returns decision item 1's union. Its docstring changes from "Every
     non-archived repository the credential owns", and so does the ceiling's message ("the account
     has more than ..."), which then names the account. The module docstring's "THE POPULATION
     IS SELF-DESCRIBING" (lines 3-20) still holds for which repositories carry a caller; its
     population sentence changes.
   - `src/pin_watcher/measure.py`, `sweep` (lines 96-113), and `src/pin_watcher/cli.py`, `main`
     (lines 104-123): the pin watcher reads the member record before it sweeps. How it reads it is
     the third "Open before building" item.
   - `src/membership_check/cli.py`: `_declarations` (lines 53-62) passes the `onboarding` and
     `member` repositories, not every member (lines 161-162), and `owned` becomes the union. Its
     docstring ("the account's repositories", line 54), its gap text (line 59) and `_spelled`'s
     docstring ("the account listing", line 90) name the new population.
   - `src/membership_check/consistency.py`: `factory_targets`' docstring ("Every owned
     repository", line 43), and `canonical` (lines 57-67), whose gap's "of this account
     ... or not owned" names a member or an installation account's public repository.
   - Tests: `tests/pin_watcher/conftest.py` (the fake `/user/repos`, lines 44-70),
     `tests/pin_watcher/test_measure.py`, and `tests/architecture/test_membership_edges.py`, which
     pins the membership check's GitHub requests (lines 186, 389-395 and 422-431). If the pin
     watcher imports `sds_members`, `tests/architecture/test_out_of_process_isolation.py`'s
     `pin_watcher` row (lines 433-450) also changes. The `/user/repos` entries in
     `tests/architecture/test_rotation_edges.py` (line 755) and
     `tests/membership_check/test_auto_merge.py` (line 202) are refusal probes for other clients,
     and they don't change.
   - The launchers' CREDENTIALS text: `scripts/run-pin-watcher.sh` (lines 99-102, "THE GITHUB
     CREDENTIAL HAS NO BWS RECORD ... reads public repository metadata") and
     `scripts/run-membership-check.sh` (lines 25-30, "GitHub, through the operator's own `gh` login"). Each launcher's mint, which increment 3
     adds as it does for the other read lanes, names the members' repositories.
   - `docs/operations/deploy.md`, "A lane's GitHub token": the paragraphs increment 3 adds for
     these two lanes name the members' repositories as the mint's, and the public listing as
     unminted reads.
   - `.bws-secrets.toml` and `.cred-consumers.toml` need no line for this amendment: no secret is
     added. Increment 3's own rows for these lanes (the lane tokens, ping URLs and the read-App
     key's `runtime-fetch` readers) are unchanged.

### Consequences

- Neither lane needs `/user/repos` or Devon's login to find its population, so both can move in
  increment 3 on a minted read token, with no credential, permission or installation beyond what
  decision 2 already grants.
- Both lanes stop seeing private non-member repositories on the Mac too, from the release that
  carries the new listing. That's 33 private repositories at the increment 3 verify, less the one
  private member (`sds-canary`).
- The population no longer depends on one account. A member in a second account brings that
  account's public repositories into both sweeps, provided the read-only App is installed there,
  which every read lane's mint already requires.
- Each pass makes a few more calls: one `GET /users/{login}` and one or more pages for each
  account, plus the member record read in the pin watcher.

### Open before building

1. **An installation account that owns no member.** Decision item 1 learns the accounts from the
   members, so it can't list an account where the read-only App is installed but no member lives.
   Today the read-only App has one installation (`180257215`), and its account owns every member
   (`docs/operations/deploy.md`, "A lane's GitHub token"), so the sets are equal. Reading the
   App's installations directly (`GET /app/installations`) needs the App's JWT. The lane program
   doesn't hold the JWT, and the Mac path has no App key, so it would split the code path.
   Resolved by the increment that installs the read-only App on a second account: it adds that
   account's first member in the same act, or it decides how the lanes learn the installation.
2. **A minted token reads a public non-member.** The design rests on the increment 3 verify's
   documentary answer, that an installation token reads public resources outside its
   repositories (`lanes-inc-3-verify-report.md`, item 3, and its self-review, lines 451-453).
   Resolved in the pin watcher's cut-over step 1: its container `--dry-run` must read a public
   non-member's caller file and list the public repositories through each endpoint the account's
   type selects, with the minted token. If GitHub refuses, the listing falls back to sending no
   token on the public calls, which D-33's option C allows, before the lane's cut-over goes on.
   The fallback covers the per-repository file reads too. At the increment 3 verify's count (32
   public repositories), each lane makes about 35 unauthenticated calls a pass. GitHub's anonymous
   limit is 60 requests an hour for one address, so the two lanes fit only if they run in
   different hours. If the fallback can't fit, it goes back to Devon before the cut-over.
3. **How the pin watcher reads the member record.** The pin watcher has no member-record read
   today, and its isolation row admits no sibling lane (`test_out_of_process_isolation.py`, line
   449). There are two options. It can import `sds_members.client.read_members` with the observer
   bearer it already holds, and the row admits `sds_members`. Or its launcher can pass the members'
   repositories that it derives for the mint (`scripts/sds_scope.py`, `ledger`), and the program
   takes them as input. Resolved by the change in decision item 5, in increment 3.
4. **Members in more than one account.** `sds-github-mint` refuses repositories of more than one
   owner (`github_app_mint.py`, lines 7-10, 148-165), and each lane has one GitHub token variable.
   Once members span two accounts, these two lanes, like every read lane that mints over the
   members, need a mint for each installation and a token for each account's reads. Resolved by
   the same increment as the first item, before the first member in a second account is added.

## Amendment 5 (2026-11-11): the membership check clones intent-packages anonymously each pass

- **Status:** Accepted, 2026-11-12. Devon accepted this text on gate G-38, in an interactive HQ
  session on 2026-11-12. Acceptance followed a clean Opus whole-branch review of PR #670 at
  `24c43faf` (HQ D-12, limit 3). The ADR's own status is unchanged.
- **Decided by:** HQ. It adds a second exception to decision 2's rule that decision 1's per-pass
  clones need a git credential (lines 541-543), whose one named exception is the rotation
  proposer's clone of infraops-mcp-server: the membership check's clone of intent-packages, which
  is public. It grants no permission, App installation or credential, so no Devon decision is
  needed (item 3 of the decision).
- **Relates to:** decision 1 (the clone bullet), decision 2 (the git bullet, the helper bullets,
  the launcher bullet, the read-only App bullet and the identity bullet), decision 5's
  `sds-membership-check` row, increments 3 and 4, cut-over step 2, Amendment 3's table rows that name the
  membership check, and Amendment 4's `sds-membership-check` row

Line numbers in this amendment are this file's at orchestrator `a6442254`, before the amendment was
appended, and a source file's line numbers are that file's at the same commit. The amendment
appends only: no passage before it is rewritten, so each line cited here still holds the text it
names. Where this amendment and an earlier passage disagree, this amendment governs.

### Context

The membership check reads intent-packages. `_standing_update_repositories()`
(`src/membership_check/cli.py`, lines 106-112, called at lines 173-174) calls `discover()`
(`src/bump_proposer/standing.py`, lines 127-173) with no argument, so it reads
`checkout_root()` (lines 118-119): `BUMP_PROPOSER_PACKAGES_CHECKOUT` when it's set, else
`DEFAULT_CHECKOUT`, `~/Projects/intent-packages` (`src/standing_identity/__init__.py`, line 25).
`discover()` reads files only: each `packages/*/package.yaml`, with no git command and no venv. A
missing `packages/` directory raises `StandingError`, and `_standing_update_repositories()` turns
`StandingError` or `OSError` into a gap, never an empty population. It keeps only each package's
`target_repository`.

Decision 1's clone bullet (lines 306-320) names the lanes that clone other repositories each pass,
and the membership check isn't one of them. Decision 5 says the lane "Compares the member record
with declarations through APIs only" (line 788), and Amendment 4 kept that row (line 2326). In the
lane's container there's no intent-packages checkout, so every pass would record the gap "the
packages checkout couldn't be read" and exit 3.

On the Mac, `scripts/run-membership-check.sh` sets no `BUMP_PROPOSER_PACKAGES_CHECKOUT`, so it
reads the default checkout. The membership check doesn't fast-forward that checkout. The bump
proposer's, rotation proposer's and work carrier's acting runs do (ADR-0031 amendment 1, #668), so
the lane's population is as fresh as their last acting run, with no signal of its own.

intent-packages is public (`gh repo view AlobarQuest/intent-packages --json visibility`,
`PUBLIC`, read 2026-11-11). Decision 2 says decision 1's per-pass clones need a git credential
whether or not the repository is private, and names one exception, the rotation proposer's clone of
infraops-mcp-server (lines 307-310 and 541-543). This amendment adds a second exception: the
membership check's clone of intent-packages, for the membership check only. The bump driver's
clone of intent-packages still needs a credential, as decision 2 says. The
lane's existing anonymous mirror, `RepoMirror` (`src/rotation_worker/git_mirror.py`; this lane's
use is `cli.py` lines 115-126), offers `sync`, `show` of one path and ancestry. It can't list a
directory, so `discover()` can't run on it unchanged.

The mechanism was probed on the Mac on 2026-11-11, by the session that wrote this amendment. With `env -i`, an empty `HOME`, no global or
system git configuration and `GIT_TERMINAL_PROMPT=0`, a shallow clone of
`https://github.com/AlobarQuest/intent-packages.git` at `main` succeeded, and `discover()` on it
returned 5 standing packages. `discover()` on an absent directory raised `StandingError` ("no
packages directory in the checkout"). An anonymous clone of a repository name that doesn't exist (no
private repository was probed) failed at once with exit 128 ("terminal prompts disabled"), so a
clone GitHub refuses fails without waiting on a prompt. The container proof is the first
"Open before building" item.

### Decision

1. **The clone.** The membership check is a decision 1 clone lane, for intent-packages, with an
   anonymous clone.

   - **What and how.** On the container path only, before it runs `membership-check`, the
     launcher runs `git clone --quiet --depth 1 --branch main
     https://github.com/AlobarQuest/intent-packages.git "$HOME/intent-packages"`. It runs git
     itself, never `gh repo clone`, with `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`
     and `GIT_TERMINAL_PROMPT=0` in the clone's environment. So no credential helper, stored
     login or extra header applies, and a clone that GitHub won't serve anonymously fails at once
     instead of prompting. `GH_TOKEN`, which the launcher exports for `gh` (decision item 4),
     doesn't reach the clone: git doesn't read it, and the only bridge from `gh` to git is a
     `gh auth setup-git` credential helper, which no global configuration holds.
   - **Where it goes, and who deletes it.** The clone goes in the pass's `HOME`. `lane-run` makes
     that directory new for each pass and deletes it when the launcher exits
     (`scripts/lane_run.py`, lines 86-90, `tempfile.TemporaryDirectory`). So the launcher deletes
     nothing itself, and no clone outlives its pass. This is how decision 1 already treats the
     `RepoMirror` roots of the rotation decomposer and proposer (lines 323-326).
   - **The variable.** The launcher exports `BUMP_PROPOSER_PACKAGES_CHECKOUT="$HOME/intent-packages"`,
     whether or not the clone succeeded (decision item 5). Like the other clone lanes' checkout
     variables, it's the launcher's own setting and doesn't join `lane-run`'s list (lines
     316-317; `COPIED` and `STATE_VARIABLES`, `scripts/lane_run.py`, lines 46-60).
   - **What it isn't.** No token, and no `scripts/sds-git.sh`: increment 4 hasn't created it,
     and this clone is decision 2's second exception to the per-pass credential (lines 541-543),
     beside the rotation proposer's clone of infraops-mcp-server. No venv: `discover()`
     reads files only. No Python change: `discover()` already reads the variable.
   - **The URL.** The repository is the one `PACKAGES_REPOSITORY` already names
     (`src/bump_proposer/standing.py`, line 57). Amendment 4's standing answer is about finding
     member repositories through App installations. intent-packages is the SDS's own package
     source, one fixed repository, not a population, so naming it doesn't conflict with that
     answer. How the launcher spells it is the second "Open before building" item.
   - **The Mac path.** It keeps its current checkout: the launcher's Mac branch doesn't clone or
     set the variable, so the Python reads `DEFAULT_CHECKOUT` there, as it does now. There's one
     code path in Python. Only the launcher's container branch differs. Setting the variable to a
     fresh per-pass clone on the Mac too, which would end the Mac's dependence on other lanes'
     fast-forwards, is optional follow-up and not part of the move.

   The following options were rejected:

   - **An API reader for `packages/`.** It would be a second `discover()`, reading the same files
     through GitHub's contents API, that can drift from the bump proposer's.
   - **`RepoMirror`.** It can't list a directory, so `discover()` can't run on it unchanged.
   - **The lane's minted read token on the clone.** intent-packages is public, so a token would
     widen the token's use for no gain.

2. **What it changes in the ADR.** Each of the following passages names the lanes that clone, the
   clones' credentials, the helper, or the membership check's reads.

   | Passage | Disposition |
   |---|---|
   | Decision 1, "These lanes are the bump driver (intent-packages, project-standards, security-standards), both bridge proposers ..., the work carrier (package checkouts) and fork hardening (the forks)" (lines 306-310) | Extended: the membership check is a clone lane too, for intent-packages, anonymously, as the rotation proposer's clone of infraops-mcp-server is. Its clone is shallow, at `main`, into the pass's `HOME`, which the pass discards (decision item 1). |
   | Decision 1, "Three Mac checkout variables point at these clones in the container, and each launcher sets its own from the pass's clone directory, so none joins `lane-run`'s list" (lines 315-317) | Stands. `BUMP_PROPOSER_PACKAGES_CHECKOUT` gains a third container setter, the membership check's launcher, and still joins no list. |
   | Decision 1, the lane-state bullet: "The other six moving lanes (... landing ledger and membership check) keep no state beyond the rebuildable caches" (lines 351-353) | Stands. The clone lives in the pass's `HOME`, not in a volume, and isn't kept between passes. |
   | Decision 2, "Decision 1's per-pass clones need a git credential too, whether or not the repository is private, except the rotation proposer's clone of infraops-mcp-server" (lines 541-543) | Extended by a second exception: the membership check's clone of intent-packages, which is public, needs no credential. The first "Open before building" item verifies it works anonymously in the container. |
   | Decision 2, "The launcher calls the helper once, before its first clone" (lines 561-562), and "Lanes that don't clone don't call the helper" (line 587) | The membership check clones and doesn't call the helper. The helper's purpose is a credential, and this clone carries none. |
   | Decision 2, "Each launcher replaces its Keychain read ..., and, if it clones, adds decision 1's clone and the helper call" (lines 589-591) | Stands for the clone. The membership check's launcher adds no helper call (preceding row). |
   | Decision 2, "Each pass's mint request names only that lane's repositories" (lines 603-605), and "The repositories these lanes read through Devon's login today are ... intent-packages ..." (lines 609-613) | Stands. intent-packages is a member (`docs/operations/membership-seed.toml`, lines 125-127), so the membership check reads it like every observed member, through the lane's minted token in the container and Devon's login on the Mac (`cli.py` lines 166, 177 and 192: `_declarations`, `_registry_files` and `_auto_merge`). Only the standing-package read goes through the anonymous clone, and only that read is outside the mint. On the Mac that read uses a local checkout, not Devon's login. |
   | Decision 2, "lists the exact set from the member record, the lanes' clone lists and that registry, and Devon installs the read-only App on those alone" (lines 620-622) | Stands. The membership check's clone adds nothing to the install set: an anonymous clone needs no installation. The lane's member reads of intent-packages (preceding row) depend on the read-only App's installation there, which Q9 (HQ D-17) decided; only the standing-package read doesn't. |
   | Decision 5, `sds-membership-check`, "Compares the member record with declarations through APIs only" (line 788) | Reworded to: "Compares the member record with declarations through APIs, and with the standing dependency-update packages in an anonymous per-pass clone of intent-packages." |
   | Increment 3, the observe-only lanes, "pin watcher ... membership check ... one at a time", and "Each launcher then mints its lane's read token from the read-only App" (lines 1010-1017) | Stands. The membership check's increment 3 change also adds decision item 1's clone to its launcher. |
   | Increment 4, "This increment adds `scripts/sds-git.sh` (decision 2) with the first lane that clones" (lines 1025-1026) | Stands. The membership check clones earlier, in increment 3, and doesn't use the helper, so the first lane that clones through the helper is still increment 4's. |
   | Cut-over step 2, "There is no reporting mode for the fork-sync watch and the membership check ... Their comparison is the ordinary pass, run on both hosts" (lines 1143-1145) | Stands, with one addition. The Mac reads the default checkout and the container reads a fresh clone, so the two outputs differ in standing packages if the Mac checkout is behind `main`. The comparison records `git -C ~/Projects/intent-packages rev-parse HEAD` beside the clone's commit, which the launcher prints (decision item 6), and a difference explained by the two commits isn't a cut-over failure. |
   | Amendment 3, decision item 2, the "`gh` commands" row: "increment 3's `GH_TOKEN` export in `run-membership-check.sh` still stands" (line 1992) | Stands. At `a6442254` the fork-list read is `forks(subprocess_runner)` at `cli.py` line 170 (line 167 in Amendment 3's citation, from an earlier commit). |
   | Amendment 3, decision item 2, cut-over step 2 "still applies to the membership check" (line 1998) | Stands, with the preceding cut-over row's addition. |
   | Amendment 4, decision item 4, the increment 3 row: the listing change lands and runs on the Mac before the lane's cut-over (line 2327) | Stands. The clone adds no precondition: the Mac branch doesn't clone. |
   | Amendment 4, decision item 4, "Decision 5, `sds-membership-check`, ... (line 788) — Stands. Its population is decision item 1's." (line 2326) | Superseded for the row's wording, which this amendment's line 788 row gives. Amendment 4's population still holds. |

3. **Credentials.** The lane's container reads the following secrets. Each is listed in
   `.bws-secrets.toml` at `a6442254` unless the row says otherwise. Only the BWS id prefix is
   shown here, never a value. The clone carries no credential and adds no row.

   | Secret | BWS id | Read today on the Mac | In the container | ADR-0059 approval |
   |---|---|---|---|---|
   | The orchestrator observer bearer, `orchestrator-observer` | `f793576f-…` | Narrow identity (`run-membership-check.sh`, lines 41 and 76) | The lane's own token | Decision 2's identity bullet: the lane's account gets a direct read grant on "the secrets its launcher fetches today" (lines 487-490). The manifest's `lanes` list doesn't name the lane yet. |
   | The change-manager READ bearer, `change-manager/M2M_TOKEN_READ` | `314f276d-…` | Broad identity (lines 42 and 79) | The lane's own token | The same bullet (lines 487-490): the launcher fetches it today. The manifest entry has no `lanes` field yet. |
   | The read-only App's private key, `lanes-read-app-private-key` | `314a966c-…` | Not read: the Mac uses `gh auth token` (line 82) | The lane's own token, piped to `sds-github-mint` | Decision 2: the identity bullet's "the private key of each App it mints from" (lines 490-491), and the key's readers, "the twelve of the read-only set" (lines 646-648), which Amendment 3 made eleven, the membership check included (line 1993). The manifest's `lanes` list doesn't name the lane yet. |
   | The lane's ping-URL secret (`membership-check-ping-url`, by the other lanes' pattern) | Not created yet; no manifest entry | Not read: the Mac resolves the ping URL by check name | The lane's own token, as `SDS_DEADMAN_PING_URL_UUID` | Decision 3 (lines 688-698): the VM root creates it in `Rotation / Keepers` at the lane's cut-over, with the lane's read grant. |
   | The Healthchecks Management API key, `healthchecks_selfhosted_api_key` | `51509d5e-…` | Broad identity, through `sds_deadman_resolve` | Never | Excluded: decision 2's identity bullet (lines 488-490) and decision 3 (lines 677-687) say no lane holds it. |

   So the change-manager READ bearer needs no new decision. The lane's account gets its grant at
   the lane's identity gate, because accepted text (lines 487-490) grants every secret the
   launcher fetches today, and the launcher fetches this one at lines 42 and 79. This amendment
   grants no new permission, App installation or credential. The GitHub token is minted per pass
   from the read-only App's key and never stored, so it has no row.

4. **The fork list.** `cli.py` line 170 calls `forks(subprocess_runner)`, which reads
   factory-runner's `.cred-consumers.toml` through `gh api` (`src/fork_sync/watch.py`, lines
   154-156). Amendment 3 kept increment 3's `GH_TOKEN` export in `run-membership-check.sh` for this
   read (line 1992), and Amendment 4 kept it too (line 2332). The lane image has `gh`
   (`lanes/Dockerfile`, the `GH_VERSION` download, installed to `/usr/local/bin`). The container
   branch exports the pass's minted read token as `GH_TOKEN` beside `SDS_MEMBERS_GITHUB_TOKEN`.

5. **Failure.** A failed clone isn't the launcher's failure. The launcher doesn't call
   `sds_outcome_fail` (exit 1) when git exits non-zero: it writes git's error into the pass's
   report and runs `membership-check` with the variable still set. `discover()` then finds no
   `packages/` directory and raises `StandingError`, and `_standing_update_repositories()`
   records the gap "the packages checkout couldn't be read" (`cli.py`, lines 106-112). The pass
   exits 3 if nothing disagrees, or 2 if something does, because disagreements outrank gaps
   (`run-membership-check.sh`, lines 18-23). Either way the pass pings `/fail` with the report. A
   clone without `packages/` is the same gap. A clone whose `packages/` holds no standing package
   is a true reading, as it is on the Mac. The standing-package direction only checks that every
   repository a package names is granted (`_require`, `src/membership_check/consistency.py`,
   lines 82-88, called at 159-164), so an empty population can't produce a false disagreement.
   The clone is per pass, so the container never reads a stale checkout.

6. **The code that follows, as later work.** None of these files is edited by this amendment. They
   change in the membership check's increment 3 move:

   - `scripts/run-membership-check.sh`: the container branch (a set `BWS_ACCESS_TOKEN`, as
     `run-pin-watcher.sh` lines 29-31 and 93-98 detect it) reads the observer bearer, the
     change-manager READ bearer and the read-only App's key with the lane's own token, mints the
     pass's read token, and exports it as `SDS_MEMBERS_GITHUB_TOKEN` and `GH_TOKEN`. It makes
     decision item 1's clone, prints one line naming the clone's commit or its failure, and exports
     `BUMP_PROPOSER_PACKAGES_CHECKOUT`. Its CREDENTIALS header (lines 25-32) describes both hosts.
     The Mac branch keeps its current reads.
   - The launcher's tests, beside `tests/scripts/` for the other increment 3 launchers: a
     container pass clones with no credential and sets the variable; a failed clone still runs
     the program and exits 3 with the gap, never 1; the Mac branch sets no variable.
   - `lanes/membership-check/compose.yaml` and `lanes/membership-check/crontab`, as for the other
     increment 3 lanes.
   - `docs/operations/deploy.md`, the registered lanes and "A lane's GitHub token", and
     `docs/operations/membership.md` if the launcher derives its mint's repositories through
     `sds_scope ledger`, as the pin watcher's change did.
   - `.cred-consumers.toml`: the launcher's `runtime-fetch` consumer row on
     `lanes-read-app-private-key`, as the pin watcher's change added, and the lane token's and
     ping URL's rows at cut-over.
   - `tests/scripts/test_deploy_lanes.py`, whose placeholder lanes gain `membership-check` while its
     ping-URL id is empty.
   - `.bws-secrets.toml`: `membership-check` joins the `lanes` lists of `f793576f-…` and
     `314a966c-…`, `314f276d-…` gains `lanes = ["membership-check"]`, and the ping-URL secret
     gets its entry once it exists, as the other lanes' ping-URL secrets did.

### Consequences

- The membership check can move in increment 3 with the standing-package direction intact, and
  with no credential, permission or installation beyond what decision 2 already grants.
- In the container, the standing population is intent-packages' `main` at the start of each pass,
  so it's fresher than the Mac's, which follows other lanes' acting runs.
- Each pass makes one more anonymous request to GitHub: a shallow clone of one repository (54
  package directories on 2026-11-11).
- If intent-packages becomes private, every container pass records the gap and exits 3 until a
  decision gives the clone a credential, through `scripts/sds-git.sh` and the lane's read mint
  (the App is already installed there). The failure is loud: the gap names the missing checkout,
  and the report carries git's error.

### Open before building

1. **The anonymous clone in the container.** The probe ran on the Mac. The membership check has
   no `--dry-run` (`cli.py` takes no options), so cut-over step 1's launcher `--dry-run` doesn't
   apply to it, and its first container pass is cut-over step 2's ordinary pass. Resolved there:
   the container pass's report shows the clone's commit and no "packages checkout" gap.
2. **How the launcher spells the repository.** It can write the URL as a literal or derive it from `PACKAGES_REPOSITORY`
   through the venv, which adds a Python call before the pass. Resolved by the membership check's
   increment 3 move, which also adds a test that the two agree if it uses the literal.
3. **The Mac checkout's freshness.** Whether the Mac branch also clones per pass (decision item 1,
   "The Mac path") is optional follow-up after the move. Until then, cut-over step 2's paired pass
   relies on the commit comparison in the decision item 2 table. Resolved by a later change, or
   by increment 6, when the Mac path ends.

## Amendment 6 (2026-11-12): the week bar is dropped

- **Status:** Accepted on merge. HQ merges it only after a clean Opus whole-branch review (HQ
  D-12, limit 3). The ADR's own status is unchanged. Devon decided it on 2026-11-12, in an
  interactive HQ session at about 17:45 UTC: "We are unable to turn the mac off for a week, and we
  are unwilling to leave SDS doing nothing for a week, once its on the VPS. I can see some value in
  a shakeout, but I dont think the value is worth the cost." Offered three replacements (a soak with
  the Mac on plus an audit, an audit plus a short outage, or cut-over checks only), he chose "Cut-over
  checks only".
- **Decided by:** Devon, on 2026-11-12 (sds-hq PR #69). Devon also approved, in an interactive
  session on 2026-11-12, disabling a moved lane's Mac job as well as booting it out (decision
  item 2).
- **Relates to:** the Context, decision 6 (its heading, increments 6 to 9, the cut-over, "Rollback
  for one lane", "The week bar" and the phase's done-when), and Amendment 3's week-bar passages

Line numbers in this amendment are this file's at orchestrator `519bb23f`, before the amendment
was appended. The amendment appends only: no passage before it is rewritten, so each line cited
here still holds the text it names. Where this amendment and an earlier passage disagree, this
amendment governs.

### Context

The Context (lines 23-24) took Phase 1's bar from the year plan: no lane that acts on a member runs
on the Mac, and a lane survives the Mac being off for a week. "The week bar" (lines 1248-1318)
measured the second half as 7 consecutive days with the Mac off or asleep, against a baseline of
each lane's Healthchecks flips and ping log captured at its cut-over. Devon decided the Mac can't be
off for a week, and the SDS won't sit idle for a week once it runs on the VPS. The year plan's
Phase 1 done-when now reads (sds-hq `plan/year-plan.md`, line 56 at `ec8e83f`, sds-hq PR #69): "no
lane that acts on a member runs on the Mac. A lane counts as moved once its paired pass on the VPS
matches its Mac run and its Mac job is booted out."

### Decision

1. **The bar for a moved lane is its cut-over checks.** A lane has moved once its paired pass on
   the VPS matches its Mac run (cut-over step 4's comparison) and its Mac job is booted out and
   disabled (cut-over steps 2 and 4, as decision item 2 states them). The bar needs no time with the
   Mac off or asleep, no baseline and no week of evidence.
2. **The cut-over's Mac step is a bootout plus a disable.** A `launchctl bootout` lasts only until
   the next login. The Mac restarted on 2026-11-12 at 08:02 local, and launchd reloaded
   `com.devon.execution-watch` and `com.devon.landing-ledger`, both booted out at their cut-overs;
   the execution watch then ran on both hosts. Cut-over step 2 runs
   `launchctl bootout gui/<uid>/com.devon.<lane>`, then
   `launchctl disable gui/<uid>/com.devon.<lane>`, and keeps the plist. Step 4 verifies that the
   agent isn't loaded and that `launchctl print-disabled gui/<uid>` lists `com.devon.<lane>` as
   disabled. Rollback runs `launchctl enable gui/<uid>/com.devon.<lane>` before it bootstraps the
   kept plist, because launchd refuses to bootstrap a disabled service.
3. **Increment 6 starts once increments 2 to 5 have cut over**, that is, once every lane they move
   has passed its cut-over checks. It no longer waits for a week.
4. **Increments 7, 8 and 9 are each done at their own cut-over checks.** Increment 7's ADR-0055
   amendment states the executor's, and increments 8 and 9's plans state theirs. None runs a week
   with the Mac off.
5. **Lanes that cut over before this amendment** are held to the same bar. A baseline captured for
   the week bar is no longer needed for any bar. Each such lane's Mac job must be disabled and
   checked as cut-over step 4 checks under decision item 2, and until it is, decision item 1 doesn't
   count the lane as moved. The execution watch and the landing ledger were disabled on 2026-11-12,
   in an interactive session with Devon's approval.
6. **What stays:**
   - The Healthchecks dead-man and outcome alarms, pointed at the same checks (year plan Phase 1;
     decision 3).
   - The paired pass and its comparison (cut-over steps 2 and 4).
   - The Mac job booted out and disabled, and the check that the last ping came from the container
     (cut-over steps 2 and 4).
   - Rollback for every moved lane until increment 6 ("Rollback for one lane").
   - The executor's `rotation-heartbeat:<store kind>` alert-hub schedule
     (`docs/operations/rotation-executor.md`), as an alarm, not as a bar.
7. **No new authority.** The amendment grants no credential, scope or permission. The baseline
   needed the broad Healthchecks account to read each check's ping log; that read is no longer
   needed. No lane holds the Management API key, and no decision grants HQ the broad account
   (decision 3, lines 677-687 and 700-702).

### Passages this amendment changes

None is rewritten. Once it's accepted, it governs these passages, cited by line at `519bb23f`:

| Passage | Disposition |
|---|---|
| Context, "and a lane survives the Mac being off for a week" (line 24) | Superseded. The bar is the year plan's done-when as quoted in this amendment's Context. The rest of the paragraph stands. |
| Decision 6's heading, "Migration order, cut-over, rollback and the week bar" (line 828) | "and the week bar" no longer names a bar. The heading's other parts stand. |
| Increment 6, "This increment starts only after the week bar has passed, because it ends the rollback path" (lines 1048-1049) | Superseded by decision item 3: it starts once increments 2 to 5 have cut over. It still ends the rollback path. |
| Cut-over step 1, "Then capture the lane's week-bar baseline ... not HQ ("The week bar")" (lines 1119-1122) | Withdrawn. The rest of step 1 stands, the ADR-0055 amendment 23 paragraph (lines 1124-1126) included. |
| Cut-over step 2, "A ping from step 2 lands after the baseline and before the week, so it counts in neither" (line 1148) | Withdrawn. The list of which reporting runs ping the real check (lines 1134-1146), the no-running-pass check, the boot-out (with the disable the next row adds) and the state comparison stand. |
| Cut-over step 2, "Then boot out the Mac agent with `launchctl bootout gui/<uid>/com.devon.<lane>`, and keep its plist" (lines 1151-1152) | Superseded by decision item 2: boot out the agent, then run `launchctl disable gui/<uid>/com.devon.<lane>`, and keep its plist. |
| Cut-over step 3, "that down event falls before the week starts, so it counts in neither the baseline nor the week" (lines 1161-1162) | Withdrawn. "If the gap outlasts the check's grace, the check goes down" stands. |
| Cut-over step 4, "and that the Mac agent isn't loaded" (line 1164) | Superseded by decision item 2: the check also confirms that `launchctl print-disabled gui/<uid>` lists `com.devon.<lane>` as disabled. |
| "Rollback for one lane", "Then `launchctl bootstrap` the kept plist" (lines 1190-1191) | Superseded by decision item 2: run `launchctl enable gui/<uid>/com.devon.<lane>` before the bootstrap, because launchd refuses to bootstrap a disabled service. |
| "Rollback for one lane", "Increment 6 requires a passed week bar, so no lane loses its rollback before the week has shown the move holds" (lines 1245-1246) | Superseded. Rollback stands for every moved lane until increment 6, which starts once increments 2 to 5 have cut over. |
| "The week bar", the bar, its measure and the baseline (lines 1248-1279) | Superseded by decision item 1. The checks before increment 2 that the passage asks for (flip retention and the ping-log limit, lines 1262-1263 and 1269-1270) served only the baseline and are withdrawn. The last sentence's fact stands, consistent with decision 3's outcome-alarm bullet (lines 741-743): a delivered finding pings success, so a `/fail` means a pass that couldn't do its job. |
| "The week bar", what goes quiet that week (lines 1281-1284) | Moot: no week is run, so nothing is excluded from it. Where each agent runs is unchanged. |
| "The week bar", the executor's second week and increments 8 and 9's weeks (lines 1286-1293) | Superseded by decision item 4. The heartbeat schedule stays as an alarm (decision item 6). Increment 7 still follows increment 6. |
| "The week bar", the phase's done-when paragraph (lines 1295-1310) | Stands, except "and each week has passed" (line 1299): the phase is done when increments 8 and 9 have moved change-window and infra-drift, each at its cut-over checks. Everything else in the paragraph stands: SDS HQ outside the phase (D-9), no moved lane's input left on the Mac (decision 5), and the classification of the other agents. |
| "The week bar", the evidence (lines 1312-1318) | Withdrawn, the read-only key's verify included (lines 1315-1316). "No lane holds it" and "no decision grants HQ the broad account" stand, as decision 2 (lines 487-490) and decision 3 (lines 677-687 and 700-702) state them. |
| Amendment 3, "Relates to", "the week bar" (line 1900) | Moot. |
| Amendment 3, decision item 1's row, "The week bar's seven daily lanes" and its six and nine (line 1983; its lines 1262-1264 are at `531d42c2`) | Moot: no lane needs a `/fail` baseline. |
| Amendment 3, decision item 5's "The week bar" bullet (lines 2045-2049) | Moot: no week is run. The fork-sync watch and fork hardening still stay on the Mac. |
| Amendment 3, "Passages this amendment changes", "the week bar (1262-1264, 1271-1275)" (lines 2129-2130, cited at `531d42c2`) | Moot. Amendment 3's other citations stand. |

Amendments 1, 2, 4 and 5 name no week-bar passage, so none of their text is changed.

## Amendment 7 (2026-11-13): the factory-events chain moves to a VPS database

- **Status:** Accepted on merge. HQ merges it only after a clean Opus whole-branch review (HQ
  D-12, limit 3). The ADR's own status is unchanged.
- **Decided by:** Devon, in interactive HQ sessions on 2026-11-13 (sds-hq `state/decisions.md`):
  D-40 B ("Approvals write their package.approved event straight into the shared factory-events
  database chain, and the carrier verifies there; append-only credentials for the approvers,
  read-only for the carrier"), D-42 A ("A new Postgres resource on the VPS (Coolify, Flavor B
  pattern) becomes the factory-events chain of record ... The Mac nightly factory-events job ships
  its other events into the same VPS chain, so there is one chain of record, and the Mac database
  retires once it does"), D-43 B (only the approvers and the Mac nightly hold the append-only role)
  and D-44 A (the read-only role also serves Devon's hand paths on the Mac). ADR-0055 amendment 24
  quotes each in full and states the mechanism. Amendment 8 records D-47, which revises D-44 A to
  D-44 B: only the work carrier reads the chain.
- **Relates to:** decision 5 ("Agents outside this repository"), "The week bar"'s done-when
  paragraph, increment 4, Q7, and ADR-0055 amendment 24.

Line numbers in this amendment are this file's at orchestrator `2cf43757`, before the amendment
was appended. The amendment appends only: no passage before it is rewritten. Where this amendment
and an earlier passage disagree, this amendment governs.

### Context

D-9 kept `com.devon.factory-events` outside Phase 1 (lines 816-818 and 1522-1526). Increment 4
moves the work carrier to the VPS, and the carrier refuses an intake whose `package.approved`
event it can't find in its own machine's chain (ADR-0055 amendment 22, "The chain the carrier
reads"). That chain is a file on the Mac, and the Mac's factory-events database is filled from it
once a night, with one owner role and no single-event append (ADR-0055 amendment 24, Context).
So a carrier on the VPS can't verify any approval. Neither proposer verifies against the chain:
`src/bump_proposer` and `src/rotation_proposer` name no `verify-approval`, `factory_events`,
`events.jsonl` or `FACTORY_EVENTS`. The bump proposer's problem is on the write side. Its
`approve --by-policy` (`src/bump_proposer/standing.py`, line 397) emits through the
intent-packages emitter, and `store.append_event` writes to `$FACTORY_EVENTS_HOME/events.jsonl`,
creating the directory and starting at `seq` 1 from the genesis hash when the file is empty
(security-standards `store.py`, lines 20-25 and 60-80). In a container (step 3f-4) whose emit
reaches `factory_events`, the approval lands in that container's own file, which the carrier
never reads. (If the emitter can't locate security-standards, it raises `EmitError` and the
approval aborts: intent-packages `emitter.py`, lines 70-79.) The rotation proposer emits no
approval (D-43 B; ADR-0055 amendment 24 item 5). D-40 B and D-42 A widen D-9 to fix that.

### Decision

1. **What comes into Phase 1, and only this.** These parts of factory-events:
   - the VPS database resource that becomes the chain of record;
   - its two roles, append-only and read-only, and their two BWS secrets;
   - the append path (a new `factory_events` append function, used by the intent-packages emitter
     and the nightly's adapters) and the verify path (the database walk that intent-packages and
     the orchestrator verify approvals with);
   - the Mac nightly appending its events into the VPS chain;
   - the Mac database's retirement.

   Nothing else of factory-events moves. SDS HQ stays outside this phase (D-9).
2. **The nightly job stays on the Mac.** Its high-power adapter reads this Mac's audit log
   (security-standards `adapters/high_power.py`, line 21, `~/.claude/audit/high-power-actions.jsonl`),
   so it adapts this Mac's events and can't run elsewhere. Only its target changes: from step FE-5
   its `adapt` appends through the append function into the VPS chain, it verifies that chain, and
   it no longer runs `ship` to the Mac database. Its `chain:factory-events` observation stays. It
   isn't a lane that acts on a member, so it stays outside the phase bar, as before.
3. **The steps, in order.** Increment 4 runs these after the change proposer (3f-1) and before the
   work carrier's lane code (3f-2). None of the code steps changes what runs until FE-5 sets the
   DSNs.
   - **FE-1, security-standards code.** The database store in `factory_events`: the schema with its
     uniqueness and its trigger, the append function, the database verify, the history import
     (which replays the Mac file in order through the append path and refuses unless the imported
     head equals the file's head; who runs it, under which role, is Devon's named decision before
     act 5, now named by amendment 8), the roles script for FE-4, and the anchor's new home
     (ADR-0055 amendment 24, "Open before building"). `emit`, `adapt` and `verify` use the database when the
     append or read DSN is set. Tests run against a disposable Postgres
     (`make check-integration`).
   - **FE-2, intent-packages.** The emitter passes the append DSN through to `factory_events emit`,
     and `default_chain_checker` reads the database under the read DSN, raising `ChainUnavailable`
     on any error. No file fallback.
   - **FE-3, orchestrator.** `package_sources` verifies against the database under the read DSN
     (`_verify_factory_chain` and `_iter_package_approval_events`) and refuses on any error.
     `tests/architecture/test_intent_edges.py` changes with it (`ENVIRONMENT`, line 38;
     `EVENTS_FILE_PARTS`, line 43; `READERS`, line 42). The bump proposer's launcher passes the
     append DSN to `approve --by-policy`. The operations docs that name `~/.factory/events.jsonl`
     (`docs/operations/package-intake.md`, line 126) change in the same step.
   - **FE-4, the database resource (production act).** HQ runs it as an announced act.
   - **FE-5, the cut-over and the nightly's target switch (production act).** HQ runs it as an
     announced act.
   - **FE-6, the Mac database's retirement (production act).** HQ runs it as an announced act, only
     after FE-1's anchor is live and a nightly has run against the VPS chain.
   - **Then the work carrier's lane code (3f-2)**, with the read DSN in its container, and later the
     bump proposer (3f-4, with the append DSN) and the rotation proposer (3f-5, with no chain
     credential: ADR-0055 amendment 24 item 5).
4. **The production acts, named so HQ may run them.** These are the only production acts this
   amendment names:
   1. **Create the Postgres resource** on `prod-vps`: a Coolify database resource, Flavor B
      pattern, with no public FQDN and its port reachable only over the tailnet (FE-4).
   2. **Create its roles**: run FE-1's roles script as the owner, once, creating the schema, the
      trigger, the append-only role and the read-only role (FE-4). This never runs in an image build
      step.
   3. **Create the two BWS secrets**, the append DSN and the read DSN, in the BWS project Devon
      names for each, and grant a read on each secret to exactly the machine accounts he names
      (FE-4). This amendment chooses no project and no account, and the act stops until he has
      named them (ADR-0055 amendment 24 item 11 and "Open before building"). The append DSN goes
      to no shared or broad identity, such as the nightly's `BWS_ACCESS_TOKEN_BROAD` or Keychain
      `BWS_ACCESS_TOKEN_VPS_BACKUP` identity (security-standards `factory-events-nightly.sh`, lines
      104-105), unless his decision says so, because that would widen D-43 B's holders. Their
      UUIDs go in each consumer repository's `.bws-secrets.toml` and their rows in orchestrator
      `.cred-consumers.toml` in the same act. **Amendment 8 names the project and the accounts**
      (D-45 A as revised by D-47), so the act no longer stops for them.
   4. **Add the vps-backup target** for the database (FE-4), before any data arrives. A database with
      data needs one; the `backup-integration` skill names the recipe.
   5. **The cut-over** (FE-5): pause the writers (the cycle's approvals, the bump proposer lane, and
      the nightly, which runs at 03:30); verify the Mac file; import it, run by the actor and under
      the role Devon has named (the act stops before the import until he has); check the VPS head
      equals the file's; seal the file (ADR-0055 amendment 24 item 10); set the append DSN for
      Devon's shell, the cycle, the bump proposer and the nightly, and the read DSN for the work
      carrier and Devon's hand paths; merge the changes that remove the file path from the three
      repositories; run one `verify` on each machine; resume the writers. This is **the nightly's
      target switch**. **Amendment 8 names the import's actor and role** (D-46 A) and the DSN
      holders: the append DSN goes to `factory-events-session` in place of "Devon's shell", and the
      read DSN to the work carrier only (D-44 B).
   6. **Retire the Mac database** (FE-6): stop and remove `factory-events-db` on the Mac's OrbStack
      host engine after a final dump kept in the Mac's backup, remove `FACTORY_DB_DSN` from
      `~/.factory/env`, and retire its secret's reference.
5. **Network.** The database isn't exposed publicly, so Infra Brain rule 212 (HTTPS for public
   FQDNs) has nothing to apply to. Containers on `prod-vps` reach it over Coolify's network (the
   external `coolify` network); the Mac reaches it over the tailnet (`ovh-vps1`).
6. **No new authority beyond the four answers.** The amendment grants the two roles to the holders
   ADR-0055 amendment 24 items 4 and 9 name, and nothing else. No lane gets the owner, and no
   identity outside those lists gets either secret. Each secret's BWS project, the machine
   accounts that read it, and the history import's actor and role are Devon's named decisions,
   taken before acts 3 and 5; this amendment makes none of them. Amendment 8 records Devon's
   answers to all three.

### Open before building

ADR-0055 amendment 24's "Open before building" lists each verify item and the step that settles
it: the anchor's home, the adapters' dedupe, the walk's cost and `emit`'s stdout (FE-1), and the
rotation class and the tailnet-only port (FE-4). It also lists two named decisions of
Devon's: each DSN's BWS project and reading accounts, before act 3 (FE-4), and the history
import's actor and role, before act 5's import (FE-5). FE-6 waits for the anchor. Both named
decisions are answered (amendment 8).

### Passages this amendment changes

None is rewritten. Once it's accepted, it governs these passages, cited by line at `2cf43757`:

| Passage | Disposition |
|---|---|
| Decision 5, "Agents outside this repository", "and factory-events and SDS HQ itself are outside this phase" (lines 816-818) | Narrowed: factory-events comes into the phase to the extent of decision item 1. SDS HQ stays outside. "None is a Mac exception" stands: the nightly stays on the Mac as an infra job, not as an exception. |
| The `factory-events` row, "into the factory events chain (`FACTORY_DB_DSN`)" and "Outside this phase (D-9)" (line 824) | Superseded by decision item 2: from FE-5 the nightly appends into the VPS chain and no longer ships to `FACTORY_DB_DSN`. Its disposition is "stays on the Mac; its chain moves to the VPS (Amendment 7)". The row's other text stands. |
| "The week bar", the done-when paragraph, "SDS HQ also acts on members ... (D-9)" (lines 1297-1298) | Stands. It names SDS HQ only. |
| "The week bar", the done-when paragraph, "Every other `com.devon.*` launch agent ... stays where it is" (lines 1301-1302) | Stands for the nightly job, which stays on the Mac. Its chain's database moves under decision item 1. |
| Increment 4, "change proposer, work carrier, the rotation decomposer, then bump and rotation proposers" (lines 1023-1024) | Extended by decision item 3: FE-1 to FE-6 run after the change proposer and before the work carrier. |
| Q7, "`com.devon.factory-events` and SDS HQ itself stay outside this phase" (line 1526) | Narrowed as decision 5's row above: the factory-events parts of decision item 1 are in the phase. The rest of Q7 stands. |

Amendments 1 to 6 name no factory-events passage, so none of their text is changed.

## Amendment 8 (2026-11-13): the factory-events chain's readers, accounts and import actor are named

- **Status:** Accepted on merge. HQ merges it only after a clean Opus whole-branch review (HQ
  D-12, limit 3). The ADR's own status is unchanged.
- **Decided by:** Devon, in interactive HQ sessions on 2026-11-13 (sds-hq `state/decisions.md` at
  `74c3507`): D-47 ("Lets update D-44 as you recommend, and I agree with your take on D-45"),
  recorded as "D-44 becomes B (only the work carrier reads the chain); D-45 append-DSN reader for
  approvals is a new narrow account factory-events-session (own Keychain item, append DSN only, no
  read-DSN grant)"; D-45 A as D-47 revises it (a new BWS project `Factory Events` holding only the
  two DSNs, and the accounts that read each); and D-46 A ("an interactive session runs the import
  once under the append-only role, reading the append DSN by UUID for that one command"). ADR-0055
  amendment 25 quotes each in full and states what it supersedes in ADR-0055 amendment 24.
- **Relates to:** Amendment 7 (its "Decided by", decision items 3, 4 and 6, and "Open before
  building") and ADR-0055 amendments 24 and 25.

Line numbers in this amendment are this file's at orchestrator `da044d6d`, before the amendment
was appended. Apart from the one-line pointers to it in Amendment 7, the amendment appends only.
Where this amendment and an earlier passage disagree, this amendment governs.

### Decision

1. **Only the work carrier reads the chain (D-44 B).** The read DSN goes to the work carrier
   alone: `sds-operator` fetches it for the Mac carrier until step 3f-2, and the work-carrier lane
   account in its container after. Devon's hand paths get no read DSN. After FE-5 a hand intake
   can't verify an approval and fails closed (ADR-0055 amendment 25 item 1).
2. **Act 3 is named and no longer stops for a decision.** Its BWS project is `Factory Events`,
   holding only the two DSN secrets. Its grants are exactly ADR-0055 amendment 25 item 3's:
   - the append DSN to the SDS HQ account, `factory-events-session` (a new narrow account with its
     own Mac Keychain item, the append DSN only), the bump-proposer lane account (the Mac launcher
     reads it from its own Keychain item until the lane moves), and `factory-events-nightly` (a new
     narrow account with one more Mac Keychain item);
   - the read DSN to the work-carrier lane account, and to `sds-operator` for the Mac carrier
     until it moves.

   The append DSN goes to none of `sds-operator`, `BWS_ACCESS_TOKEN_BROAD` or
   `BWS_ACCESS_TOKEN_VPS_BACKUP`. Act 3 still stops on anything else Amendment 7 makes it stop
   on, and still records each secret by UUID, never by name.
3. **Act 5's import is named (D-46 A).** An interactive session runs the history import once
   under the append-only role, reading the append DSN by UUID for that one command, while the
   writers are paused. It isn't a standing holder of the append role. D-46 A names no account for
   the session's fetch, and the session can read the append DSN only through an account item 2
   grants, so the import adds no grant.
4. **Act 5's DSN step.** "Set the append DSN for Devon's shell, the cycle, the bump proposer and
   the nightly, and the read DSN for the work carrier and Devon's hand paths" becomes: set the
   append DSN for interactive sessions' approvals (through `factory-events-session`), the cycle,
   the bump proposer and the nightly, and the read DSN for the work carrier only.
5. **FE-3's scope is unchanged.** The orchestrator's `package_sources` read path still serves the
   carrier's `emit-intake-payload`, so FE-3 changes the same code; only the read DSN's holders
   shrink (ADR-0055 amendment 25 item 5).
6. **No new authority beyond the answers.** This amendment grants the project and the six account
   reads in item 2 and the one-time import in item 3, and nothing else.

### Passages this amendment changes

None is rewritten. Each carries a one-line pointer to this amendment. Once it's accepted, it
governs these passages, cited by line at `da044d6d`:

| Passage | Disposition |
|---|---|
| Amendment 7, "Decided by", "D-44 A (the read-only role also serves Devon's hand paths on the Mac)" (line 2755) | Superseded by decision item 1: D-47 revised D-44 to B. |
| Amendment 7, decision item 3, FE-1, "who runs it, under which role, is Devon's named decision before act 5" (line 2807) | Answered by decision item 3. |
| Amendment 7, decision item 4, act 3, "in the BWS project Devon names ... the act stops until he has named them" (lines 2836-2844) | Superseded by decision item 2. |
| Amendment 7, decision item 4, act 5, the import's actor and the DSN holders (lines 2847-2854) | Superseded by decision items 3 and 4. |
| Amendment 7, decision item 6, "Devon's named decisions ... this amendment makes none of them" (lines 2863-2865) | Answered by decision items 2 and 3. |
| Amendment 7, "Open before building", the two named decisions (lines 2871-2873) | Answered by decision items 2 and 3. |

## Amendment 9 (2026-11-14): increment 9 pulls follow-up minting out of infra-drift

- **Status:** Accepted on merge. HQ merges it only after a clean Opus whole-branch review (HQ
  D-12, limit 3). The ADR's own status is unchanged. Devon decided it on 2026-11-13, in an
  interactive HQ session: "Lets make it so that cycle stops planning (or rather never starts)
  increment 9 as is. We do need to pull follow-up minting apart from infra-drift, so that maybe is
  a replacement for inc 9?" Then: "I think this will need to come as an add on and not done as
  part of this phase."
- **Decided by:** Devon, on 2026-11-13 (HQ D-41, sds-hq `state/decisions.md` at `700944d`). D-41
  revisits D-9 for infra-drift only.
- **Relates to:** decision 5 (the rotation proposer's Mac inputs, "Agents outside this
  repository" and the `infra-drift` row), decision 6 (increments 4 and 9, "The week bar" and the
  phase's done-when), Q7, Amendment 6 (decision item 4 and two rows of its table), ADR-0007 ("Scheduled
  trigger") and `docs/operations/follow-up-scheduling.md`

Line numbers in this amendment are this file's at orchestrator `5a781dbb`, before the amendment
was appended. The amendment appends only: no passage before it is rewritten, so each line cited
here still holds the text it names. Where this amendment and an earlier passage disagree, this
amendment governs once it's accepted.

### Context

D-9 moved `com.devon.infra-drift` off the Mac as increment 9 (lines 1079-1085, Q7). infra-drift
is the daily 03:00 drift audit, infraops-mcp-server's `scripts/drift-audit.sh`. Most of what it
does is Devon's infrastructure tooling: it audits prod and dev Coolify, applies safe remediations,
syncs App Brain, pushes escalations to change-manager, posts drift-reporter observations, and runs
the Mac's security scan. One step is the SDS's: WS-P2.8's follow-up minting (lines 152-180 of
`scripts/drift-audit.sh` at infraops-mcp-server `c3359bf`). That step reads the orchestrator SYSTEM
bearer (`221a48d5-…`) through `sds-operator` and runs
`node dist/cli/orchestrator-cli.js mint-follow-ups`, which calls `POST /api/v1/follow-ups/mint`
(SYSTEM only). ADR-0007's "Scheduled trigger" placed that step in infra-drift because it was
already scheduled; the orchestrator itself runs no scheduler.

### Decision

1. **infra-drift stays on the Mac.** `com.devon.infra-drift` stays on the Mac in Phase 1 as
   Devon's infraops tooling. Its audits, remediations, App Brain sync, change-manager escalations,
   drift-reporter observations and security scan stay as they are, with the identity they use
   today. Devon placed it outside this phase (D-41), as D-9 placed SDS HQ, so Phase 1's done-when
   doesn't count it. It isn't a Mac exception under decision 4. Its credential scan's read of
   the rotation state is carried by "Verify in increment 4".
2. **Increment 9 moves follow-up minting out of infra-drift into SDS.** The step at
   `scripts/drift-audit.sh` lines 152-180 (`c3359bf`) leaves infra-drift, and the SYSTEM bearer
   leaves infra-drift with it. Increment 9's own plan decides where minting runs in SDS, by
   Devon's decision, and which identity holds the SYSTEM bearer there. The plan also states the
   increment's done check, and the repositories and documents it changes ("Each increment's plan
   records which repositories it changes and which identities it creates", line 1087).
3. **The phase's done-when, for these two agents.** Where the done-when names change-window and
   infra-drift, it now reads: the phase needs increment 8 to have moved change-window at its
   cut-over checks (Amendment 6 decision item 4), and increment 9 to have moved follow-up minting
   out of infra-drift at the check its plan states. infra-drift's own move isn't needed. Every
   other part of the done-when stands as the ADR and Amendments 6 and 7 state it, decision 5's
   rule that no moved lane's input may still live on the Mac included.
4. **The infrastructure breakout is an add-on after Phase 1.** Moving infra-drift's
   infrastructure work out (an App Wardens infrastructure operator, Devon's own infraops, and
   change-manager becoming fully App Wardens') is planned outside this ADR, in Devon's
   Infrastructure breakout plan.
5. **No new authority.** The amendment grants no credential, scope or permission. It creates no
   identity and grants no BWS read. Increment 9's plan decides the SYSTEM bearer's new holder.

### Verify in increment 4

- **How infra-drift's credential scan reads the rotation state (increment 4, the rotation
  proposer's move).** Decision 5 gives the rotation proposer's increment the choice of where
  `cred-rotation-state.json` lives (line 802). infra-drift stays on the Mac, and its 03:00
  credential scan keeps reading `~/.config/infra-drift/cred-consumers.list` and
  `cred-rotation-state.json` there (infraops-mcp-server `src/cli/security-drift-cli.ts`, lines
  275-279 at `c3359bf`). The state file has two writers, both infraops subcommands:
  `record-rotation` (line 122), which the rotation proposer runs when the work watcher retires an
  approved revision's record (`docs/operations/rotation-proposer.md`, line 123), and
  `resolve-exposure` (line 106), which a person runs. If increment 4 moves the state file off the
  Mac, the Mac copy stops receiving those writes, and infra-drift's scan reports from a stale file.
  So when increment 4 decides the state file's home, it also decides how infra-drift's Mac
  credential scan reads it, or what that scan stops reporting. Increment 4 can't finish until it
  records that decision (HQ's merge-bar standing answer of 2026-11-06, limit 1). This amendment
  decides neither.

### Verify in increment 9

- **Where minting runs and under which identity (increment 9).** This gap is carried into
  increment 9, which can't finish until its plan records where minting runs, under which identity,
  and Devon's decision on both (HQ's merge-bar standing answer of 2026-11-06, limit 1). Until
  increment 9 lands, infra-drift's step keeps minting.

### Passages this amendment changes

None is rewritten. Once it's accepted, it governs these passages, cited by line at `5a781dbb`:

| Passage | Disposition |
|---|---|
| Decision 5, the rotation proposer's `cred-findings` inputs, `cred-consumers.list` and `cred-rotation-state.json` in `~/.config/infra-drift/` (lines 796-803) | Stands. The rotation proposer moves, so its increment must still carry the list as data in a clone and decide where the state file lives; a file only on the Mac would still tie the lane to it. infra-drift staying on the Mac keeps its own credential scan reading the Mac copies. How infra-drift's Mac credential scan reads the state once that increment decides its home is carried by "Verify in increment 4". |
| "Agents outside this repository", "change-window and infra-drift move off the Mac as increments 8 and 9" (lines 815-816) | Superseded for infra-drift by decision item 1: infra-drift stays on the Mac, outside this phase. change-window still moves as increment 8. factory-events and SDS HQ are as Amendments 7 and 8 leave them. "None is a Mac exception" stands, infra-drift included. |
| The `change-window` row (line 822) | Stands. Its shared Keychain identity with infra-drift (`BWS_ACCESS_TOKEN_INFRA_DRIFT`) is increment 8's to replace for change-window. |
| The `infra-drift` row, "Moves, increment 9 (D-9)" (line 823) | Superseded by decision item 1: it stays on the Mac, outside this phase (D-41). The row's description stands as a description of today. Once increment 9 lands, "mints due follow-up units as SYSTEM (read through `sds-operator`)" and "minted units" no longer describe it. |
| Increment 8, "it shares the Keychain BWS identity `BWS_ACCESS_TOKEN_INFRA_DRIFT` with infra-drift, so it needs its own" (lines 1076-1077) | Stands. infra-drift keeps the shared identity; change-window gets its own. |
| Increment 9, "`com.devon.infra-drift` moves off the Mac" (lines 1079-1085) | Superseded by decision item 2. Its identity questions are withdrawn: infra-drift keeps its BWS identity, and the security scan stays whole. The SYSTEM bearer question moves to increment 9's plan. "It reads the same `~/.config/infra-drift/` list and rotation state as the rotation proposer (decision 5)" stands as a fact; the first row covers it. |
| Increment 9, "It is the drift audit that feeds the rotation proposer" (line 1081) | Withdrawn as a description. At orchestrator `5a781dbb`, the rotation proposer runs infraops's `cred-findings` itself, from its own infraops checkout, and reads the due credentials from its JSON; it opens neither file directly (`docs/operations/rotation-proposer.md`, lines 12-17). infra-drift hands it no data. The two share inputs: both read the same `cred-consumers.list` and `cred-rotation-state.json`, and the drift audit writes neither. The shared state file is the subject of "Verify in increment 4". |
| "The week bar", "Increments 8 and 9 each run a week of the same kind for the agent they move" (lines 1292-1293) | Already superseded by Amendment 6 decision item 4. For increment 9 it is moot as well: it moves no agent. |
| "The week bar", the phase's done-when paragraph, "After increment 7, change-window and infra-drift still act on members from the Mac until increments 8 and 9 move them" and "The phase is done when increments 8 and 9 have moved those two" (lines 1296-1299) | Superseded by decision item 3. The rest of the paragraph stands: SDS HQ outside the phase (D-9), the rotation proposer's list and state and every other moved lane's input off the Mac (lines 1299-1301, decision 5), and the classification of the other agents. |
| Q7, "`com.devon.change-window` and `com.devon.infra-drift` ... move off the Mac as increments 8 and 9" (lines 1522-1526) | Superseded for infra-drift by D-41 (decision items 1 and 2). change-window's move as increment 8 stands. |
| Amendment 6, "Relates to", "increments 6 to 9" (line 2658) | Stands. Increment 9 is still an increment of decision 6. |
| Amendment 6, decision item 4, "Increments 7, 8 and 9 are each done at their own cut-over checks ... increments 8 and 9's plans state theirs" (lines 2694-2696) | Stands for increments 7 and 8. For increment 9, no Mac job is booted out or disabled, so Amendment 6 decision item 1's paired pass and bootout don't apply to `com.devon.infra-drift`; increment 9's plan states its done check (this amendment's decision item 2). |
| Amendment 6's row, "the executor's second week and increments 8 and 9's weeks" (line 2734) | Stands, and is moot for increment 9, which moves no agent. |
| Amendment 6's row, the phase's done-when paragraph, "the phase is done when increments 8 and 9 have moved change-window and infra-drift, each at its cut-over checks" (line 2735) | Superseded by decision item 3. The rest of the row stands. |

Amendments 1 to 5, 7 and 8 name no passage about infra-drift's move, so none of their text is
changed. ADR-0007's "Scheduled trigger" and `docs/operations/follow-up-scheduling.md` still say
infra-drift runs the daily pass; increment 9 changes them when minting moves.

## Amendment 10 (2026-11-14): increment 9's minting runs in a new follow-up-mint lane

- **Status:** Accepted on merge. HQ merges it only after a clean Opus whole-branch review (HQ
  D-12, limit 3). The ADR's own status is unchanged.
- **Decided by:** Devon, on 2026-11-14 in HQ's Slack channel, answering HQ D-49 (sds-hq
  `state/decisions.md` at `2b35ab4`). D-49 asked: "ADR-0059 increment 9 (D-41): follow-up minting
  leaves infra-drift. Where does it run in SDS? (A) A new single-purpose VPS lane,
  follow-up-mint: runs 'orchestrator mint-follow-ups' once a day, its own Coolify resource,
  Healthchecks check, and lane BWS identity that reads only the SYSTEM bearer (one more reader of
  it; you mint its token as for every lane). (B) One non-fatal step inside a lane that already
  reads the SYSTEM bearer, the rotation decomposer: no new identity, resource, or check, but
  minting rides on an unrelated lane's schedule and alarms again, the coupling D-41 undoes. (C) The
  deployed orchestrator mints on its own schedule: reverses ADR-0007's loop-free posture." The
  recorded answer is "new single-purpose VPS lane" (2026-11-14T13:23:46Z). The decision is about
  this lane only and states no general rule for lanes.
- **Relates to:** Amendment 9 (decision items 2, 3 and 5, "Verify in increment 9", and its rows on
  increment 9 and on Amendment 6 decision item 4), decision 1 and Amendment 1 (the lane image, one
  Coolify compose resource per lane), decision 2 (the lane identity and the two TOML files),
  decision 3 (the lane's check and ping-URL secret), Amendment 2, the cut-over (line 1089),
  ADR-0007 ("Scheduled trigger") and `docs/operations/follow-up-scheduling.md`

Line numbers in this amendment are this file's at orchestrator `cc6d9afd`, before the amendment
was appended. The amendment appends only: no passage before it is rewritten, so each line cited
here still holds the text it names. Where this amendment and an earlier passage disagree, this
amendment governs once it's accepted.

### Context

Amendment 9 moved follow-up minting out of infra-drift into SDS and left increment 9's plan to
decide, by Devon's decision, where minting runs and which identity holds the SYSTEM bearer there
(decision item 2, lines 3003-3008). Today the step is lines 152-180 of infraops-mcp-server's
`scripts/drift-audit.sh` at `c3359bf`. It reads the SYSTEM bearer (`221a48d5-…`) with the
`sds-operator` account's Keychain token, sets the key id `orchestrator-system`, and runs
`node dist/cli/orchestrator-cli.js mint-follow-ups` as a non-fatal step. That Node CLI is
infraops-mcp-server's (`src/cli/orchestrator-cli.ts`) and posts
`{"idempotency_key": …, "expected_version": 0}` to `POST /api/v1/follow-ups/mint`, which only
SYSTEM may call (`docs/operations/follow-up-scheduling.md`). The orchestrator runs no scheduler of
its own (ADR-0007, "Scheduled trigger"). D-49 decides the placement.

### Decision

1. **Follow-up minting runs in a new single-purpose VPS lane, `follow-up-mint`.** It follows the
   pattern every moved lane uses: the pinned lane image (decision 1), its own Coolify compose
   resource, `sds-lane-follow-up-mint` (Amendment 1), and its lane BWS token and ping URL issued
   as ADR-0055 amendment 19 issues them (Amendment 2). No person pastes the ping URL: it's read
   from the Healthchecks Management key's check listing, as decision 3 is narrowed by ADR-0055
   amendment 23 (line 714). It runs one minting pass once a day. Its
   hour is increment 9's to set. It has its own Healthchecks check, a new one: unlike the moved
   lanes, it has no Mac check to keep (decision 3, "Every lane keeps its check", lines 675-676). Its
   check is created at install time by `sds_upsert_check`, as decision 3 creates or updates every
   lane's check (lines 699-701).
2. **The command it runs.** The lane runs the orchestrator's own Python CLI, `orchestrator
   mint-follow-ups` (`src/orchestrator/cli.py`, line 765; the `orchestrator` entry point is
   `pyproject.toml` line 32). The lane image is built `FROM` the orchestrator image (decision 1,
   line 92), whose runtime stage puts the venv on `PATH`, so the command is in the image. It posts
   the same route and body as the Node CLI that infra-drift runs. The Node CLI isn't in the lane
   image: it lives in infraops-mcp-server's `dist/`, which the image doesn't carry. The lane
   doesn't run it.
3. **Authority, exactly.** D-49 approves, for this lane and nothing wider:
   - one new lane BWS machine account for `follow-up-mint` with exactly two read grants: the
     orchestrator SYSTEM bearer (`221a48d5-…`), its one work secret, and its own ping-URL secret,
     the read decision 3 gives every lane identity (line 694). The ping-URL read is lane
     plumbing, not new authority: D-49 gives the lane its own check, and Devon's standing answer
     of 2026-11-10 treats a ping URL as configuration. The account reads no other secret;
   - one Coolify compose resource, `sds-lane-follow-up-mint`;
   - one Healthchecks check.

   The amendment grants nothing at merge. The account, its grants, the resource, the check and
   the lane token are created in increment 9's identity and cut-over steps (the cut-over, line
   1089), in the order Amendment 2 sets. `docs/operations/credentials.md` records the
   `sds-operator` machine account as the SYSTEM bearer's reader in the `SDS Operator` project
   (lines 63-68) and the infra-drift step's read through it (lines 88-92), and `.bws-secrets.toml`
   names the rotation-decomposer lane among its readers (lines 20-21). The new account is one more
   reader.
4. **Increment 9's done check (proposed for increment 9's plan to confirm).** Increment 9 is done
   when both hold:
   - the `follow-up-mint` lane's first scheduled pass mints the due follow-up units, or finds none
     due, and pings its check;
   - infra-drift's minting step is removed in infraops-mcp-server, by a pull request there in
     increment 9, so the SYSTEM bearer read through `sds-operator` leaves `drift-audit.sh`.

   No Mac job is booted out (Amendment 9's row on Amendment 6 decision item 4, line 3063). This
   proposes the check that Amendment 9 decision item 2 asks the plan to state. It isn't a new
   decision.

### Verify in increment 9

- **Amendment 9's "Verify in increment 9" item is answered in part.** D-49 records where minting
  runs (item 1) and under which identity (item 3), with Devon's decision on both. Increment 9 still
  can't finish until the done check in item 4 passes. Until then, infra-drift's step keeps
  minting.
- **The repositories and documents increment 9 changes (line 1087).** Its plan records them. At
  `cc6d9afd` no launcher for the lane exists: `scripts/run-follow-up-mint.sh` was deleted by Tier 3
  item 24b (`docs/operations/follow-up-scheduling.md`). So the plan names the lane's launcher,
  crontab line and compose file in this repository, its rows in `.bws-secrets.toml` and
  `.cred-consumers.toml` (decision 2, lines 655-657), and the pull request in infraops-mcp-server
  that removes the minting step.
- **How the lane calls the Python CLI.** The Python CLI reads `ORCHESTRATOR_API_URL`,
  `ORCHESTRATOR_API_TOKEN` and `ORCHESTRATOR_API_CREDENTIAL_KEY_ID` (`src/orchestrator/cli.py`,
  lines 43-45), where infra-drift's Node CLI reads `ORCHESTRATOR_MINT_TOKEN` and
  `ORCHESTRATOR_MINT_CREDENTIAL_KEY_ID`. The key id stays `orchestrator-system`, never
  `orchestrator-drift-reporter`. The Python CLI also requires `--idempotency-key`, which the route
  accepts and never reads; the plan says what the launcher passes.
- **Whether a failed pass fails the lane's check.** infra-drift's step is non-fatal and logs a
  WARN. The lane's failure handling isn't decided here; the plan states it. An alert-hub send
  from the lane would need a sender token, a secret item 3 doesn't grant, so it would need a new
  Devon decision.
- **ADR-0007's "Scheduled trigger" and `docs/operations/follow-up-scheduling.md` ("Running a
  pass")** still say infra-drift runs the daily pass, and `docs/operations/credentials.md` (lines
  88-92) still names `drift-audit.sh` as a SYSTEM bearer consumer. Increment 9 changes them when
  minting moves.
- **The order.** Increment 9 runs after increment 4. Decision 6, "Migration order, cut-over,
  rollback and the week bar" (line 828), lists the increments in that order under "Increments"
  (line 903): increment 4 at line 1023 and increment 9 at line 1079.

### Passages this amendment changes

None is rewritten. Once it's accepted, it governs these passages, cited by line at `cc6d9afd`:

| Passage | Disposition |
|---|---|
| Amendment 9, decision item 2, "Increment 9's own plan decides where minting runs in SDS, by Devon's decision, and which identity holds the SYSTEM bearer there" (lines 3005-3006) | Answered by decision items 1 and 3 (D-49). |
| Amendment 9, decision item 2, "The plan also states the increment's done check" (lines 3006-3007) | Proposed by decision item 4, for increment 9's plan to confirm. The plan still records its repositories and documents ("Verify in increment 9"). |
| Amendment 9, decision item 3, "increment 9 to have moved follow-up minting out of infra-drift at the check its plan states" (lines 3011-3012) | Stands. The check is decision item 4's, once increment 9's plan confirms it. |
| Amendment 9, decision item 5, "Increment 9's plan decides the SYSTEM bearer's new holder" (line 3020) | Answered by decision item 3: the `follow-up-mint` lane account. Amendment 9's own grant of nothing stands; this amendment also grants nothing at merge. |
| Amendment 9, "Verify in increment 9", "Where minting runs and under which identity" (lines 3041-3044) | Answered in part by this amendment's first "Verify in increment 9" item. Increment 9 still can't finish until decision item 4's done check passes. |
| Amendment 9's row on increment 9, "The SYSTEM bearer question moves to increment 9's plan" (line 3057) | Answered by decision item 3. |
| Amendment 9's row on Amendment 6 decision item 4, "increment 9's plan states its done check" (line 3063) | Proposed by decision item 4. No Mac job is booted out. |
| Amendment 9's closing paragraph, ADR-0007 and `follow-up-scheduling.md` "still say infra-drift runs the daily pass" (lines 3068-3069) | Stands, and carried as a "Verify in increment 9" item with `credentials.md` lines 88-92. |
| Decision 3, "Every lane keeps its check. A moved lane pings the same check" (lines 675-676) | Stands for moved lanes. `follow-up-mint` has no Mac predecessor, so it gets a new check (decision item 1). |
| Decision 5, "Classification of the 20 launch agents" (line 768) | Stands. `follow-up-mint` isn't a Mac launch agent, so the count of twenty doesn't change. |
