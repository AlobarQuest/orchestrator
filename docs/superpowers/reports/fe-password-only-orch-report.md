Outcome: done

# Build report: orchestrator chain readers connect from a password plus settings

Handoff: `fe-password-only-orch` (ADR-0055 amendment 26, increment 3). Worker W-149.
Branch `hq/fe-password-only-orch`, base `cc6d9afd`. PR:
[AlobarQuest/orchestrator#700](https://github.com/AlobarQuest/orchestrator/pull/700) (not
merged: HQ reviews and merges).

## What I built

Each factory-events reader in the orchestrator now uses the role's password and the three
non-secret settings. No reader accepts a whole DSN.

- **`factory_chain_read`** connects through security-standards `factory_events.chain`:
  `chain.connection_params(chain.READ_ROLE)`, strictly the read role, with `as_kwargs()` passed
  straight to `psycopg.connect`. The lazy import is kept (`import_module("factory_events.chain")`).
  The module reads no environment variable itself and no longer imports `os`.
- **`package_sources._verified_package_approval_events`** keeps the names as literals:
  1. A set retired variable (`FACTORY_EVENTS_APPEND_DSN`, `_READ_DSN`, `_IMPORT_DSN`) refuses by
     name, whatever else is set.
  2. `FACTORY_EVENTS_READ_PASSWORD` selects the database chain.
  3. `FACTORY_EVENTS_APPEND_PASSWORD` alone refuses rather than match the file.
  4. With none of these set, the file chain answers as before.

  `_security_standards_env` still copies the whole environment, which is what passes the
  password and the three settings to the child.
- **`bump_proposer/standing.py`**: `_redact_append_dsn` is now `_redact_chain_passwords`. It
  replaces each set password's value, and a set retired variable's whole value, with a marker
  naming the variable. It parses nothing and replaces the longest value first, as intent-packages
  `redact_chain_passwords` does.
- **`scripts/run-bump-proposer.sh`**: the comment names the append password and the three
  settings. It fetches nothing.
- **`tests/architecture/test_intent_edges.py`**:
  - `ENVIRONMENT` is the two passwords and the three retired names, plus the three it had.
  - `CHAIN_READ_ENVIRONMENT` is empty: the module reads no variable of its own.
  - `CHAIN_READ_LAZY_IMPORTS` adds `factory_events.chain`.
  - A new row, `CHAIN_READ_CONNECTIONS`, pins the one `chain.connection_params` call to
    `chain.READ_ROLE`.
  - The line-388 control now injects an `os.environ.get` of a password and swaps the role to
    `APPEND_ROLE`. It shows that both changes are reported.
- **Tests**: every whole-DSN fixture is now parts (`SECRET` plus `SETTINGS`). A retired variable
  is set only to prove that it refuses or that it's redacted. The integration tests build each
  role's host, port and database from `FACTORY_TEST_DSN`. Their wrong-port and wrong-password
  cases now change one variable each. New tests cover each missing setting (through FE-1's real
  `ChainError`) and a retired variable set beside a working read.
- **Docs**: `docs/operations/package-intake.md` step 1 names the password, the settings and the
  retired variables. No `CLAUDE.md` named the retired variables.

**Refusal text: dropped, not mirrored.** intent-packages prints `ChainError`'s text to stderr. The
orchestrator's parent never relays the child's stderr, and the child's one stdout record carries
types only (FE-3's design). So a missing setting reaches the operator as `factory event database
chain could not be read (ChainError)`, without the variable's name. Relaying the name would mean
putting another repository's exception text into the record. I left that out.

## Tests and checks run

- **`make check`** on the clean committed tree, per-worktree database `orchestrator_test_hq_w_149`:
  ruff, ruff format, pyright (0 errors) and shellcheck are clean.
  - pytest: **14,901 passed, 1 skipped**. The 11 integration tests are deselected by `addopts`.
  - The skipped test is `tests/services/test_conformance_claim.py:135`. It skips when
    project-standards (`portfolio`) isn't installed, so it depends on the machine; this change
    doesn't touch that file. A full `pytest -rs` run named it: 14,901 passed, 1 skipped,
    11 deselected.
- **`scripts/check-integration.sh`**: throwaway Docker `postgres:16-alpine`, with
  `SECURITY_STANDARDS_DIR` pointing at a `git archive` of security-standards `60ec80f7` in a new
  scratch directory. **11 passed** (7 existing tests, 3 missing-setting cases, 1 retired-variable
  case), 14,902 deselected. Re-run after the log-assertion commit: 11 passed. `tests/architecture/test_layer_manifest.py`
  with this report added: 3 passed.
- **Mutations** (each restored, `__pycache__` cleared). Every one failed at least one test:

  | Mutation | Failed |
  |---|---|
  | M1: read role swapped to `APPEND_ROLE` | 5 |
  | M2: the child reads a password itself | 6 |
  | M3: `IMPORT_DSN` dropped from the retired check | 3 |
  | M4: retired variables ignored | 6 |
  | M5: append password alone falls to the file | 2 |
  | M6: selector back on `FACTORY_EVENTS_READ_DSN` | 17 |
  | M7: redaction drops a retired name | 1 |
  | M8: redaction shortest-first | 1 |
  | M9: redaction drops the read password | 1 |

- **Leak probes.** pytest resets `caplog.text` for each phase, so a check at teardown saw nothing.
  My first log check passed a deliberate debug-level leak. I fixed it to read
  `caplog.get_records("call")`. Two leaks then failed it: a debug log of the password in
  `package_sources`, and a print plus debug log in the redactor.
- **`/code-review`** (medium) on `cc6d9afd..HEAD`: five findings, and I acted on one.
  1. Needs security-standards `60ec80f7`: I added a comment naming what an `AttributeError`
     means. See the concerns section.
  2. Redaction matches the whole value: amendment 26 item 8 decides this.
  3. A retired variable refuses with no migration window: item 5 decides this.
  4. A missing setting isn't named to the operator: that's the choice described earlier.
  5. Nothing pins the settings' pass-through: already covered, because
     `test_a_verified_walk_holding_the_approval_verifies` asserts each setting in the child's
     environment.
- **Security scanner** from the repo root: 0 BLOCK, 2 WARN, 1 INFO.
  - The WARNs are `bws.secret-files-gitignored` on `.gitignore` (`*-migration/`) and
    `bws.manifest-matches-usage` on `.bws-secrets.toml`, for a UUID in
    `scripts/install-rotation-worker-launchd.sh`.
  - `git diff cc6d9afd HEAD` is empty for all three files, so this change didn't introduce them.
  - My first try at a baseline scan isn't a valid control, so I don't cite it. It scanned a
    `git archive` with a bare `git init`, which has no tracked files.
- **Grep for renamed names.** The old test and function names appear only in the ADR, which
  cites code at a commit. They don't appear in the exit manifests, `layers.toml`, any
  `CLAUDE.md`, or `docs/operations`. The remaining `FACTORY_EVENTS_*_DSN` hits are:
  - refusal and redaction literals;
  - tests that prove refusal or redaction;
  - the conftest and integration clearing lists;
  - `FACTORY_EVENTS_OWNER_DSN` (amendment item 9).

## Files changed

- `src/orchestrator/factory_chain_read.py`
- `src/orchestrator/package_sources.py`
- `src/bump_proposer/standing.py`
- `scripts/run-bump-proposer.sh` (comment only)
- `docs/operations/package-intake.md`
- `tests/architecture/test_intent_edges.py`
- `tests/cli/test_package_chain_database.py`
- `tests/cli/test_package_chain_database_integration.py`
- `tests/bump_proposer/test_standing.py`
- `tests/conftest.py`
- `docs/superpowers/reports/fe-password-only-orch-report.md` (this report)

## Self-review

- The retired-variable check uses a literal dict of three `os.environ.get` calls, not a loop
  over a tuple. That way `test_intent_edges` reads each name, as it reads the passwords. The
  names repeat once.
- The unit tests fake `factory_events.chain` and the driver, because the vendored
  `tests/fixtures/security-standards` has no `chain`. The integration tests exercise FE-1's real
  `chain`, `db_setup` and `emit`.
- No test or check printed a credential value. The placeholder passwords are
  `pw-must-not-leak-7f3a`, plus a per-run `secrets.token_hex` for the throwaway cluster.

## Concerns

- **A runtime reads the database chain only from a security-standards checkout at or after
  `60ec80f7`.** Two runtimes are older:
  - the image's pin, `security-standards.pin.toml` revision `31aa7d70`, whose bundle ships
    `src/factory_events`;
  - the Mac's `~/Projects/security-standards` at `cb832f3`, one commit behind `origin/main`.

  On either, a set read password fails closed as `UNAVAILABLE (AttributeError)`, never as "not
  approved". No holder sets a password yet, and the file path is unchanged, so nothing breaks
  today. FE-5 must come after the Mac checkout is updated, and after the pin is bumped wherever a
  container reads the chain. I changed neither.
- **A stale retired variable now refuses every intake**, including one that would have used the
  file chain (amendment 26 item 5). The amendment records that no tracked launcher sets one.
- **No refusal names the missing setting** (see "Refusal text" earlier).

## Standards followed

- **Code Brain rule 5 (no secrets in logs).** No password reaches a log record, an exception, a
  repr or a stream:
  - `as_kwargs()` goes only to `psycopg.connect`;
  - the child's record holds types and hash prefixes only;
  - the parent relays no child text;
  - the redactor covers each password variable.

  The tests assert the placeholder's absence from exception text, call-phase log records at
  DEBUG, stdout and stderr. A probe showed each check fails on a leak.
- **Code Brain rule 4 (no silent swallow).** The child's one `except Exception` keeps its inline
  justification. It reports the type as a `failure` record and exits `UNAVAILABLE`, and the
  parent raises that as a refusal. No new handler was added.
- **Code Brain rule 3 (structured JSON logs to stdout).** Unchanged: the child's JSON record on
  stdout. No file logging added.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files).** None was written. The only
  password-like strings are test placeholders and a per-run random value. The scanner shows 0
  BLOCK.
- **Infra Brain 1032 (a credential seen is exposed).** No tool showed a credential value. I
  fetched no BWS secret and called no infraops tool.
- **Infra Brain 3035 `bws.secret-holds-value-only` and `build-agent-secrets.md` rule 7.** This
  change is what lets each secret hold only a password. The address is plain configuration, and
  nothing in the orchestrator accepts a connection string for a role.
