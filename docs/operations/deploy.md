# Deploying the orchestrator

How to build, migrate, swap and verify a production release, and what each check can and cannot see.

## The deploy command (primary procedure, SDS 1.1 item 7a)

Deploy the orchestrator with `scripts/deploy_orchestrator.py`. It runs the procedure the entries
below record (#305, "Binding the release", #61, #82, #194, #201) and refuses where they say a
deploy must not proceed. Run it from a worktree at the commit you are deploying, with that
worktree's own venv, and keep the state file in the session scratchpad, never a tracked path. Run
it under `rtk proxy`, because rtk swallows the stdout of `python -m`, and the result JSON goes to
stdout.

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
  runs hourly at :50, so deploy away from :50.
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
   the saved `lane-run --read` lines). It exits 0 only when the container runs the release's lane
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
check has no flags. Its `.bws-secrets.toml` secrets are the observer bearer, the change-manager
READ bearer and the read-only App's key, all read with the lane's one token, never the Healthchecks
API key; on the Mac the narrow and broad Keychain identities still read the first two. Each
container pass also makes an anonymous shallow clone of intent-packages into the pass's `HOME`
and points `BUMP_PROPOSER_PACKAGES_CHECKOUT` at it (ADR-0059 amendment 5); the report's first line
names the clone's commit, or git's exit code and error, and a failed clone is the program's
"packages checkout couldn't be read" gap (exit 3, or 2 beside a disagreement), never the
launcher's exit 1. The check is an outcome alarm: its compose leaves `SDS_DEADMAN_PING_URL_UUID`
empty until the lane's ping-URL secret exists, so until then the container's pass still runs,
pings nothing and says so. The read-App key row's `runtime-fetch` reader for
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
   shows whether an installation token with `administration=read` is served `allow_auto_merge`:
   served, or a gap per member. It also proves the read App's installation covers every
   repository the member record observes, and that they share one owner: otherwise the mint
   refuses, and every pass exits 1.
8. Cut-over steps 2 onward: the paired pass on both hosts, recording the Mac checkout's commit
   (`git -C ~/Projects/intent-packages rev-parse HEAD`) beside the clone's, since a difference the
   two commits explain isn't a cut-over failure; then the schedule and the Mac job's retirement.

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
files and the fork list's file) and `administration=read` (`allow_auto_merge`, which GitHub serves
only to a credential with admin read: `src/membership_check/auto_merge.py`), and exports the token
as both `SDS_MEMBERS_GITHUB_TOKEN` and `GH_TOKEN`, for the fork list's `gh api` read. A failed or
empty mint exits 1. Its one other GitHub read, the standing packages, is an anonymous shallow
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
