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
- **Eight open questions.**
- **The removal PRs' test scope.** Unit tests in all three repositories plant approvals in an
  events file, and each R-entry says where those tests go.

The plan goes beyond the handoff's three repositories and three writers in four places:

- **sds-hq's `check_chain`** (`standing_approve.py` lines 391-403 at `585e916`) returns silently
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
| sds-hq `585e916` | `src/sds_hq/standing_approve.py`: lines 36-51, 87-117, 120-139, 227-251, 359-403 and 562-572. |
| sds-hq `585e916` | `src/sds_hq/secrets.py` lines 1-55; `src/sds_hq/lock.py` lines 153-162; `bin/hq-env.sh` lines 37-51; `CLAUDE.md` lines 16-23; `.bws-secrets.toml` lines 1-9. |
| sds-hq `585e916` | `docs/operations/operator-identity.md` lines 30-110; `state/goal.md` lines 33 and 59; `state/gates/factory-events-accounts.md` (G-59). |
| vps-backup `bd2b440` | `verify-backup.sh` line 255; `backup-mini.sh` lines 132-138; `BACKUP-STRATEGY.md` lines 344 and 560. |
| infraops-mcp-server `c3359bf` (local `origin/main`, not fetched this session) | `.cred-consumers.toml`: every row names a `class`. |

The following facts come from the live Mac on 2026-11-14. They're dated observations, and the act
rechecks each one:

- **Checkouts.** The orchestrator main tree's HEAD is `7739b22a`. The security-standards checkout
  is at `cb832f3`, clean, on `main`.
- **Schedules from live plists.** The rotation proposer runs at 06:30 and the activation sweep at
  07:10. `com.devon.sds-hq` has `StartInterval` 900.

## Self-review

- Each prep PR names its switch and what changes on merge. P-VB alone can't be prepped without an
  effect, so it's merged inside the act.
- The plan never puts a password in argv: each one goes as an assignment prefix or into a child's
  `env`. Its commands print only key names, hashes and heads.
- The plan keeps the ADR order: import (step 6, whose commit is the head check), seal (7), each
  holder's switch (9), removal merges (10), verify (12), resume (12 and 13). The nightly's verify
  at step 12 is also its resume, because its run appends.
- I made no grant, account or holder decision. The plan proposes names only: the file, the archive,
  the three Keychain items, the wrapper's home, and the import's fetch account (open question 4).
  The session wrapper has no `verify`, because a session reading the chain is a reader the ADRs
  don't name (open question 8). The Mac verify is the nightly's own run under its own token.
- The advisor's review led to these revisions: the staging file; the file-mode
  `--against-anchor`'s need for `FACTORY_DB_DSN` in step 4; the second `make install` after R-SS;
  the HQ clone's missing `psycopg`; and the removal PRs' test scope.

## Concerns

1. **No live proof per holder before the point of no return.** Step 10 merges the removals before
   any scheduled holder has run under its own token. The plan states this ("What the act doesn't
   prove before step 10"), and relies on each prep PR's stub tests plus first-run checks.
2. **`sds_bws_value` puts the token in `env`'s argv.** This is pre-existing. It's the plan's
   follow-up 6.
3. **I ran `git fetch`** in the main checkouts of `~/Projects/orchestrator`, `security-standards`,
   `intent-packages` and `sds-hq`. It updated remote-tracking refs only: no checkout, pull or edit.
   The handoff's "never edit, branch, check out, or pull" holds, but the fetch did touch the main
   tree's `.git`.
4. **The bump-proposer token conflict.** G-59's page defers that token, but ADR-0055 amendment 25
   item 3 needs it on the Mac for FE-5. The ADR governs, and the plan mints the token in step 0. It's
   listed as open question 5, because it changes Devon's part of G-59 as written.

## Standards followed

| Standard | How the plan meets it |
| --- | --- |
| Code Brain rule 5: never log secrets | Every password goes to a child's environment only: an assignment prefix, `extra`, or the wrapper's child. No `export`, no argv, no log line. Tests in P-SS, P-OR-1 and P-OR-2 assert that argv and reports hold no stub password. Checks print key names, `jq -r .key`, hashes and heads. |
| Code Brain rule 4: never silently swallow an exception | With the switch on, every failed fetch, missing item, or bad settings file fails the pass (`sds_outcome_fail`, `fail`, or `Refusal`). Nothing falls back to the file. The plan flags `check_chain`'s silent return and requires a refusal in database mode. |
| Code Brain rule 3: structured logs to stdout, no new log files | The plan adds no log file. The nightly's existing `LOG_FILE` is unchanged, and the wrapper's output is its child's. |
| Infra Brain 591, 592, 593: no BWS token in a tracked file | Tokens are read from Keychain items at run time. Manifests and the plan name UUIDs and item names only, and the plan pastes no token shape. |
| Infra Brain 1032: a credential in a transcript is exposed | Devon stores each token with `-w` and no value on the command line. The session never prints a value. I read no secret: no `bws`, no `security find-generic-password`, no database connection. |
| Infra Brain 212: HTTPS for public FQDNs | Doesn't apply. The database has no public FQDN (ADR-0059 amendment 7 item 5). |
| Construction mode | FE-5 is one announced act (`fe-5-cutover`). Devon's only part is minting three tokens into the Keychain. The design forks are left as open questions, not gates. |

## Follow-ups

- The plan's follow-ups 1 to 5: Keychain-token registry rows, an absent-switch refusal in the
  launchers, retiring sds-hq's file path, a second pin bump before 3f-2, and FE-6's `ship` and
  `FACTORY_DB_DSN`.
- The plan's follow-up 6: `sds_bws_value`'s token argv.
- Settle open questions 1 (`check_chain`'s role), 2 (re-import after rollback), 3 (the VPS verify),
  5 (the bump-proposer token), 6 (registry `class`) and 8 (a session `verify`) before dispatching
  P-HQ, P-OR-1 and the act.
