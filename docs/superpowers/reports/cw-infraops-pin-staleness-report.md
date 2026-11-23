Outcome: done

# Report: cw-infraops-pin-staleness (ADR-0059 Amendment 18 item 5)

Worker W-279. Branch `hq/cw-infraops-pin-staleness`, base `origin/main` `a2c27ce7`.

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

1. **A bundle that's never readable is silent.** Per the spec, unread holds the key and changes no
   exit code. If the pin lines are reformatted, or the raw host keeps failing, no alert ever fires;
   only stderr and CI's parse test notice. A follow-up could raise a second keyed alert after N
   consecutive unread passes. That needs either state or an exit-code decision, so I didn't build
   it.
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
