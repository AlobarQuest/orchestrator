# Question: adr-0055-a29-executor-on-vps

Worker W-217, 2026-11-18. Base `ad721fc0509de725f808059f6ce2878b9f613e26`. I stopped before
writing Amendment 29 or opening a PR, as handoff item 3 directs: a source contradicts D-64's
premise.

## What the handoff asked

Item 3: find from a source how Coolify and infraops reach the VPS host, and write T1 from that. "If
Coolify or `vps_exec` holders reach it, that contradicts D-64's premise: write it to the question
file and stop."

D-64 B, as recorded at sds-hq `902da11b` (`state/decisions.md`): "A systemd service on the VPS
host under its own user, outside Docker and Coolify: no Coolify path; VPS root gains Rotation /
Admin and the keepers".

## What the sources show

Both Coolify and infraops reach the VPS host as root. Root reads any file a systemd service user
owns, so both reach the executor's root token file under D-64 B.

1. **Coolify connects to its own host as root, with a terminal enabled.** infraops
   `coolify_get_server` on prod, server `localhost` (uuid `kgo8kgsk…`, `is_coolify_host: true`),
   read 2026-11-18:
   - `"user": "root"`, `"ip": "host.docker.internal"`, `"port": 22`, `"private_key_id": 0`
   - `"settings.is_terminal_enabled": true`

   So Coolify holds an SSH private key that logs in to the VPS host as root, and its web terminal
   gives a root shell on that host. Anyone with Coolify admin access (UI terminal), or who can read
   that key out of Coolify's database or `/data/coolify/ssh/keys`, gets root on the host.

2. **infraops `vps_exec` runs arbitrary shell as root on prod.**
   `~/Projects/infraops-mcp-server/src/services/ssh-client.ts` 30-33: `username =
   process.env.VPS_USER || 'root'`; `start.sh` 15 and `docs/secrets-inventory.md` 344 record
   `root (default)`, and no file in that repo sets it to another user. `src/tools/vps.ts` 34-72:
   `vps_exec` takes any `command` string and runs it through that connection. Every session with
   the infraops MCP connected holds this path; this worker session had it connected.

3. **Inference, not tested:** a Coolify API token with write scope can likely also reach host
   files by deploying a resource that bind-mounts a host path or the Docker socket. I didn't
   verify this and would not state it in the ADR without a test.

## Why this matters to the amendment

D-64's question offered B as having "no Coolify path", and Devon accepted a T1 widened to "VPS
root". Under B as it stands, "VPS root" includes Coolify's admin and terminal holders and every
infraops session, so the root identity (Rotation / Admin plus every keeper in Rotation / Keepers)
is one `vps_exec` call away from any session with infraops connected. That is a wider residual
than the question described, and the gap between B and option A (a Coolify resource) is smaller
than the question implied. A systemd user can't isolate a file from root.

## The question

Which of the following does Devon choose?

- **(1) Accept as found.** Write Amendment 29 with T1 stating that VPS root includes Coolify's root
  SSH key and terminal holders and every infraops `vps_exec` holder, and that the systemd user
  isolates the token from other non-root users and containers only. Devon re-accepts that wider
  residual explicitly (a new decision recorded in `state/decisions.md`).
- **(2) Accept, and narrow the root paths first.** As (1), plus cut-over conditions that remove
  root paths, for example moving infraops `VPS_USER` and Coolify's server user off root. Each is a
  change to another system's access, so it needs its own decision and its own increment; I would
  carry them in the amendment only as named prerequisites with an owner.
- **(3) Re-open D-64.** Reconsider C (a dedicated host or VM) now that B's premise is shown false.

No other part of the handoff was blocked at the point I stopped. I wrote no ADR text, changed no
resource, and called no write tool against any system.
