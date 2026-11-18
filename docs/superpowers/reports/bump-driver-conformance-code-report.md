Outcome: done

# Build report: bump-driver-conformance-code (W-222)

- **PR:** AlobarQuest/orchestrator#745, ready for review, not merged.
- **Branch:** `hq/bump-driver-conformance-code`, based on `origin/main` `1c1b6b25`.
- **Content commits:**
  - `8e392c9a`: the change.
  - `77cfa361`: the password-reach test, strengthened after a mutant survived.
  - `9b08f3c3`: the review fixes.
- **Head:** this report's commit, which follows `9b08f3c3`.
- **Source:** ADR-0059 Amendment 13 (D-62 C) at `1c1b6b25`, decision items 1 to 7 and the step 3g-3-inputs verify items. The producer's layout is security-standards #101 at head `f3defd0b`, read from its adapter's source as well as its report.

## What I built

The seven pieces of decision item 6, "orchestrator":

1. **`CLONED` and the mint.** `AlobarQuest/code-standards` joins `CLONED`, so `bump-driver repositories` prints it and the lane's one read mint names it.
2. **The launcher's container block** (`scripts/run-bump-driver.sh`):
   - It clones code-standards shallow at `main` into `$HOME/Developer/code-standards` through `sds-git.sh`.
   - It writes a `code-standards` shim at that clone's `.venv/bin/code-standards`, which runs the image's interpreter with `-P` on the clone's `src`.
   - The new-`HOME` guard covers `Projects`, `Developer`, `.local`, `infra-drift`, `Library`, and `.claude`.
   - It reads the three chain settings from the compose, and a missing one is fatal. It reads the read password, which is also fatal if it can't be read.
   - It runs one chain walk per pass, dry or acting. The walk uses `orchestrator.factory_chain_read` with the image's pinned security-standards, as the work carrier's does.
   - It writes `{verified, summary, mac_conformance}` to a file under the pass's `HOME`, and passes that file as `bump-driver --conformance-facts`.
3. **The driver's hold and alert keys** (`src/bump_driver/conformance.py`, `cli.py`, `alerts.py`):
   - The driver judges the facts at each bump, after the mirror is read and before the generator runs.
   - With usable facts, an acting pass writes them under `HOME`: the report, the plist markers, and a minimal `settings.json`.
4. **`tomlkit`** is an orchestrator runtime dependency.
5. **The compose** joins the external `coolify` network and carries the work carrier's three `FACTORY_EVENTS_DB_*` values.
6. **The registries:**
   - The read password's `lanes` row gains `bump-driver` in `.bws-secrets.toml`.
   - `.cred-consumers.toml` gains a `runtime-fetch` reader for `run-bump-driver.sh`.
   - The guards updated are the launcher, lock, repositories, and import-isolation tests, plus `layers.toml`.
7. **`deploy.md` and the runbook** (`landing-lanes.md`, "The bump driver"): the cut-over lines this adds. No other lane's lines change.

**Which process walks the chain, and why.** The launcher runs the walk as its own child process, and the driver reads the result from a file. That follows the work carrier's precedent (`--portfolio-scan`), for three reasons:

- The password reaches one short-lived process that holds no other secret it needs. It never reaches the driver, which spawns `factory` and git.
- The walker and schema are the image's pinned copy, the same one the carrier verifies with.
- The selection is a pure function beside `portfolio_scan` (`mac_conformance`), so it's tested without a database.

The driver does the judging and the file writing in Python, and only when `--conformance-facts` is named. On the Mac the launcher never names it, so the Mac branch can't write over a real `~/.claude/settings.json`.

## The handoff's named answers

### The mint's shape for code-standards

The mint is unchanged: `--permission metadata=read --permission pull_requests=read --permission contents=read`, one token over every repository `bump-driver repositories` prints. code-standards is one more in that list.

On the four cloned repositories, the pass reads only contents, through the shallow clone. `GitHub.pull_state` is called only from `_merge`, for a granted member's bump unit. Nothing beyond D-62's `contents: read` is needed. The read-only App's installation carries every read permission the App holds, and the mint bounds what a token can do; that is Amendment 13's own reading.

### The `tomlkit` choice

`tomlkit` is an orchestrator dependency (`tomlkit>=0.15.1`, locked at `0.15.2`) rather than a lane image addition. The change is smaller: one `pyproject.toml` line, and the image's venv carries it with no Dockerfile change. `uv lock --check` is green, and the lockfile header stays at `revision = 5`.

**Proof that the shim exits 0 or 1, never 2.** I ran the real code-standards `main` (`37de902`) through the shim on a shallow clone of change-manager at `main` (`82f1456`). I ran it with `env -i PATH=/usr/bin:/bin`, so no `uv` was on `PATH`, and with this worktree's venv interpreter standing in for the image's. It exited 0.

**Proof of no PyPI fetch.** The shim execs the interpreter directly, so `_code_cmd` takes its first branch and never reaches `uv run`.

The launcher test runs the generated shim and asserts that it imports `tomlkit` with `-P`, even with a shadowing `code_standards` directory in the working directory.

### The alert keys and the silent case

- **`bump-conformance-facts`** is one key for the lane. It fires while any bump is held for unusable facts. It resolves only on a pass whose facts are usable. A pass that holds no bump while the facts are still unusable keeps it firing, through `sync_alerts(kept=…)`, so it can't resolve falsely.
- **`bump-conformance-wiring:<repo>`** fires for a declared `launchagent:` or `hook:` entry that the event doesn't list. It's held while that repository is unread.
- **The silent case.** Every container pass walks the chain and prints the walk's summary and `conformance facts: usable …` or `unusable: <reason>` in its report. The alert fires only while a bump is held. Stale facts that no bump needs raise nothing.
- `held` is a finding: exit 2, delivered as its alert, `--told 2`.

**W-219's follow-up 3 (a declared wiring entry the event doesn't list).** I made it a hold with its own key. Without the hold, the container reads the entry as `checks.not-wired`, and the claim isn't green. The orchestrator then approves the decomposition and dispatch refuses it, so only a person superseding it clears it. That's the outcome the hold exists to prevent. The alert names the fix, which is adding the entry to security-standards' `WIRING`.

An `absent` status isn't held. It's the Mac's own fact, and the matrix reads it as `violation` on both hosts.

### The Mac's code-standards working copy against the clone's `main`

On 2026-11-18, `~/Developer/code-standards` was at `37de902`. That is `origin/main` after a fetch, and `git status --short` was empty. There was no difference.

The cut-over records this again (verify item).

## Tests and checks

- **`make check`** on the final content commit `9b08f3c3`: ruff, format, pyright, and shellcheck are clean. pytest reports **15,751 passed and 1 skipped** in 304 seconds. The skip isn't in a suite this change touches: `pytest -rs tests/bump_driver tests/cli tests/scripts` lists none.
  - An earlier full run, before the guard fixes, had three architecture failures. They were a `_ITEM_READERS` vocabulary marker, a layer entry for a then-untracked file, and `yaml` in the bump driver's import row. All three are fixed.
- **Security scan** from the worktree root: 0 BLOCK, 2 WARN, and 1 INFO. The two WARNs are a `.gitignore` glob (`*-migration/`) and an undeclared UUID `aaff45ca-…`, both in files this change doesn't touch.
- **The verify items' tests:**
  - **An unreadable record wins over an older usable event, and holds:** `test_an_unreadable_record_wins_over_the_previous_days_usable_event`, plus the `unreadable` case of the hold tests.
  - **Newest by `timestamp` whatever the `seq`:** `test_the_newest_by_timestamp_wins_whatever_its_seq`.
  - **Missing, verify failed, unreadable, instance not ok, and over the bound each hold and propose nothing:** `test_a_bump_that_reads_unusable_facts_is_held_and_proposes_nothing`, 5 cases each for an infra member and a wiring member.
  - **A member that reads nothing isn't held:** `test_a_member_that_reads_nothing_from_the_event_is_not_held`.
  - **The alert fires and resolves on the first usable pass:** `test_the_facts_alert_fires_while_held_and_resolves_on_the_first_usable_pass`, plus the keep-firing and silent-case tests.
  - **The bound is no looser than `INFRA_MAX_AGE_H`:** `test_the_drivers_bound_is_inside_the_matrixs`. The driver's bound is 35 against the matrix's 36, and the test checks project-standards' constant when the checkout is present.
  - **Matrix equivalence, fixture-based:** `test_the_written_files_give_the_cells_the_macs_inputs_give` runs project-standards' own readers (`check_infra`, `contract.parse_contract`, `check_required_checks`) at `4673d72`. It compares the Mac's inputs, free text included, with the written files, for change-manager, brain, security-standards, and project-standards, and the status and finding ids are equal. It ran locally. `test_where_the_mac_reads_unknown_the_container_holds` covers the `unknown` case.
  - **The password reaches only the chain walk:** `test_the_read_password_reaches_the_chain_walk_and_no_other_process`. It scans the environment of the derive, the mint, the four clones, and the driver, and checks the arguments and the output.
  - **The `HOME` guard refuses a `HOME` holding `Developer`:** `test_a_home_that_is_not_new_fails_before_any_read`, over all six paths.
  - **The Mac branch is unchanged:** `test_on_the_mac_the_identities_and_gh_are_as_before_and_nothing_is_minted` now also asserts no walk, no read password, no facts file, and no `Developer`. `test_launcher.py`'s Mac-line pins are unedited, and `test_on_the_mac_no_facts_hold_nothing` covers the driver.
- **Mutation testing.** I ran 16 mutants of the new guards:
  - The hold, the member test, and the wiring hold.
  - Selecting by `seq`, no fractional seconds, and a loose executor pattern.
  - A bound of 36, ignoring not-ok, and ignoring unreadable.
  - Dropping `kept`, ignoring `kept`, and allowing overwrite.
  - Exporting the password, a guard without `Developer`, ignoring the walk's exit code, and no `-P`.

  15 were killed at once. The survivor, an exported password, lived because no stub logged its environment. `77cfa361` adds an environment scan to each stub, and the mutant is killed.

## Files changed

- `src/bump_driver/conformance.py` (new), `src/bump_driver/cli.py`, `src/bump_driver/alerts.py`
- `src/orchestrator/factory_chain_read.py`
- `scripts/run-bump-driver.sh`
- `lanes/bump-driver/compose.yaml`
- `pyproject.toml`, `uv.lock`
- `.bws-secrets.toml`, `.cred-consumers.toml`, `layers.toml`
- `docs/operations/deploy.md`, `docs/operations/landing-lanes.md`
- `tests/bump_driver/test_conformance.py` (new), `tests/cli/test_factory_chain_read_conformance.py` (new)
- `tests/scripts/test_run_bump_driver.py`, `tests/bump_driver/test_launcher.py`, `test_lock.py`, `test_repositories.py`
- `tests/architecture/test_out_of_process_isolation.py`

## Self-review

I ran `/code-review` (medium) on `main...HEAD`. It raised six points:

1. **`uv.lock`'s `revision` went from 5 to 3.** This was real: my local uv 0.10.9 rewrote the header. I restored it to 5, and `uv lock --check` passes.
2. **Zero instances read as usable.** This is not a defect. project-standards reads `instances: {}` as `pass`, and the producer records the same case, so the two hosts agree.
3. **A proposal naming an unlisted instance gets an invented ok instance.** A forged event could only add proposals, which means more violations. Even so, I tightened the contract: a proposal must name a listed instance, or the event is out of contract. A test covers it.
4. **The facts file under `/tmp` leaks on an early exit.** Fixed: the file now lives under the pass's `HOME`, which `lane-run` discards on every exit path.
5. **Ragged header comments.** Reflowed. While doing that, I caught that splitting the `HOME`-guard message would have made its second half `sds_outcome_fail`'s exit code, so it stays one argument.
6. **The "Not swallowed" comment in `declared()` was inaccurate.** I reworded it to state the deliberate reason. An unreadable `PROJECT.md` makes every matrix cell `unknown` on both hosts, so holding for the event would change nothing.

No `noqa`, `type: ignore` or `eslint-disable` was added.

## Concerns

- **Devon's two acts gate every container pass, not only the acting ones.**
  - The read password is fatal, like the lane's other secrets, so until the bump-driver lane account's grant on `2f708d08-…` exists, every container pass fails at that read.
  - The mint names code-standards, so until the read-only App is installed there, every mint fails.

  The lane isn't scheduled, and `deploy.md` orders both acts before the step-8 proof. A hold-instead-of-fatal design for the password was possible, but I kept the lane's rule that every secret is fatal.
- **The equivalence test doesn't run in CI.** project-standards isn't available there, so `test_the_written_files_give_the_cells_the_macs_inputs_give` and its sibling skip. `test_the_written_files_are_the_shapes_the_matrix_reads` holds the exact file form in CI. A change to project-standards' readers would go unseen until someone runs the test locally or the cut-over's acting pass runs.
- **The lane's token row doesn't exist yet.** The handoff's "the lane's reach in `.cred-consumers.toml`" can't be a `reach_secrets` entry, because the bump-driver lane token's credential row is written at cut-over step 4 with its keeper UUID. I added the `runtime-fetch` consumer on the read password's row, and `deploy.md` step 4 now says the token's `reach_secrets` names `2f708d08-…`. I didn't invent a keeper UUID.
- **Forged events.** As Amendment 13 decision item 7 accepts, any append holder can write this event. The reader limits what a forged event can reach: plain-word wiring names only (no path), listed instances only, future-dated events skipped, and files created, never overwritten. It can't stop a forged event from releasing a hold.
- **Inherited from the producer (W-219's concerns).** A directory with no report appends nothing, so the container keeps the last event until it ages out. The Mac reads `unknown` at once in that case. Wiring changes after a report wait for the next report.
- **Standards not covered by the handoff's section:** none that this change touches beyond those listed.
- **Teardown.** The worktree and the test database `orchestrator_test_hq_w_222` are left in place for HQ, which owns the worktree and may resume this session. I created nothing in the main tree. Two scratch directories, `/tmp/w222-proof.*` and `/tmp/w222-*.log`, are outside every repository.

## Follow-ups

1. **At cut-over:** the paired-pass decision in Amendment 13's last verify item. A `--dry-run` doesn't run the generator, so it can't show the claim green.
2. **At cut-over step 4:** the lane token's credential row, with `2f708d08-…` in `reach_secrets`.
3. **Optional:** a CI path for the matrix-equivalence test, such as a pinned project-standards fixture like `tests/fixtures/security-standards`.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout in production).** I kept each surface's existing form, as the handoff asks:
  - The launcher adds `[chain] <summary> (exit N)` to its plain report, in the work carrier's form.
  - The driver adds one `conformance facts: …` line to its plain report table.
  - The chain walk's own record stays the module's structured JSON line, and the launcher passes only its summary fields to the report.
- **Code Brain 4 (never silently swallow an exception).**
  - A chain-walk failure, an unreadable facts file, and an unverified walk each become a hold with the keyed alert, never a pass.
  - Writing the facts under `HOME` fails the pass with exit 1 on any `OSError`.
  - The deliberate catches carry inline justifications: the chain walk's stderr discard (as the carrier's), and `declared()`'s unreadable manifest.
- **Code Brain 5 (never log secrets).**
  - The read password stays in an unexported variable and reaches only the chain walk, as an assignment prefix. It's never an argument, never exported, and never printed.
  - The walk's stderr is discarded. The tests scan every other process's environment and arguments, and the output, for it.
  - The facts file holds ids and statuses only.
- **Infra Brain 591, 592 and 593 (BWS token handling).** No token is in any file or in history, and no inline `BWS_ACCESS_TOKEN` was added. Secrets are referenced by UUID only (`2f708d08-…`), and the compose maps the lane token by variable. The scan reports 0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`).** It doesn't apply: I read no credential and surfaced none. There was no Keychain or BWS read, and the tests use placeholder values, so nothing needs rotating.
- **Infra Brain 212 (HTTPS).** It doesn't apply to the new connection, which is Postgres inside the `coolify` Docker network, as the work carrier's is (no HTTP endpoint). The code-standards clone uses the existing `https://github.com` URLs, and every other service is unchanged and HTTPS.
- **Workspace invariant (a compose service that connects to a separate Coolify DB resource joins `coolify`).** The bump-driver compose joins the external `coolify` network, which the compose test asserts.
