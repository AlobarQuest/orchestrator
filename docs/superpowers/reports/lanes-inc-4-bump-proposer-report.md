Outcome: done

# Report: the bump proposer's lane files and launcher (ADR-0059 increment 4, step 3f-4)

Worker W-202, branch `hq/lanes-inc-4-bump-proposer`, base `origin/main` at `cc299052` (#732).
PR: [#733](https://github.com/AlobarQuest/orchestrator/pull/733) (ready for review, not merged). Commits `8da4457d` and `a0ceb5c6`.

## What was built

The bump proposer can now run in its own lane container on the VPS, beside the Mac job, which is
unchanged until increment 6. Nothing is deployed: the lane has no resource, token, grants or
registry rows yet. Those land at cut-over (the cut-over steps that remain, later in this report).

- **`lanes/bump-proposer/compose.yaml`**: one service, modelled on the work carrier's.
- **`lanes/bump-proposer/crontab`**: `55 6 * * * lane-run bump-proposer --submit`.
- **`scripts/run-bump-proposer.sh`**: a container branch, chosen when `BWS_ACCESS_TOKEN` is set,
  beside the Mac branch.
- **`bump-proposer repositories`** (`src/bump_proposer/cli.py`): a new subcommand that prints the
  repositories the lane's read token must name.
- **`.bws-secrets.toml`**: `bump-proposer` in the `lanes` of six existing rows, and a new row for
  the "SDS lanes" App's key.
- **Docs**: `docs/operations/deploy.md` gains the bump proposer's lane section and its remaining
  cut-over steps; `docs/operations/credentials.md`'s append-password holder row names the lane
  account after cut-over.

### Files changed

| File | Change |
| --- | --- |
| `lanes/bump-proposer/compose.yaml` | New. |
| `lanes/bump-proposer/crontab` | New. |
| `scripts/run-bump-proposer.sh` | The container branch; the Mac branch is indented inside `else` and otherwise unchanged. |
| `src/bump_proposer/cli.py` | `repositories()` and its dispatch in `main()`. |
| `.bws-secrets.toml` | Six `lanes` lists, comments, and the `lanes-push-app-private-key` row. |
| `tests/scripts/test_run_bump_proposer.py` | New: 52 tests of the launcher and the lane's files. |
| `tests/bump_proposer/test_repositories.py` | New: 8 tests of the subcommand. |
| `tests/architecture/test_bridge_edges.py` | The bump proposer's bearers gain both App keys. |
| `tests/scripts/test_deploy_lanes.py` | `placeholders = {"bump-proposer"}`. |
| `layers.toml` | Registers `tests/scripts/test_run_bump_proposer.py` in `bridge`. `tests/bump_proposer/**` and `lanes/**` already covered the rest. |
| `docs/operations/deploy.md` | The lane's section, beside the work carrier's, which is untouched. |
| `docs/operations/credentials.md` | The append password's holder row. |

`scripts/lane_run.py` didn't change. It already passes `BUMP_PROPOSER_STATE_DIR`
(`STATE_VARIABLES`) and the three chain settings (`CHAIN_SETTINGS`), and the launcher derives
everything else itself, so the container needs no new variable. A test holds that every compose
variable except `LANE_SCHEDULE_ENABLED` is in `lane-run`'s list.

## Each secret and how the container reads it

Every read uses `sds_bws_value` with one argument, so the lane's one `BWS_ACCESS_TOKEN` stays in
the environment and is never a process argument. No Keychain item is read and `gh` isn't called.
The split between passes is the Mac's.

| Secret | UUID | When | Reaches |
| --- | --- | --- | --- |
| change-manager READ | `314f276d-…` | Every pass | `BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN` |
| orchestrator OBSERVER | `f793576f-…` | Every pass | `SDS_MEMBERS_OBSERVER_TOKEN`; on `--submit` also `ORCHESTRATOR_API_TOKEN` with key id `orchestrator-observer` |
| `sds-bridge` alert sender | `b415d03b-…` | Every pass (as on the Mac) | `SDS_BRIDGE_ALERT_TOKEN` |
| read-only App key | `314a966c-…` | Every pass | Piped from `bws` straight into `sds-github-mint`'s stdin; never a variable, argument or file |
| change-manager PROPOSE | `acccb346-…` | `--submit` | `BUMP_PROPOSER_CHANGE_MANAGER_TOKEN` |
| factory-events append password | `2ce0dbc7-…` | `--submit` | `FACTORY_EVENTS_APPEND_PASSWORD`, as an assignment prefix on `bump-proposer` alone, never exported |
| "SDS lanes" App key | `b8f547b9-…` | `--submit` | Piped straight into `sds-github-mint --allow-write` |

An unreadable secret fails the pass with exit 1 before the program runs, naming the secret (one
test per secret). The launcher runs `unset BWS_ACCESS_TOKEN` once every read and both mints are
done, before git or any program runs; the subcommand and both mints run under
`env -u BWS_ACCESS_TOKEN`. Tests hold that no program and no git process gets the lane's token and
that no secret's value appears in the output.

## The two GitHub tokens and the clone

- **The read token**, every pass. `bump-proposer repositories` prints `AlobarQuest/intent-packages`
  and each repository the member record grants `dependency_updates`, case-folded once. It's a
  superset of what a pass reads: `_lane` narrows the grants by the standing packages and the
  landing policy, and the packages are read from the clone this token makes, so the list can't
  wait for them. The derivation needs only the observer bearer, as the carrier's `work-watcher
  repositories` needs only its own. An unreadable member record exits 2 with nothing on stdout,
  and the launcher passes the code through, mints nothing and puts the subcommand's reason in the
  failure message. The mint asks the read-only App (`5684758`) for `metadata`, `contents`,
  `pull_requests` and `actions`, all read. Those are what the pass's reads use:
  `landing_ledger/github.py`'s `default_branch` (repository), `read_pending_updates` (pull
  requests, their first commit) and `_concluded_checks` (Actions runs and jobs), plus the clone.
  `GET /repos/{r}` needs only `metadata`, so `contents` serves the clone alone and is needed only
  on intent-packages. One mint carries one permission set, so the token holds `contents: read` on
  every granted repository. A second, narrower mint for the clone would remove that; I didn't add
  one. The token is `BUMP_PROPOSER_GITHUB_TOKEN`, which `GitHubReader` reads.
- **The push token**, `--submit` only. The mint asks the "SDS lanes" App (`5684778`) for
  `AlobarQuest/intent-packages` alone, `metadata=read` and `contents=write`, with `--allow-write`.
  It's held in the launcher's own unexported `_PUSH_TOKEN` until `sds_git_credential` takes it,
  then unset. No variable a program reads holds it: a test checks that the only variable
  `bump-proposer` receives with that value is git's `GIT_CONFIG_VALUE_0` (the base64 extraheader).
  The ADR accepts that every process of the pass shares git's environment (decision 2:
  `standing._run` passes no `env`, so the publish inherits the helper's variables).
- **The clone.** `sds_git_credential BUMP_PROPOSER_GITHUB_TOKEN`, then `git clone --quiet --depth 1
  --single-branch --branch main https://github.com/AlobarQuest/intent-packages.git` into
  `/var/tmp/sds-bump-proposer/intent-packages`. The lane user creates the directory (mode 0700)
  with no `lanes/Dockerfile` change, which assumes the image's `/var/tmp` is world-writable, as
  Debian's is. I didn't check the image itself; cut-over step 8's bare run proves it. The
  previous pass's clone is removed first, and this pass's clone is removed at the end.
  `BUMP_PROPOSER_PACKAGES_CHECKOUT` points at it. On `--submit` the launcher then calls
  `sds_git_credential _PUSH_TOKEN 'alobar-sds-lanes[bot]'
  '354576843+alobar-sds-lanes[bot]@users.noreply.github.com'`. That replaces the read header with
  the push header (one extraheader) and sets the identity pairs, so the revision commit and its
  publish are the App's. A dry run keeps the read header and sets no identity: it commits nothing.
- **The bot user ID** `354576843` is the `alobar-sds-lanes[bot]` account's. HQ read it on
  2026-11-17 from the unauthenticated `GET https://api.github.com/users/alobar-sds-lanes%5Bbot%5D`
  (login `alobar-sds-lanes[bot]`, type `Bot`). The App's own public page
  (`/apps/alobar-sds-lanes`) returns 404, so the link between that bot account and App ID
  `5684778` can't be checked without authentication. The first acting pass's commit on
  intent-packages shows it: GitHub must attribute it to `alobar-sds-lanes[bot]`.

## The `.venv` (decision: no venv in the clone)

ADR-0059's increment 4 asks that the clone get a venv for `standing._interpreter`, built as
decision 1 builds the bump driver's (`uv sync`). I didn't build one, because a venv built that way
can't approve. HQ accepted this choice on 2026-11-17. The evidence:

- `approve --by-policy` emits through intent-packages' `FactoryEventsEmitter`. Its interpreter is
  `<SECURITY_STANDARDS_DIR>/.venv-events/bin/python` if that exists, else `sys.executable`
  (intent-packages `src/intent_packages/emitter.py`, `_events_python`). The image's
  `/app/security-standards` holds only `src`, `registry` and `schema` (the orchestrator
  `Dockerfile`'s three `COPY --from=registry` lines), so the emitter runs under the lifecycle's own
  interpreter.
- `factory_events` imports `jsonschema` and `psycopg` (security-standards `src/factory_events/`).
  intent-packages declares only `httpx` and `pyyaml` at runtime. `psycopg` is only in its dev group,
  as a test fixture, and `jsonschema` isn't declared at all. The Mac's intent-packages `.venv`
  has no `jsonschema`; the Mac works because its security-standards has a `.venv-events`.
- So instead the container runs the lifecycle under the image's interpreter:
  `BUMP_PROPOSER_PACKAGES_PYTHON=/app/.venv/bin/python` (`standing._interpreter`'s existing
  override) and `PYTHONPATH=<clone>/src`, as assignment prefixes on `bump-proposer` alone, every
  container pass, and `SECURITY_STANDARDS_DIR=/app/security-standards`. That's how
  `orchestrator/package_sources.py` already runs `intent_packages verify-approval` for the carrier
  (`sys.executable` with intent-packages' `src` on `PYTHONPATH`). The orchestrator's `uv.lock`
  carries all four packages at versions inside intent-packages' floors (`httpx` 0.28.1, `pyyaml`
  6.0.3, `jsonschema` 4.26.0, `psycopg` 3.3.9), and both run Python 3.14.
- **Checked:** from a `git archive` of intent-packages `main`, `PYTHONPATH=src
  <worktree>/.venv/bin/python -m intent_packages hash packages/change-manager-fastapi-bump`, run
  under `env -i`, printed `57dfef2b…a5c2`, the hash fixture's value for that package. I ran `hash`
  only, never `approve`.
- **Checked, the emit side:** with `PYTHONPATH` at the `src` of security-standards at this
  repository's pin (`08754a0f`, from `git archive`), the worktree's `.venv/bin/python` imports
  `factory_events.db`, `factory_events.envelope`, `psycopg` (3.3.9) and `jsonschema` under
  `env -i`, and `python -m factory_events emit --help` exits 0. I ran `--help` only: a real emit
  with no chain variables would write a file chain. The first real append is the first acting
  container pass.
- No `uv` runs, so no `UV_CACHE_DIR` or `UV_PYTHON_INSTALL_DIR` is needed and nothing resolves
  unlocked packages each pass. (intent-packages' `.gitignore` lists `uv.lock`, so a clone's
  `uv sync` would have resolved fresh every pass.) A test holds that no `uv` runs and that the Mac
  sets neither variable.
- `FACTORY_AGENT_ID` is unset on both hosts, so the container's approvals carry the emitter's
  default actor, `claude-code-interactive`, as the Mac job's do today (`com.devon.bump-proposer.plist`
  sets only `PATH`, `HOME` and `BUMP_PROPOSER_PACKAGES_CHECKOUT`).

## The chain settings and network

The compose sets `FACTORY_EVENTS_DB_HOST: b82fa1vg5whecgnjreuqb5tk`, `FACTORY_EVENTS_DB_PORT:
"5432"` and `FACTORY_EVENTS_DB_NAME: postgres`, and joins the external `coolify` network, as the
work carrier's does. Every container pass, a dry run included, fails with exit 1 before any read
if one is empty (a test per setting and mode). The launcher copies them into its own variables
and unsets the ambient chain names. Only a `--submit` pass hands the three settings and the append
password to `bump-proposer`, as an assignment prefix. A dry run approves nothing, so it gets no
chain variable, as on the Mac. There's no switch file and no file mode in the container.

## The crontab (decision: 06:55)

`55 6 * * * lane-run bump-proposer --submit`. The Mac job runs at 06:50, but minute 50 is the
rotation decomposer's (`50 * * * *`), which the handoff's "unless another lane's crontab uses that
minute" covers, and `test_no_two_lanes_start_a_pass_in_the_same_minute` forbids. 55 is the
nearest free minute, and 06:55 still runs before the carry's 07:05, the order the launcher's
header gives. HQ accepted this choice on 2026-11-17. The plist is unchanged.

**The line carries a flag.** Like the carrier's `--register`, `--submit` is in the crontab line,
so a scheduled pass writes. A plain `lane-run bump-proposer`, which HQ's cycles may run, is the
dry run: it writes nothing and pings nothing.

## The state volume

`sds-lanes-bump-proposer-state`, mounted at `/var/lib/sds/bump-proposer`, which the image already
creates owned by `orchestrator`. `BUMP_PROPOSER_STATE_DIR` points at it. It holds `alerts.json`
(ADR-0059 decision 1, "Lane state"). The launcher fails a container pass with no
`BUMP_PROPOSER_STATE_DIR`, rather than taking the Mac's default. The Mac's
`~/Library/Application Support/sds/bump-proposer/alerts.json` must be copied in at cut-over before
the first `--submit` pass, or the alerts the Mac left firing never resolve.

## The paired pass: why both hosts never submit

ADR-0059, "Cut-over for one lane" (lines 1091-1170 at this base):

- Until cut-over step 3, the container runs no scheduled pass. The compose defaults
  `LANE_SCHEDULE_ENABLED` to `false`, and the entrypoint then runs a comment-only crontab
  (`lanes/entrypoint.sh`). The resource is created without deploying (step 1).
- Step 2's comparison runs the reporting mode on both hosts within one minute. For the bump
  proposer that's the bare invocation, which "pings nothing" (ADR line 1139-1140: "an
  outcome-alarm launcher run without its acting flag ... bump proposer"). It also submits nothing,
  commits nothing and holds no write credential. Both dry runs read the same `main`, provided the
  Mac's checkout is fast-forwarded first. `deploy.md` says so.
- Step 2 boots out the Mac agent (`launchctl bootout`) only once no pass is running. Only then does
  step 3 set `LANE_SCHEDULE_ENABLED=true` by a quiet-slot redeploy. "The Mac agent and the
  container never run the same lane at once" (ADR line ~1170).
- The volume holds no pass lock (only the bump driver has one), so the bootout order is the only
  exclusion. A hand `lane-run bump-proposer --submit` while the Mac agent is loaded would be a
  second submitting host. Nothing in this change prevents that, which is why the cut-over steps
  are ordered as they are.

## Report, don't fix

### Readers keyed on a revision commit's author (ADR lines 1406-1409)

**Answer: a commit by `alobar-sds-lanes[bot]` changes no reader's decision.** I searched
orchestrator `src`, intent-packages (`src`, `scripts`, `.github/workflows`) and change-manager
(`app`, workflows).

| Reader | Keys on | Effect of the App's commit |
| --- | --- | --- |
| `landing_ledger/github.py` `read_landing` (~474) | A direct push has no merged pull request; the landing takes `landed_at` from `commit.committer.date`, with no `landed_by`. | None: a date, not an identity. |
| `landing_ledger/record.py` `basis_of` (~361) and `is_machine` (~224, `endswith("[bot]")`) | `is_machine` reads `landed_by`, which comes from `merged_by` on a pull request alone. A landing with no pull request returns the basis `none` before `is_machine` is asked. | None. A direct push stays basis `none` ("pushed to the branch with no pull request"), which no audit detector reads. The `[bot]` suffix would matter only for a pull request the App merged, which this lane never makes. |
| `revision_watcher/github.py` (~87), `activation_sweep/checkout.py` (~310-317), `tool_installer/github.py`, `pin_watcher/github.py` | The committer date, parsed as an ISO 8601 instant. | None. |
| `change_proposer/cli.py` (~452), `change_proposer/reverts.py` (~38), `deploy_watcher/github.py` (~229), `orchestrator/services/landing/*` (`terms.py` ~556, `estate_landing_admission.py`, `estate_pr_merge.py` ~429-455, `revert_act.py`) | Pull request authors (`dependabot[bot]`, the dispatch App, `permitted_authors`), and the commits of a pull request under admission. | Out of reach: pull requests on member repositories only, never a direct push to intent-packages. |
| change-manager `app/deploy_policy.py` (`DEPENDABOT`, `FORK_SECURE_UPSTREAM_SYNC`, `DISPATCH_APP`, ~109-134) | `permitted_authors` of pull requests. | None: no commit reader, and `alobar-sds-lanes[bot]` is on no list. |
| `fork_sync/acknowledge.py`, `fork_sync/onboard.py` | Bot names on fork pull requests and comments. | Out of reach. |
| intent-packages workflows (`quality.yml`, `validate.yml`, `routing-policy-compatibility.yml`) | Nothing on `github.actor`, a pusher or a `[bot]` login; only `hashFiles` conditions. | None. An installation-token push does start push workflows, unlike a `GITHUB_TOKEN` push, so they keep running on the App's commits. |

intent-packages' own "author" fields (`lineage.py`, `approval_policy.py`) are package-author
strings in YAML, not git identities. Whether the landing ledger observes intent-packages at all
depends on its member state, which I didn't read live. If it does, it records basis `none` today
and still does.

### intent-packages branch protection (ADR lines 1397-1404)

Not changed and not raised. intent-packages' `main` is under classic protection with three
required checks and `enforce_admins` off, which lets Devon's admin push land today. The "SDS lanes"
App isn't an admin, so its direct push is refused until Devon admits it, for example as a bypass
actor of a ruleset that replaces the classic rule. That's cut-over step 5 in `deploy.md`, before
the first acting pass. A refused publish is a finding (exit 3) that names the stranded sha, and in
the container it costs more than on the Mac, because of the order of the steps:

- `advance` runs `approve --by-policy`, which appends a `package.approved` event, before
  `commit` pushes (`cli.py` ~638-642; `standing.py` `advance`, `commit`, `publish`). A refused
  push leaves that event in the append-only chain for a revision that exists in no repository.
  `factory_events emit` appends without deduplication.
- On the Mac the stranded commit stays in the checkout, so `require_publishable` refuses every
  later pass until a person reconciles it. That stops a second approval. In the container the
  clone is discarded, so the next pass clones `main`, revises again and approves again. Each daily
  pass adds another orphan approval until the push is admitted.
- So the branch-protection change must land before the first acting container pass (`deploy.md`
  step 5 says so). A race, where `main` moves between the clone and the push, has the same effect
  once; the window is one pass long.

I left `standing.py` alone, as the ADR says it doesn't change. A brake that survives the discarded
clone (for example a refusal recorded in the lane's volume) is a design question for HQ.

### The anonymous infraops clone (ADR lines 1034-1036)

The bump proposer doesn't touch it: it clones intent-packages alone. That clone belongs to the
rotation proposer (step 3f-5).

## Tests and checks

- `make check`: green, **15,334 passed, 1 skipped** (final run on `a0ceb5c6`'s tree). Lint, type-check, shellcheck and
  the whole-repository scans are included.
- Some of my targeted pytest runs had no `TEST_DATABASE_URL` set, so they would have defaulted
  to the shared `orchestrator_test`. `pytest --setup-plan` over those paths lists no database
  fixture (count 0), so none of them touched it. Every `make check` ran against
  `orchestrator_test_hq_w_202`.
- The existing Mac-path tests pass with no edits: `tests/scripts/test_bridge_launchers.py`,
  `tests/scripts/test_bridge_chain_switch.py` and `tests/scripts/test_packages_checkout_activation.py`.
- `tests/scripts/test_run_bump_proposer.py` (52 tests) runs the real launcher under `env -i` with
  stubs for `bws`, `git`, `gh`, `uv`, `curl`, `bump-proposer` and `sds-github-mint`. It covers the
  container branch; each secret read with the one token on the right passes; each unreadable
  secret; no program or git process getting the lane's token; the read mint's arguments and the
  push mint's, the latter only on `--submit`; the push token only in git's extraheader and in no
  argv; the identity pairs; a dry run keeping the read header; the derivation's codes and empty
  list; failed mints and clones; the clone's removal; each missing chain setting in both modes; the
  chain variables reaching the program only on `--submit`; the interpreter override with no `uv`;
  the program's exit code; the Keychain and `gh` only on the Mac branch; the Mac path minting,
  cloning and overriding nothing; and the compose, crontab, start minute and manifest rows.
- `tests/bump_proposer/test_repositories.py` (8 tests): the subcommand's list, case-folding,
  failure with nothing on stdout, the dispatch, and the guarantee that a pass's scope is always
  inside the list.
- **Mutation check:** five launcher mutations, each run against the new test file. All five made
  a test fail:
  1. Drop `unset BWS_ACCESS_TOKEN`.
  2. Export the push token as `BUMP_PROPOSER_GITHUB_TOKEN`.
  3. Check only one chain setting.
  4. Drop `PYTHONPATH`.
  5. Drop the `actions` permission.
- `/code-review` (medium) raised six findings. I fixed two: the subcommand's stderr now reaches the
  failure message (with a test), and its unused `out` parameter is gone. The other four I left, as
  follows:
  - A clone left by a failed pass is removed by the next one. The carrier has the same property,
    and the clone holds no credential.
  - Container mode is keyed on a set `BWS_ACCESS_TOKEN`. That's the carrier's ADR-chosen pattern,
    and the plist sets no such variable.
  - The crontab comment names every lane's minute, as the carrier's does, and
    `test_no_two_lanes_start_a_pass_in_the_same_minute` holds the fact.
  - The empty ping-URL placeholder is the handoff's design, held by `placeholders`.

## Self-review

- The Mac branch was re-indented inside `else`; its statements are byte-for-byte the old ones
  apart from the indent, and its tests pass unedited. The `unset ... CHAIN_MODE="file"` lines moved
  above the branch, which on the Mac also clears `PACKAGES_CLONE` and `_PUSH_TOKEN`, two names it
  never used.
- My first wording of a comment spelled out the publish command, and the merge guard
  (`test_wsp21_invariant_scan.py`) flagged it. I reworded the comment rather than exempting the
  file.
- `repositories()` lists every granted repository, not the narrower lane. So the read-only App
  must be installed on every repository granted `dependency_updates`, or the mint refuses and the
  pass fails. The first bare container run at cut-over proves the installation covers them.

## Concerns and follow-ups

- **A refused publish in the container leaves an orphan approval each pass** and has no brake
  (the branch protection section). The branch-protection change before the first acting pass
  is what prevents it; HQ may want a lasting brake.
- **The work carrier's text is now stale, and the handoff forbids me from editing it.** Its
  `deploy.md` section says it is "the one lane whose compose joins the external `coolify`
  network", and `lanes/work-carrier/compose.yaml`'s header says "unlike the other lanes". The bump
  proposer's compose now joins it too.
- **`contents: read` reaches every granted repository**, not only intent-packages, because one
  mint carries one permission set (the GitHub tokens section).

- **ADR-0059 increment 4's verify item** (lines 1030-1033) says each bridge proposer "needs a venv
  in its clone". This lane doesn't build one, for the reasons in the `.venv` section. HQ may want a
  dated line in the ADR recording that. The rotation proposer (3f-5) reaches the same `lifecycle`
  through `standing`, so the same reasoning applies to it, though it emits no approval.
- **The bot account's link to App `5684778`** is unverified without authentication. The first
  acting pass's commit attribution is the check.
- **The emitter's actor** stays `claude-code-interactive` on both hosts, as today. Changing it
  would be a change to permanent chain attribution, so I left it alone.
- **The `Standards` section doesn't cover dependency resolution at runtime.** I avoided the
  question by not running `uv`.

## Cut-over steps that remain

These are in `deploy.md`, in this order, each its own change or act:

1. The orchestrator serving this release; HQ creates `sds-lane-bump-proposer` from the compose
   without deploying it.
2. The VM root's keeper commands for the lane token and the lane's ping-URL secret.
3. The lane's registry rows (`.cred-consumers.toml`), in two changes: the token's and the ping
   URL's. In the same work, `runtime-fetch` readers for `scripts/run-bump-proposer.sh` on both App
   key rows, and the append password's row naming the container read.
4. The lane account's grants on its seven secrets and its ping-URL secret. **The broad-identity
   grants:** the change-manager READ (`314f276d-…`) and PROPOSE (`acccb346-…`) bearers, which the
   Mac reads through the broad identity, each become a direct grant to the lane account (Devon's
   act at the identity gate). The push App's key (`b8f547b9-…`) gets its first grant to any
   account.
5. **Devon's change to intent-packages' `main` protection** that admits the "SDS lanes" App's
   direct push.
6. The token's mint and the executor's write; then the ping URL's row made due and its write.
7. A served release whose compose names the ping-URL secret's id, with `bump-proposer` removed from
   `placeholders`.
8. The lane's `--add`, the `lane-run --read` proof over its eight secrets, and a bare
   `lane-run bump-proposer` in the container.
9. Cut-over steps 2 onward:
   1. Copy `alerts.json` into the volume.
   2. Fast-forward the Mac's checkout, then run the paired dry runs.
   3. Boot out the Mac agent, then turn on the schedule.

## Standards followed

| Standard | How this change meets it |
| --- | --- |
| Code Brain 3: structured JSON logs to stdout | The lane's process output is supercronic's JSON (`-json`) and the launcher's outcome report, as for every lane. This change adds no log file. |
| Code Brain 4: no silent swallow; no `\|\| true` hiding a failed BWS read, clone, mint or `uv sync` | Every BWS read, both mints, the derivation, the clone, the state directory and both `sds_git_credential` calls end in `sds_outcome_fail`. The container branch has no `\|\| true`. The only tolerated failure is the end-of-pass clone removal, which logs a line to the report and says why (no credential in the clone). No `uv sync` runs. |
| Code Brain 5: never log secrets | The App keys go `bws` to the mint's stdin, never into a variable, argument or file. Both tokens reach git only through `GIT_CONFIG_VALUE_0`, never argv. `sds-git.sh` unsets git's trace variables. git's clone message is kept but never holds the header. Tests assert no secret value, token or lane token in the output. The append password is never exported; `standing.py` redacts it from child errors. |
| Infra Brain 2: no committed secrets or `.env` files | Only UUIDs, App IDs, the bot's public user ID and non-secret chain settings are committed. |
| Infra Brain 3: no `:latest` tag | The image is `ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}`, a pinned tag the lane phases set. |
| Infra Brain 212: HTTPS | The lane has no FQDN. Its calls (GitHub, change-manager, the orchestrator, alert-hub, BWS) are HTTPS; the chain connection is in-network Postgres on `coolify`, as the carrier's is. |
| Infra Brain 591, 592: no BWS token in tracked files or history | None is written. The compose maps `${BUMP_PROPOSER_BWS_ACCESS_TOKEN}` only. |
| Infra Brain 593: the bootstrap token never inline | `BWS_ACCESS_TOKEN: ${BUMP_PROPOSER_BWS_ACCESS_TOKEN}`, from the resource variable only. A test pins the compose's whole environment. |
| Infra Brain 1032: exposed credentials rotate now | Nothing was exposed. No live secret was read in this session. |
| Infra Brain 4 (deployment): no Coolify scheduled task beside an in-container scheduler | The schedule is `lanes/bump-proposer/crontab`, run by the entrypoint's supercronic. No Coolify scheduled task. |
