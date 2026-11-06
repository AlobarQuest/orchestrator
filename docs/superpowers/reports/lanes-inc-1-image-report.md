Outcome: done

# Build report: lanes-inc-1-image (worker W-34)

ADR-0059 increment 1, the first of HQ's two PRs: the lane image and everything that only builds or
reads it. This PR adds no lane phases, run files, `prepare` lag check or `.bws-secrets.toml` entry,
and makes no Coolify, BWS, Healthchecks, infraops or VPS call.

- **PR:** #624, draft, "ADR-0059 increment 1: the lane image and lane-run"
- **Head:** `8f99e735a7686765bdd6a998a4a758d021cbfc67` (the code). The commit that adds this report follows it on the branch and changes no other file.
- **Base:** `7510c832` (`origin/main` at the start, fetched first)
- **Branch:** `hq/lanes-inc-1-image`

## What I built

1. **The lane image**, `lanes/Dockerfile`, published as `ghcr.io/alobarquest/sds-lanes`. The ADR
   names no image, so I picked that name; it matches `SDS_LANES_IMAGE_TAG` and
   `sds-lanes-<lane>-state`. Both stages are `FROM ${ORCHESTRATOR_IMAGE}`, which the workflow
   sets to `ghcr.io/alobarquest/orchestrator@<digest this run pushed>`.
   - Downloaded tools, each fetched with `ADD --checksum=sha256:<digest>`, so BuildKit fails the
     build on a mismatch:

     | Tool | Version | Asset | Digest source |
     |---|---|---|---|
     | supercronic | `v0.2.51` | `supercronic-linux-amd64` | GitHub asset digest |
     | gh | `2.95.0` | `gh_2.95.0_linux_amd64.tar.gz` | GitHub asset digest |
     | uv | `0.10.9` | `uv-x86_64-unknown-linux-gnu.tar.gz` | GitHub asset digest |
     | node | `v26.4.0` | `node-v26.4.0-linux-x64.tar.gz` | nodejs.org `SHASUMS256.txt` |
     | bws | `2.0.0` | `bws-x86_64-unknown-linux-gnu-2.0.0.zip` | GitHub asset digest |

     gh, uv, node and bws are the versions the Mac runs today, so a lane's tools don't change when
     it moves. The archives are unpacked in a `tools` stage, so no archive is in a final layer.
   - Debian packages, each pinned `=version`: `git 1:2.47.3-0+deb13u1`, `curl 8.14.1-2+deb13u7`,
     `ca-certificates 20250419+deb13u1`, and `libatomic1 14.2.0-19`. Node's linux-x64 build links
     libatomic, and the slim base lacks it; the first local build's `node --version` failed
     without it.
   - The launchers, the four helpers (`sds-bws.sh`, `sds-deadman.sh`, `sds-install.sh`,
     `sds-token.sh`), `sds_scope.py` and `lane_run.py` go to `/app/scripts/`, root-owned, so a
     lane process can't rewrite its launcher. `lanes/lane-run` goes to `/usr/local/bin/lane-run`,
     and `lanes/` to `/app/lanes/`.
   - The image creates `/var/lib/sds/<lane>` for the eleven stateful lanes and for `placeholder`,
     owned by `orchestrator` with mode 0700. It does its installs as root and ends as
     `USER orchestrator`.
2. **The scheduler.** supercronic `v0.2.51`, as in item 1. `lanes/entrypoint.sh` ends with
   `exec supercronic -json "$crontab"`. `TZ=America/New_York` is an image `ENV`, and the base
   image carries tzdata. The build runs `supercronic -test` on every `/app/lanes/*/crontab`.
3. **`LANE_SCHEDULE_ENABLED`.** The entrypoint takes the lane as its one argument (the compose's
   `command:`), and refuses a missing or malformed name or a lane with no tracked crontab with
   exit 64. When the variable is exactly `true`, it runs `/app/lanes/<lane>/crontab`. Otherwise it
   writes a comment-only crontab with `mktemp`, checks it with `supercronic -json -no-reap -test`,
   and runs supercronic on it. It logs one JSON line naming the lane, whether it's scheduled, and
   the crontab.
   - **Design choice, one crontab per lane:** with one resource per lane (Amendment 1), each
     container must run its own lane's lines and no other's. So each lane's tracked crontab is
     `lanes/<lane>/crontab`, beside its compose, and the entrypoint selects it by the lane
     argument. That's a fixed argument in the compose, not a variable, so the placeholder still
     maps no variable but its two. A lane's increment adds its directory. No crontab line has to
     be parsed to pick a lane's jobs.
4. **`HEALTHCHECK NONE`** and `ENTRYPOINT ["/app/lanes/entrypoint.sh"]`. Setting `ENTRYPOINT`
   resets the inherited `CMD`; `docker image inspect` shows `Cmd` `null`.
5. **`lane-run`**, `scripts/lane_run.py`, run as `/app/.venv/bin/python -I` by the wrapper.
   - `lane-run <lane> [args...]` starts `scripts/run-<lane>.sh` beside it with exactly `PATH`
     (`/app/.venv/bin:/usr/local/bin:/usr/bin:/bin`) and a new empty `HOME` from
     `tempfile.TemporaryDirectory`, removed after the run so passes don't pile up caches in
     `/tmp`. It also passes `BWS_ACCESS_TOKEN`, `TZ`, `SDS_DEADMAN_PING_URL_UUID` and the ten
     state variables, each only when the container sets it. It exits with the launcher's code.
     The token reaches the child through the environment only, never through argv.
   - `lane-run --read <uuid>...` refuses a non-UUID argument, a missing or empty token, or a
     missing `bws`, each with exit 2. Under the same environment it runs
     `bws secret get <uuid> --color no` per UUID with stdin, stdout and stderr detached. It prints
     `<uuid> ok` or `<uuid> refused`, and for a refusal one stderr line with bws's exit status. It
     exits 0 only when every read is `ok`, and 1 otherwise.
6. **`Release image`.** A new "Compute the lane image's tags" step runs
   `compute_image_tags.py --image lanes`, which has a new `--image` option and takes the
   orchestrator's tag names under `sds-lanes`. "Refuse to overwrite an existing tag" also checks
   the two lane tags, before either build. After the orchestrator's verify step, "Build the lane
   image and push it (amd64)" builds `lanes/Dockerfile` from `orchestrator@<pushed digest>`,
   applies the three OCI labels, pushes both tags and captures the digest. "Check the lane image's
   pushed provenance" pulls the derivable tag back, compares its digest and checks the revision
   label. Its `env:` is exactly `LANE_DIGEST`, `LANE_REVISION` and `LANE_SHA_TAG`. Every lane-step
   variable carries a `LANE_` or `ORCHESTRATOR_` prefix.
   - In `scripts/deploy_orchestrator.py`, `pushed_digest()` and the new `pushed_lane_digest()`
     both call `logged_digest(log, run_id, sha, prefix, image)`. That function keeps the old
     refusals: more than one distinct digest, or a revision or `SHA_TAG` that isn't `sha`'s on
     that image. The patterns are `(?<![\w-])<prefix>NAME: `. The old `\b` would have let
     `LANES-DIGEST:` or `LANE-DIGEST:` count as the orchestrator's digest (the verify report's
     item 13 table).
7. **The placeholder's compose**, at **`lanes/placeholder/compose.yaml`**, beside
   `lanes/placeholder/crontab`, which has comments only. It has one service, `placeholder`, with:
   - `image: ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}`;
   - `command: ["placeholder"]`;
   - `environment:` with `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}` alone;
   - the named volume `sds-lanes-placeholder-state` at `/var/lib/sds/placeholder`;
   - `restart: unless-stopped`.

   It has no port, domain, label, token or `nocopy`.

The orchestrator `Dockerfile` and `docker-compose.yml` are byte-identical to the base
(`git diff --quiet 7510c832 -- Dockerfile docker-compose.yml`), so the orchestrator image's build
is unchanged. `scripts/lane_run.py` isn't in that image, because its `Dockerfile` copies `src/`
and not `scripts/`.

## Tests and checks

| Check | Result |
|---|---|
| `make check` (ruff, ruff format, pyright, full pytest against `orchestrator_test_hq_w_34`) | Green except `test_every_pattern_names_a_file`, which reads tracked files and failed only because the new files weren't committed yet. After the commit, `tests/architecture/test_layer_manifest.py` passed (3 tests). Full run: 13860 passed, 1 skipped. |
| `shellcheck lanes/entrypoint.sh lanes/lane-run` (`.shellcheckrc`, severity warning) | Clean. `make check` covers `entrypoint.sh` by its `*.sh` glob. `lane-run` has no `.sh` suffix, so I ran shellcheck on it by hand. |
| actionlint on `release-image.yml` | Clean. actionlint isn't installed here, so I ran it from the pinned container `rhysd/actionlint:1.7.7` (`-no-color`). The base file is clean too. |
| Security scanner, repository root, `--category security` | No BLOCK. Two WARNs, both on the base too: an uncovered `*-migration/` glob, and UUID `aaff45ca-…` in `scripts/install-rotation-worker-launchd.sh`. |
| PR #624 checks on `8f99e735` | All three passed: Quality (11m7s), Rollout transcription currency, and Runner consumer compatibility (run `42309915457`). |

The new tests are:

- `tests/scripts/test_lane_run.py` (18 tests). They run the real program as a subprocess, with a
  fake launcher and a fake `bws`. They cover:
  - the exact variable set;
  - each state variable passed only when set;
  - the new, empty and removed `HOME`;
  - unknown and path-traversal lane names;
  - `--read`'s output, which contains no value;
  - `bws`'s argv, which never holds the token;
  - each refusal;
  - that the state list equals every `*_STATE_DIR` name `src/` reads, apart from the activation
    sweep's and the tool installer's, which stay on the Mac.
- `tests/scripts/test_deploy_orchestrator.py` (5 new tests, one parametrized four ways). Both
  readers run on the real release log `37494595944`, with and without appended lane lines. The
  orchestrator reader returns the same digest either way. The lane reader refuses a run without
  lane lines, two distinct lane digests, another image's tag, and another commit. Look-alike names
  (`LANES-`, `LANE-`, `XLANE_`, `ORCHESTRATOR_`) count for neither reader.
- `tests/scripts/test_compute_image_tags.py` (2 new tests): the lane tags and the `--image` option.
- `tests/architecture/test_lane_image.py` (12 tests). They hold the Dockerfile to:
  - five `ADD --checksum` downloads, and no `curl` or `wget` in a `RUN`;
  - supercronic `v0.2.51`, version-pinned apt packages, and no `:latest`;
  - both stages `FROM ${ORCHESTRATOR_IMAGE}`;
  - `HEALTHCHECK NONE`, the entrypoint, no `CMD`, a final `USER orchestrator`, and `TZ`;
  - the twelve state directories;
  - the copied files and the build-time crontab test.

  They also hold the entrypoint's exact-`true` switch and the placeholder compose's exact shape.
- `tests/architecture/test_release_workflow.py` (2 new tests). The lane build follows the
  orchestrator's verify step and builds from its digest. The lane steps log only `LANE_`-prefixed
  names, and the overwrite refusal covers the lane tags.

**Mutation pass:** 13 mutations, all killed, with `__pycache__` cleared and the file restored from
the commit after each. They cover:

- `lane-run`: adding `LANE_SCHEDULE_ENABLED` to the list, not discarding bws's stdout, dropping the
  lane-name check, a fixed `HOME`, and a presence-only token check;
- the readers: reverting the boundary to `\b`, comparing against the orchestrator's tag, and
  dropping the `LANE_` prefix;
- the image files: removing `HEALTHCHECK NONE`, switching the entrypoint test to `!= "false"`, and
  defaulting the compose to `true`;
- the workflow: logging bare `DIGEST:`, and dropping the lane tags from the overwrite refusal.

## What I confirmed locally about supercronic and the image

I built the orchestrator image for `linux/amd64` from this tree, using a registry context shaped
from `~/Projects/security-standards` at the pinned revision. Then I built the lane image `FROM` it.
OrbStack ran amd64 under emulation. I confirmed each of the following:

- `docker image inspect`: `Healthcheck` `{"Test":["NONE"]}`, `User` `orchestrator`, `Entrypoint`
  `["/app/lanes/entrypoint.sh"]`, `Cmd` `null`, `TZ=America/New_York`. `date +%Z` prints `EST`.
- Each tool prints its pinned version: `git 2.47.3`, `curl 8.14.1`, `gh 2.95.0`, `uv 0.10.9`,
  `node v26.4.0`, `npm 11.17.0`, `bws 2.0.0`, supercronic `v0.2.51`. `id` is
  `uid=999(orchestrator)`, and `/usr/local/bin` has no dangling link. Node 26 ships no `corepack`,
  so the image links only `node`, `npm` and `npx`.
- **Comment-only crontab.** `supercronic -test` accepts it ("crontab is valid", exit 0).
  `supercronic -json` on it kept running: the container was `running` with health `none` after 8
  seconds, PID 1 was `supercronic -json /tmp/tmp.…`, and the file held only the comment.
  `docker stop` ended it at once with exit code 0, logging "waiting for jobs to finish" and then
  "exiting". An inexact value, `LANE_SCHEDULE_ENABLED=TRUE`, started unscheduled too, and was
  still running when `timeout 3` stopped it (exit 124). HQ's check on the live placeholder still
  stands; this is the same binary and file shape.
- **Named-volume ownership.** A new Docker named volume mounted at `/var/lib/sds/placeholder` came
  up as `drwx------ orchestrator orchestrator`, the image directory's owner and mode, as the
  verify report's item 3 predicts. This was the local Docker engine, not Coolify.
- **A scheduled job.** I mounted a probe lane with the crontab line
  `*/5 * * * * * * lane-run probe --flag` and a launcher that prints its user, arguments and
  variable names. I started it with `LANE_SCHEDULE_ENABLED=true`, a stand-in `BWS_ACCESS_TOKEN`,
  `OTHER_LANE_TOKEN` and `EXECUTION_WATCH_STATE_DIR`. Every 5 seconds the job ran as
  `orchestrator`, got `--flag`, and saw exactly `BWS_ACCESS_TOKEN EXECUTION_WATCH_STATE_DIR HOME
  PATH TZ` plus the `PWD` its shell sets. `OTHER_LANE_TOKEN` and `LANE_SCHEDULE_ENABLED` didn't
  reach it. supercronic logged each run as JSON and reported the launcher's exit status 3.
- **Refusals.** With no argument, the entrypoint exits 64 with a JSON usage line; it does the
  same for `../x`. An unknown lane exits 64 and names the missing crontab. `lane-run --read`
  without a token exits 2: "BWS_ACCESS_TOKEN is not set in this container; nothing can be read".
- The first `-test` call logged supercronic's plain-text format, so the entrypoint passes `-json`
  (and `-no-reap`, since that call isn't PID 1) to it as well. After that, every log line the
  container writes is JSON.

## Files changed

`git diff 7510c832 --stat` at the head (the report itself adds one more file):

```
 .github/workflows/release-image.yml         |  76 +++++++++-
 lanes/Dockerfile                            |  83 +++++++++++
 lanes/entrypoint.sh                         |  35 +++++
 lanes/lane-run                              |   4 +
 lanes/placeholder/compose.yaml              |  15 ++
 lanes/placeholder/crontab                   |   3 +
 layers.toml                                 |   4 +
 scripts/compute_image_tags.py               |  22 ++-
 scripts/deploy_orchestrator.py              |  48 ++++--
 scripts/lane_run.py                         | 142 ++++++++++++++++++
 tests/architecture/test_lane_image.py       | 155 +++++++++++++++++++
 tests/architecture/test_release_workflow.py |  38 +++++
 tests/scripts/test_compute_image_tags.py    |  23 +++
 tests/scripts/test_deploy_orchestrator.py   |  77 ++++++++++
 tests/scripts/test_lane_run.py              | 221 ++++++++++++++++++++++++++++
 15 files changed, 925 insertions(+), 21 deletions(-)
```

All new files are in the `runtime` layer of `layers.toml`.

## Self-review

I ran `/code-review` at medium on PR #624. Its findings and what I did with each:

- **Fixed:** nothing checked `STATE_VARIABLES` against the code. A test now holds it equal to
  every `*_STATE_DIR` name `src/` reads, except the two Mac-only lanes.
- **Kept, with a reason:** `--read` resolves `bws` on the container's own `PATH` (the image's
  `ENV PATH`, which holds every `LANE_PATH` directory), so a test can put a stand-in there. The
  code says so in a comment.
- **Kept, with a reason:** `lane-run` doesn't forward `SIGTERM`. Docker signals PID 1 only, and
  supercronic waits for its jobs rather than signalling them, as the local `docker stop` showed.
  A handler would turn a stop into `subprocess.run` killing the launcher.
- **Kept, with a reason:** an inexact `LANE_SCHEDULE_ENABLED` starts unscheduled, and the
  entrypoint's JSON line reports `"scheduled":false`. That's the ADR's rule: anything but `true`.
- **Kept, with a reason:** the Dockerfile tests read source text, as `test_container.py` does for
  the orchestrator `Dockerfile`, because the release build is the only build CI runs.
- **Reported as concerns:** the apt pins, the build order, and `pushed_lane_digest`'s caller (the
  following section).

A first `/code-review` call, given the base sha instead of the PR, reviewed the ADR text. I
dropped those findings because they are outside this diff. One of them still deserves HQ's eye
before the second PR: the deploy command defines exit codes 0 to 3 only, and the ADR's timeout
code 4 is new.

## Concerns

1. **The apt pins can break `Release image` after the orchestrator image is pushed.** Debian
   replaces a package version in the archive at a point release or a security update, so an
   `=version` install then fails with "version not found". The lane image builds after the
   orchestrator image is pushed, so that failure leaves the orchestrator tags pushed and the run
   red. `deploy_orchestrator.py` refuses a run that didn't succeed, and a re-run is refused by the
   overwrite guard unless `allow_overwrite=true`. So a stale pin blocks an orchestrator release
   until someone bumps the four `ARG`s. The same holds for a lane-only failure, such as a
   checksum mismatch. These are the options I see:
   - (a) Keep the exact pins, and bump them when a build fails (what this PR does, as the handoff
     asks).
   - (b) Pin through `snapshot.debian.org`.
   - (c) Leave the apt packages unpinned under the base image.

   This is HQ's call, and possibly Devon's. Apt packages are checked by the archive's signed index,
   not by a sha256 digest in this repository.
2. **`pushed_lane_digest()` has no production caller in this PR.** Only its tests call it. Its
   caller is the second PR's `prepare`, which records the lane digest. The handoff asks for the
   reader here, but the workspace rule says to name the caller, so it's named here.
3. **The lane image inherits `EXPOSE 8000`.** Dockerfile has no instruction that removes an
   inherited `EXPOSE`. It publishes nothing, the compose has no port, and nothing listens on 8000.
4. **Node `v26.4.0` is a Current release, not LTS.** I matched the Mac's version so moving a lane
   doesn't change its tools.
5. **Standards the handoff's section doesn't cover:** none that I found. The image name and the
   per-lane crontab layout are design choices within the ADR, recorded in item 3.

Teardown: I'll drop the test database `orchestrator_test_hq_w_34` and remove my local images
(`w34-orchestrator:local`, `w34-lanes:local`, `rhysd/actionlint:1.7.7`), probe containers, and
the `w34-ph-state` volume at session end. I didn't use or touch the main tree.

## Standards followed

- **Code Brain BLOCK rule 3 (structured JSON logs to stdout, not files).** supercronic runs with
  `-json` for its schedule and for the `-test` call. The entrypoint's own lines are single-line
  JSON on stdout. `lane-run` keeps the repository's existing output style: plain stdout lines
  for its result, and `lane-run: …` diagnostics on stderr, as `scripts/sds_scope.py` does. It
  writes no file.
- **Code Brain BLOCK rule 4 (never swallow an exception silently).** `lane-run` catches nothing.
  Discarding bws's output under `--read` is deliberate, with the justification in a comment: the
  output is the secret's value. Each failed read is reported, both as `refused` and as a stderr
  line with bws's exit status.
- **Code Brain BLOCK rule 5 (never log secrets, credentials, auth headers, tokens or request
  bodies).** `--read` prints `<uuid> ok` or `<uuid> refused` and never a value. bws's stdout and
  stderr go to `/dev/null`. The token travels only in the child's environment, never in argv. The
  tests assert that neither the stand-in secret nor the token appears in the output, and that
  bws's argv holds no token.
- **Code Brain WARN rule 6 (no bare `print()` in application code).** `lane-run` uses `print()`
  for its result lines and its stderr diagnostics. That follows the repository's existing CLI
  convention for `scripts/` programs: `scripts/sds_scope.py` prints its result with `print()`, and
  its errors with `print(..., file=sys.stderr)`.
- **Infra Brain BLOCK 3 (never `:latest`).** No image or tool uses `:latest`. The base is the
  orchestrator image by digest, each download is version-pinned and digest-checked, and each apt
  package is version-pinned. `test_no_tool_or_image_floats` and the existing workflow test hold
  it.
- **Infra Brain BLOCK 2, 591, 592 and 593 (no secrets, `.env` files or BWS tokens in tracked
  files; no inline bootstrap token).** No tracked file holds a secret, `.env` file or token. The
  compose maps no token. `lane-run` reads `BWS_ACCESS_TOKEN` from the container's environment at
  run time. The test values are plainly fake strings. The security scanner reports no BLOCK.
- **Infra Brain BLOCK 5 (a Coolify redeploy fires only after the GHCR push completes; read the
  lane image's digest from a completed `Release image` run).** The lane digest comes from the
  pushed image, `containerimage.digest` from `--push`. A later step reads it back from the
  registry, and that step's log line is the only one `pushed_lane_digest()` reads. The second PR's
  `prepare` will read it from a run that concluded `success`, as it does for the orchestrator.
  This PR triggers no build or deploy.
- **Infra Brain WARN 214 (`restart: unless-stopped` on every compose service).** The
  placeholder's one service sets it, and `test_the_placeholder_compose_maps_only_its_two_variables`
  pins it.
- **Infra Brain WARN 213 (a compose app's domain goes in Traefik labels).** The placeholder has
  no domain, so its compose has no Traefik labels and no port. The test pins the service's exact
  keys, which leaves no room for either.
