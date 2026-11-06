Outcome: done

# Report: lanes-inc-1-verify

Worker W-28. A read-only answer to ADR-0059's "Verify before increment 1" list, for HQ to plan
increment 1's code PR and its own production checks.

## Sources read

| Source | Version | Commit |
|---|---|---|
| orchestrator `origin/main` | — | `9c8d5d8f84a32a9eb09bd7abb00a7ca4904fedd5` (equals the ADR's accepted commit) |
| ADR-0059 | at `9c8d5d8f` | `git show 9c8d5d8f:docs/decisions/0059-…` |
| Coolify (`coollabsio/coolify`) | tag `v4.0.0-beta.473` | `57ea0764b8f0a491fd1d30bedc5cbe281744b36c` |
| infraops-mcp-server (local, read only) | `HEAD` | `2b8d0249ffa462800d10159754e66ca938d01d37` |
| Healthchecks (`healthchecks/healthchecks`) | tag `v5.0.3` (latest release) | `51a503531d845fc11d5ef270d576c59c300e3620` |
| supercronic (`aptible/supercronic`) | tag `v0.2.51` | `aa1edc759b11a38528edfacf47d01f9c9d287a31` |
| moby (`moby/moby`) | `master` on 2026-11-06 | `402d0a16f131d93ee89f1b06cb5fc8c98e766dfc` |
| A real `Release image` run log | run `42038653485` (2026-11-05, public repo) | built `5834824b` |

Orchestrator paths below are at `origin/main` (`9c8d5d8f`). Coolify paths are at `v4.0.0-beta.473`.
I made no Coolify, VPS, Healthchecks, BWS or infraops call, and changed no file except this report.

## Read this first: two findings that stop increment 1 for a decision

1. **Every resource variable reaches every container (item 1).** Coolify writes every variable
   of the resource into one `.env` file and adds `env_file: .env` to every service. So every lane
   container would hold every lane's `BWS_ACCESS_TOKEN`. ADR-0059 lines 357-360 say that "defeats
   one identity per lane, and increment 1 stops for a decision".
2. **No infraops tool can create `sds-lanes` (item 7).** At `v4.0.0-beta.473`, Coolify's create
   endpoint accepts `docker_compose_raw` only base64-encoded, and refuses any request that also
   carries `type`. infraops's `coolify_create_service` requires `type` and sends the compose
   plain. Q11 has HQ create `sds-lanes` with that tool.

The other items have answers that fit the ADR, with the details noted in each section.

## 1. Compose `image:` substitution, and which containers get a variable

**Answer:**

- **`image:` substitution: yes, but by `docker compose`, not by Coolify.** Coolify stores the
  `image:` string unchanged and runs `docker compose --project-directory <workdir> … up`, and
  Compose fills `${SDS_LANES_IMAGE_TAG}` from the `.env` file Coolify writes in that directory.
  Coolify's parser creates a resource variable for a `${VAR}` only in `environment:` and
  `build.args`, not in `image:`. So `SDS_LANES_IMAGE_TAG` must be created through the service env
  API before the first start, or the image resolves to `…orchestrator-lanes:` and the start fails.
- **Scope of a variable: every service gets every variable.** Each start writes every row of the
  resource's `environment_variables()` (no filter) into `<workdir>/.env`, and the parser appends
  `.env` to every service's `env_file`, with no opt-out. So every container's environment holds
  every resource variable: every `<LANE>_BWS_ACCESS_TOKEN` and every
  `<LANE>_LANE_SCHEDULE_ENABLED`. The per-service mapping `BWS_ACCESS_TOKEN:
  ${EXECUTION_WATCH_BWS_ACCESS_TOKEN}` only adds the plain name on top. This is the ADR's stop
  condition (lines 357-360).
- **A second exposure.** For a mapped `${VAR}` the parser also adds `VAR: <stored value>` to that
  service's `environment:` in the rendered compose, which Coolify stores in its `docker_compose`
  column and writes to `<workdir>/docker-compose.yml`. So each lane's token sits in plain text in
  the rendered compose (returned by `GET /services/{uuid}` to a `read:sensitive` token) as well as
  in `.env`. The ADR already forbids that read; this is one more reason.

Ways around it lie outside Coolify's per-resource model, for example one Coolify service resource
per lane (each with its own `.env`), or keeping lane tokens out of resource variables. That choice
is HQ's and Devon's; I record it here only as the shape of the decision.

**Evidence** (Coolify `v4.0.0-beta.473`):

- `app/Models/Service.php:49, 78`: new services get `compose_parsing_version` `'5'`;
  `Service.php:1605-1613` sends version 3 and later to `serviceParser`.
- `app/Actions/Service/StartService.php:17, 21, 38`: `$service->parse()`, then
  `saveComposeConfigs()`, then `docker compose --project-directory {$workdir} -f
  {$workdir}/docker-compose.yml --project-name {$service->uuid} up -d --remove-orphans
  --force-recreate --build`.
- `bootstrap/helpers/parsers.php:2075-2078`: `$savedService->image = $image` (stored, not
  substituted). `parsers.php:2006-2010`: variables are collected from `environment` and
  `build.args` only.
- `parsers.php:2694-2702`: "Auto-inject .env file so Coolify environment variables are available
  inside containers": `->push('.env')->unique()` and `$payload['env_file'] = $envFiles;` for
  every service.
- `app/Models/Service.php:1588-1600`: `foreach ($sorted as $env) { $envs->push("{$env->key}=
  {$env->real_value}"); }`, then `tee .env`. `Service.php:1533-1535`: `environment_variables()`
  is an unfiltered `morphMany`.
- `parsers.php:2447-2459`: a `${VAR}` with no default adds `$environment[$content] =
  $envVar->value;`. `parsers.php:2380-2394`: a `${VAR:-default}` does `firstOrCreate` with the
  default (so `${<LANE>_LANE_SCHEDULE_ENABLED:-false}` creates the variable as `false` when it's
  absent) and adds `$environment[$varName] = $envVar->value;`.

**Production confirmation needed: yes.** The placeholder can show it without a secret: create a
second, harmless resource variable that no compose entry maps, deploy, and run
`printenv <NAME>` in the placeholder container. If it prints, every variable reaches every
container on the live instance too.

## 2. Volume naming, `name:` and `external: true`

**Answer:** Coolify renames every named volume to `<service-resource-uuid>_<slug>`, where `<slug>`
is `Str::slug(<source>, '-')`, and writes a top-level entry `{name: <that>}`. It doesn't honor a
volume's `name:` or `external: true`: the only top-level settings it checks are `driver_opts.type`
`cifs` and `nfs`, which skip the rename. So `sds-lanes-execution-watch-state` becomes
`<uuid>_sds-lanes-execution-watch-state`, and the uuid is known only after the create. The ADR's
design already handles this: `lanes-finish` records the name it reads, never the tracked name.

On double-prefixing: every parse reads `docker_compose_raw` and writes back the original input
(without volume `content` keys), so repeated parses and starts give the same names. A volume is
double-prefixed only when a rendered compose is written into `docker_compose_raw` from outside, for
example a PATCH whose body is the `docker_compose` a GET returned. `Str::slug` turns the `_` in
`<uuid>_x` into `-`, which gives `<uuid>_<uuid>-x`, a new, empty volume. That matches workspace
invariant #4. The lane phases always send the tracked file, so they avoid it (item 6).

**Evidence** (`v4.0.0-beta.473`):

- `parsers.php:2207-2213`: `continue` only for `driver_opts.type` `cifs` or `nfs`.
- `parsers.php:2216-2217`: `$slugWithoutUuid = Str::slug($source, '-'); $name =
  "{$uuid}_{$slugWithoutUuid}";`. `parsers.php:2219-2231`: the mount source becomes `$name`, and
  `$topLevel->get('volumes')->put($name, ['name' => $name])`.
- `parsers.php:1507`: `$compose = data_get($resource, 'docker_compose_raw')`.
  `parsers.php:2727`: the rendered result goes to `docker_compose`. `parsers.php:2748`:
  `docker_compose_raw = Yaml::dump($originalYaml, 10, 2)`.

**Production confirmation needed: yes.** On the placeholder: `docker inspect` the container's
mounts and confirm the source is `<uuid>_<tracked name>`, then deploy again and confirm the same
name. Increment 1 already plans this read.

## 3. A new named volume takes the image directory's owner and mode

**Answer:** Yes. When Docker mounts a new, empty named volume over a directory that exists in the
image, it copies the directory's contents into the volume and sets the volume root's mode and
owner to the image directory's. So a `/var/lib/sds/<lane>` that the lane image creates owned by
`orchestrator` gives a new volume that `orchestrator` owns, with no init step. Three conditions:

- The copy happens only while the volume is empty. A volume that already holds any entry keeps
  its ownership, so a volume first populated by a root process stays root-owned.
- It applies to named volumes only (Compose named volumes are ordinary local-driver volumes), not
  to bind mounts or tmpfs.
- `nocopy` (`volume-nocopy`, or Compose `volume: {nocopy: true}`) turns it off. The tracked
  compose must not set it.

**Evidence:**

- Docker docs, Storage > Volumes (`docs.docker.com/engine/storage/volumes/`, source
  `content/manuals/engine/storage/volumes.md` lines 76-93): "If you mount an empty volume into a
  directory in the container in which files or directories exist, these files or directories are
  propagated (copied) into the volume." Lines 147 and 174 describe `volume-nocopy`.
- moby `402d0a16`, `daemon/create_unix.go:83-139` (`populateVolumes`): acts only when
  `mnt.Type == TypeVolume && mnt.CopyData`, and only when the image path is a directory.
  `daemon/volume/mounts/linux_parser.go:336` sets `CopyData` to the default (true) unless
  `nocopy` is given (parsed at line 293).
- moby `daemon/container/container_unix.go:386-400` (`copyExistingContents`): returns without
  copying when the volume directory has any entry ("destination is not empty, do not copy"),
  else calls `fs.CopyDir(volume, imageDir)`.
- moby `vendor/github.com/containerd/continuity/fs/copy.go:85-110` (`copyDirectory`):
  `os.Chmod(dst, stat.Mode())` on the root, then `copyFileInfo` copies uid and gid, then xattrs.

The docs are unversioned and the moby read is `master`, not the host engine's version. This
behavior has been stable across Docker releases, but the host's engine version isn't known here.

**Production confirmation needed: yes.** On the placeholder's volume, after `sds-lanes` exists:
`stat -c '%U %a' /var/lib/sds/<placeholder>` in the container (or `docker volume inspect` plus
`stat` on the mountpoint) must show `orchestrator`. Increment 1 already plans this check.

## 4. `GET /api/v3/checks/` returns `started`, true from `/start` until the result ping

**Answer:** Yes, at Healthchecks `v5.0.3`. Every check in the v3 listing carries a boolean
`started`, which is `last_start is not None`. A `/start` ping sets `last_start`. A success or fail
ping clears it. The field is in the read-only key's listing too, so the read-only Healthchecks key
(Q5) can read it. Two details matter for the quiet-slot read:

- `started` stays `true` after a pass dies without a result ping, including after the check goes
  `down` past its grace, because `get_status` marks it down without clearing `last_start`. That's
  why the ADR's exception (a check whose `status` is also `down` doesn't hold the deploy) is
  needed: without it, one dead pass would hold every redeploy until its next pass pings.
- A success ping whose `rid` doesn't match the start's `rid` doesn't clear `started`.
  `scripts/sds-deadman.sh` sends no `rid` (its pings are `curl … "${SDS_DEADMAN_PING_URL}$1"`,
  lines 133 and 147), so `rid is None` and a success ping clears it. A launcher that later adds
  `rid` must send the same `rid` on both pings.

A paused check with manual resume ignores pings (`action = "ign"`), so its `started` doesn't
change.

**Evidence** (Healthchecks `v5.0.3`, `51a50353`):

- `hc/api/models.py:449`: `"started": self.last_start is not None,` inside `to_dict`. The listing
  view `get_checks` (`hc/api/views.py:413-435`) returns `check.to_dict(readonly=request.readonly,
  v=request.v)` per check.
- `hc/api/models.py:439-440, 448`: `with_started = v == 1`; only API v1 folds `started` into
  `status`. In v3, `status` stays `up`, `down`, `grace` and so on, and `started` is separate.
- `hc/api/models.py:471-475`: `if readonly: result["unique_key"] = …` else `uuid`, `ping_url`,
  `update_url`. The base dict, `started` included, is the same for both.
- `hc/api/models.py:516-533`: `if action == "start": self.last_start = frozen_now`;
  `elif action in ("success", "fail"):` clears `last_start` when `last_start_rid == rid`, or when
  `action == "fail" or rid is None`.
- `hc/api/models.py:359-363` (`get_status`): down once `now >= last_start + grace`, with
  `last_start` left set.

**Production confirmation needed: yes.** The live instance's version is unknown. Every rendered
page's footer prints it (`templates/base.html:215`, `site_version` in
`hc/front/templatetags/hc_extras.py:105-106`), but reading it is a Healthchecks call this task
excludes. HQ confirms the version, and that `started` appears in a read-only listing, once the
D-7 grant exists.

## 5. A UUID ping URL needs no other credential and only pings; `PING_ENDPOINT`

**Answer:** Yes on both counts, at `v5.0.3`. The UUID routes (`/ping/<uuid>`, `/start`, `/fail`,
`/log`, `/<exit-status>`) take no key and are CSRF-exempt. The view looks the check up by UUID,
records a ping, updates the check's status (which can send its alerts), and returns 404 for an
unknown UUID. It changes no configuration and can't create a check: only the slug route
(`/ping/<ping_key>/<slug>?create=1`) creates checks, and it needs the project's ping key. Two
edge cases: an exit status above 255 returns 400, and a check with HTTP-body filtering lets the
request body choose success or fail.

`PING_ENDPOINT` sets only the base URL Healthchecks prints in each check's `ping_url` (API
responses and pages). It doesn't change routing. The default is `SITE_ROOT + "/ping/"`, with a
required trailing slash. So a lane's ping URL is `<PING_ENDPOINT><uuid>`, which on a default
configuration is `https://healthcheck.alobar.net/ping/<uuid>`.

**Evidence** (`v5.0.3`):

- `hc/api/urls.py:34-38, 75-76`: the UUID ping routes and their sub-routes.
- `hc/api/views.py:181-244` (`ping`): `@csrf_exempt`, lookup by `code`, `check.ping(...)`.
- `hc/api/views.py:248-290` (`ping_by_slug`): the only `create=1` path, keyed on `ping_key`.
- `hc/settings.py:271`: `PING_ENDPOINT = os.getenv("PING_ENDPOINT", SITE_ROOT + "/ping/")`.
- `templates/docs/self_hosted_configuration.md:621-640`: `PING_ENDPOINT` is the base for the
  displayed ping URLs.
- `hc/api/models.py:474`: `result["ping_url"] = settings.PING_ENDPOINT + str(self.code)`.

**Production confirmation needed: yes.** Source can't show the live instance's `PING_ENDPOINT`,
whether a reverse proxy rewrites `/ping/`, or local patches. HQ confirms by comparing one check's
`ping_url` on its page with `https://healthcheck.alobar.net/ping/<uuid>`.

## 6. `coolify_update_service` with `docker_compose_raw`, the next deploy, and the responses

**Answer:**

- **It replaces the whole stored compose.** `PATCH /api/v1/services/{uuid}` requires
  `docker_compose_raw` base64-encoded, decodes it, normalizes it with `Yaml::dump(Yaml::parse(…))`
  (which drops comments and reformats), and sets `docker_compose_raw` to the result. infraops's
  `coolify_update_service` base64-encodes what it's given. The stored text is therefore
  semantically the tracked file but not byte-identical, so the lane phases must record the file
  they sent and never compare it byte for byte with what Coolify stores.
- **The PATCH re-parses at once; the next `coolify_deploy` runs it.** The PATCH calls
  `$service->parse()` unconditionally, which renders `docker_compose` from the raw text. It
  doesn't restart anything. `POST /api/v1/deploy` on a service calls `StartService::run`, which
  parses again, writes the rendered compose and `.env` to the work directory, and runs `docker
  compose up -d --remove-orphans --force-recreate`. `--force-recreate` recreates every container,
  which confirms the ADR's resource-wide redeploy (lines 182-187). `--remove-orphans` also removes
  any container whose service the new compose lacks (item 14). The service `start` endpoint
  refuses a running service, so `coolify_deploy` is the right call. Variable writes take effect
  only at the next start.
- **It avoids the double-prefix path,** as long as the body is the tracked source file. Parse
  always reads the raw field (item 2). The hazard is only a raw field holding rendered compose.
- **Responses.** The service PATCH returns only `{uuid, domains}`: no compose, no value. The
  service env create, update and bulk responses return the variable row through
  `removeSensitiveData`, which hides `value` and `real_value` only when the token lacks `root` and
  `read:sensitive`. For a token with either, the row carries the variable's plaintext value.
  infraops's redaction (`src/utils/register-sanitized.ts`, `deepRedact` in
  `src/utils/redaction.ts`) matches secret field names (`password`, `secret`, `token`, …), and the
  field is named `value`, so it doesn't fire; only shape rules (PEM, JWT, `ghp_`, `sk-`) catch
  anything. So `coolify_update_service_env` echoes the value it wrote. For the values the session
  writes (`SDS_LANES_IMAGE_TAG` and a schedule value) that's harmless. It's a reason never to
  write a lane token through these tools, which Q8 already rules out. Each response carries only
  the written row, never another variable.

**Evidence** (Coolify `v4.0.0-beta.473`; infraops `2b8d0249`):

- `app/Http/Controllers/Api/ServicesController.php:974-988`: `isBase64Encoded` and UTF-8 checks;
  `:993` `Yaml::dump(Yaml::parse($dockerCompose), …)`; `:1007` `$service->docker_compose_raw =
  $dockerComposeRaw;`; `:1024` `$service->parse();`; `:1049-1052` the `{uuid, domains}` response.
- `bootstrap/helpers/shared.php:1789`: `isBase64Encoded` is a strict round trip.
- `app/Http/Controllers/Api/DeployController.php:523-531`: `case Service::class: …
  StartService::run($resource);`.
- `ServicesController.php:25-40`: `removeSensitiveData` hides `docker_compose_raw`,
  `docker_compose`, `value` and `real_value` only when `can_read_sensitive === false`.
  `app/Http/Middleware/ApiSensitiveData.php:16`: `$token->can('root') ||
  $token->can('read:sensitive')`. Env responses: `:1258`, `:1384` (bulk), `:1509`.
- `app/Models/EnvironmentVariable.php:77`: `$appends = ['real_value', …]`; no `$hidden`.
- infraops `src/tools/services.ts:265-269`: `Buffer.from(params['docker_compose_raw'] as
  string, …).toString('base64')`. `src/utils/redaction.ts:13-14`: the `SECRET_NAME` pattern.

**Production confirmation needed: yes,** for two things source can't show: whether infraops's
production Coolify token has `read:sensitive` or `root` (which decides whether env writes echo
values), and that a PATCH then `coolify_deploy` keeps the placeholder's volume name (the item 2
read). infraops runs from its built `dist/`, which I didn't compare with `src/`.

## 7. `coolify_create_service`'s encoding, and which tool writes a service variable

**Answer:**

- **Coolify's create endpoint accepts `docker_compose_raw` only base64-encoded.** Plain YAML gets
  422 "The docker_compose_raw should be base64 encoded." It also refuses a request that carries
  both `type` and `docker_compose_raw`, and the compose branch's allowed-field list omits `type`,
  so even an empty `type` key fails "This field is not allowed."
- **infraops's `coolify_create_service` can't create `sds-lanes`.** It requires `type` in its input
  schema and always sends it, and it sends the compose plain (`body.docker_compose_raw =
  params.docker_compose_raw`). Every request it can build fails one of those checks. Its own
  description ("use 'docker-compose' for custom compose") doesn't match this Coolify version. The
  create needs an infraops change (the infraops-escalation lane) or a decision on another path.
  This conflicts with Q11 as written, so I report it and don't resolve it. The other infraops
  compose tools don't fill the gap. `coolify_create_application_dockercompose` sends the compose
  plain to `POST /applications/dockercompose`, which Coolify marks deprecated ("Use POST
  /api/v1/services instead", `routes/api.php:108`) and which also requires base64
  (`ApplicationsController.php:1997`). `coolify_set_compose_config` patches an existing
  application's compose settings and creates nothing.
- **Create doesn't deploy unless `instant_deploy` is true,** and the first parse writes empty
  values for variables not yet created. So the order is: create without `instant_deploy`, create
  the variables, then `coolify_deploy`.
- **The tools that write a service variable** are `coolify_create_service_env` (`POST
  /services/{uuid}/envs`), `coolify_update_service_env` (`PATCH /services/{uuid}/envs`, matched by
  key) and `coolify_bulk_update_service_envs` (`PATCH /services/{uuid}/envs/bulk`).
  `coolify_delete_service_env` removes one. The `*_app_env` tools call `/applications/…`. Infra
  Brain rule 239 is about application env tools on a compose build-pack application, which is a
  different resource type. Nothing in infraops's docs says the service env tools fail on a compose
  service, and the Coolify routes they call are the service routes, so source says they work.
  One detail: the single create and update tools send `is_buildtime`, `is_runtime` and
  `is_preview`, and Coolify's single routes ignore them; the bulk route applies them.

**Evidence** (Coolify `v4.0.0-beta.473`; infraops `2b8d0249`):

- `ServicesController.php:347-351`: "You cannot provide both service type and
  docker_compose_raw". `:495-497`: the compose branch's `$allowedFields`, without `type`.
  `:522-527`: `array_diff(array_keys($request->all()), $allowedFields)` → "This field is not
  allowed." `:578-595`: the base64 and UTF-8 checks. `:611`: `$instantDeploy =
  $request->instant_deploy ?? false`. `:627`: `$service->parse(isNew: true)`. `:648-650`:
  `StartService::dispatch` only when `instant_deploy`.
- infraops `src/tools/services.ts:172-176`: `type: z.string()` (required). `:206-216`: `type:
  params.type` in the body. `:219`: the compose sent plain.
- infraops `CLAUDE.md:198-203`: the service and application env endpoints are separate, and
  `coolify_update_service` needs base64.

**Production confirmation needed: yes,** for the service env tools on a live compose service, and
for whether service variables get the duplicate `is_preview` rows the application variables get
(orchestrator CLAUDE.md, the Coolify `/envs` bullet). If they do, `.env` would repeat a key and the
later line wins.

## 8. Where each lane-phase run's state file lives

**Answer (design, two options and a recommendation).** The requirement is that any later session
on the Mac (an HQ cycle in a different worktree, or Devon's interactive session) finds the compose
Coolify was last given and any waiting or unfinished run, with no new credential. The orchestrator
deploy's own state file can't serve: `docs/operations/deploy.md` line 10 keeps it in the session
scratchpad, which the next session can't see.

The record has the same content in either option: one JSON file per run, written atomically
(write a temporary file, then rename), with a lock file held by `flock` while a phase reads and
writes, so two sessions never interleave. Each run file holds the change (`--tag`, `--schedule` or
`--add`), the tag and its full commit, the lane image digest, the exact compose text written and
the one it replaces, the replaced variable value, each stateful service's recorded volume name,
the quiet-slot deadline (item 10), and a status: `waiting`, `refused`, `timed_out`, `superseded`,
`unfinished` (from `lanes-recheck` exit 0), `finished` or `restored`. "The last finished run" is
the newest file whose status is `finished` or `restored`.

- **Option A: a fixed directory on the Mac, outside every repository.** For example
  `~/Library/Application Support/sds/deploy-lanes/`, a sibling of the lanes' Mac state
  directories. Every session runs as the same macOS user, so every session reads it, and it needs
  no credential and no network. It's covered by Time Machine. Its cost: the record lives on one
  Mac, so the deploy command can't run elsewhere until the record moves. The ADR already places
  the deploy command "where it runs today, in the deploying session on the Mac" (lines 196-197),
  so that costs nothing in this phase.
- **Option B: a tracked file in the sds-hq repository** (for example `state/lanes/`), which HQ
  commits and pushes each cycle (`2026-11-05-sds-hq-always-on.md`, Task step 9). It gains history,
  an off-Mac copy, and review in git. Its costs: orchestrator code depends on another repository's
  layout and checkout path; a run written by a session that isn't an HQ cycle (Devon's
  interactive deploy) isn't committed until an HQ cycle commits it; and a push that fails leaves
  the Mac copy and the remote different, so two clones can disagree about whether a run is
  unfinished.

**Recommendation: Option A,** as a module constant (`LANES_STATE_DIR`) that tests override
through a `World` field, as `World.checkout` is overridden today. It meets "any session" on the
one machine where the deploy runs, with no cross-repository coupling. HQ can copy the run files
into its own state for history without becoming their source.

**The first compose (ADR lines 142-143 and 1328-1329).** No lane-phase run precedes the create,
so no file records the compose HQ creates `sds-lanes` with. One way that keeps one writer: a fourth
entry point, `lanes-finish --initial --ref <commit>`, which HQ runs after the create and first
deploy with the same container reads `lanes-finish` takes. It records the tracked compose at
`<commit>` as a `finished` run, and only when no run file exists. This is a proposal for HQ's
plan, not something the ADR decides.

**Production confirmation needed: no.** It's a design choice; nothing live settles it.

## 9. Can `prepare` read the lanes' state, refuse, and admit `--ref` at the lanes' commit

**Answer:** Partly. Reading the state and refusing fits the existing code with no new credential,
but the `--ref` admission conflicts with the code as it stands.

**Reading and refusing: yes.** With Option A, `prepare` reads a local file by a constant path,
which needs no new argument and no credential. It compares the last finished run's commit with
the served commit, which `prepare` already reads from `/health/live` (`pre_swap_revision`,
`scripts/deploy_orchestrator.py:350-358`), and refuses with `Refused` (exit 3), whose message
names the two exits (re-run the lane phases with `--tag` at the served tag, or roll the lanes
back). Unfinished is any run file with status `unfinished`. The functions it changes:

- `World` (lines 122-130): a `lanes_state_dir: Path` seam.
- A new `require_lanes_current(world, served, sha)`, called from `_prepare` (lines 518-565) after
  `pre_swap_revision` (line 530) and before `state.save` (line 550), so a refusal dispatches
  nothing.
- `_finish` (lines 820-846): prints the lane phases' first command after the binder passes (ADR
  lines 116-119).
- `_resume` (lines 583-595) needs no check: it never dispatches, and its first `prepare` checked.

The lanes' tag must be recorded with its full commit. `State.tag` is a readable tag
(`readable_tag`, `scripts/compute_image_tags.py:42-48`, `<short>[-label]-amd64`), which doesn't
give back a 40-character sha.

**The `--ref` admission: conflict, reported and not resolved.** The ADR (lines 231-233 and
760-761) has `prepare` admit `--ref` at the lanes' commit, "because that rollback leaves the lanes
on the served tag". That ref is older than the served commit. `migrations_between`
(`scripts/deploy_orchestrator.py:361-381`) raises `Refused(f"production's {previous} is not an
ancestor of {built}; a rollback is not this procedure")` whenever the served commit isn't an
ancestor of the built one, and `docs/operations/deploy.md` line 77 records that refusal as
deliberate. So `prepare` refuses that ref before any lanes check runs. The admission needs the
rollback refusal lifted or re-decided, which is a decision for HQ, not this report. The same
conflict touches ADR lines 121-122 (an orchestrator rollback "runs the lane phases with `--tag`
set to the rollback target"): the orchestrator half of that rollback isn't `prepare`'s procedure
today. A related gap: a rollback target older than increment 1 has no lane image, so a lanes
rollback can't go below increment 1's commit.

**Production confirmation needed: no.**

## 10. How the quiet-slot deadline is stored so a later cycle resumes against it

**Answer (design).** The deadline is an absolute UTC timestamp (ISO 8601) in the run's state file,
written by the first `lanes-prepare` for a change, with a `change_key` that identifies the change
exactly: `("tag", <commit>)`, `("schedule", <lane>, <value>)` or `("add", <lane>, <commit>)`. Each
`lanes-prepare` run, under the directory lock:

1. Refuses (exit 3) if any run is `unfinished`.
2. Finds the newest `waiting` run. If its `change_key` matches, it resumes: same file, same
   deadline. If it differs, it marks that run `superseded` and creates a new one with a deadline
   6 hours on.
3. If the deadline has passed, it marks the run `timed_out`, raises the alert, and exits 2. A
   later run for the same change finds no `waiting` run and records a new deadline, as the ADR
   says.
4. Otherwise it reads the two quiet-slot conditions and exits 2 (`waiting`) or 0.

The deadline must be wall-clock time. The program's only clock today is `World.clock =
time.monotonic` (line 128), which has no meaning across processes, so this needs a second seam
(`now: Callable[[], datetime]`) for tests. Each run ends well within the 1200-second cycle limit,
because it waits for nothing.

The alert has a credential gap the ADR doesn't name: `scripts/deploy_orchestrator.py` sends no
alert today (no `alert` in the file), and the manifest's alert-hub sender tokens
(`.bws-secrets.toml` lines 131-139 and 210-223) are read by other programs. Which token the
deploy command sends with, and whether `sds-operator` can read it, is listed under concerns.

**Production confirmation needed: no.**

## 11. Where `prepare` detects a ref whose tracked compose adds more than one service

**Answer:** In `_prepare`, beside `refuse_readiness_health_checks(world, sha, …)` (line 528), which
already reads tracked files at the ref with `_git(world, "show", f"{sha}:{name}")` (line 297). A
new `refuse_compose_additions(world, sha, recorded)` would read the tracked lanes compose at
`sha`, parse both it and the last finished run's recorded compose with `yaml.safe_load` (`pyyaml`
is a runtime dependency, `pyproject.toml` line 20), and refuse when `services` at `sha` has more
than one key the recorded `services` lacks. It runs with `require_lanes_current` (item 9), before
`state.save`. Two edge cases it must take:

- A ref with no lanes compose (before increment 1): `_git` raises `Refused` on a missing path, so
  the function must tell "no file" from "unreadable".
- A ref whose compose lacks a recorded service: that's item 14's case, not an addition.

**Production confirmation needed: no.**

## 12. How Coolify treats an unhealthy or no-healthcheck service container

**Answer:** Coolify only reports health; it never restarts or stops a container for it. It adds no
health check to a service compose and passes a compose `healthcheck:` through unchanged. A
container's status is `<state>:<health>`, with `unknown` when the container has no health check,
so a lane container with none shows `running:unknown`, and one with the inherited `HEALTHCHECK`
shows `running:unhealthy`. Only an API call restarts a service. Docker's own restart policy
(Coolify's default is `unless-stopped`, which meets Infra Brain WARN 214) acts on exit, not on
health. So the inherited `HEALTHCHECK` (a `/health/live` request on port 8000, which no lane
serves) would mark every lane unhealthy in Coolify's view and change nothing else.

What the lane image can set:

- **`HEALTHCHECK NONE`** in the lane Dockerfile, which turns off the inherited one. Status reads
  `running:unknown`. Infra Brain WARN 570 requires a health check only on an exposed application
  with an FQDN, and `sds-lanes` has neither.
- A process check, such as `HEALTHCHECK CMD pgrep -x supercronic`. It adds little: the scheduler
  is PID 1, so the container exits when it does, and Docker restarts it.
- A compose `exclude_from_hc: true` removes a container from Coolify's aggregate status. It's
  sticky: Coolify never sets it back to false when the key is removed.

**Recommendation:** `HEALTHCHECK NONE`. A lane's real health signal is its Healthchecks check,
which the dead-man ping already feeds.

**Evidence** (`v4.0.0-beta.473`):

- `app/Actions/Docker/GetContainersStatus.php:105-116`: `$containerHealth ??
  'unknown'`, then `"$containerStatus:$healthSuffix"`.
- `app/Services/ContainerStatusAggregator.php:219-255`: `running:unhealthy`, and `degraded`
  only for mixed running and exited, restarting or dead containers.
- `ServicesController.php:1847`: the only caller of `RestartService`. No job, listener or console
  command restarts or stops a service on health (grep of `app/Jobs`, `app/Listeners`,
  `app/Console`; `DeleteResourceJob` stops only on delete).
- `bootstrap/helpers/constants.php:18`: `const RESTART_MODE = 'unless-stopped';`.
- `parsers.php:2655-2661`: `exclude_from_hc` or `restart: no` sets `exclude_from_status`, and
  the key is removed from the rendered file. `app/Traits/CalculatesExcludedStatus.php:136-143`.

**Production confirmation needed: no** for the behavior. HQ sees the placeholder's status after
the create in any case.

## 13. `pushed_digest()` and the lane image's digest

**Answer:** The pattern is `LOG_DIGEST = re.compile(r"\bDIGEST: (sha256:[0-9a-f]{64})\b")`
(`scripts/deploy_orchestrator.py:85`). `pushed_digest` (lines 477-487) collects every match in the
run's log into a set and refuses unless it has exactly one. It also requires every `REVISION:`
match to be the built sha and every `SHA_TAG:` match to be `derivable_tag(sha)`, which is
`ghcr.io/alobarquest/orchestrator:sha-<sha>` (`compute_image_tags.py:37-39`).

The log lines come from GitHub's echo of each step's `env:` block. In run `42038653485` the only
matches are line 981 (`DIGEST: sha256:2bca2cc5…` in "Verify the pushed image asserts its own
provenance"), line 982 (`REVISION:`), and lines 657 and 983 (`SHA_TAG:`). The build script's
`DIGEST=$(…)` and the summary's `Digest:` don't match.

So a lane verify step that reuses `env: DIGEST:` adds a second distinct digest, and `prepare`
refuses. A lane verify step that reuses `SHA_TAG:` with the lanes image name also refuses, a
second trap the ADR doesn't name. I tested the pattern on candidate names (Python `re`, the same
two patterns):

| Line | `LOG_DIGEST` | `LOG_SHA_TAG` |
|---|---|---|
| `  LANE_DIGEST: sha256:…` | no match | — |
| `  LANE_SHA_TAG: x` | — | no match |
| `  LANES-DIGEST: sha256:…` | match | — |
| `  LANE DIGEST: sha256:…` | match | — |

`\b` needs a word/non-word change, and `_` is a word character, so `LANE_DIGEST:` doesn't match.

**The smallest change:**

1. `release-image.yml`: the lane image's verify step names its variables `LANE_DIGEST`,
   `LANE_SHA_TAG` (and `LANE_REVISION`, or keeps `REVISION`, which holds the same sha and passes).
2. `deploy_orchestrator.py`: add `LOG_LANE_DIGEST = re.compile(r"\bLANE_DIGEST:
   (sha256:[0-9a-f]{64})\b")` and require exactly one distinct match, as for `DIGEST:`, in
   `pushed_digest` or a sibling `pushed_lane_digest`. Leave `LOG_DIGEST` unchanged.
3. `State` (lines 224-264) gains `lane_digest: str = ""`, a default, so `cls(**raw)` still loads a
   state file written before the change. `State.load` validates it against `DIGEST` when present.
4. `tests/scripts/test_deploy_orchestrator.py`: the fake log `_log` (lines 219-231) emits the
   `LANE_DIGEST:` line, and the look-alike test (around line 799) gains the `LANE_DIGEST` case for
   `LOG_DIGEST`.

**Production confirmation needed: no.** The log format is read from a real public run.

## 14. How a rollback past an `--add` keeps each cut-over lane running

**Answer (design).** The constraint that shapes this is the single `SDS_LANES_IMAGE_TAG`: every
service runs the same tag, and a lane's launcher and crontab line arrive with the commit that adds
its service. So keeping the added service in the written compose on an older tag doesn't keep the
lane running: the older image has no launcher for it, and its scheduled passes fail. "Merge the
recorded services into the rollback's compose" isn't enough on its own.

- **Option A: the lane goes back to the Mac first.** `lanes-prepare --tag <target>` refuses when
  the target's tracked compose lacks a service the last finished run recorded, unless that
  service's recorded `LANE_SCHEDULE_ENABLED` is `false`. Its refusal names each such lane and the
  existing "Rollback for one lane" procedure (ADR lines 991-1013), which unschedules the service,
  copies its state back, and bootstraps the Mac agent. Once every such lane runs on the Mac, the
  `--tag` run writes the target's compose plus each recorded-only service's entry, kept so no
  lane-phase run removes a service (ADR lines 788-789), with its schedule held `false`. The lane
  keeps running, on the Mac, on code no newer than the served orchestrator's. A roll forward
  re-runs its cut-over. Cost: a rollback past an `--add` takes one quiet slot for the unschedule
  and one for the tag, plus a state copy per lane. It also changes an ADR rule: the compose such a
  run writes is no longer the tracked file at the tag it deploys (ADR lines 135-137), so HQ is
  choosing between two rule changes, this one or Option B's.
- **Option B: one image variable per added lane.** Each service's `image:` names its own variable
  (`<LANE>_IMAGE_TAG`), and a rollback moves only the services the target's compose carries. An
  added lane keeps the tag it was added at and keeps running in its container. Cost: it breaks
  the ADR's rule that a lane runs the served tag or the one before it (lines 119-121), and the
  added lane's newer code then runs against an older orchestrator, which the compatibility rule
  (lines 217-222) doesn't cover: it protects old lanes against a new orchestrator, not the
  reverse. It also multiplies the variables every `--tag` writes.

**Recommendation: Option A.** It keeps both of the ADR's invariants (lane code never newer than the
served orchestrator's, and no lane-phase run removes a service), and it reuses the rollback the
ADR already defines, with no new mechanism. Its delay is real: an urgent orchestrator rollback
past an `--add` waits on two quiet slots unless HQ redeploys by hand. That's a decision for HQ to
accept.

Coolify's deploy confirms the ADR's premise (lines 775-778): it runs `docker compose up
--remove-orphans` (`StartService.php:38`), so a written compose that lacks a service removes that
service's container.

**Production confirmation needed: no.**

## Scheduler candidates

The ADR's two requirements (lines 305-309): the scheduler runs as `orchestrator`, not root, and
starts `lane-run` with the container's environment.

| Candidate | Runs as `orchestrator` | Passes the container's environment | Pinnable | Fit |
|---|---|---|---|---|
| supercronic `v0.2.51` | Yes. It runs as whatever user starts it (Dockerfile `USER`) and ignores a crontab user field. | Yes. `cron/cron.go:75-79`: `env := os.Environ()`, then crontab `VAR=` lines, then `cmd.Env = env`. | Release binaries per architecture with published sha256 digests. | Meets both. |
| go-crond `26.10.0` (webdevops) | Yes, with `--allow-unprivileged`. Per-job users need root. | Yes, by its README ("keeps current environment"). Not read in source. | Release assets with digests (not read). | Meets both, by docs only. |
| Ofelia `v0.3.22` | Its exec and run jobs need the Docker socket. | Runs jobs through the Docker API in other containers. | Tagged images. | Rejected: the Docker socket is root-equivalent on the host. |
| busybox `crond`, Debian `cron` | Need root in their usual setup. | No: minimal environment. | From the distribution. | Rejected; the ADR names Debian `cron`'s two failures. |
| A shell `sleep` loop | Yes. | Yes. | Nothing to pin. | Meets both, but drifts, has no cron syntax, and needs hand-written overlap and signal handling. |

supercronic's other properties that matter here (`v0.2.51` `main.go`):

- It skips an overlapping run of the same job by default (`-overlapping`, line 55, turns overlap
  on). The bump driver's `flock` still holds across a `lane-run` exec.
- It handles `SIGTERM` and `SIGINT` by waiting for running jobs, which matters because every
  redeploy stops every container.
- It reaps child processes when it's PID 1 (`-no-reap`, line 39, disables it), so it can be the
  entrypoint's `exec` target with no `tini`.
- `-json` (line 34) makes its own log lines JSON on stdout; `-passthrough-logs` (line 50) leaves
  the launcher's output unwrapped.
- With an empty crontab it keeps running and runs nothing, which is the unscheduled state the ADR
  wants. **Not verified in source:** whether `v0.2.51` accepts a crontab file with no entries
  without exiting. Increment 1's placeholder proves it, or the entrypoint writes a comment-only
  file and checks it with `supercronic -test`.

**Recommendation: supercronic `v0.2.51`,** downloaded in the lane image build as the
`supercronic-linux-amd64` release asset and checked against its sha256 digest (never `:latest` or
an unpinned URL), started by the entrypoint with `exec supercronic -json <crontab>` as `USER
orchestrator`. HQ picks.

**Production confirmation needed: yes** for the empty-crontab behavior, which the placeholder
shows.

## Items that need production confirmation

Every item marked `production confirmation needed: yes`:

- **Item 1:** that every resource variable reaches every container on the live instance (a
  harmless unmapped variable, then `printenv` in the placeholder). This is the stop condition.
- **Item 2:** the placeholder volume's source is `<uuid>_<tracked name>`, and stays so across a
  second deploy.
- **Item 3:** the placeholder volume's root is owned by `orchestrator`.
- **Item 4:** the live Healthchecks version, and `started` in a read-only listing.
- **Item 5:** the live `PING_ENDPOINT` (a check's `ping_url` against
  `https://healthcheck.alobar.net/ping/<uuid>`).
- **Item 6:** whether infraops's production Coolify token has `read:sensitive` or `root`, and that
  a PATCH then `coolify_deploy` keeps the volume name.
- **Item 7:** the service env tools on a live compose service, and whether service variables get
  duplicate `is_preview` rows.
- **Scheduler:** supercronic's behavior with an empty crontab.

Not production questions, but decisions: item 1's token scope, item 7's create path, and the
`--ref` admission conflict in item 9.

Versions read: orchestrator `origin/main` `9c8d5d8f84a32a9eb09bd7abb00a7ca4904fedd5`, Coolify tag
`v4.0.0-beta.473` (`57ea0764b8f0a491fd1d30bedc5cbe281744b36c`), infraops `2b8d0249`, Healthchecks
`v5.0.3`, supercronic `v0.2.51`.

## Tests and checks run

- No code changed, so I ran no test suite and no `make check`.
- Python `re` against the two log patterns with four candidate line names (item 13).
- `gh run view 42038653485 --log` on the public orchestrator repository, grepped for `DIGEST`,
  `REVISION:` and `SHA_TAG:` (item 13).
- Spot-checks of the research agents' cited lines: Healthchecks `models.py:436-475, 510-535` and
  `settings.py:271`, supercronic `cron/cron.go:68-84` and `main.go:32-55`; Coolify `ServicesController.php:25-40, 343-352, 494-527, 576-596, 970-1053`, `parsers.php:2002-2012, 2072-2080, 2207-2232, 2355-2462, 2650-2704`, `Service.php:1530-1602`, `StartService.php:14-40`, `GetContainersStatus.php:105-117`, `DeployController.php:520-532`, `EnvironmentVariable.php:77`; infraops `src/tools/services.ts:170-219, 265-269`, `src/utils/redaction.ts:13-14`. Each matched the agents' claims.

## Files changed

- `docs/superpowers/reports/lanes-inc-1-verify-report.md` (this report). Nothing else.

## Self-review

- Each answer cites a file and line at a named commit or tag. Where only docs or `master` were
  available (Docker docs, moby), I said so.
- I reported the conflicts in items 1, 7 and 9 without resolving them.
- Designs (items 8, 10, 14) are proposals with options; HQ decides.

## Concerns

- **Item 1 reshapes the design.** If the decision is one Coolify resource per lane, several
  answers here change with it: the quiet slot would become per lane (a redeploy stops only that
  lane), `SDS_LANES_IMAGE_TAG` becomes one variable per resource, and items 8, 11 and 14 follow.
  I didn't re-plan for that; it's the decision's to make.
- **Item 7 conflicts with Q11.** HQ can't run the create through `coolify_create_service` at this
  Coolify version. An infraops fix (base64 and an optional `type`) goes through the
  infraops-escalation lane, outside this repository.
- **Item 9 conflicts with deploy.md.** The `--ref` admission at the lanes' commit is a rollback,
  which `migrations_between` refuses by design (`deploy.md` line 77). The orchestrator half of an
  orchestrator rollback has no procedure in `deploy_orchestrator.py`.
- **The quiet-slot timeout alert needs a credential the ADR doesn't name (item 10).**
  `deploy_orchestrator.py` sends no alert today. Which alert-hub sender token it would use, and
  whether `sds-operator` can read it, isn't decided. The ADR's "no new credential" for the lane
  phases (line 181) is about Coolify; this would be an alert-hub one.
- **Standards gap.** The handoff's standards don't cover a container entrypoint's own logging
  (supercronic's lines). I recommended `-json` to meet Code Brain rule 3; HQ may want a rule.
- **Not verified:** go-crond's checksums and overlap behavior; the Docker docs page and moby are
  unversioned (`master`), not the host engine's version.

## Standards followed

This task changed no code, so each standard applies to what I evaluated and to what I did.

- **Code Brain rule 3 (structured JSON logs to stdout):** the scheduler recommendation runs
  supercronic with `-json`, so its own lines are JSON on stdout. The deploy phases' design keeps
  the program's existing one-JSON-object-per-run output.
- **Code Brain rule 4 (no silent swallow):** item 11 notes that the compose read must tell "no
  file" from "unreadable" rather than treat a `Refused` as an empty compose.
- **Code Brain rule 5 (never log secrets):** I read no credential or BWS value, made no
  Coolify, Healthchecks or BWS call, and quoted no value. Item 6 reports that the service env
  write responses echo a value and infraops's redaction misses it, so no secret goes through
  those tools.
- **Infra Brain BLOCK 2 and BLOCK 591-593 (no secrets or BWS tokens in tracked files):** this
  report holds no BWS UUID or token. Item 1's finding is that the
  rendered compose would carry each lane token in plain text inside Coolify, not in a tracked
  file.
- **Infra Brain BLOCK 3 (no `:latest`):** the scheduler is pinned to `v0.2.51` and its sha256
  digest. The lane image is referenced by its `sha-` tag and checked by digest.
- **Infra Brain BLOCK 5 (redeploy only after the GHCR push):** the lane phases start after
  `finish`, which reads the pushed digest from a completed `Release image` run, so this holds by
  the ADR's order. Item 13 keeps the lane digest read from the same completed run.
- **Infra Brain WARN 214 (`unless-stopped`):** Coolify's default restart mode is
  `unless-stopped` (`constants.php:18`); the tracked compose must also set it on every service.
- **Infra Brain WARN 570 (health check only on an exposed FQDN app):** item 12 recommends
  `HEALTHCHECK NONE`, which this rule allows for `sds-lanes`.
- **Infra Brain WARN 239 (application env tools fail on compose apps):** item 7 names the service
  env tools, which call the service routes, and notes rule 239 is about a different resource
  type.
