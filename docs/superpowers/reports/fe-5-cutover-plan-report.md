Outcome: done

# FE-5 cut-over plan: build report

## What I built

PR [#702](https://github.com/AlobarQuest/orchestrator/pull/702), branch `hq/fe-5-cutover-plan`.
The plan's last revision and this report are the branch head; run `git log -1` on the branch for
the exact commit. The PR adds one file,
`docs/superpowers/plans/2026-11-14-fe-5-cutover.md`, which contains the following:

- **The switch.** One non-secret settings file, `~/.factory/chain-db.env`, holds the three
  settings. Every holder's new fetch runs only while the file exists. A Keychain item can't serve
  as the switch, because `sds-hq` and `sds-operator` already hold both their item and their grant.
  The file isn't `~/.factory/env` either, because the nightly sources that file with `set -a`, and
  HQ's `env -i` passes only `HQ_*` variables. The import reads a staging copy,
  `chain-db.env.pending`. The act moves it into place after the seal, which keeps the ADR's order:
  import, head check, seal, then set each holder's switch.
- **The holders table.** It has five Mac holders plus the container carrier, which belongs to 3f-2.
- **Six prep PRs, in landing order.**
  - P-SS, security-standards: the nightly's database mode. It's `make install`-deployed, so a merge
    doesn't change it.
  - P-OR-1, orchestrator: the bump proposer and Mac carrier fetches, and the manifests.
  - P-OR-2, orchestrator: the session wrapper `scripts/factory-events-session.sh` and an
    operations page.
  - P-HQ, sds-hq: the cycle's in-process fetch, the `check_chain` guard,
    `SECURITY_STANDARDS_MIN`, and `psycopg` in HQ's security-standards clone. That clone's
    `.venv-events` holds only `jsonschema` and `pyyaml` (`standing_approve.py` lines 87-117), so
    every cycle approval in database mode would fail on an `ImportError`.
  - P-PIN, orchestrator: `security-standards.pin.toml` past `60ec80f7`.
  - P-VB, vps-backup: `verify-backup.sh` line 255. It's opened before the act and merged inside it.
- **Three removal PRs, merged inside the act.** They are R-SS, R-IP and R-OR. R-OR carries FE-3's
  deferred unset-read-password refusal.
- **The act.** It's steps 0 to 14, each with who runs it, a command or check, and a source check.
  The rollback point sits before the seal, and the plan says what rolls back at each later step.
  The plan also says which holders get no live proof before the removals merge.
- **Eight open questions.** Questions 1 to 3 go to Devon; HQ settled 4 to 8 in fix round 1.
- **The removal PRs' test scope.** Unit tests in all three repositories plant approvals in an
  events file, and each R-entry says where those tests go.

The plan goes beyond the handoff's three repositories and three writers in four places:

- **sds-hq's `check_chain`** (`standing_approve.py` lines 392-404 at `585e916`) returns silently
  when `~/.factory/events.jsonl` is absent, so the seal would stop it guarding. This is open
  question 1.
- **vps-backup** `verify-backup.sh` line 255 expects `factory/events.jsonl`, so the seal would fail
  the backup verify.
- **The rotation proposer** (06:30) is a non-holder that emits. Between the seal and the removal's
  activation, its emit would re-create `events.jsonl` at `seq` 1, so the act pauses it.
- **`bump-proposer`'s Mac token** must be minted in FE-5 (amendment 25 item 3), while G-59's page
  defers it to the lane's mint sitting. This is open question 5.

## Tests and checks run

The plan is docs only, so I didn't run `make check`, as the handoff directs. I checked the
following:

- `layers.toml` line 755 covers `docs/superpowers/**`.
- The plan's step cross-references are consistent. I fixed four references that pointed at the
  wrong act step before committing.

## Files changed

- `docs/superpowers/plans/2026-11-14-fe-5-cutover.md`: new.
- `docs/superpowers/reports/fe-5-cutover-plan-report.md`: this report.

## Line citations relied on

| Repository at commit | Citation |
| --- | --- |
| orchestrator `7739b22a` | ADR-0059 amendment 7: act 5 at lines 2847-2858, item 3 at 2801-2828. Amendment 8: items 1-4. |
| orchestrator `7739b22a` | ADR-0055 amendment 24: items 2, 3, 5, 8, 10, 11 and 13. Amendment 25: items 1, 3 and 4. Amendment 26: items 1, 3, 5, 6 and 7, and "Increments". |
| orchestrator `7739b22a` | `scripts/run-bump-proposer.sh`: lines 163, 191-216 and 264-271. |
| orchestrator `7739b22a` | `scripts/run-work-carrier.sh`: lines 146, 185-232 and 335-350. |
| orchestrator `7739b22a` | `scripts/com.devon.work-carrier.plist` line 59 and `scripts/com.devon.bump-proposer.plist` line 89. |
| orchestrator `7739b22a` | `scripts/sds-bws.sh`: lines 53-61. |
| orchestrator `7739b22a` | `src/orchestrator/package_sources.py`: lines 228-230, 233-243, 246-261, 283, 357-391 and 456-488. |
| orchestrator `7739b22a` | `src/bump_proposer/standing.py`: lines 61-70, 221 and 426. |
| orchestrator `7739b22a` | `tests/architecture/test_intent_edges.py`: lines 52, 64, 219 and 268-272. |
| orchestrator `7739b22a` | `docs/operations/package-intake.md` lines 129-140; `security-standards.pin.toml`; `scripts/build_registry_bundle.py` lines 63-75; `.github/workflows/release-image.yml` lines 5-11; `Dockerfile` lines 4-44; `.cred-consumers.toml` lines 31-48; `layers.toml` line 755. |
| orchestrator, PRs | PR #700's follow-up 2 (the pin and the Mac checkout), and PR #696's follow-ups. |
| security-standards `60ec80f` | `scripts/factory-events-nightly.sh`: lines 35, 88-92, 104-115 and 117-159. |
| security-standards `60ec80f` | `src/factory_events/chain.py`: lines 17-118. |
| security-standards `60ec80f` | `src/factory_events/cli.py`: lines 22-181. |
| security-standards `60ec80f` | `src/factory_events/db.py`: lines 35, 111 and 178-221. |
| security-standards `60ec80f` | `src/factory_events/ship.py` lines 52-64; `pyproject.toml` line 15; the tests named in R-SS. |
| security-standards `60ec80f` | `governance-map.toml` lines 74-81; `scripts/com.devon.factory-events.plist.template`; `.bws-secrets.toml`; `Makefile` lines 102-105. |
| intent-packages `93d75e1` | `src/intent_packages/operations.py`: lines 429-573. |
| intent-packages `93d75e1` | `docs/decisions/0001-approval-chain-is-the-audit-trail.md` lines 56-57; `src/intent_packages/emitter.py` lines 109-121; `tests/test_op_verify_approval.py`. |
| orchestrator `7739b22a` | `tests/cli/test_package_intake_cli.py` lines 339-369 and 561; `tests/cli/test_package_chain_database.py`. |
| sds-hq `585e916` | `src/sds_hq/standing_approve.py`: lines 36-51, 87-117, 120-139, 227-251, 359-404 and 562-572. |
| sds-hq `585e916` | `src/sds_hq/secrets.py` lines 1-55; `src/sds_hq/lock.py` lines 153-162; `bin/hq-env.sh` lines 37-51; `CLAUDE.md` lines 16-23; `.bws-secrets.toml` lines 1-9. |
| sds-hq `585e916` | `docs/operations/operator-identity.md` lines 30-110; `state/goal.md` lines 33 and 59; `state/gates/factory-events-accounts.md` (G-59). |
| vps-backup `bd2b440` | `verify-backup.sh` line 255; `backup-mini.sh` lines 132-138; `BACKUP-STRATEGY.md` lines 344 and 560. |
| infraops-mcp-server `c3359bf` (local `origin/main`, not fetched this session) | `.cred-consumers.toml`: every row names a `class`. |
| infraops-mcp-server `c3359bf` (fix round 1: `git ls-remote` showed remote `main` at the same commit) | `src/security-drift/cred-consumers.ts` line 207; `src/security-drift/cred-rotation.ts` lines 31, 68, 88 and 251-257. |
| fix round 1 | orchestrator `scripts/sds-bws.sh` line 54 and `scripts/com.devon.bump-driver.plist` lines 20-30 at `7739b22a`; `scripts/run-bump-driver.sh` line 64; ADR-0055 line 3516. security-standards `src/factory_events/db.py` lines 186-189 and 222-223, `cli.py` lines 61-84, and `scripts/factory-events-nightly.sh` lines 110-112 at `60ec80f`. sds-hq `standing_approve.py` lines 105-110 and 392-404, and `secrets.py` lines 40-48, at `585e916`. vps-backup `verify-backup.sh` lines 1-24 and 206-243 at `bd2b440`. |

The following facts come from the live Mac on 2026-11-14. They're dated observations, and the act
rechecks each one:

- **Checkouts.** The orchestrator main tree's HEAD is `7739b22a`. The security-standards checkout
  is at `cb832f3`, clean, on `main`.
- **Schedules from live plists.** The rotation proposer runs at 06:30 and the activation sweep at
  07:10. `com.devon.sds-hq` has `StartInterval` 900. In fix round 1: `com.devon.vps-backup` runs
  at 02:00 daily (`backup.sh`, `backup-orb.sh`, `backup-mini.sh`, `sync-gdrive.sh`),
  `com.devon.vps-backup-verify` at 03:00 on Sundays, and `com.devon.bump-driver` at minute 50
  of every hour, running the main tree's `scripts/run-bump-driver.sh`.

## Self-review

- Each prep PR names its switch and what changes on merge. P-VB alone can't be prepped without an
  effect, so it's merged inside the act.
- The plan never puts a password or a BWS token in argv: each one goes as an assignment prefix or
  into a child's `env`. Before fix round 1, P-OR-1 and P-OR-2 fetched through `sds_bws_value`,
  whose two-argument branch passes the token as an argument to `env`; fix round 1 fixes the helper
  inside P-OR-1. Its commands print only key names, hashes and heads.
- The plan keeps the ADR order: import (step 6, whose commit is the head check), seal (7), each
  holder's switch (9), removal merges (10), verify (12), resume (12 and 13). The nightly's verify
  at step 12 is also its resume, because its run appends.
- I made no grant, account or holder decision. The plan proposes names only: the file, the archive,
  the three Keychain items, the wrapper's home, and the import's fetch account (open question 4).
  The session wrapper has no `verify`, because a session reading the chain is a reader the ADRs
  don't name (open question 8). The Mac verify is the nightly's own run under its own token.
- The advisor's review led to these revisions: the staging file; the file-mode
  `--against-anchor`'s need for `FACTORY_DB_DSN` in step 4 (fix round 1 dropped `--against-anchor`
  from step 4); the second `make install` after R-SS;
  the HQ clone's missing `psycopg`; and the removal PRs' test scope.

## Concerns

1. **No live proof per holder before the point of no return.** Step 10 merges the removals before
   any scheduled holder has run under its own token. The plan states this ("What the act doesn't
   prove before step 10"), and relies on each prep PR's stub tests plus first-run checks.
2. **`sds_bws_value` puts the token in `env`'s argv.** This is pre-existing. Fix round 1 folds the
   fix into P-OR-1, so the helper changes for every two-argument caller, not only the new fetches.
   The plan lists the callers and says each `bws` gets the same environment and argv.
3. **I ran `git fetch`** in the main checkouts of `~/Projects/orchestrator`, `security-standards`,
   `intent-packages` and `sds-hq`. It updated remote-tracking refs only: no checkout, pull or edit.
   The handoff's "never edit, branch, check out, or pull" holds, but the fetch did touch the main
   tree's `.git`.
4. **The bump-proposer token conflict.** G-59's page defers that token, but ADR-0055 amendment 25
   item 3 needs it on the Mac for FE-5. The ADR governs, and the plan mints the token in step 0. HQ
   settled open question 5 in fix round 1: the G-59 deferral was HQ's own text.

## Fix round 1

HQ's Opus review of `bed48124` found one blocking item and nine small text fixes. I fixed each one
in the plan only; nothing else changed. Line numbers are the plan's after this round.

### Blocking: BWS tokens in argv

- **The helper (plan lines 155-156, 176-185).** `sds_bws_value`'s two-argument branch
  (`scripts/sds-bws.sh` line 54) passes the token as an argument to `env`. P-OR-1 now changes it to
  the nightly's assignment-prefix form (security-standards `factory-events-nightly.sh` lines
  110-112), with a `tests/scripts/test_sds_bws.py` test whose stub `env` records its argv. The
  former follow-up 6 is deleted, because P-OR-1 now does it.
- **The callers (plan lines 191, 210-215, 232-234).** The carrier's fetch and P-OR-2's wrapper go
  through the fixed helper. P-OR-1's "What changes when it merges" lists the helper's five
  two-argument callers, each of which gets the same `bws` environment and argv.
- **Every other `bws` call (plan lines 106-108, 141, 207, 256, 444).** P-SS already uses the
  assignment-prefix form; its test now asserts no stub token in argv, as P-OR-1's and P-OR-2's do.
  P-HQ's `fetch_secret` passes the token through `subprocess.run`'s `env` (sds-hq `secrets.py`
  lines 40-48). Step 0's check reads the token into a variable and runs `bws` with it as an
  assignment prefix. The holders section now states the token rule beside the password rule.
- **The report (Self-review, Standards followed).** "Never puts a password in argv" now covers
  BWS tokens too.

### Non-blocking items

1. **P-HQ's merge effect (plan lines 300-306).** After the interactive pull, `build_venv` installs
   `psycopg[binary]` on every `prepare` (sds-hq `standing_approve.py` lines 105-110), and a failed
   install refuses as `setup_failed`, even in file mode.
2. **The registry class (plan lines 166-173, 216-220, 524-528).** Each secret's row names class
   `postgres-role-password` with `rotated_by_sds = false`. It's in infraops `CLASS_POLICY`
   (`cred-rotation.ts` line 68 at `c3359bf`; `git ls-remote` showed remote `main` at `c3359bf`).
   "The manifests change no process" became a statement of what the 03:00 scan and the rotation
   proposer's `cred-findings` read: a row with no class throws (`cred-consumers.ts` line 207), and
   an unknown class posts `cred.unknown-class` (`cred-rotation.ts` lines 251-257).
3. **Step 4 (plan line 448).** `--against-anchor` and the `~/.factory/env` sourcing are dropped.
   Plain file-mode `verify` reads only the file (`cli.py` lines 61-84), and the expected output is
   the `chain ok:` line alone.
4. **Step 6 (plan line 450).** The printed head is the file's own (`db.py` lines 222-223). The
   check column now presents `_replay`'s head comparison (`db.py` lines 186-189) and exit 0 as the
   real check.
5. **`bws` calls in steps 0 and 3 (plan line 444).** Step 0's check names `--color no` and an empty
   `HOME` on every `bws` call. Step 3 makes no `bws` call: it scans plists, `~/.factory/env`,
   `hq-env.sh` and the shell for variable names. So step 3 needed no change.
6. **P-VB's first post-seal verify (plan lines 340-346).** `verify-backup.sh` activates itself
   (line 24) and runs Sunday 03:00. It restores the `latest` `mini-host` snapshot (lines 206-243),
   which the daily 02:00 backup takes, so it reads the first 02:00 snapshot after the seal. A seal
   between 02:00 and 03:00 on a Sunday makes that run warn.
7. **Step 11 (plan line 456).** `com.devon.bump-driver` runs at minute 50 of every hour
   (`scripts/com.devon.bump-driver.plist` lines 20-30, and the live plist), isn't among the four
   jobs step 1 pauses, and `run-bump-driver.sh` activates the orchestrator tree (line 64).
8. **`check_chain`'s citation (plan lines 98, 269, 488; report "What I built" and the citation
   table).** It's lines 392-404. The plan's "returns silently" range, lines 395-397, was already
   right.
9. **Open questions (plan lines 482-540, 246).** Questions 1 to 3 are marked "To Devon". Questions
   4 to 8 are marked "Settled by HQ", each with HQ's choice: 4, the import fetches under
   `factory-events-session`; 5, the token is minted in step 0; 6, as item 2; 7, the pause stays;
   8, no session `verify`. P-OR-2 now says the wrapper has no `verify`.

### Checks run

- `git merge --ff-only bed48124` brought the branch to the PR head. `git reset --hard` was denied
  by this session's permissions; the tree was clean and `7739b22a` is an ancestor of `bed48124`,
  so the result is the same commit.
- I read every new citation at the commits named in the citation table. I read no secret, ran no
  `bws` or `security` command, and opened no database connection.
- `make check` doesn't apply: the change is docs only, under `docs/superpowers/**` (`layers.toml`
  line 755).

## Standards followed

| Standard | How the plan meets it |
| --- | --- |
| Code Brain rule 5: never log secrets | Every password goes to a child's environment only: an assignment prefix, `extra`, or the wrapper's child. Every BWS token goes to its `bws` process's environment only: an assignment prefix (the nightly's form, and P-OR-1's fixed `sds_bws_value`, which P-OR-2 uses), or sds-hq's `subprocess.run` `env`. No `export`, no argv, no log line. Tests in P-SS, P-OR-1 and P-OR-2 assert that argv and reports hold no stub password and no stub token, and P-OR-1's helper test asserts the token isn't in `env`'s argv. Checks print key names, `jq -r .key`, hashes and heads. |
| Code Brain rule 4: never silently swallow an exception | With the switch on, every failed fetch, missing item, or bad settings file fails the pass (`sds_outcome_fail`, `fail`, or `Refusal`). Nothing falls back to the file. The plan flags `check_chain`'s silent return and requires a refusal in database mode. |
| Code Brain rule 3: structured logs to stdout, no new log files | The plan adds no log file. The nightly's existing `LOG_FILE` is unchanged, and the wrapper's output is its child's. |
| Infra Brain 591, 592, 593: no BWS token in a tracked file | Tokens are read from Keychain items at run time. Manifests and the plan name UUIDs and item names only, and the plan pastes no token shape. |
| Infra Brain 1032: a credential in a transcript is exposed | Devon stores each token with `-w` and no value on the command line. The session never prints a value. I read no secret: no `bws`, no `security find-generic-password`, no database connection. |
| Infra Brain 212: HTTPS for public FQDNs | Doesn't apply. The database has no public FQDN (ADR-0059 amendment 7 item 5). |
| Construction mode | FE-5 is one announced act (`fe-5-cutover`). Devon's only part is minting three tokens into the Keychain. The design forks are left as open questions, not gates. |

## Follow-ups

- The plan's follow-ups 1 to 5: Keychain-token registry rows, an absent-switch refusal in the
  launchers, retiring sds-hq's file path, a second pin bump before 3f-2, and FE-6's `ship` and
  `FACTORY_DB_DSN`. The former follow-up 6 is now part of P-OR-1.
- Open questions 1 (`check_chain`'s role), 2 (re-import after rollback) and 3 (the VPS verify) go
  to Devon, and must be answered before dispatching P-HQ and the act. HQ settled 4 to 8.
