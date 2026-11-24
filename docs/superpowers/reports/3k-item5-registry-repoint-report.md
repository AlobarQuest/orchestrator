Outcome: done

# 3k item 5: re-point orchestrator's credential registry off the deleted fork-chain launchers

- PR: #803 (not draft; HQ merges after review).
- Head of the code change: `659967b0e1901a3d6e82267c03c309fde4f62126` (this report is committed on top of it).
- Base: `ee88b4a5`.

## What I built

orchestrator's `.cred-consumers.toml` no longer names the fork-chain launchers that #753 deleted.
The following rows changed:

| Credential | Row at `ee88b4a5` | Change |
|---|---|---|
| `bws-sds-operator-token` | 101-106, `orchestrator` `scripts/run-fork-harden.sh`, check `sds-fork-hardening` | Removed |
| `bws-sds-operator-token` | 157-162, `orchestrator` `scripts/run-tool-installer.sh`, check `sds-tool-installer` | Re-pointed: `repo = "localops"`, same `ref` and `healthchecks_check`, `note` rewritten (lines 150-155 after the change) |
| `orchestrator-system-bearer` | 216, `ref` list | `run-tool-installer.sh` removed; the rest of the list unchanged |
| `orchestrator-observer-bearer` | 403-408, `scripts/run-fork-harden.sh` | Removed |
| `orchestrator-observer-bearer` | 445-450, `scripts/run-tool-installer.sh` | Removed |

The re-pointed row's note reads: "launchd com.devon.tool-installer 03:15 (its plist passes
--install); loads Keychain Claude/BWS_ACCESS_TOKEN_SDS each run through localops' sds-token.sh,
and on an acting pass (--install without --dry-run) reads the sds-bridge alert sender token from
BWS with it; pings sds-tool-installer through sds-deadman.sh, which resolves the ping URL with the
broad account, not this token".

I also removed one paragraph from `docs/operations/rotation-executor.md`, which the handoff didn't
name. It described the removed fork-harden observer row, so it became false with this change. Its
text, kept here per the placement rule: "`sds-fork-hardening` is an outcome alarm:
`run-fork-harden.sh` pings `/fail` on every non-zero exit, so a held or ungranted fork keeps the
check down, and the `run-fork-harden.sh` row on the observer bearer then has no answer. Retiring an
old observer value waits until that pass exits 0."

No test or fixture pinned these rows, so no test changed. `tests/fixtures/rotation_destination_format.json`
line 153 still names `run-tool-installer.sh` in a format example; I left it as the handoff
requires.

## Step 2 findings: whether `repo = "localops"` is allowed

Nothing requires a `runtime-fetch` consumer's `repo` to be a registry repository or a member.

- `src/rotation_worker/destinations.py` 47: `runtime-fetch` renders `repo` and `ref` as free
  strings. intent-packages' copy (`src/intent_packages/destination_format.py` 32 at `origin/main`)
  is the same, so a revision may carry `repo="localops"`.
- `src/rotation_worker/checks.py` 311-321 (`_check_runtime_fetch_verifiable`): it checks only that
  each `runtime-fetch` destination is a key of `reads`; it never reads `repo`.
- `src/rotation_worker/readers.py` 124-146 (`scheduled_reads`): it keys each read by the rendered
  destination and checks only the read fields.
- `src/membership_check/consistency.py` 165-190 and `declaration.py` 178, 305-320: these read the
  repository that owns a `.cred-consumers.toml` (against `REGISTRY_REPOSITORIES`), never a
  consumer's `repo`.
- infraops' loader (`src/security-drift/cred-consumers.ts` 31 at `origin/main`) types `repo` as an
  optional string.
- Tests: the `*_rows.py` tests match `repo == "orchestrator"` only for their own launchers;
  `tests/membership_check/test_rotation_scope.py` tests owner repositories.

I didn't add localops to `REGISTRY_REPOSITORIES` or the member record.

What localops reads, at `origin/main` `4360c0c`:

- `scripts/run-tool-installer.sh` 83 arms the dead-man switch for `sds-tool-installer`.
  `scripts/sds-deadman.sh` resolves the ping URL with the broad account
  (`BWS_ACCESS_TOKEN_VPS_BACKUP`, the `sds_deadman_resolve` function), not the sds-operator token.
- Line 93 sources `scripts/sds-token.sh`, which loads Keychain `Claude/BWS_ACCESS_TOKEN_SDS` into
  `BWS_ACCESS_TOKEN` on every run.
- Lines 99-115 read the `sds-bridge` alert sender token from BWS with that token only on an acting
  pass (`--install` without `--dry-run`).
- `scripts/com.devon.tool-installer.plist` 28-41 passes `--install` at 03:15, and the installed
  `~/Library/LaunchAgents/com.devon.tool-installer.plist` runs
  `/Users/devon/Projects/localops/scripts/run-tool-installer.sh --install`. So the scheduled pass
  acts, and it reads the token on each run.

localops' `scripts/run-fork-harden.sh` 24-26 reads only the Healthchecks key through the broad
account, which confirms dropping the fork-harden row.

## Step 3 finding: the scheduled read

The re-pointed row keeps `healthchecks_check = "sds-tool-installer"`, so it still has a scheduled
read. I ran `scheduled_reads` and `credential_destinations` in-process on the edited file:

- `bws-sds-operator-token`: 20 destinations, and every `runtime-fetch` destination is a key of
  `reads`. The localops row maps to `CheckRead(check='sds-tool-installer')`.
- `orchestrator-observer-bearer`: 15 destinations, every `runtime-fetch` destination has a read.

`_check_runtime_fetch_verifiable` therefore passes for both, and no rotation code change is needed.
## Step 5 findings: packages against the registry

I compared each standing package's `profile_fields.destinations` at intent-packages `origin/main`
with the registry, before (`ee88b4a5`) and after this change.

### `rotation-orchestrator-observer-bearer` (rev 2, approved 2026-10-28)

- Introduced by this change (package-only lines, to drop in rev 3): package line 58
  `runtime-fetch repo="orchestrator" ref="scripts/run-fork-harden.sh"` and line 64
  `runtime-fetch repo="orchestrator" ref="scripts/run-tool-installer.sh"`.
- Pre-existing (registry-only, to add in rev 3): `run-change-proposer.sh`, `run-execution-watch.sh`,
  `run-work-carrier.sh`.
- Next due: the age anchor is infraops' `lastRotated` = 2026-10-21 (Mac
  `~/.config/infra-drift/cred-rotation-state.json`); class `orchestrator-m2m-bearer` max age 365
  days. Due once the age exceeds 365 days, so from 2027-10-21.

### `rotation-bws-sds-operator-token` (rev 2, approved 2026-10-29)

- Before this change, the package already differed: the registry had seven readers the package
  lacks (`run-bump-driver.sh`, `run-change-proposer.sh`, `run-execution-watch.sh`,
  `run-fork-harden.sh`, `run-membership-check.sh`, `run-rotation-decomposer.sh`,
  `run-unit-verifier.sh`). This change removes the `run-fork-harden.sh` difference.
- Introduced by this change: package line 47 `runtime-fetch repo="orchestrator"
  ref="scripts/run-tool-installer.sh"` is package-only, and `runtime-fetch repo="localops"
  ref="scripts/run-tool-installer.sh"` is registry-only.
- The 15 stale rows from 3h are in this file: every `orchestrator` `runtime-fetch` row under this
  credential except `run-activation-sweep.sh` (line 55). After the change their `ref` lines are 62
  (`run-bump-driver.sh`), 69, 76, 83, 90, 97, 104, 111, 118, 125, 132, 139, 146, 160 and 167
  (`run-work-carrier.sh`). Of those, the package carries nine at lines 39-46 and 48
  (`run-bump-proposer`, `run-deploy-watcher`, `run-estate-landing`, `run-inert-landing`,
  `run-landing-ledger`, `run-pin-watcher`, `run-revision-watcher`, `run-rotation-proposer`,
  `run-work-carrier`); the other six (`run-bump-driver`, `run-change-proposer`,
  `run-execution-watch`, `run-membership-check`, `run-rotation-decomposer`, `run-unit-verifier`)
  are registry-only. I didn't change any of them.
- Next due: no `lastRotated` in the Mac's state file and no `last_rotated` in the row, so the
  anchor is `created` = 2026-07-30; class `bws-machine-token` max age 365 days. Due from
  2027-07-30.

### `rotation-bws-tok-ops-mini-20260730` (rev 1, approved 2026-10-23)

- Today the package and infraops' registry agree (22 destinations each).
- When infraops row 343 is re-pointed, package line 75 `runtime-fetch repo="orchestrator"
  ref="scripts/run-tool-installer.sh"` becomes package-only and the localops row registry-only.
- Next due: no `lastRotated`, anchor `created` = 2026-07-30, class `bws-machine-token`, due from
  2027-07-30.

The anchor rule is infraops `src/security-drift/cred-rotation.ts` 211
(`state.lastRotated[id] ?? spec.last_rotated ?? spec.created`) and the age test is lines 288-291
(`ageDays > policy.maxAgeDays`). I read the Mac's state file; the containerized proposer might read
a different copy of it, which I didn't check.

### Whether the proposer revises on its own, and whether anything alerts first

- The proposer opens a revision only for a credential infraops reports due. `_pass` walks only the
  `due` list (`src/rotation_proposer/cli.py` 764), and `_decide` (180-187) turns an approved package
  whose destinations differ into an `advance` (a revision for review) only when it carries the due
  occurrence. `_walk_records` (702-748) compares no destinations. So none of the three packages is
  revised before its due date unless someone revises it by hand.
- No scheduled lane alerts on the difference before then. The only comparisons are refusals at
  use: the decomposer refuses an approved intake whose destinations differ
  (`src/rotation_decomposer/decompose.py` 71-78), and the executor refuses a claim
  (`src/rotation_worker/checks.py` 126-132). `rotation_worker` `compare-destinations`
  (`src/rotation_worker/cli.py` 104) is operator-run; no launcher, lane or plist under `scripts/`,
  `lanes/` or `deploy/` calls it.

## Tests and checks

- `make check` at the code head: ruff, ruff format, and pyright clean; pytest 14776 passed, 1 skipped (collected 14777); the registry-reading suites (rotation_worker, rotation_proposer, rotation_decomposer, membership_check, work_carrier, test_sds_cred_registries) re-run on the final tree after a note edit: 2706 passed.
- In-process `scheduled_reads` coverage check, preceding.
- `code-standards risk --repo .`: `normal`.
- `/code-review` (low) over `ee88b4a5..HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `docs/operations/rotation-executor.md`
- `docs/superpowers/reports/3k-item5-registry-repoint-report.md`

## Self-review

- The diff touches no `[[credential.exposure]]` entry and no `consumers_verified`.
- The re-pointed row keeps its check name, which localops still arms at line 83.
- The header comment of `.cred-consumers.toml` says the orchestrator reader lists come from a grep
  of `scripts/` and `src/`; the localops row is the one reader outside this repository's
  `scripts/` besides `project-standards`, and its note says where it lives.

## Concerns

- The paired intent-packages revisions (observer rev 3, sds-operator rev 3 with the 15 stale rows,
  ops-mini rev 2 after infraops row 343) need Devon by name. Until they land, a rotation of either
  orchestrator credential is refused at decomposition, but neither is due before 2027-07-30.
- The `sds-tool-installer` check proves a run, not which BWS identity it read with; that is the
  existing read model for every `healthchecks_check` row (`readers.py` 1-40).

## Standards followed

- Code Brain 3 (structured JSON logs to stdout), 4 (never silently swallow an exception), 5 (never
  log secrets): no code changed; no test changed.
- Infra Brain 591 `bws.no-token-in-tracked-files`, 592 `bws.no-token-in-git-history`, 593
  `bws.bootstrap-token-not-inline`: the diff and this report hold no token-shaped string; I named
  Keychain items and BWS UUIDs only, and made no BWS call.
- Infra Brain 1032 `cred.exposure-rotate`: the registry stays `.cred-consumers.toml` in its owning
  repository; no `[[credential.exposure]]` entry added or removed; `consumers_verified` unchanged.
- Infra Brain 212 (HTTPS): not applicable.
