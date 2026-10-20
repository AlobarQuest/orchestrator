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
it runs every five minutes on the VM. On 2026-10-20 the observer bearer's mint (`a7649b61`) was
requeued for its last attempt, and the change-manager observe bearer's mint waits for review, with
its retire to follow. Until HQ, through Devon, says no rotation unit is ready or claimed:

- Don't reinstall the VM.
- Don't merge to any repository's `.cred-consumers.toml`.
- Never do either between 02:00 and 06:00 America/New_York, the `live_estate` window.

Prepare those changes in your worktree and pull requests, and ask Devon when they can land.

## Read first

- `docs/superpowers/plans/2026-10-15-handoff-common-rules.md`: its **How to work** and **Closing
  steps** sections apply in full. Its **Don't touch** section is replaced by the preceding
  "Rotations are in progress" section.
- ADR-0055 (`docs/decisions/0055-*.md`): decisions 3, 4, 9 and 10; the consumer-kind table under
  "How each consumer kind is verified"; "Per-class disposition"; increment 8; amendment 1 in
  full, especially question 4 and "BWS confirm-dead logs in fresh"; and the two incident sections
  dated 2026-10-19 and 2026-10-20 under amendment 9.
- `docs/operations/rotation-executor.md`, all of it, and `docs/operations/credentials.md` on the
  BWS identities and why a launcher needing two of them reads each Keychain item into its own
  variable.
- `~/Projects/CLAUDE.md`, the invariants on `bws` caching a session per access token (check with
  an empty `HOME`) and on `--color no`; and this repository's CLAUDE.md on the human-act
  dependency, resolved only at `/review`.
- `src/rotation_worker/contract.py` (step kinds, actors, `validate_plan` and its segment rule,
  `ATTESTED_KINDS`), `checks.py`, `evidence.py`, `worker.py`, and `adapters/change_manager.py`,
  whose `change-manager-attested` plan is the nearest existing human act.
- `docs/superpowers/plans/2026-10-19-rotation-increment-7-report.md`, for how the last increment
  was reported.

## The tokens

Read each entry in the registry named; this list was read on 2026-10-20 and isn't to be trusted
for counts:

- `bws-sds-operator-token` (orchestrator's `.cred-consumers.toml`): the read-only `sds-operator`
  account, in Keychain item `Claude` / `BWS_ACCESS_TOKEN_SDS`, read by `scripts/sds-token.sh`, the
  scheduled launchers and infraops' `drift-audit.sh`.
- `bws-tok-ops-mini-20260730` (infraops-mcp-server's registry): the broad `Developer Machines`
  account's token, held in three Keychain items, `BWS_ACCESS_TOKEN_INFRA_DRIFT`,
  `BWS_ACCESS_TOKEN_INFRAOPS` and `BWS_ACCESS_TOKEN_VPS_BACKUP`. Many lanes read it, HQ's own
  scripts and `scripts/sds-deadman.sh` among them.
- `bws-tok-content-mini` (infraops-mcp-server's registry): `BWS_ACCESS_TOKEN_VIDEOCREATOR`.
- `bws-cred-rotation-token` (infraops-mcp-server's registry): `BWS_ACCESS_TOKEN_CRED_ROTATION`,
  read only by infraops' 04:00 rotation window, which increment 9 retires.

Not registered as credentials, as of 2026-10-20: the Keychain items
`BWS_ACCESS_TOKEN_BRAIN_OPS`, `BWS_ACCESS_TOKEN_CLAUDE_CODING` and
`BWS_ACCESS_TOKEN_LISTING_PREP`, and the machine account the github-to-bitbucket mirror uses
from its Coolify environment (infraops' registry mentions it in two notes). The executor's root
token is in the hand-rotated set (decision 4) and is out of scope.

## What to do

1. **Inventory every token and every reader.** List Keychain items by account name only
   (never `-w`, never a value), and for each, grep the portfolio, `~/Library/LaunchAgents`,
   crontabs, both OrbStack machines and the VPS for what reads it. For the mirror's token, take the
   variable's name from the mirror's `mirror.py`, never from Coolify: Coolify's env tools return
   values, and the common rules forbid them. For each token, record the machine account and access-token name that issued it
   and every reader, scheduled or on demand. Confirm which Keychain items hold the same value by
   comparing fingerprints computed in-process, the way the registry's `fingerprint_sha256_8` was
   made. Prepare registry entries for the unregistered tokens. **Stop and ask** if a token has no
   owner you can name, or a value is shared between machine accounts.
2. **Settle the open decisions with Devon** before building. Put each as a question with options
   and a recommendation:
   - **How an all-human rotation is recorded.** In `_validate_segments`, a unit holds only machine
     steps, each filing against an acceptance criterion, and a human act holds human steps that
     file nothing. So an all-human rotation has nowhere to file its evidence as the contract
     stands.
     Lay out the options, for example a unit whose only step files Devon's attested evidence, or
     evidence filed through the `/review` human-act resolution, and what each changes in the
     contract and the orchestrator. Recommend the one that changes least. A change to
     `src/orchestrator/` is a release, so name it if an option needs one.
   - **How Devon produces the evidence without handling values.** Amendment 1's confirm-dead is a
     pair of fresh logins: before the revoke, the old value logs in; after it, the same
     fingerprint fails with `invalid_client` while the replacement logs in. Each runs with an empty,
     throwaway `HOME`. Propose a Mac-side command that reads the Keychain items in-process, runs
     both halves, and prints fingerprints and outcomes only, plus where its output goes and how it
     reaches the orchestrator. Say whether a person ever sees a value, and design so they don't.
   - **The exposure window.** Amendment 1 has Devon attest the window closed 75 minutes after the
     revoke. Say how that time is recorded and checked.
   - **Each token's switch order.** One value in three Keychain items (`ops-mini`) must change in
     all three before the revoke, and scheduled lanes read Keychain at the start of each pass. Say
     when in the day each token can switch without failing a lane, and which Healthchecks checks
     prove each reader picked up the new value.
   - **`bws-cred-rotation-token`:** rotate it, or leave it until increment 9 retires its only
     reader and then revoke it.
   - **The unregistered tokens:** register them now, or record why not.

   **Stop and ask** here in every case. These are Devon's decisions.
3. **Measure before building.** With a throwaway machine-account access token in a throwaway
   project, repeat amendment 1's question 4 for the proposed command: fresh login before, revoke,
   fresh login after, with the throwaway token's fingerprint, beside a live control. Confirm the
   command never writes a state file and never prints a value. Find out from Bitwarden's
   documentation, and say which page, whether an access token can be created or revoked other than
   in the web app; don't guess. **Stop and ask** if any of this doesn't hold.
4. **Build what step 2 decided**, with tests that drive the whole sequence against fakes,
   including Devon's evidence arriving out of order, a wrong fingerprint, a "failed" after-half
   with no live-before half, and the exposure attestation filed early. Every evidence field
   passes the orchestrator's rotation-evidence check: no key name with a `SECRET_KEY_PARTS` part,
   and no 32-character unbroken run. **Stop and ask** before changing `contract.py`'s invariants,
   the rotation-evidence schema (a contract pinned in both places), or adding a dependency.
5. **Prove the refusals fire.** Mutation-test the change and report the count and every
   survivor.
6. **Install** on the VM per `docs/operations/rotation-executor.md`, under the "Rotations are in
   progress" rule, and confirm the installed source has the change.

## What the last two days taught

- **BWS rate-limits bursts.** On 2026-10-20 a 429 at `store` failed a mint straight after a host
  without an overlap, and every reader of that keeper failed until HQ undid it by hand.
  orchestrator #465 makes the executor's store retry a 429 or 5xx for about three minutes. Any
  Mac-side command you write talks to the same BWS, so give it the same tolerance, or make it
  safe to re-run.
- **An orchestrator restart can silently not happen.** If Coolify's health check on the
  orchestrator is on, Coolify judges each new container unhealthy and keeps the old one. infraops'
  change window turned it on once (rule #570). The runbook's "If a rotation stalls after host"
  says what to check. This increment shouldn't restart the orchestrator; if it must, check the
  container's age changed.
- **GitHub sometimes runs nothing for a push.** The merge of #457 started no workflow, not even
  Quality, so a workflow on `main` was never registered (#461). After you merge, confirm the
  push's runs exist before trusting a green.

## Out of scope

Every rotation, revoke and Keychain change, and creating machine accounts or access tokens:
Devon does those, HQ drives them, after this. The executor's root token and every other
hand-rotated credential. infraops' rotation window (increment 9). In infraops-mcp-server and
other repositories, only `.cred-consumers.toml` and `.bws-secrets.toml` rows are in scope. If the
work needs a change to their code or their launchers, **Stop and ask**.

## Closing steps

1. Merge each pull request yourself when its checks pass, except those the "Rotations are in
   progress" rule holds; ask Devon for those.
2. Reinstall the VM at every executor change, under the same rule. The orchestrator isn't
   redeployed: if your change touches `src/orchestrator/`, **Stop and ask**. Before merging a
   registry change to another repository, read App Brain's `default-branch-landing` route for it
   (never derive it); if landing redeploys, merge outside the scheduled lanes' hours and confirm
   the served revision.
3. Remove your worktrees, test database, merged branches and any throwaway BWS project or token
   you made, and check each main tree's `git status`.
4. Report to Devon: the inventory (each token, its machine account, its Keychain items and every
   reader), his decisions from step 2, the measurement, the build and its mutation count, and,
   for each token, the step-by-step task he follows to rotate it.
