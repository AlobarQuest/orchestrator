Outcome: done

# FE-5 P-OR-1 build report: the bump proposer's and the Mac carrier's chain fetch

- **PR:** https://github.com/AlobarQuest/orchestrator/pull/703, against `main`, not merged.
- **Base:** `origin/main` `8f81b9ef`.
- **Code head:** `375a3164`, three code commits: `8f9daa22` (the change), `2d3ce6bc` (review fixes)
  and `375a3164` (a stronger refusal assertion). This report is committed on top as docs only, so
  read the PR's head for the final sha.
- **Worker:** W-157. Fix round 0.

## What I built

The plan's P-OR-1 "Changes" (plan lines 189-213 at `8f81b9ef`), in full.

1. **Helper fix** (`scripts/sds-bws.sh` line 54).
   - The two-argument branch runs `BWS_ACCESS_TOKEN="$2" env -u FORCE_COLOR -u CLICOLOR_FORCE bws ...`.
   - The token is in `env`'s environment, not its argv. `--color no` stays on both branches.
   - A header paragraph says why.
2. **Shared switch reader**, in `scripts/sds-bws.sh`, not a new file. Every harness that copies these
   launchers already copies `sds-bws.sh`, P-OR-2's wrapper sources it anyway, and `layers.toml`
   already places it. A separately sourced file that went missing would make the presence check
   return 127, which reads as "file absent", a fallback to the file chain. The reader has these parts:
   - `FACTORY_EVENTS_SWITCH_FILE` (`$HOME/.factory/chain-db.env`) is the default path for both
     functions. P-OR-2 can pass `chain-db.env.pending`.
   - `factory_events_switch_present [path]`: true if the path exists or is a symlink, dangling or not.
   - `factory_events_read_settings [path]`:
     - It never sources the file. It unsets, then sets, `CHAIN_DB_HOST`, `CHAIN_DB_PORT` and
       `CHAIN_DB_NAME`, unexported.
     - It refuses as PR #93's parser does (security-standards `4e83d7a`, lines 129-167): an unknown
       key, a missing key, a duplicate key, an empty value, a blank line or any line without `=`, a
       value outside `[A-Za-z0-9._:-]`, a path that isn't a regular file or is a symlink, and an
       unreadable file.
     - It prints nothing and calls no outcome alarm.
   - `factory_events_settings_refusal` prints the reason: the file, the line number and the key,
     never a value.
   - It uses builtins only. The line number is a string's length, because
     `tests/architecture/test_runtime_edges.py` refuses `$((`, and its `SHELL_ROWS` row for
     `sds-bws.sh` stays unchanged.
3. **Bump proposer** (`scripts/run-bump-proposer.sh`, replacing the pass-through comment at lines
   264-271).
   - With the switch present, every run reads it, so a malformed file fails a dry run too.
   - A `--submit` run does the following:
     1. Reads `Claude` / `BWS_ACCESS_TOKEN_BUMP_PROPOSER` with `/usr/bin/security`. There's no
        environment override, so a caller can't put the token in children's environments.
     2. Fetches `2ce0dbc7-…` with `sds_bws_value <uuid> <identity>`, then unsets the identity.
     3. Calls `sds_outcome_fail` on an empty identity or value.
     4. Passes the four variables as an assignment prefix on the `bump-proposer` command only.
   - A dry run fetches nothing and passes nothing.
4. **Carrier** (`scripts/run-work-carrier.sh`).
   - The block sits just after the observer-bearer block, not inside lines 209-232. The bare pass
     resolves `SDS_IDENTITY` at line 271, so this is the first point where both paths have it. The
     identity is the same `sds-operator` read the plan names (line 210 on `--register`).
   - With the switch present and an identity, it fetches `2f708d08-…`. An empty value is
     `sds_outcome_fail`.
   - A bare pass with no identity skips the fetch and writes one `[chain]` line to the report.
   - The four variables go as a prefix on the `work-carrier` command only, never `work-watcher`.
5. **Both launchers.**
   - Each unsets its own names (`CHAIN_*`, and `BUMP_PROPOSER_EVENTS_IDENTITY` in the bump
     proposer) before use, and exports nothing.
   - Each unsets `CHAIN_PASSWORD` after its one child.
   - Neither touches `FACTORY_EVENTS_HOME`.
   - **Added after review:** with the switch present, each clears ambient
     `FACTORY_EVENTS_{APPEND,READ}_PASSWORD` and `FACTORY_EVENTS_DB_*`, so a dry run, `work-watcher`,
     and a bare carry with no identity receive none. Retired `FACTORY_EVENTS_*_DSN` names stay set,
     so the child refuses them by name (`chain.py` `RETIRED_VARS`).
   - Without the switch, nothing is set or cleared. A test pins that an ambient value still passes
     through, as on `main`.
6. **Manifests and docs.**
   - `.bws-secrets.toml`: both secrets by UUID, project `Factory Events`, with a comment naming each
     reader. No `lanes` key.
   - `.cred-consumers.toml`: four rows.
     - `factory-events-append-password` and `factory-events-read-password`: class
       `postgres-role-password`, `rotated_by_sds = false`. Each comment notes that amendment 24's open
       item leaves the type undecided, so the row is hand-rotated. The append row lists all four
       readers (bump proposer, session wrapper, sds-hq cycle, security-standards nightly). The read
       row lists the Mac carrier.
     - `bws-bump-proposer-token` and `bws-factory-events-session-token`: class `bws-machine-token`,
       `rotated_by_sds = false`, with Keychain keepers.
     - No row has a `created` date or fingerprint. The tokens don't exist until act step 0, and I
       ran no `bws`.
   - `docs/operations/credentials.md`: a new section, "The factory-events chain switch and its
     Keychain items".
   - `tests/architecture/edge_table.py` and `test_bridge_edges.py`: the bridge edge table now
     includes the two new secret reads. Before this, `test_each_launcher_fetches_exactly_its_bearers`
     failed on the bump proposer.
   - `layers.toml`: the new test file in `bridge`.

## Tests and checks

| Run | Result |
| --- | --- |
| `tests/scripts/test_sds_bws.py` | 76 passed |
| `tests/scripts/test_bridge_chain_switch.py` (new) | 36 passed (at `375a3164`) |
| `tests/scripts/test_sds_deadman.py` | 95 passed |
| `tests/scripts/test_launchd_installers.py` | 61 passed |
| `tests/scripts/test_bridge_launchers.py`, `test_packages_checkout_activation.py`, `tests/work_watcher/test_launcher_orchestration.py` (existing launcher harnesses) | 69, 25 and 15 passed |
| `make check` (ruff, ruff format, pyright, shellcheck, full pytest) at `2d3ce6bc`, clean tree, database `orchestrator_test_hq_w_157` | 14,962 passed, 1 skipped, exit 0 |
| infraops `dist/security-drift` parser (`c3359bf`; its `src/security-drift` equals `origin/main`) over all four listed registries | 51 credentials loaded, no duplicate id, no `credFindings` on the four new rows |
| CI on PR #703 at `375a3164` (Linux): Quality, Runner consumer compatibility, Rollout transcription currency | All three passed. Quality collected 14,963: 14,940 passed and 23 skipped. The local run also collected 14,963. The new files have no skip markers, so the extra CI skips are tests that need sibling checkouts or Mac-only files (for example, `test_admission_edges.py`, `test_destination_rule.py`, `test_packages_checkout_activation.py`). |
| BWS security scanner from the worktree root | 0 BLOCK. 2 WARN, both on `origin/main` already (the `*-migration/` glob; UUID `aaff45ca-…` in `install-rotation-worker-launchd.sh`) |

The launcher harness has these properties:

- It runs each real launcher from a throwaway tree, with a temporary `HOME` and
  `PATH=<stubs>:/usr/bin:/bin`.
- Its stubs are `security`, `bws`, `env`, `curl` (from `test_bridge_launchers.py`), `gh`, and the
  lane programs.
- It rewrites every `/usr/bin/security` in the copied scripts to the stub, and asserts none remains,
  so the real Keychain is never read.
- The `bws` stub answers each UUID with a distinct fake value and logs which identity came with
  which UUID.
- Every case checks that no stub token or password appears in argv, stdout or stderr (the report and
  the lane log), or a ping body.

The cases cover:

- No switch file, on all four runs: no fetch, and no chain variable set.
- No switch file, with an ambient chain variable: it still passes through.
- A submitting bump proposer: it fetches only through its own item, and the token is used for that
  one fetch.
- A dry run: no fetch and no chain variable.
- A missing item, or an unreadable password: the run pings `/fail` and the program never runs.
- The carrier on both passes: it fetches through `sds-operator`, and `work-watcher` gets no chain
  variable.
- The carrier with an unreadable password, on both passes, and with no identity, on both passes.
- A missing key or an unknown key, on all four runs.
- Pre-exported own names, and ambient chain variables with the switch present.
- Two controls.

**Discrimination.** I ran the final tests against a scratch tree holding `origin/main`'s three
scripts (`8f81b9ef`) and this branch's tests.

- 51 of the 112 new and extended tests fail: 23 of 76 in `test_sds_bws.py` and 28 of 36 in the new
  file.
- The 8 that pass there are expected:
  - the four file-absent pass-through controls
  - the plain-assignment control
  - the harness's absolute-path check
  - the dry run with the switch, because `origin/main` fetches nothing anyway
  - the registering carrier with no identity, because `origin/main` already fails at line 212
- The four file-absent "no fetch" cases fail on `origin/main` only at the argv-secret assertion. The
  stub `env` records the token as one of `env`'s arguments, which is the helper defect. Their
  no-fetch and no-variable assertions pass there.

**Mutations,** on a scratch copy of the branch. All three were caught:

- Removing the bump proposer's `unset` line: 2 tests fail.
- Handing the read password to `work-watcher`: 3 tests fail.
- Making `factory_events_settings_refusal` print nothing: all 8 malformed-switch launcher cases
  fail. They assert the reason in the lane log and the `/fail` body.

## The interpreter and `factory_events` finding

Read only. Nothing was pulled, checked out, or changed.

- **The interpreter.** `run-work-carrier.sh` runs `$REPO_ROOT/.venv/bin/work-carrier`, so
  `sys.executable` in `_verify_factory_chain` (`src/orchestrator/package_sources.py` line 250 at
  `8f81b9ef`) is the orchestrator main tree's venv Python, 3.14.3.
- **Where `factory_events` comes from.** It isn't the pin. `_security_standards_env()` (lines 233-243)
  puts `src/` of `_security_standards_dir()` on `PYTHONPATH`. That directory is
  `SECURITY_STANDARDS_DIR` if set (neither launcher nor plist sets it), else
  `~/Projects/security-standards`, the live checkout. `security-standards.pin.toml` governs only the
  image's registry bundle.
- **The pin, `31aa7d70`, has no database mode.** It has no `src/factory_events/chain.py` at all. It
  doesn't matter to the Mac carrier, but P-PIN still needs to raise it before 3f-2 (plan follow-up 4).
- **The live checkout's HEAD is `cb832f3`, which doesn't include `60ec80f`.** As of this session it
  has no password-plus-settings form. `origin/main` is `4e83d7a`, which has it. Act step 2's
  `activate_checkout` of `~/Projects/security-standards` is what brings it in before the switch flips.
  I ran `git fetch` in `~/Projects/security-standards` and `~/Projects/infraops-mcp-server`, which
  updated their remote-tracking refs only. Neither working tree changed.
- **Measured.** `git archive origin/main src/factory_events` (at `4e83d7a`) went into a scratch
  directory. `.venv/bin/python -I` then imported `factory_events.cli`, `factory_events.db`,
  `factory_events.chain` and `psycopg`. Every import succeeded, and the chain variable names match
  what the launchers pass. No connection was made.
- **psycopg version gap.** The orchestrator venv (and `uv.lock` lines 605-623) holds `psycopg` and
  `psycopg-binary` **3.3.4**. security-standards' `events` extra asks for `psycopg[binary]>=3.3.8`
  (`pyproject.toml` line 15 at `60ec80f`). The import works, but nothing here has run the database
  path under 3.3.4. See the concerns section.

## Line citations relied on

- **Plan** (`docs/superpowers/plans/2026-11-14-fe-5-cutover.md` at `8f81b9ef`): "The switch"
  (40-93), the holders' table (95-110), P-OR-1 (154-226), P-OR-2 (227-267), act steps 0 and 9,
  open questions 4 and 6, and follow-up 1 (546-575).
- **`scripts/sds-bws.sh` at `8f81b9ef`:** line 54, the two-argument branch.
- **`scripts/run-bump-proposer.sh` at `8f81b9ef`:**
  - 142: `sds_outcome_begin`
  - 173: sources `sds-bws.sh`
  - 191-192 and 210-211: the existing Keychain reads
  - 264-271: the pass-through comment and command, replaced
- **`scripts/run-work-carrier.sh` at `8f81b9ef`:**
  - 209-232: the `--register` identity and its fetches
  - 271-272: the bare pass's identity
  - 282-288: the observer block, after which the new block sits
  - 335-347: `work-watcher`
  - 349: `work-carrier`
- **`src/bump_proposer/standing.py` at `8f81b9ef`:** lines 61-70 (`_REDACTED_CHAIN_VARS`), 221
  (redaction) and 426 (`approve --by-policy`). These are consistent with the names passed.
- **`src/orchestrator/package_sources.py` at `8f81b9ef`:** lines 220-261.
- **security-standards `4e83d7a`:** `scripts/factory-events-nightly.sh` lines 112, 128 and 129-199
  (the parser loop and `run_factory_events`).
- **security-standards `60ec80f`:** `src/factory_events/chain.py` lines 17-31 (variable names and
  `RETIRED_VARS`), and `pyproject.toml` line 15.
- **infraops `c3359bf`:**
  - `src/security-drift/cred-rotation.ts` line 68 (`postgres-role-password`), line 88
    (`bws-machine-token`) and lines 251-257 (unknown class)
  - `src/security-drift/cred-consumers.ts` line 207 (class required) and the duplicate-id check
    across files
  - nothing in infraops reads a `keychain` consumer's item
- **`src/rotation_worker/registry.py` at `8f81b9ef`:** the rotation proposer, decomposer and worker
  select rows by `rotated_by_sds`, so a `false` row of class `postgres-role-password` reaches none
  of them. The decomposer is at `decompose.py:51`, the worker at `worker.py:1028`, and the checks
  at `checks.py:119`.
- **The `provider` values** (`postgres-role`, `bitwarden-sm`) are mine; the plan names none.
  `registry.provider_entries()` selects by provider alone, so I checked every caller at `8f81b9ef`:
  - `worker.py:827-830` (`_other_keepers`) skips any row that isn't `rotated_by_sds`.
  - `adapters/__init__.py:156` asks for `bws-lane-token`, and `adapters/change_manager.py:226`
    for change-manager. Neither is a provider these rows use.
  - `cli.py:410` (`pg-restore`) runs only on a credential id a person names.
  - `_entries` (`registry.py:141-155`) only parses TOML and checks the array shape.
  - Run on the worktree's file in-process, the orchestrator's reader loads all four rows. Each
    keeper resolves, its destinations render, each is `rotated_by_sds` false, and no id appears
    in the other three registry repositories' local files. No existing rotation reaches these
    rows.
- **ADR-0055 at `8f81b9ef`:**
  - amendment 24 item 11 and its table (about 3439-3459)
  - the open item "A rotation class for each secret (FE-4)" (about 3516-3521)
  - amendment 25 items 1-4 (3612-3647)
  - amendment 26 (3687 onward)

## Self-review

`/code-review` (medium) ran over `origin/main...HEAD` and returned six findings:

| Finding | What I did |
| --- | --- |
| With the switch present, an ambient `FACTORY_EVENTS_*` value could reach a dry run, `work-watcher` or a bare carry | Fixed: those names are cleared in switch mode. Tested by `test_with_the_switch_an_ambient_chain_variable_reaches_no_program`. |
| The password stayed set after its child ran | Fixed: `unset CHAIN_PASSWORD` after the child, in both launchers. |
| Refusing a blank line could take the lanes down over a cosmetic edit | Kept. It's PR #93's refusal, and the handoff asks every holder to match it. |
| The carrier uses the `sds-operator` identity, which has an environment override, and skips the fetch on a bare pass with no identity | Kept. That's the plan's account and the plan's skip. The "no override" sentence in credentials.md is about the two new items, and it's accurate. |
| `security ... || true` hides a locked Keychain | Kept. It matches every sibling read in both launchers. |
| The rows name `scripts/factory-events-session.sh` before it exists | Kept. The plan's "Files" list puts the session wrapper in these manifests. P-OR-2 adds the script. |

I also checked these points:

- File-absent behaviour equals `origin/main`, apart from the helper's argv. The proof is the
  pass-through tests on both trees, plus the four no-fetch tests, which fail on `origin/main` only
  at the argv-secret assertion.
- No password or token is exported, logged, or put in argv.
- I added no suppression comment. shellcheck SC2034 flagged two variables that only callers read. I
  fixed it by making the switch path the functions' default and adding the refusal accessor.

## Concerns

1. **psycopg version gap.** The orchestrator venv holds psycopg 3.3.4, and the `events` extra asks
   for 3.3.8 or later. The Mac carrier's database `verify` will run under 3.3.4. It imports, but
   nothing has exercised the connection path under it. HQ could raise the orchestrator pin (a
   Dependabot-shaped change), or accept the gap for the carrier's few reads.
2. **The live security-standards checkout lacks `60ec80f`.** That's expected, because act step 2
   activates it. Act step 2's source check needs to confirm that the HEAD includes `60ec80f`.
   Otherwise the carrier's `factory_events verify` won't understand the password-plus-settings form.
3. **The carrier's block placement.** It sits after the observer block, not at lines 209-232, for
   the `SDS_IDENTITY` reason in item 4 of "What I built". The command prefix is at the plan's line
   349 command.
4. **Two cases the plan leaves unstated.** Following "The switch" ("If any step fails, fail the pass
   visibly"), I made these choices:
   - A bare carrier pass with an identity but an empty value fails.
   - A dry run with a malformed switch fails, and doesn't ping.
5. **Healthchecks names in the registry.** The new `runtime-fetch` rows carry `healthchecks_check`
   only for the two orchestrator scheduled readers (`sds-bump-proposer`, `sds-work-carrier`). The
   sds-hq cycle and the security-standards nightly rows carry none, because I didn't verify their
   check names. They're notes for the manual checklist until a rotation type is decided.
6. **Standards coverage.** The handoff's standards covered everything this change touched.

## Standards followed

- **Code Brain rule 5 (BLOCK, never log secrets).**
  - Passwords and tokens are unexported shell variables, passed only as assignment prefixes.
  - The reader's refusals name no value.
  - Tests assert that no stub secret appears in argv, the report, the lane log or ping bodies.
  - This report and the PR body contain no value.
- **Code Brain rule 4 (BLOCK, never silently swallow).** Every new failure ends in
  `sds_outcome_fail`, and none falls back to the file chain:
  - an empty identity or value
  - a refused switch file
  - an unreadable password, on both carrier passes
- **Code Brain rule 3 (BLOCK, structured logs to stdout).** No log file was added. The one new
  `[chain]` line goes to the existing outcome report, which `sds_outcome_end` copies to stdout.
- **Infra Brain 591, 592 and 593 (BLOCK, no BWS token in a tracked file or commit, and no inline
  `BWS_ACCESS_TOKEN` value).**
  - Stub values are plain words, such as `stub-token-sds-operator`, and don't match the token shape.
  - The scanner reports 0 BLOCK findings.
- **Infra Brain 1032 (BLOCK, a credential seen in a transcript is exposed).** No real credential
  was read or seen:
  - no `bws` run
  - no real `security find-generic-password`, because the harness rewrites the path
  - no database connection outside the test's own database
- **Infra Brain 212 (HTTPS for public FQDNs).** It doesn't apply: the database has no public FQDN.
- **Workspace invariant: every parsed `bws` call passes `--color no`.** Both helper branches keep it.
  The tests assert the argv `secret get <uuid> --output json --color no`.
- **Construction mode.**
  - I drove this to an open PR, with no question file.
  - The only judgment calls were the plan-unstated cases in concerns item 4, which I settled by the
    plan's general rule.
  - I didn't touch the main tree, the real `~/.factory`, or `security-standards.pin.toml`.

## Follow-ups

- **P-OR-2.** Source `scripts/sds-bws.sh` for `sds_bws_value` and the reader. Call
  `factory_events_read_settings "$HOME/.factory/chain-db.env.pending"` for `import-file`, and use
  `factory_events_switch_present` for its "switch must be absent" refusal. Read the session item
  with `/usr/bin/security`, with no environment override, and stub it in tests the way
  `test_bridge_chain_switch.py` does.
- **Act step 0.** After Devon mints the two items, record `created` and `fingerprint_sha256_8` in
  `bws-bump-proposer-token` and `bws-factory-events-session-token`.
- **Act step 2's source check.** Confirm that the security-standards checkout HEAD includes `60ec80f`.
- **The psycopg 3.3.4 gap** (concerns item 1).
- **Teardown.** HQ decides when to remove worktree `hq-W-157`, its branch, and database
  `orchestrator_test_hq_w_157` after merge. Scratch directories `/tmp/w157-disc`, `/tmp/w157-mut`
  and `/tmp/w157-ss.*` remain, because `rm -rf` is denied in this session.
