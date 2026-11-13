Outcome: done

# The change proposer's lane files and launcher (ADR-0059 increment 4, step 3f-1)

Worker W-124, branch `hq/lanes-inc-4-change-proposer`, base `5ed15482`. PR #686, ready for
review, not merged.

## What I built

- **`change-proposer repositories`** (`src/change_proposer/cli.py`, `_print_repositories`). The
  subcommand reads the member record with the observer bearer. It prints every repository the
  pass's GitHub calls read, one per line, and exits 2 with nothing on stdout when the record is
  unreadable or the list is empty.
- **The launcher's container branch** (`scripts/run-change-proposer.sh`). With `BWS_ACCESS_TOKEN`
  set, the launcher does the following:
  - Skips activation.
  - Reads the observer bearer every run and the propose bearer only on `--submit`, both with the
    lane's one token.
  - Derives the repositories. When it can't, it exits 2 with no mint; any other code passes through.
  - Mints `CHANGE_PROPOSER_GITHUB_TOKEN` each pass, dry run included, under
    `env -u BWS_ACCESS_TOKEN`. A preset value is replaced.
  - Never asks `gh`.
  - Unsets `BWS_ACCESS_TOKEN` before the program runs.

  The dry-run default, the exit codes and the outcome alarm (`sds_outcome_begin
  sds-change-proposer change-proposer "$SUBMITTING"`) are unchanged. The Mac branch is the old code
  verbatim: the broad identity reads the propose bearer on `--submit`, the narrow identity reads the
  observer bearer, and `gh auth token` is the fallback.
- **`lanes/change-proposer/compose.yaml`.** One service, modeled on the deploy watcher's.
  `BWS_ACCESS_TOKEN` maps from `${CHANGE_PROPOSER_BWS_ACCESS_TOKEN}`. `LANE_SCHEDULE_ENABLED`
  defaults to `false`, `SDS_DEADMAN_PING_URL_UUID` is an empty placeholder (explained in a comment),
  and `restart: unless-stopped`. It has no volume, ports, networks or labels.
- **`lanes/change-proposer/crontab`.** `35 * * * * lane-run change-proposer --submit`.
- **`.bws-secrets.toml`.** `change-proposer` is added to the `lanes` of the observer row
  (`f793576f-…`) and the read-App key row (`314a966c-…`), and a new `lanes = ["change-proposer"]`
  is on the propose row (`acccb346-…`). Comments name the container reader. No grant was made.
- **`.cred-consumers.toml`.** On `lanes-read-app-private-key`, one `runtime-fetch` reader for
  `scripts/run-change-proposer.sh`, with `healthchecks_check = "sds-change-proposer"`, as #675 added
  for the membership check, plus the block comment. Nothing else.
- **`docs/operations/deploy.md`.** "Registered lanes" has a change-proposer paragraph with its eight
  remaining cut-over steps, and "A lane's GitHub token" has its mint.
- **`docs/superpowers/reports/lanes-inc-4-change-proposer-question-answered.md`.** The scope
  question and HQ's answer.

The image needs nothing new. `lanes/Dockerfile` copies `scripts/run-*.sh`, `sds-*.sh` and
`lane_run.py`. `change-proposer` and `sds-github-mint` are `[project.scripts]` console scripts, so
they're in the orchestrator image's venv at `/app/.venv/bin`, which the lane image inherits (the
deploy watcher's and membership check's lanes already run both forms). `lane-run` passes
`--submit` through verbatim: `run_launcher` runs `[launcher, *arguments]`, which a new test pins.
`layers.toml` already covers every touched file (`lanes/**`, `tests/scripts/test_run_change_proposer.py`).

## Scope: the mint's repositories (HQ's answer, option A)

The handoff gave the scope as the member record's `rollouts` grants, narrowed to transcribed
workflows and rollback plans. The tool's GitHub reads reach past that scope: `_scope_sweep` audits
every `ROLLOUT_WORKFLOWS` key whatever its grant. I asked, and HQ answered option A (the question
and answer are in `lanes-inc-4-change-proposer-question-answered.md`).

So, as HQ answered, the mint names the union of `_in_scope(members)` and the sweep's population.
This is ADR-0059 decision 2's "repositories its calls read", and it matches the deploy watcher's
mint (#653). Because `_in_scope` iterates `ROLLOUT_WORKFLOWS`, the scope is a subset of the table,
so the union is today the table's keys: `alobarquest/brain` and `alobarquest/change-manager`. The
derivation still reads the record, so an unreadable record mints nothing, as it proposes nothing
(exit 2). `_scope_sweep` isn't narrowed.

## Every GitHub call and the permission it needs

All calls go through `deploy_watcher.github.GitHubReader`, which refuses every verb but GET.

| Caller | Call | Permission |
|---|---|---|
| `_pass` (`open_pull_requests`) | `GET /repos/{r}/pulls?state=open&per_page=100` | `pull_requests: read` |
| `_consider` and `_scope_sweep` (`blob_revision`) | `GET /repos/{r}/contents/{path}?ref={branch}` | `contents: read` |
| `_retire_one` (`pull_request_disposition`, `--submit` only) | `GET /repos/{r}/pulls/{n}` | `pull_requests: read` |
| every call | the repository itself | `metadata: read` (granted with every token) |

`criteria.py`, `factory_marking.py` and `reverts.py` make no GitHub call (`reverts.py` reads the
orchestrator). No call reaches `actions`, so the mint doesn't ask for it. Everything is within the
read App's four permissions; no widening is needed.

## The mint's exact arguments

```sh
sds_bws_value 314a966c-5654-4e3c-8358-b4fd01268775 \
  | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id 5684758 \
      --repository alobarquest/brain --repository alobarquest/change-manager \
      --permission metadata=read --permission contents=read --permission pull_requests=read
```

The repositories come from `change-proposer repositories` at run time, never from the shell; the
two listed are what it prints today. The key goes from the BWS read to the mint's stdin and is
never in a variable, argument or file.

## The crontab minute

Minute 35, hourly. Every other lane's start minutes:

- execution-watch: 0, 15, 30 and 45
- unit verifier: 2-52/10
- deploy watcher: 20
- revision watcher: 40
- pin watcher, landing ledger and membership check: daily at 07:25, 07:30 and 07:47

35 is clear of all of them, and `test_no_two_lanes_start_a_pass_in_the_same_minute` holds it. The
Mac job is `StartInterval 3600` with no fixed minute, so no minute matches it better.

## The outcome alarm with no ping URL

The compose's `SDS_DEADMAN_PING_URL_UUID` is empty, so `sds_deadman_resolve` falls back to the
Mac's lookup by check name. That needs the broad identity, which `lane-run` doesn't pass and the
image's missing `/usr/bin/security` can't supply.

On a `--submit` pass, `sds_outcome_begin` logs `no SDS_DEADMAN_PING_URL_UUID and no broad BWS
identity — alerting disabled this run`, and the pass still runs. Every ping is a no-op, and the
exit code is the program's. A dry run resolves no URL at all and pings nothing, as on the Mac.
`test_without_a_ping_url_secret_a_submitting_pass_still_runs_and_pings_nothing` covers it.
`change-proposer` is in `test_deploy_lanes.py`'s `placeholders` until a later change writes the
keeper's id.

## Tests and checks

- **`make check`:** green, with ruff, ruff format, pyright, and pytest at 14,767 passed and
  1 skipped. Run against `orchestrator_test_hq_w_124`, with `SECURITY_STANDARDS_DIR` set to the
  fixture and `PGTZ=UTC`.
- **`shellcheck scripts/run-change-proposer.sh`:** clean.
- **New tests in `tests/change_proposer/test_change_proposer.py`:**
  - The subcommand prints the union, including a transcribed repository whose grant is withdrawn.
  - An unreadable record exits 2 with empty stdout.
  - An empty table exits 2.
  - The subcommand needs no GitHub token.
  - The printed names pass `sds-github-mint`'s request checks.
- **New tests in `tests/scripts/test_run_change_proposer.py`** run the real launcher under `env -i`
  with stubbed `bws`, `gh`, `curl`, tool and mint:
  - the exact mint call and its stdin;
  - the derivation and mint running without the lane token;
  - every read made with the lane's one token;
  - the propose bearer read only on `--submit`, and a dry run that still mints and pings nothing;
  - no `gh` in the container, and a preset token replaced;
  - an unreadable bearer or key, and a failed or empty mint, each exiting 1;
  - an underivable list exiting 2 with no mint and no key read, and other derivation codes passed
    through;
  - the no-ping-URL pass, and the program's code as the pass's code;
  - the Mac path unchanged, with no derive and no mint;
  - the compose against `add_refusals`, and the crontab against the plist;
  - `lane-run` passing `--submit` through;
  - the minute clash check, and the manifest's lane secrets all named in the launcher.
- **Changed tests:**
  - `test_bridge_edges.py`: the change proposer's bearers include the read App's key.
  - `test_deploy_lanes.py`: `placeholders = {"change-proposer"}`.
- **Mutation controls on the launcher**, each run against the launcher tests and then restored from
  `HEAD`. Ten mutants; nine were caught:
  - the mint keeps the lane token;
  - `pull_requests` is dropped;
  - `actions` is added;
  - an underivable list exits 3;
  - the propose bearer is read on a dry run;
  - the unset before the tool is removed;
  - the derivation keeps the lane token;
  - the mint runs on a scope failure;
  - a preset GitHub token is kept.

  One survived: a `gh` fallback inserted after the mint. It's an equivalent mutant: a failed or
  empty mint already exits 1 through `sds_outcome_fail` before that line, so the fallback can never
  run. The real "falls back to `gh`" case, `gh` instead of the mint, is caught by the mint-call
  test, and every container test asserts `gh` is never called.
- **`/code-review` (medium) found six items; none needed a change:**
  - The `BWS_ACCESS_TOKEN` host switch is every moved lane's established switch.
  - The union is HQ's answer, and its subset fact is recorded in the preceding scope section.
  - Exact-argv dispatch fails closed (exit 2, no mint), and the launcher calls only the bare form.
  - The "bare names" finding is wrong: the names are `owner/name`, and a test checks them against
    the mint.
  - The derivation's stderr not reaching the `/fail` body is the same as the other lanes'
    derivations; see the follow-ups.
  - The empty placeholder is the handoff's, and cut-over step 6 removes it.

## Files changed

- `src/change_proposer/cli.py`
- `scripts/run-change-proposer.sh`
- `lanes/change-proposer/compose.yaml` (new)
- `lanes/change-proposer/crontab` (new)
- `.bws-secrets.toml`
- `.cred-consumers.toml`
- `docs/operations/deploy.md`
- `tests/change_proposer/test_change_proposer.py`
- `tests/scripts/test_run_change_proposer.py`
- `tests/scripts/test_deploy_lanes.py`
- `tests/architecture/test_bridge_edges.py`
- `docs/superpowers/reports/lanes-inc-4-change-proposer-question-answered.md` (new)
- this report

## Self-review

- `run` dispatches the exact argv `["repositories"]` before argparse and otherwise calls
  `_propose`, which is the old body unchanged. The split keeps `run` under ruff's complexity limit.
- In the container, the order is: propose bearer (on `--submit`), observer bearer, derivation, then
  mint. A bearer failure stops before the derivation, and a derivation failure stops before the
  key is read.
- On a dry run in the container the observer bearer and the key are read and a token is minted.
  That's needed: the dry run reads GitHub. Nothing is written and nothing pings.
- No new credential, scope, permission or grant was created. No live call was made: no BWS, GitHub,
  Coolify, VPS, change-manager or Healthchecks call.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files).** The launcher adds no log file.
  The pass's report goes to `$SDS_OUTCOME_REPORT` and stdout through the existing outcome-alarm
  helper, which supercronic's `-json` wraps in the container. The subcommand prints only names to
  stdout and its reason to stderr, as the other lanes' derivations do.
- **Code Brain rule 4 (no silent swallow, no `|| true` hiding a failed mint or BWS read).** Every
  BWS read in the container branch is checked for empty and fails through `sds_outcome_fail` with a
  named reason. The mint checks its exit code and an empty answer. The derivation checks its code
  and an empty list. The only `|| true` in the launcher is the Mac branch's unchanged Keychain and
  `gh` reads, each followed by an explicit empty check that fails loudly.
- **Code Brain rule 5 (never log secrets, credentials, auth headers, tokens).** No value is echoed.
  The key goes straight from `bws` to the mint's stdin, and the minted token is only in the
  environment. A test asserts that none of the lane token, the minted token, the bearers or the key
  appears in the launcher's stdout or stderr.
- **Infra Brain 212 (HTTPS).** The lane has no FQDN. Its calls go to `https://sds.alobar.net`,
  `https://change-mgr.alobar.net` and `https://api.github.com` through the existing clients.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history).** No token is written
  anywhere. The files hold BWS secret UUIDs only, and the BWS write-guard raised nothing.
- **Infra Brain 593 (no inline bootstrap token).** The compose maps `BWS_ACCESS_TOKEN` from
  `${CHANGE_PROPOSER_BWS_ACCESS_TOKEN}`, the resource variable, and holds no value. The launcher
  reads the token only from its environment and passes it to no process argument.
- **Infra Brain 1032 (rotate an exposed credential).** No credential was surfaced in logs or this
  transcript; I read no secret.
- **Deployment (no Coolify scheduled task beside an in-container scheduler).** The schedule is the
  tracked crontab, run by supercronic only when `LANE_SCHEDULE_ENABLED` is `true`. No Coolify task
  was made.

## Concerns and follow-ups

- **The derivation's reason isn't in the `/fail` body.** `change-proposer repositories` writes
  `[UNUSABLE] …` to stderr, which reaches the container log but not `$SDS_OUTCOME_REPORT`, so a
  `--submit` ping says "could not be derived" without naming an unreadable record against an empty
  table. The membership check's and landing ledger's derivations behave the same. Fixing it is one
  shared change across the lanes, not this one alone.
- **The union is the table today.** If `_in_scope` ever returns a name outside `ROLLOUT_WORKFLOWS`,
  the mint widens to it. That's by design ("every repository its calls read"), but it's the one
  place the mint's reach follows a code change rather than the record.
- **All the mint's repositories need the read App installed and one owner.** If a transcribed
  repository leaves the members (the App is on the 14 members) or a second owner appears, the mint
  refuses and every pass exits 1. Cut-over step 7's first pass proves the installation covers both.

## Cut-over steps that remain

These are also in `deploy.md`. Each is its own change or act.

1. The orchestrator serving this release; then HQ creates `sds-lane-change-proposer` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `change-proposer-ping-url`.
3. This repository's registry rows, in two changes: the lane token's row and the ping URL's row.
4. The lane account's grants on its three secrets (observer `f793576f-…`, propose `acccb346-…`,
   read-App key `314a966c-…`) and its ping-URL secret. **Also the propose bearer's registry
   consumer:** a container reader on the `acccb346-…` row in change-manager's
   `.cred-consumers.toml`, which lives in change-manager and wasn't touched here.
5. The token's mint and the executor's write; then the ping URL's row made due and the executor's
   write (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `change-proposer` removed
   from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then `lane-run --read` over all four secrets. This is the only read of the
   propose bearer before the first `--submit`, since a dry run never fetches it.
8. Cut-over steps 2 onward: the paired dry runs (the bare invocation on both hosts, which ping
   nothing), the schedule, and the Mac job's retirement.

## PR

https://github.com/AlobarQuest/orchestrator/pull/686, marked ready. Not merged.
