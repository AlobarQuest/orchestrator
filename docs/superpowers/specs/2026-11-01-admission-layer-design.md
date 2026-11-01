# Admission and policy layer design

**Status:** Draft for Devon's approval. Layer id: `admission` (`make layer L=admission`).

## Intent

Admission turns an intent package into approved, ready work: intake, decomposition, authority approval, and the move to ready. `factory-policy.toml` is its refusal table. The policy core is well tested, and the layer makes no outbound calls. Its gaps are work that waits with nobody told, and a scope check that arrives after a person has already approved the work.

This pass uses the same four outcomes as the earlier layers:

1. Every signal has a home: a finding or failure reaches alert-hub as one keyed alert, never only a log.
2. Scope comes from the member record: act by grant, observe by membership.
3. Records stay true: no record, unit, or state says something that stopped being so.
4. Edges are clear: one test states each lane's outbound surface.

Phase 4 of the year plan sends client requests through this layer first, and a client waits on these gates in person.

## Measurements, 2026-11-01

| Measure | Value |
|---|---|
| Open units | 6: 4 `failed`, 2 `revision_required`, all known history |
| Outbound clients in the layer | 0 |
| Policy schema | 9, exact, fail-closed |
| A route listing intakes or proposals | None |

## Devon's decisions, 2026-11-01

1. **Admission waits alert from `execution-watch`,** not a new lane.
2. **An ungranted repository is refused at the decomposition proposal,** with `target_repository_not_granted`. Intake stays open. Rotations keep their own check.

## Goals

- Work waiting at any admission gate past its threshold raises one keyed alert that resolves when the wait ends.
- `policy_not_applied` and a failed policy approval reach a person for every class, not only bumps.
- A person is never asked to approve work that can't run for lack of a grant.
- One test states that admission has no outbound surface, and where each vocabulary copy lives.

## Non-goals

- A check for unused known-good patterns. *Claude's call.*
- Expiring a staged intake or proposal. The lane reports, and a person decides.
- Moving policy lists into the member record. `[admission]`, reach rows, and bump conditions say what work the factory takes, not where.
- Removing vocabulary copies.

## Anti-goals

- **Alert noise.** A gate that's working as designed raises nothing inside its threshold, and history stays silent through the seed.
- **A refusal at intake.** Observing is by membership, so a package for an ungranted repository may still register.
- **A wider observer.** The observer gains no route. `execution-status` serves more rows.
- **A permanent alert for a decision already made.** A rejected proposal stops raising.
- **A rotation affected by a factory rule.**

## Constraints

- The first pass of a new condition must not alert on existing subjects, although `known.json` already exists, and `execution-watch` updates itself from `main`.
- An approval path re-reads policy, and a read-time refusal must not write.
- `decomposition._policy_approval` runs in the proposal's transaction, and its savepoint swallows `DomainError`.
- `CLAUDE.md`'s rules on envelopes, supersession, and intake causes hold.
- Word guards apply under `src/orchestrator/`.

## Design

### Part 1: Every signal has a home

`execution-status` gains rows for what isn't a unit yet. A row gains `subject_kind` and `subject_id`, and `unit_id` and the unit flags become nullable. Existing rows gain `entered_at`, the newest event with that `to_state`, because `updated_at` moves on every write. The server serves an `overdue` flag, because the threshold, `dead_letter_stalled_approval_seconds` (7 days), is its setting. The queries come out of `pending_decisions.py`, which is a reporting module, so both share them.

`execution-watch` raises:

| Key | Condition |
|---|---|
| `admission-waiting:<subject>` | An overdue staged intake, undecomposed package, pending or sent-back proposal, or unit in `draft`, `awaiting_approval`, or `awaiting_review` |
| `admission-policy-not-applied:<proposal>` | A proposal still `proposed` with `policy_not_applied` recorded |

- A `draft` unit with a pending dependency isn't waiting on a person, so it's skipped.
- A revision with a rejected proposal is excluded, and its clock runs from the last decision. Otherwise `_package_breakdowns` re-lists declined work for ever.
- A key is raised on presence and gated on overdue in `firing`. `known & raised` prunes a seeded key that isn't raised, so raising only when overdue would fire history later.
- Ownership: a rotation proposal's wait belongs to `rotation_decomposer`, and a SYSTEM bump proposal's to `decompose_refused_key`. The `owned_elsewhere` table and its test extend to both.

**Policy not applied.** `_approve_by_policy` records `decomposition.policy_not_applied` for a SYSTEM rotation refusal too, with a reason at each return. It stays silent for a human's or WORKER's proposal, which policy was never going to approve, and for `proposer_not_system`. The event is immutable, so the condition holds only while the proposal is `proposed`, and it's keyed once per proposal. A proposal row must carry `is_rotation=False` to pass the lane's early return for rotation units.

**Deploy order.** The lane updates itself from `main`, so a new kind of row could fire history in the window before a seed. This part lands in two pull requests, and needs no new seed mode:

1. A tolerant client, the new rows, and the new keys raised with firing suppressed. Each pass absorbs new-kind keys into `known.json`. Deploy.
2. Delete the absorption and the suppression.

### Part 2: Scope comes from the member record

Proposing a decomposition refuses `target_repository_not_granted` after `_validated_dependencies` and past replay, so a replay is unchanged. It answers 409 with recovery `restore_grant` and writes nothing. `_approve` asks too, so a proposal pending at a withdrawal doesn't ask a person for dead work.

`unit_grant_refusal` takes a `WorkUnit`, and a proposed unit has no id before the flush, so it widens to a protocol carrying `required_capability` and `authority`. Rotation units and units with no `target_repository` skip the check.

The bump branch's own check at `decomposition.py:435` becomes unreachable and goes, with the tests that assert `repository_not_granted`. Both lanes already handle a 409: neither retries a person's proposal, and a bump refusal is only a race that clears on the next pass.

### Part 3: Records stay true

The lane's report covers staged intakes and proposals that sit forever. `CLAUDE.md`'s rule on mistyped capability levels is stale, because `runner_capability_level_violation` sits inside `runner_authority_violation` at ingress, so it moves to the archive.

### Part 4: Edges are clear

`tests/architecture/test_admission_edges.py` states that `services/intake/` and the policy modules build no client and read one file, and lists each vocabulary copy with what holds it equal.

### Part 5: Rules into tests

- Write tests for `runner_authority_violation`'s four asking points and for the standing-context approval.
- Split the long multi-condition rules into one rule each, each naming its test.
- Move history to the archive.
- Run a mutation pass over the enforced rules. Rule count isn't this part's measure: splitting raises it. The measure is rules with a test that failed when the rule broke.

## Order

Parts 1 to 5, in order. Each has its own plan, reviews, and live proof.

## Live proof

| Part | Can prove live | No live proof until a real event | Needs Devon first |
|---|---|---|---|
| 1 | The read, the seed, zero firing | Each alert | A staged proposal on `sds-canary` |
| 2 | A granted proposal still works | A real refusal | A proposal for an ungranted repository |
| 3 | The report | Not applicable | None |
| 4 | The test is the proof | Not applicable | None |
| 5 | Mutation counts | Not applicable | None |

## Left to the builder

- Row and field names beyond those stated.
- Alert wording, with the next step in it.
