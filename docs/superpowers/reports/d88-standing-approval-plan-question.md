# Question: D88-A, is the factory-runner bearer's revision 1 a first hand-over?

This question doesn't block the plan or the PR. The plan is written for option A, and option B
adds one set of conditions to tasks R1 and R3. It is ADR-0055 amendment 39's Open item D88-A,
and the answer is Devon's.

## The conflict

The recorded D-88 A excludes "a credential's first hand-over to the executor" (sds-hq
`standing-answers.md` at `5c916e36`). D-88's question text listed "factory-runner rev 1" among the
packages option A was to approve (sds-hq `state/decisions.md`, D-88).

What the sources show for `orchestrator-factory-runner-bearer`:

- **The flag:** `rotated_by_sds = true` was added on 2026-11-01 by `c2f6ffbf` (orchestrator #579,
  "Rotation Part 5: hand over the last bearer"). Amendment 18 says it "is handed over by flag".
- **No package before:** `rotation-orchestrator-factory-runner-bearer` was first written on
  2026-11-28 (intent-packages `8bfaa16`). No earlier package exists, deleted or archived, and its
  lineage records no approval.
- **No rotation before:** the executor has never rotated it. Amendment 18 says "Nothing has proved
  its first rotation". Its first rotation is also the executor's first write of the seven
  `FACTORY_RUNNER_TOKEN` Actions secrets.
- **How the registry uses the term:** the registry calls a credential handed over when Devon
  approves its revision 1. The drift-reporter row reads "Handed to the rotation executor
  2026-10-21 (... rev 1, approved by Devon ...)" (orchestrator `.cred-consumers.toml` line 448).

## Options

- **(A) Treat it as a first hand-over (recommended).** Devon approves revision 1 by name, and every
  reissue after it is covered by D-88 A. The plan needs no revision-1 path for a reissue type.
  Every first run of a credential's distribution path goes in front of a person.
- **(B) Treat it as covered.** Task R1 adds a revision-1 form for the `orchestrator` and
  `change-manager` types: the package equals a re-render from its registry row, infraops'
  `last_rotated` is null, and the other reissue conditions hold. R3 adds the matching branch to
  the intent-packages reissue grant. The execute bearer stays a first hand-over, since its flag
  isn't set yet.

## Recommendation

A. The first proof of a distribution path is the case where a person reading the package earns
their time. The registry's own usage supports it, and A keeps the plan to one form per revision
range.

Reply line: `D88-A: A` or `D88-A: B`.
