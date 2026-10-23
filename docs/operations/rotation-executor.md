# The rotation executor

ADR-0055. `src/rotation_worker/` is the credential-rotation executor: a deterministic program with
no LLM that claims approved rotation units with its own WORKER identity, works their steps through
one contract, and files `rotation-step/1` evidence. It runs as a service in the isolated OrbStack
VM `rotation-executor`, listens on no port, and is the only place a rotated value exists.

## How it decides what to do

Each pass, started by a systemd timer every five minutes, works every ready unit whose envelope
`constraints` name a `rotation_adapter`:

1. **It checks the unit against git before claiming it** (`checks.py`). The package revision's
   commit must be on intent-packages' `main`, and the package read there at
   `packages/<package_id>/package.yaml` must hash to the approved content hash. The intake's
   `source_path` is the directory on the machine that emitted it, so the executor never reads it. The
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

- `required_capability: operational_action`, and `max_attempts` of three. Any other value is
  outside the rotation pattern, so the unit needs its own authority approval.
- Envelope `constraints`:
  - `rotation_adapter`: the adapter's name, for example `fake`.
  - `rotation_unit`: the unit's name in the adapter's plan, for example `mint`.
  - `destinations`: the package's list, exactly (`rotation-worker compare-destinations` prints
    it).
  - `touches_no_hosted_service: true`, only on a unit that writes no hosted consumer.
  - `host_without_overlap: true`, only on the `orchestrator` adapter's `mint` unit, and only when
    the old value is another entry's current one (see "Host without an overlap").
- `ac_mappings`: each step files against its own acceptance criterion, the `ac_id` the adapter's
  plan names, so the package declares one criterion per machine step.
- `dependencies`: each unit after the first depends on the one before it with
  `kind: work_unit`, `required_state_or_condition: completed`. Each human act in the plan is an
  `external_system` dependency on the unit after it, resolved at `/review` with the fingerprint
  the act produced.

Once the decomposition is approved, a unit needs no authority approval of its own when the
`rotation_known_good` pattern in `factory-policy.toml` recognises its envelope (ADR-0055
amendment 11). The pattern covers the units of the adapters the approved packages use, with
exactly the constraints in the preceding list, no capability but `operational_action` and at most
three attempts. It applies only to a unit `is_rotation_unit` accepts. It never covers
`host_without_overlap`, the `fake` or `change-manager-attested` adapter, or anything else it
doesn't name, so such a unit
still needs **Approve this authority envelope** on its `/review` page; until then,
`/work-units/{id}/readiness` reports `authority_not_approved`. A claim on a unit nobody approved
asks policy again: it's refused `authority_approval_missing` if the pattern no longer recognises
the envelope (`authority_policy_unreadable` if the policy file can't be read), and otherwise
records an `authority.human_gate_not_required` event, attributed to the executor, naming the
pattern and the policy version.

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
ubuntu:noble rotation-executor`. It reaches the internet (BWS, `sds.alobar.net`, GitHub), other
OrbStack machines and the tailnet (production Coolify at `100.112.188.96:8000`), but not the Mac.

The Coolify path depends on the VPS's Tailscale node, `ubuntu-2gb-ash-1`, staying logged in to the
tailnet. Its key expiry is disabled. If a step fails with `Coolify PATCH got no answer:
ConnectTimeout`, check that node in `tailscale status` before anything else: when its key
expired, the node logged out and every write timed out before it connected (ADR-0055,
"The observer's first mint and an expired tailnet key").

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

   coolify_url = "http://100.112.188.96:8000"
   orchestrator_application = "eqj5l7k705fhi12x9i74fqf0"
   change_manager_application = "re45tafypao3nly3qa9a79dp"
   healthchecks_key_id = "8a41c80d-54a8-4b2b-aa93-b4e0012a99da"
   github_app_id = "5383095"
   github_app_key_id = "80c752aa-866c-43dc-a9e2-b4e80102d444"

   [admin_secrets]
   openrouter = "<the OpenRouter management key's secret id, in Rotation / Admin>"
   orchestrator = "<the Coolify token's secret id, in Rotation / Coolify>"
   change-manager = "<the same Coolify token's secret id>"
   ```

   `admin_secrets` maps a provider name to the BWS secret id of that provider's admin
   credential. An adapter that needs one refuses to build without its entry, and reads the secret
   only when a step mints, hosts or revokes. The `orchestrator` provider's admin credential is the
   Coolify token, because writing Coolify is how it changes which bearers the orchestrator
   accepts. `orchestrator_application` is the orchestrator's Coolify application uuid, the only
   application that adapter may write whatever a package names. The `change-manager` provider's
   admin credential is the same token, and `change_manager_application` bounds it the same way. `coolify_url` is production
   Coolify's tailnet address: the Coolify client sends its
   token only over TLS or to a tailnet address, and production Coolify has no TLS (ADR-0055
   amendment 9). `healthchecks_key_id` is the read-only Healthchecks key in
   `Rotation / Healthchecks`, read only when a scheduled reader's check is (the following
   "Scheduled readers" section). `github_app_id` and `github_app_key_id` name the rotation
   GitHub App and its private key in `Rotation / Admin`; they come together, and the executor
   reads the key only when a step writes an Actions secret or a wait reads a report ("Actions
   secrets").

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

All six are in ADR-0055's hand-rotated set: a person creates and rotates them, never the
executor. None is ever printed, passed as an argument, or kept on the Mac.

| Credential | Lives in | Read by |
|---|---|---|
| The root: the `rotation-executor` BWS machine account's access token | `/etc/rotation-worker/root-token` in the VM, owner `rotation`, mode `0400` | The executor, to log in to BWS |
| The executor's WORKER bearer | BWS secret `rotation-executor WORKER bearer` in `Rotation / Executor`; its hash in the orchestrator's `ORCHESTRATOR_M2M_CREDENTIALS` under key `rotation-executor-1`, agent `rotation-executor` | The executor, from BWS at each pass |
| The read-only Healthchecks API key | BWS secret `Rotation / Healthcheck` in `Rotation / Healthchecks`; its id in `config.toml` as `healthchecks_key_id` | The executor, from BWS, only while it reads a scheduled reader's check |
| The OpenRouter management key | BWS secret `openrouter management key` in `Rotation / Admin`; its id in `config.toml`'s `admin_secrets` | The executor, from BWS, only while an `openrouter` unit mints or revokes |
| The rotation GitHub App's private key (`alobar-sds-rotation`, App id 5383095) | BWS secret `rotation github app private key` in `Rotation / Admin`; its id in `config.toml` as `github_app_key_id` | The executor, from BWS, only while it writes an Actions secret or reads a report |
| The production Coolify token (`read`, `write`, `deploy`; not `read:sensitive`) | BWS secret `rotation-coolify-token` in `Rotation / Coolify`; its id in `admin_secrets` as `orchestrator` and `change-manager` | The executor, from BWS, only while an `orchestrator` or `change-manager` unit hosts or retires |
| The BWS projects | Bitwarden Secrets Manager | The root: write on `Rotation / Work` and `Rotation / Keepers`, read on `Rotation / Admin`, `Rotation / Coolify`, `Rotation / Executor` and `Rotation / Healthchecks`, nothing else. A consumer account: a direct read grant on each keeper secret it consumes, nothing else in `Rotation / Keepers` |

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
6. Create the projects `Rotation / Keepers` and `Rotation / Admin`. On the machine account's
   **Projects** tab, give it **Can read, write** on `Rotation / Keepers` and **Can read** on
   `Rotation / Admin`. Every rotated credential's keeper and every provider's admin key go in these
   two, so a new provider needs no new project (ADR-0055 amendment 6).
7. For each provider whose adapter needs an admin credential, create it in the provider's console
   and store it in BWS in `Rotation / Admin`, straight from the provider's page to the Bitwarden
   page, then clear the clipboard with `pbcopy < /dev/null`. Put the secret's id in `config.toml`
   under `[admin_secrets]`, keyed by the adapter's name, and confirm the root reads it from the VM.
   For OpenRouter, that's a management key with no expiry (account-wide, a provider limit), stored
   as the secret `openrouter management key` under the key `openrouter`.
8. In production Coolify, under **Keys & Tokens**, create the token `rotation-executor` with
   **read**, **write** and **deploy** only. It is team-wide: Coolify tokens name actions, not
   resources (ADR-0055 amendment 1), so the executor's Coolify client is what bounds it to one
   application. Create the project `Rotation / Coolify`, store the token there as
   `rotation-coolify-token`, give the root **Can read** on the project, and put the secret's id in
   `admin_secrets` under `orchestrator`. Set `coolify_url` to the tailnet address.

### Move a keeper

Before a credential's first rotation, its keeper secret moves from wherever it lives into
`Rotation / Keepers`. A move keeps the secret id, so no consumer's `.bws-secrets.toml` changes.

1. For each consumer account, in the secret's **Machine accounts** tab, give it a direct
   **Can read** grant. Prove with a canary first, with an empty `HOME`: the account reads a
   directly granted secret in `Rotation / Keepers` and can't read a second one there.
2. Move the secret to `Rotation / Keepers` in the console.
3. Confirm from the VM that the root reads it and that its fingerprint is the registry's, and that
   each consumer account still reads it with only `PATH` and `HOME` set. Accounts that read it
   only through its old project lose access, which is the point of the move.

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
- **The Coolify token:** create a second token with the same three abilities, replace the BWS
  secret's value in the console, run one pass by hand, then delete the old token in Coolify.
- **The OpenRouter management key:** create a new management key, replace the BWS secret's value
  in the console (the secret id, and so `config.toml`, stays), then delete the old management key
  in the OpenRouter console. Do it while no `openrouter` unit is claimed.

## The orchestrator's bearers

The `orchestrator` adapter rotates a machine bearer the orchestrator issues (ADR-0055 increment 6
and amendment 9). The orchestrator stores each identity's bearer as a sha256 in
`ORCHESTRATOR_M2M_CREDENTIALS`, so the bearer's destination names the entry, its key id:
`coolify-env-hash … key="ORCHESTRATOR_M2M_CREDENTIALS" entry="orchestrator-operator"`.

Its plan has two units, each claimed only in the `live_estate` window (read its hours in
`factory-policy.toml`; ADR-0012 amendment 1 widens them for the build phase), because each
restarts the orchestrator:

- `mint` (AC-001 to AC-006): mint a value and stage it; host it, which rewrites the setting with
  the entry's new hash and the old one as `previous` (`until` seven days out), writes both
  `is_preview` rows, restarts, and waits until the orchestrator's identities route shows the new
  hash; probe the new value live; store it in the keeper; verify the consumers.
- `retire` (AC-007 to AC-009): verify the consumers again; confirm the entry's `previous` is the
  old value's hash, write the setting without it and restart; probe the old value dead.

The executor rebuilds the setting from what the running orchestrator loaded
(`GET /api/v1/m2m-identities`, which only it may read), never from a stored copy, and changes only
the approved entry. So a hand edit to the variable must be followed by a restart straight away,
or the executor's next rotation overwrites it. Before each restart it refuses while any unit
other than a rotation unit is live, or a ready unit was dispatched and not yet claimed, the same
check `scripts/deploy_orchestrator.py` makes.

It refuses an entry that is its own `credential_key_id`: the executor's bearer is hand-rotated.
It writes the preview row before the production row, and checks for live runs again between the
write and the restart. The check narrows, but can't close, the window in which a lane dispatches a
run before the restart lands.

### Host without an overlap

A host normally keeps the old hash as `previous`. The orchestrator refuses that when the old hash
is another entry's current hash (`identity/auth.py::previous_hash_valid`), which is the case while
two identities share one value. The observer and drift-reporter bearers are split this way
(ADR-0055 amendment 9, "Decision 3 revised"): the observer rotates first with no overlap, then
the drift-reporter rotates with the usual one.

- **It's declared, never inferred.** The `mint` unit's approved envelope sets
  `host_without_overlap: true`. The value must be a boolean, `false` is the ordinary overlap, and
  the checks refuse `true` on a unit with no host step. The `retire` unit needs no declaration.
  The `mint` unit carrying it is the one rotation unit whose authority envelope a person still
  approves.
- **The executor refuses it unless an overlap is impossible.** Host writes the new hash with no
  `previous` only if another entry in what the orchestrator serves holds the old hash as current;
  otherwise it refuses before writing (`m2m_setting.Change.REPLACE`). An undeclared host on such a
  rotation is refused too, for the boot rule.
- **The old value stops working for this key id at host's restart.** Authentication is per key
  id, so the identity that shares the value keeps accepting it. Each reader of this keeper fails
  from that restart until `store` writes the new value, then switches on its next run.
- **Retire writes nothing.** Its before-revoke probe finds the old value refused and files "the
  old value was already refused; nothing to revoke". Confirm-dead probes the old value as this
  key id, so it proves it dead while the sharing identity still accepts it, then deletes the
  staging and quarantine secrets. The sharing identity's own keeper still holds the old value.
- **There's no backstop and no `until`.** The retire wait logs `overlap_until` as null, and the
  paragraph on the backstop in "Scheduled readers" doesn't apply.

### Scheduled readers

A `runtime-fetch` reader on a schedule is verified by a read, not by a person (ADR-0055
amendment 9, decision 5). Its registry row names the read in parsed fields beside `note`:
`healthchecks_check` (the check's exact name) for a reader whose check fails on an empty value,
or `observation_source` and `observation_subject` for one that posts an observation each run.
`readers.py` holds what each read accepts. A reader counts as switched once a run that started
more than two minutes after the keeper's revision date shows up: a successful check whose
`last_ping - last_duration` is that late, or an observation whose `observed_at` is.

- **Retire waits unclaimed.** A unit that retires the old value stays ready, spending no attempt,
  until every scheduled reader has switched. Each pass logs `unit_waiting` with the readers it
  waits for and the overlap's `until`. Two readers run on Sundays only, so a wait can last a week.
- **The backstop may end the overlap first.** A mint that stores just after a Sunday reader's
  run waits about seven days for its next run, which can land after `until`. Devon chose on
  2026-10-16 that weekly readers still gate retire. If `until` passes first, the orchestrator
  stops accepting the old value on its own; retire then finds it already refused, files
  "the old value was already refused; nothing to revoke", and leaves the expired `previous` in
  the setting for the next host step to drop.
- **A reader the registry names wrongly is refused before the claim.** No check with that name,
  two of them, a subject with nothing posted in the last 15 days, or a check-read reader with
  no `healthchecks_key_id` configured fails the unit into the dead-letter report. A read that
  gets no answer only waits.
- **Each reader is read once per pass.** The wait, verify-consumers and retire-old judge the same
  readings, so a reader starting its next run between the wait and the claim can't fail the
  unit. A check that is mid-run answers from its last finished run; one whose last run failed
  waits for a success.
- **A plan with no human act refuses a reader with no read.** Nobody could attest it, so retire
  could never run.

To see what the reads answer, from the VM, without claiming anything:

```sh
orb -m rotation-executor -u root runuser -u rotation -- env -i PATH=/usr/bin:/bin \
  HOME=/var/lib/rotation-worker /opt/rotation-worker/venv/bin/rotation-worker read-consumers \
  orchestrator-observer-bearer --stored-at 2026-10-15T06:00:00+00:00 \
  --config /etc/rotation-worker/config.toml
```

It prints `switched`, `not yet` or `unreadable` for each reader, with the start of the latest run
it found. It exits 2 when any reader is unreadable.

### If a rotation stalls after host

After `mint` hosts the new value, the orchestrator accepts the old one only until `previous`'s
`until`, seven days later, but the keeper holds the new value only once `store` has run. A `mint`
unit that fails after host retries from where it stopped, re-deriving from BWS and the
identities route. While it has attempts left, the operator requeues it (`POST
/work-units/{id}/requeue`, as SYSTEM); `/review`'s retry is refused until they're spent. If it has
no attempts left, finish or undo it within the seven days:

- finish: authorise a retry (`/review`) and let the next pass store the staged value;
- undo: in Coolify, set the entry's `token_hash` back to `previous.token_hash`, delete `previous`,
  both rows, restart, then delete the staging secret from `Rotation / Work`.

Past `until`, every reader of the keeper gets 401 until one of those is done.

The store retries a 429 or 5xx from BWS for about three minutes, so a stall at `store` means BWS
kept refusing, not a passing burst. If a restart for an undo doesn't take effect (the container's
age doesn't change), check that the orchestrator's Coolify health check is off: with it on, Coolify
judges each new container unhealthy and keeps the old one.

After a host without an overlap there is no `previous` and no `until`: every reader of the keeper
gets 401 from host's restart until `store` runs, so finish or undo it straight away.

- finish: authorise a retry. The retry is claimed only in the `live_estate` window. It finds the
  entry already holding the new hash with no `previous` and writes nothing, then probes, stores
  and verifies as usual.
- undo: in Coolify, set the entry's `token_hash` to the `token_hash` of the entry that shares the
  old value (for the observer, `orchestrator-drift-reporter`), both rows, restart, then delete the
  staging secret. The keeper still holds the old value, so its readers work again. A hash isn't
  a secret, so this moves no value. Undo only while both hold:
  - `Rotation / Work` has no quarantine secret for the occurrence. `store` creates it before it
    writes the keeper, so once it exists the keeper may hold the new value, and the only
    recovery is a retry.
  - The sharing entry hasn't rotated since host: its keeper's revision date in BWS is earlier
    than host's evidence. Otherwise its hash is no longer the old value's, and copying it would
    make the two identities share a value again.

  Host refuses an entry that still accepts an earlier value as `previous`, so the old hash is the
  only one an undo restores.

### If the orchestrator doesn't come back after a rotation's restart

The executor refuses every setting the orchestrator's boot would refuse, so this needs a cause it
didn't foresee. The unit fails with "the restarted orchestrator didn't load the written setting in
time". The orchestrator is then down, not degraded: Coolify doesn't keep the old container when
the new one fails to start. Measured on a throwaway application on 2026-10-15, a restart after a
boot-breaking variable change removed the running container and left the new one restart-looping,
and writing the variable back and restarting again recovered it. In production Coolify, open the
orchestrator's `ORCHESTRATOR_M2M_CREDENTIALS` and undo the change by hand, in both rows:

- after `mint`: in the approved entry, set `token_hash` back to `previous.token_hash` and delete
  `previous`; after a host without an overlap, set it to the sharing entry's `token_hash`;
- after `retire`: nothing to undo for the old value, which is already retired; check the
  application's logs for the boot refusal instead.

Restart, and confirm `https://sds.alobar.net/health/live` answers. The keeper still holds the old
value after a failed `mint`, because store comes after host, so every consumer still works.

## Actions secrets

An Actions secret is write-only, so the executor writes it through a dedicated GitHub App and
verifies it through what a workflow in the same repository reports (ADR-0055 amendment 10).

- **The App** is `alobar-sds-rotation` (installation `173083952`). It holds Secrets: write,
  Actions: read and Metadata: read, has no webhook, and is installed only on the repositories that
  hold a registered Actions-secret copy: brain, change-manager, infraops-mcp-server,
  intent-packages, orchestrator and security-standards. A repository that gains a registered copy
  must be added to the installation (**Settings > Developer settings > GitHub Apps >
  alobar-sds-rotation > Install App**), or every write to it is refused.
- **A write** mints a token for the destination's one repository with exactly
  `{secrets: write, metadata: read}`, refuses unless GitHub's answer names that repository and
  those permissions alone, seals the value with the repository's public key, and counts only once
  the secret's `updated_at` moved. It writes nothing the approved destination doesn't name.
- **The report** is `.github/workflows/secret-fingerprint.yml`, byte-identical in every holding
  repository. It runs hourly with no permissions and prints `fingerprint <NAME> <sha256 prefix>
  <length>` for each secret. A new secret name in a repository means adding it to that file.
  The executor reads the newest successful run's job log with a token minted
  `{actions: read, metadata: read}`, following GitHub's one redirect to its log storage with no
  credential.
- **A copy counts as switched once its report shows the keeper's fingerprint.** A unit that
  retires waits unclaimed until every copy has switched, so a retire can wait up to an hour after
  store. A repository whose newest successful report is older than three hours, or that has none,
  refuses the unit before its claim: GitHub turns a schedule off after 60 days without repository
  activity, and a stopped report must be seen, not waited on. Re-enable it in the repository's
  **Actions** tab. A file GitHub never registered looks the same from here: if
  `gh api repos/<owner>/<repo>/actions/workflows` doesn't list it, push a change to the file.

To see what the reports say, `read-consumers` covers Actions copies too (it reads the keeper as
the value to compare).

## GitHub PATs and the Atlassian token

A person mints these in the provider's console; the executor does the rest (ADR-0055
amendment 10).

- **`github-pat`** (`factory-pr-token`, `github-finegrained-mirror`): a human act mints and
  stages, then `store` (AC-001 to AC-004: verify-new, store, distribute, verify-consumers) and
  `retire` (AC-005 to AC-007: verify-consumers, retire-old, confirm-dead). Neither writes a hosted
  service, so both may set `touches_no_hosted_service: true`. Retire revokes the old value by value
  at `POST /credentials/revoke`, sent with no credential, then confirm-dead rechecks `GET /user`
  until it answers 401 (measured: within six seconds). For a credential with Actions-secret copies,
  the `retire` unit waits unclaimed while a dispatched run is live, because a revoke fails that
  run's push; the revoke asks once more and refuses if a run started since.
- **`bitbucket`** (`bitbucket-mirror-token`): a human act mints and stages, then `store` (AC-001,
  AC-002), `verify` (AC-003), a human act that revokes the old token in the Atlassian console, and
  `confirm` (AC-004). The `verify` unit waits unclaimed until every reader has switched; revoke
  only after it completes.
- **`orchestrator-distributed`** (`orchestrator-factory-runner-bearer`): the orchestrator plan
  with distribute after store, AC-001 to AC-010, both units windowed.

### Mint and stage a value by hand

1. In the provider's console, create the token. For `factory-pr-token`, create a new
   fine-grained PAT, never a regenerated one (regenerating kills the old value at once): its
   repository list is every factory target and factory-runner, and its permissions are the current
   PAT's (at least Contents, Pull requests and Workflows read and write). For the mirror PAT, use
   the registry's `replacement_scope`.
2. In the Bitwarden console, in `Rotation / Work`, create the secret
   `rotation/<credential id>/<occurrence>/staging` and paste the value. Run `pbcopy < /dev/null`.
3. At `/review`, resolve the human act with a note. The fingerprint is optional: `store`'s
   verify-new proves the value works before anything depends on it.

## BWS machine tokens: the Keychain instance

A BWS machine access token lives in the Mac's login Keychain, which the VM can't reach, so a
second instance of the executor runs on the Mac and rotates these tokens (ADR-0055 amendment 12).
It runs the same code through the same contract, with its own orchestrator identity
(`rotation-executor-keychain`). It keeps its state in the Keychain instead of BWS:

- **Its store is `KeychainStore`** (`secret_store.py`), through `/usr/bin/security`. A secret's id
  is its destination string, `keychain service="…" account="…"`.
- **Its staging and quarantine items** are accounts `rotation/<credential>/<occurrence>/…` under
  the service its configuration names as `work_project_id`, and so is its bearer
  (`worker-bearer`).
- **Values travel only on stdin and stdout.** A value is written through `security -i`, and never
  appears in a process argument.
- **Items are updated in place with `-U`, never re-created,** so a reader never finds one missing.
  An update never restates the item's access list: macOS then asks a person to allow it, and the
  executor would wait for an answer that never comes (measured 2026-10-22).

Each instance takes only the units of adapters kept in its own store (`Adapter.store_kind`) and
leaves the other's units unclaimed. The orchestrator confines the two identities the same way.

### Install or update the Keychain instance

`scripts/install-rotation-worker-launchd.sh <40-character commit>`, from the main tree. It checks
the orchestrator out at that commit into `~/Library/Application Support/rotation-worker/src`,
builds the environment there from the lockfile, and writes the configuration. The configuration
holds names only. It loads the LaunchAgent `com.devon.rotation-worker` only once the bearer
exists. The agent runs a pass every five minutes and logs to
`~/Library/Logs/rotation-worker.log`.

First install, in this order, with nothing dispatched in flight:

1. Run the installer. It reports that no bearer exists yet.
2. Run `"~/Library/Application Support/rotation-worker/venv/bin/rotation-worker" mint-bearer
   --config "~/Library/Application Support/rotation-worker/config.toml"`. It creates the Keychain
   item `rotation-executor`/`worker-bearer` and prints its `token_hash` and fingerprint, never
   the value.
3. Change the orchestrator's settings, both rows of each variable:
   1. Set `ORCHESTRATOR_ROTATION_KEYCHAIN_EXECUTOR_AGENT_ID=rotation-executor-keychain`, restart,
      and confirm it boots.
   2. Add `"rotation-executor-keychain-1": {"agent_id": "rotation-executor-keychain",
      "token_hash": "<hash>"}` to `ORCHESTRATOR_M2M_CREDENTIALS`, restart, and confirm it boots.
      No roles entry is needed: an unpromoted credential is a WORKER.
4. Run the installer again. It loads the agent.

To update, run the installer with the newer commit.

### The plan

The `bws-machine-token` adapter (`adapters/bws_machine_token.py`) runs these segments in order:

1. **A human act creates the token and stages it.** In the web app, create a new access token for
   the same machine account. Then run `rotation-worker stage <credential> --occurrence
   <occurrence> --config <the Mac configuration>` and paste the token at the hidden prompt. The
   command checks the token's shape, writes the staging item, and prints only its fingerprint.
   Resolve the act at `/review` with that fingerprint.
2. **`store`** (AC-001 to AC-003) proves the new token with a fresh login. Then it quarantines the
   keeper's old value, updates the keeper, and updates every other Keychain item that held the
   old value.
3. **`verify`** (AC-004) reads every item back. It waits, unclaimed, until each scheduled reader
   has run since the switch, then proves the old token still logs in.
4. **A human act revokes the old token** in the web app. Revoke it only after `verify` completes,
   and resolve the act only after revoking: `confirm`'s 75 minutes count from the resolution.
5. **`confirm`** (AC-005) proves the old token refused and the new one live. Then it deletes the
   staging and quarantine items.

The probe is a fresh login: a new SDK client in a child process, with an empty throwaway `HOME`
and the token on stdin.
- A login answers 200.
- `invalid_client` answers 400, for a revoked token and a malformed one alike.
- Anything else stops the rotation.

The control is a well-formed random token. Measured 2026-10-22: a random token and a just-revoked
one both answered `invalid_client`, and a live one logged in.

A `keychain` destination names a service and an account, not a machine: it means the item on the
Mac this instance runs on. A token also kept on another machine needs its registry entry changed
before it's rotated, or the other copy keeps the old value.

Two Keychain states stop a rotation rather than corrupt it. An item whose value isn't printable
reads back hex-encoded and fails read-back. A second item with the same service and account in
another keychain is found by reads, while `-U` writes the default keychain. Remove the duplicate,
then requeue the unit.

### Waits

- **`store` isn't claimed between 02:00 and 07:35 New York time,** while the scheduled lanes read
  these tokens.
- **`store` isn't claimed while another rotation is mid-overlap** (`overlaps.py`): the broad token
  starts the scheduled readers other rotations verify by. Another rotation counts from the first
  claim of its unit holding `store` until its last unit completes or is cancelled, so a failed unit
  holds every token switch until someone requeues or cancels it. The log line `unit_waiting` names
  the units in `mid_overlap`.
- **`confirm` isn't claimed until 75 minutes after the revoke was recorded.** A session that
  logged in before the revoke keeps working. On 2026-10-22, a client that logged in at 12:45 UTC,
  just before a revoke, kept reading until 13:39 and was first refused at 13:40. That's about 55
  minutes after its login.

### If the old token is dead before `verify` completes

The Bitbucket and BWS-token plans' `verify` unit proves the old value still works before the
person revokes it. If it's already dead, because it was revoked early or expired, every attempt
fails with "the old value probed dead", and no later step can file the missing half. The new value
is already in the keeper, so the rotation has happened without its proof:

1. At `/review`, cancel the `verify` and `confirm` units with a reason that names the early revoke.
2. Confirm the new value works with the probe the adapter uses (a fresh login, or the provider's
   API), and that the old one is refused.
3. Delete the staging and quarantine items, or secrets, named
   `rotation/<credential>/<occurrence>/…`.
4. Record the rotation in the registry as usual, with a comment that the live-before half is
   missing.

## Held copies

A held copy is a value an application keeps in its own Coolify settings to call another service,
named by a `coolify-env-held` destination (ADR-0055 amendment 13). The only one is the
orchestrator's `ORCHESTRATOR_CHANGE_RECORD_TOKEN`, its copy of change-manager's read bearer:

`coolify-env-held instance="prod" resource_type="application" uuid="eqj5l7k705fhi12x9i74fqf0"
key="ORCHESTRATOR_CHANGE_RECORD_TOKEN"`

- **Distribute writes it**, both rows, and restarts the orchestrator. It asks whether a restart
  would strand a dispatched run before writing and again before restarting, and refuses if one
  would. A copy already reporting the new value isn't written again.
- **The orchestrator reports what it loaded** on `GET /api/v1/held-credentials`, readable only by
  the VM executor: each held variable's sha256 prefix and length. Distribute waits up to ten
  minutes for the new fingerprint, and verify-consumers counts the copy switched only while the
  report shows it.
- **Only the orchestrator reports,** so a held destination anywhere else is refused before the
  claim, as is a variable the report doesn't list. A new held setting needs a line in the route's
  `HELD_SETTINGS` and a deploy first.
- **The executor writes it with the production Coolify token** it uses for the orchestrator's
  bearers (`admin_secrets` entry `orchestrator`, with `coolify_url` and
  `orchestrator_application`). Without those, a unit with a held copy stays unclaimed and the
  pass logs `unit_not_worked`.
- **The plan needs a distribute step.** For change-manager's read bearer that is the adapter
  `change-manager-distributed`: change-manager's plan with distribute after store in `mint`,
  AC-001 to AC-010.

If distribute stops after writing and before the restart, the orchestrator holds the new value in
its settings and the old one in its process; change-manager accepts both until retire, so nothing
breaks. A retry sees the copy not loaded, writes both rows again and restarts. To undo, write the
keeper's old value (in the quarantine secret until retire) to both rows and restart, between
dispatched runs.

A unit with a held copy also waits unclaimed while a restart would strand a dispatched run, as
the pass logs (`restart_would_strand`): distribute runs after host and store, so a refusal there
would spend an attempt with the rotation half done.

## change-manager's bearers

The `change-manager` adapter rotates one of change-manager's four machine bearers (`full`,
`read`, `propose`, `observe`; ADR-0055 amendment 9, "The change-manager adapter"). Each bearer's
Coolify variable holds one JSON entry keyed by its scope: the bearer's sha256 and, during an
overlap, a `previous` hash with its `until` (change-manager's `docs/operations/bearer-settings.md`).
Its destination names the variable and the scope:
`coolify-env-hash … key="M2M_TOKEN_READ" entry="read"`.

Its plan is the orchestrator adapter's, AC-001 to AC-009, and both units are claimed only in the
`live_estate` window, because each restarts change-manager:

- `mint` (AC-001 to AC-006): mint and stage; host writes the variable as the new hash with the old
  one as `previous` (`until` seven days out), both rows, restarts, and waits until change-manager
  accepts the new value; probe it; store it; verify the consumers.
- `retire` (AC-007 to AC-009): verify the consumers; write the variable as the new hash alone,
  restart, and wait until the old value is refused; confirm it dead.

The adapter named `change-manager-attested` runs the same units with a human act between them, for
a bearer one of whose readers has no read: a person attests that reader at `/review` with the new
value's fingerprint. The `full` bearer needs it for `drift-audit.sh`, whose change-manager calls
only log a warning when they fail. The other three use `change-manager`.

What differs from the orchestrator's bearers:

- **Nothing reports what change-manager loaded.** The executor builds the value from the old and
  new values alone, and confirms each write by probing `GET /api/landing-policy`, which every
  scope may read: a random bearer gets 401, an accepted one 200. The probe shows that some scope
  accepts the value, not which. Both containers can answer during a restart, so a value just
  hosted or retired must answer as it now must three times running.
- **A bad value takes change-manager down.** It doesn't boot when any scope's `previous` equals any
  scope's current hash. The executor can't read the other variables, so it reads the other
  bearers' keepers (the registry's change-manager credentials on the same application) and
  refuses a host or retire whose old or new value is one of them. The registry must name exactly
  one keeper for each other variable, and all four keepers must be in `Rotation / Keepers`,
  readable by the root, before the first change-manager rotation; a sibling keeper it can't find
  or read fails host before anything is written. A hand edit in Coolify that no
  keeper holds is invisible to this check.
- **The restart waits for change-manager's clients.** Its clients are the scheduled readers of its
  four bearers, and several run inside the window. A unit that writes waits unclaimed while any
  reader the registry names with a `healthchecks_check`, for any change-manager bearer, shows as
  started in Healthchecks. Host and retire ask again before writing and before restarting, wait
  up to five minutes, then refuse. A reader with no check can't be asked: a restart at 03:00 can
  cut short `drift-audit.sh`'s change-manager sync, which costs that night's sync, not the run.
  Every wait in a step has a deadline, but together they can outlast the 15-minute lease; a
  lapsed rotation claim is released, never reclaimed, and the next claim re-derives where the
  rotation stands.
- **Host never runs without an overlap.** The four bearers hold four values, so the adapter
  refuses `host_without_overlap: true`.
- **The wait logs no `overlap_until`.** It reads the orchestrator's identities, which don't hold
  change-manager's entries; host's evidence time plus seven days is the backstop.

### If a change-manager rotation stalls after host

The same as the orchestrator's: a retry finds change-manager already accepting the new value,
writes nothing, and goes on to store. To undo instead, in Coolify set the variable to
`{"<scope>":{"token_hash":"<previous.token_hash>"}}`, both rows, restart, then delete the staging
secret. A value that doesn't parse grants nothing for that one scope and leaves the others
working; a rule broken across scopes stops change-manager booting, so read the entries of the
other three before restarting.
