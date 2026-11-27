# The lane secrets broker

ADR-0055 amendment 30. `sds-lane-secrets-broker` is one Coolify resource on `prod-vps` that holds one
BWS identity, the machine account `lane-secrets-broker`, and hands each lane only its allowlisted
secrets over a Unix socket. A lane on it holds no BWS token. The program is
`scripts/secrets_broker.py`, its compose and allowlist are in `lanes/secrets-broker/`, and the lane
side is `sds_bws_value` and `sds_in_lane_container` in `scripts/sds-bws.sh` and `scripts/lane_run.py`.

Each lane has `/data/sds/broker/<lane>/broker.sock` on the host (socket mode 0600, directory 0750).
A lane's compose mounts only its own directory, read-only at `/run/sds-broker`, so the socket a
request arrives on is the lane's identity.

The broker lanes are the tables in `lanes/secrets-broker/allowlist.toml`: `inert-landing`,
`estate-landing`, `bump-driver`, `follow-up-mint`, `change-window`, `execution-watch`,
`landing-ledger`, `pin-watcher`, `unit-verifier`, `membership-check`, `deploy-watcher`,
`change-proposer`, `rotation-decomposer`, `rotation-proposer`, `work-carrier`, `bump-proposer` and
`revision-watcher`. A test
holds that set equal to the lane composes that map no token
(`tests/scripts/test_secrets_broker_allowlist.py`).

## Deploy the broker

1. **Create or chown the host directory first.** `/data/sds/broker` must exist on `prod-vps`, owned
   by the image's `orchestrator` user, before the broker's first start. Coolify or Docker may
   already have created it as a bind directory, root-owned, and then the broker can't make its
   lanes' directories. Create it, or chown an existing one, to the uid and gid the lane image gives
   `orchestrator`, then check it with `ls -ln`.
2. **Grant its account.** In the BWS web app, grant `lane-secrets-broker` read on each project that
   holds a lane-read secret (Devon, "Project by project"). It reads nothing else.
3. **Create the resource** from `lanes/secrets-broker/compose.yaml` without `instant_deploy`, as a
   lane's resource is created (ADR-0059, "Cut-over for one lane").
4. **Devon pastes the token.** Devon mints the account's access token in the BWS web app and pastes
   it into the resource's `SECRETS_BROKER_BWS_ACCESS_TOKEN` variable in Coolify, in the same sitting.
   Devon also stores the same token in a keeper secret in the BWS project `Rotation / Keepers`, as
   every lane token keeps a stored copy. The executor writes none of it, and the token appears in
   no transcript. Set the image tag
   variable `SDS_LANES_IMAGE_TAG` to the release that carries the allowlist.
5. **Start it and read its log.** Each lane in the allowlist gets a directory and a socket at start,
   and logs one `listening` line once its socket accepts connections. Check that every lane has its
   line before you deploy any lane on it.

The broker refuses to start, with one JSON error line and exit code 2, when `BWS_ACCESS_TOKEN` is
unset or empty, the allowlist is missing, unreadable, names a lane twice, or holds a malformed lane
name or UUID, or a lane's directory or socket can't be set up. The last names the lane and the
errno's name, for example `{"error": "lane 'inert-landing' can't start: EPERM"}` for a lane
directory the broker doesn't own. It is fail-fast: the first lane that can't start stops the
broker, so the lanes before it (they start in sorted order) log `listening` and close again.

## Move the broker to a new release

`deploy_orchestrator.py` has no `--lane secrets-broker`: the broker is no scheduled lane. Moving it
to a new image tag is one announced act through infraops, with the same two calls a lane's re-tag
writes: `coolify_update_service_env` setting `SDS_LANES_IMAGE_TAG` on `sds-lane-secrets-broker`
(`wogvks6obmvv9fdotlgnw7r3`), then `coolify_deploy` on it. `coolify_deploy` pulls the new tag
itself, and the deploy gate pass admits it on the broker, which `.claude/hq-gate-pass.json` lists
(sds-hq #124), so an HQ cycle can run it. Never use `coolify_control` `restart` to change the tag:
a restart prunes images while starting (workspace invariant #3). A rollback is the same two calls
with the old tag.

While the broker restarts, which takes seconds, its lanes have no socket. A lane pass that starts in
that gap fails its read and runs again on its next schedule. Afterwards, check the broker's log for
one `listening` line per allowlisted lane, and run one lane's `lane-run --read` before you call the
move verified.

## Rotate the broker's token

ADR-0055 amendment 34 (D-81 (2) B, D-83). The broker's token, row `secrets-broker-bws-token` in
`.cred-consumers.toml`, rotates through the rotation executor's `bws-lane-token` adapter, with the
loaded-fingerprint proof. Devon mints and revokes; the executor binds, stores, writes the variable
and proves; a session restarts the broker. The row's `created` is the age anchor, so the proposer
generates a rotation plan, and Devon approves each package revision by name: no standing approval
covers a rotation plan. The row is `rotated_by_sds = true`: the proposer and the executor act only
on a handed-over row (`src/rotation_worker/checks.py`), so "Run a rotation" follows that commit.

### Install the helper target

Do this once, as root on `prod-vps`, before the first rotation. The rotation executor's VPS helper
reads the broker's loaded token only through a target in its root-owned
`/etc/sds-pg-rotate/targets.toml` (`docs/operations/rotation-executor.md`, "Install the helper").

1. Confirm the container's name: `docker ps --format '{{.Names}}' | grep '^secrets-broker-'` must
   print exactly `secrets-broker-wogvks6obmvv9fdotlgnw7r3`. If it prints another name, stop: the
   row's derivation (`<service>-<resource uuid>`) is wrong for the broker.
2. Append the consumers-only target to `/etc/sds-pg-rotate/targets.toml`, keeping the file
   root-owned and mode 0600. The `env` is the name the container loads (`BWS_ACCESS_TOKEN`,
   `lanes/secrets-broker/compose.yaml`), not the Coolify key:

   ```toml
   [target.secrets-broker]

   [[target.secrets-broker.consumer]]
   container = "secrets-broker-wogvks6obmvv9fdotlgnw7r3"
   env = "BWS_ACCESS_TOKEN"
   ```

3. Read it: `echo '{"verb": "loaded", "target": "secrets-broker"}' | /usr/local/sbin/sds-pg-rotate`
   answers one consumer, `secrets-broker-wogvks6obmvv9fdotlgnw7r3/BWS_ACCESS_TOKEN`, with a
   non-empty fingerprint (the sha256's first 16 hex characters, a colon and the length, never the
   value). That is amendment 34's first exit check.

### Run a rotation

1. **Approve the revision** (Devon). The proposer generates the package's revision when the token
   is due; Devon approves it by name.
2. **Mint** (Devon). In the BWS web app, Devon creates a new access token for machine account
   `lane-secrets-broker` and pastes it into the inbox (`Rotation / Inbox`), never into the Coolify
   variable or the keeper. The old token stays live.
3. **Bind** (executor, before the claim). The inbox binds the pasted token only when a fresh login
   accepts it, what it sees is exactly the row's declared reach, and no other lane token's keeper
   holds it. A token that doesn't bind stays in the inbox and nothing is written.
4. **`store`** (executor). `verify_new`, then the keeper write and readback, then one `PATCH` of
   `SECRETS_BROKER_BWS_ACCESS_TOKEN` on service `wogvks6obmvv9fdotlgnw7r3`, with no restart. The
   old value moves to the rotation's quarantine secret in `Rotation / Work`. The broker keeps
   running on the old token. The claim waits for the `live_estate` window.
5. **Restart** (session). After `store` completes, and well inside the `verify` unit's 48-hour
   clock:
   1. Read the self-hosted Healthchecks listing for every broker lane's check, named by
      `LANE_CHECKS` in `scripts/deploy_orchestrator.py` (the change window's is
      `Change Manager OPs Check`). Hold while any of them shows `started`, as the deploy command
      waits for a lane's quiet slot.
   2. Don't restart near the change window's daily 04:00 New York `--submit` pass
      (`lanes/change-window/crontab`): its broker read is fatal to the pass.
   3. Announce the act, then run infraops `coolify_deploy` on `wogvks6obmvv9fdotlgnw7r3` at the tag
      the broker already runs (read `SDS_LANES_IMAGE_TAG`; don't change it). This is the second
      call of "Move the broker to a new release". Never use `coolify_control` `restart` (it prunes
      images while starting, workspace invariant #3), and never `deploy_orchestrator.py --lane`,
      which has no broker lane.
   4. Check the broker's log for one `listening` line per allowlisted lane, and run one lane's
      `lane-run --read`; it prints `ok`.
6. **`verify`** (executor). The unit waits unclaimed until the helper's `loaded` for
   `secrets-broker` equals the keeper's fingerprint, then claims and reads it once more. Its
   completion sends the "Revoke the old … value" alert. No revoke alert is sent before that match,
   so the old token is never revoked before the broker holds the new one (Infra Brain 1032, R10).
7. **Revoke** (Devon). In the BWS web app, Devon revokes the old token.
8. **`confirm`** (executor). `confirm_dead` proves the old token refused, then deletes the
   quarantine and staging secrets.

While the broker restarts, which takes seconds, no lane has a socket. A lane pass that starts in
that gap fails its read and runs again on its next schedule.

### A failed start

D-83 12 A: no machine rollback; a failed start is fixed forward. If the broker can't read with the
new token, it exits 2 or answers `bws_failed`, and every broker lane's reads fail. The bind already
proved the token logs in and sees the declared reach, so a failed start means the token was revoked
since, or a Coolify fault. Devon mints again and the rotation is retried; the broker lanes' reads
fail until that rotation completes. The adapter has no restore verb. Until `confirm`, the old value
stays in the rotation's quarantine secret.

### After a leak

After an exposure, Devon may revoke the old token first (amendment 19 item 10; Infra Brain 1032),
and the adapter accepts the old token refused. For the broker, the running token is then dead, so
every broker lane's reads fail (`bws_failed`) until `store` completes and the session restarts the
broker. Three things bound that outage: the executor's five-minute pass picks up the paste, the
`store` claim waits for the `live_estate` window, and the outage lasts at least until Devon mints
and pastes the new token. An exposure response can therefore need the hand route instead (D-81
(2) option A in `docs/superpowers/reports/broker-phase-c-adr-question-answered.md`: Devon pastes
the token into the variable and the keeper, and a session redeploys the broker), outside the
window. That's Devon's call at the time.

### What the root reaches through this keeper

D-83 10 A, accepted for the development phase and reopened with D-64 in the security phase. The
broker's keeper is in `Rotation / Keepers`, which the executor's root identity reads and writes.
So T1 includes this: whoever holds the root holds the broker's token, and through it every secret
the broker reads, the SYSTEM and VERIFIER bearers in `SDS Operator` included. The route adds no
grant: each rotation uses the token to list ids, reads no value with it, and writes it to the
broker's variable.

## Add a lane

All in one reviewed PR, as for any lane code:

1. Add `[lanes.<lane>]` to `lanes/secrets-broker/allowlist.toml`: every UUID in the lane's launcher
   `scripts/run-<lane>.sh`, including a conditional read, plus the ping-URL secret named by
   `SDS_DEADMAN_PING_URL_UUID` in its compose, each with its registry name. A test holds the list
   equal to those.
2. In the lane's compose, map no `BWS_ACCESS_TOKEN` and mount
   `/data/sds/broker/<lane>:/run/sds-broker:ro`, and nothing else under, at or above
   `/data/sds/broker`. A token lane mounts none of it.
3. In the launcher, test `sds_in_lane_container` for the host check and the empty-token check.
4. Check that the broker's account reads the BWS project of every listed secret, or holds a
   single-secret read grant on it. ADR-0059 Amendment 17 decides the second form for the bump
   driver's three secrets in `Factory Events` and `Brains - Contributor`: one single-secret grant
   each to `lane-secrets-broker`, not a project grant. Devon gave them at HQ gate G-84
   (2026-11-21), and the lane's `lane-run --read` proved them at G-86. The registry's
   `secrets-broker-bws-token` row lists every grant the account holds in `reach_projects` and
   `reach_secrets`.

Then the release ships, and a session moves the broker to it ("Move the broker to a new release"),
which creates the lane's directory and socket; its `listening` line shows it. Deploy the lane's
resource only after that, so Docker never creates the bind directory as root. Each broker start
re-binds the sockets, and a lane pass that runs while the broker is stopped fails one read and runs
again on its next schedule.

A broker lane's `--add` run finishes with `--token-present false`; the deploy checks read the lane
as one only when its compose maps no token, mounts the exact bind above and nothing else of the
broker's directory, and the allowlist at the run's commit lists it (`broker_lane` in
`scripts/deploy_orchestrator.py`). Every planned change (`--add`, `--tag`, `--token`) refuses a
compose with any other route to `/data/sds/broker` on any service: a stray mount, `volumes_from`,
a privileged service, a mount of the Docker socket, or a named volume whose `driver_opts` device
reaches it (`compose_broker_refusals`). A test holds every tracked lane compose to the same rule.
A `--token` run on a broker lane is its recorded read proof ("Prove a lane's reads").

## Prove a lane's reads

In the lane's container, `lane-run --read <uuid>...` asks the socket for each UUID and prints
`<uuid> ok` or `<uuid> refused`, never a value. Run it over every listed secret (each `ok`) and over
one UUID outside the list (`refused`). A mounted socket is never bypassed for `bws`, so a broken
broker shows as `refused`, with one of two messages on stderr:

- "no value from the secrets broker for <uuid>": the socket is there but gave no value. Either it is
  stale (nothing listens: the broker is down or restarting) or the broker answered with an error.
- "broker socket missing at /run/sds-broker/broker.sock": the mount directory exists but holds no
  socket, because the broker never set up this lane's directory. A launcher run in this state
  starts nothing and exits 2 with the same message.

A lane's recorded read proof is a `--token` run (ADR-0055 Amendment 32 item 10), which the
revoke of a lane token needs (deploy.md, "Before a lane token's revoke"). On a broker lane the
run writes no compose and no variable and makes no redeploy:

1. `lanes-prepare --lane <lane> --token --running-tag <tag> [--running-volume <volume>]` plans
   the reads over the lane's table in `lanes/secrets-broker/allowlist.toml` at the commit the
   lane runs, sorted, plus one control outside the table: estate-landing's ping URL
   `11e940d7-e43a-4ebd-9026-b508017d9cef`, or inert landing's `35557473-f80c-44e3-892a-b507013158b1`
   for a lane whose table lists the first. It refuses a lane whose table lists both.
2. `lanes-recheck`, then the printed reads of the running container through `vps_exec`: the
   digest, the schedule, the state mount, the token-present read, and
   `C=$(docker ps -q --filter name=<resource uuid>); <exactly-one check>; docker exec "$C" lane-run --read <uuid>... <control>`,
   which exits 1 because the control reads `refused`.
3. `lanes-finish --lane <lane> --token <reads> --token-present false --read-result <file>`. The
   run file records `read_uuids`, `control_uuid` and status `finished`. Any listed UUID not `ok`,
   a control not `refused`, a missing line, or a `true` token read fails it, and
   `lanes-finish --restored` closes it `closed_failed` with those mismatches as its reason.

HQ's deploy gate passes no `lane-run --read`, so an HQ cycle can't run step 2's read: a session
runs it in the revoke sitting's gate, approved there, as Amendment 32 step 7's proofs were.

## Read its log

The broker writes one JSON line per lane at start and one per request to stdout: `lane`, `uuid`
and `outcome`, never a value. Read it through the resource's logs. The outcome is `listening` (the
start line, with `uuid` null: the lane's socket accepts connections), `ok` for a served read, and
otherwise `not_allowed`, `bad_request` or `bws_failed` (the codes a lane sees) or `internal` (the
broker failed on a connection, and the lane sees no reply).

## Failure modes

- **The broker won't start.** Read its one error line. An unset or empty token, a bad allowlist, a
  missing `bws` and a lane that can't start each exit 2. Fix the variable, the allowlist or the
  named lane's directory's owner; don't start it by hand.
- **A lane read fails `not_allowed`.** The UUID isn't in the lane's allowlist entry. Add it through a
  PR and a release.
- **A lane read fails `bws_failed`.** The broker's account can't read the secret: its project grant is
  missing, or its token is revoked. The lane's dead-man check and alerts fire as for a failed `bws`
  read.
- **A lane read fails `no_reply`.** The socket file is there but stale (nothing listens: the broker
  is down or restarting), or the read timed out. Check that the broker is running and has logged the
  lane's `listening` line.
- **The dead-man log says "could not be read through the secrets broker (<code>)".** The lane's
  ping-URL read failed through the socket with that code (`not_allowed`, `bws_failed`, `no_reply`
  and so on); read it as the matching entry above.
- **lane-run fails "broker socket missing".** The lane's mount directory holds no socket: the
  broker hasn't set up that lane (it isn't in the allowlist of the broker's running release, or the
  broker hasn't started). No launcher runs. Move the broker to the release that lists the lane.
- **A lane read fails `bad_request` or `bad_reply`.** The request or reply was malformed. This points
  at a mismatch between the release's read path and the broker's.
- **The lane's directory is root-owned.** Docker created it before the broker did. Stop the lane,
  remove the directory, restart the broker, then deploy the lane.

## What the broker doesn't change

No Amendment 32 lane reads with its own token: every lane on the VPS reads through the broker,
and every Mac launcher runs unchanged. The broker lanes keep their `<LANE>_BWS_ACCESS_TOKEN`
Coolify variables and token rows until the lane tokens are revoked (ADR-0055 Amendment 32, item 5),
which waits for each lane's recorded read proof ("Prove a lane's reads").
The `bump-proposer` machine account also keeps the Mac's Keychain token `bws-bump-proposer-token`
after the revoke (D-81 (6)).

The broker's own token rotates through the executor ("Rotate the broker's token"). It is in the
unlock tier (ADR-0055 amendment 34 item 1, D-81 (1) A, D-83 11 B), which names two credentials:
the broker's token, `secrets-broker-bws-token`, and the executor's root token, since anything
holding either reads for others. The tier is a name with no mechanism: each mint, grant and revoke
of the broker's token is Devon's act in the BWS web app, and the root token stays hand-rotated.
The registry row's comment is the tier's mark.
