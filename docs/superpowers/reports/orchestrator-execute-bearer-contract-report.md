Outcome: done

# Report: the orchestrator half of change-manager's `execute` bearer contract

Worker W-265. PR [#783](https://github.com/AlobarQuest/orchestrator/pull/783), ready for review,
not merged. Code head `37b1b40b`; this report is committed on top of it, following the earlier
reports' convention, so the PR number is known when the report is written. Base `origin/main` =
`0f3929ac`, the same commit the handoff read its facts at. change-manager `origin/main` was read at
`473bc3cb`.

## What I built

- `tests/fixtures/m2m_bearer_setting.json`: `"variables"` gains `"M2M_TOKEN_EXECUTE": "execute"`
  after `"M2M_TOKEN_OBSERVE": "observe",`. No other byte changed. The new sha256 is
  `7bdecabc59e45088cb72c99ac268adce3adfab83daec1a323d347462c33e3156`, which matches the handoff.
- `tests/contract/test_change_manager_bearer_setting_contract.py`: `CONTRACT_SHA256` is that hash.
- `src/rotation_worker/adapters/change_manager.py`: `VARIABLES` gains
  `"M2M_TOKEN_EXECUTE": "execute"`. The module docstring now says five machine bearers and five
  bearers' readers. Nothing else in its behavior changes.
- `src/rotation_worker/adapters/__init__.py`: the overlap comment now says five bearers with five
  values.
- `tests/rotation_worker/test_change_manager.py`: the harness builds its values, rows, and
  siblings from `VARIABLES`, so it covers the execute scope through the constant. The one
  scope-keyed table, the `reads` dict in
  `test_the_registry_names_the_siblings_and_every_readers_check`, gains an `execute` row. The
  missing-keeper test still drops `observe` and still expects `M2M_TOKEN_OBSERVE` to be named.
- `docs/operations/rotation-executor.md`, "change-manager's bearers": the text now lists five
  bearers, five keepers in `Rotation / Keepers`, five bearers' readers, five values, and the other
  four entries to read before a restart. The attested-adapter paragraph now names `read`,
  `propose`, and `observe` as the bearers that use `change-manager`. It doesn't claim a plan for
  `execute`, because change-manager's registry row says the bearer is "Not yet handed to the
  rotation executor".

### Item 4 sweep: what I left, and why

- **ADR-0055** (lines near 1005, 1154 to 1157, 1177, and 1220) says "four bearers" and "four
  keepers". Those are dated findings from amendment 9's inventory, so I left them unchanged.
- **`docs/operations/deploy.md`** belongs to a parallel worker. Its hits (lines 374 and 387) name
  the deploy watcher's one bearer, `M2M_TOKEN_OBSERVE`. They don't list the scopes, so nothing
  there needs this change.
- **`scripts/run-deploy-watcher.sh` and `tests/rotation_worker/test_deploy_watcher_rows.py`** name
  `M2M_TOKEN_OBSERVE` as that lane's one secret. Their "four" counts are GitHub permissions and
  granted secrets, not bearers.
- **`src/deploy_watcher/observe.py:461`** is an `__all__` entry for its `observe` function, not a
  scope.
- **Rotation-destinations contract:** `tests/fixtures/rotation_destination_format.json` holds no
  change-manager destination. The only `entry=` in it is the orchestrator's
  `orchestrator-operator`.
- **Intent-packages pins:** no fixture under `tests/fixtures/intent-packages/` names a
  change-manager bearer variable or scope.
- **Third repositories:** none needs a change from this PR. The paired fixture edit in
  change-manager is the parallel PR the handoff names.

## Item 5 proof

The proof script `/tmp/w265/proof.py` (outside the repository) does the following:

1. It reads change-manager `origin/main:.cred-consumers.toml` (`473bc3cb`) into
   `/tmp/w265/cm-cred-consumers.toml` with `git -C ~/Projects/change-manager show`. Nothing in
   that tree is written.
2. It loads the file through `rotation_worker.registry.provider_entries`, using a fake mirror that
   serves that file for change-manager and an empty file for the other registry repositories.
3. For each `coolify-env-hash` destination it parses (`rotation_worker.destinations.parse`), it
   builds the adapter's `_Registry` and calls `siblings()`. It does this with the `origin/main`
   adapter (BEFORE) and with this branch's adapter (AFTER).

The fake store returns a placeholder value derived from each keeper id, and the script prints the
keeper ids, so no secret is read. Registry rows with provider `change-manager` are `full`, `read`,
`propose`, `observe`, and `execute`. The ids in the following tables are shortened.

BEFORE (`VARIABLES` has four entries):

| Destination scope | `siblings()` keepers |
|---|---|
| `full` | `314f276d-…` (read), `3b9503da-…` (observe), `acccb346-…` (propose) |
| `read` | `3b9503da-…`, `acccb346-…`, `af0e4192-…` (full) |
| `propose` | `314f276d-…`, `3b9503da-…`, `af0e4192-…` |
| `observe` | `314f276d-…`, `acccb346-…`, `af0e4192-…` |
| `execute` | Refused: `hosted_destination` rejects `M2M_TOKEN_EXECUTE` / `execute` as not a change-manager bearer variable |

The BEFORE state has a gap: a rotation of any of the four bearers never checked the execute
keeper, so it could host a value the execute keeper holds.

AFTER (`VARIABLES` has five entries):

| Destination scope | `siblings()` keepers |
|---|---|
| `full` | `314f276d-…`, `3b9503da-…`, `aa7c76af-…` (execute), `acccb346-…` |
| `read` | `3b9503da-…`, `aa7c76af-…`, `acccb346-…`, `af0e4192-…` |
| `propose` | `314f276d-…`, `3b9503da-…`, `aa7c76af-…`, `af0e4192-…` |
| `observe` | `314f276d-…`, `aa7c76af-…`, `acccb346-…`, `af0e4192-…` |
| `execute` | `314f276d-…`, `3b9503da-…`, `acccb346-…`, `af0e4192-…` |

Each scope's keeper map resolves exactly one keeper per other variable (four, no
`ContractError`).

### Can the rotation executor read keeper `aa7c76af-…`? (Verify item)

Repository sources don't prove this. They show two things:

- `docs/operations/rotation-executor.md:448` gives the root (the executor's account) read and
  write on the `Rotation / Keepers` project.
- change-manager's registry row says the secret is in `Rotation / Keepers`. That is the row's
  `note` and header comment, which a person wrote. It isn't a live read.

No source here shows the secret's actual project, and I made no BWS call. **Verify** that BWS
secret `aa7c76af-bd52-4796-9d13-b50a00e035de` is in `Rotation / Keepers`. The stakes rise once
this PR is deployed: `siblings()` then reads the execute keeper on every host and retire of the
other four bearers. If the executor can't read it, every change-manager rotation fails at host
before it writes anything.

## Tests and checks

- **`make check`** at code commit `d3e0905f` (lint, format, pyright, and the full suite): exit 0,
  with 14,873 passed, 1 skipped, and 11 warnings. `pytest --collect-only` at `37b1b40b` reports
  14,874 of 14,885 collected (11 deselected). The later commit changes one doc file.
- **Contract mutation:** with `CONTRACT_SHA256` set back to the old hash
  (`caa7e5a7…`), `test_the_fixture_is_unchanged` fails (1 failed). With the new hash restored, the
  contract file and `tests/rotation_worker/test_change_manager.py` pass (76 passed).
  `__pycache__` was cleared around the mutation.
  `test_the_variables_and_scopes_are_change_managers`, which asserts `dict(VARIABLES) == fixture["variables"]`,
  holds the adapter's `VARIABLES` and the fixture equal.
- **Security scan** from the repo root: 0 BLOCK, 2 WARN, and 1 INFO. The WARN findings are
  `bws.secret-files-gitignored` (`*-migration/` glob) and `bws.manifest-matches-usage` (undeclared
  `aaff45ca-…`). Neither string appears in `git diff origin/main...HEAD`, so both were already on
  `main`. The INFO finding is the standing least-privilege judgment item.
- **`code-standards risk`:** `normal`, so `vendor-review` isn't required.
- **`/code-review` low** on `origin/main...HEAD`: I ran it twice, and each forked run returned an
  empty result with no findings listed.
- **After this report was added:** `tests/architecture/test_layer_manifest.py` passed (3
  passed). A security scan rerun from the repo root gave the same result: 0 BLOCK and the same 2
  WARN and 1 INFO findings.

## Files changed

- `tests/fixtures/m2m_bearer_setting.json`
- `tests/contract/test_change_manager_bearer_setting_contract.py`
- `src/rotation_worker/adapters/change_manager.py`
- `src/rotation_worker/adapters/__init__.py`
- `tests/rotation_worker/test_change_manager.py`
- `docs/operations/rotation-executor.md`
- `docs/superpowers/reports/orchestrator-execute-bearer-contract-report.md` (this report)

## Self-review

- The diff is the smallest one that does the job. Every adapter path reads scopes from `VARIABLES`,
  so one added entry covers `hosted_destination`, `_Registry._others`, and `siblings()`. The proof
  shows this against the real registry.
- None of the files the handoff reserves for the parallel worker were touched.
- No production act was taken, and no secret value was read.

## Concerns

- The keeper-read verify item in the preceding section. Until it's confirmed, deploying this
  change can block every change-manager rotation at host.
- Until change-manager's paired fixture PR merges, the two repositories' `CONTRACT_SHA256` values
  differ. That mismatch is the contract's intended signal of a one-sided edit, and it clears when
  both PRs land.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout in production):** this change adds and
  changes no logging.
- **Code Brain rule 4 (never silently swallow an exception):** this change adds no `except`.
- **Code Brain rule 5 (never log secrets):** no file, test, or report holds a bearer value or a
  real bearer hash. The fixture's hashes are change-manager's pinned synthetic examples, and they
  are unchanged. The proof printed keeper ids (identifiers, not secrets) from a fake store.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN`):** the diff holds no token and no `BWS_ACCESS_TOKEN` assignment. The security
  scan reports 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value anywhere in this session.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. No domain changed.
