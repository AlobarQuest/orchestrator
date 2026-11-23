Outcome: done

# Report: the rotation executor's install procedure for the VPS host (increment 7, item c1)

PR: #PRNUM, branch `hq/lanes-inc-7-host-install`, base `a3109318`. Ready for review; HQ reviews and
merges. Nothing ran on any host, VM, Coolify, BWS or infraops in this task.

## What I built

- **The procedure.** `docs/operations/rotation-executor.md` has a section, "Install or update it on
  the VPS host", after the VM's section. The VM's section stays and opens by saying when it applies:
  until the cut-over, and then only as the rollback while the VM's root token is unrevoked. The
  host section covers the route (`vps_exec` as root, an interactive session's step), installing at
  a commit, the config, the root token's file (its end state only), the helper's key and host pin,
  the cut-over order, the one-pass command under `env -i`, reading passes in `journalctl`, enabling
  the timer only after the VM's is stopped and disabled, and rollback as far as item 4 step 6 and
  item 5 support it. It says it's unproven until the cut-over.
- **One code change the inventory proved needed** (inventory item 2). `provision.sh` used an existing
  `rotation` user as found. A marked block, `# BEGIN service-user` to `# END service-user`, after the
  user's creation and before the first `install`, refuses with exit 2 and the reason when the user
  has uid 0, is in any group but its own, or has a login shell other than `/usr/sbin/nologin`. It
  never changes the user. The header names the host procedure. `set-root-token.sh`, the unit and
  the timer are unchanged.

## Inventory: the VM install against a VPS-host install

Sources are read at `a3109318`. "ADR" means
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, and "the page" means
`docs/operations/rotation-executor.md`. Nothing here was read from a host.

1. **How a session runs root commands on the host.** On the VM, the page has the session run
   `orb -m rotation-executor -u root bash -s -- <commit> < provision.sh` (page, lines 353-356).
   The sources name one route to root on `prod-vps` that a session holds: infraops `vps_exec`,
   which runs a shell command over SSH as `VPS_USER`, `root` by default (ADR amendment 29 item 2,
   lines 4111-4116, and amendment 31 item 2, lines 4462-4466, where it stays root by D-74 A).
   infraops' `src/tools/vps.ts` (lines 38-92) shows the tool takes a `command` string only, with
   no stdin and no TTY, and a `timeout` of at most 300000 ms (default 30000). It returns stdout,
   stderr and the exit code into the transcript. So:
   - `bash -s < provision.sh` has no direct form. The repository is public (`gh repo view`:
     `PUBLIC`), and `provision.sh` already clones it unauthenticated (line 38). A session can
     download the script at the commit from `raw.githubusercontent.com` to a temporary file and
     run that file, in one `vps_exec` call with a 300000 ms timeout. A pipe into `bash -s` isn't
     used: without `pipefail` in the login shell, a failed download would run an empty script and
     exit 0.
   - The deploy gate (sds-hq `docs/operations/deploy-gate.md`, "What passes") admits `vps_exec`
     only for the deploy script's named reads and one plain `lane-run <lane>` pass. Neither
     `provision.sh` nor the one-pass command is among them, so every root step here is an
     interactive session's step, where the high-power gate asks a person on each call.
2. **The `rotation` user.** `provision.sh` runs `useradd --system --home-dir /var/lib/rotation-worker
   --shell /usr/sbin/nologin rotation` only when no such user exists (lines 46-48). It passes no
   `-G`, so the user it creates is in no group but its own: on Ubuntu, `USERGROUPS_ENAB yes`
   gives it a group named `rotation`, which the next lines' `install -g rotation` depend on. When
   the user already exists, the script checks nothing about it: groups, login shell and uid are
   used as found. ADR amendment 29 item 1 (lines 4069-4072) requires no login shell, no sudoers
   rule and no membership in `docker` or any other group that reaches root. The VPS runs Docker
   (the page, "Install the helper": "Its only dependency is Python 3.12 and Docker"; the lanes run
   in Coolify there), so a `docker` group exists on the host, and the VM had none. Nothing in the
   sources says whether a `rotation` user exists on `prod-vps`. **Code is needed here:** a refusal
   in `provision.sh` when the user is in any group but its own, has a login shell, or has uid 0.
   A sudoers rule naming the user is a read in the procedure (`sudo -l -U rotation`), not code.
3. **Host packages, paths and architecture.** `provision.sh` assumes:
   - `apt-get` when any of `git`, `curl` or `ssh` is missing (lines 41-44). The host is Ubuntu
     24.04 (`~/Projects/docs/superpowers/plans/2026-10-23-hetzner-to-ovh-migration.md`, line 11),
     so `apt-get` exists. Which of the three are installed isn't in the sources; the script
     installs what's missing either way.
   - `uname -m` of `aarch64` or `x86_64` (lines 26-33). The VM is aarch64 (the Mac mini). The VPS's
     architecture isn't in the sources. The x86_64 pin has never run on the VM, so I downloaded
     `uv-x86_64-unknown-linux-gnu.tar.gz` for 0.10.9 and hashed it: `20d79708…c57594`, equal to
     the pin (line 31). An architecture outside the two is refused with exit 2.
   - `sha256sum`, `tar`, `install`, `useradd`, `systemctl`, `runuser`: coreutils, passwd,
     systemd and util-linux, all in Ubuntu 24.04's base.
   - The paths `/opt/rotation-worker`, `/var/lib/rotation-worker` and `/etc/rotation-worker`, the
     same as ADR amendment 29 item 1 (lines 4073-4077). The unit (`ProtectSystem=strict`,
     `ProtectHome=yes`, `ReadWritePaths=/var/lib/rotation-worker`, `PATH` and `HOME` only) needs no
     change for the host.
4. **The root token's handover.** On the VM, Devon pastes the token at `set-root-token`'s hidden
   prompt in a terminal on the Mac (page, "Create them", step 2). `set-root-token.sh` refuses
   without a TTY (lines 16-19), and `vps_exec` has none. ADR amendment 29 item 4 step 2 (lines
   4195-4198) leaves move-versus-second-token to Devon at the cut-over's mint gate. The handoff's
   rules (Devon only mints into BWS and grants read, runs no terminal step, and the value enters
   no transcript) aren't met by any existing path I found:
   - `set-root-token` through `vps_exec`: refused, no TTY.
   - Devon at a terminal prompt over SSH: a terminal step of Devon's.
   - A BWS secret holding the token that something on the host reads: nothing on the host holds
     a BWS identity before the token arrives, and a grant to the broker's or any other account
     would widen a grant (Infra Brain 598, as the ADR states it).
   - Moving the VM's file: no sourced route carries a file's bytes from the VM to the host without
     the session's stdout (`vps_exec` and `vps_write_file` take the content as an argument).
   This is an open question (the handoff says to list it, not design it). The procedure states the
   file's end state and leaves the handover to the mint gate.
5. **The helper's reach.** Unchanged in substance (ADR amendment 29 item 1, lines 4084-4090, and
   item 4 step 5, lines 4204-4205):
   - Destination `root@<VPS tailnet address>`, `pg_helper_host` unchanged.
   - The same key, read from `Rotation / Keepers` by `pg_helper_key_id` at each use. Nothing is
     created or copied on the host.
   - The same `authorized_keys` line. It carries `restrict` and the forced command and no `from=`
     (page, "Install the helper", step 4), so a connection from the host itself matches it with no
     edit.
   - The host-key pin is re-read from the new host (`ssh-keyscan -t ed25519 <tailnet address>`
     on the VPS) and compared with `/etc/ssh/ssh_host_ed25519_key.pub`. It's the same host, so the
     line is expected to be the one in the VM's config. A difference stops the cut-over.
   - One new fact: a connection from the host to its own tailnet address is delivered locally,
     through the loopback route, not through `tailscale0`. The migration plan's firewall summary
     (line 220) lists `tailscale0` and port 22 for `INPUT` and doesn't say whether loopback is
     accepted. SSH on port 22 is accepted on every interface. Coolify on port 8000 is a Docker
     published port. ADR amendment 29 item 5's checks (the helper's `loaded` answers, the tailnet
     Coolify `PATCH` answers 2xx) prove both. No change is made for this.
6. **`config.toml`.** No key changes for the host:
   - `coolify_url` stays `http://100.92.138.19:8000`, the tailnet address (ADR amendment 29
     item 1, lines 4091-4093).
   - `pg_helper_host` and `pg_helper_known_host` stay, the latter if the re-read in item 5 equals
     it.
   - `root_token_file` and `state_dir` are the same paths.
   - `store` stays `bws`, so the heartbeat key `rotation-heartbeat:bws` is unchanged (lines
     4078-4083). `config.py`'s `VM_ONLY` keys are tied to the `bws` store kind, not the host.
   - `source_revision` is rewritten by `provision.sh` to the installed commit.
   - Every other id (`credential_key_id`, `bearer_secret_id`, the projects, the admin secrets,
     the Healthchecks and GitHub App ids, the inbox) names something that doesn't move (lines
     4078-4083).
   The config holds ids and paths, no value (item 1), so a session copies the VM's file to the
   host as it is, then sets its owner, group and mode (`root`, `rotation`, `0640`).
7. **One instance at a time.** ADR amendment 29 item 4 step 6 (line 4206) stops and disables the
   VM's timer before the VPS's first pass, which includes a pass run by hand. So the install, the
   config and the token's file can come before that step, but the `env -i` proof pass can't.
   `provision.sh` never enables the timer (it ends in `systemctl daemon-reload`), so installing
   early starts nothing.
8. **State.** Item 4 step 4 (lines 4201-4203): copy the three state files from the VM's
   `/var/lib/rotation-worker`, or accept their loss, which restarts a wait and never shortens it.
9. **Backups.** Verify item "No vps-backup recipe copies the executor's root token or `/etc` off
   the host" (line 4232) is the cut-over's, not this procedure's.

Conclusion: the procedure can be written without a design choice outside Amendment 29 and Devon's
answers. Two points stay open for the report: the root token's handover (item 4) and rollback
past the VM token's revoke.

## Files changed

- `docs/operations/rotation-executor.md`: the VM section's opening paragraph, and the section
  "Install or update it on the VPS host" (lines 432-580).
- `scripts/rotation-worker-vm/provision.sh`: the `service-user` block and two header lines. The
  `source-revision` markers are unchanged.
- `tests/scripts/test_provision_source_revision.py`: six tests for the block, and the docstring.
  The file is listed in `layers.toml` by name, so no row is needed.
- `docs/superpowers/reports/lanes-inc-7-host-install-report.md`: this report (`docs/superpowers/**`
  in `layers.toml`).

## Tests and checks

- `make check` on a clean tree, with database `orchestrator_test_hq_w_275`: exit 0, lint and
  pyright clean, **14,674 passed and 1 skipped (14,675 collected)** in 316 seconds. It ran at
  `HEAD`, after the code change was committed; see "Final run" for the run after the last docs
  edits.
- **Final run:** `make check` again at `c8164b75`, after every page and report edit before this
  line, on a clean tree: exit 0, 14,674 passed and 1 skipped (14,675 collected).
- The provision tests: 8 passed. The six new ones failed before the block existed (6 failed,
  2 passed).
- **Mutation proof.** Each mutant was applied to `provision.sh`, the test file run, and the script
  restored (checked equal to the original with `diff`). All six were caught:

  | Mutant | Result |
  |---|---|
  | uid check removed | 1 failed |
  | group check removed | 2 failed |
  | shell check removed | 1 failed |
  | uid refusal exits 0 | 1 failed |
  | block moved after the installs, before `daemon-reload` | 1 failed |
  | only a `docker` membership refused (`== *docker*`) | 1 failed (the `sudo`-group test) |

- `uv run --project ~/Developer/code-standards code-standards risk --repo .`: `normal`, so
  `vendor-review` wasn't required.
- `/code-review` (medium) on `origin/main...HEAD`: four nits and no bugs. I fixed one: the
  state-file copy had no command, and now it has a heredoc like the config's. I kept three as they
  are. The header's "Nothing here depends on OrbStack" line predates this change and stays true. A
  sudoers rule is a read in the procedure, not code (see the self-review). The tests stub `id` and
  `getent`: they prove the block's decisions, not the real tools' output format, which is
  coreutils' and glibc's.
- The x86_64 uv pin: I downloaded the 0.10.9 tarball and hashed it locally. It equals the pin. The
  VM is aarch64, so that pin has never run.

### How the VM install's behavior stays unchanged

No executable line changed. The diff adds the block and two header lines, and removes one line,
the header comment `# Install or update the credential-rotation executor in its isolated VM
(ADR-0055 decision 2).` (`git diff origin/main -- scripts/rotation-worker-vm/provision.sh | grep
'^-'` shows only it). On
the VM, `useradd --system --shell /usr/sbin/nologin rotation` with no `-G` gives a nonzero system
uid, the group `rotation` alone (Ubuntu's `USERGROUPS_ENAB`, which the existing
`install -g rotation` lines already depend on), and that shell, so the block passes:
`test_the_user_it_creates_passes` holds that shape. That's argued from the script, not observed on
the VM, which this task doesn't touch. If the VM's user were found otherwise, the next VM update
would refuse before installing anything, and that user would already break amendment 29 item 1.

## Self-review

- **The sudoers rule isn't checked in code.** `sudo -l -U` output is text meant for people, and
  parsing it is fragile. The procedure's step 2 reads it, and amendment 29 item 1 is the rule.
  Groups, uid and shell are the parts a stable command answers.
- **The install fetches `provision.sh` from `raw.githubusercontent.com` at the commit**, because
  `vps_exec` has no stdin. A commit-addressed URL serves fixed bytes. The command downloads to a
  file and checks curl's exit code before running anything, so a failed download can't run as an
  empty script that exits 0.
- **The hand pass's output comes back through `vps_exec`.** The executor logs one JSON line per
  event with no value (Code Brain 5), so the transcript gets what the journal would.
- **A `vps_exec` timeout only closes the connection** (infraops `src/services/ssh-client.ts`,
  lines 68-71), so the remote command may keep running. The install runs under `flock -n`, so a
  re-run can't overlap a live one. Before enabling the timer, `pgrep -u rotation` must print
  nothing, so a timed-out hand pass can't overlap the first timer pass (item 4 step 6).
- **The state files hold no value.** I read their writers: `announced-acts.json` is a list of alert
  keys (`alert_hub_client` `KeyedAlerts`), `wait-since.json` maps keys to times (`FirstSightings`),
  and `revokes-observed.json` maps rotation ids to times (`revokes.py`, lines 98-106). Copying them
  through the transcript exposes nothing.

## Concerns

- No standard covers host-user isolation or where a root credential rests on a host (amendment 29
  item 2 says so). This change rests on the ADR, as the handoff says.
- Infra Brain has no systemd lesson. The unit is unchanged.

## Open questions

1. **The root token's handover to the host.** No existing path meets the handoff's rules (inventory
   item 4): `set-root-token` needs a terminal and `vps_exec` has none; a prompt over SSH is a
   terminal step of Devon's; a BWS copy that something on the host reads needs an identity there
   first, or a widened grant; and no sourced route moves the VM's file without the session's
   stdout. The page states the file's end state and leaves the route to the mint gate (amendment 29
   item 4 step 2). Someone has to decide a route before the cut-over.
2. **Rollback after the VM's root token is revoked, or after a move.** Item 5 makes that revoke the
   last check, and nothing in item 4 or 5 says how the VM would run again after it. It would need a
   new token: question 1's handover again, in reverse. Under the "move the file" choice there's no
   second token, so there's nothing to revoke and the VM is left with no token. Item 5's check "after
   its revoke its root token is refused" then doesn't apply as written. The page says it has no
   rollback past either point and that a move has nothing to revoke.

## Follow-ups

1. ADR-0055 amendment 29's Consequences list `rotation-executor.md`'s VM procedures as stale. The
   install is now covered. These still name only `orb -m rotation-executor` and aren't changed
   here: "Create them" step 2 (`set-root-token`) and step 3 (`mint-bearer`), "Move a keeper"
   step 3, "Rotate them by hand" (the root token), "Install the helper" steps 3 and 5 (key creation
   and the pin from the VM), the proofs after it, and "Adopt the keeper". After the cut-over each
   needs its host form, or a note that it's the VM's.
2. No ADR-0055 line cite into the page moved: every cite (lines 114 to 174) comes before the
   insertion. Amendment 29 item 3's helper citations (line 4146) are still stale, as amendment 31's
   Consequences say. That's W-274's file, and I didn't edit it.
3. At the cut-over, record how long the `env -i` hand pass took. If one exceeds `vps_exec`'s
   300 seconds, the page needs another form for it, for example `systemctl start` with the
   journal read (that form proves the unit's environment, not `env -i`).
4. Item 4 step 4 names three state files. The VM's state directory also holds the inbox's
   `inbox-refused.json` (a fingerprint prefix; losing it delays one alert by a pass,
   `inbox.py`, lines 192-193) and the canary's fake-adapter state ("The canary": "a state file in
   the VM"). Check at the cut-over whether either needs to move.
5. Verify item "No vps-backup recipe copies the executor's root token or `/etc`" stays the
   cut-over's, as amendment 29 assigns it.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout, no file logs):** nothing changes how the executor
  logs. The page reads passes with `journalctl -u rotation-worker -o cat` (one JSON line per event),
  and the hand pass prints the same lines to stdout.
- **Code Brain 4 (never swallow an error):** `provision.sh` keeps `set -euo pipefail`. Each refusal
  prints its reason to stderr and exits 2, which the tests check (`returncode == 2`, reason in
  stderr). The install command returns the script's exit code, and a failed download stops
  before anything runs.
- **Code Brain 5 (never log secrets):** no step prints a value. The token's file is checked with
  `stat` (owner, group, mode), never read. The config and state files hold ids and times only.
- **Infra Brain 2, 591, 592, 593 (no secrets, env files or BWS tokens in tracked files; no inline
  `BWS_ACCESS_TOKEN`):** none is added. The page describes the token's file and never shows a
  token.
- **Infra Brain 1032 (no revoke without attested consumers):** cut-over step 6 puts the VM token's
  revoke after every other amendment 29 item 5 check, and rollback is offered only before it.
- **Infra Brain 598, as the ADR states it (the root keeps exactly its scope):** nothing adds or
  widens a BWS identity, project, grant or scope. The open question names the widening a BWS-copy
  route would need, and doesn't take it.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. No domain changes.
- **Repo rules:** `make check` green with the collected count read from a file. The test sits in a
  file `layers.toml` already lists, and the report under `docs/superpowers/**`. No W-274 file,
  `.cred-consumers.toml`, `targets.toml` or `deploy/vps/sds-pg-rotate` is touched.
  `scripts/rotation-worker-vm/` isn't moved, and the `source-revision` markers stay.
  `~/Developer/code-standards/STANDARDS.md`: the risk verdict is `normal`, and the `/code-review`
  result is listed in the preceding sections.
- **Devon's procedure-page rules:** every root step is a session's. Devon's only part is minting
  into BWS and granting read, and he runs no terminal step. No BWS grant step is added, so the
  grant-step wording rule has nothing to apply to.
