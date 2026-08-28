# ADR-0024 — A refusal caused by being behind is not a finding

- **Status:** Accepted
- **Date:** 2026-08-16
- **Decided by:** Devon
- **Extends:** the three refusal rulings of 2026-08-13/14 (recorded in `CLAUDE.md`), ADR-0019

## Decision

**Beside an exception, the estate-landing agent suppresses every refusal that is caused by the head
being behind its base and that says nothing about the change itself.**

This replaces the enumeration the previous rulings were building — deliberate, then exception, then
freshness-beside-an-exception — with a criterion. Each of those rulings closed one case and
generated the next; this closes the class.

## Why a rule rather than a fourth patch

The pattern is now four deep and each fix produced the next category:

1. A **deliberate refusal** (`landing_pace_exhausted`, `landing_outside_change_window`) is not a
   finding — the system pacing itself.
2. An **exception** (`landing_update_type_unparseable`) is not a finding — it can never clear and
   waits on a person.
3. **Freshness beside an exception** is not a finding — the lane deliberately declines to freshen a
   pull request it can never land, so the staleness is our own choice.
4. And now **`landing_rollout_moved` beside an exception**, for exactly the same reason: a head that
   is never freshened never acquires the pinned rollout workflow, so the pin mismatch persists
   forever. Traced 2026-08-16 on `brain#31`/`#32`: `_held_status` subtracts freshness beside an
   exception but not this code, leaving `{landing_rollout_moved}` unexplained → `held` → **exit 3
   every night, permanently** — the failure ruling 1 exists to prevent, rebuilt by ruling 3's own
   correctness.

A fifth member would arrive the same way. The criterion below covers it in advance.

## The criterion, and the discriminator that keeps it narrow

A refusal is **freshness-derived** when it is produced by the head's position relative to its base
and carries no information about the change itself. Today that is exactly two:

| refusal | freshness-derived? | why |
|---|---|---|
| `landing_head_not_current_with_base` | **yes** | it *is* the position |
| `landing_rollout_moved`, **base blob matches the pin** | **yes** | the head predates a workflow change; merging the base carries the pinned bytes in |
| `landing_rollout_moved`, base does **not** match | **no** | the workflow genuinely moved; freshening cannot put that right |
| `landing_checks_not_clean` | **no** | a failing check is a fact about the change, and freshening does not reliably clear it |
| `landing_pace_exhausted`, `landing_outside_change_window` | n/a | already deliberate, and time-based rather than position-based |

**`landing_checks_not_clean` is the case that keeps this honest.** A rebase re-runs checks and might
turn one green, so "would freshening clear it?" alone is too loose a test and would silence a red
build. The discriminator is *does this say anything about the change?* — and a failing check does.

## One concept, two consumers

The same set already governs `qualifies_for_branch_update`: a pull request may be brought up to date
when every refusal it carries is freshness-derived or deliberate. So this is **not a second rule** —
it is the same predicate read by a second consumer:

- **`qualifies_for_branch_update`** — may the lane *act* on this? Yes if all obstacles are
  freshness-derived or deliberate.
- **`_held_status`** — is this a *finding*? Not if what remains, beside an exception, is
  freshness-derived.

Expressing it once means a fifth member is handled in both places by construction, which is the
whole point of ruling on the class rather than the case.

**It cannot be a plain set.** `landing_rollout_moved` is freshness-derived only when the base blob
matches the pin, so the predicate takes that fact — as `qualifies_for_branch_update` already does
since `#177`.

## Boundaries

- **A genuinely moved rollout workflow still reports, beside an exception or not.** That is the
  guard `_rollout_term` exists for and this decision does not touch it.
- **Suppression is only ever beside an exception.** A pull request refused on freshness alone, with
  no exception present, is still a finding — it is transient, and the lane will freshen it on the
  next pass.
- **The line still prints.** Suppression means it does not drive exit 3, never that it is hidden.
