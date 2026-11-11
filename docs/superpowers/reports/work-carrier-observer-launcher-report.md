Outcome: done

# Report: the work carrier's launcher fetches its observer credential (ADR-0055 a22, 3e-cid-4)

Worker W-95. PR [#667](https://github.com/AlobarQuest/orchestrator/pull/667), ready for review,
not merged. Code head `ebb7df74`; this report is committed on top of it. Base `origin/main` =
`671acd1d`, the commit the handoff read; every fact it cited held there.

## What I built

- **Launcher.** `scripts/run-work-carrier.sh` gains `WORK_CARRIER_OBSERVATION_UUID` (line 113,
  beside `ORCHESTRATOR_SYSTEM_UUID`, with a comment). Inside the first `--register` block (lines
  194-217), after the SYSTEM fetch and with the same `SDS_IDENTITY`, it exports
  `WORK_CARRIER_OBSERVATION_KEY_ID=work-carrier-observer` and fetches
  `WORK_CARRIER_OBSERVATION_TOKEN` through `sds_bws_value` only when it isn't already set. An
  unreadable token calls `sds_outcome_fail`. The header's two-identities block names the new
  fetch.
- **`.bws-secrets.toml`.** A `[[secret]]` entry for `a9843d17-…`, `work-carrier-observer-bearer`,
  `SDS Operator`, with a comment naming its one reader. No `lanes` key.
- **`.cred-consumers.toml`.** A `work-carrier-observer-bearer` row in the M2M bearers section,
  following `orchestrator-verifier-bearer`: class `orchestrator-m2m-bearer`, provider identity
  naming key id `work-carrier-observer`, `ActorRole.OBSERVER` and agent id `work-carrier`,
  `bws_uuid`, `created = "2026-11-11"`, `disposition = "reissue"`, and
  `fingerprint_sha256_8 = "91de9b26"`. Its consumers are `bws-secret` (the keeper),
  `coolify-env-hash` (`prod`, `eqj5l7k705fhi12x9i74fqf0`, `ORCHESTRATOR_M2M_CREDENTIALS`, entry
  `work-carrier-observer`) and `runtime-fetch` (`scripts/run-work-carrier.sh`,
  `healthchecks_check = "sds-work-carrier"`). There's no `rotated_by_sds`, no
  `rotate_requested` and no exposure.
- **Docs.** `docs/operations/rotation-proposer.md` names the launcher as the source of both
  variables, says an unreadable token fails the pass, and says a bare pass reports each standing
  approval refused. `docs/operations/authentication.md` gets a paragraph after the add-identity
  procedure naming the launcher and the BWS secret.
- **Tests.**
  - `tests/scripts/test_bridge_launchers.py` has five new tests: a `--register` pass fetches the
    UUID and exports both names; a bare pass does neither; a preset token isn't fetched again; an
    unreadable token exits 1, pings `/fail` with its message, and runs no program; the launcher's
    UUID equals the manifest entry.
  - `tests/work_watcher/test_launcher_orchestration.py` presets the new token in its
    `CREDENTIALS` fixture, as it presets every other one.
  - The bridge edge table declares the bearer (the deviation listed under the following heading).

## Deviation: the architecture guard edit, approved by HQ

`tests/architecture/test_bridge_edges.py::test_each_launcher_fetches_exactly_its_bearers`
refused the launcher, because it names a UUID no `Edge.bearers` declares. Following the
handoff, I stopped and asked (`work-carrier-observer-launcher-question.md`). HQ approved
exactly two edits, which I made in `ebb7df74`:

- `tests/architecture/edge_table.py`: `WORK_CARRIER_OBSERVATION_BEARER` beside `SYSTEM_BEARER`,
  with a comment.
- `test_bridge_edges.py`: it imports the constant and adds it to the `work_carrier` row's
  `bearers` only.

I changed nothing else under `tests/architecture/`.

Control: with the table entry reverted, the guard failed (`1 failed, 2 passed`, the
`run-work-carrier.sh` case). With it restored, the guard passed (`3 passed`). I cleared
`__pycache__` around the mutation.

## Checks

1. **`make check`** with `PGTZ=UTC` and database `orchestrator_test_hq_w_95`, at `ebb7df74`:
   14568 collected, 14567 passed, 1 skipped, 0 failed. Exit 0, with ruff, pyright
   (`0 errors, 0 warnings`) and shellcheck clean.
   - The first run, before the fixture fix and the guard edit, had 11 failures: ten in
     `test_launcher_orchestration.py` and the guard.
2. **`bash -n scripts/run-work-carrier.sh`**: clean. **`shellcheck`**: clean. The repo has
   `.shellcheckrc`, so `make check` runs shellcheck over every `*.sh` (`Makefile:66`).
3. **The bare form fetches nothing new.** Shown from the script text without running it:
   - The only lines that name the observation UUID or the two variables are line 113 (the
     constant) and lines 209-216.
   - Lines 209-216 sit inside `if [ "$_wants_register" -eq 1 ]; then` (line 194), closed at line
     217.
   - The bare `else` branch (lines 246-261) is unchanged.
   - `test_a_bare_carrier_neither_fetches_nor_exports_its_observation_identity` holds this, and
     a mutation that fetched on a bare pass failed it.
4. **Schema source for the `.cred-consumers.toml` row**, read only, at infraops-mcp-server
   `origin/main` `c3359bf`:
   - `src/security-drift/cred-consumers.ts:47` declares `fingerprint_sha256_8?: string` on
     `CredentialSpec`, for every class.
   - `src/security-drift/cred-rotation.ts:247` reads it only for an exposure's detail text, and
     `CLASS_POLICY` (`cred-rotation.ts:24-60`) has no per-class field rules.
   - The consumer kinds and their fields follow `.cred-consumers.toml` lines 10-24 there.
   - The `healthchecks_check` pattern follows the verifier and observer rows in this repo.
   - No other M2M bearer row carries a fingerprint, because their rows predate recording one, not
     because of a schema rule.
5. **Fetch failure is `sds_outcome_fail` (exit 1).** The code supports the handoff's default:
   - `src/work_carrier/cli.py:537-540` computes `identified` from the environment in both modes.
   - `src/work_carrier/prepare.py:284-285` turns a policy rotation without the identity into
     `Refused(..., standing.REFUSED, NO_OBSERVATION_IDENTITY)`.
   - `cli.py:588-590` maps any refusal to `EXIT_FINDINGS` (3).
   - So carrying on would report a queued policy rotation with the wrong cause ("no carrier
     observation identity is configured") and would say nothing on a pass with none queued.
     Both conflict with Code Brain rule 4.
   - The launcher's own header defines exit 1 as including "an unreadable credential" (line 53),
     and its other `--register` fetches fail the same way (lines 203-204, 271-273).
   - The cost: an unreadable `a9843d17-…` stops the whole 07:05 pass, the retirement phase
     included.

Further checks:

- **Security scan** from the worktree root: 0 BLOCK, 2 WARN and 1 INFO.
  - The two WARNs predate this branch: the gitignore glob `*-migration/`, and
    `bws.manifest-matches-usage` for `aaff45ca-…`, which appears only in older reports, introduced
    in `d4d8ecba`.
  - The INFO is the judgment-only `bws.least-privilege-scope`.
- **`/code-review` low** on `origin/main...HEAD`: no findings.
- **Mutations of the launcher**, each run against the new tests and each restored:
  - Dropping the fail line made 1 test fail.
  - Fetching on a bare pass made 2 fail.
  - Ignoring a preset token made 1 fail.

## Files changed

- `scripts/run-work-carrier.sh`
- `.bws-secrets.toml`
- `.cred-consumers.toml`
- `docs/operations/rotation-proposer.md`
- `docs/operations/authentication.md`
- `tests/scripts/test_bridge_launchers.py`
- `tests/work_watcher/test_launcher_orchestration.py`
- `tests/architecture/edge_table.py`
- `tests/architecture/test_bridge_edges.py`
- this report

## Self-review

- The new failure message passes one argument to `sds_outcome_fail`. Its second argument is the
  exit code (`scripts/sds-deadman.sh:221-225`), and my first draft split the message across two
  arguments, which I caught and fixed before committing.
- The FATAL message ("the work carrier's own observation bearer") is distinct from the
  member-record bearer's ("observer credential"), so existing assertions still discriminate.
- The only `--register` runs were inside the stubbed test harness. I didn't run the real launcher,
  and I read, printed or fetched no secret value.

## Concerns

- The `sds-operator` account's read of `a9843d17-…` is unproven until the first scheduled
  `--register` pass after merge. The secret is in `SDS Operator`, which that account reads per
  `.cred-consumers.toml`'s `bws-sds-operator-token` header, but nothing in this session exercised
  the read. If it fails, that 07:05 pass exits 1 and pages through `sds-work-carrier`.
- A bare (inspecting) pass reports every policy-approved rotation revision as
  `standing_approval_refused`, because the identity is exported only under `--register`, as the
  handoff required. The scheduled pass would carry it. `rotation-proposer.md` says so.
- Main tree activation: the scheduled lane runs the main tree's working copy, so the change goes
  live there once merged and pulled by `activate_checkout`.

## Follow-ups

- Merge #667 (HQ), then read the next scheduled `sds-work-carrier` result to confirm the fetch.
- Teardown after merge: worktree, database `orchestrator_test_hq_w_95`, and the branch.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** the launcher's one new message goes
  through `sds_outcome_fail`, which writes the outcome report and pings `/fail`, the same path
  as every other message in the launcher. No new log channel.
- **Code Brain rule 4 (never swallow an exception silently):** a failed fetch is named
  ("FATAL: could not read the work carrier's own observation bearer from BWS") and fails the pass
  with exit 1. The choice and its grounds are in check 5. A test holds the message and the
  exit code.
- **Code Brain rule 5 (never log secrets):** no line prints the token. The message names neither
  the value nor the identity. The test stubs record variable names only, never values.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history, no inline bootstrap
  token):** the secret is referenced by UUID only, the identity comes from the Keychain at
  runtime, and the security scan reports 0 BLOCK.
- **Infra Brain 1032 (cred.exposure-rotate):** the new `.cred-consumers.toml` row records no
  exposure, because none is known. No value entered the transcript or any file.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply, because no domain changed.
