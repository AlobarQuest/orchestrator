# FE-6: retire the Mac factory-events database

This plan covers ADR-0059 amendment 7, decision item 4, act 6 (FE-6): "stop and remove
`factory-events-db` on the Mac's OrbStack host engine after a final dump kept in the Mac's backup,
remove `FACTORY_DB_DSN` from `~/.factory/env`, and retire its secret's reference." It lists the
prep pull requests per repository, the act as an ordered sequence, what an unattended HQ cycle can
run, and the questions the ADRs leave open.

Code citations are at orchestrator `23c8b990`, security-standards `0226b19`, vps-backup `33116a5`,
sds-hq `5fe92eb`, intent-packages `7887469` and infraops-mcp-server `c3359bf`, unless a line names
another commit. Facts about the live Mac are observations from 2026-11-15 ~19:40 UTC, and the act
rechecks each one it relies on.

Two resources share the name `factory-events-db`. This plan retires only the Mac container on the
OrbStack host engine (`docker --context orbstack`, port 5545). The VPS Coolify database
`b82fa1vg5whecgnjreuqb5tk` is the chain of record, and no step here touches it.

## Fixed facts

| Fact | Value | Source |
| --- | --- | --- |
| Container | `factory-events-db`, image `postgres:16` (image id `sha256:f961d097a9ce…`), Up (healthy), `0.0.0.0:5545->5432` | `docker --context orbstack ps -a` and `inspect --format` (status, image, mounts) |
| Compose project | `factory-events-db`, working dir `~/Projects/security-standards/deploy/factory-events-db` | container labels |
| Volume | `factory-events-db_factory_events_pgdata` (compose prefixes the project name to `factory_events_pgdata`) | `inspect --format '{{range .Mounts}}…'`; `docker volume ls` |
| Network | `factory-events-db_default` | `docker network ls` |
| Definition | `deploy/factory-events-db/docker-compose.yml` lines 1-21: database and user `factory_events`, `POSTGRES_PASSWORD` from the gitignored `.env` | security-standards; `.gitignore` lines 22-23 |
| Password copies on disk | `deploy/factory-events-db/.env` (mode 0600, exists); `~/.factory/env`; `~/.factory/env.bak-hc-20261024` | `ls`; key names only, through `grep -o '^[A-Za-z_][A-Za-z0-9_]*='` |
| BWS secret | `factory-events/POSTGRES_PASSWORD`, `ea09a782-e22f-4ab9-b381-b47c00adb9ed`, project `Ops / Platform` | security-standards `.bws-secrets.toml` lines 26-29 |
| Last write | the 2026-11-15 08:30 UTC nightly's `ship`: `shipped=17 head_seq=4080 head=eb1902ab…c8b233c` | `~/Library/Logs/factory-events.log` |
| Sealed file | `~/.factory/events.sealed.jsonl`, 4,080 lines, mode 0444 | `wc -l`, `ls -l` |
| VPS chain at the cut-over | `chain ok: 4080 events, head eb1902ab…c8b233c` (2026-11-15 13:15 UTC kickstart) | `~/Library/Logs/factory-events.log` |

### Who reads the database

Nothing that runs reads it. The only reader of `FACTORY_DB_DSN` is `ship` (security-standards
`src/factory_events/ship.py` lines 45-49), and nothing calls `ship`:

- The nightly stopped running it at FE-5 (`scripts/factory-events-nightly.sh` lines 239-240), and
  the deployed `~/.claude/bin/factory-events-nightly.sh` equals that file.
- `ship` refuses whenever a chain password is set (`cli.py` lines 86-98), and the nightly always
  sets one.
- No tracked file in orchestrator, sds-hq, intent-packages, infraops-mcp-server, vps-backup or
  factory-runner runs `factory_events ship` or names `FACTORY_DB_DSN` outside ADRs, plans and
  reports, except orchestrator `docs/operations/credentials.md` lines 392-393 (prose).

The only other client is vps-backup's Recipe F dump: `backup-mini.sh` line 173 runs
`docker exec factory-events-db pg_dump` every night at 02:00. Recipe F uses `docker exec` and the
container's local socket. It doesn't connect to port 5545.

### Where the secret is referenced

Searching every repository's `origin/main` for the UUID and the name finds one row. These
repositories were searched: security-standards, orchestrator, vps-backup, infraops-mcp-server,
sds-hq, intent-packages, factory-runner and project-standards.

- security-standards `.bws-secrets.toml` lines 26-29 is the one row, and lines 1-3 of its header
  describe `~/.factory/env` as provisioned from these entries.
- No `.cred-consumers.toml` row names the secret, the container or the port. The files listed in
  `~/.config/infra-drift/cred-consumers.list` don't either.
- Nothing in `~/.claude/bin` or `~/.config/infra-drift` names it.

### The precondition

Amendment 7 runs FE-6 "only after FE-1's anchor is live and a nightly has run against the VPS
chain" (decision item 3). The FE-5 plan's follow-up 6 says "after the first nightly against the
VPS chain".

The 2026-11-15 13:15 UTC kickstart meets the second condition. It was the nightly script, run by
launchd, against the VPS chain, and it exited 0 with `chain ok: 4080 events`.

It doesn't meet the first. No anchor existed when it started, so its `verify --against-anchor` was
a plain verify, and it printed no `anchor ok:` line. The run's observation records
`anchor_verified: false`. Its `verify --record-anchor` wrote `~/.factory/state/chain-anchor.json`
(`cli.py` lines 64-74), so no run has yet checked the VPS chain against a recorded anchor.

**FE-6 waits for the first scheduled nightly that verifies against the recorded anchor.** That run
is 2026-11-16 at 03:30 EST (08:30 UTC). Its log must show `anchor ok: seq 4080 present` and its
observation `anchor_verified: true`, exit 0. Act step 1 checks this. If that run fails, FE-6 waits
for the failure's cause to be fixed and a later run to pass.

### Why the final dump needs its own copy

"A final dump kept in the Mac's backup" can't rely on the restic snapshots alone. `backup-mini.sh`
lines 212-220 prune the `mini-host` tag to 7 daily, 4 weekly and 3 monthly snapshots. Restic keeps
the last snapshot of each week and month, and every snapshot after the act lacks the dump. So the
last snapshots holding `postgres/factory_events.sql.gz` survive only as dailies, about seven days,
unless the act falls on the last snapshot of a week or month.

So the act writes the final dump to `~/.factory/retired/factory-events-db-final.sql.gz`, mode
0444. `backup-mini.sh` line 138 copies `~/.factory` into every `mini-host` snapshot, so every later
snapshot carries the dump, and the weekly verify checks it (prep PR 1). Open question 1 asks Devon
to confirm this location.

The dump holds no password: `pg_dump` of one database excludes roles. Its rows are the sealed
file's events, which the same backup already carries, plus `chain_heads`, the history of the heads
`ship` anchored. The dump is the only copy of `chain_heads`.

## Prep pull requests

Each prep pull request is opened and reviewed before the act and merged inside it. Its handoff marks
it "hold: merge inside FE-6 step N", so no cycle merges it on a clean review. Landing order follows
the act: P6-VB at step 6, P6-SS at step 12 and P6-OR at step 13.

These repositories need no pull request:

- **sds-hq:** no code reads the database or the secret. `state/goal.md` and the gate pages are
  HQ's records, which the cycle updates.
- **infraops-mcp-server:** nothing names the database, the port or the secret.
- **intent-packages and factory-runner:** nothing names them either.

### 1. P6-VB: vps-backup, the final dump replaces the nightly dump (merge inside the act, step 6)

Files:

- `backup-mini.sh` lines 172-173: delete the comment and the
  `pg_dump_local "factory_events" "factory-events-db" …` call. `pg_dump_local` stays, because
  `email_capture` (line 170) uses it.
- `backup-mini.sh` lines 132-137 (comment): add that `retired/factory-events-db-final.sql.gz` is
  the Mac database's final dump (FE-6), read-only, kept for audit.
- `verify-backup.sh` line 252: `verify_postgres_tag "mini-host" email_capture`.
- `verify-backup.sh` line 255: add `factory/retired/factory-events-db-final.sql.gz` to the
  `verify_dir_tag "mini-host"` list.
- `BACKUP-STRATEGY.md`:
  - Line 121: delete the `factory-events-db` row.
  - Line 122: the `~/.factory/` row names the final dump.
  - Lines 343-347 (`dir_copy_local`'s targets): name `retired/`.
  - Lines 562-566 (restore note): restore the final dump into a disposable `postgres:16` for audit
    only. It's a retired projection, not the chain.

Tests: none of vps-backup's tests (`tests/test-nas-mount.sh`, `tests/test_drill_compare.py`,
`tests/test_provider_agent.py`) cover these lines. The PR has three checks:

- Run `bash -n` on both scripts, and run the repository's tests.
- Use a scratch-restic harness, as `docs/superpowers/reports/fe-5-p-vb-report.md` did. Show that
  `verify-backup.sh` warns `Missing file: dirs/factory/retired/factory-events-db-final.sql.gz` on a
  snapshot without it and passes on one with it.
- Show that `backup-mini.sh` no longer names `factory-events-db`.

What changes when it merges: `backup.sh` pulls the checkout at 02:00 (`activate_checkout`, lines
13-24) before `backup-mini.sh` runs in the same launchd command. From then on:

- The `mini-host` backup stops dumping the container, and its `postgres_attempted` fact drops from
  2 to 1. A grep of orchestrator `src/` and `tests/`, sds-hq and infraops-mcp-server found no
  reader of that fact.
- The Sunday 03:00 verify requires the final dump in the latest snapshot.

Merging it early fails the next verify, which would find no final dump, and stops the nightly dump
before the final one. Merging it late is worse once the container stops: `pg_dump_local` warns
`not running` and the run exits 1 (`backup-mini.sh` lines 157-160 and 225-229). So it merges inside
the act, after the final dump and before the stop.

### 2. P6-SS: security-standards, `ship`, the compose file and the manifest row (merge inside the act, step 12)

Files:

- Delete `src/factory_events/ship.py`.
- `src/factory_events/cli.py`: delete `_cmd_ship` (lines 86-110) and the `ship` parser (lines
  179-187), and drop `ship` from the module docstring (lines 1-5).
- `src/factory_events/store.py` docstring lines 9-10: drop "ship projects the pre-cut-over file
  until FE-6".
- `src/factory_events/README.md`:
  - Delete lines 25-28 (`ship`).
  - Delete line 113 (the `FACTORY_DB_DSN` example).
  - Rewrite lines 171-172 per open question 4.
- Delete `deploy/factory-events-db/docker-compose.yml`, and delete `.gitignore` lines 22-23, which
  hold its `.env` ignore.
- `.bws-secrets.toml`: delete lines 26-29 (`ea09a782-…`). Rewrite header lines 1-3, because no
  remaining secret is provisioned into `~/.factory/env`. Line 7's `consumption` mode is whatever the
  scanner accepts for the remaining rows. Run the scanner from the repository root and expect no new
  finding.
- `scripts/factory-events-nightly.sh` lines 239-240: the comment cites `_cmd_ship`, which this PR
  deletes. Say instead that the nightly has no projection step. Because this edits the deployed
  script, step 12's `make install` always runs.
- `scripts/factory-events-nightly.sh` lines 60-61 and 71-72 and `tests/test_factory_nightly.py`
  lines 216 and 239: only if open question 4 drops the `shipped` and `head_seq` facts.
- These stay: README lines 122-130 and vps-backup `BACKUP-STRATEGY.md` lines 128-131 and 554-560
  ("re-materialize `~/.factory/env` from BWS"). The file keeps `CM_BASE_URL` and
  `FACTORY_HC_PING_URL` after step 9.

Tests:

- Delete `tests/test_factory_ship.py`, 3 integration tests.
- `tests/test_factory_chain_mode.py`: drop `["ship"]` from the parametrize at lines 101-102 (3
  cases, one per `chain.RETIRED_VARS` entry) and delete `test_ship_refuses_in_database_mode` (lines
  286-289).
- Before deleting a test, grep the repository's manifests and evidence for its name. The PR body
  states the collected-test counts before and after: unit drops by 4, integration by 3.
- Run `make check`, `make check-integration` and the security scanner.

What changes when it merges: the nightly's `activate_checkout` (line 37) pulls it at the next 03:30
run. Nothing calls `ship`, so no running job changes. Three effects:

- `python -m factory_events ship` becomes an argparse error.
- The compose file leaves the working copy, which removes the `docker compose up` path that would
  recreate the container.
- The deployed nightly (`~/.claude/bin`) changes only after `make install` (step 12).

It merges inside the act, after step 11 deletes the gitignored `.env`. Merged before that, the
`.gitignore` change would leave a file holding the database password untracked and visible in a
repository that sessions commit from.

### 3. P6-OR: orchestrator, the credentials page (merge inside the act, step 13)

Files:

- `docs/operations/credentials.md` lines 392-393: "Never read `~/.factory/env` for these
  settings: the nightly sources it under `set -a`." Drop "and it carries `FACTORY_DB_DSN`", which
  is false after step 9.

No ADR changes. Amendment 7's act 6 already names the act, and the superseding note at ADR-0059
line 2890 already governs line 824's `FACTORY_DB_DSN`. ADR-0055 amendment 26 item 9 (line 3843)
stays as written history. This plan's "As run" section records the act.

Tests: docs only. No `make check`; the PR's CI runs as usual.

What changes when it merges: nothing that runs. It merges inside the act so the page doesn't
describe a state that doesn't exist yet.

## The act

HQ announces FE-6 as one act (construction mode):
`sds-hq prod announce fe-6-mac-db-retire "Retire the Mac factory-events database" --why "ADR-0059 amendment 7 act 6"`.
An interactive HQ session runs every step unless the step names a cycle or Devon. Devon's only part
is the BWS decision in step 14.

**Window:** 2026-11-16 or later, after the 08:30 UTC nightly passes (step 1). The act runs in local
daytime and finishes before 02:00 local, so the 02:00 backup and the 03:30 nightly see the finished
state. It avoids Sunday 02:00-04:00 local, when `verify-backup.sh` (03:00) and the restore drill
(04:00) run. No writer needs pausing: nothing has written to the Mac database since 2026-11-15
08:30 UTC.

**Secrets:** no step prints, fetches or passes the database password.

- `docker exec … psql` and `pg_dump` connect as `factory_events` over the container's local socket,
  which needs no password, as `backup-mini.sh` line 162 does.
- No step runs a bare `docker inspect` (its `Config.Env` holds the password), `cat`, `source` or
  `env` on `~/.factory/env*` or the `.env`.
- Every read of those files prints key names only, through `grep -o '^[A-Za-z_][A-Za-z0-9_]*='`.
- The restic password stays in `--password-file ~/.restic-password`.

Each step's failure stops the act at that step (Code Brain rule 4). Run every pipeline under
`set -o pipefail`.

| Step | Who | Command or check | Source check after |
| --- | --- | --- | --- |
| 1 | Cycle or session | Preconditions, read only:<br>- the 2026-11-16 08:30 UTC (or a later scheduled) nightly logged an `anchor ok:` line and `done (database chain)` in `~/Library/Logs/factory-events.log`, and its `chain:factory-events` observation is `passed` with `anchor_verified: true`;<br>- the 2026-11-16 02:00 `mini-host` backup logged `Mac mini host backup completed successfully` in `~/Library/Logs/vps-backup.log`;<br>- `docker --context orbstack ps -a --filter name=factory-events-db --format '{{.Status}}'` is `Up … (healthy)`;<br>- P6-VB, P6-SS and P6-OR are open, reviewed and green. | Each line as stated. Any miss: no act today. |
| 2 | Cycle | Announce the act (the preceding command). | The announcement is in sds-hq `state/`. |
| 3 | Session | Record the live state:<br>- `docker --context orbstack exec factory-events-db psql -U factory_events -d factory_events -Atc "select count(*), count(distinct event_id), min(seq), max(seq) from factory_events"`;<br>- `… -Atc "select seq, head_hash from chain_heads order by id desc limit 1"`;<br>- `… -Atc "select count(*) from pg_stat_activity where backend_type = 'client backend'"` (positive control: counts this query's own connection), then the same with `and pid <> pg_backend_pid()`;<br>- `nc -z -w 2 127.0.0.1 5545` (positive control for step 15's probe);<br>- `wc -l < ~/.factory/events.sealed.jsonl` and the last line's `hash` (`tail -1 … \| python3 -c 'import sys,json; print(json.load(sys.stdin)["hash"])'`). | `4080\|4080\|1\|4080`; `4080\|eb1902ab…c8b233c`; the control prints 1 and the filtered query 0; `nc` exits 0; the file has 4,080 lines and ends at `eb1902ab…c8b233c`. The VPS chain holds more events than 4,080 by now, because approvals append to it after the cut-over and none of them reach this database. The comparison is to the sealed file and to the VPS chain's head at seq 4080 (Fixed facts), not to the VPS chain's current count. |
| 4 | Session | Take the final dump: `mkdir -p ~/.factory/retired`, then `docker --context orbstack exec factory-events-db pg_dump -U factory_events factory_events \| gzip > ~/.factory/retired/factory-events-db-final.sql.gz`, `chmod 0444` it, `gunzip -t` it, and record its SHA-256. | `gunzip -t` exits 0; mode `0444` (`stat -f %Lp`); the SHA-256 is in the act's progress entry. |
| 5 | Session | Restore-check the local copy: start a scratch container with no network and no published port, pinned by digest (Infra Brain 3): `docker --context orbstack run -d --rm --name fe6-restore-check --network none -e POSTGRES_USER=factory_events -e POSTGRES_DB=factory_events -e POSTGRES_HOST_AUTH_METHOD=trust "$(docker --context orbstack image inspect postgres:16 --format '{{index .RepoDigests 0}}')"`. Wait for `docker --context orbstack exec fe6-restore-check pg_isready -h 127.0.0.1` (bounded, 60 s). The TCP probe skips the image's init server, which answers only on the socket. Load: `gunzip -c ~/.factory/retired/factory-events-db-final.sql.gz \| docker --context orbstack exec -i fe6-restore-check psql -v ON_ERROR_STOP=1 -q -U factory_events -d factory_events`. Run step 3's two row queries against it. Then `docker --context orbstack stop fe6-restore-check`. | The load exits 0, and both queries print exactly step 3's values. `docker ps -a` no longer lists `fe6-restore-check`. |
| 6 | Session | Merge P6-VB. Then `. ~/.claude/bin/activate-checkout.sh && activate_checkout ~/Projects/vps-backup`, and run `bash ~/Projects/vps-backup/backup-mini.sh` by hand. The container is still running, and the merged script no longer dumps it. | `git -C ~/Projects/vps-backup rev-parse HEAD` is P6-VB's merge commit. The run logs `completed successfully` with no warning. `restic ls latest --tag mini-host --repo /Volumes/backups/restic-repo --password-file ~/.restic-password` lists `…/dirs/factory/retired/factory-events-db-final.sql.gz` and no `postgres/factory_events.sql.gz`. |
| 7 | Session | Check the backup copy: `restic dump` that path from the latest `mini-host` snapshot into a `mktemp -d` scratch directory, and compare its SHA-256 with step 4's. Delete the scratch directory after. | The SHA-256 values are equal. That snapshot is the "final dump kept in the Mac's backup", and every later snapshot carries it too. |
| **RP** | — | **Rollback point.** Nothing is stopped or deleted yet. To roll back from here: revert P6-VB (its revert restores line 173 and the verify lines) and leave the container running. `~/.factory/retired/` can stay. | — |
| 8 | Session | Stop the container: `docker --context orbstack stop factory-events-db`. | Status `Exited`. `nc -z -w 2 127.0.0.1 5545` now fails (step 3 proved the probe passes on a live port). Rollback: `docker --context orbstack start factory-events-db`. |
| 9 | Session | Remove the `~/.factory/env` line. `grep -c '^FACTORY_DB_DSN=' ~/.factory/env` prints 1; run `sed -i '' '/^FACTORY_DB_DSN=/d' ~/.factory/env`, writing no backup copy (a copy in `~/.factory/` would reach the NAS, since `--exclude env` matches only the exact name); then `chmod 600` it. Handle `~/.factory/env.bak-hc-20261024` per open question 3. | The names left are `CM_BASE_URL=` and `FACTORY_HC_PING_URL=`, and the mode is `600`. Rollback: re-add the line from BWS `ea09a782-…`, only while the secret exists (step 14). |
| 10 | Session | Prove the nightly doesn't need the database: `launchctl kickstart gui/$(id -u)/com.devon.factory-events`. Don't run it within 30 minutes of 03:30. `kickstart` returns before the run ends, so wait (bounded, 5 minutes) for the run's last log line. | Exit 0, `anchor ok:` and `done (database chain)` in the log; the observation is `passed`. |
| 11 | Session | Remove the container, then the volume and the network: `docker --context orbstack rm factory-events-db`, `docker --context orbstack volume rm factory-events-db_factory_events_pgdata`, `docker --context orbstack network rm factory-events-db_default`. Then delete the compose `.env`: `rm ~/Projects/security-standards/deploy/factory-events-db/.env`. **The volume removal can't be undone** (open question 5). | `docker --context orbstack ps -a`, `volume ls` and `network ls` list none of the three. `.env` is absent. Rollback after this: restore step 4's dump into a new `postgres:16` container. |
| 12 | Session | Merge P6-SS. Then `activate_checkout ~/Projects/security-standards`, and run `make install` there. | HEAD is P6-SS's merge commit. `git -C ~/Projects/security-standards status --porcelain --ignored deploy/` prints nothing. `cmp` shows the deployed `~/.claude/bin/factory-events-nightly.sh` equals the checkout's. |
| 13 | Session | Merge P6-OR. Never pull the orchestrator main tree by hand; the lanes' activation updates it. | P6-OR is `MERGED`. |
| 14 | Devon | Delete or keep BWS secret `factory-events/POSTGRES_PASSWORD` (`ea09a782-…`, project `Ops / Platform`) per open question 2. A BWS change is Devon's. | Devon's answer in chat, recorded in sds-hq `state/decisions.md`. |
| 15 | Session | Final check that nothing on the Mac reaches port 5545:<br>- `nc -z -w 2 127.0.0.1 5545` fails;<br>- `docker --context orbstack ps -a --filter publish=5545` prints nothing;<br>- `grep -rlE ':5545\b\|5545:5432\|FACTORY_DB_DSN\|ea09a782' ~/Library/LaunchAgents ~/.claude/bin ~/.config/infra-drift` prints nothing, and the `~/.factory/env*` name listings show no `FACTORY_DB_DSN=`. The patterns are anchored: a bare `5545` matches hash substrings (orchestrator `uv.lock` line 1005 holds one), and `factory-events-db` is also the VPS database's name;<br>- each repository's `origin/main` grep for the same patterns, excluding `*.lock`, hits only ADRs, `docs/superpowers/` plans, specs and reports, and sds-hq `state/` history. | Each as stated. `lsof -iTCP:5545` isn't used: it printed nothing while the port was published, so it can't show absence. |
| 16 | Cycle | Watch the first scheduled runs after the act: the 02:00 `mini-host` backup (no warning), the 03:30 nightly (`anchor ok:`), and the next Sunday 03:00 `verify-backup.sh`, which reports `[OK] [mini-host] dirs/factory/retired/factory-events-db-final.sql.gz`. | Each run's log line. A miss reopens the act. |

### What an unattended cycle can do

A cycle has Bash on the Mac but changes no file outside sds-hq `state/` by hand. It can run:

- step 1 (log reads, `docker ps`, pull request states);
- step 2 (the announcement, written to `state/`);
- step 15's read-only checks;
- step 16.

Steps 3 to 13 need an interactive session. Each one execs into a container, writes under `~/.factory`
or `/tmp`, edits `~/.factory/env`, merges a pull request, or deletes a container, volume or file.
Step 14 is Devon's.

### What rolls back

- Before **RP**, nothing has changed except a new read-only file and a merge, which reverts.
- Step 8 (stop) rolls back with `docker start`.
- Step 9 (env line) rolls back from BWS only until step 14 deletes the secret, and nothing reads
  the line.
- Step 10 is a check.
- From step 11, the container and its volume can't come back. The checked final dump replaces them:
  a new container restored from it holds the same rows (step 5 proved it).
- Steps 12 and 13 revert as merges.
- Step 14, if Devon deletes the secret, can't be undone. A restored dump needs a new password, not
  the old one.

## Open questions

The ADRs don't settle these. Each has a recommendation; Devon decides. Three of them set a prep
pull request's scope, so their answers come before that pull request is dispatched:

- Open questions 1 and 3 come before P6-VB: question 1 sets the path in `verify-backup.sh` line
  255 and in act step 4, and question 3 decides the `env*` exclude.
- Open question 4 comes before P6-SS: it decides the nightly's facts.
- Open questions 2 and 5 come before the act (steps 14 and 11).

1. **Where the final dump lives.** Amendment 7 says "kept in the Mac's backup", and restic
   retention keeps the last snapshots holding it for about seven days (see "Why the final dump needs
   its own copy").
   - Recommended: `~/.factory/retired/factory-events-db-final.sql.gz`, mode 0444. Every
     `mini-host` snapshot carries it, and the weekly verify checks it (P6-VB).
   - Alternative: tag that snapshot (`restic tag --add fe6-final`) so `forget --group-by tags`
     keeps it in its own group. That works through a retention side effect, and the verify doesn't
     check it.
2. **The BWS secret `ea09a782-…`.** Recommended: delete it at step 14. After step 11 the password
   authenticates nothing, a restore of the dump needs no old password, and a live-looking secret for
   a database that doesn't exist invites reuse. Alternative: keep it until the next quarterly
   credential review. It costs nothing, but it keeps a stale reference.
3. **`~/.factory/env.bak-hc-20261024`.** Its key names are `CM_BASE_URL`, `FACTORY_DB_DSN` and
   `FACTORY_HC_PING_URL` (names only were read). `dir_copy_local "factory" … --exclude env`
   (`backup-mini.sh` line 138) excludes only a file named exactly `env`, so this copy, with the
   Mac database's password, is in the `mini-host` snapshots and their Google Drive mirror.
   - Recommended: delete the file at step 9. Its other two values duplicate `~/.factory/env`, and
     the password it holds stops mattering at step 11.
   - Also recommended: P6-VB widens the exclude to `--exclude 'env*'`, which matches neither
     `chain-db.env` nor anything else in the tree, so no later `env` copy reaches the NAS. Older
     snapshots age out under the 3-month retention.
4. **The nightly's `shipped` and `head_seq` facts.** They are always 0 since FE-5
   (`factory-events-nightly.sh` lines 239-240), and no reader was found.
   - Recommended: drop them in P6-SS, with `make install` at step 12. A fact that can't change
     reports nothing.
   - Alternative: keep them at 0, which leaves the observation's shape unchanged.
5. **The volume.** Amendment 7 names "stop and remove `factory-events-db`", the container, and
   doesn't name its volume. Recommended: remove it at step 11, after steps 5 and 7 prove the dump and
   its backup copy. The volume holds nothing the checked dump lacks. Kept, it's a second
   password-protected copy under a retiring secret, and a path back to a retired service.
   Alternative: keep the volume for a set period and remove it in a later act. That's a second
   act for no data the dump lacks.

The ADRs don't contradict each other on FE-6's preconditions or order. Act 6 grants the stop, the
removal, the env line and the reference, and step 14 leaves the BWS change to Devon. So this plan
asks no question before it can be written.

## The FE-5 follow-ups

FE-6 takes the FE-5 plan's follow-up 6 and leaves follow-ups 2 and 3:

- **Follow-up 2** (launchers fail by name when `~/.factory/chain-db.env` is absent) changes launcher
  behavior.
- **Follow-up 3** (sds-hq's `Settings.chain` and `events_home`, `standing_approve.py` lines 149-164
  and 724-725) is the file-path switch-off mode.

Neither touches the Mac database, and neither depends on FE-6. Folding them into a retirement act
would put lane-behavior changes inside an act whose steps are deletions. Each can go as its own pull
request at any time.

## Follow-ups

1. Check Code Brain, Infra Brain and App Brain for records naming the Mac database (port 5545,
   `FACTORY_DB_DSN`, `factory_events_pgdata`, `ea09a782-…`). This plan's session had no Brain
   tools, so none was checked.
2. After step 16, record the act in an "As run" section of this plan, as the FE-5 plan does, and
   mark the FE-5 plan's follow-up 6 done in HQ's records.
