Outcome: done

# Report: the estate lander's lane files and launcher (ADR-0059 increment 5, step 3g-2)

Worker W-210. Branch `hq/lanes-inc-5-estate-landing`, base `origin/main` at `ea2905a9`. The PR is
open and ready, not merged. Nothing live was called: no BWS read of a real secret, no mint, no
GitHub App, GHCR, Coolify, change-manager, orchestrator, alert-hub or Healthchecks call, and no
`launchctl`.

## What changed

- `scripts/run-estate-landing.sh`: a container branch, modelled on #736. A set `BWS_ACCESS_TOKEN`
  means the lane's container. In that case the launcher does the following:
  - It skips activation.
  - It unsets every preset credential variable, including `ESTATE_LANDING_COOLIFY_TOKEN`.
  - It refuses an unset `LANDER_STATE_DIR` (exit 1).
  - It reads each secret with the lane's one token from the environment.
  - It derives the mint's repositories from `estate-landing repositories` and mints
    `ESTATE_LANDING_GITHUB_TOKEN` from the read-only App.
  - It unsets `BWS_ACCESS_TOKEN` and runs the lander, then exits with the lander's code.

  The Mac lines are unchanged, apart from activation being re-indented inside a branch.
- `src/estate_lander/cli.py`: a `repositories` subcommand. It prints the landing policy's
  deploying population, one repository per line, and exits 0. It fails closed with exit 2 and
  nothing on stdout for a missing credential, an unreadable policy, or an empty deploying
  population. `main` dispatches it, and `run(argv)` is unchanged.
- `lanes/estate-landing/compose.yaml` and `lanes/estate-landing/crontab` (new).
- `.bws-secrets.toml`: `"estate-landing"` added to the `lanes` of exactly the five rows
  (`221a48d5`, `314f276d`, `b415d03b`, `f9f59233`, `314a966c`). The `f9f59233` row gains a
  `lanes` key, and the comments name the new reader.
- Tests:
  - `tests/scripts/test_run_estate_landing.py` (new; registered in `layers.toml`).
  - `tests/estate_lander/test_repositories.py` (new; covered by `tests/estate_lander/**`).
  - `tests/architecture/test_landing_edges.py`: the estate lander's change-manager column gains
    `GET /api/landing-policy`, its predicate is the union of its two change-manager clients, and
    its bearers gain the read App key.
  - `tests/scripts/test_deploy_lanes.py`: `estate-landing` added to `placeholders`.
- `layers.toml`: the new launcher test file.
- Docs: `docs/operations/deploy.md` gains the estate lander's lane paragraph and its remaining
  cut-over steps, after the inert lander's. `docs/operations/credentials.md` gains the holder
  paragraph after the inert lander's.
- `scripts/lane_run.py`: **unchanged.** The only non-secret variable the container needs is
  `LANDER_STATE_DIR`, which `STATE_VARIABLES` already passes. The Coolify URL keeps its default, so
  nothing passes `ESTATE_LANDING_COOLIFY_URL`.
  `test_every_compose_variable_lane_run_must_pass_is_in_its_list` holds this.

## Each secret and how the container reads it

Every read is `sds_bws_value <uuid>` with one argument, so the lane's token comes from the
environment and never appears in argv. Each secret is always read in the container; a preset
variable is discarded.

| Secret | When | Failure |
|---|---|---|
| change-manager READ `314f276d-…` → `ESTATE_LANDING_CHANGE_MANAGER_TOKEN` | every pass, first (the repository list needs it) | FATAL, exit 1 |
| read App key `314a966c-…` | every pass, piped straight into `sds-github-mint` | FATAL, exit 1 (names the key read or the mint) |
| orchestrator SYSTEM `221a48d5-…` → `ESTATE_LANDING_ORCHESTRATOR_TOKEN` | every pass | FATAL, exit 1 |
| `sds-bridge` alert sender `b415d03b-…` → `SDS_BRIDGE_ALERT_TOKEN` | `--submit` only | FATAL, exit 1 |
| Coolify token `f9f59233-…` → `ESTATE_LANDING_COOLIFY_TOKEN` | `--submit` only | not fatal: logs `[estate-landing] the Coolify token could not be read (the lane's token): re-points are refused`, and `sds_bws_value`'s own stderr shows too. No `|| true`: without `set -e` none is needed, so nothing else is hidden. |

On a bare pass, the alert sender and Coolify token are absent from the lander's environment
(tested). `ESTATE_LANDING_COOLIFY_UUID` isn't passed by `lane-run`, so the container always reads
the default id.

## The read token and its permissions

`sds-github-mint --app-id 5684758`, with one `--repository` for each line
`estate-landing repositories` prints. The subcommand runs without the lane's token, and only the
change-manager credential reaches it. The mint also runs without the lane's token. The key goes
from BWS to the mint's stdin in its stored form.

The permissions are split by pass, because each pass uses different reads:

- Every pass: `metadata=read` and `pull_requests=read`. `_stranded` uses
  `GitHubReader.open_pull_requests` for each `revert_opened` watch row.
- `--submit` adds `contents=read`. `Production.at_or_after` uses `GitHubReader.compare_status`, and
  `production` is `None` on a bare pass.

`test_the_estate_landers_github_reads_are_the_listing_and_the_compare` already pins that these two
methods are the only ones the CLI calls on the reader. All three permissions are in the App's fixed
D-18 list; none was added. The mint test pins `contents` on `--submit` for a specific reason. A
compare that fails reads as "not at or after", and `_restore` (`revert_pass.py` ~611-617) then
starts the applications' deploy again. So a token without `contents` would redeploy production on
every submitting pass that restores a landing.

**Repositories:** the landing policy's deploying population (`LandingPolicy.deploying`, the
document's top-level `repositories`). change-manager's policy approves this lane's records only for
repositories in that set (`app/deploy_policy.py` ~689, `repository_not_in_policy`). So a landing
of a policy-approved record, and therefore its watch row, is in the list. This mirrors #736's
`inert-landing repositories`, and it uses the same READ credential the lane already holds.

## The crontab and why

`16 * * * * lane-run estate-landing --submit`. The Mac job's minute, 15, belongs to
execution-watch (`*/15`). Minutes 14 and 16 are the two nearest free ones. I took 16, the later
one, as every moved lane before it took a later minute than its Mac job (30→31, 35→37, 20→25,
45→47, 50→55). `test_no_two_lanes_start_a_pass_in_the_same_minute` checks it against every lane's
crontab. The quiet-slot rule in deploy.md (outside 02:00-06:00 New York, and the check not
`started`) doesn't depend on the minute. The line carries `--submit`, as the plist's
`ProgramArguments` do. The flag is in the crontab, so HQ cycles that run a bare
`lane-run estate-landing` don't fire a scheduled pass; a bare run asks for nothing, though it pings
the real check.

## The state volume

`sds-lanes-estate-landing-state` at `/var/lib/sds/estate-landing`, set as `LANDER_STATE_DIR`.
`lanes/Dockerfile` already pre-creates `/var/lib/sds/estate-landing`, owned by `orchestrator`, mode
0700, so no Dockerfile change was needed.

The files, read from the code: `held-since.json`, `alerts.json` and `revert-alerts.json`. The estate
CLI calls `sync_revert_alerts("estate-landing", …)`, and `lander.held_alerts` keeps its memory in
`REVERT_ALERTS_FILE`. ADR-0059's lane-state row (line 339) names only the first two; see the
concerns section.

## The Mac path, and how I proved it unchanged

- `git diff -w origin/main -- scripts/run-estate-landing.sh` removes exactly two lines, both
  comments (the GitHub-token paragraph, reworded to name the container). No code line was removed
  or changed. Activation is re-indented inside `if [ -z "${BWS_ACCESS_TOKEN:-}" ]`.
- The container block ends in `exit "$rc"` before the first Mac line.
  `test_the_keychain_and_gh_are_reached_only_after_the_container_block` pins that every Keychain
  and `gh` call comes after it.
- No launcher test existed for this script. The only existing test that reads it,
  `test_landing_edges.py`'s `test_each_launcher_fetches_exactly_its_bearers`, gained the read App
  key, as #736's did for the inert lander. I added `_mac` tests for the bare and `--submit` passes:
  - The identities: HC key broad, READ broad, SYSTEM narrow, alert and Coolify narrow.
  - `gh auth token` is called once, and nothing is minted or derived.
  - The narrow identity stays exported to the lander.
  - Preset overrides are still honoured.
  - An unreadable Coolify token logs the old line and leaves the variable empty.
  - The exit codes pass through.
- `test_the_coolify_url_is_the_tailnet_default` pins `DEFAULT_COOLIFY_URL ==
  "http://100.92.138.19:8000"`. `src/estate_clients/confined.py` isn't touched.

## Tests and checks run

- Targeted: `tests/scripts/test_run_estate_landing.py`, `tests/estate_lander`,
  `tests/architecture/test_landing_edges.py`, `tests/scripts/test_deploy_lanes.py`,
  `tests/scripts/test_run_inert_landing.py` and `tests/architecture/test_layer_manifest.py`: 472
  passed. The first layer-manifest run failed only because the new file was untracked; it passed
  after the commit.
- Mutation check of the new guards (9 mutations, each restored, `__pycache__` cleared): 9 killed, 0
  survived. The mutations were:
  - dropping `contents` on `--submit`;
  - minting `contents` on every pass;
  - keeping `BWS_ACCESS_TOKEN` for the lander;
  - not unsetting a preset Coolify token;
  - making the Coolify read fatal;
  - dropping the `LANDER_STATE_DIR` check;
  - allowing an empty deploying population;
  - printing the inert population too;
  - deriving the list with the lane's token.
- `make check` on database `orchestrator_test_hq_w_210`: see "make check result" below.

## Report-don't-fix findings

### GHCR with an App installation token

GitHub's documentation, fetched 2026-11-17, says the following:

- "Working with the Container registry"
  (`docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry`):
  "GitHub Packages only supports authentication using a personal access token (classic)." It
  doesn't mention GitHub App installation tokens. It also says "You can also access public
  container images anonymously."
- "About permissions for GitHub Packages"
  (`docs.github.com/en/packages/learn-github-packages/about-permissions-for-github-packages`):
  "to pull a package, you must authenticate with a personal access token or `GITHUB_TOKEN`", and
  "in the Container registry, public packages allow anonymous access and can be pulled without
  authentication." It doesn't mention App tokens either.

So the documentation gives no support for an installation token of an App without a Packages
permission getting a `pull` token for a **private** image. The answer for that case is "not
supported per the documentation". Whether `ghcr.io/token` happens to honour one is **unverified**.
Two read-only probes would settle it:

1. The anonymous exchange from the lane container:
   `curl -s -o /dev/null -w '%{http_code}' 'https://ghcr.io/token?scope=repository:alobarquest/<image>:pull&service=ghcr.io'`
   for each deploying repository's image. Today those are `brain` and `change-manager`, as the
   policy declares them, and the image names come from Coolify.
2. An in-process `Registry(token).digest(image, tag)` with a token minted in-process, printing only
   the outcome type. That is a live mint, so it is HQ's or an interactive session's act, not mine.

**Which images are public:** D-23 says `sds-lanes` is public and `orchestrator` private. The
deploying population's images, `brain` and `change-manager`, have no recorded visibility that I
could find, so they're **unverified**; probe 1 answers it. For a public image, the documentation
says an anonymous exchange serves a pull token. The registry client, however, always sends basic
auth `alobarquest:<token>`. Whether GHCR answers a public image's exchange when the credential
can't read packages is also unverified, and probe 2 answers that.

**What fails, and how loudly, if the minted token can't pull.** Only on `--submit`, and only when
the watch holds a bad landing. In that case `Registry._token` raises `RegistryUnreadable`, which
fails as follows:

- Re-point (`revert_pass.py` 339): the outcome is `repoint-refused`, the landing goes into
  `unpinned`, and the alert `landing-not-pointed-back:<repo>:<pr>` raises. Production stays on the
  bad build, the revert isn't opened by that path, and the pass exits 3.
- Restore (582): the outcome is `error`, "production stays pinned: GHCR issued no pull token (HTTP
  …)", and `landing-production-pinned` stays open.

It's loud, but it surfaces only at the moment the lane is needed most, and no pass before that
exercises it. That is concern 1.

### Proving reach before the schedule turns on

A bare pass with no watch rows uses the minted GitHub token for nothing. `_stranded` lists pull
requests only for `revert_opened` rows, `production` is `None`, so neither the registry nor the
compare is reached, and `GitHubReader`/`Registry` construction makes no request. Whether the
watch has such rows can't be known in advance: it does only while a revert is open.

So the paired bare pass proves the following:

- The lane's token reads the three every-pass secrets.
- The landing policy is readable with the READ bearer.
- The read mint succeeds over the deploying population, so the App is installed on each repository
  and they share one owner.
- The orchestrator answers the SYSTEM bearer, and change-manager's records read.
- The report matches the Mac's.

It doesn't prove `contents`, the Coolify token, Coolify reach, or GHCR.

Read-only proofs from the lane container that need no `--submit` pass and no new code path or flag:

- **Coolify network reach:** `curl -fsS http://100.92.138.19:8000/api/health`, unauthenticated,
  as G-73 ran it from the revision watcher's container. `curl` is in the image, because the
  dead-man helper uses it.
- **GHCR reach and image visibility:** probe 1, the anonymous exchange.
- **The Coolify token's and the minted token's authority:** there is none. Each needs the
  credential in a request, and the only code that sends it is the `--submit` re-point and restore
  path. `lane-run --read` proves only that the secret is readable.

I wrote these into deploy.md's cut-over step 7.

### The paired pass beside the hourly Mac job

The comparison runs are bare runs on both hosts, so neither asks for a landing. The Mac's
scheduled job still submits at minute 15. The pair is run in the same minute, away from :15
(after that pass has finished, and before :15 of the next hour), and outside 02:00-06:00 New York,
where ordinary bumps are asked about. It then compares only what a bare report shows. The
container's schedule stays `false` until the Mac job is booted out and disabled (ADR-0059
Amendment 6), so at most one host submits in any hour. Only the Mac submits until the bootout, and
only the container submits after the schedule redeploy. The bootout happens between two :15 passes,
with `launchctl print` showing no running PID. The lane is moved outside the landing window
(ADR-0059 increment 5), so the switch can't fall inside the window's four passes either.

### Callers of the orchestrator SYSTEM credential the lane would join

I checked each caller in this repository by grep for `221a48d5`:

- `run-work-carrier.sh` and `run-rotation-decomposer.sh`, already in their lane containers.
- `run-inert-landing.sh`, whose container lands with #736 and isn't cut over yet.
- `run-activation-sweep.sh`, `run-bump-driver.sh`, `run-estate-landing.sh`, and
  `run-tool-installer.sh`, on the Mac.
- `scripts/exit_probe.py` and `scripts/deploy_orchestrator.py`.

The three callers in other repositories that #736's report cites from `.cred-consumers.toml`'s
`orchestrator-system-bearer` entry are unchanged (I didn't re-verify them). Those are infraops'
`drift-audit.sh`, intent-packages' `factory` CLI, and the restart-recovery kit's `credentials.py`.
The estate lane would be the fourth container holder.

**Does the lane need SYSTEM?** On today's routes, yes, for every pass:

- `GET landing-watch` and `GET landing-unknown-outcomes` (`authorize_actor`, SYSTEM only) run on
  the bare pass too.
- `POST estate-pr-merge`, `estate-pr-branch-update`, `held-pr-close`, `landing-revert`,
  `landing-repoint`, `landing-pinned`, and `landing-restored` all go through `lane_act` and
  `revert_act`'s `authorize_actor`.
- `_Members` reads `GET /api/v1/members` with key id `orchestrator-system`.

`GET estate-pr-merge-admission` accepts any authenticated actor. A narrower role would be a grant,
so I don't propose one here.

### The kill switch `ORCHESTRATOR_ESTATE_LANDING_ENABLED`

Nothing in the container reads it. Outside `src/orchestrator/`, a grep of `src/`, `scripts/`, and
`lanes/` finds it only in `install-estate-landing-launchd.sh`'s comment (line 10) and echo text
(line 40). `src/orchestrator/config.py` reads it, and the estate routes consult it. It stays an
orchestrator variable and restart (ADR-0059 392-398). It stops the container's acts exactly as it
stops the Mac's, because both act only through those routes. The watch's Coolify steps are
different: the lander takes them itself, and only after the orchestrator records
`landing-repoint` or `landing-pinned`, which the switch also bounds.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** nothing new logs to a file.
  The launcher's lines go to stderr as the existing lanes' do, and the lander's report output is
  unchanged. The launcher's plain-text FATAL lines follow the shell launchers' existing convention.
  JSON logging for shell launchers isn't covered by the standard as quoted; see the concerns.
- **Code Brain rule 4 (never silently swallow an exception):** every BWS read and the mint check
  their result and exit with a named FATAL line. The one non-fatal read, the Coolify token, logs
  its line, and `sds_bws_value`'s stderr isn't redirected. The container block has no `|| true`.
  In Python, `repositories()` turns `LandingPolicyError` into a printed `[UNUSABLE]` reason and
  exit 2.
- **Code Brain rule 5 (never log secrets):** no secret value, lane token, minted token, or GHCR
  token is printed or passed in argv. `test_each_pass_reads_its_secrets_with_the_lanes_one_token`
  checks the output and `test_no_token_reaches_an_argument` checks every argv. The key goes to the
  mint by pipe. The FATAL lines name ids and exit codes only.
- **Infra Brain 212 (HTTPS for a public FQDN):** the lane has no FQDN. Coolify is reached only over
  the tailnet address, which `confined.py`'s guard enforces unchanged.
- **Infra Brain 591, 592 (no BWS token in tracked files or history):** no token was written. Only
  ids appear, and the write guard raised nothing.
- **Infra Brain 593 (the bootstrap token never inline):** the compose maps `BWS_ACCESS_TOKEN` from
  `${ESTATE_LANDING_BWS_ACCESS_TOKEN}`, the resource variable, only. The compose test pins the
  whole environment.
- **Infra Brain 1032 (an exposed credential rotates):** no credential was surfaced to a log or
  transcript, and the session read no secret.
- **Rule 2 (no secrets or `.env` committed):** none.
- **Rule 3 (no `:latest`):** the image is `sds-lanes:${SDS_LANES_IMAGE_TAG}`.
  `deploy.add_refusals` passes, and it refuses a missing or `latest` tag.
- **Rule 4 (no Coolify scheduled task beside an in-container scheduler):** the schedule is the
  container's crontab, run by the entrypoint only when `LANE_SCHEDULE_ENABLED` is `true`. No
  Coolify task was created.

## Self-review

- The design choice I'd most want reviewed is deriving the repositories from the landing policy's
  deploying population rather than from the watch rows. The watch is the exact set the reader
  touches. It's also empty most of the time, though, which would leave nothing to mint while
  `run()` refuses an empty GitHub token. The policy is the set change-manager approves estate
  records in, read with a credential the lane already holds, and it mirrors #736.
- The permission split by pass adds one `case` to the mint. I kept it so a bare pass's token is
  exactly as narrow as its reads, and the mint test pins both forms.
- `main()` now calls `run(sys.argv[1:])` instead of `run()`. Argparse reads `sys.argv[1:]` when
  given `None`, so the behaviour is the same, and a test pins both forms.

## Concerns and follow-ups

1. **GHCR pull with the minted token is unsupported per GitHub's documentation for private images,
   and is unverified either way.** If it fails, the container can't point production back after a
   bad landing. That is loud (`landing-not-pointed-back` or `landing-production-pinned`), but it
   happens at the worst moment, and no pass before then exercises it. HQ needs to run probe 1
   (anonymous, read-only) before the cut-over turns the schedule on. If an image is private, the
   registry client's authentication, or that image's visibility, becomes a decision for Devon. The
   handoff says not to change the client.
2. **The lander gains a read of `GET /api/landing-policy`.** It's the same host and the same
   READ-scoped credential, the route the inert lander, bump proposer, and membership check already
   read with this bearer. I don't read it as a new network path, identity, or scope, but it widens
   the estate lander's outbound table by one route. `test_landing_edges.py` records it, and HQ may
   want to confirm.
3. **A record a person approves outside the policy's deploying population is outside the minted
   token.** Its watch row's listing is held as unreadable, and its compare fails to "not at or
   after". That compare failure starts its applications' deploy again on each submitting pass that
   restores it. On the Mac, `gh auth token` reaches any repository, so this is a container-only
   difference. I know of no such record. The docstring of `repositories()` states it.
4. **ADR-0059's lane-state row for `estate-landing` (line 339) names `held-since.json` and
   `alerts.json` only.** The estate lane also writes `revert-alerts.json`
   (`sync_revert_alerts("estate-landing", …)`). The compose comment and deploy.md's cut-over copy
   step name all three. The ADR row needs an amendment, which is outside my fence.
5. **Standards gap:** the quoted Code Brain rule 3 doesn't say whether shell launchers' stderr lines
   must be JSON. I followed the existing launchers.
6. The handoff's fact "the change-manager READ credential … every pass" and the other facts
   matched the source.

## Cut-over steps that remain

These are in deploy.md under the estate lander's paragraph:

1. The orchestrator serving this release; then HQ creates `sds-lane-estate-landing` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `estate-landing-ping-url`.
3. The lane's registry rows in two changes (the token's, then the ping URL's), plus a
   `runtime-fetch` reader for `scripts/run-estate-landing.sh` on the read App key's row.
4. **The lane account's grants on the five rows and its ping-URL secret:**
   - `314f276d-…` change-manager READ, today read by the broad identity; a direct grant is Devon's
     act at the identity gate.
   - `221a48d5-…` SYSTEM, `b415d03b-…` alert sender, and `f9f59233-…` Coolify token, in
     `SDS Operator`.
   - `314a966c-…` read App key, in `SDS / Lane Apps`.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write.
6. A served release whose compose names the ping-URL secret's id, with `estate-landing` removed
   from `placeholders`.
7. `--add`; `lane-run --read` over all six secrets; a bare `lane-run estate-landing`. Then the
   read-only reach probes: Coolify `/api/health`, and the anonymous GHCR exchange for each
   deploying image.
8. Cut-over steps 2 onward, outside 02:00-06:00 New York:
   1. Copy the three state files into the volume.
   2. Run the paired bare runs away from :15.
   3. Boot out and disable the Mac job.
   4. Set the schedule to `true`.
