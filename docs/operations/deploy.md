# Deploying the orchestrator

How to build, migrate, swap and verify a production release, and what each check can and cannot see.

## The deploy command (primary procedure, SDS 1.1 item 7a)

Deploy the orchestrator with `scripts/deploy_orchestrator.py`. It runs the procedure the entries
below record (#305, "Binding the release", #61, #82, #194, #201) and refuses where they say a
deploy must not proceed. Run it from a worktree at the commit you are deploying, with that
worktree's own venv, and keep the state file in the session scratchpad, never a tracked path. Run
it under `rtk proxy`, because rtk swallows the stdout of `python -m`, and the result JSON goes to
stdout. Where `rtk` isn't on `PATH`, as in an unattended HQ cycle (a call to it exits 127 and runs
nothing), nothing rewrites the command: wherever this page says `rtk proxy uv run python -m …`,
run `uv run python -m …` directly.

It runs in two phases, with a `recheck` between them, because **five steps stay infraops calls
the deploying session makes itself**. The workspace rule (`~/Projects/CLAUDE.md`) routes every Coolify change through the
infraops MCP tools, and reading the host means `vps_exec`. A local program can call neither, so
`prepare` prints those calls exactly and `finish` takes their result:

1. `coolify_get_application(uuid="eqj5l7k705fhi12x9i74fqf0")`, before `prepare`, to read
   `health_check_enabled` and `health_check_path`, which `prepare` takes as arguments;
2. `coolify_update_application(..., docker_registry_image_tag=<tag>)`, the tag write;
3. the migration, `vps_exec` running alembic from the new image (only when a migration changed);
4. `coolify_deploy(...)`, the swap;
5. the container → image → `RepoDigest` read, through `vps_exec`.

Steps 2 to 4 are printed in #305's order: tag write, migration, `recheck`, swap. Two facts about
the window between them:

- **From the tag write on, any Coolify restart or redeploy of the application swaps the new image
  in**, whether or not the migration has run. Don't start the window while anything else might
  restart it.
- **`recheck`** runs immediately before `coolify_deploy`. It reads `/health/live` (still the
  recorded pre-swap revision; if it already serves the built commit, it says "already swapped,
  run finish"). It then reads `/health/ready` from the old image, which proves the migration ran:
  503 `migration_drift` when the deploy has a migration, 200 when it has none, and a refusal for
  any other answer. Last, it repeats the nothing-live check, because the first one is as old as
  the build (up to 45 minutes). **If it stops on a live unit after the migration, don't roll
  back.** The old image serves the migrated database, its readiness reports 503
  `migration_drift`, and no health check reads readiness (#61). Wait for the unit to finish, then
  run `recheck` again.

```bash
cd <worktree at the commit>   # uv sync --frozen first
rtk proxy uv run python -m scripts.deploy_orchestrator prepare --ref origin/main \
  --state <scratchpad>/deploy.json \
  --coolify-health-check-enabled <from step 1> --coolify-health-check-path <from step 1> \
  [--label <suffix>] [--expect-schema <ResponseModel>[.<field>] ...]
# ... the infraops calls it prints, with this immediately before coolify_deploy:
rtk proxy uv run python -m scripts.deploy_orchestrator recheck --state <scratchpad>/deploy.json
rtk proxy uv run python -m scripts.deploy_orchestrator finish --state <scratchpad>/deploy.json \
  --observed-digest sha256:<from the RepoDigest read>
```

**`prepare`** resolves the ref to a full sha and refuses one that is not on `origin/main`. It then
checks these, in order:

- **Safe by construction.** It refuses if the built commit's `Dockerfile` or `docker-compose.yml`
  names `/health/ready`, or if Coolify's check is enabled on it. In any of those cases,
  migrate-first is an outage (#61). It checks the pre-swap commit's files the same way, because
  the migration runs while the OUTGOING image is serving, so that image's health check is the
  one that would fire. It reads `git show <sha>:<file>`, not the working tree.
  `tests/architecture/test_container.py::test_no_health_check_consults_readiness` uses the same
  predicate, so the edit fails its pull request before it can reach a deploy.
- **Nothing live (#82).** It reads `/api/v1/status-ledger?include_inactive=true` with the SYSTEM
  bearer. It refuses (exit 2) while any unit is `claimed`, `executing`, `submitted` or
  `verifying`. It also reads `GET /api/v1/work-units/{id}/history` for every `ready` unit, and
  refuses when the last `dispatch.dispatched` event has no later event with `to_state: claimed`.
  Such a run is starting in its target repository but has not claimed yet, so the ledger cannot
  see it. `draft` units are reported in the output and do not block, because a draft is inert
  across a restart. A run dispatched and never claimed keeps its unit refusing until it is claimed
  or retired. The run's `factory-runner-pilot.yml` lives in the target repository, so a
  `gh run list` against `AlobarQuest/factory-runner` does not see it. The `bump-driver` lane starts
  runs hourly at :50 (the Mac job), so deploy away from :50. After the lane's cut-over its
  container starts them at :51 instead.
- **The pre-swap revision.** It reads `/health/live` once and records the result as
  `previous_commit`. It refuses if the outgoing image states no revision, and stops (exit 2) if
  production already serves the commit. It refuses a `previous_commit` the checkout does not hold
  (fetch first), and one that is not an ancestor of the built commit, because a rollback is not
  this procedure.
- **Every lane is current.** It refuses while any lane-phase run is unfinished, and while a
  registered lane's last finished run shows a tag other than the served one. The refusal names the
  lane and its two exits: re-run that lane's phases with `--tag` at the served tag, or roll that
  lane back (ADR-0059, "Rollback for one lane"). A registered lane with no passed `--add` is
  skipped. The check runs before the state file is saved, so a refusal dispatches nothing. "The
  lane phases", following, defines a registered lane.
- **The database is at the outgoing head.** It requires the old image's `/health/ready` to
  answer 200 before building. A 503 `migration_drift` here means the database is already ahead of
  the running image (a migration applied without its swap, or a hand change). `recheck` would then
  read that drift as this deploy's migration, so `prepare` refuses and says why.
- **Build.** Never run `release-image.yml` by hand for a commit you will deploy with this command:
  every build pushes the commit's `sha-<full sha>` tag, the workflow refuses to overwrite a tag, and
  `prepare`'s own build then fails for that commit whatever `--label` it is given. Deploy a later
  commit instead. It saves the state file first, then runs
  `gh workflow run release-image.yml --ref main -f ref=<sha> -f label=<label>`. The workflow's
  `run-name` is `Release image <ref>`, and `gh workflow run` returns no run id, so `prepare`
  takes the first run newer than any it saw before dispatching whose title names this commit. It
  saves that run's id, waits for it to succeed, and survives up to three consecutive failed `gh`
  calls while polling. It reads the pushed digest from the `DIGEST:` line of the "Verify the
  pushed image" step in `gh run view --log`. Neither a step summary nor an artifact can be read
  through the API. It refuses unless that step names this commit's `REVISION` and `SHA_TAG`. It
  reads the lane image's digest from the same log's `LANE_DIGEST:` line, under the same refusals
  for `LANE_REVISION` and `LANE_SHA_TAG`, and records it as `lane_digest`. The
  readable tag comes from `scripts/compute_image_tags.py`, the same function the workflow uses.
- **Migrations.** It lists every `migrations/versions/*.py` changed between the pre-swap revision
  and the built commit. Only then does it print the migration step.

A second `prepare` against an existing state file, or `prepare --resume --state <path>`, never
dispatches again: the workflow refuses to overwrite a tag. If the build was already read, it
reprints the steps. Otherwise it continues where the first one stopped: finding the run by its
title, waiting for it, reading its digest. If the dispatch itself never happened, the run never
appears, and `prepare` says to remove the state file and start again.

**`finish`** checks these, in order:

1. It refuses unless the observed digest equals the pushed one, and unless the checkout is at the
   built commit (the binder must match the served image).
2. It polls `/health/live` for up to 300 s until it serves the built commit. It stops at once on a
   revision that is neither the built nor the pre-swap commit, because that means somebody else
   deployed.
3. It requires `/health/ready` to answer 200. `api/health.py::ready` answers 200 only when the
   database has exactly one head and it equals the code's single head. So a 200 is the
   `alembic current` == `heads` check from #201, read from outside.
4. It requires `/openapi.json` to parse and serve paths, plus every `--expect-schema` model or
   field (#194). The OpenAPI document carries no revision; the revision match is `/health/live`'s.
5. It runs `image-release bind` with the recorded `previous_commit`, never a fresh read. Both
   bearers are fetched in-process from BWS and passed in the binder's environment.
6. It prints, on stderr and as `lane_phases`, the lane phases' first command,
   `lanes-prepare --lane <lane> --tag sha-<built commit>`, for each registered lane with a passed
   `--add`. It reads the registry and the run files before the binder, so an unreadable registry
   stops `finish` before it binds.

Every command prints one JSON result, including when a subprocess times out, `gh` is missing, or
the OpenAPI document is malformed. Exit 0 means done. Exit 2 means a condition: a live unit, a digest
mismatch, a revision not served, readiness failing, a missing schema model, a binder
condition, or a lane's quiet slot unmet. Exit 3 means a state it cannot read or will not deploy
from. Exit 4 means a lane's quiet-slot wait passed its deadline (`lanes-prepare` only). Exit 1
means usage.

The numbered entries below remain the procedure's record, and the manual fallback when the command
cannot run. The command adds no step they do not describe.

## The lane phases (ADR-0059 increments 1 and 2)

Each scheduled lane that has moved off the Mac is its own Coolify compose resource,
`sds-lane-<lane>`, with one service named after the lane (ADR-0059 Amendment 1). The deploy
command's three lane phases change one lane's resource at a time, the way `prepare`, `recheck` and
`finish` change the orchestrator's: the command checks and prints, and the deploying session makes
every infraops call. The command never calls Coolify, holds no Coolify credential, sends no alert,
reads no alert-hub token, and creates or deletes no resource.

Each phase takes `--lane <lane>` and exactly one change:

- `--tag sha-<40 hex>`: move the lane to a release's sha tag. It must be the served orchestrator
  tag, and its commit must descend from the commit of the lane's first passed `--add`; an older
  target goes back to the Mac first ("Rollback for one lane").
- `--schedule <true|false>`: change the lane's `LANE_SCHEDULE_ENABLED`. It keeps the running tag
  and writes the tracked compose at that tag.
- `--add --tag sha-<40 hex>`: the lane's first start, on the resource HQ created. The tag must be
  the served orchestrator tag.
- `--token`: redeploy the lane after the VM rotation executor wrote its `<LANE>_BWS_ACCESS_TOKEN`
  (ADR-0059 Amendment 2). It keeps the running tag, digest and schedule, writes the tracked compose
  at that tag, and writes no variable. It refuses a lane with no passed `--add`.

A `--tag` takes the release's sha tag only. The command resolves it to its full commit on
`origin/main`, finds that commit's one successful `Release image` run (in the newest 200), and
reads the lane image digest from that run's `LANE_DIGEST:` line.

### Run files

The phases keep their record under `~/Library/Application Support/sds/deploy-lanes/`
(`LANES_STATE_DIR`), outside every repository, so any session on the Mac finds a lane's chain:
one directory per lane, one JSON file per run, written atomically, with `.lock` held (`flock`)
while a phase reads and writes. Each file holds the change and its `change_key`
(`tag:<commit>`, `add:<commit>`, `schedule:<value>` or `token`), the 6-hour deadline as an absolute UTC
time, the resource uuid, the tag with its full commit, the lane digest, the compose it sends, the
compose and values it replaces, the recorded volume, for a `--token` run the secret UUIDs its
`lane-run --read` must read, and one status:

| Status | Meaning |
|---|---|
| `waiting` | Recorded by `lanes-prepare`; nothing is written to the resource yet. |
| `refused` | `lanes-prepare` refused the change; nothing was written. |
| `timed_out` | The quiet-slot wait passed its deadline (exit 4); nothing was written. |
| `superseded` | A run for another change to the same lane replaced it; nothing was written. |
| `unfinished` | `lanes-recheck` exited 0, so the session writes the resource. |
| `finished` | `lanes-finish` passed. The newest one is the lane's record: its compose is the one Coolify was last given, and its tag is the lane's tag. |
| `restored` | `lanes-finish --restored` passed: the lane is back on the values the run replaced, so it never becomes the lane's record. |
| `closed_failed` | A `--token` run whose `lanes-finish` failed, closed by `lanes-finish --restored` with no write. Its `reason` holds the mismatches the failing `lanes-finish` recorded. It restored nothing and never becomes the lane's record; `prepare`'s lag check reads past it, as it reads past `restored`. |

The phases find the lane's current run by `--lane` and the change, never by a path. The first
compose of a resource is the one its `--add` sends: an `--add` has no replaced compose, so its
diff shows the whole tracked file as new, and the compose its `lanes-finish` records is the lane's
recorded compose from then on.

### The quiet slot

A lane's resource is redeployed only in its quiet slot: outside estate landing's 02:00 to 06:00
New York window, and while the lane's own Healthchecks check doesn't have `started` set to `true`.
A `started` check whose `status` is also `down` doesn't hold it. `LANE_CHECKS` in
`scripts/deploy_orchestrator.py` names each lane's check as its launcher's dead-man ping names it
(`tests/scripts/test_deploy_lanes.py::test_the_check_names_are_the_launchers`). The command reads
`GET https://healthcheck.alobar.net/api/v3/checks/` with the read-only key, BWS
`f07b1368-f937-4287-baf1-b4ed00b6b274` (`HC_API_KEY`, `Rotation / Healthchecks`), as
`sds-operator`.

No process waits: the first `lanes-prepare` for a change records a deadline 6 hours later. A
re-run for the same change resumes against it; a run for another change marks the waiting one
`superseded` and records its own. The run that finds the deadline passed marks it `timed_out`,
prints one line naming the lane, the change and the deadline, and exits 4. Raise the alert through
your own channel; a later `lanes-prepare` records a new deadline. `lanes-prepare` refuses while a
run of its lane is `unfinished`.

### Run a change

1. Read the running container before `lanes-prepare` (not for an `--add`, which has no container).
   For `--tag` and `--token`, its tag with `vps_docker_ps`. For `--schedule`, its value with `vps_exec` running
   `docker exec <container> printenv LANE_SCHEDULE_ENABLED`. For each of the eleven stateful lanes,
   the source of its `/var/lib/sds/<lane>` mount, with `vps_exec` running `docker inspect` with a
   `--format` that prints only that mount. No read lists another variable's value.
2. Run `lanes-prepare` with the change and those reads:

   ```bash
   rtk proxy uv run python -m scripts.deploy_orchestrator lanes-prepare --lane <lane> \
     --tag sha-<served> --running-tag <tag> [--running-volume <volume>]
   rtk proxy uv run python -m scripts.deploy_orchestrator lanes-prepare --lane <lane> \
     --schedule <true|false> --running-schedule <value> [--running-volume <volume>]
   rtk proxy uv run python -m scripts.deploy_orchestrator lanes-prepare --lane <lane> \
     --add --tag sha-<served> --resource-uuid <uuid> --schedule-written false
   rtk proxy uv run python -m scripts.deploy_orchestrator lanes-prepare --lane <lane> \
     --token --running-tag <tag> [--running-volume <volume>]
   ```

   A `--token` run records the UUIDs `.bws-secrets.toml` lists for the lane at its running tag:
   each `[[secret]]` whose `lanes` field names the lane. It refuses a lane the manifest lists no
   secret for.

   Exit 2 means the quiet slot is unmet: re-run it on a later cycle. Exit 4 means the wait timed
   out. Exit 0 prints, on stderr, how the compose differs from the recorded one, the exact calls of
   the following steps, and their inverse.
3. Run `lanes-recheck` with the same `--lane` and change, then the writes and the redeploy, with no
   other step between them. Write only if it exits 0; a non-zero exit sends you back to
   `lanes-prepare`. The writes are `coolify_update_service` with the whole compose as
   `docker_compose_raw`, and one variable: `SDS_LANES_IMAGE_TAG` through
   `coolify_update_service_env` for `--tag`, through `coolify_create_service_env` for `--add`
   (Coolify makes no row for a `${VAR}` in `image:`), or `LANE_SCHEDULE_ENABLED` through
   `coolify_update_service_env` for `--schedule`; `--token` writes no variable, because the
   executor has written the token. Then `coolify_deploy` on the resource.
4. Once the deployment has finished, read the new container with the `vps_exec` commands
   `lanes-prepare` printed: its image's `RepoDigest`, its `LANE_SCHEDULE_ENABLED`, the source of a
   stateful lane's state mount, and for an `--add` or a `--token` whether its `BWS_ACCESS_TOKEN`
   is non-empty (`test -n`, which prints no value). A `--token` run adds one more read:
   `docker exec <container> lane-run --read <uuid>...` over the run's recorded UUIDs, which prints
   one `<uuid> ok` or `<uuid> refused` line per UUID and never a value. Save those lines, as
   printed, to a file outside the repository.
5. Run `lanes-finish` with the change and the reads (`--observed-digest`, `--observed-schedule`,
   `--observed-volume`, `--token-present`, and for `--token` `--read-result <file>`, the path of
   the saved `lane-run --read` lines). `--observed-digest` takes the bare `sha256:<64 hex>` value
   after the `@`, not the whole `RepoDigests` line (`ghcr.io/…@sha256:…`); anything else is a
   usage error. It exits 0 only when the container runs the release's lane
   digest, has the expected schedule, mounts the recorded volume (or, for an `--add`, the
   resource's one named volume, never a double-prefixed one), and, for an `--add` or a `--token`,
   holds a token. A `--token` run passes only if every recorded UUID reads `ok` too. A read-result
   line that isn't `<uuid> ok|refused`, a UUID named twice, or a UUID the run didn't record is a
   usage error (exit 1), and the command never repeats the line. `--read-result` is for `--token`
   only. If the redeploy itself failed, pass `--redeploy-failed` instead of the reads. A failing
   `lanes-finish` records its mismatches as the run's `reason`.
6. When `lanes-finish` exits 2, run the inverse it prints at once:
   - For `--tag` and `--schedule`: `coolify_update_service` with the recorded compose, the
     replaced variable value, and a second `coolify_deploy`. Read the container again, then run
     `lanes-finish --restored` with fresh reads. It exits 0 only when the container shows the
     replaced values again.
   - For `--add`: the delete of `sds-lane-<lane>` alone (`coolify_delete_service`), with no
     second `coolify_deploy`, which HQ performs as its own announced production act under D-20.
     Coolify only queues the deletion, so re-read for up to 10 minutes until
     `coolify_list_services` no longer lists `sds-lane-<lane>` and `vps_docker_ps` shows no
     container of it. Then run `lanes-finish --restored --service-listed false
     --container-present false`. The command doesn't wait; a resource still listed after
     10 minutes fails the check.
   - For `--token`: nothing to undo, because its compose is the one the lane already ran. Run
     `lanes-finish --lane <lane> --token --restored`, with no reads. It writes nothing, closes the
     run `closed_failed` with the recorded mismatches as its reason, prints them on stderr, and
     answers `"result": "closed_failed"`, never a success. It refuses a run with no failed
     `lanes-finish` recorded. Report the failure: the reissue's revoke stays unresolved. The lane
     can then take a retry `--token`, a `--tag`, or a rollback to the Mac.

`lanes-finish` checks the digest, the schedule, and the volume (and, for an `--add` or a
`--token`, that a token is present), never which secrets the lane's launcher reads. So a change
that alters the lane's BWS reads, such as a `--tag` to a release whose launcher reads a new UUID,
isn't verified when `lanes-finish` passes. Before you call it verified, for a lane whose crontab
line has no flag, run one pass (`lane-run <lane>`) or `lane-run --read <uuid>...` over the new
UUIDs in the container. Verify a lane whose crontab line has a flag (`--submit`) from its next
scheduled pass.

### Before a lane token's revoke

Devon revokes a reissued lane token's old value only after a `--token` run's `lanes-finish`
passes on the redeployed container (ADR-0055 amendment 19, D-26), unless a leak had him revoke
first. Every redeploy after the executor's `PATCH` loads the new token, a `--tag` or `--schedule`
included, but only `--token` reads `lane-run --read`. So when another change redeploys the lane
between the `PATCH` and the `--token` run, still run `--token` after it passes: its compose and
values are the ones the lane already runs, so the second redeploy changes nothing but proves the
reads on the container that holds the token. Report that `lanes-finish`, not the other change's,
as the revoke's precondition.

### Registered lanes and the lane-token matcher

A lane is registered while this repository's `.cred-consumers.toml` at `origin/main` holds its
token entry. The matcher (`lanes_in_registry`) takes any `[[credential.consumer]]` with
`kind = "coolify-env"`, `resource_type = "service"` and `key = "<LANE>_BWS_ACCESS_TOKEN"`, and
reads the lane as `<LANE>` lower-cased with `_` as `-` (`EXECUTION_WATCH_BWS_ACCESS_TOKEN` is
`execution-watch`). It refuses a key that names no lane the command knows, and two entries for one
lane. Removing a lane's entry, the last step of "Rollback for one lane", drops the lane from
`prepare`'s check; its run files remain.

`execution-watch`'s tracked files (`lanes/execution-watch/compose.yaml` and `crontab`) don't
register it: its token entry lands with its cut-over. Its compose names
`SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of the lane's ping-URL secret; until the executor
writes the URL there, that secret holds the inbox's empty marker and the container's dead-man
helper pings nothing (`scripts/sds-deadman.sh`). The secrets a lane reads are listed in `.bws-secrets.toml` by each
secret's `lanes` field, which `lane_secrets` reads for a `--token` run and the cut-over's identity
proof uses.

`landing-ledger`'s tracked files (`lanes/landing-ledger/compose.yaml` and `crontab`) don't register
it either. It keeps no state, so its compose mounts no volume. Its crontab runs
`lane-run landing-ledger` daily at 07:30 New York time, as the Mac job does; the ledger has no
scheduled-pass flag. Its `.bws-secrets.toml` secrets are the observer bearer and the read-only
App's key, plus its ping-URL secret. Its compose names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret
id of `landing-ledger-ping-url`, which the container's dead-man helper reads the URL from with the
lane's own token (`scripts/sds-deadman.sh`). Its cut-over (ADR-0059, "Cut-over for one lane")
still needs the following, each in its own change or act:

1. The executor's token write.
2. The orchestrator serving the release whose compose and `.bws-secrets.toml` name the ping-URL
   secret's id.
3. The lane's `--add`, then the `lane-run --read` proof and a `--dry-run` in the container.
4. Cut-over steps 2 onward: the paired reporting runs, the schedule, and the Mac job's retirement.

`deploy-watcher`'s tracked files (`lanes/deploy-watcher/compose.yaml` and `crontab`) don't
register it either. It keeps no state, so its compose mounts no volume. Its crontab runs
`lane-run deploy-watcher` hourly at minute 20, as the Mac job's `StartInterval` of 3600 does; the
watcher has no scheduled-pass flag. Its `.bws-secrets.toml` secrets are the observer bearer,
change-manager's `M2M_TOKEN_OBSERVE`, the read-only App's key and its ping-URL secret, all read with
the lane's one token; on the Mac the broad and narrow Keychain identities still read the two
bearers. Its compose names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of
`deploy-watcher-ping-url`, which the container's dead-man helper reads the URL from with the lane's
own token (`scripts/sds-deadman.sh`). Its cut-over (ADR-0059, "Cut-over for one
lane") still needs the following, in this order, each in its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-deploy-watcher` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `deploy-watcher-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, the read-App key row's `runtime-fetch` reader for
   `scripts/run-deploy-watcher.sh`, and change-manager's registry consumer for
   `M2M_TOKEN_OBSERVE` in the lane's container (that row lives in change-manager).
4. The lane account's grants on its three secrets and its ping-URL secret.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `deploy-watcher` removed
   from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof and a `--dry-run` in the container.
8. Cut-over steps 2 onward: the paired reporting runs, the schedule, and the Mac job's retirement.

A deploy watcher pass takes about 6 to 7 minutes (118 changes at about 3 s each, measured
2026-11-15). A hand pass through an SSH exec whose timeout is shorter loses its result, so verify
the lane from the scheduled pass's `job succeeded` line in the container log.

`revision-watcher`'s tracked files (`lanes/revision-watcher/compose.yaml` and `crontab`) don't
register it either. Its state is the acting pass's `alerts.json`, so its compose mounts the named
volume `sds-lanes-revision-watcher-state` at `REVISION_WATCHER_STATE_DIR`
(`/var/lib/sds/revision-watcher`). Its crontab runs `lane-run revision-watcher` hourly at minute
40, as the Mac job's `StartInterval` of 3600 does; the minute keeps it clear of every other lane's
start, and the watcher has no scheduled-pass flag. Its `.bws-secrets.toml` secrets are the observer
bearer, the hosting platform's API token, App Brain's read key, the `sds-bridge` alert sender
(acting passes only), the read-only App's key and its ping-URL secret, all read with the lane's one
token; on the Mac the narrow and broad Keychain identities still read the first four. Its compose
names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of `revision-watcher-ping-url`, which the
container's dead-man helper reads the URL from with the lane's own token (`scripts/sds-deadman.sh`).
Its cut-over (ADR-0059, "Cut-over for one
lane") still needs the following, in this order, each in its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-revision-watcher` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `revision-watcher-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, the read-App key row's `runtime-fetch` reader for
   `scripts/run-revision-watcher.sh`.
4. The lane account's grants on its five secrets and its ping-URL secret.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `revision-watcher` removed
   from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof and a `--dry-run` in the container. The
   dry run also proves the read App's installation covers every repository the member record's
   apps name: a repository outside it makes the mint refuse, and every pass exit 1.
8. Cut-over steps 2 onward: the copy of the Mac's `sds/revision-watcher/alerts.json` into the
   volume (cut-over step 2, before the first acting pass in the container, or the alerts the Mac
   left firing never resolve), the paired reporting runs, the schedule, and the Mac job's
   retirement.

`pin-watcher`'s tracked files (`lanes/pin-watcher/compose.yaml` and `crontab`) don't register it
either. Its state is the acting pass's `alerts.json`, so its compose mounts the named volume
`sds-lanes-pin-watcher-state` at `PIN_WATCHER_STATE_DIR` (`/var/lib/sds/pin-watcher`). Its crontab
runs `lane-run pin-watcher` daily at 07:25 New York time. The Mac job runs at 07:20, but minute 20
is the deploy watcher's hourly start, and 25 keeps it clear of every other lane's start; the
watcher has no scheduled-pass flag. Its `.bws-secrets.toml` secrets are the observer bearer, the
`sds-bridge` alert sender (acting passes only), the read-only App's key and its ping-URL secret,
all read with the lane's one token; on the Mac the narrow Keychain identity still reads the first
two. Its compose names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of `pin-watcher-ping-url`,
which the container's dead-man helper reads the URL from with the lane's own token
(`scripts/sds-deadman.sh`). The read-App key row's `runtime-fetch`
reader for `scripts/run-pin-watcher.sh` landed with the launcher. Its cut-over (ADR-0059, "Cut-over
for one lane") still needs the following, in this order, each in its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-pin-watcher` from the compose
   without deploying it.
2. The VM root's keeper commands for the lane token and `pin-watcher-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row.
4. The lane account's grants on its three secrets and its ping-URL secret.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `pin-watcher` removed from
   `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof and a `--dry-run` in the container. The
   dry run derives and mints as a scheduled pass does, and settles ADR-0059 amendment 4 open
   item 2: with the minted token it must read a public non-member's caller file and list through
   each account-type endpoint (`/users/{login}/repos` and `/orgs/{login}/repos`). It also proves
   the read App's installation covers every repository the member record observes, onboarding
   ones included, and that they share one owner: otherwise the mint refuses, and every pass
   exits 1.
8. Cut-over steps 2 onward: the copy of the Mac's `sds/pin-watcher/alerts.json` into the volume
   (cut-over step 2, before the first acting pass in the container, or the alerts the Mac left
   firing never resolve), the paired reporting runs, the schedule, and the Mac job's retirement.

`membership-check`'s tracked files (`lanes/membership-check/compose.yaml` and `crontab`) don't
register it either. It keeps no state, so its compose mounts no volume. Its crontab runs
`lane-run membership-check` daily at 07:47 New York time. The Mac job runs at 07:45, but minute 45
is one of execution-watch's quarter-hours, and 47 keeps it clear of every other lane's start; the
check has no flags. Its `.bws-secrets.toml` secrets are the observer bearer, the change-manager READ
bearer, the read-only App's key and its ping-URL secret, all read with the lane's one token, never
the Healthchecks API key; on the Mac the narrow and broad Keychain identities still read the first
two. Each container pass also makes an anonymous shallow clone of intent-packages into the pass's
`HOME` and points `BUMP_PROPOSER_PACKAGES_CHECKOUT` at it (ADR-0059 amendment 5); the report's first
line names the clone's commit, or git's exit code and error, and a failed clone is the program's
"packages checkout couldn't be read" gap (exit 3, or 2 beside a disagreement), never the launcher's
exit 1. The check is an outcome alarm: its compose names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret
id of `membership-check-ping-url`, which the container's dead-man helper reads the URL from with the
lane's own token (`scripts/sds-deadman.sh`). The read-App key row's `runtime-fetch` reader for
`scripts/run-membership-check.sh` landed with the launcher. Its cut-over (ADR-0059, "Cut-over for
one lane") still needs the following, in this order, each in its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-membership-check` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `membership-check-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row.
4. The lane account's grants on its three secrets and its ping-URL secret.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `membership-check` removed
   from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof. The check has no `--dry-run`, so its first
   container pass is cut-over step 2's ordinary pass, which settles ADR-0059 amendment 5 open
   item 1: its report must show the clone's commit and no "packages checkout" gap. The same pass
   shows whether the read App's installation token is served GraphQL's `autoMergeAllowed`
   (`POST /graphql`, `src/membership_check/auto_merge.py`): served, or a gap per member whose
   detail names the route, the status and each error's `type`. Both hosts read the setting there;
   REST's `allow_auto_merge` isn't read, because GitHub serves merge-related settings only to a
   token with `contents:write`. It also proves the read App's installation covers every
   repository the member record observes, and that they share one owner: otherwise the mint
   refuses, and every pass exits 1.
8. Cut-over steps 2 onward: the paired pass on both hosts, recording the Mac checkout's commit
   (`git -C ~/Projects/intent-packages rev-parse HEAD`) beside the clone's, since a difference the
   two commits explain isn't a cut-over failure; then the schedule and the Mac job's retirement.

`change-proposer`'s tracked files (`lanes/change-proposer/compose.yaml` and `crontab`) don't
register it either. It clones nothing and keeps no state, so its compose mounts no volume. Its
crontab runs `lane-run change-proposer --submit` hourly at minute 35, as the Mac job's
`StartInterval` of 3600 runs it with `--submit` in its plist; 35 is a minute no other lane starts
on. Its `.bws-secrets.toml` secrets are the observer bearer, the change-manager propose bearer, the
read-only App's key and its ping-URL secret, all read with the lane's one token; the propose bearer
is read only on a `--submit` pass, as on the Mac, where the narrow and broad Keychain identities
still read the first two. The bare run is the lane's dry run: it reads the observer bearer and mints
its read token, writes nothing, and pings nothing. The check is an outcome alarm that pings only on
`--submit` passes: its compose names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of
`change-proposer-ping-url`, which the container's dead-man helper reads the URL from with the lane's
own token (`scripts/sds-deadman.sh`). The read-App key row's `runtime-fetch` reader for
`scripts/run-change-proposer.sh` landed with the launcher. Its cut-over (ADR-0059, "Cut-over for one lane") still needs the following, in this
order, each in its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-change-proposer` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `change-proposer-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row.
4. The lane account's grants on its three secrets and its ping-URL secret, and a reader for the
   container in change-manager's `.cred-consumers.toml` on the propose bearer's row (that registry
   lives in change-manager, not here).
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `change-proposer` removed
   from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof over all four secrets. The proof is the
   only read of the propose bearer before the first `--submit` pass, because a dry run never
   fetches it.
8. Cut-over steps 2 onward: the paired dry runs (the bare invocation on both hosts, which ping
   nothing), the schedule, and the Mac job's retirement.

`rotation-decomposer`'s tracked files (`lanes/rotation-decomposer/compose.yaml` and `crontab`)
don't register it either. Its state is the acting pass's `alerts.json` and `approval-since.json`,
so its compose mounts the named volume `sds-lanes-rotation-decomposer-state` at
`ROTATION_DECOMPOSER_STATE_DIR` (`/var/lib/sds/rotation-decomposer`). Its registry mirrors aren't
state: they sit under `lane-run`'s new `HOME`, so each pass refetches the four public registry
repositories anonymously with the image's `git`. Its crontab runs `lane-run rotation-decomposer`
hourly at minute 50, as the Mac job's `StartInterval` of 3600 runs it with no arguments; 50 is a
minute no other lane starts on. Its `.bws-secrets.toml` secrets are the orchestrator SYSTEM bearer,
the change-manager READ bearer, the `sds-bridge` alert sender and its ping-URL secret, all read with
the lane's one token; the sender is read only on an acting pass (no `--dry-run`), as on the Mac,
where the narrow Keychain identity still reads the SYSTEM bearer and the sender and the broad one
the READ bearer. It needs no GitHub token: it calls no GitHub API. `--dry-run` is the lane's
reporting mode: it reads the two bearers, proposes nothing, sends nothing and pings nothing. The
check is an outcome alarm that pings only on acting passes: its compose names
`SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of `rotation-decomposer-ping-url`, which the
container's dead-man helper reads the URL from with the lane's own token
(`scripts/sds-deadman.sh`). Its cut-over (ADR-0059, "Cut-over for one lane") still needs the
following, in this order, each in its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-rotation-decomposer` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `rotation-decomposer-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row.
4. The lane account's grants on its three secrets and its ping-URL secret. The READ bearer, which
   the broad identity reads on the Mac, becomes a direct grant to the lane account (Devon's act at
   the lane's identity gate).
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `rotation-decomposer`
   removed from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof over all four secrets and a `--dry-run` in
   the container. The proof is the only read of the sender before the first acting pass, because a
   dry run never fetches it; the dry run also proves the anonymous mirror fetch from the VPS.
8. Cut-over steps 2 onward: the copy of the Mac's `sds/rotation-decomposer` files into the volume
   (before the first acting pass in the container, or the alerts the Mac left firing never resolve
   and every approval-wait clock restarts), the paired `--dry-run` reporting runs on both hosts,
   which ping nothing, the schedule, and the Mac job's retirement. No ADR-0046 switch covers this
   lane, which proposes as SYSTEM, so a rollback that can't wait for the quiet slot is HQ's
   redeploy of the lane's resource by hand.

`unit-verifier`'s tracked files (`lanes/unit-verifier/compose.yaml` and `crontab`) don't register
it either. Its state is the pass's `alerts.json`, so its compose mounts the named volume
`sds-lanes-unit-verifier-state` at `VERIFIER_STATE_DIR` (`/var/lib/sds/unit-verifier`). Its crontab
runs `lane-run unit-verifier` every ten minutes at minutes 2, 12, 22, 32, 42 and 52
(`2-52/10`), as the Mac job's `StartInterval` of 600 does; those minutes keep it clear of every
other lane's start, and the verifier has no scheduled-pass flag. Its `.bws-secrets.toml` secrets
are the VERIFIER bearer, the `sds-bridge` alert sender (every pass), the read-only App's key and
its ping-URL secret, all read with the lane's one token; on the Mac the narrow Keychain identity
still reads the first two through `sds-token.sh`. Its compose names `SDS_DEADMAN_PING_URL_UUID`, the
BWS secret id of `unit-verifier-ping-url`, which the container's dead-man helper reads the URL from
with the lane's own token (`scripts/sds-deadman.sh`). Its cut-over (ADR-0059, "Cut-over for one
lane") still needs the following, in this order, each in its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-unit-verifier` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `unit-verifier-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, the read-App key row's `runtime-fetch` reader for
   `scripts/run-unit-verifier.sh`.
4. The lane account's grants on its three secrets and its ping-URL secret.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `unit-verifier` removed
   from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof and a `--dry-run` in the container. The
   dry run also proves the read App's installation covers every repository the member record
   grants `dependency_updates` or `app_changes`: a repository outside it makes the mint refuse,
   and every pass then runs with no GitHub token and exits 3.
8. Cut-over steps 2 onward: the copy of the Mac's `sds/verifier/alerts.json` into the volume
   (cut-over step 2, before the first pass in the container, or the alerts the Mac left firing
   never resolve), the paired reporting runs, the schedule, and the Mac job's retirement.

`work-carrier`'s tracked files (`lanes/work-carrier/compose.yaml` and `crontab`) don't register it
either. Its state is the watcher's `alerts.json`, so its compose mounts the named volume
`sds-lanes-work-carrier-state` at `WORK_WATCHER_STATE_DIR` (`/var/lib/sds/work-carrier`). It's the
one lane whose compose joins the external `coolify` network, because the carry and its chain verify
connect to the factory-events database resource (ADR-0059 amendment 7 item 5). The compose sets
the chain's three non-secret settings as plain values: `FACTORY_EVENTS_DB_HOST`
`b82fa1vg5whecgnjreuqb5tk` (the resource's container), `FACTORY_EVENTS_DB_PORT` `5432` (its port
inside the network; `15432` is the tailnet publish, for the Mac) and `FACTORY_EVENTS_DB_NAME`
`postgres`. `lane-run` passes them on (`CHAIN_SETTINGS`), and the launcher fails a pass with any of
them empty; it has no file mode. Its crontab runs `lane-run work-carrier --register` daily at 07:05
New York time, as the Mac job does; minute 5 is one no other lane starts on. The flag is in the
crontab line, so a scheduled pass registers, and the bare `lane-run work-carrier` is the reporting
pass.

Its `.bws-secrets.toml` secrets are its ping-URL secret and the following eight, all read with the
lane's one token:

- On every pass: the change-manager READ bearer, the orchestrator SYSTEM bearer (the watcher's read;
  the carry holds it only on `--register`), the orchestrator OBSERVER bearer, the factory-events
  read password, and the read-only App's key.
- On `--register` only: the carrier's own OBSERVER bearer, the `sds-bridge` alert sender, and the
  change-manager PROPOSE bearer.

The read password and the chain settings reach `work-carrier` and the chain verify alone, never
`work-watcher`. Each pass mints the lane's GitHub read token (`metadata`, `contents` and
`pull_requests`, read) for the repositories `work-watcher repositories` prints: intent-packages and
each repository whose pull request a live record names. The pass then clones intent-packages,
shallow at `main`, through `scripts/sds-git.sh` into `/var/tmp/sds-work-carrier/intent-packages`,
which the image links as `/intent-packages`. That's where `package_sources` looks, beside `/app`.
The pass discards the clone when it's done. After the retirement, which reads no chain, it runs
`python -m orchestrator.factory_chain_read` once, before the carry. Any outcome but `verified`
carries nothing and fails the pass with exit 1. This is the VPS verify D-52 A gives to the carrier's first VPS pass, and
it runs whatever the queue holds.

The same walk gives the carry its portfolio input (ADR-0059 amendment 11). A verified record
carries `portfolio_scan`: the newest usable `portfolio.scanned` event by its scan time, or the
reason there is none. The launcher writes that part to a file the pass owns and removes, and hands
it to `work-carrier` as `--portfolio-scan`, so the container reads no Mac file and `lane-run`
passes nothing new. The report gets only the chain summary and the carrier's `[feed]` line, which
every verified container pass prints first: the scan time and age, the row count and `result`, or
why there is no usable event. A repository-targeted record whose repository has no fresh row
(no event, a scan time over 48 hours old, a scan that couldn't read the member record, or no row)
holds as `UNDECIDED` and the pass exits 2. An empty queue with a stale feed exits as it would
without it. The Mac pass gets no `--portfolio-scan` and still reads `~/.portfolio/portfolio.json`
(`WORK_CARRIER_PORTFOLIO`); that read leaves with the Mac job. The check is an outcome alarm that pings only on `--register`. Its
compose names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of `work-carrier-ping-url`, which the
container's dead-man helper reads the URL from with the lane's own token (`scripts/sds-deadman.sh`).

Its cut-over (ADR-0059, "Cut-over for one lane") still needs the following, in this order, each in
its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-work-carrier` from the compose
   without deploying it.
2. The VM root's keeper commands for the lane token and `work-carrier-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, the read-App key row's `runtime-fetch` reader for
   `scripts/run-work-carrier.sh`, and the read password's `runtime-fetch` row's note naming the
   lane account as its holder in place of `sds-operator`.
4. The lane account's grants on its eight secrets and its ping-URL secret. Two are read by the broad
   identity on the Mac, the change-manager READ bearer (`314f276d-…`) and PROPOSE bearer
   (`acccb346-…`), and each becomes a direct grant to the lane account (Devon's act at the lane's
   identity gate).
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `work-carrier` removed from
   `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof over all nine secrets and a bare
   `lane-run work-carrier` in the container. That bare run is the first in-network read of
   `b82fa1vg5whecgnjreuqb5tk:5432` and the first VPS chain verify, and it proves the read App's
   installation covers every repository a live record names. The proof is the only read of the
   three `--register` secrets before the first acting pass. The run also fetches the four public
   credential registries anonymously under `lane-run`'s `HOME`, which a standing rotation's carry
   reads (`work_carrier/cli.py`, `RepoMirror`).
8. The portfolio feed D-55 A and D-56 A chose (ADR-0059 amendment 11). The scan's feed
   (project-standards), the schema value and the `portfolio-scan` registry agent
   (security-standards `08754a0f`), and the carrier's reader with this repository's pin at
   `08754a0f` are merged. Before security-standards' nightly adapter merges, the carrier must run
   an image built at that pin: every walk re-validates each stored event against its image's
   schema copy, and the chain is append-only. Until a fresh `portfolio.scanned` event is in the
   chain, a repository-targeted record holds (exit 2) in the container while the Mac carries it,
   so the paired comparison fails whenever one is queued. A standing rotation still carries. The
   cut-over can't finish until the first VPS pass reads an event under 48 hours old, or holds its
   repository-targeted records for a reason its `[feed]` line names.
9. Cut-over steps 2 onward:
   - The copy of the Mac's `sds/work-watcher/alerts.json` into the volume, before the first
     `--register` pass in the container, or the alerts the Mac left firing never resolve.
   - The paired bare reporting runs on both hosts in the same minute, which register and ping
     nothing. Fast-forward the Mac's `~/Projects/intent-packages` to `origin/main` first, because
     the container clones `main` and a bare Mac pass leaves its checkout where it is.
   - The schedule, and the Mac job's retirement. After the retirement, `sds-operator`'s read of the
     read password goes, and the lane account is its one holder.

`bump-proposer`'s tracked files (`lanes/bump-proposer/compose.yaml` and `crontab`) don't register
it either. Its state is the proposer's `alerts.json`, so its compose mounts the named volume
`sds-lanes-bump-proposer-state` at `BUMP_PROPOSER_STATE_DIR` (`/var/lib/sds/bump-proposer`). Like
the work carrier's, its compose joins the external `coolify` network and sets the chain's three
non-secret settings, because a `--submit` pass's `approve --by-policy` appends to the
factory-events database resource (ADR-0059 amendment 7). The launcher fails any pass, a dry run
included, with one of them empty; it has no file mode. Its crontab runs
`lane-run bump-proposer --submit` daily at 06:55 New York time. The Mac job runs at 06:50, but
minute 50 is the rotation decomposer's, so the lane takes 55, which no other lane starts on and
which still runs before the carry's 07:05. The flag is in the crontab line, so a scheduled pass
writes, and the bare `lane-run bump-proposer` is the dry run, which writes and pings nothing.

Its `.bws-secrets.toml` secrets are its ping-URL secret and the following seven, all read with the
lane's one token. Its compose names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of
`bump-proposer-ping-url`, which the container's dead-man helper reads the URL from with the lane's
own token (`scripts/sds-deadman.sh`).

- On every pass: the change-manager READ bearer, the orchestrator OBSERVER bearer, the
  `sds-bridge` alert sender, and the read-only App's key.
- On `--submit` only: the change-manager PROPOSE bearer, the factory-events append password, and
  the "SDS lanes" App's key (`lanes-push-app-private-key`).

Each pass mints `BUMP_PROPOSER_GITHUB_TOKEN` from the read-only App (`metadata`, `contents`,
`pull_requests` and `actions`, read) for the repositories `bump-proposer repositories` prints:
intent-packages and each repository the member record grants `dependency_updates`, a superset of
the repositories the pass reads. The read-only App must be installed on each of them, or the mint
refuses and the pass fails. The pass clones intent-packages with that token, shallow at `main`,
through `scripts/sds-git.sh` into `/var/tmp/sds-bump-proposer/intent-packages`, and discards the
clone when it's done. On `--submit` it also mints the "SDS lanes" App's token for intent-packages
alone (`contents: write`) and hands it to git with the App's `[bot]` identity in place of the read
token, so `standing.publish`'s `git push origin main` pushes as the App and its commits are the
App's. No program variable holds the push token; only git's extraheader does. intent-packages'
lifecycle runs under the image's interpreter, with the clone's `src` on `PYTHONPATH`
(`BUMP_PROPOSER_PACKAGES_PYTHON`), not a venv in the clone: the emitter runs the chain's writer
under that interpreter, and only the image's venv carries `jsonschema` and `psycopg`. The check is
an outcome alarm that pings only on `--submit`.

Its cut-over (ADR-0059, "Cut-over for one lane") still needs the following, in this order, each in
its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-bump-proposer` from the compose
   without deploying it.
2. The VM root's keeper commands for the lane token and `bump-proposer-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, a `runtime-fetch` reader for `scripts/run-bump-proposer.sh` on both App
   key rows (the push key's row names no reader yet), and the append password's row naming the
   lane account's container read beside its Mac Keychain item.
4. The lane account's grants on its seven secrets and its ping-URL secret. The account already
   reads the append password (ADR-0059 amendment 7). Two are read by the broad identity on the Mac,
   the change-manager READ bearer (`314f276d-…`) and PROPOSE bearer (`acccb346-…`), and each
   becomes a direct grant to the lane account (Devon's act at the lane's identity gate). The push
   App's key is the first grant on `b8f547b9-…` to any account.
5. Devon's change to intent-packages' `main` protection that admits the "SDS lanes" App's direct
   push, for example as a bypass actor of a ruleset that replaces the classic rule (ADR-0059,
   "intent-packages `main` requires three status checks"). Until then every acting pass's publish
   is refused, a finding (exit 3) that strands a commit only the discarded clone held. The pass
   approves before it publishes, so each refusal also leaves a `package.approved` event in the
   chain for a revision that exists nowhere. And because the clone is discarded,
   `require_publishable` never sees the stranded commit: the next pass approves again. So this
   change must come before the first acting pass.
6. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
7. A served release whose compose names the ping-URL secret's id, with `bump-proposer` removed from
   `test_deploy_lanes.py`'s `placeholders`.
8. The lane's `--add`, then the `lane-run --read` proof over all eight secrets and a bare
   `lane-run bump-proposer` in the container. That bare run is the first read mint over the
   granted repositories and the first clone, and it proves the read App's installation covers
   every one. It doesn't reach the chain or the push App: the proof is the only read of the three
   `--submit` secrets before the first acting pass.
9. Cut-over steps 2 onward:
   - The copy of the Mac's `sds/bump-proposer/alerts.json` into the volume, before the first
     `--submit` pass in the container, or the alerts the Mac left firing never resolve.
   - The paired dry runs on both hosts in the same minute, which write and ping nothing.
     Fast-forward the Mac's `~/Projects/intent-packages` to `origin/main` first, because the
     container clones `main` and a dry run on the Mac leaves its checkout where it is.
   - The schedule, and the Mac job's retirement. After the retirement, the Mac's
     `BWS_ACCESS_TOKEN_BUMP_PROPOSER` Keychain item has no reader.

`rotation-proposer`'s tracked files (`lanes/rotation-proposer/compose.yaml` and `crontab`) don't
register it either. Its state is the proposer's `alerts.json` and `approval-since.json`, so its
compose mounts the named volume `sds-lanes-rotation-proposer-state` at
`ROTATION_PROPOSER_STATE_DIR` (`/var/lib/sds/rotation-proposer`). It joins no external network: it
holds no chain credential (D-43 B; ADR-0055 amendment 24 item 5), so its `revise` and `transition`
record `event_id` None in the lineage, and every service it calls is an HTTPS URL. Its crontab runs
`lane-run rotation-proposer --submit` daily at 06:31 New York time. The Mac job runs at 06:30, but
minute 30 is execution-watch's, so the lane takes 31, which no other lane starts on and which still
runs before the bump proposer's 06:55 and the carry's 07:05. The bare `lane-run rotation-proposer`
is the dry run, which writes nothing, but this lane arms the dead-man switch, so a bare run pings
its check (ADR-0059, "It pings the real check").

infraops' `cred-findings` reads `cred-consumers.list` from `INFRADRIFT_CONFIG_DIR` and
`cred-rotation-state.json` from `SECURITY_DRIFT_STATE_DIR` (infraops `src/security-drift/paths.ts`).
The compose sets both, and the launcher refuses every pass, a dry run included, with exit 2 while
either is unset or empty, before it clones anything or reads any of its four secrets:

- `SECURITY_DRIFT_STATE_DIR` is `/var/lib/sds/rotation-proposer/security-drift`, a directory in the
  lane's volume (ADR-0059 Amendment 12 decision 1). It's a directory of its own, apart from the
  proposer's `alerts.json` and `approval-since.json`, because infraops writes the file there by
  a temporary file and a rename, and the name says whose file it holds. A pass also exits 2 when
  the directory has no `cred-rotation-state.json`, so every pass before the cut-over copy refuses.
- `INFRADRIFT_CONFIG_DIR` is `/var/tmp/sds-rotation-proposer/infra-drift`, beside the pass's clones
  and off the volume. The launcher writes `cred-consumers.list` there each pass, after its clones,
  and discards it with them. A list left by an earlier pass is removed before the clones, so no
  list names a clone the pass didn't make.

The list names four registries, the Mac list's four (ADR-0059 decision 5): the
`.cred-consumers.toml` of infraops-mcp-server, change-manager, factory-runner and orchestrator, each
in its clone at `main`. infraops' exposure scan (Infra Brain rule 1032) reads exposures from the
registries the list names, so a clone without its registry file exits 2 and the launcher never
writes a shorter list. `scripts/sds-cred-registries.sh` holds the clones and the list, for the pass
and for a person's command (described after the cut-over list). An exposure open on the Mac stays
open in the container (Amendment 12's verify item "an open exposure survives the move"): the
exposure is declared in a registry the list names, and the cut-over copy carries the state that
shows it unresolved.

Its `.bws-secrets.toml` secrets are its ping-URL secret and the following four, all read with the
lane's one token. The launcher reads the four on `--submit` only; a dry run reads none of them. Its
compose names `SDS_DEADMAN_PING_URL_UUID`, the BWS secret id of `rotation-proposer-ping-url`, which
the container's dead-man helper reads the URL from on every pass (`scripts/sds-deadman.sh`).

- The change-manager PROPOSE bearer.
- The orchestrator OBSERVER bearer.
- The `sds-bridge` alert sender.
- The "SDS lanes" App's key (`lanes-push-app-private-key`).

Each pass clones five public repositories anonymously, shallow at `main`, into
`/var/tmp/sds-rotation-proposer`, with git given no credential and without the lane's token in its
environment, and discards them when it's done: intent-packages and the four registries' repositories.
The infraops-mcp-server clone is also the checkout `cred-findings` runs from (its `dist/` is
tracked). Its registry mirrors (`RepoMirror`) live in that directory too. The pass gathers these
inputs before it reads any secret, so a pass that can't use them reads none. On `--submit` it mints
the "SDS lanes" App's token for intent-packages alone (`contents: write`) after the clones, and
hands it to git with the App's `[bot]` identity, so `standing.publish`'s `git push origin main` pushes as
the App. No program variable holds the push token; only git's extraheader does. intent-packages'
lifecycle runs under the image's interpreter with the clone's `src` on `PYTHONPATH`
(`BUMP_PROPOSER_PACKAGES_PYTHON`), as the bump proposer's does.

Its cut-over (ADR-0059, "Cut-over for one lane") still needs the following, in this order, each in
its own change or act:

1. Done (the change that set both directory settings): the compose names the state file's
   directory on the volume and the list's directory beside the clones, and the launcher writes the
   list each pass. ADR-0059 Amendment 12 decided the state file's home. Every pass still exits 2
   until the state file is copied in (step 10).
2. The orchestrator serving this release; then HQ creates `sds-lane-rotation-proposer` from the
   compose without deploying it.
3. The VM root's keeper commands for the lane token and `rotation-proposer-ping-url`.
4. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, a `runtime-fetch` reader for `scripts/run-rotation-proposer.sh` on the push
   App key's row.
5. The lane account's grants on its four secrets and its ping-URL secret. The PROPOSE bearer
   (`acccb346-…`) is read by the broad identity on the Mac and becomes a direct grant to the lane
   account (Devon's act at the lane's identity gate).
6. Devon's change to intent-packages' `main` protection that admits the "SDS lanes" App's direct
   push (the bump proposer's step 5, which serves both lanes). Until then every revising pass's
   publish is refused: that credential reports `error` (exit 3), and the commit lived only in the
   discarded clone. This lane approves nothing, so a refusal leaves no chain event, and the next
   pass revises again from `main`.
7. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
8. A served release whose compose names the ping-URL secret's id, with `rotation-proposer` removed
   from `test_deploy_lanes.py`'s `placeholders`.
9. The lane's `--add`, then the `lane-run --read` proof over all five secrets and a bare
   `lane-run rotation-proposer` in the container, after the state file's copy (step 10's first
   item), without which the bare run exits 2. That bare run is the first anonymous clone of the
   five repositories and the first `cred-findings` in the container. It reads no secret, so the
   proof is the only read of the four before the first acting pass.
10. Cut-over steps 2 onward:
    - The copy of the Mac's `~/.config/infra-drift/cred-rotation-state.json` into the volume
      (`rotation-state-copy`), before step 9's bare run. It must produce
      `/var/lib/sds/rotation-proposer/security-drift/cred-rotation-state.json` as a regular file
      (not a link), mode 0600, owned by the container's `orchestrator` user, in a directory that
      user owns at mode 0700. infraops refuses a file another uid owns (`loadValidated0600Json`),
      so a plain `docker cp` or a root `docker exec` leaves a file every pass refuses. Write it as
      `orchestrator`, reading the Mac file on stdin:

      ```sh
      docker exec -i -u orchestrator <container> sh -c 'umask 077 &&
        mkdir -p /var/lib/sds/rotation-proposer/security-drift &&
        cat > /var/lib/sds/rotation-proposer/security-drift/cred-rotation-state.json'
      ```

      Then compare its SHA-256 with the Mac copy's. From then on nothing writes the Mac copy.
    - The copy of the Mac's `sds/rotation-proposer/` files into the volume, before the first
      `--submit` pass in the container, or alerts never resolve and approval-wait clocks restart.
    - The paired dry runs on both hosts in the same minute, which write nothing and each ping the
      real check. Fast-forward the Mac's `~/Projects/intent-packages` and
      `~/Projects/infraops-mcp-server` to `origin/main` first, because the container clones `main`.
    - The schedule, and the Mac job's retirement.

A person's infraops command for this lane, `resolve-exposure` or `record-rotation` (ADR-0059
Amendment 12), runs inside the lane's container from an interactive session, as the lane's user:

```sh
docker exec -i -u orchestrator <container> bash -c \
  'source /app/scripts/sds-cred-registries.sh && sds_infraops_session "$@"' _ \
  resolve-exposure --cred <credential-id> --exposure <exposure-id>
```

The pass's clones under `/var/tmp/sds-rotation-proposer` are deleted at each pass's start and end,
so between passes they're absent, or partial while a pass runs. `sds_infraops_session` never reads
them: it unsets the lane's token, makes its own anonymous clones of the four registries' repositories
in a fresh directory under `/var/tmp`, writes their list there, and discards both when infraops
exits. The command and its two variables come from the following places:

- The infraops CLI is the session's own infraops-mcp-server clone at `main`,
  `dist/cli/security-drift-cli.js`, run with the image's `node`.
- `INFRADRIFT_CONFIG_DIR` is the session's directory holding that list. `record-rotation` reads
  the list; `resolve-exposure` reads only the state file.
- `SECURITY_DRIFT_STATE_DIR` is the compose's, which `docker exec` inherits.

The session refuses root (a root write leaves the state file owned by root, and every later pass
refuses it), an unset or empty `SECURITY_DRIFT_STATE_DIR` and a missing state file (exit 2), and it
fails as a pass does on a failed clone (1) or a clone without its registry file (2). Otherwise it
exits with infraops' code. It needs no mount, volume or credential beyond the lane's own.

`inert-landing`'s tracked files (`lanes/inert-landing/compose.yaml` and `crontab`) don't register
it either. Its state is the lander's `held-since.json`, `alerts.json` and `revert-alerts.json`, so
its compose mounts the named volume `sds-lanes-inert-landing-state` at `LANDER_STATE_DIR`
(`/var/lib/sds/inert-landing`); the launcher refuses a pass with it unset. It joins no external
network: it reaches no separate Coolify database resource, and every service it calls
(change-manager, the orchestrator, alert-hub, GitHub) is an HTTPS URL. It lands nothing itself:
the orchestrator lands through its own dispatch App. Its crontab runs
`lane-run inert-landing --submit` hourly at minute 37 New York time. The Mac job runs at minute 35,
but minute 35 is the change proposer's, so the lane takes 37, which no other lane starts on. The flag
is in the crontab line, so a scheduled pass asks for landings. The bare `lane-run inert-landing` is
the reporting run, which asks for nothing, but this lane arms the dead-man switch, so a bare run
pings its check (ADR-0059, "It pings the real check").

Its `.bws-secrets.toml` secrets are the following four, all read with the lane's one token. Its
ping-URL secret joins them when it exists; until then the compose's `SDS_DEADMAN_PING_URL_UUID` is
empty and the lane is in `test_deploy_lanes.py`'s `placeholders`.

- On every pass: the change-manager READ bearer, the orchestrator SYSTEM bearer, and the read-only
  App's key.
- On `--submit` only: the `sds-bridge` alert sender.

Each pass mints `INERT_LANDING_GITHUB_TOKEN` from the read-only App (`metadata` and
`pull_requests`, read) for the repositories `inert-landing repositories` prints: the landing
policy's inert population, the only repositories a pass lists pull requests in. The read-only App
must be installed on each of them, and they must share one owner, or the mint refuses and the pass
fails (exit 1). On the Mac, a repository `gh` can't list is one `unreadable` line (exit 3) beside
the others. A policy the list can't be read from fails the pass with exit 2 before anything is
minted, as the pass itself would.

Its cut-over (ADR-0059, "Cut-over for one lane") still needs the following, in this order, each in
its own change or act:

1. The orchestrator serving this release; then HQ creates `sds-lane-inert-landing` from the compose
   without deploying it.
2. The VM root's keeper commands for the lane token and `inert-landing-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, a `runtime-fetch` reader for `scripts/run-inert-landing.sh` on the read
   App key's row.
4. The lane account's grants on its four secrets and its ping-URL secret. The change-manager READ
   bearer (`314f276d-…`) is read by the broad identity on the Mac and becomes a direct grant to the
   lane account (Devon's act at the lane's identity gate). The SYSTEM bearer (`221a48d5-…`) and the
   alert sender (`b415d03b-…`) are in `SDS Operator`, read on the Mac as `sds-operator`.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `inert-landing` removed from
   `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof over all five secrets and a bare
   `lane-run inert-landing` in the container. That bare run is the first read mint over the inert
   population, and it proves the read App's installation covers every one. It reads the alert
   sender only on `--submit`, so the proof is that secret's only read before the first acting pass.
8. Cut-over steps 2 onward:
   - The copy of the Mac's `sds/inert-landing/` files into the volume, before the first `--submit`
     pass in the container, or every held pull request's clock restarts and alerts never resolve.
   - The paired bare reporting runs on both hosts in the same minute, which ask for nothing and
     each ping the real check. The Mac's hourly `--submit` job still runs at minute 35, so run the
     pair away from it; a bare run asks for nothing, so neither run lands beside it.
   - The schedule, and the Mac job's retirement. The container's schedule is off until the Mac job
     is booted out and disabled, so the two never submit in the same hour.

`estate-landing`'s tracked files (`lanes/estate-landing/compose.yaml` and `crontab`) don't
register it either. Its state is the lander's `held-since.json`, `alerts.json` and
`revert-alerts.json` (the estate lane syncs revert alerts too, `lander.held_alerts`), so its compose
mounts the named volume `sds-lanes-estate-landing-state` at `LANDER_STATE_DIR`
(`/var/lib/sds/estate-landing`); the launcher refuses a pass with it unset. It joins no external
network, the `coolify` network included: it reaches no separate Coolify database resource, every
service it calls but one (change-manager, the orchestrator, alert-hub, GitHub, GHCR) is an HTTPS
URL, and the one, Coolify's API, is the tailnet address `estate_lander.image_repoint`'s
`DEFAULT_COOLIFY_URL`, which a container on its resource's default network reaches (HQ G-73). It
lands nothing itself: the orchestrator lands through its own dispatch App. Its crontab runs
`lane-run estate-landing --submit` hourly at minute 16 New York time. The Mac job runs at minute 15,
but minute 15 is execution-watch's (`*/15`), so the lane takes 16, which no other lane starts on.
The flag is in the crontab line, so a scheduled pass asks for landings. The bare
`lane-run estate-landing` is the reporting run, which asks for nothing and reads neither Coolify
nor GHCR, but this lane arms the dead-man switch, so a bare run pings its check (ADR-0059, "It
pings the real check").

Its `.bws-secrets.toml` secrets are the following five, all read with the lane's one token. Its
ping-URL secret joins them when it exists; until then the compose's `SDS_DEADMAN_PING_URL_UUID` is
empty and the lane is in `test_deploy_lanes.py`'s `placeholders`.

- On every pass: the change-manager READ bearer, the orchestrator SYSTEM bearer, and the read-only
  App's key.
- On `--submit` only: the `sds-bridge` alert sender, whose failed read fails the pass, and the
  estate lander's Coolify token (`f9f59233-…`), whose failed read is logged and leaves every
  re-point and restore refused by name.

Each pass mints `ESTATE_LANDING_GITHUB_TOKEN` from the read-only App for the repositories
`estate-landing repositories` prints: the landing policy's deploying population, where this lane's
landings are. A bare pass's token carries `metadata` and `pull_requests`, read (the open pull
requests a revert is in); a `--submit` pass's adds `contents`, read (the compare of a served commit
with the revert's). The same token is the registry client's credential on `--submit`. The read-only
App must be installed on each repository, and they must share one owner, or the mint refuses and
the pass fails (exit 1). A policy the list can't be read from, or one declaring no deploying
population, fails the pass with exit 2 before anything is minted.

Its cut-over (ADR-0059, "Cut-over for one lane") still needs the following, in this order, each in
its own change or act. It moves outside the 02:00 to 06:00 New York landing window.

1. The orchestrator serving this release; then HQ creates `sds-lane-estate-landing` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `estate-landing-ping-url`.
3. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, a `runtime-fetch` reader for `scripts/run-estate-landing.sh` on the read
   App key's row.
4. The lane account's grants on its five secrets and its ping-URL secret. The change-manager READ
   bearer (`314f276d-…`) is read by the broad identity on the Mac and becomes a direct grant to the
   lane account (Devon's act at the lane's identity gate). The SYSTEM bearer (`221a48d5-…`), the
   alert sender (`b415d03b-…`) and the Coolify token (`f9f59233-…`) are in `SDS Operator`, read on
   the Mac as `sds-operator`. The read App key (`314a966c-…`) is in `SDS / Lane Apps`.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `estate-landing` removed
   from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof over all six secrets and a bare
   `lane-run estate-landing` in the container. That bare run is the first read mint over the
   deploying population, so it proves the read App's installation covers each repository. It
   doesn't prove `contents`, Coolify or GHCR: a bare pass uses none of them, and with no
   `revert_opened` row in the watch it lists no pull requests either. It reads the alert sender and
   the Coolify token only on `--submit`, so the `--read` proof is those secrets' only read before
   the first acting pass. Before the schedule turns on, prove what a bare pass can't, read-only,
   from the lane's container:
   - Coolify reach: `curl -fsS http://100.92.138.19:8000/api/health` (no token), as G-73 did from
     the revision watcher's container.
   - GHCR reach and each estate image's visibility: the anonymous exchange,
     `curl -s -o /dev/null -w '%{http_code}' 'https://ghcr.io/token?scope=repository:alobarquest/<image>:pull&service=ghcr.io'`,
     followed by a manifest `HEAD` with the token it returns, for each deploying repository's
     image. Run it first on the two images whose visibility is recorded (D-23): `sds-lanes`, which
     is public, and `orchestrator`, which is private. An estate image's result means public or
     private only if those two controls answer differently.
   Neither proves the minted token's pull or the Coolify token's authority; only a `--submit` pass
   with a bad landing in the watch uses them.
8. Cut-over steps 2 onward:
   - The copy of the Mac's `sds/estate-landing/` files (`held-since.json`, `alerts.json` and, if
     present, `revert-alerts.json`) into the volume, before the first `--submit` pass in the
     container, or every held pull request's clock restarts and alerts never resolve.
   - The paired bare reporting runs on both hosts in the same minute, which ask for nothing and
     each ping the real check. The Mac's hourly `--submit` job still runs at minute 15, so run the
     pair away from it and outside 02:00 to 06:00 New York; a bare run asks for nothing, so
     neither run lands beside it.
   - The schedule, and the Mac job's retirement. The container's schedule is off until the Mac job
     is booted out and disabled, so the two never submit in the same hour.

`bump-driver`'s tracked files (`lanes/bump-driver/compose.yaml` and `crontab`) don't register it
either. Its state is the driver's `alerts.json`, `pass.lock` and `mirrors/`, so its compose mounts
the named volume `sds-lanes-bump-driver-state` at `BUMP_DRIVER_STATE_DIR`
(`/var/lib/sds/bump-driver`). The launcher refuses a pass with it unset, because the pass lock
must be on the volume to exclude two passes. It joins the external `coolify` network, because each
pass's chain walk connects to the factory-events chain, a separate Coolify database resource
(ADR-0059 Amendment 13; the work carrier's three chain settings, `b82fa1vg5whecgnjreuqb5tk`, `5432`
and `postgres`, are in its compose). Every other service it calls (change-manager, the
orchestrator, alert-hub, GitHub, the Brains) is an HTTPS URL at the program's default. Its crontab runs `lane-run bump-driver` hourly at minute
51 New York time. The Mac job runs at minute 50, but minute 50 is the rotation decomposer's, so
the lane takes 51, which no other lane starts on. The line has no flag, as the plist has none: the
driver acts unless `--dry-run` is given. So the bare `lane-run bump-driver` is an acting pass, and
once the lane is in `.claude/hq-gate-pass.json` an HQ cycle may run it out of schedule. The
reporting run is `lane-run bump-driver --dry-run`, which acts on nothing and pings nothing.

Its `.bws-secrets.toml` secrets are the following seven, all read with the lane's one token on every
pass, each fatal if unreadable, the first six as on the Mac. Its ping-URL secret joins them when it exists; until
then the compose's `SDS_DEADMAN_PING_URL_UUID` is empty and the lane is in `test_deploy_lanes.py`'s
`placeholders`.

- The orchestrator SYSTEM bearer and the `sds-bridge` alert sender (read on a `--dry-run` pass
  too, which sends nothing).
- The change-manager READ bearer.
- The Code Brain and Infra Brain contributor keys, handed to `factory` alone.
- The read-only App's key.
- The factory-events read password (`2f708d08-…`, `Factory Events`), for the chain walk alone,
  never exported (Amendment 13 decision item 7). The Mac's bump driver doesn't read it.

Each pass mints `BUMP_DRIVER_GITHUB_TOKEN` from the read-only App for the repositories
`bump-driver repositories` prints: code-standards, intent-packages, project-standards and
security-standards, and each repository the member record grants `dependency_updates`, read as
SYSTEM. The token carries `metadata`, `pull_requests` and `contents`, read: a granted pull request's
state, the mirrors' clones and fetches, and decision 1's four clones. On the four cloned
repositories a pass reads only contents, by the clone. The driver writes nothing to GitHub; it
dispatches and lands through the orchestrator as SYSTEM. A member record the list can't be read
from fails the pass with exit 3 before anything is minted. The launcher clones the four
repositories shallow at `main` into `$HOME/Projects/` and, for code-standards, `$HOME/Developer/`,
the Mac's paths, under the new `HOME` `lane-run` gives each pass and removes after it, so the pass
discards them. `factory` and `code-standards` are shims that run the image's interpreter on their
clones' `src`, as the bump proposer runs intent-packages' lifecycle; no venv is built, and
`tomlkit`, code-standards' one dependency, is an orchestrator dependency. Every pass then walks the
factory-events chain for the Mac's conformance facts and hands them to the driver
(`docs/operations/landing-lanes.md`, "The bump driver").

Its cut-over (ADR-0059, "Cut-over for one lane") still needs the following, in this order, each in
its own change or act.

1. The conformance claim's three Mac-only inputs, which ADR-0059 Amendment 13 (D-62 C) decides,
   before any acting container pass. `factory decompose` derives each bump unit's claim from
   project-standards' compliance matrix, which reads, under `HOME`:
   - code-standards (`~/Developer/code-standards`), through its venv's `code-standards`, else
     `uv run`. Without it the `code` cell reads `unknown`.
   - The infra-drift report (`~/infra-drift/reports`), which the Mac's `com.devon.infra-drift`
     writes. infra-drift stays on the Mac (D-41, Amendment 9). Without it the `infra` cell reads
     `unknown`.
   - The LaunchAgent plist a `launchagent:` required-check executor names
     (`~/Library/LaunchAgents/<label>.plist`), and the Claude settings a `hook:` executor names
     (`~/.claude/settings.json`). Without them the `checks` cell reads `violation`
     (`checks.not-wired`).

   A claim that isn't green is `violations`. The orchestrator approves the decomposition, but
   dispatch refuses the unit (`conformance_not_green`), and only a person superseding it clears
   it. A `--dry-run` pass never runs the generator, so the paired reporting runs of cut-over step 2
   can't show this. Amendment 13 needs these, as step 3g-3-inputs, before the acting pass:
   - Devon's acts: the read-only App installed on `AlobarQuest/code-standards` (`contents: read`),
     and a read grant on the factory-events read password (`2f708d08-…`) to the lane account.
   - security-standards: the nightly's adapter for the new event in `adapt --source all`, its
     `source.system` value in the event schema, and one credential-less registry agent. The names
     are HQ's proposals in the amendment (`mac_conformance.reported`, `mac-conformance-facts`).
   - This repository (done: step 3g-3-inputs): code-standards in `CLONED` and the read mint; the
     launcher's clone into `$HOME/Developer/code-standards`, its `code-standards` shim and
     `tomlkit` for the image's interpreter, the wider new-`HOME` guard, the chain walk and the
     files the driver writes under `HOME`; the driver's hold, before the generator, with its keyed
     alerts; the compose's `coolify` network and three chain settings; and the read password's
     lane row in `.bws-secrets.toml` and its `runtime-fetch` reader in `.cred-consumers.toml`.
     These change steps 4, 5 and 8: the lane reads one more secret, and its dry run clones four
     repositories and walks the chain.
   - project-standards only if the materialized files can't reproduce the Mac's cells.
2. The orchestrator serving this release; then HQ creates `sds-lane-bump-driver` from the compose
   without deploying it.
3. The VM root's keeper commands for the lane token and `bump-driver-ping-url`.
4. The lane's registry rows in this repository, in two changes: the token's row and the ping URL's
   row. In the same work, a `runtime-fetch` reader for `scripts/run-bump-driver.sh` on the read App
   key's row. The token's `reach_secrets` names the factory-events read password (`2f708d08-…`)
   beside the other six.
5. The lane account's grants on its six secrets and its ping-URL secret. The SYSTEM bearer
   (`221a48d5-…`) and the alert sender (`b415d03b-…`) are in `SDS Operator`, read on the Mac as
   `sds-operator`. The change-manager READ bearer (`314f276d-…`, `Rotation / Keepers`) and the two
   Brain keys (`750f737f-…`, `da8134b0-…`, `Brains - Contributor`) are read by the broad identity
   on the Mac and become direct grants to the lane account (Devon's act at the lane's identity
   gate). The read App key (`314a966c-…`) is in `SDS / Lane Apps`. The factory-events read password
   (`2f708d08-…`, `Factory Events`) is a read grant to the lane account, Devon's act (Amendment 13
   decision item 7): until it is made, `lane-run --read` refuses that UUID and every container pass
   fails on it.
   Devon's other act comes before step 8's dry run: the read-only App's installation on
   `AlobarQuest/code-standards`, `contents: read` (Amendment 13 decision item 7). Until then the
   mint, which names code-standards, fails, and so does every container pass.
6. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
7. A served release whose compose names the ping-URL secret's id, with `bump-driver` removed from
   `test_deploy_lanes.py`'s `placeholders`.
8. The lane's `--add`, then the `lane-run --read` proof over all eight secrets and a
   `lane-run bump-driver --dry-run` in the container. That run is the first read mint over the
   granted members and the four clones, so it proves the read App's installation covers each
   repository, and it proves the clones. It also walks the chain over the `coolify` network, and
   its report shows the walk's summary and `conformance facts: …`. It doesn't run `factory`: a dry
   run proposes nothing.
9. Cut-over steps 2 onward:
   - The copy of the Mac's `sds/bump-driver/alerts.json` into the volume, before the first acting
     pass in the container, or the alerts the Mac left firing never resolve. `pass.lock` and
     `mirrors/` aren't copied.
   - The paired `--dry-run` reporting runs on both hosts in the same minute, which act on nothing
     and ping nothing. Run the pair away from minute 50, when the Mac's acting job runs.
   - Before the Mac agent's bootout, no process holds the Mac's `pass.lock`; before a rollback's
     bootstrap, none holds the volume's (`flock -n` in the lane's container). The lock never spans
     the two hosts, so this order is what keeps them from acting at once.
   - The schedule, and the Mac job's retirement. The container's schedule is off until the Mac job
     is booted out, so the two never act in the same hour.

`follow-up-mint`'s tracked files (`lanes/follow-up-mint/compose.yaml` and `crontab`) don't
register it either. It's ADR-0059 increment 9's lane (Amendment 10): a new lane with no Mac job,
so it has no Mac check to keep, no state to copy and no agent to boot out. It keeps no state, so
its compose mounts no volume, and it joins no external network: the one service it calls, the
orchestrator, is an HTTPS URL at the launcher's default. Its crontab runs
`lane-run follow-up-mint` daily at 06:43 New York time: after infra-drift's 03:00 audit, outside
02:00 to 06:00, on minute 43, which no other lane starts on. The line has no flag: the launcher
takes none, and every pass mints.

Each pass reads one secret, the orchestrator SYSTEM bearer (`221a48d5-…`), with the lane's one
token, and runs `orchestrator mint-follow-ups --idempotency-key follow-up-mint:<UTC timestamp>
--json` with `ORCHESTRATOR_API_URL`, `ORCHESTRATOR_API_TOKEN` and
`ORCHESTRATOR_API_CREDENTIAL_KEY_ID` (`orchestrator-system`). An unreadable or empty bearer fails
the pass before the call, and the CLI's exit 1 fails it after; either pings the check's `/fail`.
The launcher sends no alert-hub message: the lane holds no sender token. Without
`BWS_ACCESS_TOKEN` it refuses with one line and reads nothing. Its ping-URL secret joins its
secrets when it exists; until then the compose's `SDS_DEADMAN_PING_URL_UUID` is empty and the lane
is in `test_deploy_lanes.py`'s `placeholders`.

Until the infraops-mcp-server change in step 7 lands, infra-drift's 03:00 step also mints. Two
passes a day mint nothing twice: a follow-up unit's id derives from its revision
(`follow_up_unit_id`), so the later pass reports each one `already_minted`.

Its cut-over (ADR-0059, "Cut-over for one lane", and Amendment 10 decision item 4) still needs the
following, in this order, each in its own change or act.

1. The orchestrator serving this release; then HQ creates `sds-lane-follow-up-mint` from the
   compose without deploying it.
2. The check, `sds-follow-up-mint`, created before anything reads its ping URL. It has no Mac
   predecessor, and the executor reads the ping URL from the check listing. ADR-0059 decision 3
   moves the name lookup to install time: Devon or an interactive session on the Mac runs
   `sds_upsert_check` (`scripts/sds-install.sh`) through the broad account. The lane has no
   `install-*-launchd.sh` to call it, so this step runs it by hand with the check's payload.
3. The lane's identity gate: the new `follow-up-mint` lane account with exactly two read grants,
   the SYSTEM bearer (`221a48d5-…`, `SDS Operator`) and its own ping-URL secret. Then the VM
   root's keeper commands for the lane token and `follow-up-mint-ping-url`, and the lane's
   registry rows in this repository, in two changes: the token's row and the ping URL's row.
4. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write, read from the check created in step 2 (ADR-0055 amendment 23).
5. A served release whose compose names the ping-URL secret's id, with `follow-up-mint` removed
   from `test_deploy_lanes.py`'s `placeholders`.
6. The lane's `--add`, the `lane-run --read` proof over both secrets, the schedule turned on, and
   the first scheduled pass, which mints the due follow-up units or finds none due and pings the
   check.
7. The infraops-mcp-server pull request removing `drift-audit.sh`'s minting step, with its
   `BWS_ACCESS_TOKEN_SDS` Keychain read for the bearer.
8. In this repository, after step 7 merges: the SYSTEM row's consumers in `.cred-consumers.toml`
   (the infraops `drift-audit.sh mint-follow-ups` reader leaves; `run-follow-up-mint.sh` joins
   the orchestrator reader's list) and the keeper's note; ADR-0007's "Scheduled trigger";
   `follow-up-scheduling.md`, "Running a pass"; and `credentials.md`'s SDS consumer paragraph.

`change-window`'s tracked files (`lanes/change-window/compose.yaml` and `crontab`) don't register
it either. It's ADR-0059 increment 8's lane: it executes the change requests change-manager holds
as approved, for production Coolify only (D-67 C; a person handles dev items). It keeps no state,
so its compose mounts no volume: each pass clones infraops-mcp-server anonymously, shallow at
`main`, into a directory it removes, and runs its bundled CLI, `dist/bundle/change-mgr-cli.js`
(D-66 A; infraops #171), which needs no `npm ci`. It joins no external network: change-manager,
alert-hub and the Anthropic API are HTTPS URLs, and production Coolify is the tailnet address
`http://100.92.138.19:8000`, which estate landing reaches the same way (HQ G-73). Its crontab runs
`lane-run change-window --submit` daily at 04:00 New York time, the Mac job's hour. That's inside
02:00 to 06:00, which is the quiet slot a redeploy of this resource avoids, not a rule on when a
lane runs, and minute 0 is also an execution-watch start (`*/15`).

A bare pass is a dry run: `run-window --dry-run --instance prod` lists the approved items and
prints the ids it would claim as one JSON line. It opens no window, claims nothing and builds no
Anthropic client, so it reads the `execute` bearer alone and pings nothing (the check is the
window's). A `--submit` pass runs `run-window --instance prod --report-dir <pass directory> --now
<UTC time>` and then sends the digest. The program gets `COOLIFY_BASE_URL`, `COOLIFY_API_TOKEN`,
`ANTHROPIC_API_KEY`, `CHANGE_MGR_API_BASE` (`https://change-mgr.alobar.net`) and
`CHANGE_MGR_M2M_TOKEN`, the names the bundle reads. No `COOLIFY_DEV_*` or `COOLIFY_PROD_*` variable
reaches it; infraops' Coolify client reads `COOLIFY_PROD_*` before the base pair.

The lane keeps the window's existing check, `Change Manager OPs Check` (slug `operations-check`),
as an outcome alarm: `/start`, then success on 0 or `/fail` with the report. `LANE_CHECKS` names
it, and `test_the_check_names_are_the_launchers` reads a quoted check name. Whether the self-hosted
listing the quiet slot reads holds exactly one check of that name is a cut-over verify item.

The digest is an `info` notice, sent at once with the Mac's key (`report:change-window:<date>`)
and subject, and cut to the hub's notice limits (12,000 characters, a 16,384-byte body). It isn't
an `info` alert held through quiet hours, for these reasons, from alert-hub's sender guide and
source:

- A held alert that is closed before quiet hours end never delivers: `staleReason` cancels a
  pending delivery when its alert is closed (`src/engine/delivery.ts`).
- An alert left open makes the next day's send with the same key a repeat that notifies nobody,
  and the sender guide forbids a date in an alert key.
- An alert's `message` caps at 4,000 characters, a notice's at 12,000, and the sender guide's
  "Send a report as a notice" is the documented form for a daily report.

So the digest arrives at about 04:00, not at the Mac's 07:30 outbox send: a notice delivers on
arrival, and the lane keeps no outbox. A failed send logs one line and leaves the pass's exit as it
was.

The lane's four secrets don't exist yet, so each id is empty in the launcher, and a pass that needs
an empty id fails closed with one line before it clones or reads anything. `test_run_change_window.py`'s
`EMPTY_IDS` lists them, and `test_deploy_lanes.py`'s `placeholders` lists the lane for its empty
ping-URL id. The ids live in the launcher, not the compose, because `lane-run` passes the
launcher a fixed list of variables.

Its cut-over (ADR-0059, "Cut-over for one lane") still needs the following, in this order, each in
its own change or act.

1. The orchestrator serving this release; then HQ creates `sds-lane-change-window` from the
   compose without deploying it.
2. The lane's identity gate and the mint gates for its four secrets and its ping URL: the new
   `change-window` lane account with read grants on exactly those five. The four are a dedicated
   Coolify window token (D-68 B), change-manager's `execute` bearer (`M2M_TOKEN_EXECUTE`,
   change-manager #168), an Anthropic API key, and an alert-hub sender token for the digest. Then
   the VM root's keeper commands for the lane token and `change-window-ping-url`.
3. The lane's `.bws-secrets.toml` and `.cred-consumers.toml` rows in this repository, with the
   secrets' UUIDs, in their own changes. In the same work, a change writes each UUID into the
   launcher and removes it from `EMPTY_IDS`.
4. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write, read from `Change Manager OPs Check` (ADR-0055 amendment 23).
5. A served release whose compose names the ping-URL secret's id, with `change-window` removed from
   `test_deploy_lanes.py`'s `placeholders`.
6. The lane's `--add`, then the `lane-run --read` proof over all five secrets.
7. The paired pass: `run-window --dry-run --instance prod` on both hosts in the same minute (the
   bare `lane-run change-window` in the container), compared on exit code and on the set of ids
   each would claim. Beside it, read-only reach probes from the lane's container: `curl -fsS
   http://100.92.138.19:8000/api/health` (no token) for production Coolify, and an unauthenticated
   request to the Anthropic API base that proves the container reaches the host. Neither proves
   the Coolify token's or the Anthropic key's authority; only a `--submit` pass uses them.
8. The schedule, and the Mac job's retirement: boot out and disable `com.devon.change-window`
   between two 04:00 passes, then turn the container's schedule on, so the two never run the same
   window.
9. After the cut-over: change-manager's pull request deleting its
   `launchagent:com.devon.change-window` required check (D-69 A); security-standards' registry and
   governance-map lines for the moved job; and, after rollback ends, infraops-mcp-server's removal
   of the Mac job.

### A lane's GitHub token (ADR-0059 increment 3)

A lane that reads or pushes to GitHub mints its own installation token each pass from one of the
two lane Apps, with `sds-github-mint` (`src/estate_clients/github_app_mint.py`). It's a console
script, so the lane image carries it at `$REPO_ROOT/.venv/bin/sds-github-mint` with no
`lanes/Dockerfile` change.

| App | App id | Installation | Key (BWS `SDS / Lane Apps`) | Registry row |
|---|---|---|---|---|
| `alobar-sds-lanes-read` | `5684758` | `180257215` | `314a966c-5654-4e3c-8358-b4fd01268775` | `lanes-read-app-private-key` |
| `alobar-sds-lanes` | `5684778` | `180257673` | `b8f547b9-67d7-4417-adc0-b4fd0126b0f0` | `lanes-push-app-private-key` |

The helper takes the following inputs:

- **The key, on stdin**, in its stored form: base64 of the PEM, on one line as macOS `base64`
  writes it or wrapped. It refuses raw PEM, because that means the keeper holds something other
  than the stored form. It never reads the key from argv or an environment variable.
- **`--app-id`**, one **`--repository owner/name`** per repository, and one
  **`--permission name=level`** per permission, `metadata=read` among them (GitHub grants it with
  every token, so a request without it can't match the answer). It accepts only `read` levels
  unless **`--allow-write`** is given, which only the push lanes pass.

It prints only the token on stdout, so the launcher captures it into the lane's existing
`<LANE>_GITHUB_TOKEN` variable. It finds the installation from the repositories, never from a
configured id. It refuses repositories of more than one owner, lookups that resolve more than one
installation, and an answer whose permissions or repositories differ from the request. Once its
arguments parse, every failure exits 1 with `FATAL: <cause>` on stderr and nothing on stdout; a
usage error is argparse's and exits 2. No message carries the key, the JWT or the token.
The following is the general shape; the ledger's own call follows it.

```sh
<LANE>_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
  | "$REPO_ROOT/.venv/bin/sds-github-mint" --app-id "$READ_APP_ID" \
      --repository AlobarQuest/brain --repository AlobarQuest/orchestrator \
      --permission contents=read --permission metadata=read)"
```

The landing ledger is the first lane that mints. In its container, `scripts/run-landing-ledger.sh`
derives the repositories with `scripts.sds_scope ledger` first, so an unreadable scope exits 3
before any mint, and then runs, once per pass:

```sh
_lane_secret "$READ_APP_KEY_UUID" \
  | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id 5684758 --repository <owner/name>... \
      --permission metadata=read --permission contents=read \
      --permission pull_requests=read --permission actions=read
```

Those four permissions cover every GET in `src/landing_ledger/github.py`. A failed or empty mint
exits 1 before the ledger runs, and the container never falls back to `gh auth token`. On the Mac
the launcher keeps the `gh` login until increment 6.

The deploy watcher mints the same way in its container (`scripts/run-deploy-watcher.sh`). Its
repositories are the keys of `ROLLOUT_WORKFLOWS` (`src/deploy_watcher/workflows.py`), printed by
`deploy-watcher repositories`, because a change record naming any other repository is refused
before a read. An empty table exits 3 before any mint. The keys are lowercase; the mint casefolds
both its request and GitHub's answer, so they pass as printed. The same four permissions cover
every GET in `src/deploy_watcher/github.py`.

The revision watcher mints the same way in its container (`scripts/run-revision-watcher.sh`). Its
repositories are those of the live members' apps, printed by `scripts.sds_scope apps` from the
member record with the observer bearer, each once: the same population its subjects come from
(`src/revision_watcher/subjects.py`). An unreadable record or an empty population exits 3 before
any mint. Its GET calls are a branch, a commit and a compare (`src/revision_watcher/github.py`), so
it mints `metadata=read` and `contents=read` only.

The pin watcher mints the same way in its container (`scripts/run-pin-watcher.sh`), a dry run
included. Its repositories are the member record's observed ones (onboarding and member), printed
by `scripts.sds_scope ledger` with the observer bearer: the same population the watcher's members
come from (`src/pin_watcher/cli.py`). The mint never names a public non-member; the watcher reads
those through the same token as public reads. An unreadable record or an empty population exits 3
before any mint. Its GET calls are the recommendation, each caller file, a compare and a commit
(`contents`), and each repository and each owner's listing (`metadata`;
`src/pin_watcher/github.py`), so it mints `metadata=read` and `contents=read` only. Members in
more than one owner make the mint refuse, which exits 1 (amendment 4 open item 4).

The membership check mints the same way in its container (`scripts/run-membership-check.sh`).
Its repositories are the member record's observed ones, printed by `scripts.sds_scope ledger` with
the observer bearer: the population its declaration and registry-file reads cover
(`src/membership_check/cli.py`). Its auto-merge read also covers retired members, which the mint
doesn't name, so in the container a retired member's auto-merge state is a gap. intent-packages and
factory-runner are members, so the fork list's read is covered too. An unreadable record or an empty population exits 3 before any mint, with the
program not run; any other `sds_scope` code passes through. It mints `metadata=read` (the listings
and each repository), `contents=read` (`factory-target.toml`, the registry files, the workflow
files and the fork list's file) and `administration=read`, which no read is known to need: the
auto-merge setting is GraphQL's `autoMergeAllowed` (`src/membership_check/auto_merge.py`), because
GitHub serves REST's merge-related settings only to a token with `contents:write`. It
exports the token as both `SDS_MEMBERS_GITHUB_TOKEN` and `GH_TOKEN`, for the fork list's `gh api`
read. A failed or empty mint exits 1. Its one other GitHub read, the standing packages, is an anonymous shallow
clone of `https://github.com/AlobarQuest/intent-packages.git` at `main`, run as git with
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0` and no token in its
environment (ADR-0059 amendment 5): the repository is public, so the clone carries no credential.

The unit verifier mints the same way in its container (`scripts/run-unit-verifier.sh`), into
`ROTATION_VERIFIER_GITHUB_TOKEN`. Its repositories are those the member record grants
`dependency_updates` or `app_changes`, the grants a bump's or an app change's repository is held to,
printed by `unit-verifier repositories` with the lane's own VERIFIER bearer, each once. Its one
GitHub call is `GET /repos/{repository}/branches/main/protection/required_status_checks`
(`src/unit_verifier/required_checks.py`), so it mints `metadata=read` and `administration=read`
only. Unlike the other lanes, a failed derivation or mint doesn't stop the pass: rotation units
need no GitHub read, so the pass runs with no GitHub token and the launcher then exits 3, which
pings `/fail`. In that pass every unit with checks to file is held unverified as `verify-refused`,
with its alert, and the next pass tries again.

The change proposer mints the same way in its container (`scripts/run-change-proposer.sh`), a dry
run included. Its repositories are every repository its GitHub calls read, printed by
`change-proposer repositories` with the observer bearer: the member record's `rollouts` scope,
narrowed to repositories with a transcribed rollout workflow and rollback plan, joined with the
scope sweep's population, every key of `ROLLOUT_WORKFLOWS` whatever its grant
(`src/change_proposer/cli.py`, `_scope_sweep`). Since the scope is a subset of the table, that's
the table's keys, but the derivation reads the record anyway, so an unreadable record or an empty
list exits 2 before any mint, as it proposes nothing; any other code passes through. Its GET calls
are a pull request listing and one pull request (`pull_requests`) and a rollout workflow's file
(`contents`), through `src/deploy_watcher/github.py`, so it mints `metadata=read`,
`contents=read` and `pull_requests=read`. A failed or empty mint exits 1.

The narrowing bounds what the token is granted. It doesn't stop the token reading public
resources, which need no token at all
(`docs/superpowers/reports/lanes-inc-3-verify-report.md`, item 3).

## Rotation instances

A change under `src/rotation_worker`, `src/estate_clients` or `src/alert_hub_client` does not reach
the rotation executor by deploying the orchestrator. Reinstall both instances at the merged commit:
the VM with `scripts/rotation-worker-vm/provision.sh` and the Keychain instance with
`scripts/install-rotation-worker-launchd.sh`
(`docs/operations/rotation-executor.md`, "Install or update it"). An instance left behind raises
`rotation-instance-behind` once the change is 7 days old.

## Rolling back past the bearer overlap parser

**Rolling back past the image that parses `previous` requires removing `previous` first.** From
that image on, an entry in `ORCHESTRATOR_M2M_CREDENTIALS` may carry
`"previous": {"token_hash": "<sha256>", "until": "<UTC instant>"}` while a credential rotation
overlaps the old and new bearers (ADR-0055 decision 7). An older image accepts only `agent_id`
and `token_hash` in an entry, so it refuses to start while any entry has `previous`, and every
program that authenticates to the orchestrator goes down with it. Before rolling back to such an
image, remove every `previous` from the setting (both rows of the variable), then roll back.
Removing `previous` early ends the overlap: a consumer still on the old bearer stops
authenticating, so check which consumers have switched first.

The parser refuses to start with an `until` more than eight days after boot, or one that isn't
UTC, so a rotation sets it about seven days out.

## The record

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #9

- Production Coolify images must be amd64 or multi-arch. Local Apple Silicon
  Docker builds produce arm64 images by default; use `docker buildx build
  --platform linux/amd64 --push` or a multi-arch build for `sds.alobar.net`, and
  verify the running container image/digest after Coolify reports deployment
  finished.

### #19

- **`sds.alobar.net` is NOT Cloudflare-proxied** — it answers `server: uvicorn`
  behind Traefik directly. The portfolio-wide invariant that Cloudflare 403s
  default Python User-Agents with `error code: 1010` does **not** apply here;
  factory-runner's `httpx` default UA authenticates fine (verified from a
  GitHub-hosted runner, 2026-07-09). Do not misdiagnose a failure here as that.

### #37

- **MERGED IS NOT DEPLOYED. Ask production what it is running before you reason about
  what it can do.** On 2026-07-12 production was serving
  `ghcr.io/alobarquest/orchestrator:d6d73b3-ws64-verifier-amd64` — a WS-6.4-era image —
  while WS-P2.1 (PR #47) and WS-P2.15 (PR #50) had been merged to `main` for days.
  `recover-evidence`, `dead-letter`, `requeue`, `reconciliation/detect`,
  `consistency-check` and **`pr-binding`** were all **absent from production** and
  returned 404, though every one of them exists in `main`. Program exit criterion #7
  ("operator status and recovery controls exist") was marked **MET**, citing five routes
  **none of which production served**; the five recovery drills that marked criterion #5
  MET run against a **local** orchestrator and had never touched production. WS-P2.16's
  entire subject — the `pr-binding` route — was undeployed, so a perfectly correct
  worker call would have 404'd; **six adversarial reviews of the WS-P2.16 plan missed
  this, because every one of them read the repository instead of asking production.**
  The check is one command and it is not optional:
  `curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json; print(sorted(json.load(sys.stdin)['paths']))"`.
  A green suite on `main` says nothing about the machine that serves traffic.

### #52

- **The prod orchestrator image build is PAVED-ROAD-automated (image-build-automation
  workstream); Coolify only ever pulls a prebuilt GHCR tag and does not build.** The paved
  road: `security-standards.pin.toml` (repo root) is the **single source of truth** for the
  pinned `revision`/`artifact_sha256` — prose here is context, not authority; read the file.
  `scripts/shape_registry_context.py` turns a security-standards checkout at that revision
  into the shaped `{agents/, src/, schema/, SOURCE_REVISION}` build-context via `git archive`
  (never a raw checkout — untracked files would poison the digest); it writes `SOURCE_REVISION`
  **with a trailing newline** — that byte is part of the digest contract, and it's a real
  footgun the old manual recipe left implicit (the fixture convention and
  `build_registry_bundle.py` both hash the file verbatim, newline included). The `Release
  image` GitHub Actions workflow (`.github/workflows/release-image.yml`,
  `workflow_dispatch`, native `linux/amd64`) reads the pin, shapes the context, and runs
  `docker buildx build --push` to **two** tags (WS-P2.18 Inc 7): the derivable
  `ghcr.io/alobarquest/orchestrator:sha-<full-40-char-sha>`, which depends on the commit and
  nothing else, and the human-readable `:<short-sha>[-<label>]-amd64`, which is kept as a caption.
  Both are composed by `scripts/compute_image_tags.py` — it is the function, and it refuses a
  revision that is not a full 40-character sha, so a malformed one fails the build rather than
  producing an image that asserts an unresolvable provenance.
  **The workflow only builds and pushes — it never deploys.** Pointing Coolify at the new tag is
  a separate step that the deploying session performs. It isn't a human gate (R11).
  Two digests, two different jobs, not competing checks: the **bundle digest**
  (`REGISTRY_ARTIFACT_SHA256`, currently `7aea8471…` per the pin file) is the **build-time,
  security-critical** gate — the Dockerfile's `registry` build stage recomputes it from the
  shaped context and **fails closed** on any mismatch (wrong/tampered actor registry), whether
  the build runs in CI or by hand. The **image SHA / running container's `RepoDigest`** is the
  separate **deploy-time identity** check the deploying session runs after the Coolify swap —
  proving prod is running bit-for-bit what the workflow pushed.
  **CORRECTED 2026-07-31 (WS-P2.17 Inc 4): the recipe this file has been shipping for that check
  does not work.** `docker inspect <container> --format '{{index .RepoDigests 0}}'` fails, because
  `RepoDigests` is a property of an **image**, not of a container — inspecting a container returns
  no such field. Go container → image → digest instead:
  `docker image inspect "$(docker inspect <container> --format '{{.Image}}')" --format '{{index .RepoDigests 0}}'`.
  The invariant ("ask production what it is running") was right; only the command was wrong, and
  a wrong command that errors is at least loud — do not replace it with one that prints an empty
  string. Bumping either digest requires
  bumping `security-standards.pin.toml`'s `revision` and `artifact_sha256` together (see that
  file's own header comment for the recompute recipe).
  **Fallback / differential baseline — keep this runnable, don't delete it:** the manual
  `docker buildx` recipe still works and is the thing to fall back to if the workflow is down,
  or to diff against if a CI-built image looks wrong. Recipe:
  `docs/software-delivery-system/2026-07-09-ws64a-deploy-and-onboarding-state.md`. Unless an
  actor/registry change is intended, PIN `SECURITY_STANDARDS_REVISION` to the pin file's
  `revision` (`65655ddf…`) and assert the computed `artifact_digest()` equals the pin file's
  `artifact_sha256` (`7aea8471…`) BEFORE the long build — that is the same byte-identical bundle
  gate (13 actors), done by hand. Then `docker buildx build --platform linux/amd64
  --build-context registry=$ART --build-arg SECURITY_STANDARDS_REVISION=$SHA --build-arg
  REGISTRY_ARTIFACT_SHA256=$DIGEST -t ghcr.io/alobarquest/orchestrator:<sha>-<ws>-amd64 --push .`
  produces a single amd64 v2 manifest; verify the running container's RepoDigest == the pushed
  digest after Coolify swaps (via `.Image`, per the correction above).
  **A hand-run build must also pass the three `--label` flags the workflow passes**
  (`org.opencontainers.image.revision` with the FULL sha, `.source`, `.created`), the second
  `-t sha-<full-sha>`, **and `--build-arg ORCHESTRATOR_REVISION=<the same full sha>`** — or the
  fallback silently produces a less-identifiable artifact than the paved road, precisely the state
  Inc 7 closed. The workflow refuses such an image; a hand build has nothing to refuse it.
  **The build arg is what `/health/live` serves, and omitting it is SILENT rather than loud**: the
  image runs, the endpoint answers 200, and `revision` reads `null` — which the currency census
  reports as a subject that cannot state what it is rather than as one that is stale, so a hand
  build with the labels and without the arg is invisible until somebody asks why one row of the
  census never resolves. `ARG ORCHESTRATOR_REVISION` is declared in the **runtime** stage, and that
  is load-bearing: measured 2026-09-08 against a throwaway image, the identical `--build-arg`
  against an `ARG` on the builder stage lands EMPTY in the runtime environment, so a well-formed
  build command produces an unstamped image.
  **The FULL 40-character SHA goes in the workflow's `ref` INPUT, not in `gh workflow run --ref`.**
  These are two different things and this bullet used to conflate them, which cost WS-P2.18 Inc 4 a
  422. `--ref` selects the git ref the workflow FILE is read from and expects a branch or tag;
  passing a raw SHA to it fails `HTTP 422`. The revision to build is a workflow **input**
  (`-f <input>=<40-char-sha>`), and there `actions/checkout` treats a non-40-character value as a
  branch/tag pattern, matches nothing, and fails the run — so a short SHA cannot build, however
  valid it is to `git`. Read `.github/workflows/release-image.yml`'s `inputs:` block for the input's
  actual name rather than guessing it. A plain `docker build .` with no `registry` context fails at
  `COPY --from=registry` — that is expected, not a Dockerfile bug. (Verified 2026-07-25, WS-P2.5
  Inc 2 deploy. Automation added 2026-07-26, image-build-automation workstream.)

### #53

- **An orchestrator image with NO labels is an old image, not a tampered one — every image built
  before 2026-08-02 carries `Config.Labels: null`.** WS-P2.18 Inc 7 made the build assert
  `org.opencontainers.image.revision` (full 40-char sha), `.source` and `.created`, and made the
  workflow **pull the pushed image back from the registry and fail closed** unless the revision
  label equals the commit built — a label present in the build command and absent from the
  artifact proves nothing, which is exactly how the no-label state survived unnoticed. Ask an
  image what it is with
  `docker image inspect <ref> --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`.
  **Until an image is next rebuilt the answer is `<no value>`, and for those the ONLY provenance is
  the tag's short sha** — `git rev-parse <short-sha>` back to the commit, and nothing at all if the
  tag was lost. Production's `c755c99-wsp218inc5-amd64`
  (`sha256:615c0b3053671fed9a33f9012f645f301eaf6da7aa9dafa29e14a7c06f820939`) is such an image; the
  discontinuity ends the first time production is rebuilt, and nothing about the deployed image was
  changed to close it. Note the container→image→digest correction above applies to reading labels
  too: `Config.Labels` on a *container* is the container's own label set, not the image's.
  **`sha-<full-sha>` is DERIVABLE, not IMMUTABLE — a rebuild silently re-points it, measured, not
  assumed.** Two builds of `e1204893…` on 2026-08-02 produced the same tag name and the digests
  `sha256:da035e69…` and `sha256:a0936e08…`. The `.created` label alone guarantees this (it is a
  build timestamp), and `python:3.12-slim` is a moving base tag underneath it, so image
  reproducibility was never a property this build had. **The digest is the identity; the tag is how
  you find it.** Two consequences: a rollback target should be recorded as a digest whenever one is
  to hand, and during a rebuild there is a real (seconds-long) window in which the two tags
  disagree, because buildx pushes them sequentially — observed live, and a plausible way to
  misdiagnose a defect that is not there. Making the derivable tag refuse to overwrite is an open
  follow-up, not something GHCR enforces.

### Binding the release (SDS 1.1 item 5a)

- **`--previous-commit` is the pre-swap revision recorded at the swap step (#305 above).**
  **Every re-run of a deploy's binder must reuse that same recorded value, never a fresh read.** A
  read after the swap returns the built commit, which turns every unbound unit into a silent
  `shipped_earlier`. Each run prints `previous_commit` and `built_commit` in its JSON output, so a
  re-run can copy both from the first run's output. After the
  swap, and only after the running container's `RepoDigest` equals the pushed digest and
  `/health/live` reports the built commit, run `image-release bind`. **A unit is bound to the first
  deployed image that carries it, from this program's first run onward**: it binds a
  `container_image` release artifact for every completed factory unit whose landing the built
  commit carries and the previous commit does not. Units an earlier image shipped stay unbound and
  are reported `shipped_earlier`. It then files the deployment observation, which mints one
  post-deploy unit per binding, and verifies that unit.
  **The observation is final**: one per binding per environment, and the minted unit has one
  attempt. So the command retries the whole production probe up to 6 times, 10 s apart, until
  every probe is 2xx, the revision matches, every required route is served and auth answers
  401/200. It writes nothing until the revision matches. A production still degraded after the
  retries is filed as it is, and verification then asks for revision.
  Run it from a worktree's venv (`uv run --directory <worktree>`), never the main tree's `.venv`,
  which the scheduled lanes own. Run `--dry-run` first: it reads and probes, writes nothing, and
  needs only the SYSTEM bearer. The two bearers are separate identities in separate variables. Fetch
  them in a subshell so neither they nor `BWS_ACCESS_TOKEN` outlive the command, and never echo them:

  ```bash
  (
    source ~/Projects/orchestrator/scripts/sds-token.sh
    bws_value() { env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])'; }
    ACTIVATION_BIND_TOKEN="$(bws_value 221a48d5-3f29-4898-b300-b4820140c880)" \
    IMAGE_VERIFY_TOKEN="$(bws_value 660d5846-abcb-4751-be86-b483012899eb)" \
      uv run --directory <worktree> image-release bind \
        --repository AlobarQuest/orchestrator --checkout <worktree> \
        --previous-commit <the pre-swap revision recorded at the swap, reused on every re-run> \
        --built-commit <full-40-char-sha> --digest <pushed sha256:…> \
        --observed-digest <running container's sha256:…> \
        --image-name orchestrator --tag <short-sha>[-<label>]-amd64 \
        --workflow-run-url <Release image run URL> \
        --base-url https://sds.alobar.net --deployer hq-session
  )
  ```

  Exit 0: done, or nothing to do. Exit 2: a condition, with the reason in the JSON output:
  - the two digests differ (nothing is read or written);
  - production still does not serve the built commit, or an unauthenticated read is not refused
    (nothing is written);
  - an authenticated read does not answer 200 (`auth_posture_unrecordable`, nothing is written);
  - a binding to another digest was never observed (orphaned);
  - a merge commit is missing from the checkout (fetch and re-run);
  - verification asked for revision or review.

  Exit 3: a refusal or an unreadable state, including a `--previous-commit` the checkout does not
  hold. A re-run over the same deploy re-files nothing.

  Run the binder from a worktree at the built commit. The observation's summary shapes are checked
  on both sides, and a binder that does not match the served image sends a shape the server refuses
  only after the binding is written (a newer binder against an older server, or the reverse).

### #61

- **Migrating before the image swap puts the STILL-RUNNING old image into `/health/ready` 503
  `migration_drift`, and that is only survivable because neither health check consults
  `/health/ready`.** The readiness probe compares the code's expected head against the database's,
  so between `alembic upgrade head` and the Coolify swap the old container reports unavailable
  while `/health/live` stays 200 and traffic keeps flowing. Coolify's own health check is
  **disabled** (`health_check_enabled: false`) and the Dockerfile `HEALTHCHECK` probes
  `/health/live`. **If either is ever pointed at `/health/ready`, migrate-first becomes an
  outage** — the container would be killed as unhealthy mid-window. Keep the window short and do
  not "improve" the health checks without re-deciding the migration order. Verified 2026-07-28
  (WS-P2.8 deploy, ~4-minute window, no traffic impact).

### #70

- **Coolify stores an `is_literal` env value wrapped in single quotes and injects the STRIPPED
  form — so a write must send the RAW value, and `is_build_time` is rejected outright.** Two
  independent traps in the same API, both hit during the GAP-7 password rotation. (1) The
  production `ORCHESTRATOR_DATABASE_URL` is stored as 111 bytes (`'<109-byte DSN>'`) while the
  container receives 109; POSTing the already-quoted form yields a **double-quoted** 113-byte
  value that still parses one layer down, so a naive readback check passes while the app would
  get a broken DSN. Verify by hashing what the CONTAINER receives
  (`docker exec … sh -c 'printf %s "$VAR"' | sha256sum`), never what the API returns. (2)
  `POST /api/v1/applications/{uuid}/envs` **422s on `is_build_time`** — `{"errors":
  {"is_build_time":["This field is not allowed."]}}` — the accepted spelling is `is_buildtime`,
  exactly as the GET response spells it. Because the reliable write path is
  delete-by-uuid + recreate, a 422 leaves the variable **absent**: validate an unfamiliar body
  against a throwaway key first, and retry rather than exit. (Verified 2026-07-29, GAP-7.)

### #82

- **Changing policy costs a release, and a release restarts the orchestrator — the artifact is read
  per call but its bytes arrive with the image.** WS-P2.18 Inc 2 separated two things that read as
  one: *getting new bytes to the process* (a release) and *making the process notice bytes it has*
  (free — `factory-policy.toml` is re-read per call, never cached). Only the second was decidable at
  that increment, and it is the half that cannot be retrofitted. **Operational rule: change policy
  only when no run is live** — the Actions run concluded, the unit out of `executing`, cost-actuals
  recorded. This is the same rule closing a bounded window already imposes, and for the same reason:
  the runner calls back at the *end* of its run, `fail-run` fails the same way `finalize-run` does,
  and a restart in that window strands the unit with its attempt spent.

### #105

- **The reach check needs `ORCHESTRATOR_APP_BRAIN_URL` and `ORCHESTRATOR_APP_BRAIN_READ_KEY` in the
  environment, and without them it fails closed SILENTLY.** WS-P2.28. Absent, admission refuses with
  `reach_estate_source_unconfigured` — correct behaviour, but nothing is stranded and nothing
  complains until somebody routes work and wonders why it will not run. **Both variables must ship
  with the release that first carries the check**; verify them from inside the container after the
  swap, the way `ORCHESTRATOR_M2M_*` is verified. Use the **read-only** key WS-P2.29 provisioned,
  never the full-access one.

### #194

- **A post-deploy check that asks whether production is HEALTHY, or which IMAGE it runs, cannot see
  that the served surface is missing a field a consumer needs. Ask what it SERVES.** Two days were
  lost to this on 2026-08-16: `#167` (the freshness rule) and `#177` (the deadlock fix) were merged
  and undeployed, production's `EstateLandingAdmissionResponse` carried only its seven pre-Increment-6
  keys, and the lander read `branch_update_qualifies`, got nothing, and **skipped every record**. The
  recurring `0 updated, 0 would-update` was the field being absent — not the rule finding nothing to
  do — and HQ read those lines each morning and reasoned from them. Production had been on
  `4cb6dd8-adr0019inc5b-amd64` since ~2026-08-12, which predates Increment 6 entirely.
  **The check is one command and belongs in every deploy verification alongside the digest:**
  `curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json;
  print(sorted(json.load(sys.stdin)['components']['schemas']['<ResponseModel>']['properties']))"`.
  Confirmed working after the 2026-08-16 swap to `6e47adb-adr0024-amd64`: the served properties are
  now `branch_update_qualifies, change_record_id, head_sha, policy_version, pr_number, refusals,
  repository, rollout_base_matches_pin, satisfied`.
  Note the pairing with the existing `response_model` invariant: that one says a field the model
  does not declare is silently dropped **in the code**; this one says a field the deployed image does
  not carry is silently absent **in production**. Both fail the same way — the consumer reads a key
  that is not there and continues — and neither is visible to a green test suite.

### #195

- **Pointing Coolify at a new tag is HQ's mechanic, not Devon's gate.** The paved road once
  called it *"a separate, manual gate"*, meaning **the workflow does not do it**, not that Devon
  does. His gate is deciding what and how a change may happen; the execution is HQ's, via
  `infraops` (`coolify_update_application` + `coolify_deploy`), which is the sanctioned path and
  which this session's infra-separation waiver permits. Misreading it cost a round-trip on
  2026-08-16 and it is the same conflation as assigning him a merge. **Record the outgoing tag
  before the write** — here `4cb6dd8-adr0019inc5b-amd64` — because the derivable `sha-<full>` tag
  can be re-pointed by a later build and the digest is the only immutable identity.

### #201

- **A DEPLOY VERIFICATION THAT CHECKS HEALTH, DIGEST, REVISION LABEL AND SERVED SCHEMA STILL CANNOT
  SEE THAT THE DATABASE IS BEHIND.** On 2026-08-19 HQ deployed three images (`#181`, `#182`, `#183`)
  and ran **zero** migrations; production sat at `0026_adr19_estate_merge` while head was
  `0028_adr26_change_record`, serving code that expected a column the database did not have. It
  surfaced as a **bare HTTP 500** from `POST /api/v1/package-intakes` — because only `DomainError`
  and `APIAuthenticationError` have registered handlers, a schema mismatch reaches the wire with no
  diagnosis, and only the container log named it:
  `psycopg.errors.UndefinedColumn: column work_package_revisions.change_record_id does not exist`.
  **Add `alembic current` vs `alembic heads` to every deploy verification.** It is one command and
  it is the only one of the five that can see this:
  `docker exec <container> sh -c 'cd /app && .venv/bin/alembic current; .venv/bin/alembic heads'`.
  The ordering rule already recorded here — migrate BEFORE the image swap, because the old container
  tolerates the new schema and the new one does not tolerate the old — was skipped three times in one
  afternoon without anything noticing. Earlier deploys were fine only because nothing needed a
  migration; that is luck, not a working practice.

### #267

- **THE ORCHESTRATOR'S COOLIFY SWAP IS NOT ZERO-DOWNTIME, unlike `brain`'s — and the difference is
  that its own Coolify health check is DISABLED.** Measured 2026-09-01 during the ADR-0038 deploy,
  timestamps from one probe loop and `docker inspect`: `11:23:36Z` the old container answering
  `/health/live` 200; `11:23:39Z` the new container started; `11:23:56Z` the proxy answering
  **`no available server`**; `11:24:18Z` the new container serving. So the old container was gone
  ~17 seconds after the new one started and ~22 seconds before it could serve. **This qualifies the
  recorded rolling-update invariant** — *"Coolify serves the OLD container throughout the swap"* —
  which was measured on `brain`, where `health_check_enabled` is TRUE and the log shape is
  `New container started` → `Waiting for healthcheck` → `New container is healthy` → `Removing old
  containers`. The orchestrator has `health_check_enabled: false` (deliberately, so `/health/ready`'s
  `migration_drift` 503 cannot kill the container during a migrate-first window), so Coolify has **no
  readiness signal to gate the removal on** and drops the old container on a timer instead. The
  timing is measured; that mechanism is inferred from the configuration and is not proven.
  Two consequences. A brief public outage is expected on every orchestrator deploy — do not diagnose
  it. And the CI-poll invariant that leans on the old container answering throughout does **not**
  hold here, so a liveness poll against `sds.alobar.net` during a swap can fail for a reason that is
  not a bad build.

### #285

- **[CORRECTED 2026-09-07 — the straggler is neither slow nor stuck: COOLIFY'S DEPLOYMENT
  FAILED. Read the correction at the end of this bullet before acting on any of it.]
  `brain`'s deploy verification fails on ONE OF FOUR APPS, a DIFFERENT one each time, and the
  straggler does not report within the full 600-second deadline.** Measured 2026-09-05 across the
  last eight push-triggered `ci.yml` runs: **two failed (25%)**, both at *"Verify every deployed
  brain is serving the new revision"*. Run `33843605720` — `infra`, `open`, `code` reported inside
  ~40 seconds and **`app` never did**; run `33871480326` — `open`, `infra`, `app` reported and
  **`code` never did**. So it is not one slow application, and it is not the deadline being
  marginally short: three swap in under a minute and the fourth is still absent ten minutes later.
  It resolves afterwards — `deploy_watcher` independently records `production_reached=yes
  attests=revision_confirmed` for both — so the change is fine and the workflow's verdict is not.
  Two consequences worth knowing before touching it. The per-app check is CORRECT and must stay:
  the four brains genuinely swap at different times, and a poll that checked one and generalised
  would pass while three served the previous image. And the failing run leaves its change record at
  `approved` rather than `resolved` (item 78 on 2026-09-04), so the records accumulate slowly.
  **Raising the deadline would be a guess** — nothing here says the straggler is slow rather than
  stuck, and that distinction is what a remedy would have to rest on.
  **CORRECTED 2026-09-07 BY ASKING COOLIFY, WHICH IS THE ONE PARTY NOBODY HAD ASKED. It is a
  third thing: the deployment FAILED and was rolled back, about two seconds in.** Coolify's helper
  container exits between the `docker run` that starts it and the `docker exec` that follows, the
  rolling update aborts on `No such container`, Coolify removes the new version, and the previous
  container keeps serving — which is byte-for-byte what a deployment still in flight looks like
  from the outside. The workflow's verdict was HONEST; what it could not do was name the cause,
  because it only ever asked production what it was SERVING.
  **Census over 154 deployments of the four brains: 5 failed, all in `brain-app` (3) and
  `brain-code` (2), none in `brain-infra` or `brain-open`.** Every failure carries a doubled
  `Preparing container with helper image` line and every success carries one — a clean
  discriminator, and the cheapest way to classify a new one. The Docker daemon log puts the
  helper's `task-delete` 1.7 s after its network join, with **no OOM kill** (the host has 3.8 GB
  and 2.8 GB of swap in use, so it is under pressure, but nothing killed it).
  **The obvious hypothesis is FALSIFIED, and the falsification is the reusable part.** `brain-app`
  and `brain-code` are triggered third and fourth against `concurrent_builds: 2`, so queueing
  looks like the answer. Measured, the wait before a deployment's first log line is 25.7–30.9 s
  for the five failures and **min 0.8 / median 24.6 / max 62.0 s for the 149 successes** — the
  failed range sits inside the successful distribution. Position separates the two PAIRS; queue
  wait separates nothing. **Why the helper exits is not established**, and a remedy resting on
  queueing would have been resting on nothing.
  **`It resolves afterwards` IS FALSE, and that is the half that cost real time.** It resolved for
  the 2026-09-04 pair because a later push redeployed them. Nothing corrected 2026-09-06:
  **`app-brain` served `4b80fac8` while `main` was `de1bb53b` for a day and a half**, under a red
  run on `main` whose message sent a reader looking at three things that were all wrong. A failed
  Coolify deployment is not self-healing — it waits for the next deploy of that app, or for a hand.
  **CLOSED for the REPORTING half by `brain#62`.** The trigger step keeps the deployment id Coolify
  names (it was printed and discarded, which is why nothing downstream could ask about it), and the
  verify step reads that deployment's status before each revision poll, failing fast with Coolify's
  own error line. Only an explicit `failed` decides — an unreadable status is not a verdict, so an
  unreachable Coolify costs a slower failure and never a wrong one — and the revision poll stays
  the authority on success, because `finished` and *serving the revision* are different facts and
  the four brains swap at measurably different times off one image. **The deployment still fails;
  it now says so in about forty seconds instead of ten minutes, and production still needs a
  redeploy.** Whether to retry automatically, serialize the four triggers, or raise
  `concurrent_builds` is open, and the last of those touches every app on that host rather than
  these four.

### #305

- **THE ORCHESTRATOR IMAGE DOES NOT SELF-MIGRATE, change-manager's DOES, AND THIS REPOSITORY'S TWO
  RECORDED RECIPES DISAGREE ABOUT IT.** Settled 2026-09-19 from the image rather than from prose,
  during the G1+G2 deploys. The orchestrator's `Dockerfile` ends at a bare
  `CMD ["uvicorn", "orchestrator.main:app", …]` — **no entrypoint script and no `alembic` anywhere**
  — so a swap applies no migration. change-manager is the opposite: its `entrypoint.sh` runs
  `alembic upgrade head` at container start, which is where the "no manual migrate step" line in the
  ADR-0019 Increment 1 report comes from. Two notes here conflict and both are about the
  orchestrator: `docs/superpowers/plans/2026-07-30-wsp212-context-enrichment.md:1719` says migrate
  against production, then build, then point Coolify; `…/2026-07-27-wsp27-inc2-inbound-reconciliation.md:1190`
  says point Coolify and deploy, then run alembic **in the new container**. The first is right, and
  the reason the second reads plausibly is that it describes what change-manager does.
  **THE MECHANISM IS THE PART NOBODY WROTE DOWN, and the obvious reading of "migrate first" is
  impossible:** the new migration exists only in the new image, and the running container carries
  the old code. So the order is **build → migrate → swap**, where BUILDING DEPLOYS NOTHING
  (`release-image.yml` only pushes; `build_pack: dockerimage` means Coolify pulls a prebuilt tag and
  never builds). Migrate by running alembic **from the new image** on the `coolify` network, with
  the database URL extracted inside the VPS from the running container's own environment and never
  printed:
  `DB=$(docker inspect <c> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^ORCHESTRATOR_DATABASE_URL=' | cut -d= -f2-)`
  then `docker run --rm --network coolify -e ORCHESTRATOR_DATABASE_URL="$DB" <new-image> sh -c 'cd /app && .venv/bin/alembic upgrade head'`.
  Measured twice that day — `0034→0035` and `0035→0036`, `current == heads` after each, swap ~50s,
  `/health/ready` back to 200 — and the drift window between migrate and swap is exactly the
  documented one: the old container reports `migration_drift` 503 on a readiness endpoint that
  Coolify's own check (`health_check_enabled: false`) and the Dockerfile `HEALTHCHECK`
  (`/health/live`) both ignore. Keep it short; do the tag write BEFORE the migration so the swap is
  one call afterwards.
  **Immediately before the swap call, record the `revision` that
  `curl -s https://sds.alobar.net/health/live` reports, and keep it with the deploy's notes.** It is
  the outgoing image's revision and the binder's `--previous-commit` (see "Binding the release").
  After the swap it can never be read again: `/health/live` then reports the new build.
