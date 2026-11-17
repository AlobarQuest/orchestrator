Outcome: done

# Report: the rotation proposer's lane files and launcher (ADR-0059 increment 4, step 3f-5)

Branch `hq/lanes-inc-4-rotation-proposer`, base `a410af18` (#733). PR #734, open and ready, not
merged. No production act: no BWS read, no mint, no push, no `launchctl`, and no service call.
The one network act was `git fetch origin` in `~/Projects/intent-packages` and
`~/Projects/security-standards`, to read their `origin/main` with `git show`. It moved
remote-tracking refs only; neither working tree or branch was touched.

## What changed (files)

| File | Change |
| --- | --- |
| `scripts/run-rotation-proposer.sh` | A container block after the `--submit` test. It runs the pass and exits. Three header paragraphs and the push App constants are insertions. `git diff a410af18 -- scripts/run-rotation-proposer.sh \| grep '^-[^-]'` prints nothing, so the Mac's lines are byte for byte as they were. |
| `lanes/rotation-proposer/compose.yaml` | New. One service with `BWS_ACCESS_TOKEN: ${ROTATION_PROPOSER_BWS_ACCESS_TOKEN}`, `LANE_SCHEDULE_ENABLED` defaulting to false, the state volume at `/var/lib/sds/rotation-proposer`, and empty `SDS_DEADMAN_PING_URL_UUID`, `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR`. It has `restart: unless-stopped` and no ports, labels or networks. |
| `lanes/rotation-proposer/crontab` | New: `31 6 * * * lane-run rotation-proposer --submit`. |
| `scripts/lane_run.py` | `INFRAOPS_SETTINGS = ("INFRADRIFT_CONFIG_DIR", "SECURITY_DRIFT_STATE_DIR")`, passed when set, with a docstring line. |
| `.bws-secrets.toml` | `rotation-proposer` added to the `lanes` of the observer (`f793576f-…`), PROPOSE (`acccb346-…`), alert-sender (`b415d03b-…`) and push App key (`b8f547b9-…`) rows, with their reader comments updated. These name future grants; no grant is made. |
| `docs/operations/deploy.md` | A rotation proposer block after the bump proposer's step 9 and its cut-over steps. The bump proposer's and the work carrier's text isn't touched. |
| `docs/operations/credentials.md` | One line under the chain holders table: this launcher holds no chain password, on the Mac or in its container. |
| `tests/scripts/test_run_rotation_proposer_lane.py` | New, 52 tests, registered in `layers.toml` (rotation layer, beside `test_run_rotation_proposer.py`). |
| `tests/scripts/test_run_rotation_proposer.py` | The one-fetch assertion now expects two `sds-bridge` reads, the Mac's and the container's, each inside a `--submit` gate. |
| `tests/scripts/test_lane_run.py` | A test for `INFRAOPS_SETTINGS`; the "no token in the list" test includes it. |
| `tests/architecture/test_rotation_edges.py` | The push App key joins `rotation_proposer`'s bearers. |
| `tests/architecture/test_bridge_edges.py` | A comment: the push key is read by both proposers. |
| `tests/scripts/test_deploy_lanes.py` | `rotation-proposer` joins `placeholders`. |

## Tests and checks

- `make check` with `TEST_DATABASE_URL` set to `orchestrator_test_hq_w_203`: ruff, ruff format,
  pyright and shellcheck are clean. pytest **collected 15,387 items** (`10 workers [15387 items]`)
  and passed 15,386, with 1 skipped, in 320 s. That run preceded the last commit, which adds one
  test to the lane file; the affected files (152 tests) passed after it.
- CI on #734 (Quality run `44190499114`, head `da9a257c`): **success**. It collected 15,388
  items (`4 workers [15388 items]`) and passed 15,365, with 23 skipped, in 555 s.
- Mutation controls on the launcher, each run against the new test file with `-x` (so the run
  stops at the first failure):

  | Mutation | Result |
  | --- | --- |
  | The state-file existence check removed | 1 failed |
  | `unset BWS_ACCESS_TOKEN` removed | 1 failed |
  | The anonymous git config removed | 1 failed |

  The launcher was restored from a byte copy, and `cmp` confirmed the restore.
- Security scanner (`security_scan.cli . --category security`, worktree root): **0 BLOCK**, 2 WARN,
  1 INFO.

## Each secret and how it's read

In the container, every read uses `sds_bws_value <uuid>` in its one-argument form, so the token
reaches `bws` through the environment only, and only on `--submit`. A dry run reads no secret, as
on the Mac.

| Secret | UUID | Container | Mac (unchanged) |
| --- | --- | --- | --- |
| change-manager PROPOSE | `acccb346-…` | `--submit`, lane token → `ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN` | broad identity |
| orchestrator OBSERVER | `f793576f-…` | `--submit`, lane token → `ORCHESTRATOR_API_*` (key id `orchestrator-observer`) | narrow identity |
| `sds-bridge` alert sender | `b415d03b-…` | `--submit`, lane token → `SDS_BRIDGE_ALERT_TOKEN` | narrow identity |
| SDS lanes App key | `b8f547b9-…` | `--submit`, lane token, piped straight into `sds-github-mint --allow-write` | not read |
| Healthchecks API key | `51509d5e-…` | not read (see the following paragraph) | broad identity, through `sds-deadman.sh` |

The dead-man switch: with `SDS_DEADMAN_PING_URL_UUID` empty, `sds_deadman_resolve` falls through to
its name lookup, which asks for `BWS_ACCESS_TOKEN_BROAD` or the Keychain. In the container
`lane-run` passes no `BWS_ACCESS_TOKEN_BROAD`, and the Linux image has no `/usr/bin/security`, so
the lookup finds no identity and logs "alerting disabled". It reads nothing, `51509d5e-…`
included. This is the same path #733's bump proposer takes. The tests set a broad stand-in so they
never touch the real Keychain, and assert the launcher's own Keychain reads all sit after the
container block's closing `fi`. Once step 8 names the ping-URL id, the helper reads that secret
instead.

## The push token and the clones

- **Clones.** intent-packages and infraops-mcp-server are cloned with `--depth 1 --single-branch
  --branch main` into `/var/tmp/sds-rotation-proposer/{intent-packages,infraops-mcp-server}`. git
  gets no credential: `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and
  `GIT_TERMINAL_PROMPT=0` are set, the trace variables are unset, and no `GIT_CONFIG_COUNT` is set.
  The clones come before the push credential exists, so its header never rides either fetch. A test
  asserts both clone lines carry an empty `value0` and `count`. Both clones and the mirror cache
  are removed before the next clone and after the pass. A failed removal at the end is logged and
  doesn't change the code, because the clones hold no credential.
- **Registry mirrors.** `XDG_CACHE_HOME` points at `/var/tmp/sds-rotation-proposer/cache` as an
  assignment prefix on the program, so `RepoMirror`'s root (`rotation_worker/git_mirror.py`,
  `default_mirror_root`) is per-pass and discarded. The mirrors' own git runs with a built
  environment (`_run`: `PATH`, `HOME`, no global or system config), so no extraheader reaches them.
- **Push token.** On `--submit` the launcher mints for `AlobarQuest/intent-packages` alone, with
  `metadata=read` and `contents=write`, as App `5684778` (the key goes from BWS to the mint's
  stdin). After both clones it calls `sds_git_credential _PUSH_TOKEN 'alobar-sds-lanes[bot]'
  '354576843+alobar-sds-lanes[bot]@users.noreply.github.com'` and unsets `_PUSH_TOKEN`. A test
  asserts that no variable holds the raw token and that only `GIT_CONFIG_VALUE_0` holds its header.

## The fence, and the state file's writers and readers

**What I did.** The container takes both directory settings from the compose only, which leaves
them empty. The launcher refuses with exit 2, before any BWS read, mint, clone or program, in each
of these cases:

- either setting is unset or empty;
- `$INFRADRIFT_CONFIG_DIR/cred-consumers.list` is missing;
- `$SECURITY_DRIFT_STATE_DIR/cred-rotation-state.json` is missing.

Each case names the setting or file. An empty value is refused because infraops'
`process.env.X ?? default` (`paths.ts` 25-26) keeps `""`, which would resolve both files against
the working directory. I picked no home and moved or copied no file.

**The list file: not built, and why.** `cred-consumers.list` on the Mac names four paths (paths
only):

```
~/Projects/infraops-mcp-server/.cred-consumers.toml
~/Projects/change-manager/.cred-consumers.toml
~/Projects/factory-runner/.cred-consumers.toml
~/Projects/orchestrator/.cred-consumers.toml
```

Only one of the four is a file in the container before `cred-findings` runs: infraops-mcp-server's,
in the launcher's clone.

- The pass does fetch all four repositories, but as bare `RepoMirror` mirrors
  (`rotation_worker/registry.py` `REGISTRY_REPOSITORIES`). Python fetches them lazily in `_walks`,
  after `_inputs` has already called `read_due()` (`cli.py` 835), so they don't exist when
  `cred-findings` runs.
- change-manager and factory-runner need clones the launcher doesn't make.
- The image carries no orchestrator `.cred-consumers.toml`: the Dockerfile copies `src`, the venv,
  the registry bundle and security-standards. A copy built into the image would be the release's,
  not `main`'s.

So building the list would need either two new clones or a reordered pass, and the handoff says to
report that rather than add it. It belongs with the state file decision (cut-over step 1).

**Writers and readers of `cred-rotation-state.json`** (infraops at `c3359bf`, orchestrator at this
branch). Every one resolves the path through `securityPaths()` (`paths.ts` 25-26, 46-47).

| Who | Reads or writes | Where |
| --- | --- | --- |
| The rotation proposer, through `record-rotation` | **Writes** (`saveRotationState`, line 122; it reads the list at 119 and the state at 120) | orchestrator `src/rotation_proposer/cli.py` 583 (`_record_retired` → `record_rotation`), `findings.py` 249-255 (`_invoke(..., "record-rotation", "--cred", ..., "--date", ...)`); infraops `security-drift-cli.ts` 115-125 |
| A person, through `resolve-exposure` | **Writes** (`saveRotationState`, line 106; reads at 93) | infraops `security-drift-cli.ts` 87-107 |
| The rotation proposer, through `cred-findings` | Reads the list (171) and the state (174-177); refuses a missing list or state itself | orchestrator `cli.py` 835 (`_inputs`) and 901 (`_served_last_rotated`, the `standing-approval` command), `findings.py` 242-246; infraops `security-drift-cli.ts` 164-197 |
| The Mac's 03:00 scan (infra-drift), through `credScan` | Reads both (it treats a missing file as empty) | infraops `security-drift-cli.ts` 130-145, called at 275-279 |

`loadRotationState` and `saveRotationState` validate and write the file as mode `0600`
(`cred-rotation.ts` 146-161). So whatever home is chosen must keep the file `0600` and owned by the
image's `orchestrator` user, and it must be writable from the container, because `record-rotation`
writes there. If the container's copy and the Mac's diverge, the 03:00 scan reports from whichever
copy `record-rotation` didn't write, which is the case ADR-0059 lines 3031-3043 describe.

## The chain answer, with its source lines

**`revise` and `transition` in the container record `event_id` None and don't fail the pass. The
emitter doesn't fall back to a local chain file.**

- intent-packages `origin/main` (`134b4c9`), `src/intent_packages/operations.py`:
  - The module docstring, lines 1-7: "a failed emit never blocks the operation, it just records
    `event_id: null`".
  - `do_transition` and `do_revise` both have `except EmitError: event_id = None` (lines 173 and
    366). `do_supersede` behaves the same way (418); only `approve` (312) aborts on `EmitError`, and
    this lane never approves.
- `src/intent_packages/emitter.py`:
  - `FactoryEventsEmitter.emit` (127-165) shells out to `python -m factory_events emit` and raises
    `EmitError` on any non-zero exit (162-163).
  - `_security_standards_dir` (97-106) uses `SECURITY_STANDARDS_DIR`, which the launcher sets to
    the image's `/app/security-standards`.
- `create_rotation` (`factory/scaffolds.py`) emits nothing.
- security-standards, at the image's pin `08754a0f` and at `origin/main` (`2385423`), identical
  in these lines:
  - `factory_events/cli.py` `_cmd_emit` calls `chain.append_event` (line 40) and exits 1 on
    `ChainError`.
  - `chain.py` `append_event` (84-86) calls `_require_password()` (52-54), which raises "no chain
    password is set".
  - The `store.py` JSONL writer is no longer reached by `emit` (`store.py` docstring: "emit and
    adapt never reach append_event here (chain.py refuses without a password)").

The launcher clears every `FACTORY_EVENTS_*` variable, the retired DSNs included, before the pass,
so the emit's refusal is "no chain password", every time. A test asserts that no `FACTORY_EVENTS_`
name reaches the program even when the environment carried some. This matches ADR-0055 amendment
24 item 5 (3392-3398) and the bullet at 3565. The compose names no chain setting and joins no
`coolify` network; nothing else in the lane needs that network.

## The crontab and the push race

**Minute.** `lane_start_minutes` compares minutes across all hours, so the following minutes are
taken:

- 0, 15, 30 and 45 (execution-watch)
- 2, 12, 22, 32, 42 and 52 (unit verifier)
- 5, 20, 25, 35, 40, 47, 50 and 55 (the other lanes)

The Mac's 06:30 is execution-watch's minute, so the lane takes **06:31**, the nearest free minute.
It runs before the bump proposer's 06:55 and the carry's 07:05. A test pins `{31}` and checks that
no other lane uses it.

**A rejected non-fast-forward push.** `standing.publish` (`bump_proposer/standing.py` 489-517) runs
`git push origin main`. On refusal it raises `StandingError("<package> rev N is committed as <sha>
and unpublished: …")`. The rotation pass catches that per credential (`cli.py` 737-738) as an
`error` outcome, which is a finding: the pass exits 3, the declared finding code, so the armed
switch pings success (`--finding 3`), and the pass sends the keyed alert. Other due credentials in the same pass go on.

The stranded commit lived only in the discarded clone, so the next pass clones `main` again, finds
the package unrevised, files the observation again (it replays) and revises again. Because the
rotation proposer never approves, a refused publish leaves no chain event. This is unlike the bump
proposer, whose approval precedes its publish (#733's deploy.md step 5).

A race needs another push to intent-packages `main` between this pass's clone and its push. The
bump proposer at 06:55 doesn't overlap a pass that starts at 06:31 unless that pass runs about 24
minutes. Devon's own approval pushes can interleave at any time. Either way, the cost is one day's
delay for that credential and one alert.

The discarded clone also removes `require_publishable`'s brake: the stranded commit is never seen
again, so no later pass refuses because of it. That's harmless here because nothing was approved.

## The state volume

`sds-lanes-rotation-proposer-state` is mounted at `/var/lib/sds/rotation-proposer`, which the
Dockerfile creates owned by `orchestrator`, mode 0700 (`lanes/Dockerfile` 71-74). `lane_run.py`
already passes `ROTATION_PROPOSER_STATE_DIR` (line 60). The launcher refuses a pass without it
(exit 1) and creates it if absent. The volume holds `alerts.json` and `approval-since.json`
(ADR-0059 table, line 345). The Mac's `sds/rotation-proposer/` files are copied in before the first
`--submit` pass (deploy.md step 10).

## The paired-pass answer, with its ADR lines

ADR-0059 1137-1141 puts the rotation proposer in the group whose reporting mode, the bare
invocation, **pings the real check**: the residual `sds-deadman.sh` names
("`run-rotation-proposer.sh` … so a bare operator run of those still pings"). So the paired dry
runs on the Mac and in the container each ping `sds-rotation-proposer`. In the container that
holds only once step 8 names the ping-URL id; before that the container's dry run pings nothing.

The fence's exit 2 also pings `/fail` (2 isn't the declared finding code), so a bare proof run
before the state file decision would page. That's why deploy.md puts the decision first, before the
`--add` proof run.

For the comparison to mean anything, both hosts must read the same inputs. The Mac's
`~/Projects/intent-packages` and `~/Projects/infraops-mcp-server` must be fast-forwarded to
`origin/main`. The container's list and state must be the same content as the Mac's, which is the
decision.

## Report, don't fix

- **Readers keyed on a revision commit's author (ADR 1406-1409).** This lane's commits add no
  reader. Its revisions are `ready_for_review` commits, and every reader that keys on a commit's
  author is in #733's table, which still holds. I found no other reader on the rotation path:
  - `rotation_worker` checks only that the revision's commit is an ancestor of `main` and
    recomputes its hash (`git_mirror.is_ancestor`).
  - The decomposer, `standing_approval`, and the work carrier read package files and lineage, not
    git identities.
  - A grep for `author`, `committer`, `%an`, `%ae` and `[bot]` across `src/rotation_*`,
    `src/work_carrier`, `src/work_watcher`, and intent-packages `src`, `scripts` and `.github`
    found none keyed on a git identity.

  Devon's approval of the revision is his own commit and is unchanged.
- **intent-packages branch protection (ADR 1397-1404).** Unchanged and not raised. The App's direct
  push is refused until Devon admits it, which is #733's step 5. Here it's cut-over step 6, a
  precondition for the first revising pass. Its failure mode is milder here (the preceding
  section).
- **The anonymous infraops clone (ADR 1034-1036).** I didn't make a live clone (the handoff rules
  out every live call). The evidence is static:
  - infraops-mcp-server is one of the four `REGISTRY_REPOSITORIES`, which the rotation
    decomposer's lane already mirrors anonymously in its container (ADR-0059 decision 1;
    `lanes/rotation-decomposer/compose.yaml` line 8).
  - The membership check's live lane clones intent-packages anonymously (ADR-0059 line 2537).
  - `dist/cli/security-drift-cli.js` is tracked, and its whole static import graph (16 files at
    `c3359bf`) imports only `node:` built-ins, so it runs with no `npm ci`. The image has node.

  The first real clone is the bare run at step 9.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout).** The launcher adds no log stream. The
  program's existing lines (`_log` JSON in `cli.py`) are unchanged. The launcher's own lines are
  one-line FATAL messages to stderr, as every launcher's are. It writes no log file.
- **Code Brain 4 (no silent swallow; no `|| true` hiding a failed read, clone, mint or push).**
  - Every BWS read, the mint, both clones and `sds_git_credential` check their result and exit
    with a message.
  - The one `||` in the block is the final clone removal. It logs a line and carries an inline
    reason: the clones hold no credential, and the next pass removes them.
  - The push is `standing.publish`'s, which raises and is reported.
  - The Mac's existing `|| true` on the Keychain reads is unchanged; each one is followed by an
    explicit empty check and a FATAL.
- **Code Brain 5 (never log a secret).**
  - The launcher prints no value, header or token.
  - git trace and curl verbose are unset.
  - The clone's captured message holds no credential, because none is configured when it runs.
  - The tests assert that no secret value or token appears in output or in any argv.
- **Infra Brain 2, 591, 592 (no secrets, `.env` files or BWS tokens in tracked files or
  history).** Only UUIDs, the App id and the bot's public noreply address are added. The scanner
  reports 0 BLOCK.
- **Infra Brain 3 (no `:latest`).** The image is `sds-lanes:${SDS_LANES_IMAGE_TAG}`.
- **Infra Brain 212 (HTTPS).** The lane has no FQDN. Every call is HTTPS: `https://sds.alobar.net`,
  change-manager's default, alert-hub, and `https://github.com`.
- **Infra Brain 593 (bootstrap token never inline).** The compose maps
  `${ROTATION_PROPOSER_BWS_ACCESS_TOKEN}` only. No plist or script holds a token.
- **Infra Brain 1032 (an exposed credential rotates now).** None was surfaced: I read no secret,
  and no value appeared in any output.
- **Infra Brain 4, deployment (no Coolify scheduled task beside an in-container scheduler).** The
  schedule is the crontab, which the entrypoint runs only when `LANE_SCHEDULE_ENABLED=true`.
  There's no Coolify task.

## Self-review

`/code-review` (medium) ran on `a410af18...HEAD` and returned five findings. None changes this PR:

1. **The state directory's writability isn't checked**, only the file's existence. `record-rotation`
   writes there, so a read-only or ephemeral mount would lose the write and leave the credential
   due. That's a property of the home the decision picks: concern 4 and cut-over step 1.
2. **The empty placeholders refuse every pass.** That's the design; deploy.md puts the decision
   before the `--add` proof and the schedule.
3. **The list's paths might not resolve in the container.** If they don't, infraops'
   `loadCredConsumerFiles` throws `cannot read <file>` (`cred-consumers.ts` 240-245). That's a
   non-zero exit, so the pass exits 2 visibly rather than reporting nothing due.
4. **An exit after the first clone leaves it under `/var/tmp` until the next pass removes it.** It
   holds no credential. Cleaning up on exit would need the EXIT trap, which the dead-man switch
   owns (`sds_deadman_finish`); #733 leaves its clone the same way.
5. **The container block repeats #733's read, mint and clone shape.** Extracting a shared helper
   would change the bump proposer's launcher, which this handoff fences off. Follow-up 8.

The advisor's review before completion found four more, all fixed:

- the test count;
- "before any read", which was true only while the ping-URL id is empty (a test now covers a named
  id);
- an assertion that couldn't fail (the compose's networks);
- the overstated "no live call".

## Concerns and follow-ups

1. **The worktree refusal in `/app`.** `sds_refuse_linked_worktree` runs in the container too,
   because the Mac's lines are unchanged. `/app` has no `.git`, so both `rev-parse` calls fail
   with equal empty output and the check passes. A test runs the container path from a tree with
   no `.git` to prove it. The cost is two "fatal: not a git repository" lines on stderr in every
   container pass. Moving the call into the Mac's path would change a Mac line;
   increment 6 can drop it with the Mac branch.
2. **The push header is in every child's environment.** As in #733, `GIT_CONFIG_VALUE_0` is
   exported for `standing.publish`'s push, so `rotation-proposer` and its children inherit it.
   Those are node `cred-findings`, the intent-packages lifecycle, and the mirrors' git, though
   `RepoMirror` builds a clean environment. No variable a program reads by name holds the token.
3. **The list's content and the four registry paths** are part of the state file decision. The
   container needs a `cred-consumers.list` whose paths resolve inside it. This launcher builds none.
4. **The state file must stay `0600` and owned by `orchestrator`** wherever it lives
   (`cred-rotation.ts` 146-161), and must be writable by the lane (`record-rotation`).
5. **`credentials.md`.** It has no rotation proposer row, because the lane holds no chain password.
   A row in that table would claim it does, so I added a sentence under the table instead.
6. **Not covered by the Standards section.** git's credential handling in a child process (concern
   2) and file-permission requirements for a shared state file (concern 4).
7. **No live anonymous clone was made.** The evidence is the decomposer's and membership check's
   live anonymous reads and the static import graph. Step 9's bare run is the first proof.
8. **A shared container helper for the two proposers.** The read, mint, clone and credential steps
   now exist in both launchers. Extracting them changes `run-bump-proposer.sh`, so it's its own
   change.
9. **A stale line in the bump proposer's fenced deploy.md section.** Its step 4 says its grant on
   the push App key is "the first grant on `b8f547b9-…` to any account". That holds only if the bump
   proposer cuts over first. I left it, because the section is fenced.

## Cut-over steps that remain

These are in deploy.md's rotation proposer block, in this order:

1. **The state file decision** (Devon, through HQ). Where `cred-rotation-state.json` lives, how
   infra-drift's Mac scan reads it, and how `cred-consumers.list` reaches the container. Then a
   change writes both directory settings into the compose, with the volume or mount that carries
   the files.
2. The orchestrator serving this release; HQ creates `sds-lane-rotation-proposer` without
   deploying it.
3. The VM root's keeper commands for the lane token and `rotation-proposer-ping-url`.
4. The lane's registry rows (token, then ping URL), and a `runtime-fetch` reader for this launcher
   on the push App key's row.
5. The lane account's grants on its four secrets and its ping-URL secret. PROPOSE (`acccb346-…`)
   becomes a direct grant (Devon's act at the identity gate).
6. Devon's intent-packages `main` protection change admitting the App's push (shared with the bump
   proposer's step 5).
7. The token's mint and the executor's write; then the ping URL's.
8. A release whose compose names the ping-URL id, with `rotation-proposer` out of `placeholders`.
9. `--add`, the `lane-run --read` proof over five secrets, and a bare `lane-run rotation-proposer`.
10. The state copy, the paired dry runs (after fast-forwarding both Mac checkouts), the schedule,
    and the Mac job's retirement.

Merging #734 is HQ's call; this worker doesn't merge. Once it merges, the worktree, the
`orchestrator_test_hq_w_203` database and the branch can be removed.
