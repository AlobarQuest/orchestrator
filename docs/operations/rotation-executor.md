# The rotation executor

ADR-0055. `src/rotation_worker/` is the credential-rotation executor: a deterministic program with
no LLM that claims approved rotation units with its own WORKER identity, works their steps through
one contract, and files `rotation-step/1` evidence. It runs as a service in the isolated OrbStack
VM `rotation-executor`, listens on no port, and is the only place a rotated value exists.

## How it decides what to do

Each pass, started by a systemd timer every five minutes, works every ready unit whose envelope
`constraints` name a `rotation_adapter`:

1. **It checks the unit against git before claiming it** (`checks.py`). The package revision's
   commit must be on intent-packages' `main` and hash to the approved content hash. The
   credential's registry entry, read at its repository's `main`, must still say
   `rotated_by_sds = true` and name the adapter's provider. The registry's destinations, the
   package's `profile_fields.destinations` and the envelope's `destinations` must be equal. A
   refusal claims the unit and fails it with the reason, so it reaches the dead-letter report. A
   git or network failure leaves the unit for the next pass.
2. **It claims and starts the unit, then runs its steps** (`contract.py`). Before each step it
   renews the lease and reads the registry again.
3. **It files each step's evidence and submits the unit.** A contract refusal fails the unit with
   its reason, which never holds a value.

The contract's invariants, the step kinds and why each exists are in `contract.py`'s docstring.
The executor never trusts evidence: it re-derives where a rotation stands from BWS (the keeper,
and the staging and quarantine secrets named `rotation/<credential>/<occurrence>/…` in
`Rotation / Work`) and from probes.

## Decompose a rotation

Each adapter fixes its decomposition (`adapters/`). For each unit, the decomposition proposal
gives:

- `required_capability: operational_action`, and `max_attempts` of at least three.
- Envelope `constraints`:
  - `rotation_adapter`: the adapter's name, for example `fake`.
  - `rotation_unit`: the unit's name in the adapter's plan, for example `mint`.
  - `destinations`: the package's list, exactly (`rotation-worker compare-destinations` prints
    it).
  - `touches_no_hosted_service: true`, only on a unit that writes no hosted consumer.
- `ac_mappings`: each step files against its own acceptance criterion, the `ac_id` the adapter's
  plan names, so the package declares one criterion per machine step.
- `dependencies`: each unit after the first depends on the one before it with
  `kind: work_unit`, `required_state_or_condition: completed`. Each human act in the plan is an
  `external_system` dependency on the unit after it, resolved at `/review` with the fingerprint
  the act produced.

The runner brief can't serve a rotation unit, because a rotation unit has no
`target_repository`. The executor reads its unit from `GET /api/v1/work-units/{id}` and its
package revision from `GET /api/v1/package-intakes/{revision_id}`.

## The canary

`rotation-canary` is a credential with no real provider, kept so the executor's whole path can run
on production with nothing live at stake. The fake adapter mints, probes and revokes it from a
state file in the VM. Its keeper is `rotation-canary keeper` in `Rotation / Work`, created once by
`rotation-worker seed-canary`, which prints the keeper's id and creates nothing if it exists. Its
registry entry is in this repository's `.cred-consumers.toml`, and its standing package is
intent-packages' `rotation-rotation-canary`, with one acceptance criterion per machine step
(AC-001 to AC-008). Class `rotation-canary` is never due by age, so it rotates only when a revision
of the package asks.

To run it, give the package a new `occurrence`, approve the revision, and register its intake.
The decomposition is the fake adapter's plan: units `mint` (AC-001 to AC-003), `store` (AC-004,
AC-005) and `retire` (AC-006 to AC-008), each depending on the one before. Between `store` and
`retire` is a human act: a person attests that the `runtime-fetch` consumer holds the new value,
resolving it at `/review` with the new value's fingerprint (`sha256_prefix` and `length`) from the
`store` unit's evidence.

## Install or update it

The VM is `rotation-executor`, created with `orb create --isolated --isolate-network
ubuntu:noble rotation-executor`. It reaches the internet (BWS, `sds.alobar.net`, GitHub) and other
OrbStack machines, but not the Mac.

1. Install the code at a commit that is on GitHub:

   ```sh
   orb -m rotation-executor -u root bash -s -- <40-character commit> \
     < scripts/rotation-worker-vm/provision.sh
   ```

   The script installs a checksum-pinned uv, checks the orchestrator out at that commit, builds
   `/opt/rotation-worker/venv` from the lockfile with the `rotation-worker` group, and installs
   the service and timer. It writes no configuration and no credential, so it is safe to re-run
   for each update.
2. Write `/etc/rotation-worker/config.toml` (ids and paths, no value), owned by root, group
   `rotation`, mode `0640`:

   ```toml
   orchestrator_url = "https://sds.alobar.net"
   credential_key_id = "rotation-executor-1"
   organization_id = "<BWS organization id>"
   work_project_id = "<Rotation / Work project id>"
   bearer_secret_id = "<the WORKER bearer's secret id, once it exists>"
   root_token_file = "/etc/rotation-worker/root-token"
   state_dir = "/var/lib/rotation-worker"
   source_revision = "<the first 12 characters of the installed commit>"
   ```

3. Enable the timer: `orb -m rotation-executor -u root systemctl enable --now
   rotation-worker.timer`. Read its passes with `journalctl -u rotation-worker` (one JSON line per
   event).

The service runs with only `PATH` and `HOME` set (R16). To run one pass by hand the same way:

```sh
orb -m rotation-executor -u root runuser -u rotation -- env -i PATH=/usr/bin:/bin \
  HOME=/var/lib/rotation-worker /opt/rotation-worker/venv/bin/rotation-worker run \
  --config /etc/rotation-worker/config.toml
```

## Its credentials

All four are in ADR-0055's hand-rotated set: a person creates and rotates them, never the
executor. None is ever printed, passed as an argument, or kept on the Mac.

| Credential | Lives in | Read by |
|---|---|---|
| The root: the `rotation-executor` BWS machine account's access token | `/etc/rotation-worker/root-token` in the VM, owner `rotation`, mode `0400` | The executor, to log in to BWS |
| The executor's WORKER bearer | BWS secret `rotation-executor WORKER bearer` in `Rotation / Executor`; its hash in the orchestrator's `ORCHESTRATOR_M2M_CREDENTIALS` under key `rotation-executor-1`, agent `rotation-executor` | The executor, from BWS at each pass |
| The read-only Healthchecks API key | BWS, in `Rotation / Healthchecks` | No reader until an adapter verifies a `runtime-fetch` consumer by its check |
| The three BWS projects | Bitwarden Secrets Manager | The root: write on `Rotation / Work`, read on `Rotation / Executor` and `Rotation / Healthchecks`, nothing else |

### Create them

1. In the Bitwarden web console, under Secrets Manager:
   1. Create the projects `Rotation / Work`, `Rotation / Executor` and `Rotation / Healthchecks`.
   2. Create the machine account `rotation-executor`. On its **Projects** tab, give it
      **Can read, write** on `Rotation / Work` and **Can read** on the other two. Give it no
      other project.
   3. On its **Access tokens** tab, create a token with no expiry. Keep the page open.
2. In a terminal on the Mac, run `orb -m rotation-executor -u root
   /opt/rotation-worker/bin/set-root-token`, then copy the token from the console and paste it
   at the hidden prompt. The value passes through the Mac's clipboard and nowhere else: no
   console-to-VM path avoids it, because the VM has no browser. Clear the clipboard afterwards
   with `pbcopy < /dev/null`.
3. Run `rotation-worker mint-bearer` in the VM (as the `rotation` user, with the configuration
   minus `bearer_secret_id`). It generates the bearer and stores it in `Rotation / Work` as
   `rotation-executor WORKER bearer`, then prints the secret id, the bearer's `token_hash` and its
   fingerprint, never the value. In the console, move that secret to `Rotation / Executor`; a
   move keeps the secret id. Put the id in the config as `bearer_secret_id`.
4. Change the orchestrator's settings, both rows of each variable, in this order and with
   nothing dispatched in flight:
   1. Set `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID=rotation-executor`, restart, and confirm it
      boots. The setting alone boots.
   2. Add `"rotation-executor-1": {"agent_id": "rotation-executor", "token_hash": "<hash>"}` to
      `ORCHESTRATOR_M2M_CREDENTIALS`, restart, and confirm it boots. Boot refuses the credential
      without the setting, which is why the setting goes first. The executor needs no
      `ORCHESTRATOR_M2M_ROLES` entry: an unpromoted credential is a WORKER.
5. In Healthchecks, under the project's **Settings**, create a read-only API key, and store it in
   BWS as a secret in `Rotation / Healthchecks`, from the console.

### Rotate them by hand

- **The root token:** create a second access token on the machine account, store it with
  `set-root-token`, run one pass by hand to prove it, then revoke the old token in the console.
  A revoked token's cached sessions can last about an hour (ADR-0055 amendment 1), and the
  executor keeps none: every pass logs in afresh.
- **The WORKER bearer:** run `mint-bearer` again (it refuses while a bearer waits in
  `Rotation / Work`), move the new secret, then give the orchestrator the new hash as the
  credential's `token_hash` with the old one as `previous` (`until` at most seven days ahead),
  restart, point `bearer_secret_id` at the new secret, run one pass, then remove `previous`,
  restart, and delete the old secret.
- **The Healthchecks key:** create a new read-only key, replace the BWS secret's value in the
  console, then delete the old key in Healthchecks.

