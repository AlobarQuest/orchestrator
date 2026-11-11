Outcome: done

# Report: the revision watcher's lane files and launcher (ADR-0059 increment 3, step 3e-3)

Worker W-82. Branch `hq/lanes-inc-3-revision-watcher`, based on `origin/main` at `8baf0950`. The
handoff's stop point is a ready pull request, not merged. I made no production calls: no live
mint, no reads or writes of a real BWS secret, no GitHub App call, and no Coolify, VM, VPS, App
Brain, alert-hub, change-manager, Healthchecks or `launchctl` call.

## What I built

This change follows the deploy watcher (#653, `b0e25764`). The state volume follows
`lanes/execution-watch/compose.yaml`, and the scope derivation follows the landing ledger.

### Files changed

- `lanes/revision-watcher/compose.yaml` (new): one `revision-watcher` service. It has the
  following settings:
  - `command: ["revision-watcher"]`.
  - `BWS_ACCESS_TOKEN: ${REVISION_WATCHER_BWS_ACCESS_TOKEN}`.
  - `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}`.
  - `REVISION_WATCHER_STATE_DIR: /var/lib/sds/revision-watcher`, on the named volume
    `sds-lanes-revision-watcher-state`.
  - An empty `SDS_DEADMAN_PING_URL_UUID: ""`, explained in the header comment as #653 did.
  - `restart: unless-stopped`.

  It has no ports, networks or labels.
- `lanes/revision-watcher/crontab` (new): `40 * * * * lane-run revision-watcher`.
- `scripts/run-revision-watcher.sh`: adds the container branch (details in the following
  sections). On the Mac, it now reads the narrow identity from its Keychain item directly, with a
  `BWS_ACCESS_TOKEN_SDS` override, so a set `BWS_ACCESS_TOKEN` means the container (see
  Concerns).
- `scripts/sds_scope.py`: a new `apps` population.
- `src/sds_members/populations.py`: `app_repositories`. It returns the repositories that
  `app_subjects` names, each once by casefolded name, in the record's order.
- `.bws-secrets.toml`: adds `"revision-watcher"` to the `lanes` of the five rows: the observer
  bearer, `prod-coolify-api-token`, `APP_BRAIN_READ_KEY`, `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` and
  `lanes-read-app-private-key`. The platform token and App Brain key rows had no `lanes` field, so
  I added one. I also updated the comments that said only the broad identity reads those two.
- `docs/operations/deploy.md`: "Registered lanes" names the revision watcher and its remaining
  cut-over steps. "A lane's GitHub token" names its mint, derivation and permissions.
- `tests/scripts/test_run_revision_watcher.py`: rewritten, now 34 tests.
- `tests/scripts/test_sds_scope.py`: 5 new tests.
- `tests/scripts/test_deploy_lanes.py`: `placeholders = {"deploy-watcher", "revision-watcher"}`.
- `tests/architecture/test_runtime_edges.py`: `SDS_SCOPE_BORROWS` now lists `app_repositories`.
  This guard pins what `sds_scope` imports, and the new name is a population function from the
  same module.

I made no changes to `lanes/Dockerfile`, `pyproject.toml`, `layers.toml` (`lanes/**` already
covers the new files), `scripts/lane_run.py` (`REVISION_WATCHER_STATE_DIR` is already in
`STATE_VARIABLES`) or `scripts/deploy_orchestrator.py` (the lane is already in `LANE_CHECKS` and
`STATEFUL_LANES`).

### `.cred-consumers.toml`: no change

The handoff asks for the read-App key's `runtime-fetch` reader only "if #644's ledger reader is the
pattern." It isn't the pattern for this step:

- #644 was the ledger's cut-over step 1, made after its lane account held the grant.
- The registry's own comment (`.cred-consumers.toml`, before `lanes-read-app-private-key`) says
  "The landing-ledger lane's account holds a read grant … so that row lists the lane's reader."
- #653 deferred the deploy watcher's reader row to its cut-over step 3.

I followed #653. The row is cut-over step 3 in `deploy.md`.

## The mint: exact arguments, permissions, and repositories

In the container, once per pass, the launcher runs the following:

```sh
sds_bws_value "$READ_APP_KEY_UUID" \
  | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id 5684758 --repository <owner/name>... \
      --permission metadata=read --permission contents=read
```

- **Permissions.** `src/revision_watcher/github.py` makes three GET calls:
  `/repos/{r}/branches/{b}`, `/repos/{r}/commits/{sha}` and `/repos/{r}/compare/{base}...{head}`.
  All three are `contents` reads, plus the `metadata` that GitHub grants with every token. The mint
  requires `metadata` by name (`_check_request`). The App holds both permissions, so nothing needs
  widening.
- **Repositories.** The launcher runs `python -m scripts.sds_scope apps` with
  `SDS_SCOPE_ORCHESTRATOR_TOKEN` set to the observer bearer and under `env -u BWS_ACCESS_TOKEN`.
  It prints the repositories of the live members' `apps` in the member record, each once. That's
  the same record and the same population (`app_subjects`) that `load_subjects` reads for the
  watcher's subjects. `test_apps_is_the_repositories_the_revision_watchers_subjects_name` holds
  the two equal, and no owner name or repository list appears in code.
- **Why an `sds_scope` mode and not a `revision-watcher repositories` subcommand.**
  `revision_watcher.cli` has one Typer command and no callback, so it runs bare as
  `revision-watcher [--dry-run]`. A second command would make Typer require a subcommand name,
  which breaks the plist, the crontab and the launcher's `"$@"`. The ledger already derives
  through `sds_scope`.
- **A failed derivation.** If `sds_scope` exits 3, or exits 0 with an empty list, the launcher
  exits 3 before it reads the App key and mints nothing. Any other non-zero code passes through
  unchanged. The Healthchecks check gets `/fail`.
- **A failed or empty mint.** The launcher exits 1 before the watcher runs. The container never
  asks `gh`, and a preset `REVISION_WATCHER_GITHUB_TOKEN` is replaced by the minted token.

### Order in the container

1. Activation is skipped.
2. The dead-man switch is armed.
3. The launcher reads the observer bearer, the platform token, the App Brain key and, on acting
   passes only, the alert sender, all with the lane's one token.
4. It derives the repositories.
5. It reads the App key and mints the GitHub token.
6. It runs `unset BWS_ACCESS_TOKEN`.
7. It runs `revision-watcher "$@"` and folds the exit code as before.

## The crontab minute

The crontab line is `40 * * * *`, hourly, as the Mac's `StartInterval` of 3600 runs it. Minute 40
is clear of every other lane's start:

- `:00`, `:15`, `:30` and `:45` (`execution-watch`, `*/15`).
- `:20` (the deploy watcher).
- `07:30` (the landing ledger).

`test_no_two_lanes_start_a_pass_in_the_same_minute` checks this against every `lanes/*/crontab`.
The watcher has no scheduled-pass flag, so the line passes no flags, as the plist does.

## The dead man's switch with no ping URL

`SDS_DEADMAN_PING_URL_UUID` is empty, so `sds-deadman.sh` falls back to the Mac's lookup by check
name. That lookup needs the broad Keychain identity, which the container doesn't have. Each pass
logs `no SDS_DEADMAN_PING_URL_UUID and no broad BWS identity — alerting disabled this run`, pings
nothing, and still runs. `test_without_a_ping_url_secret_a_pass_pings_nothing` pins this. The
schedule stays off (`LANE_SCHEDULE_ENABLED` defaults to `false`) until the cut-over turns it on.

## Tests and checks

- `make check`, with the database `orchestrator_test_hq_w_82` and the fixture
  `SECURITY_STANDARDS_DIR`: **14,305 passed, 1 skipped, exit 0**. That's the final commit's run.
- My first run failed one test, `test_sds_scope_reads_the_members_and_nothing_else`, on the new
  import. I updated the pin as described earlier.
- `shellcheck scripts/run-revision-watcher.sh`: clean.
- Security scanner (`security_scan.cli . --category security`): 0 BLOCK, 2 WARN, 1 INFO.

The new tests cover the following:

- The container branch skips activation.
- The mint's exact arguments and repositories. The key reaches the mint's stdin undecoded, and the
  lane token is absent from the mint, the derivation and the watcher.
- Every BWS read uses the lane's one token, in a fixed order, and `gh` is never asked.
- The alert token is read on acting passes only, on both hosts. An unreadable alert token exits 1
  before the derivation.
- An unreadable bearer, platform token or estate key exits 1 before the derivation.
- On a derivation failure: exit 3 with no mint (for exit 3 and for an empty list with exit 0), and
  127 passes through.
- A failed, empty or keyless mint exits 1 without running the watcher.
- Exit-code folding (0, 1, 2, 3, and 127 → 1) on both hosts.
- The Mac path: two identities, the `gh` login, no mint, and a preset token kept.
- Compose parsing: the exact service, including the volume and the state variable,
  `add_refusals` empty, and the volume declared.
- The state variable is in `lane_run.STATE_VARIABLES`.
- The crontab line, and the plist's `StartInterval`.
- The `.bws-secrets.toml` `lanes` set equals the five UUIDs, and each appears in the launcher.
- `sds_scope apps`: order and dedupe, retired members excluded, equality with `load_subjects`, the
  output passing the mint's `_check_request`, an empty population exiting 3, and casefolded
  dedupe.

## Self-review

I ran `/code-review` at medium effort on `origin/main...HEAD`. It reported five findings:

1. **A set `BWS_ACCESS_TOKEN` on the Mac now selects the container branch.** This is by design and
   matches the ledger and deploy watcher (see Concerns). No change.
2. **The container's `_narrow_secret` and `_broad_secret` are the same function.** This is
   deliberate, so each call site names the Mac identity it would use, as the deploy watcher's
   `_change_manager_secret` and `_observer_secret` do. No change.
3. **`app_repositories` deduped case-sensitively, but the mint refuses a repository named twice
   under casefolding,** so every pass would exit 1. **Fixed** in commit 2, with a test.
4. **The empty ping-URL placeholder.** This is intentional, and the handoff asks for it. No
   change.
5. **A `--dry-run` in the container still mints.** This is intentional: the cut-over's dry run is
   the proof that the mint path works. No change.

## Concerns and follow-ups

- **The Mac's ambient-token meaning changed.** Before this change, an operator shell that exported
  `BWS_ACCESS_TOKEN` gave the launcher its narrow identity through `sds-token.sh`. It now means the
  container. The launchd job sets no `BWS_ACCESS_TOKEN`, so the scheduled path behaves the same:
  it reads the same Keychain item (`Claude`/`BWS_ACCESS_TOKEN_SDS`). The ledger and deploy watcher
  made the same change, and `BWS_ACCESS_TOKEN_SDS` is the named override.
- **Installation coverage can't be checked offline.** If installation `180257215` doesn't cover
  every repository the member record's apps name, the mint refuses and every pass exits 1. I can't
  check that without a live call. It's part of the cut-over `--dry-run` proof (deploy.md step 7).
- **The scope ignores `ORCHESTRATOR_API_URL`.** `sds_scope` reads the record at its default URL
  (`https://sds.alobar.net`), while the watcher honors an `ORCHESTRATOR_API_URL` override. Under an
  override, the mint set and the subjects could come from different records. The ledger has the
  same property. Nothing sets the override in the container.
- **The lane account will read a write-capable token.** Decision 2 grants the lane account
  `prod-coolify-api-token`, which can write environment variables (its manifest comment, measured
  2026-11-05). The watcher uses it only to list applications. The grant is already decided, and
  this change makes none, but the lane account's reach is wider than its use.
- **The platform URL uses HTTP (Infra Brain rule 212).** `REVISION_WATCHER_PLATFORM_URL` defaults
  to `http://coolify-1.devonwatkins.com`, unchanged, as the handoff says. Moving it to HTTPS is a
  follow-up.
- **No ping-URL UUID yet.** It's a cut-over step.

## Cut-over steps that remain

These are in `deploy.md` and follow ADR-0059's "Cut-over for one lane". Each is its own change or
act:

1. The orchestrator serving this release. Then HQ creates `sds-lane-revision-watcher` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `revision-watcher-ping-url`.
3. The registry rows, in two changes: the token's row and the ping URL's row. In the same work,
   the read-App key row's `runtime-fetch` reader for `scripts/run-revision-watcher.sh`.
4. The lane account's grants on its five secrets and its ping-URL secret.
5. The token's mint and the executor's write. Then the ping URL's row made due, and the executor's
   write.
6. A served release whose compose names the ping-URL secret's id, with `revision-watcher` removed
   from `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof and a container `--dry-run`. The dry run
   also proves installation coverage.
8. Cut-over steps 2 onward:
   - The **state copy**: the Mac's `sds/revision-watcher/alerts.json` into the volume before the
     first acting pass in the container, or the alerts the Mac left firing never resolve.
   - The paired reporting runs.
   - The schedule.
   - The Mac job's retirement.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files).** The change adds no logging
  path. The launcher's messages are the existing stderr lines in the shape the models use. The
  container sends both streams to the container log, and nothing writes a log file.
- **Code Brain rule 4 (never silently swallow an exception; in shell, no `|| true` that hides a
  failed mint or BWS read in the container).** The container path has no `|| true`.
  - Every BWS read is checked for an empty value and exits 1 with a named cause.
  - The derivation's exit code is checked.
  - The mint's exit code and empty output are checked.
  - The two `|| true` that remain are on the Mac branch only: the Keychain lookups, which are
    followed by an empty check and exit 1, and the existing `gh auth token` fallback. Tests pin
    each failure.
- **Code Brain rule 5 (never log secrets).** The App key goes from the BWS read to the mint's
  stdin and is never in a variable, an argument or a file. The tokens stay in the environment and
  are never echoed. `test_every_read_uses_the_lanes_one_token_and_gh_is_never_asked` asserts that
  no secret value appears in stdout or stderr. I read no real secret.
- **Infra Brain rule 212 (HTTPS for public FQDNs).** The lane has no FQDN, no port and no labels.
  The platform URL's `http://` is unchanged and listed as a follow-up.
- **Infra Brain rules 591 and 592 (no BWS token in tracked files or history).** No token appears in
  any file. Only UUIDs are present. The security scanner reports 0 BLOCK.
- **Infra Brain rule 593 (the bootstrap token never inline).** The compose maps `BWS_ACCESS_TOKEN`
  from `${REVISION_WATCHER_BWS_ACCESS_TOKEN}` only, and `add_refusals` checks that mapping.
- **Infra Brain rule 1032 (an exposed credential rotates now).** I surfaced no credential.
- **Deployment (no Coolify scheduled task beside an in-container scheduler).** The schedule is
  the container's crontab, gated on `LANE_SCHEDULE_ENABLED`. I created no Coolify task.
