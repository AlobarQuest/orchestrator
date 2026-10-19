# Handoff: GitHub PATs, the Atlassian token and factory-runner's bearer (ADR-0055 increment 7)

Increment 7 moves the credentials that live in GitHub Actions secrets, or that a person mints in a
console, onto the rotation executor. It needs one new piece of machinery, the Actions-secret
writer: a dedicated rotation GitHub App whose installation tokens write Actions secrets
(ADR-0055 amendment 1, question 6). This handoff inventories the credentials, settles the open
decisions with Devon, builds the writer and the adapters, and installs them on the VM. It doesn't
move keepers, write packages or rotate anything.

HQ is the session driving the rotations in progress (the observer and the change-manager observe
bearer). Report to Devon; questions marked **Stop and ask** go to him before you build past them.

## Rotations are in progress: don't disturb them

The executor reads each registry live from its repository's `main` and parses the whole file, and
it runs every five minutes on the VM. Until HQ, through Devon, says no rotation unit is ready or
claimed:

- Don't reinstall the VM.
- Don't merge to orchestrator's or change-manager's `.cred-consumers.toml`.
- Never do either between 02:00 and 06:00 America/New_York, the `live_estate` window.

Prepare those changes in your worktree and pull requests, and ask Devon when they can land.

## The credentials

Read each entry in the registry named; don't trust this list's counts, read on 2026-10-19:

- `factory-pr-token` (factory-runner's `.cred-consumers.toml`): a fine-grained PAT with a keeper
  and eight `gh-actions-secret` copies. Read its `rotation_preconditions`: they put two steps on
  the manual checklist.
- `orchestrator-factory-runner-bearer` (orchestrator's `.cred-consumers.toml`): an orchestrator
  bearer with a `coolify-env-hash` entry, seven `gh-actions-secret` copies and one `runtime-fetch`
  reader. The executor refuses it today, for two reasons:
  - its `coolify-env-hash` row has no `entry`, which `destinations.py` requires. The row's comment
    says the key id is an Actions secret no file records and is to be read from the orchestrator's
    m2m-identities route (by agent id, never by value);
  - its `runtime-fetch` reader names no read, which `checks.py` refuses for a plan with no human
    act. Its plan needs a human attestation for that reader, or the reader needs a read.
- `github-finegrained-mirror` (infraops-mcp-server's registry): a fine-grained PAT with a keeper
  and a `coolify-env` copy. Its disposition is `reissue-least-privilege`, with a
  `replacement_scope`.
- `bitbucket-mirror-token` (infraops-mcp-server's registry): an Atlassian API token with a keeper
  and a `coolify-env` copy.
- `github-classic-aihelper` and `github-classic-lifeops` are `github-pat-classic` with disposition
  `revoke-no-replacement`, a class ADR-0055's per-class table doesn't list.

## Read first

- `docs/superpowers/plans/2026-10-15-handoff-common-rules.md`: its **How to work** and **Closing
  steps** sections apply in full. Its **Don't touch** section is replaced by the preceding
  "Rotations are in progress" section.
- ADR-0055 (`docs/decisions/0055-*.md`): decisions 3, 4 and 5; the consumer-kind table under
  decision 5; the threat model, including T7 on dependencies; "Per-class disposition"; increment
  7; amendment 1 in full, especially question 6 and "The GitHub secret writer is a dedicated
  rotation App"; the Coolify decision; and amendment 6 (where admin keys live).
- `docs/operations/rotation-executor.md`, all of it, and `docs/operations/credentials.md` on
  `FACTORY_PR_TOKEN` (its copies, and the workflow-file push probe that verifies a rotation today)
  and on the dispatch App (why its reach is wide, and why it isn't the writer).
- `docs/operations/architecture-guards.md`: what a new `workflow_dispatch` workflow needs.
- `src/rotation_worker/contract.py` (human steps, staging secrets, `ATTESTED_KINDS`, and
  `_distribute`, which writes only `bws-secret` today), `destinations.py`, `checks.py`,
  `registry.py`, `cli.py`, `worker.py`, `coolify.py`, and `adapters/orchestrator_bearer.py` and
  `adapters/change_manager.py`, the two adapters nearest to this work.
- `docs/superpowers/plans/2026-10-18-handoff-change-manager-adapter.md` and the report of the
  session that built it (orchestrator #447): the pattern for steps 1 and 6 to 9.

## What to do

1. **Inventory every copy.** For each credential, grep the portfolio for the keeper's UUID and
   name, and list each repository's Actions secrets by name (names only, never values) to find
   copies the registry doesn't list. Check `~/Library/LaunchAgents`, crontabs, both OrbStack
   machines and the VPS for other readers, as #430 did. Record which BWS account reads each
   keeper, for the keeper moves. Confirm that no two keepers share a value, by fingerprints
   computed in-process. Only keepers can be compared: Actions copies are write-only, and the
   Coolify copies are readable only through tools the common rules forbid, so say so rather than
   trying. Read the factory-runner bearer's key id from the identities route and add the missing
   `entry`. Only the configured executor may read that route, so read it on the VM through the
   executor's own client (`rotation_worker.cli.build_setup`), printing key ids and agent ids
   only. Prepare registry fixes in their own repositories (in scope, below), and land them
   under the preceding rule. **Stop and ask** if a copy has no owner you can name, or if a value
   is shared.
2. **Settle the open decisions with Devon** before building. Put each as a question with options
   and a recommendation:
   - How a `gh-actions-secret` copy is verified: a probe workflow, or the human attestation the
     ADR allows meanwhile. A probe workflow means a workflow change in every repository holding a
     copy, which is outside this handoff's scope, and it needs both workflow-guard allowlists.
     Say what `FACTORY_PR_TOKEN`'s push probe does today and whether it can serve.
   - The classic PATs: they're `revoke-no-replacement`, so the question is whether the executor
     needs a revoke-only path for them, or whether they stay manual.
   - Whether a `coolify-env` copy (a raw value, not a hash) stays a human write, as the per-class
     table says. The ADR gives no reason for that, and its Coolify decision said it would update
     the per-class rows, so these two rows may simply have been missed. Verification stays a human
     attestation either way (the consumer-kind table). Don't invent a reason.
   - How a person hands a value they minted to the executor: decision 3's staging secret and
     human-act dependency, and what Devon does in the GitHub or Atlassian console, step by step,
     including `factory-pr-token`'s two `rotation_preconditions` (the repository-access list is
     settings-page only) and the mirror PAT's `replacement_scope`.
   - Where the App's private key lives. The asset table says `Rotation / GitHub`, written before
     amendment 6, which puts every provider's admin key in `Rotation / Admin` under an
     `admin_secrets` key, and the root has no grant on a `Rotation / GitHub` project. Recommend
     `Rotation / Admin` and a correction to the asset row.

   **Stop and ask** here in every case. These are Devon's decisions.
3. **Confirm the mechanisms you can before the App exists.**
   - Run `rotation-worker compare-destinations` on the VM for one credential from each of
     factory-runner and infraops-mcp-server. With no standing package for these credentials it
     exits 2 after printing the registry's side; judge only those registry lines. Both
     repositories are already among the registry repositories in `registry.py`.
   - For a PAT's retire, confirm how the old value is revoked and how its death is shown
     (`POST /credentials/revoke`, per the per-class table; the Atlassian row says revoke is
     unverified, so retire-old is human there). Probe only with throwaway credentials.

   **Stop and ask** if any of these doesn't hold.
4. **Prepare the rotation App for Devon.** He creates it; you can't create Apps or handle its
   private key. Write his steps: the App's name, the only two permissions (Secrets: write and the
   mandatory Metadata: read), no webhook, installed only on the repositories that hold a
   registered Actions-secret copy (read the registries again now, since a fork-onboarding session
   may have added copies), and the private key stored where step 2 decided, readable by the root.
   Give Devon the steps as a task with instructions, and check his result by shape and
   fingerprint only. The executor must refuse to write a repository the approved destination
   doesn't name, whatever the App can reach.
5. **Repeat question 6's measurement against the real App**: mint for one repository with
   `{secrets: write, metadata: read}`, read the mint response's `permissions`, write a throwaway
   secret name, and delete it. A token minted with `{metadata: read}` must get 403 on the same
   write. **Stop and ask** if it doesn't hold.
6. **Decide each adapter's plan** the way `orchestrator_bearer.PLAN` is written: steps, units, the
   human acts between units, and which acceptance criterion each step files against. Extending the
   orchestrator adapter for the factory-runner bearer is in scope. Each unit that writes a hosted
   consumer asks the `live_estate` window, and one that writes only BWS and Actions secrets may
   set `touches_no_hosted_service`; say which and why.
7. **Build it**: the Actions-secret writer, the adapters, and their tests, which drive each unit
   against fakes, including a stop after host and its retry. Writing Actions secrets means
   changing the contract itself: `_distribute` writes only `bws-secret`, and `gh-actions-secret`
   is in `ATTESTED_KINDS`. **Stop and ask** with the proposed change to both before making it, and
   before adding any dependency (sealing a secret needs a library such as PyNaCl; T7 promises few
   dependencies, pinned by hash). Nothing assumes an installation token's length. Anything that
   imports an HTTP client needs its `OUTBOUND_ALLOWLIST` entry.
8. **Prove the refusals fire**: a write to a repository or secret name the destination doesn't
   name, a token minted with more than the two permissions, a staged value equal to the old one,
   and whatever step 2 decides for verification. Mutation-test the change and report the count and
   every survivor.
9. **Install** on the VM per `docs/operations/rotation-executor.md`, under the "Rotations are in
   progress" rule, and confirm the installed source has the change.

## Out of scope

The keeper moves and their grants, the App's creation (step 4 prepares it), the packages,
`rotated_by_sds`, and every rotation. HQ does those after this, one credential at a time. In
factory-runner and infraops-mcp-server, only the registry rows in `.cred-consumers.toml` and
`.bws-secrets.toml` are in scope. Their code, workflows and deploys are not: if the work needs a
change there, **Stop and ask**.

## Closing steps

1. Merge each pull request yourself when its checks pass, except those the "Rotations are in
   progress" rule holds; ask Devon for those.
2. Reinstall the VM at every executor change, under the same rule. The orchestrator isn't
   redeployed: if your change touches `src/orchestrator/`, **Stop and ask**. Before merging a
   registry change to another repository, read App Brain's `default-branch-landing` route for it
   (never derive it); if landing redeploys, merge outside the scheduled lanes' hours and confirm
   the served revision.
3. Remove your worktrees, test database and merged branches, and check each main tree's
   `git status`.
4. Report to Devon: the inventory (each copy, its owner, and the account that reads each keeper),
   his decisions from step 2, the App's installation list, each adapter's plan, the mutation count,
   and every grant the keeper moves need.
