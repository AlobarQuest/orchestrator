# D-48 B pass, after D-86: two open items for Devon

These are open items, not stop points. The pull request that records D-86's answers in ADR-0055
amendment 38 opens anyway. Each item is written into the amendment's "D-86's open items" section
with the same options.

## D86-A. Healthchecks ping URLs

D-86's clarification names Healthchecks ping URLs as P1. An issued ping URL has no rotation plan,
though: `src/rotation_worker/plans.py` lines 61-63 ("a ping URL has none, since it can't be
rotated, only replaced by recreating its check"), and D-77. Amendment 38's scope already puts the
18 ping-URL rows out (17 in orchestrator's registry, one in sds-hq's).

- (A) Out, as amendment 38 says.
- (B) In, by recreating each check. That's a person's Healthchecks act for every row, so it's P2
  by D-86's own rule.

**Recommendation: A.** B needs a person's act for each of the 18 rows, which D-86 sends to Phase 2.

## D86-B. Edits inside a row's own registry

D-86 (3) says "no registry change in this pass". HQ's reading of the scope rule, (iv), allows an
edit that stays inside the row's existing registry. Two such edits matter:

1. `change-manager-execute-bearer` has no `rotated_by_sds` key (change-manager
   `.cred-consumers.toml` line 240 at `f7f8bc07`). Handing it over means setting
   `rotated_by_sds = true`.
2. infraops reports a row due by an open exposure, by its class's maximum age, or by a
   `rotate_requested` date later than its last rotation (`src/rotation_proposer/findings.py` lines
   3-6 and 124-137). A row that isn't due by exposure or age rotates only after a
   `rotate_requested` date is written on it. The eight in-pass rows were created between 2026-06-14
   and 2026-10-20; whether infraops reports any of them due by age today wasn't read.

The options:

- (A) Both edits are allowed: D-86 (3) covers moves between registries and new rows.
- (B) Neither is allowed: the execute bearer is out, and the eight rows rotate only as infraops
  reports them due.
- (C) Only `rotate_requested` dates are allowed: the execute bearer is out.

**Recommendation: A**, with the execute bearer's flag set only after HQ's D-84 condition 2 check
that its scope (change-manager #168) writes nothing the executor trusts. Neither edit adds a reach,
a keeper move or a type, and without the dates the pass rotates only what infraops already reports
due.
