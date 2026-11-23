Outcome: done

# Report: a non-terminal stdin mode for `set-root-token` (increment 7, item c3)

Branch `hq/lanes-inc-7-root-token-stdin`, base `769b02af`. Goal step 3i, plan amendment
3i-cut-over-plan item c3.

## What I built

`scripts/rotation-worker-vm/set-root-token.sh` has an explicit `--stdin` flag:

- With no argument, the script behaves as before. It refuses unless stdin is a terminal (exit 2),
  then reads at the hidden prompt, with the same messages and exit codes. The only change on this
  path is that the argument count is checked before the root check, and zero arguments pass that
  check.
- With `--stdin`, the script refuses a terminal on stdin (exit 2). Otherwise it reads one line,
  strips one trailing LF (`read`) and then one trailing CR, and refuses (exit 1, "the file is
  unchanged") in each of the following cases:
  - Empty input, or an empty first line.
  - Any further byte after the first line. This covers a second non-empty line, and also
    `value\n\n`: the handoff says to strip one trailing newline only, so a second newline counts
    as a second line.
  - A value longer than 4096 characters.
  - Whitespace anywhere in the value (`[[:space:]]`).
- An unknown argument, or more than one argument, exits 2. The message doesn't repeat the
  argument, so a token passed as an argument by mistake isn't printed.
- Validation runs before `mktemp`, so a refusal creates no partial file and leaves the target
  untouched. The write path is unchanged: `umask 077`, `mktemp` beside the target,
  `chown rotation:rotation`, `chmod 0400`, `mv -f`.
- The value lives only in an unexported shell variable, which is unset after the write. No
  message interpolates it.
- The header comment names both modes and the pipe form
  `<source> | ssh root@<host> /opt/rotation-worker/bin/set-root-token --stdin`.

The script runs on bash 3.2 (macOS) and bash 5: it uses `read -n 1`, not `read -N`. The new mode
is inert: nothing calls it yet.

## Test seam

I didn't add a test-only switch to the script. The test (`tests/scripts/test_set_root_token.py`)
copies the script into `tmp_path` and makes two exact substitutions. It first asserts that each
original line occurs exactly once, so a drift in the script fails the test loudly:

- `target=/etc/rotation-worker/root-token` becomes `target=<tmp>/etc/root-token`.
- `[[ $EUID -ne 0 ]]` becomes `[[ 0 -ne 0 ]]`.

`chown` and `stat` resolve to stubs on a `PATH` of `<stubs>:/usr/bin:/bin`. The `chown` stub logs
its argument, and the test asserts `rotation:rotation`. The `stat` stub stands in for GNU
`stat -c`, which macOS doesn't have. The real `umask`, `mktemp`, `chmod` and `mv` run, and the
test asserts mode `0400` from the file itself. Terminal cases use `os.openpty()`. For the prompt
case, the test types only after the terminal's `ECHO` flag clears, because `read -s` flushes
pending input when it turns echo off.

I picked a copy over an env-var override because it's the smaller change to a script that runs
as root: production code gets no path or `chown` override that a caller could set. CI needs no
root and no `rotation` user.

Coverage (20 tests):

- A pipe without the flag is refused.
- The hidden prompt still stores the value read from a terminal.
- `--stdin` refuses a terminal.
- An unknown argument, or two arguments, is refused, and the argument isn't echoed.
- One line ending in LF, CRLF or no newline is stored as `0400` holding the value plus one newline.
- Ten bad inputs each refuse, leave the existing file byte-identical, leave no partial file, and
  never call `chown`. The inputs are empty input, an LF-only line, a CRLF-only line, two lines,
  a trailing blank line, a leading blank line, an inner space, an inner tab, a leading space, and
  an over-length value, also with spaces.
- The longest accepted value (4096 characters) is stored.

Every run asserts that the value doesn't appear on stdout or stderr. All values are
placeholders such as `placeholder-value-1` or `x` repeated.

Mutation check: I made nine single mutations, one at a time, and restored the script after each
(`/usr/bin/diff` against the original showed it identical). The suite failed for every one:

- The whitespace check was removed.
- The multi-line check was removed.
- The terminal refusal in `--stdin` mode was removed.
- The CR strip was removed.
- The length check was removed.
- The empty check was removed.
- The value was added to the refusal message.
- The argument was echoed in the unknown-argument message.
- `chmod 0400` was changed to `0600`.

## Tests and checks run

- `make check` on the committed tree `64a34570`: rc 0, **14705 passed, 1 skipped**. That covered
  ruff, ruff format, pyright (0 errors) and shellcheck. The database was
  `orchestrator_test_hq_w_277`.
- After review, I moved the length check ahead of the whitespace check and added one test case
  (`9e455910`). Afterwards, `tests/scripts/test_set_root_token.py` passed (20), and shellcheck and
  ruff format were clean. I didn't run the full `make check` again for that two-line reorder.
- An earlier `make check` run had one failure: `test_layer_manifest`, because the new test file
  was untracked when the guard read `git ls-files`. Committing the file fixed it.
- Risk command (`code-standards risk --repo .`): **high-risk**, on the auth rule
  (`set-root-token.sh` and the test).
- Vendor review (`/tmp/vendor-review-00fif1_a`): **complete, 2 of 2 seats succeeded**. Claude
  and Codex each returned approve with 0 findings, and 0 were dropped. agy was off by
  configuration.
- `/code-review` (medium) returned three nits:
  1. The length was checked after whitespace, and only after the whole line was in memory. I
     **fixed** the order. The memory bound stays as it is, because the source is a session's
     pipe and not adversarial input.
  2. Control characters pass validation. I **left this as is**, because the handoff limits the
     shape check to "no whitespace, length within a bound".
  3. The prompt mode doesn't get the new checks. I **left this as is**, because the handoff
     requires the prompt mode to stay byte for byte as it was.

## Files changed

- `scripts/rotation-worker-vm/set-root-token.sh`: the `--stdin` mode and the header.
- `tests/scripts/test_set_root_token.py`: new.
- `layers.toml`: the new test file, placed in the `rotation` layer beside
  `test_provision_source_revision.py`.
- This report.

I didn't touch `provision.sh` or its source-revision markers. Those markers live in
`provision.sh`, not in this script, and `test_provision_source_revision.py` still passes.

## Self-review

- The change is the receiving end only. It makes no choice about where the value comes from
  (D-75).
- `IFS= read -r token || [[ -n "$token" ]] || refuse ...` swallows no error. A failed read with
  no data refuses with a message, and a final line without a newline is still judged by the
  checks that follow.
- One failure path predates this change and is unchanged: if `chown`, `chmod` or `mv` fails after
  `mktemp`, a root-owned `0600` partial holding the value stays beside the target, because the
  script sets no `trap`.

## Concerns

- No real credential appeared in any output, test, or transcript.
- The partial file described in the self-review is a possible follow-up: a `trap` that removes
  `$partial` on error. I left it out because it changes the existing write path, which the
  handoff said to keep.

## Follow-ups (runbook, not edited: W-276 owns it)

`docs/operations/rotation-executor.md` needs updating once the cut-over route is decided:

- Lines 509-513 say that `set-root-token` needs a terminal and that no route to the file is
  written yet. With `--stdin`, the script has a pipe mode. Only the source of the value (D-75)
  stays open.
- Lines 619-625 cover the Mac/VM terminal procedure. After the move, the VPS host procedure
  would be `<source> | ssh root@<host> /opt/rotation-worker/bin/set-root-token --stdin`.
- Line 672 covers rotating the root token through `set-root-token`. It may need the same pipe
  form.

## Standards followed

- **Code Brain 3 (structured logs):** this isn't a log system. As before, every message goes to
  stderr as one plain line, and the success line goes to stdout.
- **Code Brain 4 (never swallow an error):** `set -euo pipefail` stays, and the script has no
  `|| true`. Each failing read or check ends in an explicit `refuse` (exit 1) or exit 2 with a
  message.
- **Code Brain 5 (never log secrets):** no message interpolates the value or an argument. Tests
  assert the value is absent from stdout and stderr on every run, and two mutations prove those
  assertions fail the suite.
- **Infra Brain 591 and 592 (no BWS-token-shaped string in tracked files or history):** there's
  no shape pattern, only a whitespace check and a length bound. Test values are
  `placeholder-*` and `x` repeated.
- **Infra Brain 593 (no inline bootstrap token):** the change has no `BWS_ACCESS_TOKEN` anywhere.
- **Infra Brain 1032 (report exposures):** none occurred.
- **Infra Brain 212 (HTTPS):** not applicable.
- **Orchestrator `CLAUDE.md`:**
  - The new file has a `layers.toml` row.
  - `make check` passed on the committed tree.
  - The test database was this worker's own.
  - I didn't touch the main tree.
