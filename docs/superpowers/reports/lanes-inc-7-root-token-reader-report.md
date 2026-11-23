Outcome: done

# Report: the VM-side reader that feeds the root token to the host (increment 7, D-75 B)

Worker W-281. Branch `hq/lanes-inc-7-root-token-reader`, base `origin/main` `e6307caf`.

## What I built

- **The verb: `rotation-worker emit-secret <secret-id> [--config <path>]`** (`src/rotation_worker/cli.py`,
  `emit_secret`). It takes one BWS secret UUID. It logs in through `open_store`, which builds
  `BwsStore` with `read_root_token`: a fresh login with no state file, the same path as every
  other verb. It reads the secret with `BwsStore.read`, which already carries the transient
  429/5xx retry, and writes the value plus one newline to stdout. Nothing else goes to stdout:
  `logs.configure()` writes its JSON log lines to stderr. `BwsStore`'s surface is unchanged, and
  there is no new dependency and no second BWS client.
- **Refusals, each exit 2, before the store opens:** a terminal on stdout, a non-`bws` instance
  (the Keychain instance has no root), and an id that isn't a lowercase UUID.
- **Failures, each exit 2, after the store opens:**
  - A `ConfigError` (for example, the root token file's owner or mode) prints its message
    through `scrub`. That message names paths and ids only, and it's the one a person needs to
    fix the file.
  - Every other exception prints only `emit-secret: <id> unreadable: <class>`. The catch-all
    carries an inline reason (Code Brain rule 4): a value read in this process might stand in the
    message, and a traceback might show it.
  - A value holding whitespace or a control character is refused. `set-root-token --stdin` would
    refuse it anyway.
  - A failed write prints `<id> not written: <class>`, and the stdout descriptor is pointed at
    `/dev/null`. Without that, the interpreter's flush at exit failed again, and the process
    exited 120 instead of 2. `/code-review` found this, and a subprocess test with a real closed
    pipe holds it.
- **The exact invocation** is in `docs/operations/rotation-executor.md`, "Put the root token in
  its file", step 3. Run it from the Mac in a session:

  ```sh
  set -o pipefail
  orb -m rotation-executor -u root runuser -u rotation -- env -i PATH=/usr/bin:/bin \
    HOME=/var/lib/rotation-worker /opt/rotation-worker/venv/bin/rotation-worker emit-secret \
    <the host token's secret id> --config /etc/rotation-worker/config.toml \
    | ssh -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
      -i ~/.ssh/hetzner_infraops root@40.160.102.182 /opt/rotation-worker/bin/set-root-token --stdin
  ```

  I checked each path against `provision.sh` (`opt=/opt/rotation-worker`, the venv at
  `$opt/venv`, `set-root-token` installed at `$opt/bin/set-root-token`, the service user
  `rotation` with home `/var/lib/rotation-worker`, the config at `/etc/rotation-worker/config.toml`)
  and against the page's existing `runuser … env -i` forms.
- **Runbook** (`docs/operations/rotation-executor.md`):
  - "Put the root token in its file" replaces the stale "No route to the file is written yet"
    paragraph with D-75 B's route in four steps. Devon mints the second token on
    `rotation-executor`, named apart from the VM's (for example `rotation-executor-vps-host`), and
    stores it in `Rotation / Admin`. The VM is updated to a commit that has the verb (a separate
    act). The session runs the pipe. Then the `stat` check.
  - The step keeps the page's rules (no transcript, argument list, other file or log; no widened
    identity, project, grant or scope). It also warns never to run the `emit-secret` half alone in
    a session.
  - A non-zero pipe status is indeterminate, and a second run is safe because it writes the same
    value. The host's first pass proves the value. Codex raised this in the vendor review.
  - The cut-over order:
    - Step 1 names the pipe.
    - Step 6: Devon revokes the VM's original token by its name, in the console, only after every
      other item 5 check.
    - Step 7 (new): Devon deletes the BWS copy in `Rotation / Admin`.
    - The rollback precondition now reads true until step 6's revoke. The "if the token was
      moved" clauses are gone.
  - I didn't touch #793's sections, near lines 1304 and 1588.
- **ADR-0055**: amendment 29 item 4 step 2 now records D-75 B in #790's form. It quotes the
  question and the answer from sds-hq `state/decisions.md` at `a1b5c30b`, with the source (Devon,
  interactive session, 2026-11-23, recorded at 2026-11-23T12:07:23Z), and names the new verb. I
  made no other ADR change.
- **`layers.toml`**: no row is needed. The new test file matches the rotation worker's existing
  `tests/rotation_worker/**` pattern, and `make check`'s layer-manifest guard passes.

### The project-limit choice: any secret the root identity reads

`config.toml` names no `Rotation / Admin` project id. It has `work_project_id` and
`keepers_project_id`, and `admin_secrets` maps provider names to secret ids, not a project. The
handoff bars adding a config key only for this, so the verb doesn't limit by project.

This adds no reach. The verb runs as `rotation`, which already owns the root token file
(`read_root_token` refuses any other owner), and that token reads every secret the root reads. A
process with the file can already print any of them. The verb adds one shape that prints a value,
for a pipe. The remaining risk is a session running it with captured stdout, which isn't a
terminal. The runbook forbids running that half alone, and the follow-ups name the narrowing.

## Tests and checks

- `tests/rotation_worker/test_emit_secret.py` has 16 tests, with placeholder values only
  (`placeholder-value-1`):
  - The value reaches stdout exactly once with one newline, through `cli.main`.
  - A terminal refuses with no store opened.
  - A store failure whose message carries the value exits 2, with the value on neither stream.
  - An unknown id exits 2.
  - Four malformed ids refuse with no store opened.
  - The Keychain instance refuses.
  - A failed write names only the class.
  - An unforeseen exception names only the class.
  - A config error names its reason.
  - Three whitespace or control-character values are refused.
  - A real closed pipe in a subprocess exits 2 with one message.
- **Mutation checks:** I cleared `__pycache__` around each mutation and restored the file after.
  Three mutations, no survivors:
  1. Dropping the TTY refusal fails `test_a_terminal_is_refused_before_the_store_is_opened`.
  2. Printing the store's message instead of its class fails
     `test_a_store_failure_exits_2_and_carries_no_value`.
  3. Dropping `_discard(out)` fails `test_a_real_closed_pipe_exits_2_with_one_message`
     (`assert 120 == 2`).
- **`make check`** on database `orchestrator_test_hq_w_281`: lint and type-check are green, with
  14,760 passed and 1 skipped, exit 0, at `4c446ad7`, the final code and docs. An earlier run at
  `b82ff17b` gave the same count.
- **Risk verdict**
  (`uv run --project ~/Developer/code-standards code-standards risk --repo .`): `high-risk`, rule
  `secrets`, matched on `tests/rotation_worker/test_emit_secret.py`.
- **Vendor review:** agy was off by configuration.
  - Run 1 covered the diff before the `/code-review` fixes. Claude and Codex both approved, with
    0 findings.
  - Run 2 covered the diff after those fixes. Claude approved. Codex raised one medium finding,
    labelled `cited`: the runbook said a non-zero status means the file is unchanged, and ssh can
    drop after the write. A doc claim can't be reproduced by a failing test. I fixed it in
    `c59ae9cd`.
  - That final doc-only edit hasn't been through a vendor review itself.
- **`/code-review` (medium)** raised five nits, and I fixed four:
  - The exit 120 on a closed pipe reproduced, and I fixed it.
  - The whitespace or control-character value is now refused.
  - The runbook claimed a uniform error format; I reworded it.
  - The runbook didn't say what proves the value; it now names the host's first pass.
  - The fifth, limiting the project, is the choice in the preceding section.

## Files changed

- `src/rotation_worker/cli.py`: the verb, its parser entry, and the docstring line.
- `tests/rotation_worker/test_emit_secret.py` (new).
- `docs/operations/rotation-executor.md`.
- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`.
- This report.

## Self-review

The diff touches none of the do-not-touch files: `.cred-consumers.toml`, `set-root-token.sh`,
`provision.sh`, the lane rows, and #793's sections. `provision.sh` needs no change, because the
verb ships in the same venv as every other verb. Nothing ran against the VM, the VPS or BWS. The
main tree wasn't touched.

## Concerns

- **Whether `orb` gives the VM process a pipe or a pty is unproven until the live run.** The
  route's hops were proved with a throwaway value, but not through this verb. If `orb` allocates
  a pty, the verb refuses, which fails closed.
- **The bitwarden SDK's own output.** The verb itself writes nothing but the value to stdout. The
  Rust SDK isn't expected to write to stdout, but nothing here can prove that without the live
  login. A stray line would reach `set-root-token`, which refuses a second line and leaves the
  file unchanged, so this also fails closed.
- **The literal `prod-vps` address.** The handoff wrote `root@<prod-vps>`. I wrote
  `40.160.102.182`, the route proved on 2026-11-23, so the command is runnable as written, and the
  page names it as `prod-vps`. A host move must update this line. `portfolio lint` warns on it
  once the address is in `retired-hosts.toml`.
- **The verb can print any secret the root reads.** See the project-limit choice. It's no wider
  than the token file `rotation` already holds.

## Standards followed

- **Code Brain rule 3 (structured logs to stdout):** stdout here carries the value only, as the
  handoff directs. Anything else is a one-line message to stderr, as the other verbs do, and
  `logs.configure()` writes its JSON lines to stderr.
- **Code Brain rule 4 (no silent swallow):** each `except` prints the id and class, or the
  scrubbed config message, and exits 2. The catch-all and `_discard`'s in-memory-stream return
  each carry an inline reason.
- **Code Brain rule 5 (never log secrets):** no message, log line, test assertion message or
  report line carries a value. The tests assert the placeholder is absent from stderr, including
  when the store's own message holds it.
- **Infra Brain 591 and 592 (no BWS-token-shaped string in tracked files or history):** the
  tests use `placeholder-value-1` and its variants, and the docs describe the token, never its
  shape.
- **Infra Brain 593 (no inline `BWS_ACCESS_TOKEN`):** none is added. The pipe runs under
  `env -i` with `PATH` and `HOME` only.
- **Infra Brain 1032 (report exposure; no revoke without attested consumers):** I saw no real
  credential. The runbook puts the VM token's revoke after every other item 5 check.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable.
- **ADR-0055 decision 3 (values never in an argument list):** the verb takes the secret's id, not
  its value. The value goes from the SDK to stdout and through ssh's stdin to
  `set-root-token --stdin`.
- **The orchestrator's `CLAUDE.md` invariants and `make check`:** green as stated. The layer
  manifest is unchanged and covers the file.

## Follow-ups

- At the cut-over: update the VM to this PR's merge commit, then run the pipe. Prove the value by
  the host's first pass, and check whether `orb` gives the verb a pipe.
- Optional narrowing, if HQ wants it: name the Admin project in the config (a new key, a separate
  decision), or limit the verb to the one secret id after the cut-over. Removing the verb once
  the BWS copy is deleted is another option.
