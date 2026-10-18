# Handoff: rotate change-manager's four bearers (ADR-0055 increment 6)

change-manager's four machine bearers (`full`, `read`, `propose`, `observe`) are the rest of
increment 6. Their overlap is built, deployed and verified on production (ADR-0055 amendment 9,
"change-manager's overlap, built and verified"). The executor has no adapter for them. This
handoff inventories their readers, builds a `change_manager` adapter, and installs it on the VM.
It doesn't move keepers, write packages or rotate anything.

HQ is the session that drove the operator and observer rotations. Report to Devon; questions
marked **Stop and ask** go to him before you build past them.

## Read first

- `docs/superpowers/plans/2026-10-15-handoff-common-rules.md`: its **How to work** and **Closing
  steps** sections apply in full. Its **Don't touch** section is spent.
- ADR-0055 decisions 3, 5 and 7, and amendment 9 in full (`docs/decisions/0055-*.md`), especially
  "change-manager's overlap, built and verified" and decision 5's reader reads.
- `docs/operations/rotation-executor.md`, all of it.
- `src/rotation_worker/contract.py`, `checks.py`, `worker.py`, `readers.py`, `destinations.py`,
  `coolify.py`, and `adapters/orchestrator_bearer.py`, the adapter nearest to this one.
- change-manager: `docs/operations/bearer-settings.md`, the bearer-setting parser it names,
  `tests/fixtures/m2m_bearer_setting.json` (pinned in both repositories), and its
  `.cred-consumers.toml` entries `change-manager-{full,read,propose,observe}-bearer`.
- `docs/superpowers/plans/2026-10-15-handoff-observer-drift-reporter-inventory.md` and the
  registry rows #430 produced: the pattern for step 1.

## What to do

1. **Inventory the readers.** Each bearer's registry entry lists its readers in one
   `runtime-fetch` row with several scripts in `ref`. The executor verifies each scheduled reader
   by the read its own row names (`healthchecks_check`, or `observation_source` with
   `observation_subject`), and a plan with no human act refuses a reader with no read. For each
   bearer:
   - Find every reader: grep the portfolio for the keeper's UUID and name, and check
     `~/Library/LaunchAgents`, crontabs, both OrbStack machines and the VPS, as #430 did. The
     registry's list is a starting point, not the answer.
   - Record which BWS account each reader fetches the keeper with (its Keychain item), and its
     schedule. The keeper moves need one direct grant per account.
   - Split the entry into one `runtime-fetch` row per reader, with the read that shows it worked.
     Use the readers.py rules as they stand.
   - Confirm, by fingerprint only, that no two of the four bearers share a value. change-manager's
     boot refuses a `previous` equal to any scope's current hash, as the orchestrator's does.

   **Stop and ask** if a reader has no read that shows it worked, if a reader runs inside the
   02:00 to 06:00 window in a way the overlap doesn't cover, or if two bearers share a value.
2. **Confirm the mechanisms the adapter rests on before building.** The ADR says the executor
   can't read change-manager's loaded hashes and confirms writes by probe. Find, in
   change-manager's source, a route each scope may call that answers 200 for an accepted bearer
   and 401 for a refused one, with no side effect, and confirm a random bearer gets 401 there.
   Find what in change-manager reads the bearer settings at boot, and confirm a restart applies a
   rewritten variable. Confirm the executor's Coolify client and token can write change-manager's
   application (its uuid is in the registry's `coolify-env-hash` rows). **Stop and ask** if any
   of these doesn't hold.
3. **Decide the plan.** Write the adapter's plan the way `orchestrator_bearer.PLAN` is written:
   which steps, which units, and which acceptance criterion each step files against. Host builds
   the scope's variable from the new value and the quarantined old one, without reading the
   setting (the ADR's constraint on this adapter). Say what the executor checks before each
   change-manager restart, since change-manager's own clients are the scheduled lanes, and
   whether the restart needs the `live_estate` window. **Stop and ask** if the plan needs a step
   `contract.py` doesn't have.
4. **Build it**, through `contract.py` only, with tests that drive each unit against fakes,
   including a stop after host and its retry. Hold the variable's form to change-manager's pinned
   fixture, not to a copy of it.
5. **Prove the refusals fire**: a host whose `previous` would equal another scope's current hash,
   a host on an application or variable the destination doesn't name, and a probe that can't
   tell 200 from 401. Mutation-test the change and report the count and every survivor.
6. **Install** on the VM per `docs/operations/rotation-executor.md`, and confirm the installed
   source has the change.

## Out of scope

The four keeper moves and their grants, the four packages, `rotated_by_sds`, and every rotation.
HQ does those after this, one bearer at a time. Any change to change-manager's code is out of
scope: if the adapter needs one, **Stop and ask**.

## Closing steps

1. Merge each pull request yourself when its checks pass.
2. Reinstall the VM at every executor change. Neither the orchestrator nor change-manager is
   redeployed: if your change touches `src/orchestrator/` or change-manager, **Stop and ask**.
3. Remove your worktree, test database and merged branch, and check the main tree's `git status`.
4. Report to Devon: the reader inventory (each reader, its account, its schedule and its read),
   the probe route per scope, the plan, the mutation count, and every grant the keeper moves need.
