# Postgres role password rotation: design

Devon approved the approach on 2026-10-30: a locked-down helper on the VPS, and the package revision
approval covering the outage. Everything else here is the builder's call, marked "Builder's call".

## Intent

A Postgres password exposed in an agent session had to be rotated by hand on 2026-10-30, because the
rotation executor has no Postgres type. Devon's requirement: "I just need the existing process to
work with PG passwords as well." A Postgres rotation follows the same plan shape, the same one click
(the package revision approval, ADR-0055 amendment 16), and the same `live_estate` change window.

## Goals

- `postgres-role` becomes a seventh credential type, derived by `plans.derive` with no fixed plan.
- The Authentik database password rotates end to end with no click except the revision approval.
- The registry row, generated package, decomposition, policy recognition and verification all work
  through the existing paths.

## Non-goals

- The 11 standalone Coolify databases. Coolify keeps their password in a field no API call sets, and
  their apps are expected to hold it inside a `DATABASE_URL`. That shape is a later increment.
- Overlapping passwords through role swapping (two login roles under one owner). It changes ownership
  in every database for a brief outage in a night-time window.

## Anti-goals

- A general shell, or a general `psql`, for the executor. The credential on the VPS must be unable to
  do anything but the helper's verbs on the allowlisted targets.
- Any path where a password reaches argv, a log, evidence, or a tool argument.
- A new human gate.

## Constraints from the existing system

- Postgres holds one password per role, so no overlap exists. `ALTER ROLE` refuses the old value for
  new connections at once. Open connections survive until the restart.
- `POSTGRES_PASSWORD` is read only at database creation: writing the env var doesn't change the role.
- `pg_hba.conf` trusts loopback and the local socket and has `host all all all scram-sha-256`, so a
  probe must connect to the container's non-loopback address or it proves nothing. Measured on the
  Authentik database, 2026-10-30.
- The executor VM reaches the VPS's tailnet address on port 22 and has an `ssh` client. The VPS has
  Python 3.12 and `permitrootlogin without-password`. Measured 2026-10-30.
- The Coolify token has no `read:sensitive`, so `PG_PASS` is written blind. A Coolify service env has
  one row, not the two an application has. `POST /api/v1/services/{uuid}/restart` answered 200.
- `alobar-id`'s compose has an outage-causing network rule; the service restart honoured it on
  2026-10-30 (`server` stayed on `coolify`).

## The helper

`sds-pg-rotate` is one root-owned, stdlib-only Python file on the VPS. Source lives in this repository
and is installed by a documented step. The executor reaches it over the tailnet with a dedicated ed25519
key whose `authorized_keys` line is `restrict,command="/usr/local/sbin/sds-pg-rotate" ssh-ed25519 ...`.
The client ignores any requested command in favor of the forced one.

Input is one JSON line on stdin. Output is one JSON line on stdout. Verbs:

- `set {target, password}`: if the password already authenticates, do nothing; otherwise run
  `ALTER ROLE` through `docker exec -i`, over stdin. Answers `{"changed": bool}`.
- `probe {target, password}`: authenticate over TCP to the container's non-loopback address. Answers
  `{"status": 200}` for accepted, `{"status": 401}` for `password authentication failed`, and
  `{"status": null}` for anything else.
- `loaded {target}`: for each consumer the target names, the fingerprint of the env var it loaded.
  Answers `{"fingerprints": {"<container>": "<sha256 first 12>"}}`.

Targets live in root-owned `/etc/sds-pg-rotate/targets.toml`, not in the request: database container,
role, database, and consumers as `container` and `env` pairs. An unknown target or verb is refused. The
password must match `^[0-9a-f]{64}$` before use, so it needs no quoting. No verb returns a password,
writes one to a log, or puts one in argv.

Builder's call: the fingerprint uses the format `rotation_worker.values.Fingerprint` compares with.
Builder's call: passwords reach the `probe` shell inside the container over stdin, with `read`.

## The executor

- **Type.** `TYPES["postgres-role"] = CredentialType(Capabilities(machine_mint=True,
  machine_revoke=True, hosted=True, overlap=False))`. `CLASS_TYPES["postgres-role-password"] =
  "postgres-role"`. `overlap` defaults to true and doesn't change the derived steps, so no existing
  plan or criterion moves.
- **Plan.** Derived with no special case: `mint` unit (mint, stage, host, verify_new, store,
  verify_consumers), then `retire` unit (verify_consumers, retire_old, confirm_dead).
- **Mint.** `secrets.token_hex(32)`.
- **Host.** Write the variable, run `set`, restart the service, wait for `loaded` to show the new
  fingerprint in every consumer. The order keeps Coolify ahead of the database, so a crash leaves a
  state the next attempt repeats safely. Host touches no BWS secret.
- **Probe.** `probe` beside a known-bad random value. Live is 200 and dead is 401.
- **Revoke.** Never reached: `_retire_old` files "already refused". If the old value answers live,
  the database was reset behind the rotation, so `revoke` raises `ContractError`.
- **Consumers.** `UnitRun._holds` answers a hosted consumer with `accepts(provider, new)`, which for
  Postgres shows only the database. Add a `Provider.consumer_loaded(consumer, new)` method; existing
  hosted providers return `accepts`, and this one also requires `loaded` and the registry row's
  `health_url` to answer 200.
- **Coolify client.** `CoolifyClient` takes `resource_type` from the destination, keeping `permits`
  exact. A service writes one row; an application keeps its two.
- **Connection.** A new `helper_client.py` runs the system `ssh` with `BatchMode=yes`, a pinned
  `known_hosts` entry and `IdentitiesOnly=yes`, sends the JSON on stdin, and keeps the key in a 0600 file
  in the state directory only for the call. It is the one new outbound path.
- **Config.** `pg_helper_key_id`, `pg_helper_host` and `pg_helper_known_host`, as `github_app_key_id`
  is. The key lives in BWS `Rotation / Admin`. The Coolify token stays in `admin_secrets` under
  `postgres-role`.

## The no-overlap decision (ADR-0055 amendment 17)

`host_without_overlap` stays as it is: a per-envelope declaration a person approves, refused unless
another identity shares the old value. A type that has no overlap is a different thing: the type
declares it, its host always replaces, and the generated package states the outage in plain words, so
the revision approval covers it. `rotation_known_good` adds `postgres-role` to its adapters in all
reach rows it needs, and still never recognises `host_without_overlap`. The generated `HOST` condition
for an `overlap=False` type reads "The hosted service replaces the old value with the new one, and
its dependents restart; they're unavailable until the restart finishes."

Bumping the policy is a release. `check_profile_budget_agreement.py` and the policy schema tests apply.

## The registry and the keeper

- A row `alobar-id-postgres` in infraops' `.cred-consumers.toml`, since `alobar-id` isn't a registry
  repository: `class = "postgres-role-password"`, `provider = "postgres-role"`, `rotated_by_sds = true`,
  `helper_target = "alobar-id"`, `health_url = "https://id.alobar.net/-/health/ready/"`, keeper
  `bws-secret`, consumer `coolify-env` (`resource_type = "service"`, key `PG_PASS`, `redeploy = true`).
- infraops' `cred-rotation.ts` table gains `postgres-role-password` with `executor: false`.
- The keeper moves from the Mac Keychain to `Rotation / Keepers` through a small `rotation-worker
  adopt-keeper` command run in the VM as the `rotation` user, reading the value on stdin. Then the
  Keychain item is deleted.

## Failure and recovery

Nothing requeues a failed unit by itself, and `/review` sits behind Authentik, so a host step must
not leave the service down and wait for a person.

- **Host undoes itself.** If `set`, the restart, or the `loaded` wait fails, host writes the old value
  back to Coolify, runs `set` with the old value, restarts, waits for `loaded`, and then raises
  `ContractError`. The unit fails with the service on the old value, the keeper unchanged, and staging
  still holding the unused value. The next attempt starts clean. This adds no gate.
- **If the undo fails too,** the unit fails and alerts, and recovery is `rotation-worker pg-restore
  <credential>`, run in the VM. It reads the keeper and repeats the same writes, so no value crosses a
  tool argument and nothing depends on Authentik. `BREAK_GLASS.md` and the runbook name it.
- **A failure after host** leaves a working service, so ordinary requeue applies.
- `sds_rotation` claims treat the failed unit as any other: a BWS token rotation waits on it as it
  waits on any unfinished overlap, until it is requeued or cancelled.

## Calls made after the builder check

- **Hours.** The `live_estate` window is the policy's (`00:00` to `23:59` during the build), per Devon's
  "same parameters". The generated package says the restart can happen at any hour in that window.
- **When it's due.** `postgres-role-password` has `maxAgeDays: Infinity` in infraops, matching
  `coolify-pg-password`: no scheduled outage. An exposure row or `rotate_requested` triggers it. The
  live proof uses `rotate_requested`, so `record-rotation` closes it with no manual step.
- **The old value's shape.** The value from the 2026-10-30 rotation is 64 lowercase hex characters,
  from `secrets.token_hex(32)`. `adopt-keeper` refuses any other shape for this class.
- **The Coolify write.** The 2026-10-30 PATCH and load were seen with `prod-coolify-api-token`, not the
  executor's token. The plan measures `rotation-coolify-token` on a throwaway service variable before
  the live run. A write that doesn't land fails `loaded`, and host undoes itself.
- **The Keychain item** is `Claude/AUTHENTIK_POSTGRES_PASSWORD`, created 2026-10-30, with no readers.

## Proof required

- Mutation-tested contract and helper tests, with every survivor reported.
- `test_types` and generated-package tests for the new type, with existing plans byte-unchanged.
- A helper test proving that a probe through loopback isn't what `probe` runs.
- A test that a failure at each host stage restores the old value and raises.
- A live first rotation of the Authentik password inside the change window, through the real package,
  decomposition, claim, and verifier, with no click but the revision approval.
