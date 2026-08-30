# ADR-0037 — A landing is attributed to the rule that armed it, not to the login that merged it

- **Status:** Accepted
- **Date:** 2026-08-30
- **Decided by:** Devon
- **Relates to:** ADR-0016, ADR-0018, ADR-0034
- **Unblocks:** ADR-0035, which is blocked precisely because changing the arming credential
  silently changes what the ledger records

## Decision

**`basis_of`'s rule branch decides on whether the gate ARMED the landing, not on whether the
merging login ends in `[bot]`.** `landed_by` continues to be recorded; it stops being the thing the
basis is gated on.

**Scope is the rule basis only.** The factory branches keep `is_machine`, because there "was a
person in the loop" is the actual question being asked and it is keyed on a work unit rather than
on a suffix.

## Why the login was never the fact

`is_machine` is `login.endswith("[bot]")` — a **login-suffix heuristic standing in for "was this
attended?"**. It has been a known residual since 2026-08-10, `basis_of`'s own docstring names it,
and ADR-0035 walked into it: arming with a user PAT makes `merged_by` a `User`, so every cascade
landing would record as `human` and Detector A would silently stop auditing the native lane.

**Look at what a cascade landing actually records.** Measured on `infraops-mcp-server#87`, landed
2026-08-30:

- *what changed* — `package.json`, `package-lock.json`, commit, head, base, pull request number
- *which dependency* — `@anthropic-ai/sdk`, ecosystem `npm_and_yarn`, `semver-minor`
- *what permitted it* — `decision: ADR-0016`, the rule path, the rule blob `3457db3c`, the rule run,
  the rule outcome
- *what was verified* — both checks named, both `success`, `checks_observed: 2`
- *who pressed merge* — `landed_by: github-actions[bot]`

Twelve substantive facts, and the login is one. The claim that carries the weight is *"this landing
conformed to the rule pinned at blob `3457db3c` and these named checks passed"* — re-derivable and
re-evaluable against a transcription. Devon, 2026-08-30, asking what the "who" was doing for this
class of change, is what settled it.

## The consequence to take deliberately

**A person merging an ARMED pull request now records as `auto_merge_rule`.** Under the old rule it
recorded as `human`.

That is intended, and for this class of change it is the truer of the two. The rule had already
permitted it and the named checks had already verified it; a person clicking merge on a Dependabot
bump exercised no judgment about the diff that the record does not already hold. HQ initially
argued the opposite — that this weakened the record — and was defending a proxy as though it were
the fact.

Where attendance genuinely matters is a FACTORY landing, and that is `BASIS_FACTORY`: a different
branch, keyed on a work unit with a human authority approval and verifier-decided criteria behind
it. It is deliberately untouched here.

## The design is forced by the API, and it lands in the registry that already exists

**The arm step can only be identified by NAME.** It carries no `id:` (only the metadata step does),
and GitHub's job-steps payload exposes `name`, `status`, `conclusion` and `number` — no id. So the
discriminator is a string match against workflow prose, which is the coupling this estate normally
refuses.

**Which is why it belongs in `rules.py`, transcribed per blob sha alongside the rule it describes.**
That registry already exists for exactly this problem: it is hand-transcribed, keyed by the gate's
blob, held to the bytes by a fixture, and fails closed on an unknown revision. A revision that
renames its arm step gets a new blob, hence a new entry, hence the new name — the coupling becomes
self-maintaining rather than brittle. ADR-0034 has already renamed that step once.

**The data is already fetched.** `_checks_and_gate` reads the gate run's jobs and keeps only
`name` and `conclusion`, discarding `steps[]`. No new API call.

## Consequences

- **Forward-only.** The 657 stored landing observations keep the basis they were recorded with;
  `audit_landing` reads the stored value rather than re-deriving it. Nothing is re-attributed and
  nothing needs to be — which also means the change is invisible until the next cascade landing.
- **`skipped` is the excluded case, not a failure.** An arm step that did not run because its `if:`
  excluded the ecosystem reports `skipped`; that is the cascade declining, and it must not read as
  armed.
- **It unblocks ADR-0035 without endorsing it.** After this, the arming credential and the recorded
  basis are independent, so the arming identity becomes a free choice rather than one that silently
  destroys the record. Which identity to arm with is still ADR-0035's open question.
- **`rules.py` gains a field, so every transcribed revision must declare it** — including the three
  historical ones, whose arm-step names must be read from the bytes each pins rather than assumed
  to match today's.
