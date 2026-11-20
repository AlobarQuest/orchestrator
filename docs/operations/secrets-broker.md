# The lane secrets broker

ADR-0055 amendment 30. `sds-lane-secrets-broker` is one Coolify resource on `prod-vps` that holds one
BWS identity, the machine account `lane-secrets-broker`, and hands each lane only its allowlisted
secrets over a Unix socket. A lane on it holds no BWS token. The program is
`scripts/secrets_broker.py`, its compose and allowlist are in `lanes/secrets-broker/`, and the lane
side is `sds_bws_value` and `sds_in_lane_container` in `scripts/sds-bws.sh` and `scripts/lane_run.py`.

Each lane has `/data/sds/broker/<lane>/broker.sock` on the host (socket mode 0600, directory 0750).
A lane's compose mounts only its own directory, read-only at `/run/sds-broker`, so the socket a
request arrives on is the lane's identity.

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
to a new image tag is a session act through infraops: set `SDS_LANES_IMAGE_TAG` on the resource,
then stop it, `docker pull` the new tag, and start it. Never change the tag with a restart, which
prunes images while starting (workspace invariant #3); a rollback re-pulls the old tag the same way.
Read its log for each lane's `listening` line afterwards.

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
4. Check that the broker's account reads the BWS project of every listed secret.

Then the release ships, and a session moves the broker to it ("Move the broker to a new release"),
which creates the lane's directory and socket; its `listening` line shows it. Deploy the lane's
resource only after that, so Docker never creates the bind directory as root. Each broker start
re-binds the sockets, and a lane pass that runs while the broker is stopped fails one read and runs
again on its next schedule.

A broker lane's `--add` or `--token` run finishes with `--token-present false`; the deploy checks
read the lane as one only when its compose maps no token, mounts the exact bind above and nothing
else of the broker's directory, and the allowlist at the run's commit lists it (`broker_lane` in
`scripts/deploy_orchestrator.py`). An `--add` refuses any other mount reaching `/data/sds/broker`,
on a token lane or a broker lane (`stray_broker_mounts`), and a test holds every tracked lane
compose to the same rule. A `--token` run on a broker lane still assumes a token
(`.bws-secrets.toml` reads and a `--read` proof). That is open until Phase B.

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
- **lane-run fails "broker socket missing".** The lane's mount directory holds no socket: the
  broker hasn't set up that lane (it isn't in the allowlist of the broker's running release, or the
  broker hasn't started). No launcher runs. Move the broker to the release that lists the lane.
- **A lane read fails `bad_request` or `bad_reply`.** The request or reply was malformed. This points
  at a mismatch between the release's read path and the broker's.
- **The lane's directory is root-owned.** Docker created it before the broker did. Stop the lane,
  remove the directory, restart the broker, then deploy the lane.

## What the broker doesn't change

The 12 lanes already on the VPS, and every Mac launcher, keep their tokens and run unchanged. The
broker's own token is the one BWS token that stays manual until Phase C; its registry row, added at
the deploy step once the resource and its keeper secret exist, is `rotated_by_sds = false` until then.
