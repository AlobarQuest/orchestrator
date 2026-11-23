Outcome: done

# Report: increment 7 code step, the lane token's `loaded` fingerprint proof

- **Worker:** W-272
- **Branch:** `hq/lanes-inc-7-loaded-fingerprint`
- **Base:** `883cd863`
- **Pull request:** [#789](https://github.com/AlobarQuest/orchestrator/pull/789), ready for review.
  HQ reviews and merges.

## What I built

ADR-0055 Amendment 29 item 3, as HQ answered stop point 1 (decisions (a) and (b) yes, plus the
additions in its answer).

1. **The helper's consumers-only target** (`deploy/vps/sds-pg-rotate`, `_target(name, verb)`).
   - A target with none of `container`, `role` or `database` is consumers-only. `loaded` accepts
     it, and `set` and `probe` refuse it as `bad_target`. Amendment 29 item 3 says "`_target`
     refuses it for `set` and `probe`"; that maps to the existing `bad_target` code, from `_ident`.
   - A target naming any of the three must name all three validly, for every verb, so a Postgres
     target keeps today's validation for `loaded` too. A partial target is refused for every verb.
   - `_loaded` is unchanged. It still refuses a missing or empty consumer list, a non-table
     consumer, a bad container name and a malformed env name.
2. **The adapter's proof** (`src/rotation_worker/adapters/bws_lane_token.py`).
   - `helper_target(row)` returns `None` when the field is absent; it isn't required. `check_row`
     refuses a malformed value before a claim. `build_provider` refuses a row naming a target
     when no helper is configured.
   - `LoadedRead.mismatch(expected)` makes one `HelperClient.loaded` read and returns a reason
     unless the map is non-empty and every `<container>/<env>` print equals
     `printed(expected)`, that is `<sha256_prefix>:<length>`. The match is exact string
     equality, not `Fingerprint.matches`, so an 8-character prefix doesn't pass.
   - `consumer_loaded`: with no target, or on an issue, it's today's code unchanged (the `PATCH`
     2xx proof). A reissue on a target row makes one `loaded` read, sends no `PATCH`, and raises
     `ContractError` on anything but a match. It must raise, because `_verify_consumers` would
     only count a hosted consumer that returned `False` ("N-1 of N") and pass. The docstring
     records that `consumer_loaded` is reached only from `verify_consumers` and `retire_old`
     (`UnitRun.unverified`), never from store or distribute.
   - `loaded_wait(unit, keeper)` returns `None` without reading anything unless the provider has a
     target, the plan isn't an issue, and the unit has a `verify_consumers` step. `keeper` is a
     callable, so a row without a target never reads the keeper before the claim.
3. **The pre-claim wait** (`src/rotation_worker/worker.py`).
   - A `runtime_checkable` `WaitsForLoad` protocol, and `_loaded_waits` in `_take`'s pre-claim
     chain right after `_adapter_waits`. `Adapter.wait` is unchanged.
   - A reason, a `ContractError` (including `HelperRefused`, no answer and a malformed answer),
     or a `StoreError` (the keeper, or the helper's SSH key) becomes `Wait(reason)`. That's the
     ordinary 48-hour `rotation-unit-waiting` clock, never the reader clock. Anything else that
     raises escapes to `run_pass`'s generic handler, which leaves the unit unclaimed as a stuck
     wait (`worker.py`, lines 182-188). No path is a pass.
   - `_provider` passes `issue=approved.plan.issue` through the new `AdapterContext.issue`, whose
     default `False` is the strict path. `build_provider`'s `pg_helper` and `issue` are required,
     so the context's default is the only default.

`cli.py`'s two other `AdapterContext` constructions can't reach the lane-token adapter.
`seed_canary` (about line 272) builds only the fake adapter, and `pg-restore` (about line 421)
only `postgres-role`. Neither needs `issue` or the helper.

## Transition rule

A row without `helper_target` behaves exactly as before. It never builds the helper, never reads
the keeper before the claim, and proves its variable by the same `PATCH`. Every issue does the
same, with or without a target. I added no `helper_target` to `.cred-consumers.toml`. Tests pin
this: `test_a_row_without_a_helper_target_proves_its_reissue_by_the_patch_and_builds_no_helper`
runs with a helper factory that fails if called, and
`test_a_row_without_a_helper_target_never_waits_on_a_load` passes a keeper read that fails if
called.

The merge is inert for a second reason too: the VM (`provision.sh`), the Mac
(`install-rotation-worker-launchd.sh`) and the host helper (installed by hand) each run a pinned
install, which a merge doesn't change. The stop point 1 file has the line references.

## Tests and checks

- **`make check`:** green at the final commit. 14,668 passed and 1 skipped (14,669 collected),
  with ruff, pyright and the whole-repo guards, against `orchestrator_test_hq_w_272`.
- **CI on #789:** every check passes. The `Quality` job collected 14,669 tests: 14,655 passed and
  14 skipped.
- **New tests:** 48.
  - `tests/deploy/test_sds_pg_rotate.py`, 20 (41 before, 61 after): a consumers-only target
    answers `loaded` with the exact `docker exec ... printf` argv; `set` and `probe` refuse it; a
    partial target is refused for every verb (9 cases); six missing or malformed consumers are
    refused with no docker call; a Postgres target still serves all three verbs; and the parity
    test.
  - `tests/rotation_worker/test_lane_token_loaded.py`, 28, through `worker._take` with the real
    adapter and contract:
    - A reissue's `verify` passes on an equal print, with two reads (before and after the claim)
      and no `PATCH`.
    - Eight answers wait unclaimed (`client.calls == []`) on a 48-hour non-reader `Wait`: the old
      print, an empty print, one of two prints matching, an 8-character prefix, an empty map, a
      refusal, no answer, and an unreadable key.
    - An unreadable keeper waits and asks no helper.
    - The claimed read fails the unit on a mismatch and on an unreadable answer, with exactly two
      reads.
    - A reissue's `store` unit with a target completes and reads nothing (HQ's addition).
    - An issue's `store` unit with a target keeps the `PATCH` and never reads `loaded`.
    - The two no-target pins above.
    - A context built without `issue` takes the `loaded` read (HQ's addition).
    - Build refuses a target with no helper; `check_row` refuses five malformed targets and passes
      a row with none.
    - A wait logs `unit_waiting` with its unit and a fingerprint-only reason.
    - The `loaded` path answers only for the approved variable, and reads nothing otherwise.
    - Captured logs, the failure reason and the raised text hold no token. The log tests
      re-enable the executor's logger first: an earlier test's alembic `fileConfig` on the same
      xdist worker disables it, which made the first version of the log test fail in the full
      run (and would have let a log assertion pass with nothing to read).
    - Only the VM instance takes a lane-token unit (`_ours` is false on a Keychain store).
- **Fingerprint parity (HQ's item 3):**
  `test_the_helper_s_print_and_the_lane_token_adapter_s_compare_equal` runs the real helper's
  `run` through the fake docker. It feeds the answer through a real `HelperClient` (its answer
  regex included) into the adapter's `LoadedRead.mismatch`. The same fake value matches, and a
  different value and a one-character-shorter value don't.
- **Mutation proof:** 20 mutations, one per rule, each run against both test files with
  `__pycache__` cleared before and after. All 20 were killed, and there are no survivors.
  - The first run left two survivors, which I fixed:
    - "`issue` defaults to the `PATCH` path" survived because `build_provider` had its own
      default. I made its `issue` and `pg_helper` required and mutated `AdapterContext`'s default
      instead.
    - "The value in the failure text" survived because `_work` scrubs failure reasons. I added
      an assertion on the raised text itself, which no scrub reaches.

  | # | Rule broken | Result |
  | --- | --- | --- |
  | M1 | `loaded` skips all target validation | killed |
  | M2 | a consumers-only target serves `set` and `probe` | killed |
  | M3 | a claimed mismatch returns `False` instead of raising | killed |
  | M4 | a target row's reissue keeps the `PATCH` | killed |
  | M5 | `issue` ignored | killed |
  | M6 | `AdapterContext.issue` defaults to `True` | killed |
  | M7 | an empty map passes | killed |
  | M8 | the exact match loosened to a prefix | killed |
  | M9 | an unreadable answer passes | killed |
  | M10 | `_loaded_waits` dropped from the pre-claim chain | killed |
  | M11 | the wait put on the reader clock | killed |
  | M12 | the wait reads in every unit | killed |
  | M13 | a malformed `helper_target` accepted | killed |
  | M14 | no refusal when no helper is configured | killed |
  | M15 | the print format drifts (`/` for `:`) | killed |
  | M16 | a row without a target gets one | killed |
  | M17 | the token value in the raised text | killed |
  | M18 | the worker doesn't pass `issue` | killed |
  | M19 | the `loaded` path drops the approved-variable guard | killed |
  | M20 | the wait isn't logged | killed |

- **Risk command:**
  `uv run --project ~/Developer/code-standards code-standards risk --repo .` answered
  `high-risk` (auth: `bws_lane_token.py` and its test).
- **Vendor review:** complete for the seats that ran, on the diff through the second commit
  (`f4bc8ee6` and the strict-default commit). The commits after it add tests, an inline comment
  and this report, and change no logic. Claude approved with no findings, and Codex
  approved with no findings ("No material blocking issue is supported by the provided diff and
  repository context"). agy is off by configuration. Nothing was dropped, so there was nothing to
  reproduce.
- **`/code-review` (medium, on the branch):** five nits, none a bug.
  - **The `dict(consumer) != dict(self._variable)` guard.** No change. It's the guard
    `write_variable` already has, and both sides parse the same approved destination string.
  - **Exceptions outside `ContractError` and `StoreError`.** No change. `run_pass` leaves the
    unit unclaimed as a stuck wait, as checked above.
  - **The committed `-question-answered.md` file.** Kept. Twelve answered question files are
    committed in `docs/superpowers/reports/`.
  - **An empty target relies on `_loaded`'s refusal.** Already pinned by the `no-consumer` and
    `empty` cases.
  - **The helper must be reinstalled before the cut-over.** Carried to the follow-ups.

  An earlier `/code-review` run mistook `883cd863` for its target and reviewed that commit
  instead. Its findings aren't about this change, and I acted on none.

## Verify items

- **`loaded` tells the new token from the old while both answer.** The helper hashes the value in
  the container's environment (`printf %s "$BWS_ACCESS_TOKEN"` through `docker exec ... sh -c`)
  and returns only the sha256 prefix and length. A container reports the token it loaded at its
  last start. Before the session's restart it prints the old token's fingerprint, a mismatch, so
  the unit waits; after it, the new one's, a match. Both tokens being live in BWS doesn't change
  what the container loaded. The parity test proves the two formats agree, and the `old-token`
  case proves an old print waits.
- **The lane image has `sh` and `printf`.** `lanes/Dockerfile` builds `FROM ${ORCHESTRATOR_IMAGE}`
  (lines 12 and 44), and the root `Dockerfile` is `FROM python:3.14-slim` (lines 2 and 22),
  Debian 13. Debian's Essential packages keep `/bin/sh` (dash, where `printf` is a builtin) and
  coreutils' `/usr/bin/printf`. The exec runs as `USER orchestrator` (`Dockerfile`, line 48),
  which needs no privilege to read its own environment. I read only build files and ran no image.
- **The restart wait fits the 2-hour lease and the 02:00-06:00 window.**
  - The wait is `_loaded_waits`, in `_take` before `_work`, which is where `_claim` is called
    (`worker.py`). A waiting unit is never claimed.
  - The claimed step is one `loaded` read: `consumer_loaded` calls `LoadedRead.mismatch` once,
    bounded by `LOADED_TIMEOUT_SECONDS` (30) plus the SSH connect timeout (10). That's far inside
    the 2-hour lease, and nothing waits inside the claim.
  - The session restarts the lane in its quiet slot. The next pass's pre-claim read then matches,
    and the claim waits for the window as any windowed `verify` claim does. I didn't touch
    `plans.ENV_WRITING_STEPS`, so `verify` stays windowed.
- **The container name form the helper matches.** `docker exec` takes the target's `container`
  string exactly, validated against `IDENT` (`[A-Za-z_][A-Za-z0-9_.-]*`). There's no prefix or
  pattern match. Whether Coolify keeps that name stable across redeploys is HQ's live read.

## Files changed

- `deploy/vps/sds-pg-rotate`: `_target(name, verb)`, and its docstring.
- `src/rotation_worker/adapters/bws_lane_token.py`: `helper_target`, `printed`, `LoadedRead`,
  `loaded_wait`, both proofs in `consumer_loaded`, and the module docstring.
- `src/rotation_worker/adapters/__init__.py`: `AdapterContext.issue`, and passing the helper and
  `issue` to the lane-token build.
- `src/rotation_worker/worker.py`: `WaitsForLoad`, `_loaded_waits` in `_take`, and `issue` passed
  at `_provider`.
- `tests/deploy/test_sds_pg_rotate.py`: 20 tests.
- `tests/rotation_worker/test_lane_token_loaded.py`: new, 26 tests. `layers.toml` already covers
  it (`tests/rotation_worker/**`).
- `docs/superpowers/reports/lanes-inc-7-loaded-fingerprint-question-answered.md` (stop point 1)
  and this report.

## Self-review

- **No suppression comments.** None was added (no `# noqa`, `# type: ignore` or
  `eslint-disable`).
- **Python 3.12 helper.** The helper still parses as Python 3.12
  (`test_source_parses_as_python_312`) and adds no multi-exception `except`.
- **Values never leave in plain text.** A value reaches no argv, log line, reason or exception.
  The reason texts hold fingerprints, target names and refusal words only.
- **The second read is deliberate.** The claimed read repeats the pre-claim read so that a lane
  restarted again between the pass's read and the claim fails closed instead of passing on a
  stale read.
- **SSH reads while waiting.** A waiting `verify` unit makes one SSH `loaded` call per pass until
  the restart. That's the cost of the unclaimed wait the amendment chose.

## Concerns

- **The helper must be reinstalled before the cut-over gives any row a `helper_target`.** The
  installed helper refuses a consumers-only target, and the merge doesn't change it. The verify
  unit would then wait until its 48-hour alert, with `the helper refused loaded: bad_target` as
  its reason. It fails safe, but the cut-over needs the reinstall first.
- **Broker lanes, as HQ asked:** No broker lane's lane-token row may get `helper_target`; only
  the broker's own row could, naming its own container.
- **No standard covers it, beyond those HQ copied in.** No Code Brain `secrets` road exists, as
  the handoff says. The change touches no other cross-cutting concern.

## Follow-ups (not done here; each is outside this handoff's scope)

1. **The cut-over** (Amendment 29 item 4): reinstall `sds-pg-rotate` at this commit, write each
   lane's consumers-only target in `targets.toml`, and add `helper_target` to the lane-token rows
   (never a broker lane's).
2. **`docs/operations/rotation-executor.md`**, the "Lane tokens and ping URLs" and "Install the
   helper" sections: describe the consumers-only target, the `loaded` wait, and the rule that
   every listed consumer must match.
3. **A `src/rotation_worker/CLAUDE.md` invariant, once this merges.** The rule: a lane-token row
   naming `helper_target` proves a reissue's variable only by an exact `loaded` fingerprint match.
   The wait is unclaimed, and the claimed read raises on a mismatch. An issue, and a row with no
   target, keep the `PATCH` proof. The handoff limits me to code, tests and docstrings, so I
   didn't edit `CLAUDE.md`.
4. **Evidence doesn't name the proof.** `verify_consumers` files the same "N of N consumers hold
   or attest the new value" note for both proofs, so the evidence row doesn't show whether the
   `PATCH` or the `loaded` read proved the variable. Naming it would change the contract's note,
   which is outside this handoff.
5. **ADR-0055:** record that Amendment 29 item 3's "refuses it for `set` and `probe`" is the
   `bad_target` code, and that the 16-hex comparison is exact.

## Standards followed

- **Code Brain 3 (BLOCK, error-logging): structured JSON logs to stdout, no file logs.** The one
  new log line is `LOG.info("unit_waiting", extra={...})`, the executor's existing structured
  logger. It writes no file.
- **Code Brain 4 (BLOCK, error-handling): never swallow silently.** `_loaded_waits` catches
  `ContractError` and `StoreError` deliberately, with an inline justification at the `except`
  (an unreadable answer is a wait, never a pass). It logs `unit_waiting` with the error's scrubbed
  text and records a `Wait`, which raises `rotation-unit-waiting` at 48 hours. A test pins the log
  line, and mutation M20 proves the test fails without it. There's no `except ...: pass`.
- **Code Brain 5 (BLOCK, security): never log secrets.** Only fingerprints (prefix and length)
  appear in reasons, logs and exceptions. `test_no_log_line_or_failure_reason_holds_a_token`
  checks captured logs and the failure reason. The raised-text assertion covers the path no scrub
  reaches, and mutation M17 proves that assertion fails when a value leaks. The helper sends only
  the fingerprint over SSH.
- **Infra Brain 591, 592 and 593 (BLOCK, security): no BWS token in tracked files.** The fixtures
  are `lane-token-placeholder-new` and `-old`, `"b" * 64`, `"k" * 32` and
  `throwaway-coolify-token`, none in the token shape. No config holds a `BWS_ACCESS_TOKEN` value.
  The bws-write-guard hook allowed every write.
- **Infra Brain 1032 (BLOCK, security): no revoke without attested consumers.** For a target row,
  `verify`, which the person's revoke follows, passes only on an exact fingerprint match. Every
  mismatch, empty print, empty map, refusal, missing answer and unreadable key either waits
  unclaimed or fails the claimed unit. Mutations M3, M7, M8 and M9 prove each such hole fails a
  test.
- **The orchestrator `CLAUDE.md` invariants.**
  - The step that must wait waits before the claim, in `_take` (`src/rotation_worker/CLAUDE.md`).
  - The two wait clocks stay separate (M11).
  - Every step runs through `contract.py`; no step was re-implemented.
  - I used a per-worktree test database.
  - The new file is in `layers.toml` through its existing pattern.
- **`STANDARDS.md` gates.** `make check` is green, and `/code-review` ran on the branch. The risk
  command answered `high-risk`, so `vendor-review` ran with Claude and Codex; agy is off by
  configuration.
