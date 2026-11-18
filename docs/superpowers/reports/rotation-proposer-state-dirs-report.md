Outcome: done

# Report: rotation-proposer-state-dirs

- PR: #743 (ready for review, not merged), branch `hq/rotation-proposer-state-dirs`, based on
  `90f26ecda1445eddb938e75720e6b17d7928b17a`.
- Content commit: `2617a571f32184ce468449497e7de5d909f8f48b`. The head is the commit that adds this
  report on top of it.

## What I built

The rotation proposer's lane container gets both infraops inputs (cut-over item 1 in
`docs/operations/deploy.md`).

- **State directory: `SECURITY_DRIFT_STATE_DIR=/var/lib/sds/rotation-proposer/security-drift`.**
  This is a subdirectory of the lane's existing volume `sds-lanes-rotation-proposer-state`
  (ADR-0059 Amendment 12). It's a separate directory for two reasons. infraops writes the file by
  a temporary file and a rename, so the directory holds only infraops' store, apart from the
  proposer's `alerts.json` and `approval-since.json`. The name also says whose file it is. The fence
  still exits 2 on an unset or empty setting, or on a missing `cred-rotation-state.json`, before
  any clone or secret.
- **The copy's target, in `deploy.md`.** `rotation-state-copy` must produce
  `/var/lib/sds/rotation-proposer/security-drift/cred-rotation-state.json` with these properties:
  - A regular file, mode 0600, owned by the container's `orchestrator` user.
  - In a directory at mode 0700 that the same user owns.
  - Written through `docker exec -i -u orchestrator … sh -c 'umask 077 && mkdir -p … && cat > …'`
    from stdin, then compared by SHA-256 with the Mac copy. A plain `docker cp` or a root exec
    leaves a file that `loadValidated0600Json` refuses.
- **List: `INFRADRIFT_CONFIG_DIR=/var/tmp/sds-rotation-proposer/infra-drift`.** The value comes from
  the compose, so Amendment 12 decision 1 and `lane_run.INFRAOPS_SETTINGS` hold as written. The
  directory sits beside the pass's clones and off the volume. Each pass does the following:
  1. Removes the previous pass's list along with its clones.
  2. Clones the four registries' repositories anonymously, shallow at `main`, through
     `scripts/sds-cred-registries.sh`: infraops-mcp-server, change-manager, factory-runner, and
     orchestrator. The infraops clone doubles as the `cred-findings` checkout, so the pass makes
     three new clones.
  3. Checks that each clone has a readable `.cred-consumers.toml`, and exits 2 if one doesn't.
  4. Writes the list, four absolute paths in the Mac list's order.
  5. Discards the clones and the list after the program.
- **Order.** The order is now: fence, alert-state directory, clones, registry check, list, then
  secrets (`--submit`), mint, `unset BWS_ACCESS_TOKEN`, git credential, and program. A pass with an
  unusable input exits 2 having read no secret. The lane token is still set during the clones, so
  `sds_anonymous_clone` runs each git with `env -u BWS_ACCESS_TOKEN` and no credential
  configuration. The push token is minted after the clones.
- **A person's command between passes.** The exact form, in `deploy.md`:

  ```sh
  docker exec -i -u orchestrator <container> bash -c 'source /app/scripts/sds-cred-registries.sh && sds_infraops_session "$@"' _ resolve-exposure --cred <id> --exposure <id>
  ```

  - The CLI is the session's own infraops clone at `main` (`dist/` is tracked), run with the image's
    `node`.
  - `INFRADRIFT_CONFIG_DIR` is the session's own list, in a fresh `/var/tmp` directory, never the
    pass's.
  - `SECURITY_DRIFT_STATE_DIR` is inherited from the compose through `docker exec`.
  - The session unsets the lane token, refuses root, refuses an unset or missing state file, and
    discards its clones on exit.

  It needs no new mount, volume, or credential.
- **The Mac branch doesn't change.** The new helper is sourced inside the container block only.

### Probe before writing

In a scratch directory under `/tmp`, I made anonymous `--depth 1` clones of the four repositories,
wrote the list, and added a 0600 copy of the Mac state file. Then I ran
`node infraops-mcp-server/dist/cli/security-drift-cli.js cred-findings` with no `node_modules`. It
exited 0, and its `findings` equal those of the Mac's own `~/.config/infra-drift` run (one
finding).

The scratch directory `/tmp/w218probe.ygePUg` is still there, because the permission rules denied
`rm -rf`. It holds public clones and a copy of the rotation state. That copy carries no secret,
but delete the directory.

I confirmed with `gh` that all five repositories are public and that each of the four registries
has `.cred-consumers.toml` on `main`.

## Tests and checks

- `make check`, run on a clean tree at the content commit with `orchestrator_test_hq_w_218`:
  exit 0, **15,635 passed, 1 skipped** (ruff, format, pyright, shellcheck, and pytest).
- Security scan from the repo root (`security_scan.cli . --category security`): 0 BLOCK. The 2 WARN
  and 1 INFO findings are on `.gitignore`, `.bws-secrets.toml`, and machine-account scope. This
  change didn't touch those files.
- **The Mac-branch test is
  `tests/scripts/test_run_rotation_proposer_lane.py::test_the_mac_branchs_environment_and_arguments_are_unchanged`**,
  for both the dry run and `--submit`. It pins:
  - The exact set of variables the program receives, with neither infraops setting among them.
  - The program's arguments.
  - That nothing is cloned and no list is written.

  Two runs check that it discriminates:
  - Differential: it passes against `origin/main`'s launcher.
  - Mutation: exporting `INFRADRIFT_CONFIG_DIR` in the Mac branch fails both cases.
- New and changed tests in `test_run_rotation_proposer_lane.py`:
  - The list's content: four entries, each `<clone>/<repository>/.cred-consumers.toml`.
  - A clone without its registry file exits 2 and reads no secret, for each registry, dry and
    submit.
  - A stale list is removed before it can be read.
  - A missing state file exits 2 before anything, dry and submit.
  - A failed clone exits 1 with `FATAL:`, for all five repositories, and leaves no list and no
    secret read.
  - The secret and mint failures now come after the inputs and before the program.
  - The five clones are anonymous.
  - Cleanup covers the list.
  - The compose pin has the two values and a new comment.
- `tests/scripts/test_sds_cred_registries.py` (new) tests the session:
  - Its own clones and list, the inherited state directory, and no lane token reaching node.
  - Cleanup, and infraops' exit code passed through.
  - The refusals of root, an unset or empty state directory, and a missing state file.
  - A missing registry file and a failed clone.
- `deploy.add_refusals` still returns `[]` on the compose. `rotation-proposer` stays in
  `test_deploy_lanes.py`'s `placeholders`.

## Files changed

- `lanes/rotation-proposer/compose.yaml`: both settings and the header comment.
- `scripts/run-rotation-proposer.sh`: the header, the fence (the list check removed), the inputs
  block moved before the secrets, and cleanup.
- `scripts/sds-cred-registries.sh` (new): `sds_anonymous_clone`, `sds_cred_registries`, and
  `sds_infraops_session`.
- `tests/scripts/test_run_rotation_proposer_lane.py` and `tests/scripts/test_sds_cred_registries.py`
  (new).
- `tests/architecture/test_lane_image.py`: the helper count goes from 5 to 6.
- `lanes/Dockerfile`: the comment changes from "five helpers" to "six". The `COPY scripts/sds-*.sh`
  line already ships the new helper.
- `layers.toml`: places the new test.
- `docs/operations/deploy.md`: the two settings, the list, the clones, the person's command, cut-over
  item 1 marked done, and the copy's target and mode.

## Self-review

I ran `/code-review` at medium on the diff. It raised five points, and I changed none of them:

- **An `exit 1` after the clones leaves them on disk.** This happens on a failed secret read or
  mint. The next pass removes them. They hold no credential. The launcher already behaved this way
  for failures after its clones, and an EXIT trap could collide with the dead-man switch's.
- **The compose's list path "must match" the clone root.** It doesn't have to. The list names
  absolute clone paths wherever the list itself lives.
- **The session runs `main`'s infraops with write access to the state file.** This is the same
  trust the pass already has: its program runs `record-rotation` from the same clone. The Mac also
  runs a checkout of `main`.
- **A killed session leaks its temporary directory.** This is minor. A container restart doesn't
  clear `/var/tmp` either.
- **A dry run now makes five clones instead of two.** This follows from "clones at `main`". A
  GitHub outage fails the pass with `FATAL:`, as it did before.

## Concerns

- **The cut-over order changed.** In `deploy.md`, the state file's copy now comes before step 9's
  bare run, because a bare run without the state file exits 2. Earlier text had the copy only in
  step 10.
- **The pass needs five GitHub fetches.** Every pass, the dry run included, depends on five
  anonymous GitHub fetches. A dry run that fails on a clone exits 1.
- **Code Brain 4.** The pass's final cleanup keeps its existing `|| echo` on `rm -rf`. That's an
  output step that logs the failure, not an input step, and the handoff's "no `|| true` on an input
  step" holds.

## Follow-ups

- ADR text this makes stale, listed in the PR body and not edited:
  - ADR-0059 decision 5 calls the list "hand-maintained". In the container, the launcher writes it
    each pass.
  - Amendment 12 decision 1's "the directory that holds the lane's `cred-consumers.list`" still
    holds, but that list is now per-pass output.
- A fifth registry (security-standards' `.cred-consumers.toml`) is an open item for an interactive
  session. The list keeps the Mac's four.
- Delete the probe directory `/tmp/w218probe.ygePUg`.
- Tear down the test database `orchestrator_test_hq_w_218` and this worktree after merge.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout).** This rule applies to application logs, not
  the launcher. The launcher and the new helper are shell scripts and keep the existing form:
  plain `FATAL:` lines on stderr.
- **Code Brain 4 (never silently swallow an exception).** Each clone, registry check, and list
  write stops the pass with a `FATAL:` line and a non-zero exit:
  - A failed clone or list write exits 1.
  - A missing registry file exits 2.

  No input step uses `|| true`. The tests assert each `FATAL:` line and exit code.
- **Code Brain 5 (never log secrets).** No token, extraheader, or secret value is printed:
  - Clones stay anonymous, with no credential configured, `GIT_CONFIG_GLOBAL=/dev/null`, no
    prompt, and no trace.
  - Clones run without `BWS_ACCESS_TOKEN` in git's environment, and before the push token is
    minted.
  - `test_no_token_reaches_an_argument_a_program_or_git` and the git-log assertions hold this.
- **Infra Brain 591, 592, and 593.** No BWS token is in a tracked file or in history. The compose
  keeps `${ROTATION_PROPOSER_BWS_ACCESS_TOKEN}` with no inline value. The scan shows 0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`).** The list names all four registries, and the pass
  refuses (exit 2) instead of writing a shorter list when one is missing. An open exposure stays
  readable in the container for two reasons:
  - Its declaration is in a registry the list names. The probe showed the clone-built list gives
    the Mac's findings.
  - Its open state is in the cut-over copy on the volume, which the pass reads at
    `SECURITY_DRIFT_STATE_DIR`. That is Amendment 12's verify item "an open exposure survives the
    move".
- **Infra Brain 212 (HTTPS on a public FQDN).** Doesn't apply: the lane serves nothing, with no
  port, no domain, and no Traefik labels.
