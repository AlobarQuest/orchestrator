Outcome: failed

(In progress. This file holds the inventory, stop point 1, and is completed when the work ends.)

# Report: the rotation executor's install procedure for the VPS host (increment 7, item c1)

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
