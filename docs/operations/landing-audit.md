# The landing audit — what replaced the human gate on a routine update

ADR-0016 moved routine dependency updates off the human merge gate. The gate was never a
correctness control — nobody reads a lockfile diff and decides — it was a **visibility** control:
it guaranteed a person saw every landing. Removing it removes the visibility, and these two
detectors are what puts it back, as a report rather than a stop.

Neither detector acts. Both read and file observations, the same shape as the reconciliation
lane: a wrong detector costs a wrong record, never a wrong action.

---

## Detector A — permissive drift

**Did every rule-permitted landing actually satisfy the rule that was in force when it landed?**

The ledger records the update's own metadata as values and pins the gate by its **git blob sha at
the landing commit**, so this is answerable by re-evaluation rather than by trusting the gate's own
report of itself. `src/landing_ledger/rules.py` transcribes, by hand, every revision the estate has
run; `tests/fixtures/auto-merge-rules/<sha>.yml` holds the real bytes, and a test recomputes each
fixture's blob sha and compares it to the registry key. A revision with no entry is a **finding**,
never a pass — a rule nobody classified is deciding landings.

The 2026-08-07 revision whose ecosystem literal was hyphenated is transcribed **as written**, so
the audit reproduces the defect rather than erasing it. Same landing facts, different pinned
revision, opposite verdict — that is the whole reason the pin exists.

Findings: `rule_not_satisfied`, `rule_revision_unknown`, `rule_revision_missing`,
`update_metadata_missing`, `rule_run_did_not_succeed`, `check_did_not_pass`.

## Detector B — the quiet gate

**Is anything eligible, green, and simply not landing?**

The ledger cannot see this. A pull request that never lands leaves no landing record, so the
failure is an *absence* in the very thing that would report it. B therefore reads the open updates
from GitHub and classifies each against the rule **currently installed** in that repository.

It covers two of the three known generators — a rule that stopped arming, and the estate's habit of
disarming the siblings of whichever update lands first — because both present identically:
eligible, green, unarmed.

Healthy outcomes, which produce nothing: ineligible and unarmed (the rule declining to act),
eligible but red (the checks doing their job), and armed, green and freshly settled (a landing
about to happen).

Findings: `eligible_green_and_not_armed`, `armed_green_and_still_open`,
`update_metadata_unreadable`, `current_rule_revision_unknown`.

## The third generator is not in B

A recorder that silently covers less than it claims is a property of the recorder, not of landing.
Folding it into B would put "the producer under-reported" behind a detector whose input is what the
producer produced — a check that consumes the thing it is meant to detect. It is answered where it
happens: `landing-ledger record` no longer exits 0 on a pass that could not read a repository or
dropped a landing it did read.

## Caveats are not findings

A caveat qualifies the audit's own evidence; it does not assert that anything is wrong, and it does
not raise the exit status. It is still recorded, so it cannot be lost by being quiet.

- `rule_pinned_after_this_landing_changed_it` — the ledger reads the gate at the *landing* commit,
  so an update that edits the gate is pinned to the rule it installed rather than the one that
  armed it. Real: `factory-runner#42`.
- `no_rule_installed` — the repository has no gate. Three deliberately do not, and `orchestrator`
  **cannot**: its own architecture guards forbid the command the gate runs. Reporting that as a
  violation would make the detector permanently red about a scope decision somebody made. The
  count of green updates that will therefore never land unattended is carried in the detail.

## Where the report goes

One `landing_audit` observation **per repository per pass**, written whether or not anything was
found. Unconditional on purpose: a detector that writes only when it finds something is
indistinguishable from a detector that has stopped running, and this estate has already shipped one
reporting obligation that was silent for a whole workstream.

`POST /api/v1/observations` is the OBSERVER role's entire write surface, so it is also the only
place a report can go. **Observations do not appear in the `/review` queue** — the queue lists
pending human decisions, and a finding is not one. Read them with:

```
GET /api/v1/observations?observation_type=landing_audit
```

The launcher's exit status is the other half, and is what a scheduled run actually surfaces.

## Running it

```
scripts/run-landing-ledger.sh              # record, then audit
scripts/run-landing-ledger.sh --dry-run    # writes nothing; prints what it would
scripts/install-landing-ledger-launchd.sh  # daily at 07:30, operator-run
```

| exit | meaning |
|---|---|
| 0 | everything was measured and nothing was found |
| 1 | the tool itself failed — a missing credential, an unhandled error |
| 2 | something was found; the pass worked, reality did not |
| 3 | some part of reality could not be read, so the answer is missing rather than clean |

3 outranks 2: an incomplete pass cannot claim it found everything there was to find. A broken tool
and an honest finding sharing one exit code is a collision this estate has already paid for once.

## Prerequisites

- Migration `0023_wsp36_landing_audit` applied and an image carrying it, or every write is refused:
  `observation_type` is bounded by a database CHECK and by the running image's vocabulary.
- `LANDING_LEDGER_TOKEN` — the OBSERVER bearer, BWS `f793576f-…` in `SDS Operator`.
- `LANDING_LEDGER_GITHUB_TOKEN` — **has no BWS record.** The launcher falls back to `gh auth
  token`, which means a scheduled job resting on an interactive login, and it dies silently when
  that login is re-issued. Backlogged P2 `113af446955b`.

The launcher records a **seven-day** window, not the thirty-day default. Recording costs about
twelve GitHub requests per landing and the 2026-08-08 backfill exhausted the 5000/hour limit in one
thirty-day pass, so a daily job at that window would run out partway through every morning and exit
3 forever. A gap longer than a week needs one manual `landing-ledger record --days N`.
