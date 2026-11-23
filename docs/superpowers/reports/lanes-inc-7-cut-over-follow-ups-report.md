Outcome: done

# Increment 7 cut-over follow-ups: build report

Worker W-282, branch `hq/lanes-inc-7-cut-over-follow-ups`, base `origin/main` `51a8bbe7`. Pull
request: #798.

**Read first (item 7):** the Mac's Keychain instance runs `06cfea7c` and raises
`rotation-instance-behind:keychain` on its first pass after **2026-11-24T04:32:51Z**, not about 7
days after #789. The change that starts the clock is #730 (`6522a174`, 2026-11-17T04:32:51Z,
`src/estate_clients/confined.py`). The following "Item 7" section has the options.

## What I built

Docs, one compose comment, and one test file. No production code changed, and no production act
ran.

### Item 1: the heartbeat key

The live key is `rotation-heartbeat:bws-secret`. It's built from the store's read-back kind, not
the config's `store` value:

- `src/rotation_worker/notify.py:67`: `return alert_key("rotation-heartbeat", store_kind)`
- `src/rotation_worker/cli.py:274`: `setup.notifier.heartbeat(setup.store.read_back_kind)`
- `src/rotation_worker/secret_store.py:89`: `read_back_kind = "bws-secret"` (BWS store); the
  Keychain store's is `"keychain"` (`secret_store.py:223`).

The Mac's log agrees for its own instance: its last pass logged
`"key": "rotation-heartbeat:keychain"` (read-only read of `~/Library/Logs/rotation-worker.log`).

Only `docs/operations/rotation-executor.md` line 497 named the wrong literal. I corrected it and
said why the key ends in `bws-secret`. ADR-0055 Amendment 29 item 4 step 9 and the runbook's alert
table say `rotation-heartbeat:<store kind>`, which is correct, so I left them. `deploy.md` doesn't
name the key. No file names a literal `rotation-instance-behind:bws` either. Under
`docs/superpowers/`, only `reports/lanes-inc-7-host-install-report.md` line 114 names
`rotation-heartbeat:bws`; it's a finished build's record, so I left it.

### Item 2: the install step as two calls

"Install the code" step 1 is now two `vps_exec` calls:

1. Create `/root/rotation-worker-provision` (mode `0700`) and `curl -sSfL` the script at the
   pinned 40-character commit to `provision-<commit>.sh.part`, then `mv` it to
   `provision-<commit>.sh`. The rename runs only when `curl` succeeds.
2. `flock -n /run/rotation-worker-provision.lock bash <that file> <commit>`, with the 300000 ms
   timeout.

The path is fixed and named by the commit because two calls share no shell state, so a `mktemp`
path from call 1 is gone in call 2. I read the gate's deny pattern in
`~/.claude/hooks/high-power-gate.sh`: it denies any `vps_exec` command matching `rm -[a-z]*f` or a
pipe into a shell. Neither call matches; cleanup is a plain `rm` (no `-f`), described as optional.
The runbook had no digest check, so none was added. The VM's install (line ~359) uses a stdin
redirect, not this shape, so it needed nothing.

### Item 3: the `pgrep -u rotation` check

"Cut over to the host" step 5 now runs:

```sh
systemctl is-active rotation-worker.service; pgrep -af '/opt/rotation-worker/venv/bin/[r]otation-worker'
```

and must print `inactive` and nothing else. `is-active` alone misses step 4's pass, which
`runuser` starts outside the unit, so the step pairs it with a `pgrep -f` on the executor's own
path. The bracket keeps `pgrep` from matching the `vps_exec` shell that carries the pattern. I
checked the pattern with `grep -E` against three command lines: the unit's `ExecStart`, step 4's
`runuser` line, and the calling shell. It matched the first two and not the third. It isn't run
on the host. The step says why `pgrep -u rotation` is wrong (uid 999 is shared with lane and
Postgres container processes). `provision.sh` is unchanged; the uid overlap is a follow-up in the
PR body.

### Item 4: `read-consumers` printing nothing

**Expected, not a bug.** `read_consumers` (`src/rotation_worker/cli.py:193`) prints one line per
entry of `scheduled_reads(entry.credential)` and exits 2 only if one is unreadable.
`scheduled_reads` (`src/rotation_worker/readers.py:123`) returns a read only for a `runtime-fetch`
consumer that names `healthchecks_check` or `observation_source` with `observation_subject`, or
for a `gh-actions-secret` copy. The `execution-watch-bws-token` row in `.cred-consumers.toml` has
two consumers, a `bws-secret` keeper and a `coolify-env` variable, and neither names a read. So
the map is empty, the loop prints nothing, and rc 0 means "every scheduled reader answered" over
zero readers. That row's lane is proved by the helper's `loaded` fingerprint (`helper_target =
"execution-watch"`), which `read-consumers` doesn't read. Making it read that would be a design
choice the handoff doesn't name, so I didn't. The runbook now says what an empty answer with exit
0 means, naming this row. I checked all 12 `bws-lane-token` rows that name `helper_target`:
none has a scheduled read, so the sentence holds for each.

### Item 5: the change-window comment

`lanes/change-window/compose.yaml` lines 8-10 now say each pass runs the infraops bundle the image
bakes at a pinned commit (ADR-0059 Amendment 18) and writes the report to a pass directory it
removes (`scripts/run-change-window.sh` lines 106-110). "No state, no volume" stays true. No test
reads the comment.

### Item 6: #797's tautological tests

`_never_opened` raised `AssertionError`, which `emit_secret`'s `except Exception` (`cli.py:435`)
turned into exit 2, so the tests passed with their guard gone. I replaced it with an `opened`
fixture that records each call and returns a working `MemoryStore`. The terminal, malformed-id
and Keychain tests now assert `opened == []` and the refusal's exact stderr line (#797 follow-up 1
asked for each message).

Mutants, each run against the new tests and, as a control, against `origin/main`'s copy of the
file. Source was restored by copying a saved file back, with `PYTHONDONTWRITEBYTECODE=1`,
`python -B` and existing `.pyc` files removed; `git diff --quiet src/` held after each.

| Mutant | New tests | Old tests (control) |
|---|---|---|
| Delete the `UUID_PATTERN.fullmatch` guard in `emit_secret` | 4 failed | 4 passed |
| Set `config.UUID_PATTERN` to `.*` | 4 failed | 4 passed |
| Delete the `config.store != "bws"` guard | 1 failed | 1 passed |
| Delete the `out.isatty()` guard | 1 failed | 1 failed |

The terminal test already caught its mutant through its `"pipe"` check, as #797 said; it now
also asserts `opened == []`.

#797's other follow-ups:

- **7, done (doc wording):** ADR-0055 Amendment 29 item 4 step 2 said `emit-secret` logs in "as
  every other verb does". `compare_destinations` (`cli.py:103`) reads only the git mirror and
  doesn't log in, so the sentence now says "as every verb that reads the store does".
- **2, 3, 5 and 9, not done:** they aren't wording fixes in the files this handoff names. 2 and 9
  are about #797's report, 3 needs a test of behaviour, and 5 is a guard design choice.
- **4 and 6, not done:** each is wording in a runbook section no item names, and the handoff bars
  editing other sections.
- **8, not done:** the fix is a docstring in `src/rotation_worker/logs.py`, which is under
  `BEHIND_PATHS`. Editing it would start the behind clock on every instance for a comment.

### Item 7: the Mac Keychain instance and `rotation-instance-behind`

Investigated only; no code or install changed.

**What raises it.** `worker.behind` (`src/rotation_worker/worker.py:284`), run by every pass of
every instance. It syncs the orchestrator mirror and asks `RepoMirror.oldest_change_since`
(`git_mirror.py:96`) for the committer time of the oldest commit on `main` after the instance's
`source_revision` that touches `BEHIND_PATHS` (`src/rotation_worker`, `src/estate_clients`,
`src/alert_hub_client`; `worker.py:132`). It raises `rotation-instance-behind:<store kind>` when
that commit is at least `ROTATION_BEHIND_ALERT_DAYS` (7) days old. An unreadable mirror holds the
key, never raises it.

**When.** I read the Mac instance's `source_revision` from
`~/Library/Application Support/rotation-worker/config.toml` (that line only; the file holds ids
and names, no value). It's `06cfea7c4adc` (2026-11-13). The first later commit touching
`BEHIND_PATHS` is `6522a174` (#730, 2026-11-17T04:32:51Z, `src/estate_clients/confined.py`), so
the Mac instance raises `rotation-instance-behind:keychain` on its first pass at or after
**2026-11-24T04:32:51Z**. Its launchd job runs every 300 seconds and is loaded (`launchctl list`
shows `com.devon.rotation-worker`, last exit 0; last pass 2026-11-23T14:45:59Z). #783, #789, #791
and #797 follow, but the oldest change sets the clock. The host instance, installed at the
cut-over, raises nothing until a change lands after its revision.

**Why the docs disagree.** Amendment 29 item 4 step 8 ("the Mac's Keychain instance is
unchanged") is about its role and config at the cut-over, not its code. `deploy.md` "Rotation
instances" (line ~1529) still says to reinstall both instances at any change to those paths, and
the alert enforces that.

**Options.**

1. **Reinstall the Mac instance at `main`** with `scripts/install-rotation-worker-launchd.sh`, as
   `deploy.md` says. That clears the alert and keeps the rule as written. It's a deploy on the
   operator Mac, so it's HQ's act, not this PR's. I recommend this one, before
   2026-11-24T04:32Z.
2. **Accept the alert** until a Keychain-relevant change is installed. It stays open the whole
   time, which teaches people to ignore it.
3. **Narrow what counts per instance** (for example, a per-store `BEHIND_PATHS`, or ignoring
   commits that touch no code the Keychain instance runs). That's a code and design change to the
   alert, which the handoff rules out for this PR; it would need its own decision, and
   `estate_clients` and `alert_hub_client` are shared by both instances, so the narrowing isn't
   obvious.
4. **Retire the Keychain instance** if no Keychain-stored credential still needs it. That's a
   design decision outside this handoff.

## Tests and checks

- `make check` with my own database `orchestrator_test_hq_w_282` and the worktree's own venv:
  **rc 0, 14768 passed, 1 skipped** (5 min 57 s). Lint, format, type check and the whole-repo
  scans passed. A rerun at `fbf9bd4a`, after the ADR and wording edits, also gave rc 0, 14768 passed, 1 skipped (6 min 20 s).
- CI on PR #798 at `ca477b2e` (the commit before this line was added): all three jobs pass.
  `Quality` collected and ran 14755 passed, 14 skipped (run 45204677270). CI skips 13 more
  than the local run, which I didn't investigate.
- `uv run pytest tests/rotation_worker/test_emit_secret.py`: 16 passed before the mutants.
- The four mutants in the preceding "Item 6" table.
- `code-standards risk --repo .`: **high-risk**, rule `secrets`, matching
  `tests/rotation_worker/test_emit_secret.py` (the file tests the secret-emitting verb; it holds
  only the placeholder value `placeholder-value-1`).
- `/code-review` (medium): three nits.
  - Fixed: the `pgrep` pattern's trailing space; it now matches any argument list.
  - Fixed: the step now says a failed download's `.part` file is overwritten on the next try.
  - Not changed: the reviewer suggested looser stderr assertions. #797 follow-up 1 asked for each
    refusal's message, and the exact message is what tells the three refusals apart.
- `vendor-review` (risk verdict high-risk): review complete, 2 of 2 seats succeeded (Claude and Codex, both
  `approve`, 0 findings, 0 dropped); agy: off by configuration. Run over the two code commits
  before this report was added.

## Files changed

- `docs/operations/rotation-executor.md`: items 1, 2, 3 and 4, only in "Install the code",
  "Write the config", "Cut over to the host" step 5, and the `read-consumers` paragraph.
- `lanes/change-window/compose.yaml`: item 5, comment only.
- `tests/rotation_worker/test_emit_secret.py`: item 6.
- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: #797 follow-up 7,
  one sentence.
- `docs/superpowers/reports/lanes-inc-7-cut-over-follow-ups-report.md`: this report.

## Self-review

- Every runbook edit is inside a section an item names. The heartbeat table and the ADR stay
  unedited because they're correct.
- The two-call install and the step 5 check are untested on the host; the runbook already says
  the host procedure is unproven until the cut-over runs it.
- The tests assert behaviour through `emit_secret`'s public signature; the one entry-point test is
  unchanged.

## Concerns

- Item 7's alert fires about 2026-11-24T04:33Z unless the Mac instance is reinstalled first.
- The host `rotation` user's uid (999) overlaps lane and Postgres container processes. Any check
  or limit keyed on that uid on the host sees them too. It's recorded as a follow-up; `provision.sh`
  is unchanged because a live host depends on it.
- The handoff's standards don't cover runbook commands that run on a production host through the
  high-power gate. I wrote the commands against the gate's own pattern.

## Standards followed

- **Code Brain 3 (structured logs to stdout, not files):** no logging added or changed. The tests
  read the verb's stderr messages, which already exist.
- **Code Brain 4 (never silently swallow an exception):** no exception handling added. Item 6
  removes a test helper whose exception was swallowed by `except Exception`, which hid what the
  tests claimed to check. The handler itself is unchanged and logs each error's class.
- **Code Brain 5 (never log secrets):** no new message. The tests still assert no message carries
  the placeholder value, and the new stderr assertions are whole-line equalities that hold none.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files, history or inline):** the diff holds
  no token, no token-shaped string and no access-token assignment. The fixtures use
  `placeholder-value-1` and fixed UUIDs, which are ids, not tokens. I read no secret.
- **Infra Brain 1032 (`cred.exposure-rotate`):** nothing in this PR declares or resolves an
  exposure. No value was surfaced.
- **Infra Brain 212 (HTTPS):** not applicable; no public endpoint changed.
