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

**CORRECTED 2026-08-30 by the build session, before building — this ADR said "the data is already
fetched" and that is false.** `_checks_and_gate` (`github.py:262-264`) does `gate = run; continue`,
and the `continue` skips the jobs fetch that only non-gate runs reach. Nothing about the gate job is
fetched at all, so `steps[]` is not being discarded — it was never requested. Extracting the arm
step costs **one new request per gated landing**, roughly +90 a week against the launcher's ~12
requests per landing over a 7-day window. Acceptable, but it is a new call and this ADR claimed
otherwise.

**Also corrected: there are EIGHT transcribed revisions, not "three plus the current one."** All
eight need the new field, and they carry at least four distinct arm-step names — `72391c0f`,
`e849b3a8` and `a4a4b8da` share one; `3457db3c` has ADR-0034's; `77ab867d` has its own; and
`4d87d9b7`, `12880ce7` and `43e37ed9` share a fourth. Read each from the bytes it pins.

## The fail-open this would have created, and the shape that avoids it

Found by the build session before writing code, and it is the reason the change is scoped to
transcribed revisions rather than applied unconditionally.

`audit_landing` returns `(), (), ()` for any basis that is not `auto_merge_rule`. An **untranscribed**
gate revision has no registry entry, so there is no arm-step name to look for — under a naive
implementation it would lose the rule basis, `DRIFT_RULE_UNKNOWN` would become unreachable, and the
finding that exists precisely to catch a rule nobody classified would vanish silently. The guard
this ADR relies on would be disabled by the same change that depends on it.

**So the conjunct is: armed WHEN the revision is transcribed, falling back to today's
`is_machine(landed_by)` when it is not.** Untranscribed revisions stay byte-identical to current
behaviour and keep reaching the audit; the new attribution applies exactly where the registry can
support it. That is also the honest scope — the ledger can only claim the gate armed a landing for a
revision whose arm step it knows.

## A documented doubt becomes a silent failure, and the fix is to key on answerability

Two findings from adversarial review on the implementing diff. Both are about this change and both
are real.

**The revision the ledger pins is the gate at the LANDING COMMIT, not the gate that RAN.**
`_gate_revision` reads `/contents/{GATE_PATH}?ref={landing_sha}`, while the arm step executed under
whatever the pull request's head carried. That mismatch is PRE-EXISTING and is already named in
`audit.py` as `CAVEAT_RULE_SELF_MODIFIED` — *"the pinned revision is this landing's OWN new rule …
the rule that armed it was the previous revision."*

**This ADR makes it worse in the silent direction.** If the two revisions name their arm step
differently, the transcribed name is not found in the run, the outcome reads as absent, and a
genuinely armed machine landing drops to `unattributed` — which `audit_landing` returns early on, so
it is not merely wrong but invisible. It needs a blob move AND a name change AND a pull request
spanning the boundary. The name has changed twice historically and a blob move is imminent under
every open option for the arming identity, so this is live rather than theoretical.

**The general shape is worth carrying: a caveat is where a doubt goes to be ignored — which is
fine until something starts depending on the thing being doubted.** That sentence is `audit.py`'s
own, written about this very caveat, and this change is the something.

**The fix keys the fallback on whether the question was ANSWERABLE, not on registry membership
alone.** An absent outcome conflates three different states — no such step, jobs unreadable (a 404
answers `None`), and an untranscribed revision — and only the last was being caught. So: fall back
to today's `is_machine` when the revision is transcribed **and** a step of that name was actually
found; treat `skipped` as the positive observation it is, the gate declining, which continues to
refuse the rule basis. That closes the landing-commit mismatch as a side effect, and it makes
`skipped` and absent produce DIFFERENT bases — a stronger proof than the one this ADR asked for.

**Measured, not assumed:** a non-Dependabot pull request's gate run concludes `skipped`
(`orchestrator#208`), and `change-manager#72` has no gate run at all — so the existing
`outcome == "success"` conjunct already refuses the job-skipped shape, and this fallback cannot be
reached by a landing the gate never considered.

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
- **`rules.py` gains a field, so all EIGHT transcribed revisions must declare it**, each read from
  the bytes it pins rather than assumed to match today's.
- **The record pass re-reads a rolling 7-day window every night**, so an ALREADY-STORED landing whose
  basis flips under this change is an `observation_conflict` — skipped, incomplete, exit 3 nightly
  until it leaves the window. This is the same trap ADR-0036 hit one field over. It must be measured
  with a before/after `record --dry-run` at 7 and 30 days BEFORE merging, and any flip inside the
  7-day window raised rather than shipped.
