Outcome: done

# Report: cw-infraops-pin-staleness (ADR-0059 Amendment 18 item 5)

Worker W-279. Branch `hq/cw-infraops-pin-staleness`, base `origin/main` `a2c27ce7`. PR #796.

## What I built

The pin watcher's pass compares change-window's pinned infraops bundle with infraops `main`'s
bundle, by sha256, and keeps the answer on the keyed alert `infraops-pin-behind:change-window`.

- `src/pin_watcher/bundle.py` (new) does the following:
  - It reads orchestrator `main`'s `lanes/Dockerfile` from the public raw host.
  - It parses `ARG INFRAOPS_COMMIT` and the `ADD --checksum=sha256:` digest of the one `ADD` whose
    URL holds `${INFRAOPS_COMMIT}`, and requires exactly one of each.
  - It swaps the placeholder for `main`, requires the result to stay on `raw.githubusercontent.com`
    over https, and hashes the file that URL serves.
  - It uses a `ConfinedClient` that sends GET only and no `Authorization` header.
- `src/pin_watcher/alerts.py` adds `BUNDLE_ALERTS_FILE = "infraops-pin-alerts.json"` and
  `open_bundle_hub`, a second `KeyedAlerts` with its own resolved title.
- `src/pin_watcher/cli.py` changes in three ways:
  - `_check_bundle` runs right after the credential checks and before the member record read, so
    neither the member record nor GitHub failing skips it.
  - Every pass prints both digests. Only the acting pass syncs the key.
  - The existing `_tell` body became `_sync`, which both key sets use.

### URL derivation

The infraops URL comes only from the Dockerfile's `ADD` line, so infraops' owner, repository and
path aren't written in code. The orchestrator's own Dockerfile URL needs the orchestrator's
repository named once. It's the constant `DOCKERFILE_PATH =
"/AlobarQuest/orchestrator/main/lanes/Dockerfile"`, which follows the precedent of
`rotation_worker.worker.BEHIND_REPOSITORY` and the pin watcher's own `RUNNER_REPOSITORY`. I found
no way to derive it that wasn't more fragile: the baked copy is the wrong file to read, and the
member record isn't a source of this repository's name.

### How the key is isolated

The bundle key never shares a `KeyedAlerts` or a state file with the `pin-behind:<repo>` keys.
Caller keys stay in `alerts.json` with the resolved title "Caller pin is at the recommendation".
The bundle key lives in `infraops-pin-alerts.json` with the resolved title "change-window's
infraops pin matches infraops main". Both files are in `PIN_WATCHER_STATE_DIR`, which is the lane
volume. Two tests cover the two directions:

- `test_a_caller_pass_never_resolves_the_bundle_key`: a pass that resolves a caller key leaves the
  bundle key remembered and sends no resolve for it.
- `test_a_bundle_resolution_never_resolves_a_caller_key`: a pass that resolves the bundle key
  leaves the caller key open, and the only resolved title sent is the bundle's.

### What a live pass logs

The pass logs one stdout line. I read it live, with no credential, against the real files on
2026-11-23:

```
infraops pin current: pinned cb94a415ec93 (commit 8dc4d43e3094), main cb94a415ec93
```

When the digests differ, the line reads `infraops pin behind: …`. An unreadable side logs
`UNREAD: change-window's infraops pin: <URL> answered <status>` (or `is unreachable: <type>`, or
the parse reason) to stderr.

### Exit codes and the dead-man switch

The exit codes and `sds_deadman_arm sds-pin-watcher --finding 2` are unchanged.

- A stale or unreadable bundle changes no exit code.
- A failed send of the bundle key, or an unusable state file, is the existing incomplete code
  (3). The existing exit 3 already covers an alert-hub send that failed. The caller keys are still
  synced when the bundle key's send fails, and a test covers that.
- `run-pin-watcher.sh` is untouched. No secret, compose variable, or network path was added.

## Tests and checks

- `make check`: green. **14,737 passed, 1 skipped** (ruff, format, and pyright all clean). The
  first run failed one guard: `test_out_of_process_isolation` flagged the new `collections.abc`
  import, so I added `collections` to the pin watcher's row. The second run passed.
- `tests/pin_watcher`: 125 passed. The new `tests/pin_watcher/test_bundle.py` covers:
  - Parsing the real tracked `lanes/Dockerfile`, so a reformat of the pin lines fails CI.
  - Six malformed Dockerfiles.
  - Equal digests read as current, and the reads carry no credential.
  - Stale detection.
  - A non-200 response on each URL, which names the URL and status.
  - No answer, and a non-UTF-8 Dockerfile.
  - Fire, then resolve.
  - Unread holding the key, in four ways.
  - Isolation, both directions.
  - Both digests appearing in the log.
  - A dry run sending nothing and writing no state file.
  - A failed bundle send alone giving exit 3.
  - A failed bundle send still telling the callers.
  - The alert token never appearing in the output.

  An autouse `raw` fixture in `conftest.py` stubs the raw host (equal by default), so no test
  fetches GitHub.
- Mutation results: I mutated each item, ran the tests, and restored it, clearing `__pycache__`
  each time. Seven of eight mutants were killed.

  | Mutation | Result |
  |---|---|
  | `stale` always False (unequal never fires) | killed |
  | `stale` always True (equal never resolves) | killed |
  | `classify(None)` returns no unread key (unread resolves) | killed |
  | bundle state file = `alerts.json` (shared state) | killed |
  | bundle send failure ignored in the exit code | killed (after I split the test; the first form survived because caller sends failed too) |
  | bundle send failure skips the caller sync | killed |
  | parse accepts two bundle `ADD`s | killed |
  | CLI skips the sync on unread instead of passing the key as unread | survived: equivalent mutant. `sync({}, {KEY})` with `KEY` remembered sends nothing and re-saves the same state, so skipping the sync is observably identical. |

- Risk verdict (`code-standards risk --repo .`): **normal**, so vendor-review wasn't needed.
- `/code-review` (medium): four nits and no correctness bug. I kept the design. The nits are
  listed under follow-ups.

## Files changed

- `src/pin_watcher/bundle.py` (new), `src/pin_watcher/alerts.py`, `src/pin_watcher/cli.py`
- `tests/pin_watcher/test_bundle.py` (new), `tests/pin_watcher/conftest.py`,
  `tests/pin_watcher/test_client_surface.py` (the HTTP-speaking set is three files, with a reason)
- `tests/architecture/test_wsp21_invariant_scan.py`: `OUTBOUND_ALLOWLIST` entry for `bundle.py`,
  with its reason.
- `tests/architecture/test_out_of_process_isolation.py`: `collections` added to the pin watcher
  row.
- `docs/decisions/0059-…md`: Amendment 19 appended. It names the owner, the state file, and the
  verify item's closing condition. No earlier passage is rewritten.
- `docs/operations/deploy.md`: one paragraph in "Bump change-window's infraops pin" covering the
  key, its state file, and how it resolves. There's no dedicated pin watcher page, so the pin
  bump section, which the handoff names as the resolution, holds it.
- `layers.toml`: unchanged. The existing `src/pin_watcher/**` and `tests/pin_watcher/**` patterns
  cover both new files, and `test_layer_manifest.py` passes. Adding a pattern would duplicate one.

## Self-review

- Amendment 18 item 5 matches the current code: the pin watcher does measure pins against a
  recommendation and alerts per key. Nothing in the amendment is false, so I didn't stop there.
- The fire, resolve, and hold behavior is pinned by tests that kill the mutants.
- The check runs before the member record read. The cost is up to two 30-second timeouts when the
  raw host hangs. I judged that worth it, because otherwise an unreadable member record would hide
  the bundle signal.

## Concerns and follow-ups

1. **Decision for HQ: a stale pin and an unreadable check both exit 0.** I kept the exit codes
   unchanged, as the handoff's wording directs, so the bundle check affects the exit code only
   through a failed send (3). That differs from the lane's own convention in two ways. The
   launcher header defines 0 as "nothing was found", yet a stale pin also exits 0, because the
   alert is its signal. And an unreadable caller exits 3, while an unreadable bundle exits 0, with
   only stderr and CI's parse test to notice it. Mapping stale to 2 and unread to 3 would follow
   the lane's convention, but it changes which passes the dead-man switch reports as failed. That
   change is HQ's to make.
2. **The parse is strict by design.** `ADD --checksum=… <url> change-mgr-cli.js` must be the
   instruction's exact shape. An added `--link` or `--chmod` flag makes it unreadable. The tracked
   Dockerfile test fails in CI first, which is the behavior the handoff asked for.
3. **CDN lag.** raw.githubusercontent.com caches for about 5 minutes. A pass within minutes of a
   bump merging could read the old Dockerfile for one pass. Its "behind" would still be true of
   what it read, and the next daily pass resolves it.
4. **Verify item closure needs a deploy.** Amendment 18's first verify item closes when this
   merges and one live acting pass logs both digests. That pass needs a lane image release and a
   redeploy of `sds-lane-pin-watcher`, which are HQ's acts.
5. The orchestrator repository is named once in `bundle.py`, as described under "URL derivation".

## Standards followed

- **Code Brain rule 3 (structured logs to stdout, not files):** the digests line goes to stdout
  through `typer.echo`, like the watcher's other lines. Failures go to stderr. No logging library
  and no log file were added.
- **Code Brain rule 4 (never swallow silently):** `BundleUnreadable` is caught in one place and
  logged with its reason. The reading becomes unread, never equal. Transport errors are converted
  by `ConfinedClient` to `TransportFailure` and then to `BundleUnreadable`, with the type only.
  Nothing else is caught.
- **Code Brain rule 5 (never log secrets):** no credential exists in the check. The alert token
  goes only to `KeyedAlerts`, and `test_the_alert_token_never_reaches_the_output` asserts it's
  absent from output. The URLs carry no credential (lesson 6).
- **Code Brain rule 7 (log failures with context):** every unread message names the full URL and
  either the status or the transport error type, or the parse reason.
- **Road `notifications`:** the alert goes through `alert_hub_client.KeyedAlerts` with a stable key
  (`infraops-pin-behind:change-window`, no timestamp or counter). A later equal reading sends
  `status: resolved`. The helper doesn't raise past its `AlertHubError`, which the pass already
  handles as incomplete.
- **Infra Brain BLOCK 2, 591, 592, 593 (no secrets or BWS token in git, history, or inline):** no
  secret, token, or BWS read was added, and the diff holds only public URLs and digests.
- **Infra Brain BLOCK 1032 (report exposed credentials):** I saw no exposed credential.
- **Infra Brain BLOCK 3 (no `:latest`):** no image tag was touched.
- Rules 1, 4, 5, 6, 211, and 212 don't apply, as the handoff states.
- Do-not-touch list: `.cred-consumers.toml`, `rotation-executor.md`, `tests/rotation_worker/`,
  `lanes/Dockerfile`, `run-change-window.sh`, `lanes/change-window/`, `sds-cred-registries.sh`,
  `.bws-secrets.toml`, and compose are all unchanged.

## Fix round 1

Worker W-280. The round builds on PR #796's head at its start, `65961049`; nothing later than
that commit was on the branch. The round's commits sit on top of it.
I reached the head with `git merge --ff-only`: `git reset --hard` was denied by the harness, and a
fast-forward gives the same result because the worktree branch was at the PR's base, `a2c27ce7`.

### Findings and fixes

1. **`deploy.md` named the wrong lane's state file.** The revision-watcher paragraph is back to its
   `a2c27ce7` text, line breaks included: `git diff a2c27ce7 -- docs/operations/deploy.md` no
   longer touches it. The pin-watcher paragraph names `alerts.json` and
   `infraops-pin-alerts.json`. The pin-bump paragraph says "a pass after the bump merges resolves
   the alert" instead of "the first pass".
2. **An unread bundle was silent when no key was open.** `_check_bundle` now returns whether the
   check is complete: `False` when the reading is unread, and `False` when an acting pass's sync
   failed. The pass ends with exit 3 in either case. An acting pass still syncs the unread key
   first, so an open key stays held and is never resolved. A stale pin keeps the exit code the
   callers give. `scripts/run-pin-watcher.sh` and `sds_deadman_arm` are unchanged. In both, 3
   already means an incomplete pass: the launcher passes 0 to 3 through, and the dead-man helper
   sends `/fail` for every non-zero exit except the declared finding code 2. So the stop point
   doesn't apply.
   - **Dry run:** a `--dry-run` pass with an unreadable caller exits 3. `result.unreadable` is
     checked whatever the mode, as `test_a_listing_that_cannot_be_read_whole_exits_three_and_files_nothing[dry-run-…]`
     shows. A dry run with an unread bundle matches it: exit 3, nothing sent, and no state file
     written.
3. **ADR-0059 Amendment 19:** decision item 4 now states the rule: unread exits 3, a dry run
   included; the key is held and never resolved. It also gives the reason: a moved or renamed
   bundle would otherwise stay green. Item 5 no longer says a stale or unread bundle changes no
   exit code; it says only a stale one doesn't. Only Amendment 19 was edited.
4. **`cli.py` module docstring:** describes the order (credential checks, bundle check, member
   record) and the unread rule. `bundle.py`'s module docstring also says the pass exits
   incomplete on unread.

### New and changed tests (`tests/pin_watcher/test_bundle.py`)

- `test_unread_holds_an_open_key_and_is_incomplete` (4 failure kinds): replaces the earlier
  exit-0 test. With the key open, the pass exits 3, nothing is resolved, and the key stays
  remembered.
- `test_unread_with_no_open_key_is_incomplete_not_silent` (4 failure kinds): with the key never
  opened, the pass exits 3, sends nothing, and writes no key.
- `test_unread_outranks_a_caller_finding`: an unread bundle alongside a drifted caller exits 3,
  and the caller's key still fires.
- `test_a_dry_run_with_an_unread_bundle_is_incomplete_and_sends_nothing`: exits 3, sends nothing,
  and writes no state file.
- `test_a_stale_pin_keeps_the_callers_exit_code`: a stale pin exits 0 with current callers and 2
  with a drifted one. The existing equal and stale pass tests still assert exit 0.

### Mutation result

Three mutants of `src/pin_watcher/cli.py`, run against `tests/pin_watcher`, with `__pycache__`
cleared around each. The file was restored byte-for-byte afterward.

| Mutant | Result |
|---|---|
| `return reading is not None` becomes `return True` (unread leaves the exit unchanged) | killed, 10 failed |
| The exit test drops `bundle_complete` | killed, 11 failed |
| A failed bundle sync no longer returns `False` | killed, 1 failed (`test_a_failed_bundle_send_alone_is_incomplete`) |

### Checks

- `uv run pytest tests/pin_watcher`: 132 passed.
- `make check` (database `orchestrator_test_hq_w_280`, migrated to head, `PGTZ=UTC`): ruff
  "All checks passed!", pyright 0 errors, and **14744 passed, 1 skipped**.
- `uv run --project ~/Developer/code-standards code-standards risk --repo .`: **`normal`**.

### Files changed in this round

- `src/pin_watcher/cli.py`: the unread rule and the module docstring.
- `src/pin_watcher/bundle.py`: the module docstring's unread bullet.
- `tests/pin_watcher/test_bundle.py`: the tests listed earlier.
- `docs/operations/deploy.md`: finding 1.
- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`: Amendment 19
  only.
- This report.

### Standards followed (fix round 1)

- **Code Brain rule 3:** the unread case still logs one `UNREAD:` line to stderr through
  `typer.echo`, and the summary line goes to stdout. No logging library and no file were added.
- **Code Brain rule 4:** `BundleUnreadable` is still caught in one place and logged. The pass no
  longer swallows it at the pass level either: it surfaces as exit 3, which the dead-man check
  reports as a failure.
- **Code Brain rule 5:** no credential was added. `test_the_alert_token_never_reaches_the_output`
  still passes.
- **Code Brain rule 7:** the unread messages are unchanged: URL and status, transport error type,
  or parse reason.
- **Road `notifications`:** unread syncs the key as unread through `KeyedAlerts`, so an open key
  is never resolved. Equal still sends `status: resolved`.
- **Infra Brain BLOCK 2, 591, 592, 593:** no secret, token, or BWS read is in the diff.
- **Infra Brain BLOCK 1032:** I saw no exposed credential.
- **Infra Brain BLOCK 3:** no image tag was touched.
- Rules 1, 4, 5, 6, 211, and 212 don't apply. The do-not-touch list is unchanged, and so are
  `scripts/run-pin-watcher.sh` and `sds_deadman_arm`.

### Code review

I ran `/code-review` at medium effort on `65961049..HEAD`. It returned three nits and no blocking
findings:

- **An unread bundle beside a caller finding exits 3, not 2.** This is intended. It follows HQ's
  decision and the lane's existing rule that 3 outranks 2. `test_unread_outranks_a_caller_finding`
  pins it, and I added one sentence to Amendment 19 item 4 that states it.
- **This section named the starting head ambiguously.** I reworded its first paragraph.
- **`_check_bundle` returns one bit for two causes (unread, sync failed).** I kept the single
  bit, because the pass needs only completeness, the stderr line says which cause it was, and the
  mutation runs show the tests pin both causes. No change.

After review, I also added the unread bundle to the exit-code table at the top of `cli.py`'s
docstring (also its `--help` text). I confirmed the dead-man claim against the exit trap
(`scripts/sds-deadman.sh`, `sds_deadman_finish`): an exit other than 0 or the declared finding
code (2) sends `/fail`.

