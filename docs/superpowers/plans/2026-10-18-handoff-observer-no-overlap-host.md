# Handoff: rotate the observer bearer once without an overlap (ADR-0055 increment 6)

The observer and drift-reporter bearers share one value. They're split by two ordinary
rotations: the observer first, without an overlap, then the drift-reporter with one. The executor
can't host without an overlap today. This handoff builds that one capability in the rotation
executor and installs it on the VM. It doesn't move keepers, write packages or rotate anything.

HQ is the session that drove the operator bearer's rotation. Report to Devon; questions marked
**Stop and ask** go to him before you build past them.

## Read first

- `docs/superpowers/plans/2026-10-15-handoff-common-rules.md`: its **How to work** and **Closing
  steps** sections apply in full. Its **Don't touch** section is spent.
- ADR-0055 decision 7 and amendment 9 in full, especially "Decision 3 revised: the split is two
  ordinary rotations" (`docs/decisions/0055-*.md`).
- `docs/operations/rotation-executor.md`, especially "If a rotation stalls after host" and "If the
  orchestrator doesn't come back after a rotation's restart".
- `src/rotation_worker/m2m_setting.py` (`build`, `Change`, `_refuse_what_boot_refuses`),
  `adapters/orchestrator_bearer.py` (`host`, `revoke`, `_await`, `PLAN`), `contract.py` (the host,
  retire-old and confirm-dead steps), `checks.py`, `envelope.py` and `worker.py`.
- `src/orchestrator/identity/auth.py::previous_hash_valid`: the rule that makes the overlap
  impossible while the two entries share a hash. Don't change it.
- `.cred-consumers.toml`: the `orchestrator-observer-bearer` and
  `orchestrator-drift-reporter-bearer` entries.

## What to build

A host that writes the entry's new hash with no `previous`, used only for the observer's first
rotation. Two conditions, both required:

- **It's declared in what Devon approves**, never inferred by the executor. Where the declaration
  lives (the package, the envelope or the adapter's plan) is your design. Read how
  `touches_no_hosted_service` reaches the executor for the pattern, and check whether adding a
  field changes a contract pinned with intent-packages or factory-runner.
- **The executor refuses it unless an overlap is impossible**: the old hash is another entry's
  current hash in what the orchestrator serves. An approved declaration on any other rotation is
  refused, so it can't become a general way to skip the overlap.

## What to do

1. **Trace the run before building.** Walk the observer's rotation step by step with a
   no-overlap host: mint, stage, host, verify-new, store, verify-consumers, then the retire unit's
   wait, retire-old and confirm-dead. For each, find what it assumes about `previous`, and say
   what it does when there's none. `revoke` already has a path for an entry with no `previous`;
   confirm it fits. Confirm-dead must still show the old value refused for the observer's key id
   while the drift-reporter's key id still accepts it. **Stop and ask** if a step can't be made to
   fit without changing the contract's invariants (ADR-0055 decision 3).
2. **Define the recovery.** With no `previous`, the runbook's undo can't restore the entry from
   it. Work out how a unit that stops after host is finished or undone, using only what the
   executor and the runbook can read, and write it in the runbook. **Stop and ask** if undoing
   needs a person to handle a value outside the VM.
3. **Build it**, with tests that drive the whole observer plan through a no-overlap host against
   fakes, including a stop after host and its retry.
4. **Prove the refusals fire**: a declared no-overlap host on a rotation whose old hash is no other
   entry's current hash, and an undeclared one on a rotation whose old hash is. Mutation-test the
   change and report the count and every survivor.
5. **Install** on the VM per `docs/operations/rotation-executor.md`, and confirm the installed
   source has the change.

## Out of scope

Moving the two keepers and the two grants #430 listed, the observer's and drift-reporter's
packages, `rotated_by_sds`, and both rotations. Each comes after this.

## Closing steps

1. Merge each pull request yourself when its checks pass.
2. Reinstall the VM at every executor change. The orchestrator isn't redeployed: if your change
   touches `src/orchestrator/`, **Stop and ask**.
3. Remove your worktree, test database and merged branch, and check the main tree's `git status`.
4. Report to Devon: the step 1 trace, the recovery from step 2, where the declaration lives, and
   the mutation count.
