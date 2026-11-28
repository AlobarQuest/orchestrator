# Question: D-88 A R1 and R2: only the tip's approval is chain-backed

W-346, handoff `d88-r1r2-orchestrator`. The branch `hq/d88-r1r2-orchestrator` is pushed at head
`3724916aa315632df00a0324b33da07bf8260dbc`, on base `73b77ea7`. No PR is open yet; I'll open it
once you answer. R1 and R2 are built as amendment 39 and your three settled changes describe them:

- `make check`: 15,301 passed, 1 skipped.
- Mutation pass: 47 mutants, one per exclusion check, and every one is killed.
- `code-standards risk`: `normal`.
- `/code-review`: run, and four of its nits are fixed.

I stopped before the PR and the report because of the review bar. D-84 condition 2 says the
no-person approval routes must refuse a package the root wrote. Built as amendment 39 specifies,
they don't refuse every such package.

## What the code relies on that the chain doesn't back

Only the current revision's approval is checked against the factory-events chain. intent-packages
`verify_approval` (`operations.py`, after line 492) says so: "Only the current revision is checked."
The orchestrator's emitter matches one `package.approved` event, on the tip's revision and hash
(`package_sources.py` 383-405 and 408-438).

A reissue's other inputs are the lineage's word. These are revision 1's `devon` approval, which
`reissue_lineage` and `policy_approved_reissue` require, and the last approval's `commit` and
`approved_hash`, which `proposer_revised` reads and compares against. Both forged-approval tests
you asked for pass. One is in `carry_reissue`
(`test_a_tip_approval_the_verified_chain_event_doesn_t_back_refuses`) and one in `_verified_approval`
(`test_a_reissue_approval_the_emitter_didn_t_verify_refuses`). Both cover the tip only.

## The path

1. The rotation root, which can write intent-packages `main`, commits revision 1 of a
   `rotation-<orchestrator bearer>` package. It then commits a lineage approval of revision 1 with
   `approver: devon` and that commit as `commit`. Then it commits revision 2, `ready_for_review`.
2. `rotation-proposer standing-approval` passes every reissue condition. The package at the forged
   commit hashes to the forged `approved_hash`, because the root wrote both.
3. A cycle's `standing-approve` then writes a real policy approval and a real chain event on
   revision 2. The carrier verifies that tip event, the gate admits it, and the executor runs it.

The code review's important finding is the same path, one step later. The carrier skips
`proposer_revised` and `no_new_copy` (reading 8), and nothing ties the command's passing run to
the policy approval it's meant to precede. So a written copy added in a tip that no command run
saw also goes through.

## Options

- **(A) Accept and record the residual.** Merge as built. Add to amendment 39 that the reissue
  route trusts the lineage for every approval before the tip, and that the rotation root can
  forge one. This matches D-34's exposure today: a lane revision 1 needs no earlier approval, so it
  has none to forge. Nothing changes in code.
- **(B) Verify earlier approvals where the chain is already read.** In `package_sources.py`, the
  emitter could match revision 1's approval, and the last earlier approval, against the events
  `_verified_package_approval_events()` already returns. It would carry them in the payload, and
  the carrier would compare them as it compares the tip's. This is an orchestrator change outside
  R1 and R2, but it needs no new credential. The intake model is a `CommandBase` without
  `extra="forbid"` (`api/schemas/intake.py` 109, `api/schemas/common.py` 20), so an added payload
  field is no response-model change. The open question is the chain's contents, which I can't
  read from here. If the October `devon` approvals of these seven packages aren't
  `package.approved` events in the database chain, (B) refuses all seven.
- **(C) Give the command a chain read.** `standing-approval` would verify revision 1's approval
  and the last approval against the chain itself. That's a new credential use for the
  rotation-proposer (`FACTORY_EVENTS_READ_PASSWORD`), which is one of the handoff's stop points.
  It's also the only option that covers `proposer_revised`'s comparison. It still leaves
  reading 8: the carrier can't prove the command ran.

I recommend (B), if HQ can confirm the seven October approvals are in the database chain.
Otherwise I recommend (A), with the residual recorded. Then R4's `standing-approve` would be the
only place a reissue's earlier approvals get checked, and only by the cycle that runs it.

## What I need

Which option, and whether it lands in this PR. For (B), also whether the October approvals are
chain events.
