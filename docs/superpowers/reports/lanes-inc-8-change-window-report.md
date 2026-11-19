Outcome: done

# Build report: the change-window lane's files and launcher (ADR-0059 increment 8, PR (4))

Worker W-231, branch `hq/lanes-inc-8-change-window`, base `669c8a78`. The PR contains code, tests,
lane files and docs only. I made no live call and no production act, and wrote no
`.bws-secrets.toml`, `.cred-consumers.toml` or ADR change. HQ answered the two questions in
`lanes-inc-8-change-window-question-answered.md`: Q1 A and Q2 A.

## What changed

| File | Change |
| --- | --- |
| `lanes/change-window/compose.yaml` | New. One service: the `sds-lanes` image at `${SDS_LANES_IMAGE_TAG}`, `command: ["change-window"]`, `BWS_ACCESS_TOKEN: ${CHANGE_WINDOW_BWS_ACCESS_TOKEN}`, `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}` and `SDS_DEADMAN_PING_URL_UUID: ""`. It has no volume, network, ports or labels, and uses `restart: unless-stopped`. |
| `lanes/change-window/crontab` | New. `0 4 * * * lane-run change-window --submit`. |
| `scripts/run-change-window.sh` | New. The container-only launcher. |
| `tests/scripts/test_run_change_window.py` | New. 47 tests with fake `bws`, `git`, `node` and `curl`. |
| `scripts/deploy_orchestrator.py` | One `LANE_CHECKS` row: `"change-window": "Change Manager OPs Check"`. I also edited the table's comment. |
| `tests/scripts/test_deploy_lanes.py` | `_ARM` and `_CHECK_NAME` read a quoted or a bare check name (never a `$` expansion), and `len(names) == 1` stays. I added a control test, `test_a_check_name_is_read_quoted_or_bare_and_never_as_an_expansion`. `change-window` joins `placeholders`. |
| `layers.toml` | The launcher and its test join the `landing` layer. `lanes/**` is already `runtime`'s. |
| `docs/operations/deploy.md` | Under "Registered lanes and the lane-token matcher": the lane, its modes, check, digest (with the reasons for a notice) and cut-over steps. |
| `docs/operations/credentials.md` | The `change-window` lane account as the future holder of the four secrets. |
| `docs/superpowers/reports/lanes-inc-8-change-window-question-answered.md` | HQ's answered question, kept as the other answered questions are. |

`lane_run.py`, `test_lane_image.py`, `test_sds_bws.py`, the Dockerfile, `STATEFUL_LANES` and
`src/` are unchanged: no test required a change to any of them.

## The secrets and how each is read, by mode

All four ids are empty constants in the launcher (Q2 A), as
`NAME="${CHANGE_WINDOW_<X>_UUID:-}"`. The override is a test seam that `lane-run` never passes.
`EMPTY_IDS` in the test lists all four, so a change that writes one must update the test. Each is
read with the lane's one token through `sds_bws_value`'s one-argument form (`--color no`, the token
in the environment).

| Secret | Launcher id | Bare | `--submit` |
| --- | --- | --- | --- |
| change-manager `execute` bearer | `CHANGE_MANAGER_EXECUTE_UUID` | Read | Read |
| Coolify window token | `COOLIFY_WINDOW_TOKEN_UUID` | Not read | Read |
| Anthropic API key | `ANTHROPIC_KEY_UUID` | Not read | Read |
| alert-hub digest sender token | `DIGEST_SENDER_UUID` | Not read | Read, never exported |

A pass that needs an empty id stops before it clones or reads anything. It prints one line,
`FATAL: <ID> is empty: its secret doesn't exist yet, so this pass can't run`, and exits 1. An
unreadable or empty value stops the pass before the window runs, also with exit 1. The ping-URL
secret is read only on `--submit`, by `sds_outcome_begin`, because a bare pass pings nothing.

## The calls and their variables

1. `git clone --quiet --depth 1 --single-branch --branch main
   https://github.com/AlobarQuest/infraops-mcp-server.git <pass dir>/infraops-mcp-server`, through
   `sds_anonymous_clone`. It runs with no credential and no lane token.
2. Bare: `node <clone>/dist/bundle/change-mgr-cli.js run-window --dry-run --instance prod`.
3. `--submit`: `node <bundle> run-window --instance prod --report-dir <pass dir>/reports --now
   <UTC ISO time>`.
4. `--submit`: `POST https://alerts.alobar.net/v1/alerts`, with the bearer header given to curl on
   stdin as its config.

The program gets `CHANGE_MGR_API_BASE=https://change-mgr.alobar.net` and `CHANGE_MGR_M2M_TOKEN` in
both modes. On `--submit` it also gets `COOLIFY_BASE_URL=http://100.92.138.19:8000`,
`COOLIFY_API_TOKEN` and `ANTHROPIC_API_KEY`. I confirmed these names against `fdf3e91b`'s source
and the bundle itself: `client()` reads the change-manager pair, and `coolify-client.ts`
`getInstanceConfig` reads `COOLIFY_${INSTANCE}_*` first and then, for prod, `COOLIFY_BASE_URL` and
`COOLIFY_API_TOKEN`. `new Anthropic()` reads `ANTHROPIC_API_KEY`. The launcher unsets
`COOLIFY_DEV_*` and `COOLIFY_PROD_*`, so a planted `COOLIFY_PROD_BASE_URL` can't redirect the prod
client; a test plants all four. `BWS_ACCESS_TOKEN` is unset before `node`, and `git` never has it.

## The crontab and why

`0 4 * * *` is HQ's configuration: the Mac job's hour, in the image's America/New_York time.
`--submit` is in the line, as the other acting lanes put it, so the bare run is the dry run. No
test refuses the time. The hour falls inside `LANDING_HOURS`, which governs redeploys of the
resource (the quiet slot), not lane passes.

## The digest

The digest is sent as an `info` notice, at once, with the Mac's key `report:change-window:<date>`
and subject `Change window <date>`. Its message is the run's `<date>.change-window.md`, cut to 12,000
UTF-16 units and a 15,000-byte encoded message (the hub's 16,384-byte body limit), with a footer
that points at the window run's `report_md` in change-manager. The cut is the Mac's `_cut_report`
logic with a different footer. A notice, not a held `info` alert, for these reasons:

- A held alert that is closed never delivers. `staleReason` in alert-hub's
  `src/engine/delivery.ts` cancels a pending delivery when its alert is `closed`, and
  `dispatchAlert` holds a non-critical alert during quiet hours (`src/engine/dispatch.ts` ~98). So
  "a daily key and a close" sent at 04:00 is cancelled before 07:00.
- An alert left open makes the next day's send with the same key a repeat that notifies nobody.
  The sender guide's "Keys" section says to never put a timestamp in a key.
- An alert's `message` caps at 4,000 characters, and a notice's at 12,000 (the sender guide's
  request table). The guide's "Send a report as a notice" section names a notice as the form for
  daily reports, and says a notice delivers on arrival.

HQ accepted delivery at about 04:00. The Mac queued the digest for its 07:30 outbox send, which ADR
0004 records. The reasons are also in the `deploy.md` section.

## Failure handling and exit codes

I used the rotation proposer's meanings: 1 means the tool failed, and 2 means its inputs couldn't
be used.

- Exit 1: an argument other than none or `--submit`, no lane token, an empty id, an unreadable or
  empty secret, a failed clone, or `run-window` failing. `run-window`'s 1 passes through, and any
  other code is reported as 1 with a `FATAL: run-window exited <rc>` line.
- Exit 2: the clone has no `dist/bundle/change-mgr-cli.js`, or there's no `node`. Both are checked
  before any secret is read.
- On `--submit`, every non-zero exit pings `/fail` with the report. A bare pass pings nothing.
- The digest send is best-effort. A missing digest, a request that can't be built, or a send the
  hub doesn't answer with 202 each log one `[change-window]` line, and the exit code doesn't
  change. The justification is inline: the window has run and recorded each outcome, so failing
  the pass would report the window itself as failed.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_231`: ruff, format, pyright and shellcheck clean.
  **15,832 passed, 1 skipped (15,833 collected)** in 314 seconds.
- `tests/scripts/test_run_change_window.py`: 47 passed.
- Mutation runs: 16 mutations, each with the file backed up, restored and checked restored, and
  `__pycache__` cleared. Every mutation failed at least one test, so none survived:
  - Check-name test (HQ's requirement):
    - The launcher names `Change Manager Ops Check`, which differs from its `LANE_CHECKS` row.
    - The launcher carries a second `CHECK_NAME="sds-change-window"` line.
    - The launcher adds a second name through an `sds_deadman_arm` line.
  - Launcher:
    - The `unset BWS_ACCESS_TOKEN` line is removed.
    - The dev and prod Coolify unsets are removed.
    - The dry run loses `--dry-run`.
    - The window loses `--instance prod`.
    - The tailnet Coolify URL is swapped for the public domain.
    - A 202 is no longer read as sent.
    - The missing-bundle check is removed.
    - The empty-id check is removed.
    - The digest token is exported.
    - A failed send sets `rc=1`.
    - An odd `run-window` code folds to 0.
    - The sender token's character guard is removed.

  My first M5 and M12 attempts matched the header comment instead of the code and survived. I reran
  both against the code lines, and both failed a test.

## Report, don't fix

- **What `--dry-run` reads and calls** (`fdf3e91b`, `src/cli/change-mgr-cli.ts`). `main` checks
  every flag, then `client('change-window-agent')`, which throws unless `CHANGE_MGR_API_BASE` and
  `CHANGE_MGR_M2M_TOKEN` are set. `doRunWindow` with `dryRun` calls `c.getApproved()` (`GET
  /api/items?status=approved`) once. It reads `MAX_CHANGES_PER_WINDOW` (default 5), prints one
  JSON line and returns. `makeAgent` isn't called, so no Anthropic client is built and no Coolify
  call is made. So the bare pass needs only the change-manager bearer. The bundle's Anthropic SDK
  also reads `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, profile, config-directory and
  federation variables, but only when the client is built. `lane-run`'s fixed environment passes
  none of them.
- **The digest's sender and token.** The form is a notice, for the reasons in "The digest". The
  Mac sends as `report-infraops` (BWS `4ed74de3-…`, infraops' `load_alert_hub_tokens`). The
  narrowest choice is a new sender, for example `report-change-window`. Its name matches the
  documented `report-*` email-only routing rule (sender guide, "Route reports to email only"), so
  no rule change is needed. Its token can send only as that sender, and its one consumer is this
  lane. Reusing `report-infraops` would share infraops' token with the Mac until rollback ends. A
  sender token is the narrowest credential alert-hub offers: it posts `/v1/alerts` as one source.
  Optionally, set its **Expected every** to a day, so a silent lane raises a `silent:` alert.
- **Anthropic key scope.** From repository docs only: infraops-mcp-server's
  `docs/secrets-inventory.md` says Anthropic "doesn't support per-key scoping". So one key for this
  lane is the narrowest form those docs know: it's separately revocable, and it's held by one
  consumer. The bundled SDK reads workspace, service-account and federation variables, which hints
  at narrower forms in the SDK's version. I didn't verify that without a web call, and it's for the
  mint gate to check.
- **The `execute` bearer's coverage.** `api-client.ts` at `fdf3e91b` calls `GET
  /api/items?status=approved`, `POST /api/items/{id}/claim`, `POST /api/items/{id}/outcome`,
  `POST /api/window-runs` and `PATCH /api/window-runs/{id}` on a run (and `POST /api/sync` for
  `sync` only, which this lane never runs). `app/scopes.py` at `e57076f2` puts all five in
  `EXECUTE`, and the dry run's list call is in `_READ_ROUTES`. `app/api.py` at `e57076f2` returns
  409 unless the item is `in_progress` (~529). The `scopes.py` comment above `SCOPE_ROUTES` still
  says `outcome` "isn't gated on `in_progress`". **Follow-up for change-manager:** correct that
  stale comment. I didn't edit change-manager.
- **What the lane drops from the Mac job** (`change-window.sh` at `fdf3e91b`), and whether each
  drop changes behavior:
  - Activation, the env file and the Keychain bootstrap. The image and the per-pass clone replace
    them. No behavior change.
  - The `tee -a` log file. Output goes to stdout, which supercronic's `-json` wraps. No behavior
    change (Code Brain rule 3).
  - The dev Coolify token, `COOLIFY_DEV_*` and the unscoped run, which covered both instances.
    **This changes behavior** (D-67 C): once the Mac job retires, no job executes a dev item; a
    person does.
  - The public Coolify URL (`http://coolify-1.devonwatkins.com`). The lane uses the tailnet
    address. Same instance, different path.
  - The full change-manager bearer (`af0e4192-…`). The lane uses the `execute` bearer, which
    covers every call a run makes, so a run behaves the same. The lane can no longer approve,
    defer or sync.
  - `ALERT_HUB_TOKEN`, which the Mac exports "for the CLIs". The change-mgr bundle reads no
    `ALERT_HUB_*` variable, so dropping it changes nothing.
  - The 07:30 outbox (`queue_report`). **This changes behavior:** the digest arrives at about
    04:00, and a failed send isn't retried at the next morning send, because the lane keeps no
    outbox. The window's report still reaches change-manager as the run's `report_md`.
  - The Mac's `HC_URL` from its env file. The lane reads the ping URL by UUID. Same check, and the
    pings are the same: `/start`, then success or `/fail`. The lane also sends the report as the
    `/fail` body.
  - `python3` for the BWS parse. The lane uses the venv's interpreter through `sds_bws_value`. No
    behavior change.

## Self-review

- The launcher follows `run-follow-up-mint.sh` (one-argument BWS reads, outcome alarm, no Mac
  branch) and the rotation proposer's clone order: it gathers inputs before secrets, and unsets the
  token before the program runs. It reuses `sds_anonymous_clone` rather than adding a second clone
  helper.
- A shellcheck warning found that a local `body` shadowed `sds_outcome_ping`'s array of the same
  name in the sourced helper. I renamed it to `request`.
- The test's `Run` reads every stub's argv log, so the no-secret-in-argv check is dynamic, across
  `bws`, `git`, `node` and `curl`. It isn't a text scan.
- Every pass's temporary directory is checked empty after the run, failures included.
- `/code-review` at medium effort on `origin/main...HEAD` returned five findings:
  - Fixed: the digest token was written into a quoted curl config line unchecked. A token holding a
    quote, backslash or whitespace now fails the pass before the window runs, as
    `sds_deadman_resolve_by_uuid` checks the ping URL. Three tests cover it.
  - Not fixed: the CLI's codes other than 0 and 1 fold to 1. `main` exits only 1 on an error, and
    the fold is documented in the header.
  - Not fixed: the digest file's date. The source names the file `opts.now.slice(0, 10)`, which is
    the launcher's own `--now` date.
  - Not fixed: the cut loop costs one encode per character. It's the Mac's `_cut_report` logic,
    and it runs only on a digest over 12,000 characters.
  - Reported as a concern: the clone at an unpinned `main`, which is D-66 A's design.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** nothing writes a log file. The
  launcher's lines and the program's output go to stdout, which the image's `supercronic -json`
  emits as JSON records, as for every lane. The Mac's `tee -a` isn't carried over.
- **Code Brain rule 4 (no silent swallow):** no `|| true` hides a failed BWS read, clone or run.
  Each fails the pass with a `FATAL:` line. The digest's best-effort send carries an inline
  justification, and every failure branch logs one line.
- **Code Brain rule 5 (never log secrets or request bodies):** secrets reach the program through
  its environment and curl through its stdin config. A test asserts that no secret value appears in
  any stub's argv, stdout or stderr, and that the digest's request body isn't logged. The digest
  token is never exported.
- **Infra Brain 212 (HTTPS):** the lane has no FQDN. It calls change-manager, alert-hub and the
  Anthropic API over HTTPS, and production Coolify over the tailnet address (G-73), as estate
  landing does.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history):** no token is written. The
  ids are empty and are ids, not secrets.
- **Infra Brain 593 (the bootstrap token is never inline):** the compose maps
  `${CHANGE_WINDOW_BWS_ACCESS_TOKEN}` only.
- **Infra Brain 1032 (rotate an exposed credential):** I surfaced no credential. The reads I made
  were source files and docs.
- **Infra Brain rule 2 (no committed secrets or `.env` files):** none.
- **Infra Brain rule 3 (no `:latest`):** the image is `sds-lanes:${SDS_LANES_IMAGE_TAG}`, which
  `--add` requires to be a sha tag.
- **Infra Brain rule 4 (no Coolify scheduled task beside an in-container scheduler):** the schedule
  is the container's crontab, off until `LANE_SCHEDULE_ENABLED` is `true`.

## Concerns and follow-ups

- **The clone is unpinned.** Each `--submit` pass runs infraops-mcp-server's `main` bundle with
  the prod Coolify window token, the Anthropic key and the `execute` bearer in its environment, so
  `main`'s branch protection is the trust boundary. That's D-66 A's design and the rotation
  proposer's clone model, recorded here for the mint gates.
- **Shared minute 0.** The 04:00 line shares minute 0 with execution-watch's `*/15`. Every other
  lane took a minute no lane starts on, and each lane's own test asserts that its minute is unique.
  This lane's test doesn't, because the time is HQ's configuration. The two containers share no
  state.
- **`scripts/CLAUDE.md`.** Its list of outcome-alarm launchers doesn't name
  `run-change-window.sh`. The file is outside my fence, so add the name in a docs change.
- **Change-manager comment.** The stale `scopes.py` comment is a follow-up for change-manager.
- **Check uniqueness at cut-over.** Whether the self-hosted Healthchecks listing that
  `quiet_slot_unmet` reads holds exactly one check named `Change Manager OPs Check` is a cut-over
  verify item. I didn't check it here. The Mac job's header says "Healthchecks.io".
- **A failed digest send is lost, not retried.** The window's report survives in change-manager.
- **Increment-wide.** HQ's ADR-0059 amendment for the increment is separate.

## Cut-over steps that remain

These are in `deploy.md`, in order:

1. The release served, then HQ creates `sds-lane-change-window`.
2. The identity gate and the mint gates for the four secrets and the ping URL, then the keeper
   commands.
3. The `.bws-secrets.toml` and `.cred-consumers.toml` rows, with each UUID written into the
   launcher and removed from `EMPTY_IDS`.
4. The token's mint and the executor's write, then the ping URL's.
5. A release whose compose names the ping-URL id, with the lane removed from `placeholders`.
6. `--add`, then `lane-run --read` over the five secrets.
7. The paired `run-window --dry-run --instance prod` on both hosts, compared on exit code and id
   set, plus read-only reach probes to prod Coolify and the Anthropic API base.
8. Boot out and disable the Mac job between two 04:00 passes, then turn the schedule on.
9. Change-manager's D-69 A pull request, security-standards' registry and governance-map lines,
   and infraops' removal of the Mac job after rollback ends.
