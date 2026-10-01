# Local development

WS-3.1 supports PostgreSQL 16 only. The local database is disposable; no tracked
environment file or credential is required.

```bash
docker compose up -d orchestrator-postgres
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn orchestrator.main:app --reload --port 8000
uv run orchestrator --help
```

The web process never applies migrations. Run Alembic explicitly before starting it.
Use `/health/live` for process liveness and `/health/ready` for database and migration
readiness.

The checked-in Compose service intentionally clears `ORCHESTRATOR_REGISTRY_BUNDLE`; it is
health-only and all authenticated API/review routes remain fail-closed. To exercise those
routes, supply the complete runtime configuration documented in `authentication.md`. No
fixture bearer token or CSRF secret is baked into Compose.

To build the fixture-registry image locally:

```bash
docker build \
  --build-context registry=tests/fixtures/security-standards \
  --build-arg SECURITY_STANDARDS_REVISION=0123456789abcdef0123456789abcdef01234567 \
  --build-arg REGISTRY_ARTIFACT_SHA256=00e17cb8e9841ccc8d892f0ca5b7ee240a23721c62d2d0f2236b5777ddab7d69 \
  -t orchestrator:ws31 .
docker run --rm --entrypoint orchestrator orchestrator:ws31 --help
```

The fixture identity bundle is test-only and contains no credentials. Both its recorded
source revision and deterministic content digest are pinned; changing any accepted artifact
byte requires an explicit digest update.

## Moved from CLAUDE.md

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #3

- On this machine, repo-local agent instructions live in `CLAUDE.md`. Treat
  `AGENTS.md` references from generic agent tooling as equivalent to checking
  `CLAUDE.md` unless a repo explicitly provides both files.

### #6

- The default `make check` gate must resolve Python tools from the repo-local
  `.venv/bin` before global PATH. A global `pytest` can collect with the wrong
  interpreter and fail imports even when the uv-scoped suite is green.

### #7

- Local dogfooding must use a runtime database separate from `orchestrator_test`.
  The test fixtures intentionally drop and recreate the test database, so storing
  live orchestrator lifecycle state there will erase approved intake, decomposition,
  claims, and evidence during `make check`.

### #33

- **Never run two pytest suites against the test database concurrently.** The fixtures drop and
  recreate `orchestrator_test`, so a background run and a foreground run corrupt each other and
  produce a spray of unrelated failures (27 failed / 13 errors, on a tree that passes 1210/1210
  when run alone). Before believing a suite-wide regression, re-run it *alone*.

### #43

- **`make check` runs `ruff format --check .` over the WHOLE repo — but per-task `ruff check` and
  the diff-scoped Stop hook only see CHANGED files + lint rules, so whole-repo *format*-debt is
  invisible until a full `make check`.** A file can be committed to `main` ruff-check-clean but not
  ruff-format-clean and nothing catches it until someone runs the full gate — `test_pr_bindings.py`
  landed format-dirty via WS-P2.16 (`2b18c98`) and only surfaced during WS-P2.2's final `make check`
  (it fails *without* your change too — a differential, not your regression). Two consequences: (1)
  before declaring `make check` green, expect it may red on **pre-existing** format-debt in files you
  never touched — run `ruff format --check .` and diff against `main` before blaming your diff; (2)
  have implementers run `ruff format` (or `make fix`), not just `ruff check`, before committing, or
  the debt accretes. (Verified 2026-07-24, WS-P2.2.)

### #75

- **`code-standards sync` is SAFE in this repo as of 2026-08-01 (WS-P2.25) — the prohibition below
  is lifted, and what made it necessary is worth keeping.** Ownership is now block-level
  (code-standards ADR-0008): each vendored file wraps the canonical content in
  `code-standards:managed:start`/`:end` markers, sync replaces the block and preserves everything
  around it, and **a file with no markers is locally owned, is never written, and is REPORTED on
  every sync**. This repo's `quality.yml` and `.github/dependabot.yml` carry no markers and each says
  so in a header comment; the `Makefile` now DOES carry a managed block (see below). Proven here,
  not assumed: a full `sync` on 2026-08-01 left
  `quality.yml` byte-identical (sha `6403063119dce3fd` before and after) with the brief-compatibility
  job present, and printed a `LOCAL:` line for all three. **Before trusting any sync, run
  `code-standards sync --dry-run` — it reports, per file, exactly what sync would do, from the same
  classifier sync uses.**

  Two consequences specific to this repo. (1) **RESOLVED 2026-08-01 (PR #113): the `Makefile` was
  `local` only because it was the pre-WS-P2.24 template — skip-semantics, no refusal on a missing
  tool — not because anyone chose to own it. It has been adopted; the gate now REFUSES.** Adoption
  was a measured no-op (rc=0, 1883 collected, 1882 passed, zero `skipping` lines, before and after);
  what changed is the failure mode. **Proving that required a control with BOTH conditions, and
  neither alone discriminates here:** `ruff`, `pyright` and `pytest` are all on this machine's
  **global** PATH (`~/.local/bin`, homebrew), so removing `.venv` leaves `command -v` succeeding;
  and the Makefile re-prepends `$(CURDIR)/.venv/bin`, so a scrubbed PATH alone is undone. A tree with
  **no `.venv`** under **`env PATH=/usr/bin:/bin`** gives the real differential — old: rc=0 with four
  `skipping` lines; new: rc=2, `make check: ruff not found`. The global-PATH half is specific to this
  machine and is the easier one to miss.
  (2) `.shellcheckrc` was absent and sync created it, so `make check` now runs shellcheck here.
  Verified clean under the Makefile's actual invocation (`find . … -exec shellcheck {} +`, all files
  in one call, relative paths from the repo root) with a positive control proving the sweep fires.
  Note a probe using absolute paths from another cwd reports 5 spurious SC1091s — measure under
  conditions of use.

  **The rest of this bullet is the pre-WS-P2.25 record. It explains why the mechanism exists.**

### #84

- **83% of this repo's test runtime is per-test schema rebuild, and no test runs in parallel.**
  Measured 2026-08-01: `migrated_engine` (`tests/services/conftest.py`, re-exported by
  `tests/persistence`, `tests/api` and `tests/web`) is **function-scoped** and does
  `DROP SCHEMA public CASCADE` → `CREATE SCHEMA` → **a full `alembic upgrade head` over 21
  migrations** for every test that requests it. One rebuild is **0.554s**; **305 test functions**
  take it, which is **169s of a 203s local suite** (~620s of ~750s in CI). Separately,
  `pytest-xdist` is a declared dev dependency that is **never invoked** — there is no `addopts`, so
  nothing is parallel locally or in CI. The fix is a session-scoped schema plus a fast per-test
  reset, and the reset must be **`TRUNCATE ... RESTART IDENTITY CASCADE`, not transaction
  rollback**: services commit internally by design, and a persistence assertion must re-read through
  a DIFFERENT session, neither of which survives being wrapped in an outer transaction. Only after
  that is xdist safe, keyed per worker (`orchestrator_test_gw0…`), because the fixtures drop and
  recreate the database and two suites must never share it.

### #98

- **Build sessions run in their own git WORKTREE, with their own venv and their own test database.
  HQ keeps the main tree — and that split is forced, not preferred.** A session's shell cwd resets to
  its launch directory between tool calls, and the diff-scoped Stop hook runs from that cwd, so a
  session **cannot relocate itself**; only the person launching it can. Until 2026-08-02 every
  handoff said *"Repo: `~/Projects/orchestrator`"*, putting HQ and the build session in one tree.
  That cost three false Stop-hook blocks in a single day — a build session's uncommitted
  work-in-progress attributed to HQ, each needing an audited `CODE_STANDARDS_BYPASS=1` — plus a
  CLAUDE.md batch held for hours and a running "the tree is busy" sequencing tax.
  **Recipe, proven 2026-08-02** (created, ran 38 tests, torn down; shared `orchestrator_test`
  untouched, and the worktree invisible to the main tree's `git status`):
  `git worktree add .worktrees/<ws> -b <branch> main` · `uv sync --frozen` in it ·
  `createdb -h 127.0.0.1 -U postgres orchestrator_test_<ws>` · export `TEST_DATABASE_URL` and
  `ORCHESTRATOR_DATABASE_URL` at that database and `SECURITY_STANDARDS_DIR` at
  `$PWD/tests/fixtures/security-standards` · `uv run alembic upgrade head`. Teardown is
  `git worktree remove … --force` + `dropdb`.
  **`.worktrees/` is ALREADY in `.gitignore` and already in the Makefile's `PRUNE_DIRS`** — the
  convention was anticipated and simply never used. **The per-worktree DATABASE is the half that is
  easy to miss and not optional:** `tests/conftest.py` drops and recreates whatever
  `TEST_DATABASE_URL` names, so two sessions sharing one database corrupt each other *regardless of
  which tree they are in* — a worktree alone fixes the Stop hook and leaves the real hazard intact.
  `conftest` reads that variable from the environment, so no code change is needed. (An
  `orchestrator_test_task6` database already existed, so somebody improvised this once without it
  becoming convention.)
  **TEARDOWN HAS A DEFINED POINT IN TIME, and it is the END OF THE SESSION, after the report is
  written** (Devon, 2026-08-14, after a morning in which three merged worktrees, five test databases
  and one stray file had accumulated). It is three steps and all three matter:
  **(1) Check the MAIN tree, not only your worktree.** `git -C <main tree> status --porcelain` must
  show nothing you created. The cwd-reset trap puts writes there while the session works correctly
  in its worktree, and the diff-scoped Stop hook lints untracked files at the *session's* cwd — so a
  fragment left behind blocks **whoever stops next**, not its author. On 2026-08-14 one such file
  blocked two sessions on work neither had written.
  **(2) Remove the worktree and drop the test database** — `git worktree remove … --force` +
  `dropdb`. **(3) DELETE THE BRANCH ONCE YOUR PULL REQUEST IS MERGED — locally as well as on the
  remote.** **CORRECTED 2026-08-24; this step used to read "Leave the branch and the pull request;
  HQ merges, so the branch must survive."** That was written when HQ did the merging. Sessions now
  merge their own pull requests, so the session that merges is the one place that knows the branch
  is finished, and leaving it stranded the branch forever. The cost was measured: **79
  landed-but-undeleted local branches across the six SDS targets** on 2026-08-24, 39 of them in
  `orchestrator` — every one correct behaviour under the old instruction, which is what made it
  invisible. `delete_branch_on_merge` is now `true` on all six repositories, so the REMOTE branch
  goes on merge; **the local one is yours** (`git branch -D`, since `-d` uses ancestry and
  squash-merge defeats it). If your pull request is NOT merged when you finish, leave both and say
  so. **The objection to answer, because a session will raise it: "leave it up in case CI sends me
  back."** Recreating is fully scripted and takes about three minutes, and a genuine second attempt
  wants a fresh tree from current `main` anyway. Standing by is the exception and needs to be asked
  for, not assumed. Note the Agent tool's `isolation: "worktree"` covers subagents a session
  spawns and does nothing for a session opened in a terminal, which is the case that was hurting.

### #145

- **A build-session worktree gets a DIFFERENT Python than CI unless you pin it, and the digest in
  a handoff is stale the moment anything merges.** Two release-time traps, both hit on 2026-08-07.
  (1) `uv sync` in a fresh worktree picks the newest interpreter satisfying `requires-python =
  ">=3.12"` — it chose **3.14.3** while `quality.yml` pinned **3.12**, so the session's green
  `make check` was measured on an interpreter CI never uses.
  **THIS BULLET TWICE PRESCRIBED THE DEFECT IT WAS WRITTEN TO PREVENT, AND THAT IS WHY THE DIGIT
  IS GONE.** It said `--python 3.12` for a week after `quality.yml` moved to 3.14 on **2026-09-02**
  (`6fe1f95`), producing exactly the state described above with the versions swapped: a local gate
  that cannot predict CI. It was then corrected to `--python 3.14` on 2026-09-09, which is the same
  construction one number later. **Use `uv venv --clear` with NO flag.** Since 2026-09-09 the
  repository root holds **`.python-version`**, `uv venv` and `uv sync` honour it natively, and
  `actions/setup-python` reads it through `python-version-file:` — so the interpreter is written
  once and everything else derives. Measured in a build worktree that day: bare `uv venv --clear`
  over an EXISTING 3.14 venv re-resolved 3.14.3 from the file alone, with no flag given.
  The PRINCIPLE is what to carry, not the digit: **name nothing, read `.python-version`** — a
  number in prose ages, and this one aged in seven days.
  `tests/architecture/test_interpreter_agreement.py` holds the sites that cannot read the file to
  it — both Dockerfile base tags, pyright's `pythonVersion` and, since 2026-09-09,
  `requires-python`, **all three to EQUALITY** — and was proven to red on each. The floor was held
  only to not EXCLUDING the pin while the two deliberately differed; they no longer do.
  **AND THE FLOOR IS A LEVER ON THE SOURCE TREE, WHICH IS THE PART NOBODY EXPECTS.** `[tool.ruff]`
  sets no `target-version`, so ruff derives BOTH its lint and its format target from
  `requires-python`. Raising it `>=3.12` → `>=3.14` rewrote **26 sites across 23 files** in one
  commit: 11 × UP037 (quotes stripped from forward-reference annotations — `NameError` on 3.12)
  and 15 files reformatted under **PEP 758**, where `except (A, B):` becomes `except A, B:` — a
  hard **SyntaxError** before 3.14, not a style difference. So moving `.python-version` rewrites
  the tree into syntax the previous interpreter cannot parse; do it only when nothing is left on
  the old one, and expect the mechanical diff in the same commit. Read `except A, B:` carefully:
  in Python 2 that spelling meant *catch A, bind it to B*, so it is the one construct in this tree
  a long-time reader can silently misread as the opposite of what it says.
  **AND THE INTERPRETER CENSUS MISSED THE MOST CONSEQUENTIAL LANE TWICE — `release-image.yml`,
  the production image build.** Neither #251's census nor the follow-up brief listed it. It runs
  **five** `python3` steps against files in this tree — `scripts/shape_registry_context.py`,
  `scripts/compute_image_tags.py` and three inline snippets — and it carried **no
  `actions/setup-python` at all**, so they ran on the runner's system interpreter: `ubuntu-latest`
  is `ubuntu-24.04`, which ships **Python 3.12.3** (measured from `actions/runner-images`, not
  assumed). It survived the floor raise only by accident — none of those files was in either
  cascade — and would not have stayed survivable, because the next `except`-tuple edited into
  either script gets reformatted into 3.14-only syntax automatically. Fixed in the same commit by
  the four-line step every other workflow already carries.
  **THE GENERAL DEFECT WAS THAT #251's GUARD IS KEYED ON PRESENCE, and that shape recurs here.**
  `test_every_setup_python_step_reads_the_version_file` asks whether every `setup-python` step
  reads the file — so a workflow with NO such step was never in its population, and the guard
  reported clean for months while the production image build ran on 3.12. Same shape as a landing
  census keyed on `basis == "rule"` reporting 0 across 671 rows: **a filter reports clean because
  the broken thing never entered the filter.** The inverse now ships beside it —
  `test_every_job_running_runner_python_sets_it_up_before_the_first_use` — asserting that a `run:`
  block invoking the runner's own interpreter has a setup step **earlier in the same job**, since a
  setup step below the first `python3` line is decoration. Three properties of it are worth knowing
  before editing: `uv run`/`uvx`/`.venv/bin/python` are exempt because they resolve through
  `.python-version` themselves (measured, with a control: the same `uv run` gives 3.14.3 and
  3.12.13 as the file changes, `requires-python` held constant); `docker` is exempt as another
  machine's interpreter; and **both are matched per LINE, never per step** — `release-image.yml`
  builds an image and then reads its digest with a runner `python3` in the SAME `run:` block, so a
  step-level docker exemption waves that invocation through. Its red control is the real file at
  `ada8d20`, not a fixture. **And the file scan cannot kill a mutation of those patterns** — once
  every workflow carries its step the scan short-circuits and never reaches the classifier, which
  is why `runner_python_lines` is tested directly; four mutations survived the scan and die there.
  Also note this repo is on **psycopg 3**: `TEST_DATABASE_URL` must be
  `postgresql+psycopg://`, not `+psycopg2://`, which fails with a bare `ModuleNotFoundError` that
  reads like a broken environment.
  (2) **`artifact_sha256` cannot be computed before the merge SHA and cannot be carried across a
  rebase.** `SOURCE_REVISION` is hashed into the bundle, so the digest is a function of the
  security-standards commit. The Inc-1 report quoted `6fee03e9…` for `abfaf41`; unrelated merges
  advanced `main`, the branch rebased to `85125a1`, and the real digest was `9cea9814…`. Always
  recompute from the **merged** revision:
  `scripts/shape_registry_context.py --source <checkout> --revision <sha> --output <dir>` then
  `build_registry_bundle.py`'s `artifact_digest`. Verify by assembling the bundle and reading back
  `source_revision` and the actor list before pinning. The in-build gate fails closed on a wrong
  digest, so the cost of getting it wrong is a failed build rather than a bad image — but it is a
  25-minute failed build.

### #176

- **The scheduled local jobs read the MAIN TREE's working copy, so MERGING CHANGES NOTHING ON THIS
  MACHINE.** `com.devon.deploy-watcher` (and its siblings) invoke
  `~/Projects/orchestrator/scripts/run-*.sh`, whose `REPO_ROOT` resolves off `BASH_SOURCE`, and the
  program is `$REPO_ROOT/.venv/bin/<name>`. The step that is easy to forget is `git pull` in the
  main tree; **no `uv sync` is needed for a new MODULE**, because the editable install is a bare
  `.pth` path append. The failure of forgetting is silent in the worst direction: the old launcher
  sets no new environment variable, the old CLI requires none, and the job keeps exiting 0 while
  the thing you shipped never runs.
  **CORRECTED 2026-08-21: a new CONSOLE SCRIPT is the opposite case, and the original wording is
  incomplete in the direction that bites.** A `[project.scripts]` entry does NOT appear from a
  `git pull` — `uv sync` is mandatory — and the launchers invoke it by absolute path
  (`$REPO_ROOT/.venv/bin/<name>`), so the pass hits a missing binary rather than an import error.
  Measured on `work-watcher` (ADR-0029): after pulling `d5afa9b` the entry was declared in
  `pyproject.toml` and absent from `.venv/bin` until a sync. Read the rule as **module → no sync,
  bin entry → sync**, and check `[project.scripts]` in the diff rather than inferring from whether
  files were added. The blast radius is now smaller than it was: the launchers' exit-code fold
  surfaces a missing binary as 127 instead of folding it to 0, and the installer refuses at install
  time — but only for lanes that have an installer.
  **AND THE HELPER'S SYNC RELOCATES THE INTERPRETER, WHICH ITS OWN COMMENT DOES NOT SAY.**
  `~/.claude/bin/activate-checkout.sh::_activate_sync_dependencies` runs `uv sync --frozen` when a
  pulled range touches `pyproject.toml` or `uv.lock`, and explains itself entirely in terms of a
  MISSING BINARY — the case it was built for. Since `.python-version` exists that same sync also
  REBUILDS THE VENV ON THE PINNED INTERPRETER whenever the two disagree, unattended, on whichever
  scheduled lane fires first, printing `dependencies synced` as though nothing larger had
  happened. Benign while the tree runs on either interpreter; not benign the moment a change is
  version-specific. Read it as: a pull that moves a manifest can move the interpreter under all
  eleven lanes, and the trigger is a cron rather than a person.
  **SHARPENED 2026-08-21: in a FRESH WORKTREE, `uv sync --frozen` is necessary and NOT SUFFICIENT.**
  Measured building item 150's fix: `work-carrier` and `work-watcher` were declared in
  `pyproject.toml` and absent from the new worktree's `.venv/bin` after a clean `uv sync --frozen`;
  `uv sync --reinstall-package orchestrator` installed both. So the rule has two halves — **a pull
  into an existing tree needs a sync; a fresh worktree may need a reinstall on top of one** — and the
  failure is the same either way: the launchers invoke by absolute path, so it dies at a missing
  binary rather than at an import error. Check `.venv/bin` against `[project.scripts]` directly
  rather than trusting that a sync did it.

### #208

- **`emit-intake-payload` cannot verify a package approval from a git WORKTREE, and it fails with a
  message that reads like a package fault.** It resolves the sibling `intent-packages` checkout by a
  fixed relative path from its own module file, so from `~/Projects/orchestrator/.worktrees/<ws>/` it
  lands at `.worktrees/intent-packages/src` — which does not exist — and refuses with
  `package_not_intakeable — approval verification failed`. Nothing in that message says "wrong
  directory". **Every build session works in a worktree by convention, so this is the DEFAULT
  environment for the carry and for `factory decompose`**, and it nearly cost item 150's live
  differential: the worktree's first pass returned a plausible and alarming answer about record 62's
  package, and only re-running from the main tree settled it. Backlogged P2 `3c6f2330fa37`; the fix
  is an environment override of the kind `SECURITY_STANDARDS_DIR` already provides one function over.
  Until then: **run anything that verifies a package approval from a MAIN tree**, and treat an
  approval-verification refusal seen from a worktree as unproven rather than as a finding about the
  package.

### #218

- **A script that installs a LaunchAgent must REFUSE to run from a linked worktree.** `REPO_ROOT`
  resolves from `BASH_SOURCE` and is written into the plist verbatim, so installing from a build
  worktree pins the scheduled job to a path that session tears down — after which it fails every
  morning with **nothing reporting it**, which is the silent-quiet twin of a permanently-red
  control. The discriminator is `git rev-parse --git-dir` versus `--git-common-dir`: they differ in
  a linked worktree and are equal in a main tree, measured both ways 2026-08-24.
  Every installer calls `sds_refuse_linked_worktree` from `scripts/sds-install.sh` before writing
  anything (#292, 2026-09-27); five of eleven had lacked it, two while carrying a comment claiming
  it. A future installer sources that file, and `tests/scripts/test_launchd_installers.py` fails
  for any installer that does not refuse from a linked worktree.

### #220

- **`delete_branch_on_merge` converts an unanswerable question into a trivial one, and is now `true`
  on all six SDS targets** (it was already true on four; `orchestrator` and `intent-packages` were
  set 2026-08-24). Once the remote branch is deleted on merge, `git fetch --prune` makes the local
  branch report `[origin/<name>: gone]` — a reliable local prune signal needing neither a pull-request
  lookup nor content guessing. The difference is measurable: `brain`, which already had the setting,
  showed `gone` for 10 of 10 local branches, while `orchestrator` showed it for only 14 of 39.
  **No git setting deletes a LOCAL branch**, which is why the teardown step above had to change
  rather than being automated away.

### #237

- **Under `launchd`, `uv` is NOT on PATH — it lives in `~/.local/bin`, and the plists name only the
  system directories.** A producer that shells out to `uv` must resolve it with `shutil.which` and
  fall back to that path explicitly, or **every scheduled pass reports unmeasurable while working
  perfectly from an operator's shell** — the exact shape the `env -i PATH=… HOME=…` probe rule
  exists to catch, and a reminder that the probe is the only thing that sees it. This generalises
  past `uv` to any tool installed by a user-level installer rather than a system package manager.

### #246

- **The seven scheduled launchers do NOT share an exit-code vocabulary: `2` and `3` mean OPPOSITE
  things in two groups, and anything wired to those codes must be keyed per launcher.** Measured
  2026-08-29 by reading all seven headers, after HQ nearly shipped a universal rule that would have
  inverted the signal for three of them.
  `run-landing-ledger.sh`, `run-deploy-watcher.sh` and `run-activation-sweep.sh`: **2 = something
  was FOUND**, 3 = it could not be read or measured ("3 outranks 2: an incomplete pass cannot claim
  it found everything there was to find").
  `run-estate-landing.sh`, `run-change-proposer.sh`, `run-work-carrier.sh` and
  `run-bump-proposer.sh`: **2 = could not use its INPUTS**, 3 = something was found.
  0 and 1 agree everywhere (nothing found; the tool itself failed). So a consumer that treats
  non-zero as failure collapses "the tool broke" with "the lane found something" — which is exactly
  the collision each header exists to prevent, and which the first dead-man switch shipped with.
  Read the header of the launcher you are wiring; a table like this one is for checking yourself
  against, never for keying on. Note also the `for rc in 1 3 2` fold several of them end with lets
  any code outside `{0,1,2,3}` — 127 for a missing binary — fall through to `exit 0`.

### #252

- **A Healthchecks check reading `status=new, n_pings=0` is NOT failing — it has never pinged, and
  the two are visibly different.** To establish whether a scheduled lane has run since a change
  landed, read `launchctl list` for its last exit status AND the mtime of its log in
  `~/Library/Logs/`; the check's status alone cannot distinguish "failing" from "has not run since
  the thing that would make it ping". Measured 2026-08-29: HQ told Devon two checks were "failed on
  standing findings and will page tomorrow". They were `new` — four of seven were — because their
  lanes last ran at 07:10 and 07:32 and the switch landed at 10:19. The underlying observation was
  right (both lanes did last exit 2, their finding code) and the state description was wrong.

### #261

- **`pkill -f <parent>` leaves the pytest CHILD running, and the orphan keeps `DROP SCHEMA`-ing the
  test database.** Found 2026-08-31 by the Increment 2 session: its mutation harness was reaped,
  its process check was keyed on the harness's own name, so it reported zero survivors while an
  orphaned `pytest` went on recreating the schema underneath the next run — producing a 43-failed
  result on a tree that passes 136/136 alone. That is the estate's standing "never run two pytest
  suites against one test database" rule reached by a route nobody was watching, and it is the
  correct-about-the-wrong-noun family again: the check named the parent and the hazard was the
  child. Kill the process GROUP, and verify by looking for `pytest` itself rather than for the
  thing that launched it.

### #264

- **Concurrent build sessions dispatched from one HQ SHARE A SCRATCHPAD, so any fixed-name scratch
  file collides.** In-process subagents inherit the parent session's scratchpad directory, so two
  sessions working in different worktrees still write to one path. Observed 2026-08-31: Increment
  2b's `mutate.py` overwrote Increment 3's at the same path while both were live. No harm that time
  — Increment 3's run had already completed under its own harness and its tree was clean — but the
  failure mode is the same class as two sessions sharing a test database, and worse in consequence:
  **a harness silently swapped mid-run mutates one tree while reporting about another.** The
  discipline is the same as for the database — namespace per session, or write scratch into your own
  worktree — and it belongs in the dispatch brief, because a session cannot see that it has a peer.

### #270

- **A LANE'S DECLARED FINDING CODE PINGS ITS DEAD-MAN CHECK **SUCCESS**, NOT `/fail` — so "a
  standing finding drives a permanently-red control" is false of every lane in this estate.**
  `sds_deadman_finish` (`scripts/sds-deadman.sh:141-153`) has three branches: `rc == 0` pings
  success; `rc == SDS_DEADMAN_FINDING_CODE` logs *"exit N is this lane's finding code — the pass
  ran and reported"* and pings **success**; everything else pings `/fail`. That is the whole point
  of `--finding N`, and its own header says so — the check answers *is this lane alive*, never
  *did it find something*. Measured 2026-09-01, not read: `sds-inert-landing` reads **up** after a
  pass that exited 3 with four subjects held.
  **Both the programme plan's §7 decision 10 and the 2026-09-01 HQ handoff asserted the opposite**,
  costing a decision its actual cost. The real cost of a permanent resident is narrower and a
  different kind of thing: the exit code is permanently the finding code, so nothing keyed on it
  can distinguish a NEW finding from the standing one without reading the log. When weighing what
  a standing finding costs, read the launcher's `--finding` declaration before assuming a page.

### #271

- **`launchctl print`'s `runs` and `last exit code` are NOT evidence about whether a scheduled lane
  has ever run.** Measured 2026-09-01: `landing-ledger`, `estate-landing`, `deploy-watcher` and
  `bump-proposer` all report `runs = 0 / last exit code = (never exited)` while carrying logs of
  63K–743K. The counter is per-load, and `launchctl list`'s status column reads `0` for a job that
  has never fired, which is the same value as a clean exit. **The record is the log file, the
  Healthchecks ping count, and the lane's own output.** A missing log IS honest evidence of no run
  — pair it with the plist's mtime and the next scheduled minute before concluding anything.

### #272

- **HQ KEEPING THE MAIN TREE IS RIGHT FOR READING AND WRONG FOR EDITING: a checked-out branch there
  puts branch code under all eight schedulers.** The launchers resolve `REPO_ROOT` from
  `BASH_SOURCE` and run the working copy, so a three-file fix made in the main tree is what every
  lane executes until the tree goes back. Observed 2026-09-01 — the 12:35 inert-landing pass opened
  `[activation] ~/Projects/orchestrator is on 'adr0038-remove-cascade-guards', not main — running
  what is there`. The activation guard did its job and the exposure was minutes, but nothing warns
  BEFORE a pass fires and a session cannot see which lane is next. **Use a worktree for any edit,
  HQ included**; the existing rule already gives build sessions one for a different reason (the
  Stop hook), and this is a second, independent reason that applies to the tree HQ keeps.

### #273

- **A BARE `uv venv --clear` IN THE MAIN TREE MOVES ALL EIGHT SCHEDULERS TO A NEW INTERPRETER, AND
  `--clear` DOES NOT PRESERVE THE ONE THAT IS THERE.** The 2026-09-01 near-miss is recorded as
  *"had the flag been `--python 3.14`, all eight lanes would have moved interpreter silently"* — the
  trigger is the opposite: **omitting the flag**. Measured 2026-09-02 in a clean directory carrying
  this repository's `pyproject.toml`: bare `uv venv` → **3.14.3**; `uv venv --python 3.12` →
  3.12.13; **bare `uv venv --clear` over an existing 3.12 venv → 3.14.3**. Yesterday's invocation
  was survived only because it happened to name 3.12. At the time there was no `.python-version`
  file and no `[tool.uv]` table, `requires-python` was `>=3.12`, and `uv python list` showed 3.14.3
  installed — so uv picked the newest satisfying the floor, exactly as the worktree bullet above
  predicts. What that bullet does not say is that the same command in the **main tree** costs
  production schedulers rather than one session's test run: they resolve `REPO_ROOT` from
  `BASH_SOURCE` and run `$REPO_ROOT/.venv/bin/<name>`, so the interpreter moves under all of them
  at once, silently, with no flag involved.
  **THE FLAG IS NO LONGER THE ANSWER, AND THE HAZARD IS NOW WIDER THAN THE COMMAND THIS BULLET IS
  NAMED AFTER.** Since 2026-09-09 the root holds `.python-version`, so **`uv venv --clear` with no
  flag rebuilds the main tree on the pinned interpreter** — read `.venv/bin/python --version`
  afterwards, which is the half that never stops being worth doing. But `.python-version` is a
  request to uv, not a note: measured 2026-09-09, **`uv sync --frozen` over a venv that disagrees
  with it prints `Removed virtual environment at: .venv` and recreates it**, and either
  `.python-version` OR a raised `requires-python` is sufficient on its own (both were probed
  separately). So the trigger has moved from *omitting a flag on one command* to *any `uv sync`* —
  and this file's own rule prescribes exactly that `uv sync` whenever a `[project.scripts]` entry
  is added. There is no flag that avoids it and no way to opt a working copy out. **The rule that
  survives is the one that was always the real one: do not run uv against the main tree while a
  scheduled pass may fire, and check `.venv/bin/python --version` after anything that could have
  moved it.** The launchers themselves never go through uv — they invoke `.venv/bin/<name>` by
  absolute path — so nothing moves them except a rebuild of that venv.
  **THE TRAILING CLAUSE USED TO READ "the main tree's 3.12 is deliberate and is the floor CI
  pins", AND THE SECOND HALF IS GONE.** CI moved to 3.14 on 2026-09-02 (`6fe1f95`), and since
  2026-09-09 `.python-version` is what CI, a build worktree and this tree are all meant to
  resolve — one number, not a deliberate split. `requires-python` is still `>=3.12` and is
  deliberately BEHIND it: raising the floor is the obviously right end state and was measured to
  be a 26-file change rather than a line, because ruff derives both its lint and its format
  target from `requires-python` and 3.14 turns on PEP 758 (`except A, B:`, a hard SyntaxError on
  3.12) plus 11 forward-reference unquotings. Three of the files that reformats back scheduled
  lanes. So the floor moves in its own increment AFTER this tree's venv does, at which point both
  cascades are free; `tests/architecture/test_interpreter_agreement.py` holds it to not
  EXCLUDING the pin in the meantime and records the whole measurement.
  **COUNT THE LANES, DO NOT RECALL THEM.** This bullet's headline says EIGHT and there are
  **eleven** (`ls scripts/com.devon.*.plist`, all loaded — measured 2026-09-09: activation-sweep,
  bump-proposer, change-proposer, deploy-watcher, estate-landing, inert-landing, landing-ledger,
  pin-watcher, revision-watcher, tool-installer, work-carrier). Eight was last true on 2026-09-03;
  `pin-watcher`, `tool-installer` and `revision-watcher` arrived over the following five days. The
  headline is left as written because it is the record of what was measured then — and because the
  way it went stale is the more useful half: **the 2026-09-09 correction rewrote the sentence
  below it and copied the digit forward without re-measuring**, two thousand lines from its own new
  line saying a number in prose ages. A count in a heading is a number in prose.

### #300

- **AN XCODE UPDATE RESETS THE LICENSE, AND AN UNACCEPTED LICENSE STOPS EVERY SCHEDULED LANE AT
  ONCE — each failing with a message that names something other than Xcode.** Measured 2026-09-15.
  Xcode 27.0 was installed at 2026-09-14 22:52 EDT. `/usr/bin/python3` and `/usr/bin/git` are Xcode
  shims: with the license unaccepted, both print *"You have not agreed to the Xcode license
  agreements"* instead of running, and `launchctl list` recorded exit **69** for the lanes that
  surfaced it directly. launchd's PATH resolves bare `python3` to that shim, and every launcher parses
  BWS output as `bws secret get … | python3 -c …`, so all eleven SDS lanes failed from the first pass
  after the install. **Three different messages, one cause:** the launchers said a secret *could not
  be read from BWS* (BWS was fine; `python3` died and `bws` got a broken pipe), the dead-man switch
  said *the Healthchecks API key could not be read — alerting disabled this run*, and the activation
  helper said *`~/Projects/orchestrator` is not a git checkout*. When many unrelated lanes fail at once
  with plausible, different messages, look for what they share before believing any of them; here
  `env -i PATH=/usr/bin:/bin HOME="$HOME" python3 -c pass` settled it in one command.
  **The dead-man switch worked, and only its Healthchecks side could have.** Its lane side runs the
  same broken pipeline, so it sent nothing — no `/start`, no `/fail`. Healthchecks' silence timer
  needs nothing from the machine, and moved the checks to `down`, which emails Devon. Do not "fix"
  the lane side by giving it a different interpreter: the silence path is the one that survives a
  machine that cannot run anything. Two `infraops-mcp-server` lanes (`drift-audit.sh`,
  `change-window.sh`) did send `/start` and `/fail`, so their ping path does not depend on the shim.
  **What still ran without the license:** `/usr/bin/jq`,
  `/opt/homebrew/opt/python@3.12/libexec/bin/python3`, and this repository's `.venv/bin/python`.
  Local git did not, so the clean-tree rule could not be satisfied until the license was accepted.
  **The fix is `sudo xcodebuild -license accept`, and it is Devon's** — `sudo` is on the deny list.
  **CORRECTED 2026-09-27 (#292): the launchers no longer parse secrets with bare `python3`.** They
  source `scripts/sds-bws.sh`, which parses `bws` output with the repository's own
  `.venv/bin/python` by absolute path. This bullet used to say not to re-point the launchers, on
  the ground that it would hide the next Xcode lapse behind a different failure. It does not hide
  it: `/usr/bin/git` is the same kind of shim, so a lane that runs git still stops, and the
  Healthchecks silence timer still pages. What changed is that the secret read no longer breaks
  first and blames BWS, and twelve copies of the parse became one.
  Recovery was proven by running each lane once with `launchctl kickstart` and reading its
  Healthchecks check back to `up`, not by the license probe alone.
