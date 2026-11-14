Outcome: done

# FE-5 cut-over plan: build report

## What I built

PR [#702](https://github.com/AlobarQuest/orchestrator/pull/702), branch `hq/fe-5-cutover-plan`,
head `74384e1f` (this report is a later commit on the same branch). The PR adds one file,
`docs/superpowers/plans/2026-11-14-fe-5-cutover.md`, which contains the following:

- **The switch.** One non-secret settings file, `~/.factory/chain-db.env`, holds the three
  settings. Every holder's new fetch runs only while the file exists. A Keychain item can't serve
  as the switch, because `sds-hq` and `sds-operator` already hold both their item and their grant.
  The file isn't `~/.factory/env` either, because the nightly sources that file with `set -a`, and
  HQ's `env -i` passes only `HQ_*` variables.
- **The holders table.** It has five Mac holders plus the container carrier, which belongs to 3f-2.
- **Six prep PRs, in landing order.**
  - P-SS, security-standards: the nightly's database mode. It's `make install`-deployed, so a merge
    doesn't change it.
  - P-OR-1, orchestrator: the bump proposer and Mac carrier fetches, and the manifests.
  - P-OR-2, orchestrator: the session wrapper `scripts/factory-events-session.sh` and an
    operations page.
  - P-HQ, sds-hq: the cycle's in-process fetch, the `check_chain` guard, and
    `SECURITY_STANDARDS_MIN`.
  - P-PIN, orchestrator: `security-standards.pin.toml` past `60ec80f7`.
  - P-VB, vps-backup: `verify-backup.sh` line 255. It's opened before the act and merged inside it.
- **Three removal PRs, merged inside the act.** They are R-SS, R-IP and R-OR. R-OR carries FE-3's
  deferred unset-read-password refusal.
- **The act.** It's steps 0 to 14, each with who runs it, a command or check, and a source check.
  The rollback point sits before the seal, and the plan says what rolls back at each later step.
- **Seven open questions.**

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
| security-standards `60ec80f` | `governance-map.toml` lines 74-81; `scripts/com.devon.factory-events.plist.template`; `.bws-secrets.toml`; `Makefile` lines 102-105. |
| intent-packages `93d75e1` | `src/intent_packages/operations.py`: lines 429-573. |
| intent-packages `93d75e1` | `docs/decisions/0001-approval-chain-is-the-audit-trail.md` lines 56-57. |
| sds-hq `585e916` | `src/sds_hq/standing_approve.py`: lines 36-51, 120-139, 227-251, 359-403 and 562-572. |
| sds-hq `585e916` | `src/sds_hq/secrets.py` lines 1-55; `src/sds_hq/lock.py` lines 153-162; `bin/hq-env.sh` lines 37-51; `CLAUDE.md` lines 16-23; `.bws-secrets.toml` lines 1-9. |
| sds-hq `585e916` | `docs/operations/operator-identity.md` lines 30-110; `state/goal.md` lines 33 and 59; `state/gates/factory-events-accounts.md` (G-59). |
| vps-backup `origin/main` (2026-11-14) | `verify-backup.sh` line 255; `backup-mini.sh` lines 132-138; `BACKUP-STRATEGY.md` lines 344 and 560. |
| infraops-mcp-server `origin/main` | `.cred-consumers.toml`: every row names a `class`. |

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
- The plan honours the ADR order: import, head check, seal, holder settings, removal merges,
  verify, resume. It inserts a non-appending check (step 9) before the removals. It writes the
  switch file before the import, which is safe because every scheduled holder is paused at that
  point and the wrapper needs it. This is a deviation from "set each holder's switch" after the
  seal, and HQ needs to accept it. The alternative is a separate settings path for the import.
- I made no grant, account or holder decision. The plan proposes names only: the file, the archive,
  the three Keychain items and the wrapper's home (open question 4).

## Concerns

1. **The switch file goes in before the seal.** Act step 5 writes it before the import, so the
   holders are technically switched from step 5. They're paused, so nothing reads it until step 13.
   HQ needs to confirm this reading of "set each holder's switch".
2. **`sds_bws_value` puts the token in `env`'s argv** (`scripts/sds-bws.sh` line 55,
   `env ... BWS_ACCESS_TOKEN="$2" bws`), so it's briefly visible in the process list. This is the
   estate's existing pattern for tokens. No password goes on argv. The new fetches reuse the helper,
   so they inherit it.
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
- Settle open questions 1 (`check_chain`'s role), 2 (re-import after rollback), 3 (the VPS verify),
  5 (the bump-proposer token) and 6 (registry `class`) before dispatching P-HQ, P-OR-1 and the act.
