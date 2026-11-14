Outcome: done

# FE-5 P-PIN build report (worker W-159)

- **PR:** https://github.com/AlobarQuest/orchestrator/pull/705 (open, not merged), branch
  `hq/fe-5-p-pin`, base `origin/main` `03075209`.
- **Code commit:** `9776c8e498a4475758f47533624cc517732b707d`. This report is a second commit on the
  same branch, so the PR head is the commit that adds this file. Read the head from the PR.

## What I built

1. **The pin.** `security-standards.pin.toml` moves from `revision = "31aa7d70…"` to
   `revision = "60ec80f7ec5dd68d1e6fa9d13c90e9bd72b5d286"`, which is
   `git -C ~/Projects/security-standards rev-parse 60ec80f`. `artifact_sha256` moves from
   `b11a9e9c…` to `d5cb4a58dc912d7f8c3196dab0013f8516f05e1af439ce25811a53be572803a8`. The header
   comment is unchanged.
2. **The psycopg floor.** `pyproject.toml` line 15 moves from `psycopg[binary]>=3.3.4` to
   `>=3.3.8`. That's the only psycopg site in the file: the orchestrator declares psycopg only in
   `[project].dependencies`, not in any dependency group or optional dependency. `uv.lock` was
   relocked with `uv lock --upgrade-package psycopg --upgrade-package psycopg-binary`.
3. **Item 3** is a finding only. See "Does a main-tree fast-forward sync its `.venv`" later in this
   report.

## Recompute of the digest

The recipe matches the pin header and `docs/operations/deploy.md` #52. It shapes the context with
`scripts/shape_registry_context.py`, which runs `git archive <revision> <pathspec>` against the
security-standards object store (never the working tree) and writes `SOURCE_REVISION` with a
trailing newline. Then it hashes the result with `build_registry_bundle.artifact_digest`. Each run
used a new, empty `mktemp -d` output directory:

```
S=$(mktemp -d /tmp/w159-pin.XXXX)
for rev in 31aa7d70ba208d17bfe53f50fed0e035ff451870 \
           60ec80f7ec5dd68d1e6fa9d13c90e9bd72b5d286 \
           60ec80f7ec5dd68d1e6fa9d13c90e9bd72b5d286; do
  d=$(mktemp -d $S/ctx.XXXX)
  .venv/bin/python scripts/shape_registry_context.py --source ~/Projects/security-standards --revision $rev --output $d
  .venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); from pathlib import Path; \
    from build_registry_bundle import artifact_digest; print('$rev', artifact_digest(Path('$d')))"
done
```

Output:

```
31aa7d70ba208d17bfe53f50fed0e035ff451870 b11a9e9c0c28d0c77e32c6fd831bab9fd9f83278e66d76c4ff0effdc60a416b9
60ec80f7ec5dd68d1e6fa9d13c90e9bd72b5d286 d5cb4a58dc912d7f8c3196dab0013f8516f05e1af439ce25811a53be572803a8
60ec80f7ec5dd68d1e6fa9d13c90e9bd72b5d286 d5cb4a58dc912d7f8c3196dab0013f8516f05e1af439ce25811a53be572803a8
```

- **Control:** the first line reproduces the existing pin's `artifact_sha256` from its revision,
  which shows that the recipe is the one that produced the pin.
- **Twice from fresh archives:** the second and third lines agree.
- **The in-build gate, run locally:** the Dockerfile runs `scripts/build_registry_bundle.py
  --artifact-dir /registry --source-revision … --artifact-sha256 …` (lines 15-19). I ran the same
  command against the third context. With the new digest it exits 0 and writes a bundle with
  `source_revision` `60ec80f7…` and 20 actors, `lane-credential-policy` among them. With the old
  digest it exits 1 with `RegistryValidationError: registry artifact digest does not match`
  (negative control). The Docker build itself didn't run.

The plan, the pin tests and the recipe agree, so I didn't write a question file.

## The psycopg finding

**factory_events's database mode uses nothing that psycopg 3.3.4 lacks.** The `>=3.3.8` floor in
security-standards comes from a Dependabot requirement bump, not from a feature need.

- The floor's history: security-standards `1baec77` (#88, authored by `dependabot[bot]`, "update
  psycopg[binary] requirement") moved all three sites from `>=3.3.6` to `>=3.3.8` and changed
  nothing but `pyproject.toml` and `uv.lock`.
- What the database mode calls, at `60ec80f`:
  - `src/factory_events/db.py` lines 65-67 and 156-161: `psycopg.connect(host=, port=, dbname=,
    user=, password=, connect_timeout=, application_name=)`, `conn.execute`, `conn.read_only`,
    `conn.isolation_level = IsolationLevel.REPEATABLE_READ`, `conn.transaction()` and a named
    (server-side) cursor `conn.cursor(name="factory_events_walk")`.
  - `src/factory_events/db_setup.py` lines 22-25: `psycopg.connect(dsn, connect_timeout=,
    application_name=)` and `conn.execute(bytes)`.
  - `src/factory_events/ship.py` lines 53-55, 68 and 92: `psycopg.connect(dsn)` and
    `psycopg.types.json.Json`.
  - `chain.py` and `cli.py` don't reference psycopg.
- Every one of these is part of the psycopg 3 API from 3.0. I also checked it empirically under
  the orchestrator's 3.3.4 venv, before the bump: `IsolationLevel.REPEATABLE_READ`, `Json`, and
  `Connection.read_only`, `isolation_level`, `transaction`, `cursor` and `execute` all exist, and
  `Connection.cursor` takes `name`. No connection was made.

I raised the floor anyway, as the handoff directs, so the image's environment satisfies what the
copied code declares.

## Packages the lock moved

Per `uv lock`'s own output and `git diff uv.lock` (artifact URL lines aside):

- `psycopg` 3.3.4 → 3.3.9 (`uv.lock` line 605-606).
- `psycopg-binary` 3.3.4 → 3.3.9 (lines 622-623).
- The project's `requires-dist` specifier for psycopg, `>=3.3.4` → `>=3.3.8`.
- No other package moved. 68 packages resolved before and after.
- **The lock's format `revision` line changed from 5 to 3.** See concern 1.

`uv lock --check` passes with uv 0.11.26 (Homebrew, the one launchd's PATH finds) and 0.10.9
(`~/.local/bin`).

## Does a main-tree fast-forward sync its `.venv`

**Yes, when a launcher activates the main tree.** This merge changes the main tree's installed
psycopg to 3.3.9 at the first activation after it lands. I read the script only, at
`~/.claude` `e652bc9`:

- `~/.claude/bin/activate-checkout.sh` `activate_checkout` fast-forwards a checkout on `main` with
  an upstream (lines 64-93) and then calls `_activate_sync_dependencies` (line 102).
- `_activate_sync_dependencies` (lines 111-126) runs `uv sync --frozen` in the checkout when
  `.venv` exists, `uv` is on PATH, and the pulled range touches `pyproject.toml` or `uv.lock`. This
  PR touches both.
- The orchestrator's launchers call it on the main tree, for example `scripts/run-bump-driver.sh`
  line 64, `run-execution-watch.sh` line 58, `run-fork-sync-watch.sh` line 55,
  `run-landing-ledger.sh` line 63 and `run-deploy-watcher.sh` line 81.
- launchd's PATH finds `uv`: the live `~/Library/LaunchAgents/com.devon.bump-driver.plist` sets
  `PATH=/opt/homebrew/bin:…`, and `/opt/homebrew/bin/uv` is 0.11.26.

So the Mac carrier and the bump proposer's chain fetch (P-OR-1), which run security-standards
`factory_events` on the main tree's `.venv`, run under psycopg 3.3.9 after the next activation.
Given the finding in the preceding section, I expect no behavior change.
`docs/operations/local-development.md` (line 276 onward) notes that the same sync also rebuilds the
venv when `.python-version` and the venv's interpreter disagree. This PR doesn't change
`.python-version`.

## Tests and checks

All runs used the worktree's own `.venv` and database `orchestrator_test_hq_w_159`, with
`SECURITY_STANDARDS_DIR=tests/fixtures/security-standards` and `PGTZ=UTC`, on the committed tree
(`9776c8e4`, `git status` clean).

| Check | Result |
|---|---|
| `alembic upgrade head` | exit 0 |
| `make check`: `ruff check` | All checks passed |
| `make check`: `ruff format --check` | 1065 files already formatted |
| `make check`: `pyright` | 0 errors, 0 warnings |
| `make check`: pytest (`-n auto`, 10 workers) | 14962 passed, 1 skipped, exit 0 |
| `tests/architecture/test_release_workflow.py` (the pin tests) | 9 passed |
| Digest recompute, twice plus control | Matches (see the recompute section) |
| `build_registry_bundle.py` gate, positive and negative | exit 0 and exit 1 |
| `uv lock --check` (0.11.26 and 0.10.9) | exit 0 |

The release workflow's in-build digest gate can't run here, and I didn't dispatch it.

## Files changed

- `security-standards.pin.toml`: `revision` and `artifact_sha256`.
- `pyproject.toml`: line 15.
- `uv.lock`: psycopg, psycopg-binary, the specifier, and the format `revision` line.
- `docs/superpowers/reports/fe-5-p-pin-report.md`: this report.

## Line citations relied on

| Citation | Commit |
|---|---|
| Plan "P-PIN" (lines 313-327), merge-order checks (352-362), follow-up 4 | orchestrator `03075209` |
| `security-standards.pin.toml` header and values | orchestrator `03075209` |
| `docs/operations/deploy.md` #52 (lines 756-789) | orchestrator `03075209` |
| `scripts/shape_registry_context.py` (whole file), `scripts/build_registry_bundle.py` lines 63-75 | orchestrator `03075209` |
| `Dockerfile` lines 4-19 and 37-44, `.github/workflows/release-image.yml` lines 5-11 | orchestrator `03075209` |
| `tests/architecture/test_release_workflow.py` lines 24-29 | orchestrator `03075209` |
| P-OR-1 report lines 172-179 and 256-259 (psycopg gap) | orchestrator `03075209` |
| `pyproject.toml` lines 11, 15 and 24 | security-standards `60ec80f` |
| `src/factory_events/db.py` 65-67, 156-161; `db_setup.py` 22-25; `ship.py` 53-55, 68, 92 | security-standards `60ec80f` |
| The floor's origin, `pyproject.toml` diff | security-standards `1baec77` (#88) |
| `activate-checkout.sh` lines 64-102 and 111-126 | `~/.claude` `e652bc9` |
| `scripts/run-bump-driver.sh` lines 64 and 106; the other launchers' activation lines | orchestrator `03075209` |
| `docs/operations/local-development.md` lines 142-148 (worktree recipe), 276-282 | orchestrator `03075209` |

## Self-review

- The diff is three files of content and holds the handoff's scope: no other dependency moved,
  and no code changed.
- The new digest is the output of the repository's own scripts, not a hand computation, and the
  same recipe reproduces the old pin.
- The psycopg finding rests on reading every psycopg call site in `factory_events` at `60ec80f`
  and an attribute check under 3.3.4. Nothing ran the database path against a live Postgres under
  either version.

## Concerns

1. **The lockfile's format `revision` dropped from 5 to 3.** Dependabot wrote revision 5
   (`0eba87bc`, #507). The uv versions on this Mac (0.11.26 and 0.10.9) both write 3; the lock's
   content is otherwise the same apart from the two psycopg entries. I tried uv 0.13.8 (the latest
   release, through `uvx`), but its requests to PyPI timed out from this machine after three
   retries, while 0.11.26 resolved in under a second. A revision-3 lock is the older format, which
   every uv here and the Dockerfile's uv 0.5.31 already read. I expect Dependabot's next uv bump to
   write 5 again. If HQ wants revision 5 kept, a session with a working newer uv can relock the
   branch.
2. **I ran `git -C ~/Projects/security-standards fetch -q origin` once** to confirm the facts HQ
   gave. It updates only remote-tracking refs (it was a no-op: `origin/main` was already
   `4e83d7a8`). The working tree and `HEAD` (`cb832f3`) are unchanged, but the handoff asked for
   reads through `git show`/`archive` only.
3. **Scratch left behind:** `/tmp/w159-pin.e2Jh` (shaped contexts, a lock backup, an unused uv
   0.13.8 cache, logs). The test database `orchestrator_test_hq_w_159` remains for any fix round.
   HQ decides teardown of the worktree, branch and database after merge.
4. The merge-order check "the Mac security-standards checkout is past `60ec80f7`" is still unmet:
   it is at `cb832f3`. That's act step 2's job, not this PR's.

## Standards followed

- **Code Brain rule 5 (never log secrets):** the change touches no secret. No command printed a
  credential; the shaped context holds only registry, schema and source files.
- **Code Brain rule 4 (never swallow an exception):** no code changed. The `factory_events` code I
  cited logs and re-raises every driver failure (`db.py` lines 102-115, for example).
- **Code Brain rule 3 (structured logs to stdout, no new log file):** no logging added. The logs I
  wrote are scratch files under `/tmp`, outside the repository.
- **Infra Brain 591, 592 and 593 (no BWS token in a tracked file or commit, no inline
  `BWS_ACCESS_TOKEN`):** the diff holds a git sha, a SHA-256 digest and a version specifier. I
  didn't run `bws` or read the Keychain.
- **Infra Brain 1032 (a credential in a transcript is exposed):** no credential appeared in this
  session's transcript.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; nothing here has an FQDN.
- **Pins duplicated across dependency groups and optional dependencies bump together; `uv lock
  --check`:** the orchestrator has one psycopg site (line 15), which I bumped, and `uv lock
  --check` passes.
- **`uv sync` skips optional dependencies; read the collected-test count:** the orchestrator has no
  optional dependencies holding a test tool, and the local run collected and passed 14962 tests.
  The CI job log's count is for the reviewer to read on PR #705.
- **Construction mode:** I drove the work to the PR without stopping for non-blocking choices, and
  stopped short of the gates the handoff reserves (no merge, no release dispatch, no Coolify call).

## Follow-ups

1. Plan follow-up 4 stands: bump `security-standards.pin.toml` past R-SS's merge commit before
   3f-2. The recipe in the recompute section repeats as written with the new revision.
2. Optional: if HQ wants lockfile revision 5 kept (concern 1), relock this branch with a working
   uv 0.13 or later.
