# Handoff: BWS machine tokens (ADR-0055 increment 8)

Increment 8 brings the BWS machine tokens into the rotation system. Unlike every class before it,
each step stays human: Devon mints the replacement access token, puts it where its consumers read
it, revokes the old one and confirms it dead, and the executor only records the evidence (ADR-0055,
"Per-class disposition", and amendment 1's "BWS confirm-dead logs in fresh"). The reason is
decision 4: a machine unit would put live consumer BWS tokens inside the executor's reach.

So this handoff builds no adapter that mints or writes. It inventories the tokens, settles with
Devon how an all-human rotation is recorded, builds what that decision needs, and installs it. It
doesn't rotate, revoke or move anything.

HQ is the session driving the rotations in progress. Report to Devon; questions marked **Stop and
ask** go to him before you build past them.

## Rotations are in progress: don't disturb them

The executor reads each registry live from its repository's `main` and parses the whole file, and
it runs every five minutes on the VM. These units are in flight as of 2026-10-20:

- `a7649b61-9393-55e9-89f3-9fb5451dbec0`, the observer bearer's mint, requeued for its last attempt;
- `e1430e36-95f4-565e-8ec3-3d06a31a32fe`, the change-manager observe bearer's mint, in review;
- `d688930b-6b2b-57d5-a091-2c7b2c3a1888` and `a5465f19-0562-5c38-9d48-9967507e75a8`, the retire
  units that follow them.

Until HQ, through Devon, says no rotation unit is ready or claimed, don't:

- reinstall, reconfigure or run a pass of the rotation executor (`orb -m rotation-executor`);
- merge to any repository's `.cred-consumers.toml`;
- deploy, restart or change the settings of the orchestrator (`sds.alobar.net`, Coolify
  application `eqj5l7k705fhi12x9i74fqf0`), and above all not `ORCHESTRATOR_M2M_CREDENTIALS`;
- read, write or move anything in BWS project `Rotation / Work`;
- act on the units listed, or on their change records;
- run `scripts/run-rotation-proposer.sh --submit` or `scripts/run-work-carrier.sh --register` by
  hand;
- make any BWS call at all. A 429 at `store` caused the 2026-10-20 outage (ADR-0055, "The
  observer's second mint, a BWS rate limit and a re-enabled health check"), and your calls share
  BWS with the executor's.

Never do any of these between 02:00 and 06:00 America/New_York, the `live_estate` window, even once
HQ has cleared them. Prepare changes in your worktree and pull requests, and ask Devon when they can
land.

## Read first

- `docs/superpowers/plans/2026-10-15-handoff-common-rules.md`: its **How to work** and **Closing
  steps, for every handoff** sections apply in full. Its **Don't touch what the rotation touches**
  section is replaced by the preceding "Rotations are in progress" section.
- ADR-0055 (`docs/decisions/0055-*.md`): decisions 3, 4, 9 and 10; the consumer-kind table under
  "How each consumer kind is verified"; "Per-class disposition"; increment 8; amendment 1 in
  full, especially question 4 and "BWS confirm-dead logs in fresh"; amendment 10, as the pattern
  for recording an increment's decisions; and the two incident sections dated 2026-10-19 and
  2026-10-20 under amendment 9.
- `docs/operations/rotation-executor.md`, all of it, and `docs/operations/credentials.md` on the
  BWS identities and why a launcher needing two of them reads each Keychain item into its own
  variable. `docs/operations/rotation-proposer.md` on `rotated_by_sds` and what the proposer does
  with it.
- `~/Projects/CLAUDE.md`, the invariants on `bws` caching a session per access token (check with
  an empty `HOME`) and on `--color no`; and this repository's CLAUDE.md on the human-act
  dependency, resolved only at `/review`.
- `src/rotation_worker/contract.py` (step kinds, actors, `validate_plan`, `_validate_counts`,
  `_validate_order`, `_validate_segments`, `ATTESTED_KINDS`), `checks.py` (`_approved_package`,
  the registry checks before it, `_check_destination_fit`, `_check_human_retire_verifiable`),
  `readers.py`, `evidence.py`, `worker.py`, and `adapters/change_manager.py`, whose
  `change-manager-attested` plan is the nearest existing human act.
- `src/orchestrator/kernel/secret_metadata.py` (`SECRET_KEY_PARTS` and how it matches).
- `docs/superpowers/plans/2026-10-19-rotation-increment-7-report.md`, for how the last increment
  was reported.

## Handling values

These apply to every step, on top of the common rules:

- Never print, paste or pass a value as a tool argument or a process argument. That includes
  `bws --access-token` and `env BWS_ACCESS_TOKEN=… bws` (the pattern `scripts/sds-bws.sh` uses).
  A command that handles a token reads it from Keychain inside its own process and hands it to a
  fresh SDK client there (ADR-0055 decision 3, R1; amendment 1). If it calls the `bws` CLI for a
  parsed answer, it passes `--color no`.
- `security find-generic-password -w` appears only inside the one process that hashes or uses the
  value, never in a shell where its output reaches the transcript.
- Never run `bash -x` or `set -x` on a script that sources or reads a token, including
  `scripts/sds-token.sh`, `scripts/sds-deadman.sh` and infraops' `drift-audit.sh`.
- Search for token names, never contents: `grep -l` or `grep -o` on the variable name. Never print
  matching lines, `env`, `docker inspect` or `/proc/*/environ`. Don't search the executor VM's
  `/etc/rotation-worker`, which holds the root token. On the VPS and OrbStack machines, use
  `ssh orb` or `orb -m` with a names-only command, not `vps_exec`, whose output isn't filtered.
- Never call infraops tools that return values: the Coolify env-list tools,
  `coolify_get_deployment`, `cloudflare_list_pages_projects`.

## The tokens

Read each entry in the registry named; this list was read on 2026-10-20 and isn't to be trusted
for counts:

- `bws-sds-operator-token` (orchestrator's `.cred-consumers.toml`): the read-only `sds-operator`
  account, in Keychain item `Claude` / `BWS_ACCESS_TOKEN_SDS`, read by `scripts/sds-token.sh`, the
  scheduled launchers, infraops' `drift-audit.sh`, and project-standards' portfolio scan
  (factory-runner's registry).
- `bws-tok-ops-mini-20260730` (infraops-mcp-server's registry): the broad `Developer Machines`
  account's token, held in three Keychain items, `BWS_ACCESS_TOKEN_INFRA_DRIFT`,
  `BWS_ACCESS_TOKEN_INFRAOPS` and `BWS_ACCESS_TOKEN_VPS_BACKUP`. Many lanes read it, HQ's own
  scripts and `scripts/sds-deadman.sh` among them. `BWS_ACCESS_TOKEN_INFRAOPS` is read once, by the
  infraops MCP server's `start.sh`, so its holder sees a new value only after a restart.
- `bws-tok-content-mini` (infraops-mcp-server's registry): `BWS_ACCESS_TOKEN_VIDEOCREATOR`, read
  by VideoCreator's `start.sh` at boot, so it too changes only on a restart.
- `bws-cred-rotation-token` (infraops-mcp-server's registry): `BWS_ACCESS_TOKEN_CRED_ROTATION`,
  read only by infraops' 04:00 rotation window, which increment 9 retires.

Not registered as credentials, as of 2026-10-20: the Keychain items
`BWS_ACCESS_TOKEN_BRAIN_OPS`, `BWS_ACCESS_TOKEN_CLAUDE_CODING` and
`BWS_ACCESS_TOKEN_LISTING_PREP`, and the machine account the github-to-bitbucket mirror uses
from its Coolify environment (infraops' registry mentions it in two notes). The executor's root
token is in the hand-rotated set (decision 4) and is out of scope.

## What to do

1. **Inventory every token and every reader.** List Keychain items by account name only, and for
   each, search the portfolio, `~/Library/LaunchAgents`, crontabs, both OrbStack machines and the
   VPS for what reads it, by the rules in "Handling values". For the mirror's token, take the
   variable's name from the mirror's `mirror.py`, never from Coolify. For each token, record the
   machine account and access-token name that issued it, every reader, scheduled or on demand,
   and whether each reader reads per pass or once at start. Confirm which Keychain items hold the
   same value with a Keychain-only hasher you write: one process that reads each item and prints
   its account name, length and sha256 prefix. Don't reuse
   `infraops-mcp-server/scripts/cred-consumer-sweep.py` as it stands; it also reads BWS and
   Coolify. Prepare registry entries for the unregistered tokens, noting that a `coolify-env`
   consumer such as the mirror's is a hosted kind, which `_check_destination_fit` refuses for any
   plan without a `host` step. **Stop and ask** if a token has no owner you can name, or a value
   is shared between machine accounts.
2. **Settle the open decisions with Devon** before building. Put each as a question with options
   and a recommendation:
   - **How an all-human rotation is recorded.** The executor refuses a BWS-token unit at several
     checks today, before any step runs, and the options must answer each:
     - `checks.py` refuses an entry the registry hasn't marked `rotated_by_sds`, and no BWS-token
       entry is marked.
     - `checks.py` refuses an entry whose `bws_uuid` keeper isn't one of its destinations, and a
       machine token has no BWS copy of itself (infraops' registry says so in its comment on
       these entries).
     - `_check_human_retire_verifiable` refuses a plan where a person revokes unless every
       destination other than a provider or a BWS read-back kind has a scheduled read, and
       `readers.py` gives a scheduled read only to `runtime-fetch` consumers, so a `keychain`
       consumer can never have one.
     - `_validate_counts` and `_validate_order` require exactly one of each step in `ONCE`,
       starting with mint and then stage, so a unit whose only step files Devon's evidence fails.
     - `_validate_segments`: a unit holds only machine steps, each filing against an acceptance
       criterion, and a human act holds human steps that file nothing.
     - `_approved_package` needs a standing `rotation-<credential_id>` package in
       intent-packages for every rotation.
     Lay out the options, for example evidence filed through the `/review` human-act resolution
     or a new kind of unit, and what each changes in the contract and the orchestrator. Recommend
     the one that changes least. A change to `src/orchestrator/` is a release, so name it if an
     option needs one. Setting `rotated_by_sds` on any entry makes infraops' window refuse the
     credential and the rotation proposer act on it (`docs/operations/rotation-proposer.md`), a
     standing-authority change: **Stop and ask** before proposing it for any entry. Writing a
     package in intent-packages is outside this handoff's repositories: **Stop and ask** whether
     it's in scope.
   - **How Devon produces the evidence without handling values.** Amendment 1's confirm-dead is a
     pair of fresh logins: before the revoke, the old value logs in; after it, the same
     fingerprint fails with `invalid_client` while the replacement logs in. Each runs with an empty,
     throwaway `HOME`. Propose a Mac-side command that reads the Keychain items in-process, runs
     both halves, and prints fingerprints and outcomes only, plus where its output goes and how it
     reaches the orchestrator. Say whether a person ever sees a value, and design so they don't.
   - **Where the old value lives between the two halves.** The after-half needs the old value
     after Devon has already replaced its Keychain items, and decision 4 rules out staging it in
     `Rotation / Work` (amendment 1 says the same). Lay out the options, for example one process
     that holds it in memory across a wait for Devon's confirmation, or a separate throwaway
     Keychain account. Never BWS. **Stop and ask**: this is Devon's decision.
   - **How each reader is proved switched.** A green Healthchecks check can't tell the old value
     from the new: both stay live until the revoke, and a reader using the real `HOME` keeps an
     old token working through `bws`'s cached session for about 55 minutes after it (amendment 1,
     question 4). Propose a read that discriminates, and show it failing on a known-old reader.
   - **The exposure window.** Amendment 1 has Devon attest the window closed 75 minutes after the
     revoke. Say how that time is recorded and checked.
   - **Each token's switch order.** One value in three Keychain items (`ops-mini`) must change in
     all three before the revoke. Readers that read per pass pick up the new value on their next
     pass; holders that read once at start (the infraops MCP server, VideoCreator) need a
     restart. Several scheduled readers run between 02:15 and 05:15. Say when in the day each
     token can switch without failing a lane. The broad items also bootstrap the readers that the
     observer, drift-reporter and change-manager rotations verify through Healthchecks, so a
     switch while one of those rotations is between `store` and `retire_old` can stall or fail
     it: each token's task for Devon carries the precondition that no such rotation is in flight.
   - **`bws-cred-rotation-token`:** rotate it, or leave it until increment 9 retires its only
     reader and then revoke it.
   - **The unregistered tokens:** register them now, or record why not.

   **Stop and ask** here in every case. These are Devon's decisions.
3. **Measure before building.** **Stop and ask** first: Devon creates a throwaway machine account
   and access token in a throwaway project, stores the token in a throwaway Keychain account, and
   later revokes and deletes them himself. You never create, revoke or delete a token. With that
   token, read only in-process, repeat amendment 1's question 4 for the proposed command: fresh
   login before, Devon's revoke, fresh login after, with the throwaway token's fingerprint, beside
   a live control. Confirm the command never writes a state file and never prints a value. Find
   out from Bitwarden's documentation, and say which page, whether an access token can be created
   or revoked other than in the web app; don't guess. **Stop and ask** if any of this doesn't hold.
4. **Build what step 2 decided**, with tests that drive the whole sequence against fakes,
   including Devon's evidence arriving out of order, a wrong fingerprint, a "failed" after-half
   with no live-before half, and the exposure attestation filed early. Every evidence field
   passes the orchestrator's rotation-evidence check: no key name containing a `SECRET_KEY_PARTS`
   part, and no 32-character unbroken run. That check is a substring match on the lowered key, so
   this increment's natural names fail it: anything containing `log` (`login…`), `token`,
   `credential` or `secret`. **Stop and ask** before changing `contract.py`'s invariants, the
   rotation-evidence schema (a contract pinned in both places), or adding a dependency.
5. **Prove the refusals fire.** Mutation-test the change and report the count and every
   survivor.
6. **Install** on the VM per `docs/operations/rotation-executor.md`, under the "Rotations are in
   progress" rule. `provision.sh` doesn't stop the timer, so stop `rotation-worker.timer` and let
   any running pass finish first, then start it again. Update `source_revision` in
   `/etc/rotation-worker/config.toml` to the installed commit by hand, and confirm a pass logs that
   revision.

## What the last two days taught

- **BWS rate-limits bursts.** On 2026-10-20 a 429 at `store` failed a mint straight after a host
  without an overlap, and every reader of that keeper failed until HQ undid it by hand.
  orchestrator #465 makes the executor's store retry a 429 or 5xx for about three minutes. Any
  Mac-side command you write talks to the same BWS, so give it the same tolerance, or make it
  safe to re-run.
- **An orchestrator restart can silently not happen.** If Coolify's health check on the
  orchestrator is on, Coolify judges each new container unhealthy and keeps the old one. infraops'
  change window turned it on once (rule #570). The runbook's "If a rotation stalls after host"
  says what to check. This increment doesn't restart the orchestrator; if a decision needs it,
  **Stop and ask**.
- **GitHub sometimes runs nothing for a push.** The merge of #457 started no workflow, not even
  Quality, so a workflow on `main` was never registered (#461). After you merge, confirm the
  push's runs exist before trusting a green.

## Out of scope

Every rotation, revoke and Keychain change, and creating or deleting machine accounts or access
tokens, throwaway ones included: Devon does those, HQ drives them, after this. The executor's root
token and every other hand-rotated credential. infraops' rotation window (increment 9). In
infraops-mcp-server and other repositories, only `.cred-consumers.toml` and `.bws-secrets.toml`
rows are in scope. If the work needs a change to their code, their launchers or intent-packages,
**Stop and ask**.

## Closing steps

1. Merge each pull request yourself when its checks pass, except those the "Rotations are in
   progress" rule holds; ask Devon for those. Any held when you finish, list in your report: HQ
   merges them once Devon clears them.
2. Reinstall the VM at every executor change, under the same rule. The orchestrator isn't
   redeployed: if your change touches `src/orchestrator/`, **Stop and ask**. Before merging a
   registry change to another repository, read App Brain's `default-branch-landing` route for it
   (never derive it); if landing redeploys, merge outside the scheduled lanes' hours and confirm
   the served revision.
3. Record Devon's step 2 decisions as ADR-0055 amendment 11, in the pattern of amendment 10, and
   write `docs/superpowers/plans/<date>-rotation-increment-8-report.md` in the pattern of the
   increment 7 report.
4. Remove your worktrees, test database and merged branches, and check each main tree's
   `git status`. Remind Devon to revoke and delete the throwaway token and project if step 3 used
   them.
5. Report to Devon: the inventory (each token, its machine account, its Keychain items and every
   reader), his decisions from step 2, the measurement, the build and its mutation count, any
   held pull requests, and, for each token, the step-by-step task he follows to rotate it.
