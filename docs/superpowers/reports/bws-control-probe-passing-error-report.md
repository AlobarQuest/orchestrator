Outcome: done

# Report: the BWS control probe's passing-error handling (ADR-0055 amendment 37)

- **PR:** https://github.com/AlobarQuest/orchestrator/pull/831, branch
  `hq/bws-control-probe-passing-error`, based on `0b7b9e23`. Open, not merged: HQ merges.
- **Head before this report's commit:** `4b4dfded68dcd4faba2c4a2a753d40f964b6fc18`. The report's
  own commit follows it on the same branch.
- No live call to BWS or any other service was made. No production act was taken. The fix
  reaches the VPS host only at the next reinstall of the host executor.

## What I built

All code is in `src/rotation_worker/adapters/bws_machine_token.py`. `contract.py`,
`.cred-consumers.toml`, and the other adapters are unchanged.

1. **A scrubbed answer category, logged.**
   - The child (`_LOGIN`, line 99) prints exactly one of the following answers:
     - `live`
     - `refused`: both `invalid_client` and `400` are in the message. This test is unchanged
       and is checked first.
     - `status:<code>`: the regex `\[([1-5][0-9][0-9]) [A-Za-z]` matches the SDK's
       `[400 Bad Request]` shape. Only the three digits are printed.
     - `error:<exception class name>`
   - The child never prints `str(error)` or any part of the message.
   - The parent accepts only `ANSWER` (line 120), applied with `fullmatch`, ASCII:
     `live|refused|status:[1-5][0-9]{2}|error:[A-Za-z_][A-Za-z0-9_]{0,63}`.
   - The parent adds `unparsed` for output outside that set, logged by length only, plus
     `timeout` and `empty`. `_login` is at line 217.
2. **The return value is unchanged.** `probe` (line 207) returns `_ANSWERS.get(answer)`: 200 for
   `live`, 400 for `refused`, and `None` for anything else. `_ANSWERS` and `dead_statuses` are
   unchanged.
3. **One bounded retry.**
   - When the first answer isn't in `_ANSWERS`, `probe` waits `RETRY_PAUSE` (5.0 seconds, line
     65) and calls `_login` once more. The second answer is the one `probe` returns.
   - `live` and `refused` are never retried, and there's no third attempt.
4. **`_fresh`'s other callers behave the same.**
   - `_fresh` (line 286) now wraps a lower `_run` (line 292) that returns the finished child or
     `None` on timeout.
   - `_fresh` still returns `stdout.strip()` or `None`.
   - `reach`, `reach_sets`, and `same_identity` still call `_fresh`, with no retry and no log
     line.
5. **The ADR line.** ADR-0055's `Amended:` list gains a 2026-11-27 amendment 37 entry (line 54).
   It records the scrubbed category set, the single retry after a 5-second pause, its source
   (unit `6f2b6610`'s 22:09 UTC confirm failure and G-114's diagnostic), and that `probe` still
   returns 200, 400, or nothing. Nothing else in the ADR changed.
   - Placement: amendments 33 to 36 have body sections but no `Amended:` entries, so the list
     jumps from 32 to 37. The handoff asked for one line and no other ADR change, so I added no
     body section and left the missing entries alone.
6. **Runbook.** `docs/operations/rotation-executor.md` gains a "Read a failed BWS probe" section
   (line 162), after the alerts table that defines `rotation-unit-failed`. That row's action cell
   points to the section. It covers the sample
   line, each field, each answer, the retry, and what to do for each pair of answers. For
   example, it says to wait out a double `status:429` before requeuing.

### Control or subject

`probe(value)` isn't told its role, and `contract.py` was off limits. Instead, `known_bad()`
adds each control it issues to `self._controls` (line 204), and `probe` labels a value `control`
when it's in that set (line 210). The set holds no fingerprint for logging. It exists only in
memory, for the label. Every caller of the BWS provider uses one instance for both calls:
`contract.Run.probe`, `accepts`, `revokes.py`, and `bws_lane_token`'s `_login` delegate.

My first version kept only the last control. The vendor review found that a second
`known_bad()` before the first control's probe mislabels the first control as `subject`, and a
repro test confirmed it. The fix is the set, plus
`test_every_control_issued_is_logged_as_the_control`.

### The log line

The executor's logger, `logging.getLogger("rotation_worker")`, writes one line per attempt
through `logs.JsonFormatter`, which renders every field as a string:

```json
{"time": "…", "level": "warning", "event": "bws_login", "role": "control", "attempt": "1", "returncode": "0", "answer": "status:429", "elapsed_seconds": "0.41"}
```

- `role` is `control` or `subject`, and `attempt` is 1 or 2.
- `answer` is the category.
- `returncode` is present whenever the child finished, and absent on `timeout`.
- `stdout_length` is present only for `unparsed`.
- `elapsed_seconds` is rounded to two decimals.
- The level is `info` for `live` and `refused`, and `warning` for every other answer.
- Stderr content is never read into the log.

## Tests and checks

- `make check`, run on `orchestrator_test_hq_w_334`: ruff and pyright clean (`0 errors, 0
  warnings, 0 informations`); **15,185 passed, 1 skipped** in 315.7 s.
- The following tests are new in `tests/rotation_worker/test_bws_machine_token.py`:
  - `test_the_login_child_prints_only_its_category` runs `_LOGIN`'s own text in-process
    against a fake `bitwarden_sdk`. It covers eight SDK answer shapes: live, refused,
    `status:400` with `invalid_grant`, 429, 503, `invalid_client` without a 400, and two class
    names. Each message carries a run-time `known_bad()` token and SDK text, and the test
    asserts that neither reaches stdout or stderr.
  - `test_an_indeterminate_login_is_tried_once_more_and_its_second_answer_stands` crosses eight
    indeterminate first answers (timeout, empty, empty with code 1, 429, 400, class name, other,
    multi-line) with three second answers. It checks for exactly two calls, one pause of
    `RETRY_PAUSE`, two log lines, and that the second answer is what `probe` returns.
  - `test_a_determinate_login_is_never_retried` covers `live` and `refused`.
  - `test_never_more_than_one_retry`
  - `test_the_log_line_names_the_subject_and_never_a_value_or_the_sdk_text` checks roles,
    levels, `stdout_length`, `returncode`, and its absence on timeout. It asserts that no
    value, fingerprint prefix, or SDK text is in the raw records or in the rendered
    `JsonFormatter` output.
  - `test_the_other_fresh_logins_keep_their_answers_with_no_retry_or_log` covers `reach`,
    `reach_sets`, and `same_identity`, each answering, unreadable, and timing out. It checks
    one call per child, no pause, and no log records, then proves that the capture can see a
    login line.
  - `test_every_control_issued_is_logged_as_the_control`
- An autouse fixture patches the pause and re-enables the `rotation_worker` logger. Alembic's
  `fileConfig` disables it on a shared xdist worker. Without the fixture, the first full run
  had 27 failures that didn't reproduce in isolation, and the "no log" assertion passed only
  because the logger was disabled.
- `tests/architecture/test_rotation_edges.py`: `_LOGIN`'s expected child imports gain stdlib
  `re`. This is the one test outside the named files that needed a change. It is a mechanical
  update, not a design choice.
- Mutations, each run on the final code and restored, with `__pycache__` cleared:

  | Mutant | Result |
  |---|---|
  | M1: remove the retry | 26 failed |
  | M2: a `live` control retries (`!= "refused"`) | 2 failed |
  | M3: drop the `400` half of the `refused` test | 1 failed (the child test) |
  | M4: a second retry | 10 failed |
  | M5: log the unparsed stdout | 1 failed |
  | M6: the child prints the SDK message | 1 failed |
  | M7: every login labeled `subject` | 28 failed |

  No mutant survived.
- `/code-review` (medium) returned five nits:
  - The runbook's JSON sample showed numbers, but the fields are strings. Fixed.
  - "A real refusal" was wrong wording, and `status:400` wasn't defined. Fixed.
  - The role labeling relied on the last minted control. Fixed after the vendor review.
  - Worst-case latency. This is addressed under "Concerns".
  - The ADR has no amendment body. Not changed, because the handoff asked for one line only.
- `code-standards risk` returned `high-risk` (auth: the adapter and its test), so I ran
  `vendor-review` with Claude and Codex; agy was off by configuration. Both seats ran and
  approved. One finding was kept (Claude's role label), reproduced in
  `/tmp/vendor-review-_x9cd2wd/repro/test_role_label.py`, which failed with
  `['subject'] == ['control']`, and fixed. The repro now passes. No findings were dropped.
- **Review scope.** `/code-review` and the vendor review covered the diff at `a1818a2e`, before
  the control-set fix (`4b4dfded`). That fix is covered by its new test, the M7 mutant, the
  vendor finding's repro, and the final `make check`.
- **No test double bypasses the new path.** One test patches `_fresh`
  (`test_lane_token_project_reach.py:186`), and only for `reach`, which still calls `_fresh`.
  A `--durations` run of `tests/rotation_worker` found no test near the 5-second pause.
- **CI** at `965cd472`: `Quality` passed in 10m49s, with 15,172 passed and 14 skipped (more
  skips than locally, from environment-guarded tests). `Rollout transcription currency` and
  `Runner consumer compatibility` passed too.
- **Token-shape grep of the diff** (`git diff origin/main...HEAD`, added lines): zero UUIDs and
  zero `0.`-prefixed token shapes. The new tests build their values at run time with
  `known_bad()`.
- **Security scanner** (`security_scan.cli . --category security`, from the repo root): 0 BLOCK
  findings.

## Files changed

- `src/rotation_worker/adapters/bws_machine_token.py`
- `tests/rotation_worker/test_bws_machine_token.py`
- `tests/architecture/test_rotation_edges.py`: one expected-import set.
- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: one list entry.
- `docs/operations/rotation-executor.md`
- `docs/superpowers/reports/bws-control-probe-passing-error-report.md`

## Self-review

- Each status in the 22:09 failure's error now has a name in the log. The control's fresh login
  is tried once more, so one passing error no longer fails the unit by itself.
- For a lane token, `bws_lane_token` delegates to this provider, so its probes gain the same log
  line and retry. That adapter's file is unchanged.
- `revokes.py` probes a known-bad value before each claim, through the same provider. An
  indeterminate answer there now costs one more login per pass, still bounded at one.
- Before this change, a child whose stdout had extra lines was already indeterminate. It stays
  indeterminate, now as `unparsed` with its length, and gets the one retry.

## Concerns

- **Worst-case latency.**
  - One `probe` call can take up to 125 s: two 60 s timeouts and the 5 s pause.
  - The heaviest BWS step, `confirm`, makes four probe calls, up to about 8.3 minutes when
    every login times out.
  - The lease is renewed before each step, and `DEFAULT_LEASE` is 15 minutes
    (`orchestrator/kernel/leases.py:24`), so the worst case fits. The margin is smaller than
    before.
- **ADR placement.** The `Amended:` list jumps from 32 to 37 (see "What I built", item 5). HQ
  might want to backfill the entries for 33 to 36.
- **The 429 guidance is inferred.** The runbook reads a double `status:429` as a rate limit
  from HTTP's meaning of 429. I haven't seen the SDK report one, and the runbook says so.
- **The retry's origin.** The retry absorbs a single passing error. A repeated indeterminate
  answer, such as a rate limit, still fails the unit as before. The log now names that answer.
  I didn't measure the cause of the 22:09 answer; the next failure's log line will show it.

## Standards followed

- **Code Brain 3 (logging):**
  - The change uses the executor's own logger and `logs.JsonFormatter`: structured JSON lines,
    one per login attempt. It adds no file logging.
  - The lines go to stderr, not stdout. `logs.py` documents why: stdout is `emit-secret`'s value
    channel. This is the executor's existing choice, which I kept.
- **Code Brain 4 (error handling):**
  - The child's `except` prints its category, and nothing is swallowed.
  - The parent logs a timeout, empty output, and unparsed output, each with its category, and
    returns them as indeterminate.
  - The change adds no bare `except …: pass`.
- **Code Brain 5 (security):**
  - No token, control value, fingerprint, or SDK message text appears in any output line, log
    line, exception message, or test snapshot.
  - The tests assert this against raw records and rendered lines, and mutants M5 and M6 prove
    the assertions.
  - `values.scrub` still runs over every rendered line.
- **Infra Brain 591, 592, 593 (BWS token rules):** the diff adds no token-shaped literal (grep:
  zero). The new tests build their values at run time with `known_bad()`. The scanner reports
  0 BLOCK findings.
- **Infra Brain 1032 (`cred.exposure-rotate`):** no registry change and no
  `[[credential.exposure]]` entry.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. The change adds no endpoint.
