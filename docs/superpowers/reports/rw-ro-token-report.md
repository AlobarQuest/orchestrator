Outcome: done

# rw-ro-token: point the revision watcher at the read-only Coolify token

PR: https://github.com/AlobarQuest/orchestrator/pull/712 (branch `hq/rw-ro-token`, base `main`
at `cb372bf3`, commit `a0c73111`). Not merged, released, or deployed: HQ owns those acts
(`rw-ro-token-merge`, `rw-ro-token-release`, `rw-ro-token-redeploy`).

## What I built

- `scripts/run-revision-watcher.sh`: `PLATFORM_TOKEN_UUID` is `0bf066e9-28c1-4aa7-8c3d-b50300d492a6`
  (`prod-coolify-read-token`), and its comment says it's the read-only token that only lists apps.
  I read the launcher whole: the one variable serves both the container path
  (`_broad_secret` = the lane's token) and the Mac path (`_broad_secret` = the broad identity), so
  both hosts read the new token. No other line changed, and no failure path was added.
- `.bws-secrets.toml`: a new `[[secret]]` for `prod-coolify-read-token` (`Ops / Platform`,
  `lanes = ["revision-watcher"]`), placed where the old entry's `lanes` sat so
  `lane_secrets(…, "revision-watcher")` keeps its order. The old `prod-coolify-api-token` entry
  stays, without `lanes`, and its comment says why.
- `.cred-consumers.toml`: `revision-watcher-bws-token`'s `reach_secrets` names seven UUIDs, the
  old and the new token included. The comment says the old one stays until the later gate removes
  the grant. A new row `coolify-revision-watcher-read-token` covers the read token (item 4).
- Tests: `tests/scripts/test_run_revision_watcher.py` pins the new UUID.
  `tests/rotation_worker/test_revision_watcher_rows.py` has `GRANTED` at seven and a named
  `STILL_GRANTED` set holding the old UUID. The reach assertion is `reach == lane secrets |
  STILL_GRANTED`, and a new test checks that every `STILL_GRANTED` UUID is neither a lane secret
  nor named in the launcher, so the exception can't hide a secret the lane still reads.

## Item 2: who still reads the old secret

Nothing in this repository reads `bbd71f41-…` after the change. A grep of `src/`, `scripts/`,
`lanes/`, `docs/operations/`, `docs/decisions/`, `layers.toml`, and every `CLAUDE.md` finds it only in the two
manifests and the rows test. The deployed orchestrator doesn't read it (the only
`REVISION_WATCHER_PLATFORM_TOKEN` reader is `src/revision_watcher/cli.py`, fed by this launcher).
The estate lander uses its own Coolify token (`f9f59233-…`).

So I dropped `lanes` from the old entry but kept the entry. The lane container runs a pinned image
built before this change, and it fetches `bbd71f41-…` until HQ releases and redeploys. A rollback
to such an image would fetch it too. The infraops-mcp-server manifest also declares it, as its
owner.

## Item 3: why the shape differs from both tests

The handoff said to follow the registry checks if they force a shape. They couldn't both be
followed:

- `test_the_token_row_declares_…` required `reach_secrets == lanes secrets`, which would keep the
  old UUID in `lanes`.
- `test_the_repositorys_manifest_lists_the_watchers_secrets` requires the launcher to name every
  `lanes` UUID, which the launcher no longer does for the old one.

The declared reach is read only at bind time: `build_provider` in `adapters/bws_lane_token.py`
turns it into `reach_digest`, and nothing in `src/rotation_proposer` or the package content reads
`reach_secrets`. So merging this changes no derived plan or package revision for the due row.

`reach_secrets` must name exactly what the account sees: `bws_lane_token`'s bind refuses a token
whose reach isn't exactly the declared set (`adapters/bws_lane_token.py` ~205). `lanes` means
"a lane reads it in its container" (the manifest header), and feeds only the `--token` read proof
in `scripts/deploy_orchestrator.py`. So the old UUID is in `reach_secrets` but not in `lanes`, and
the rows test carries the difference as a named, self-checked exception.

## Item 4: registry rows

- `prod-coolify-api-token` has no `[[credential]]` row in this repository or in
  infraops-mcp-server's `.cred-consumers.toml` (grep by UUID and name). I didn't add one, since
  this repository doesn't own it.
- The new token needs one: it's minted for this lane, and this repository is its only reader. The
  class `coolify-api-token` already exists (infraops `cred-rotation.ts`: `maxAgeDays: 365`,
  "never read:sensitive"), and `coolify-estate-lander-token` is the precedent, so no new class and
  no question. The row has `created = "2026-11-15"` (G-62 closed that day,
  `sds-hq/state/decisions.json`), `disposition = "reissue"`, a keeper consumer, and a
  `runtime-fetch` consumer for the launcher with `healthchecks_check = "sds-revision-watcher"`.
  It has no `fingerprint_sha256_8`: computing one means reading the value, which the handoff
  forbids. The field is optional (infraops prints `fp ?` when absent; the lane rows omit it). It
  isn't `rotated_by_sds`: the class is in `UNTYPED`
  (`tests/architecture/test_rotation_agreement.py`), so it falls to the manual checklist.

## Item 5: the Mac rollback path

The Mac launchd job reads the platform token through the broad identity (Keychain
`BWS_ACCESS_TOKEN_VPS_BACKUP`). By the records, that identity reads the new secret through a
project grant, so the Mac path isn't expected to break. This is unmeasured: I didn't run `bws`.

- infraops-mcp-server `.cred-consumers.toml` row `bws-tok-ops-mini-20260730`: keeper
  `BWS_ACCESS_TOKEN_VPS_BACKUP`, machine account "Developer Machines, Ops + Content".
- vps-backup `docs/2026-06-12-bws-rearchitecture.md`, "Machine account → scope → tokens":
  Developer Machines is scoped to the Ops and Content projects. "Ops" is `Ops / Platform` there.
- The 2026-09-08 measurement in `.bws-secrets.toml` (broad reads the old token in
  `Ops / Platform`) fits a project grant, though it doesn't prove one.

These records are from June to September 2026, outside this repository, and not re-measured.

## Tests and checks

- Baseline `make check` (clean tree, `orchestrator_test_hq_w_170`): **15,006 collected**, 7 failed,
  14,998 passed, 1 skipped. The 7 failures are void: I edited the launcher and manifests while
  that run was in progress, and all 7 are in the two test files that read them.
- After `make check` (commit `a0c73111`'s tree): **15,007 collected** (one new test), 13 failed,
  14,993 passed, 1 skipped. Ruff, ruff format, pyright (0 errors), and shellcheck pass.
  - All 13 failures are in `tests/work_watcher/test_launcher_orchestration.py`, which this change
    doesn't touch. The same 13 fail on a clean `git archive` of `main` (`cb372bf3`), so the
    control is red too. See the concerns.
  - The three affected files on this branch (`test_run_revision_watcher.py`,
    `test_revision_watcher_rows.py`, and the work-watcher file): 50 passed, 13 failed (the same
    13). On `main`: 49 passed, 13 failed.
- Security scanner from the repo root: 0 BLOCK, 2 WARN. One is `bws.secret-files-gitignored`.
  The other is `bws.manifest-matches-usage` for an undeclared `aaff45ca-…`, which isn't in this
  diff. Neither old nor new UUID is flagged.
- `/code-review` (low) on `main...HEAD`: the skill returned an empty result (`(none)`), with no findings listed.
- CI on PR #712: green (see the final section).

## Files changed

- `scripts/run-revision-watcher.sh`
- `.bws-secrets.toml`
- `.cred-consumers.toml`
- `tests/scripts/test_run_revision_watcher.py`
- `tests/rotation_worker/test_revision_watcher_rows.py`
- `docs/superpowers/reports/rw-ro-token-report.md` (this report)

## Self-review

- The order of `lane_secrets` for the lane is unchanged (observer, platform, estate, alert, read
  App key, ping URL), so the launcher test's list holds with only the UUID swapped.
- The launcher comments that say "the broad account reads the platform token" (the 2026-09-08
  measurement) describe the old token's read. I left them, because the handoff limited the
  launcher change to line 56. Each stays true if the project grant holds, as item 5 expects.
- `STILL_GRANTED` is self-policing: if the launcher starts reading the old UUID again, or it
  returns to `lanes`, the new test fails.

## Concerns

1. **The follow-up after the second gate has two edits.** HQ's plan step 5 drops the old UUID from
   `reach_secrets`. It must also empty `STILL_GRANTED` in
   `tests/rotation_worker/test_revision_watcher_rows.py`. Until that PR merges, a rotation of
   `revision-watcher-bws-token` (due, `rotate_requested = "2026-11-11"`) fails bind, because the
   new token would see six secrets against a declared seven. Merge the follow-up promptly after
   the grant is removed, or don't issue that row's token in between.
2. **Between this merge and HQ's redeploy, the container reads the old token, and the Mac job
   reads the new one** (the Mac activates the merged working copy on its next pass). Both grants
   stand during that window, so neither breaks.
3. **The Mac path is unmeasured** (item 5): if the broad identity holds direct grants only, the
   Mac launchd job fails with "could not read the platform token" (exit 1) after this merges. The
   records say project grant.
4. **A test-isolation defect outside this change**: `tests/work_watcher/test_launcher_orchestration.py`
   passes the real `HOME` to `scripts/run-work-carrier.sh`, which checks the factory-events switch
   file `$HOME/.factory/chain-db.env` (`scripts/sds-bws.sh:95`). That file appeared on this Mac at
   08:06 local today (I checked its metadata only, never its contents), presumably from the FE-5
   work. The launcher then takes the database path and fails its BWS stub read: 13 failures
   locally, on `main` as well. CI has no such file. Fixing it means setting `HOME` in that test's
   `_run`, a file beyond this handoff's list, so I left it for HQ to route.
5. The standards section has no Code Brain `secrets` road (as HQ noted). Nothing else the change
   touches is uncovered.

## Standards followed

- **Code Brain rule 5 (never log secrets):** no message, test, commit, PR, or report carries a token
  value. The only identifiers are BWS UUIDs, and the test stubs use `value-of-<uuid>` fakes.
- **Code Brain rule 4 (no silent swallow):** the launcher change is one UUID and its comment. The
  existing empty-value check (`FATAL: could not read the platform token`, exit 1) still covers
  the new secret. No new failure path.
- **Code Brain rule 3 (structured logs to stdout):** no log file was added.
- **Infra Brain 2, 591, 592, 593 (no secret or BWS token in tracked files):** only UUIDs were
  added. The security scanner reports 0 BLOCK, and the write-guard hook didn't fire.
- **Infra Brain 1032 (exposed credential rotates):** no token value appeared in this session. I
  didn't run `bws`, read the Keychain, or call Coolify. I checked `~/.factory/chain-db.env`'s
  metadata only (`ls -la`), never its contents.
- **Infra Brain 212 (HTTPS):** doesn't apply.
- **Orchestrator `CLAUDE.md` invariants:** `.bws-secrets.toml` references secrets by UUID with a
  manifest entry for every UUID the code fetches. `.cred-consumers.toml` names exactly what the
  lane's account reads, which the bind requires. The new report file falls under
  `docs/superpowers/**` in `layers.toml`. `make check` is red only on the 13 pre-existing,
  environment-caused failures in concern 4, which are red on `main` too.
- **Worktree rules:** all work happened in `.worktrees/hq-W-170` with its own venv and the
  `orchestrator_test_hq_w_170` database. The main tree was never touched.

## CI on the PR

On commit `a0c73111` (run `43718118359`), every check passes: `Quality` (13m44s: 15,003
collected, 14,980 passed, 23 skipped, 0 failed), `Rollout transcription currency`, and
`Runner consumer compatibility`. The work-watcher tests in concern 4 pass in CI, which confirms
they fail locally only because of the Mac's switch file. The commit that adds this report triggers
another run, and it changes only this file.
