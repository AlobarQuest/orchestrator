Outcome: done

# Report: `emit-secret` and increment 7 doc follow-ups

Branch `hq/emit-secret-follow-ups`, base `62193f5d`. PR: #801. Nothing was deployed,
nothing ran on the VPS or on the `rotation-executor` VM, and no secret was read.

## What I built, item by item

1. **Empty value (#797 item 3).** The premise doesn't hold at `62193f5d`: no store can hand
   `emit-secret` an empty value. `Value.__init__` raises `ValueError` on empty text, and
   `BwsStore.read` builds `Value(secret.value)`, so an empty secret already exits 2 with
   `emit-secret: <id> unreadable: ValueError` and writes nothing to the pipe. I added no verb-level
   guard: it would be unreachable code. I added the test the item asks for,
   `test_an_empty_value_or_one_with_surrounding_whitespace_is_refused_as_unreadable[]`, whose stub
   store wraps the stored text as `BwsStore.read` does. It asserts exit 2, an empty pipe, and the
   exact stderr line (no value). It passed on first run, so there was no red phase; the mutant is
   the proof (see "Mutants").
2. **Surrounding whitespace (#797 item 4).** The refusal is kept. The same parametrized test pins
   it for a leading space, a trailing space, and a trailing newline (the existing inner-whitespace
   test didn't reach these: `Value` refuses them before the verb's own check). Runbook step 1 of
   "Put the root token in its file" says to store the value with no trailing newline and no
   leading or trailing spaces, and what `emit-secret` does otherwise.
3. **Pipe guard (#797 item 5).** `emit_secret` now refuses unless `os.fstat(out.fileno())` is a
   FIFO or a socket (`_is_pipe_or_socket` in `src/rotation_worker/cli.py`). It reads the mode
   from `out.fileno()`, not literal fd 1. A stream with no descriptor, or a closed one, is
   refused with the same message: `emit-secret: writes only to a pipe or a socket, not a terminal
   or a file`. Tests were written first and failed (3 failed) before the guard. The test fakes
   now carry real descriptors: `os.pipe()`, `socket.socketpair()`, `os.openpty()`, and a
   `tmp_path` file.

   **The live route.** On the runbook's route, `emit-secret` runs under `orb`, not ssh: `orb -m
   rotation-executor ... emit-secret ... | ssh ... set-root-token --stdin`. ssh only carries the
   pipe's other end. I probed what `orb` hands a VM process as stdout, on the `ubuntu` OrbStack
   machine (not `rotation-executor`), with the same `-u root runuser -u <user> -- env -i` shape and
   a one-line `os.fstat(1)` print:

   | Mac-side stdout | What the VM process saw |
   |---|---|
   | piped to `cat` | FIFO |
   | redirected to a file | FIFO |
   | captured by the tool, no redirect | FIFO |
   | redirected to a file inside the VM | regular file |

   So the live route passes the new guard (I relied on `orb` relaying stdout as a FIFO, measured
   above; the `ubuntu` and `rotation-executor` machines run the same OrbStack and Ubuntu noble).
   The probe also shows the guard can't see a Mac-side redirect or a tool's capture: `orb`
   hands a FIFO in all three Mac-side cases measured. A Mac terminal wasn't measured. It catches a terminal and a redirect inside the VM. The runbook's "never
   run the `emit-secret` half alone in a session" warning stays; I rewrote its reason, which
   said the refusal didn't apply because "a tool's captured stdout isn't a terminal". The verb
   is kept.
4. **Hard-coded address (#797 item 6).** The command and the line after it name
   `<prod-vps address>`, the `prod-vps` role's public address read with Infra Brain's
   `get_environment("prod-vps")` at the run. Only those two lines changed.
5. **ADR wording (#797 item 7).** Already fixed on `main` by #798 (`f1728ce7`, ADR-0055 line
   4232: "as every verb that reads the store does (`compare-destinations` reads only git and
   doesn't log in)"). I left the ADR alone.
6. **Log stream coupling (#797 item 8).** `src/rotation_worker/logs.py`'s docstring says the
   lines go to stderr, and why: stdout is `emit-secret`'s value channel, and the systemd unit sets
   neither stream, so the journal takes both. `configure()` passes `sys.stderr` explicitly instead
   of relying on `StreamHandler`'s default. A new test,
   `test_logs_go_to_stderr_so_stdout_stays_the_value_channel`, pins it: stdout empty, the line on
   stderr. It passed on first run (behavior already existed); the mutant is the proof.
7. **Host `rotation` uid 999 (investigated, nothing changed).** See the next section.

## Item 7: the host `rotation` uid overlap

Nothing here was observed on the host. Each statement is read from this repository, except
where it's marked as general knowledge.

- **Where the uid is chosen.** Nowhere. `scripts/rotation-worker-vm/provision.sh` line 49 runs
  `useradd --system --home-dir "$state" --shell /usr/sbin/nologin rotation` only when the user
  doesn't exist, with no `--uid`. The overlap itself is recorded in the runbook (step 5 of "Cut
  over to the host", line 630).
- **Why 999 (general knowledge, not from the repo).** On Debian and Ubuntu, `useradd --system`
  takes the highest free uid in `SYS_UID_MIN..SYS_UID_MAX` (100 to 999), so the first system user
  on a host gets 999. The official Postgres image's `postgres` user is uid 999. The lane image's
  `orchestrator` user is created the same way (`Dockerfile` line 38, `groupadd --system` and
  `useradd --system`, inherited by `lanes/Dockerfile`), so on a Debian base it too is likely 999.
  Container processes run under the host's uid numbering, so `ps` on the host names them
  `rotation`. The overlap is a shared number, not a shared identity.
- **What `provision.sh` checks of an existing user** (lines 51 to 67): not uid 0, in its own
  group only, shell `/usr/sbin/nologin`. It never changes an existing user, so a uid change
  isn't something a re-run of `provision.sh` does.
- **What the uid touches on a host that already has the user:**
  - Files owned by `rotation`: `/var/lib/rotation-worker` (0700, `provision.sh` line 69) and
    everything under it (the three state files and anything else the executor writes there),
    and `/etc/rotation-worker/root-token` (`set-root-token` chowns it `rotation:rotation`, mode
    0400, at each write). The group `rotation` owns `/etc/rotation-worker` (root:rotation 0750,
    `provision.sh` line 70) and `config.toml` (root:rotation 0640, runbook line 503). Ownership is
    stored by number. `usermod -u` re-owns files under the user's home directory
    (`/var/lib/rotation-worker`) and nothing else (general knowledge, `usermod(8)`), so
    `root-token`'s owner and the two group-owned paths need a manual `chown`/`chgrp`.
  - The systemd unit (`rotation-worker.service`) names `User=rotation` and `Group=rotation` by
    name, so it needs no edit; the next start picks up the new uid.
  - `/usr/local/sbin/sds-pg-rotate` has no check on the `rotation` user or its uid. It runs as
    root through `authorized_keys`' forced command; its one ownership check is that
    `/etc/sds-pg-rotate/targets.toml` is owned by uid 0 (`deploy/vps/sds-pg-rotate` line 90).
    A uid change doesn't affect it.
  - `set-root-token` sets the root token file's owner by name, so a later write uses the new uid.
  - Anything keyed on the number: the runbook's warning against `pgrep -u rotation` (step 5 of
    "Cut over to the host").
- **Options:**
  1. Leave it. The overlap affects only checks that select by uid; the runbook already steers
     the one such check (`pgrep`) to a path match. Cost: none. Risk: a later check or limit
     keyed on the uid sees container processes.
  2. Pick a fixed uid outside the range images use, in `provision.sh` for new hosts
     (`useradd --system --uid <n>`, with `<n>` chosen below the image range, and a refusal if
     taken), and change the existing host by hand: stop and disable the timer, confirm no pass
     runs, `usermod -u <n> rotation` and `groupmod -g <n> rotation`, then `chown`/`chgrp` the
     paths outside the home directory listed above, check with `stat`, re-enable the timer. Cost: a provisioning change plus a one-time
     host act in a quiet window. `provision.sh`'s "never changes an existing user" rule stays.
  3. Have `provision.sh` refuse a `rotation` uid that a running container also uses. That turns
     the overlap into a refusal on every run on the existing host, so it needs option 2 first.

  I'd choose option 1 unless a uid-keyed control is planned, then option 2. The choice is
  HQ's; nothing here changed `provision.sh`.

## Mutants

Each mutant was applied, `__pycache__` cleared, the two test files run, then restored.

| Mutant | Tests that failed |
|---|---|
| M1 `values.py`: drop `not text or` from `Value`'s guard (item 1) | `...surrounding_whitespace_is_refused_as_unreadable[]` (`assert 0 == 2`: the verb succeeded on the empty value), plus 9 other tests in `test_config_logs_store.py` |
| M2 `values.py`: drop `text != text.strip()` (item 2) | the three surrounding-whitespace cases, plus `test_a_private_key_is_read_without_its_trailing_newline` |
| M3 `cli.py`: guard back to `out.isatty()` (item 3) | terminal, regular-file, and no-descriptor refusals |
| M4 `cli.py`: accept FIFO only | `test_a_socket_is_written_to` |
| M5 `cli.py`: accept every kind | terminal and regular-file refusals |
| M6 `logs.py`: handler on `sys.stdout` (item 6) | `test_logs_go_to_stderr_so_stdout_stays_the_value_channel` |

M6's first attempt raised `NameError` (`logs.py` didn't import `sys` then), so it proved nothing;
I reran it with a valid import, and again after `sys.stderr` was made explicit.

## Tests and checks

- `make check` (database `orchestrator_test_hq_w_285`, `SECURITY_STANDARDS_DIR` at the fixture,
  `PGTZ=UTC`): exit 0, **14,776 passed, 1 skipped**, at commit `2985ecd4`. The first full run
  found one failure in my new log test: Alembic's `fileConfig` disables loggers created before it
  runs, so in an xdist worker that ran a migration first, the `rotation_worker` logger was
  disabled. Confirmed in a one-off script (`named disabled: True root disabled: False`); the test
  now logs through the root logger. The run after that fix is the green one.
- At the final code commit `948a956f` (explicit `sys.stderr`): `make check` exit 0,
  **14,776 passed, 1 skipped**.
- `code-standards risk --repo .`: `high-risk`, rule `secrets: tests/rotation_worker/test_emit_secret.py`
  (the path).
- `/code-review` (medium) at `2985ecd4`: four nits, results under "Self-review". The vendor
  review covered `948a956f`. Neither covered the last runbook wording edit (the "never run the
  half alone" reason now names the three measured cases) or this report.
- Vendor review (`/tmp/vendor-review-dh_o4196`, diff sha256 `d6b4caf0…`): complete, 2 of 2
  seats ran; claude approve, codex approve, 0 findings kept, 0 dropped. agy: off by
  configuration. With no findings, there was nothing to reproduce.
- CI on head `dfd74f4a` (run 45230925041): all three jobs pass. `Quality` collected and
  passed 14,763 tests, 14 skipped. The commit that adds this line changes only this report.

## Files changed

- `src/rotation_worker/cli.py`: `_is_pipe_or_socket`, the guard, its message, and the module and
  function docstrings.
- `src/rotation_worker/logs.py`: docstring; `StreamHandler(sys.stderr)`.
- `tests/rotation_worker/test_emit_secret.py`: real-descriptor fakes; socket, terminal, file,
  no-descriptor, and empty or padded value tests.
- `tests/rotation_worker/test_config_logs_store.py`: the stderr pin.
- `docs/operations/rotation-executor.md`: "Put the root token in its file", steps 1 and 3 only.

## Self-review

`/code-review` findings and what I did:

1. `StreamHandler()` relies on the default stream: taken, `sys.stderr` is explicit.
2. The guard can't tell the host's pipe from a tool's capture: true, measured, and the runbook
   says so; the docstring and message don't claim transcript protection. No change.
3. `Captured` writes to a `StringIO`, not its descriptor: the real-descriptor write is covered by
   `test_a_real_closed_pipe_exits_2_with_one_message`, a subprocess on a real pipe. No change.
4. The `<prod-vps address>` placeholder in a pasted command: the same command already holds
   `<the host token's secret id>`, so it's edited before use either way. No change.

## Concerns

- The guard narrows `emit-secret` to pipes and sockets, but under `orb` that only refuses a
  terminal or an in-VM redirect. The runbook's warning, not the code, is what keeps a value out
  of a transcript.
- Outside the places the handoff names, `rotation-executor.md` still hard-codes the tailnet
  address `100.92.138.19` (lines 347, 379, 508, 583, 587), and ADR-0055 line 4233 still says the
  verb is "refusing a terminal". I left both, per the stop points.
- Item 1's handoff premise (a bare newline) was false at the base; recorded rather than adding
  unreachable code.

## Standards followed

- **Construction-mode ruling (context).** Followed the coding rules and project standards only;
  no extra machinery. Nothing in the diff bears on it beyond that.
- **Code Brain 3 (logging: structured JSON to stdout).** The executor's JSON lines go to stderr on
  purpose, and the diff writes that down instead of changing it: stdout is `emit-secret`'s value
  channel, and any log line there would reach `set-root-token --stdin` as the value. Under
  systemd the unit sets neither stream, so both land in the journal and the lines are collected
  exactly as stdout lines would be. A test pins the stream.
- **Code Brain 4 (no silent swallow).** `_is_pipe_or_socket` catches `OSError` and `ValueError`
  from `fileno()` and `fstat` with an inline reason, and the caller refuses with a stderr
  message and exit 2; nothing is swallowed silently. The existing `except Exception` in the verb
  is unchanged.
- **Code Brain 5 (never log secrets).** No message names a value: the new refusal is a fixed
  string, and the empty or padded value case asserts the exact stderr line (id and class only).
  No test asserts on a value in stderr; test values are `placeholder-value-1` and variants.
- **Infra Brain 591, 592, 593 (no BWS token in files, history, or inline).** No token, fixture
  token, or token-shaped string anywhere in the diff.
- **Infra Brain 1032 (`cred.exposure-rotate`).** Nothing in this PR declares or resolves an
  exposure.
- **Infra Brain 212 (HTTPS).** Doesn't apply: no endpoint changed.
- **Workspace host-move rule.** The runbook names the `prod-vps` role and Infra Brain's
  `get_environment("prod-vps")` instead of the address, at the place item 4 names.
