Outcome: done

# Report: the membership check's lane files and launcher (ADR-0059 increment 3, step 3e-6)

Worker W-107. Branch `hq/lanes-inc-3-membership-check`, based on `origin/main` at `a957cd49`. The
PR is open and marked ready, not merged.

## What changed

| File | Change |
|---|---|
| `scripts/run-membership-check.sh` | A container branch, detected by a set `BWS_ACCESS_TOKEN` as `run-pin-watcher.sh` detects it. It skips activation and the Keychain, reads three secrets with the lane's one token, derives the mint's repositories, mints the token, exports it, clones intent-packages anonymously, and runs the program. The program's output now appends (`>>`) to the report. The CREDENTIALS header describes both hosts. The Mac branch's reads are byte-identical and it sets no `BUMP_PROPOSER_PACKAGES_CHECKOUT`. |
| `lanes/membership-check/compose.yaml` | New. One service, `command: ["membership-check"]`, `BWS_ACCESS_TOKEN: ${MEMBERSHIP_CHECK_BWS_ACCESS_TOKEN}`, `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}`, `SDS_DEADMAN_PING_URL_UUID: ""` (explained in a comment), and `restart: unless-stopped`. It has no volume, ports, networks, or labels. |
| `lanes/membership-check/crontab` | New. `47 7 * * * lane-run membership-check`. |
| `.bws-secrets.toml` | `membership-check` joins the `lanes` lists of `f793576f-…` and `314a966c-…`. `314f276d-…` gains `lanes = ["membership-check"]`. The comments name the launcher as a reader and say its account is granted at cut-over. No grant was made. |
| `.cred-consumers.toml` | One `runtime-fetch` reader row on `lanes-read-app-private-key` for `scripts/run-membership-check.sh` (`healthchecks_check = "sds-membership-check"`), following the pin watcher's row. The block comment is updated to match. |
| `tests/scripts/test_run_membership_check.py` | New: 29 tests (see the following sections). |
| `tests/scripts/test_deploy_lanes.py` | `membership-check` joins `placeholders`. |
| `tests/architecture/test_membership_edges.py` | `CHECK_EDGE.bearers` gains the read-App key (`READ_APP_KEY`), which the launcher now names. |
| `tests/architecture/edge_table.py` | Fixes the shell reader: it read a `<<<` here-string as a heredoc and skipped every command after it (see Concerns). |
| `tests/architecture/test_runtime_edges.py` | Adds an "after a here-string" row to the reader's injection controls. |
| `layers.toml` | Places the new test file in the `membership` layer. |
| `docs/operations/deploy.md` | "Registered lanes": a `membership-check` paragraph with its schedule, secrets, clone, outcome alarm, and the cut-over steps still to come. "A lane's GitHub token": the membership check's mint, three permissions, `GH_TOKEN`, and the anonymous clone. |
| `docs/operations/membership.md` | One line: `run-membership-check.sh` derives its mint's repositories through `sds_scope ledger`. |

There is no Python change in `src/`.

## The mint

The mint runs once per container pass, after the derivation:

```sh
sds_bws_value "$READ_APP_KEY_UUID" \
  | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id 5684758 --repository <owner/name>... \
      --permission metadata=read --permission contents=read --permission administration=read
```

- **Repositories.** They come from the member record's observed repositories (onboarding and
  member). The launcher derives them with
  `SDS_SCOPE_ORCHESTRATOR_TOKEN="$SDS_MEMBERS_OBSERVER_TOKEN" env -u BWS_ACCESS_TOKEN
  "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope ledger`, run from `REPO_ROOT`, exactly as
  `run-pin-watcher.sh` does. There's no typed list and no owner name. intent-packages and
  factory-runner are members, so the mint covers them like any member, including the fork list's
  read of factory-runner's `.cred-consumers.toml`.
- **Permissions.** These three, and no others. The call audit found every GitHub read in
  `cli.py`, `auto_merge.py`, `declaration.py`, the pin reader's `repositories()` and
  `fork_sync/watch.py::forks` within them:
  - `metadata`: the listings and `GET /repos/{r}`.
  - `contents`: `factory-target.toml`, the registry files, the workflow directory and files, and
    the fork list's file.
  - `administration`: `allow_auto_merge`.
- **The key.** It goes from the BWS read straight to the mint's stdin. The mint runs without the
  lane's token.
- **`GH_TOKEN`.** The minted token is assigned to both `SDS_MEMBERS_GITHUB_TOKEN` and `GH_TOKEN`
  and replaces any preset value. The container never runs `gh auth token`; tested with a `gh` stub
  that logs every call.
- **`allow_auto_merge`.** Whether an installation token with `administration=read` is served
  `allow_auto_merge` is a cut-over fact. I didn't probe it. The first container pass shows it as
  served or as a gap.

## The clone

The clone runs in the container branch only, after the mint:

```sh
env -u BWS_ACCESS_TOKEN -u GH_TOKEN -u SDS_MEMBERS_GITHUB_TOKEN \
    -u SDS_MEMBERS_OBSERVER_TOKEN -u SDS_MEMBERS_CHANGE_MANAGER_TOKEN \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
  git clone --quiet --depth 1 --branch main \
    https://github.com/AlobarQuest/intent-packages.git "$HOME/intent-packages"
```

- **How it runs.** It's run as git itself, never through `gh repo clone`. It uses no token, no
  `scripts/sds-git.sh`, and no venv. The `env -u` list covers all five credential variables, not
  only the handoff's three, because the two bearers are also in the environment. The commit read
  (`git -C … rev-parse HEAD`) runs through the same wrapper.
- **The checkout variable.** `BUMP_PROPOSER_PACKAGES_CHECKOUT="$HOME/intent-packages"` is
  exported whether or not the clone succeeded. `HOME` is `lane-run`'s per-pass temporary
  directory, and the launcher deletes nothing itself.
- **The clone line.** It's the report's first line, before the program's output, which appends:
  - `[membership-check] intent-packages clone: commit <sha>`, or
  - `[membership-check] intent-packages clone failed: git exited <code>: <git's error, newlines
    flattened to spaces>`.

  It prints a commit or git's stderr, never an environment.
- **Open item 2, the URL spelling.** This change resolves it. The launcher spells the URL as the
  literal `PACKAGES_CLONE_URL="https://github.com/AlobarQuest/intent-packages.git"`, and
  `test_the_clone_url_names_the_repository_packages_repository_names` holds it equal to
  `f"https://github.com/{PACKAGES_REPOSITORY}.git"`. The anonymity test also asserts that git
  received exactly that URL.

## Failure rules as built

| Case | Behavior |
|---|---|
| The observer or change-manager bearer is unreadable in the container | `sds_outcome_fail "FATAL: … (the lane's token)"`, exit 1, before the derivation. |
| `sds_scope` exits 3, or exits 0 with empty output | `sds_outcome_fail "…could not be derived; nothing was minted or checked" 3`. Nothing is minted or cloned, and the program doesn't run. |
| `sds_scope` exits any other non-zero code (1, 126, 127, 137) | `sds_outcome_fail "…sds_scope exited <code>" <code>`. The code passes through, as in the pin watcher. |
| The key is unreadable, or the mint fails or returns nothing | `sds_outcome_fail "FATAL: could not mint … (sds-github-mint exited N)"`, exit 1. Nothing is cloned and the program doesn't run. |
| The clone fails | Not the launcher's failure: git's error goes in the report, the variable is set anyway, the program runs, and the pass exits with the program's code (3 with the gap, 2 beside a disagreement). `sds_outcome_fail` is never called. |
| The program exits | Its code is the pass's code. `sds_outcome_end "$?" --body-on-success` is unchanged. |

`sds_outcome_fail` writes its message to the report and stderr, pings `/fail` with it when a URL
is resolved, and exits. No failure is swallowed.

## The crontab minute

The crontab line is `47 7 * * *` in the image's America/New_York zone. That's after the landing
ledger's 07:30 and near the Mac job's 07:45. Minute 45 is one of execution-watch's quarter-hours.
Minute 47 is clear of every lane: 0/15/30/45, 2-52/10, 20, 25, 30 and 40.
`test_no_two_lanes_start_a_pass_in_the_same_minute` asserts it.

## The outcome alarm with no ping URL

`sds_outcome_begin`, `sds_outcome_fail` and `sds_outcome_end --body-on-success` are unchanged.
`sds-membership-check` is in `deploy_orchestrator.py`'s `LANE_CHECKS`, which a test asserts.

With the compose's empty `SDS_DEADMAN_PING_URL_UUID`, `sds_deadman_resolve` falls through to the
lookup by name. That lookup needs a broad identity the container doesn't have, and the image has
no `/usr/bin/security`. So the pass logs `no SDS_DEADMAN_PING_URL_UUID and no broad BWS identity —
alerting disabled this run`, pings nothing, and runs.
`test_without_a_ping_url_secret_the_pass_still_runs_and_pings_nothing` shows the pass runs. It
uses a stand-in broad identity, so the test never reaches the real macOS Keychain.

## Tests and checks

- `tests/scripts/test_run_membership_check.py`, with 29 tests. Its stubs are fake `git`, `bws`,
  `gh`, `curl`, `python -m scripts.sds_scope`, `sds-github-mint` and `membership-check`. It runs
  no live clone and makes no network or BWS call. The tests cover the following:
  - The mint's exact arguments and stdin, and that the lane token is absent from the mint and the
    program.
  - `GH_TOKEN` equals the minted token, a preset value is replaced, and `gh` is never called in
    the container.
  - Every read uses the lane's token, and `51509d5e-…` is never read with it.
  - The clone command, URL and target.
  - The three `GIT_*` settings on both git calls, and that no credential variable reaches git by
    name or value. Every token variable is preset, so each is already exported when assigned.
  - The URL literal agrees with `PACKAGES_REPOSITORY`.
  - The report carries the clone line, then the program's output.
  - A failed clone still runs the program and exits 3, or 2 beside a disagreement, never 1.
  - The bearer, derivation (3, 3 with output, empty with 0, 1, 127), key and mint failure codes.
  - The pass runs with no ping URL.
  - The program's codes 0 to 3 pass through.
  - The Mac branch's reads are unchanged, and it doesn't clone, mint, or set the variable.
  - The compose meets `add_refusals` and maps nothing else, and the lane isn't stateful.
  - The crontab line, the plist's 07:45, and `TZ`.
  - Minute uniqueness.
  - The manifest's `lane_secrets` order, and each UUID is in the launcher.
- **Mutation.** Each of the following launcher mutations made at least one test fail, so all 11
  were killed and none survived:
  1. `-u GH_TOKEN` dropped from the clone.
  2. `-u SDS_MEMBERS_GITHUB_TOKEN` dropped.
  3. The two bearers' `-u` dropped.
  4. `-u BWS_ACCESS_TOKEN` dropped.
  5. `GIT_CONFIG_GLOBAL` dropped.
  6. `>>` changed to `>`.
  7. `administration=read` dropped.
  8. The clone failure made fatal.
  9. The checkout export dropped.
  10. The `sds_scope` code folded to 3.
  11. The edge-table reader fix reverted: 5 tests failed, the new here-string row among them.

  Mutations 1 to 4 first survived because the token variables weren't exported until after the
  clone. I strengthened the anonymity test to preset them, after which all four failed. I
  restored the launcher after each mutation and confirmed it with `cmp`.
- **The pre-existing tests** pass unchanged: `test_launcher_scope.py`'s Mac-path tests and
  `test_membership_edges.py`'s launcher regexes, apart from the bearer set.
- **`make check`** ran against `orchestrator_test_hq_w_107`, with `PGTZ=UTC` and
  `SECURITY_STANDARDS_DIR` set to the fixture. Results: ruff check and `ruff format --check` passed (1057 files), pyright reported 0 errors, shellcheck ran clean, and pytest collected and passed 14677 tests (1 skipped) in 6 minutes 18 seconds, exit 0.

## Self-review

- The handoff told me to mirror `run-pin-watcher.sh`. The one structural difference is that every
  container failure goes through `sds_outcome_fail`, not `echo; exit`, because this lane is an
  outcome alarm and doesn't arm a dead-man trap.
- I kept the Mac branch textually unchanged so its reads and the existing regex tests stay as
  they were. The only Mac-visible changes are that activation now sits inside
  `if [ -z "${BWS_ACCESS_TOKEN:-}" ]` (true on the Mac), that `unset BWS_ACCESS_TOKEN` runs before
  the program (the Mac had none), and that the output appends to an empty report.
- I ran `/code-review` at medium effort on the diff. Its seven findings needed no change:
  - Three describe behavior the handoff requires: the checkout variable set on a failed clone, `sds_scope` exit 3 pinging `/fail`, and the empty ping-URL placeholder.
  - One is the per-pass registry mirror, already concern 2.
  - One is the mint's reach to public non-members, the pin watcher's design under amendment 4.
  - One says `unset BWS_ACCESS_TOKEN` precedes `sds_outcome_end`, but that function resolves nothing again; it pings the URL that `sds_outcome_begin` resolved.
  - One notes an unbounded git error line in the ping body. It's low impact, because Healthchecks truncates bodies and git's errors are short.

## Standards followed

- **Code Brain rule 3, structured JSON logs to stdout, not files.** The launcher's lines go to
  stdout and stderr, and the report is the outcome alarm's existing temporary body, copied to
  stdout by `sds_outcome_end`. The launcher writes no log file. Its lines are plain text, as every
  sibling launcher's are; supercronic's `-json` wraps them in the container.
- **Code Brain rule 4, never swallow silently.** The container path uses no `|| true` on the
  mint, a BWS read, or the clone. Each failure exits through `sds_outcome_fail` with its code. The
  clone's failure is deliberately not fatal, as amendment 5 item 5 requires, and its exit code and
  error go into the report with an inline comment saying why.
- **Code Brain rule 5, never log secrets.** The clone line prints a commit or git's stderr, never
  an environment. Tests assert that no secret value appears in stdout or stderr, and that no
  secret reaches git's environment.
- **Infra Brain 212, HTTPS.** The lane has no FQDN. Its calls are to `https://sds.alobar.net`,
  `https://change-mgr.alobar.net` and `https://api.github.com`, and the clone uses
  `https://github.com`.
- **Infra Brain 591 and 592, no BWS token in tracked files or history.** Only UUIDs and the App
  id are in the diff; there's no token.
- **Infra Brain 593, no inline bootstrap token.** The compose maps
  `${MEMBERSHIP_CHECK_BWS_ACCESS_TOKEN}` from the resource variable only.
- **Infra Brain 1032, exposed credentials rotate.** No credential was surfaced. I read no real
  secret and made no live call.
- **Deployment, no Coolify scheduled task.** The schedule is the container's crontab, off until
  `LANE_SCHEDULE_ENABLED=true`.

## Concerns and follow-ups

1. **I fixed a shared guard.** `tests/architecture/edge_table.py`'s shell reader read the second
   and third `<` of a `<<<` here-string as a heredoc opener. It then waited for a delimiter that
   never came, so every command after a `while … done <<< "$X"` loop went unread, silently. That
   loop is in eight launchers. The membership check's launchctl control test found the problem
   once this launcher gained the loop. The fix steps over `<<<` whole, and a new injection row
   holds it.

   This is a test-only change, but it widens what the reader sees in any launcher scanned with
   `read_shell`. The full `tests/architecture` run stayed green, so no other scanned launcher
   hid a command.
2. **Registry mirrors per pass.** In the container, `_flagged_repositories()`'s `RepoMirror` lives
   under `$HOME/.cache/membership-check/mirrors`. `HOME` is per pass, so the registry mirrors are
   cloned fresh, anonymously, on every pass. This existing behavior is outside this handoff, and
   the code is unchanged. Any private repository in `REGISTRY_REPOSITORIES` would show there as a
   "credential registries couldn't be read" gap. The first container pass shows whether it does.
3. **`allow_auto_merge` with `administration=read`.** This is unproven until the first container
   pass, as the handoff says.
4. **Open item 3, the Mac checkout's freshness.** This change doesn't address it: the Mac branch
   doesn't clone.

## Cut-over steps that remain

These are in the order `docs/operations/deploy.md` gives:

1. The orchestrator serving this release. HQ then creates `sds-lane-membership-check` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `membership-check-ping-url`.
3. The lane's registry rows, in two changes: the token's row and the ping URL's row.
4. The lane account's grants on `f793576f-…`, `314f276d-…`, `314a966c-…` and its ping-URL secret.
5. The token's mint and the executor's write, then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `membership-check` removed
   from `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof. The check has no `--dry-run`, so its
   first container pass is cut-over step 2's ordinary pass. That pass settles amendment 5 open
   item 1. Its report must show all of the following:
   - The clone's commit, with no "packages checkout" gap.
   - `allow_auto_merge` served, or a gap per member.
   - The mint accepted, which shows the read App covers every observed repository under one
     owner.
8. Cut-over step 2's paired pass on both hosts. Record the Mac checkout's commit
   (`git -C ~/Projects/intent-packages rev-parse HEAD`) beside the clone line's commit; a
   difference the two commits explain isn't a cut-over failure. Then the schedule and the Mac
   job's retirement.
