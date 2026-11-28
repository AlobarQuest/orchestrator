Outcome: done

# Build report: read GitHub App private-key keepers with `read_key`

Worker W-339. Handoff: `sds-hq/state/handoffs/app-key-keeper-read-key.md`.
PR: https://github.com/AlobarQuest/orchestrator/pull/834, branch `hq/app-key-keeper-read-key`,
base `b1b4bd2c`. The PR is open and unmerged. Its head is the commit that adds this report; the
code head is `1b23388a`.

## What I built

Every read of a `github-app-private-key` secret (keeper, quarantine, staging, and each other
App-key keeper) goes through `SecretStore.read_key`, which strips surrounding whitespace. Every
other class keeps `read`, which refuses a padded value through `Value.__init__`.

### Shape chosen, and why

- `secret_store.read_as(store, credential_class, secret_id)` (`secret_store.py:89`) is the one
  class-keyed decision, with `KEY_CLASS = "github-app-private-key"` (`secret_store.py:47`).
  `github_app_key.CLASS` is that constant (`github_app_key.py:62`), so the class string has one
  definition.
- `Rotation` carries the registry row's class (`contract.py:405-406`, `credential_class: str |
  None = None`). `checks.check_unit` sets it from the row (`checks.py:155-162`), and that is the
  only place a Rotation is built on a reading path. `UnitRun.read` (`contract.py:507-509`) reads
  every one of the rotation's secrets through `read_as`.
- I rejected a wrapper store that strips every read during an App-key unit. `github-pat` shares
  the `github` provider name with `github-app-key`, so `worker._other_keepers` reads keepers of
  both classes in a single rotation. A blanket wrapper would strip PAT keepers too, which would
  hit the handoff's stop point. That site dispatches on each registry entry's own class instead.
- I rejected a reader that the adapter supplies to the contract: `inbox.bind`, `checks` and the
  worker's waits read the keeper outside the contract, so the class has to travel with the
  rotation anyway.

### Every changed read site (lines at `1b23388a`)

| Site | Line | Was |
|---|---|---|
| Probe (`cli.probe`) | `cli.py:535` | `store.read` |
| `read-consumers` keeper fingerprint (`cli.read_consumers` → `worker.consumer_reads`) | `cli.py:230`, `worker.py:1050` | `setup.store.read` |
| Adapter's old value (`github_app_key.build_provider.old`) | `github_app_key.py:216` | `store.read` (now `read_key` directly: the adapter is class-specific) |
| `UnitRun.old` (quarantine, keeper) | `contract.py:524`, `525` | `self.store.read` |
| `UnitRun.new` (staging) | `contract.py:531` | `self.store.read` |
| `UnitRun._holds` (a `bws-secret` consumer) | `contract.py:632` | `self.store.read` |
| `_store` (keeper, quarantine, read-back) | `contract.py:712`, `720`, `727` | `run.store.read` |
| `_store_issue` (keeper, read-back) | `contract.py:739`, `747` | `run.store.read` |
| `_confirm_dead` (staging or keeper, quarantine) | `contract.py:876`, `877` | `run.store.read` |
| Inbox bind (keeper) | `inbox.py:192` | `store.read` |
| Issue fact check (keeper, staging) | `checks.py:212`, `220` | `store.read` |
| Loaded wait (keeper fingerprint) | `worker.py:668` | `setup.store.read` |
| Other keepers of the provider (per entry's class) | `worker.py:904` | `setup.store.read` |
| Revoke wait (quarantine) | `worker.py:921` | `setup.store.read` |
| Revoke guide (keeper) | `worker.py:975` | `setup.store.read` |

The issue fact check isn't reached by an App key today (`github-app-key` doesn't issue:
`plans.py:76` defaults `issues` to false). The loaded wait isn't reached either: the GitHub
provider isn't `WaitsForLoad`. Both go through `read_as` anyway, so every reader of a rotation's
secrets makes the same decision.

Reads I left unchanged, because none of them reads an App-key secret: `cli.py:585`
(`pg-restore`, postgres rows only), `cli.py:172-197` and `249` (Healthchecks, ping-URL, notifier
and inbox tokens, bearer), `emit-secret` (`cli.py:459`, a secret by id with no row; its one documented use pipes the
host's root token, `rotation-executor.md` "Put the root token in its file", and no repository
file passes it an App-key keeper, by a grep of `emit-secret` and both keeper UUIDs),
the other adapters' own reads, and `worker.py:626` (Coolify token).

### Comparisons (requirement 2)

Both sides of every `same_as` and every fingerprint on an App-key secret come from `read_as`, and
a staged or inbox value is a `Value`, which can't hold padding. So a padded keeper and the same
key written back stripped compare equal.
`test_a_padded_keeper_equals_its_key_quarantined_stripped_by_an_earlier_attempt` and
`test_the_inbox_reads_a_padded_keeper_and_knows_the_key_it_holds` (the inbox answers "it is the
value the keeper already holds") pin it.

### A real rotation writes the new key unpadded (requirement 4)

The store step writes the keeper with `run.store.write(run.rotation.keeper_id, new)`
(`contract.py:726`, and `744` for an issue). `new` is a `Value`, whose constructor refuses
surrounding whitespace (`values.py:47-48`). `BwsStore.write` sends `value.reveal()` unmodified
(`secret_store.py:156-170`). The quarantine copy is created from the stripped keeper read
(`contract.py:717`). `test_a_rotation_reads_a_padded_keeper_and_stores_the_new_key_unpadded`
asserts that after `store`, the keeper no longer holds padded text and reads back as the new key.

## Tests and checks

New tests (placeholder keys generated at run time by the existing `_pem` and `_stored` helpers).
They use a `PastedStore` fake (`tests/rotation_worker/stores.py`) that keeps raw text and follows
`BwsStore`'s rules: `read` builds `Value(text)` and refuses padding, and `read_key` strips.

| Test | Covers |
|---|---|
| `test_probe.py::test_a_padded_app_key_keeper_is_read_stripped_and_proved` | The probe reads a padded App-key keeper and passes |
| `test_probe.py::test_a_padded_keeper_of_another_class_is_still_not_readable` | A padded Anthropic keeper still exits 2, "not readable", nothing sent |
| `test_github_app_key.py::test_a_rotation_reads_a_padded_keeper_and_stores_the_new_key_unpadded` | Adapter and contract: store, verify, confirm on a padded keeper |
| `test_github_app_key.py::test_a_padded_keeper_equals_its_key_quarantined_stripped_by_an_earlier_attempt` | Comparison: padded keeper equals the stripped quarantine |
| `test_github_app_key.py::test_the_inbox_reads_a_padded_keeper_and_knows_the_key_it_holds` | Inbox bind on a padded keeper |
| `test_github_app_key.py::test_the_worker_s_revoke_alert_reads_a_padded_keeper` | Worker revoke guide |
| `test_github_app_key.py::test_other_keepers_read_a_padded_app_key_and_still_refuse_a_padded_token` | Other keepers: App key read, padded `github-pat-fine-grained` still refused |
| `test_github_app_key.py::test_a_padded_keeper_of_another_class_is_still_refused` | Contract: a padded `bws-lane-token` keeper still raises `ValueError` |

### Failing-without proof

The test helper `Rotating` builds its `Rotation` with the class, which `b1b4bd2c` has no field
for. I ran the new tests (`-k padded`) twice with `src/` reset to `b1b4bd2c`
(`git checkout b1b4bd2c -- src/`, restored from the committed head afterwards):

1. Pure base: 7 failed, 1 passed. Five fail with `TypeError: Rotation.__init__() takes 6
   positional arguments but 7 were given`: the four that use `Rotating`, and the lane-token guard,
   which builds its `Rotation` with the class. `test_other_keepers_…` fails with `ValueError: a
   credential value is non-empty and has no surrounding whitespace` on its App-key half. The probe
   test fails with `assert 2 == 0` (the probe exits 2, "not readable"). The Anthropic probe guard
   passes.
2. Base plus only the `credential_class` field on `Rotation`, with no read site changed: 6
   failed, 2 passed. Five fail with `ValueError: a credential value is non-empty and has no
   surrounding whitespace` (`values.py:48`): the rotation, the earlier-attempt quarantine, the
   inbox, the revoke guide, and the App-key half of `test_other_keepers_…`. The probe test fails
   with `assert 2 == 0`. The lane-token and Anthropic guards pass, as they should: they pin the
   existing refusal.

So each App-key test fails on the defect itself.

The guards were shown red by mutation: with `read_as` changed to call `read_key` for every class
(`if True:` in place of `if credential_class == KEY_CLASS:`), exactly the three guard tests fail
and the five App-key tests pass. The failures are `DID NOT RAISE ValueError` twice (the PAT half
of `test_other_keepers_…` and the lane-token guard) and the Anthropic probe exiting 0 instead of
2. I restored `src/` from the committed head and cleared `__pycache__` before and after.

### Checks

- `make check` at `d87f16ff`, with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` set to
  `orchestrator_test_hq_w_339`, `SECURITY_STANDARDS_DIR` set to the fixture, and `PGTZ=UTC`:
  rc 0, **15,193 passed, 1 skipped**. The first run failed on `ruff format --check` in my own
  `contract.py` edit. I fixed it in `d87f16ff`.
- Last commit `1b23388a` (the two review nits): `ruff check`, `ruff format --check` and `pyright`
  on `src/rotation_worker` are clean, and `tests/rotation_worker` passes (1,995 passed). I didn't
  rerun the full `make check` after this commit. CI's `Quality` job runs it on the PR.
- `code-standards risk`: `high-risk` (secrets: `src/rotation_worker/secret_store.py`).
- `/code-review` (medium): no correctness findings and three nits. I fixed two in `1b23388a`
  (issue-fact reads through `read_as`, and the class passed unconverted in `cli.py`). The third
  (an unclassed `Rotation` reads strictly) is under Concerns.
- `vendor-review`: complete for the seats that ran. Claude approves, Codex approves, and agy is off
  by configuration. One finding was kept (Claude's own, low, `cited`, not reproducible on current
  code: no reading path builds an unclassed Rotation). Nothing was dropped.
- Token-shape grep of added lines (`git diff b1b4bd2c -U0 | grep '^+'` for a BWS-token shape,
  40+ character base64 runs, `ghs_`, `github_pat_`, `sk-ant`, and `BEGIN … PRIVATE`): no match.

## Files changed

- `src/rotation_worker/secret_store.py`: `KEY_CLASS` and `read_as`.
- `src/rotation_worker/contract.py`: `Rotation.credential_class`, `UnitRun.read`, 12 reads.
- `src/rotation_worker/checks.py`: Rotation built with the row's class, issue-fact reads.
- `src/rotation_worker/adapters/github_app_key.py`: `CLASS = KEY_CLASS`, `old` through `read_key`.
- `src/rotation_worker/inbox.py`: `bind`'s keeper read.
- `src/rotation_worker/worker.py`: five reads; `consumer_reads` takes the class.
- `src/rotation_worker/cli.py`: probe and `read-consumers`.
- `tests/rotation_worker/stores.py`: `PastedStore`.
- `tests/rotation_worker/test_github_app_key.py`, `tests/rotation_worker/test_probe.py`: new tests.
  `Rotating` takes an optional store and builds its Rotation with the class.

## Self-review

- The probe still fails closed: the read stays inside the same `except Exception` that prints
  only "keeper is not readable by this identity". An empty or whitespace-only keeper still raises
  `ValueError` from `read_key` (`Value("")`).
- `KeychainStore.read_key` is `read` (`secret_store.py:254-255`), so the Keychain instance's
  behaviour is unchanged. It doesn't run App-key adapters anyway (`store_kind`).
- A row with no `class` or a non-string `class` gets `None` and reads strictly.
- No new log line or message. Every existing message about a read names no value.

## Concerns

- `Rotation.credential_class` defaults to `None`, and `None` reads strictly. Only `check_unit`
  builds a Rotation that is read through today, and it passes the class. A future reading path
  that builds a Rotation without the class would refuse a padded App key. I kept the default
  because the three other constructions (`worker.py:337`, `cli.py:353`, `cli.py:568`) only name
  keys or serve postgres rows.
- `inbox.pasted` (`inbox.py:72`) already read every class's staging and inbox values with
  `read_key`. That behaviour predates this change, and I left it alone.
- The live proof is HQ's G-116 exit check 2 rerun, after the PR merges and the rotation host runs
  the new revision. I made no BWS or GitHub call.

## Follow-ups

None needed in `.cred-consumers.toml`, ADR-0055 or `rotation-executor.md`. Optional text for
`rotation-executor.md` (#833 owns it), if HQ wants the rule written down:

> A `github-app-private-key` secret (keeper, quarantine, staging) is read with `read_key`, which
> strips a console paste's surrounding whitespace (`secret_store.read_as`, keyed on the row's
> class); every other class's secrets are read with `read`, which refuses a padded value. A
> rotation writes the new key unpadded.

## Standards followed

- **Code Brain 3 (logging):** no logging was added or changed. Existing logs stay structured JSON
  to stdout, and no file logging was added.
- **Code Brain 4 (error handling):** no exception is swallowed. A padded value of any other class
  still raises `ValueError` where it did before. The probe still fails closed: a keeper it can't
  read exits 2 with "not readable by this identity"
  (`test_a_padded_keeper_of_another_class_is_still_not_readable`). No bare `except …: pass` was
  added.
- **Code Brain 5 (security, never log secrets):** no message, log line or test output carries a
  key or a slice of one. The only change to what a read returns is stripping whitespace inside
  `read_key`, and the probe's output line is unchanged. The test asserts statuses only.
- **Infra Brain 591, 592, 593 (no token in tracked files or history, bootstrap token not
  inline):** the diff adds no token or token-shaped literal (grep result under Checks). Test keys
  are RSA keys generated at run time. The other placeholders are `"pat-placeholder"` and
  `"lane-token-placeholder"`, which have no provider token shape.
- **Infra Brain 1032 (`cred.exposure-rotate`):** no registry change and no credential exposed, so
  nothing needs rotating. The stored BWS values stay as they are, by Devon's choice.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. The change adds no endpoint and
  no FQDN.
- **Construction-mode ruling:** I drove the work to an open PR without stopping and hit no stop
  point. The fix strips only `github-app-private-key` and needs no change to
  `.cred-consumers.toml`, ADR-0055 or `rotation-executor.md`. `make check` failed only on my own
  formatting, which I fixed.
- **Standards the handoff doesn't cover:** none came up. The change touches no cross-cutting
  concern beyond secret handling, which the listed standards cover.
