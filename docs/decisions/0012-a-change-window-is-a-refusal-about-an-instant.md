# ADR-0012 — A change window is a refusal about an instant, asked once, at admission

- **Status:** Accepted
- **Date:** 2026-08-01
- **Workstream:** WS-P2.18 Increment 5
- **Supersedes:** nothing. **Extends:** ADR-0009 (reach), ADR-0010 (the artifact refuses),
  ADR-0011 (a known-good pattern is a withheld refusal).

## Context

Devon's original constraint for this workstream was one sentence: *if I am on the phone with a
client, I do not want my machine rebooted.* That is a statement about **when** work may start,
keyed on **what the work touches** — which is `reach` (ADR-0009), not the change class, not the
risk level, and not where the work executes.

The obvious way to write that down is a grant: *work may run between 02:00 and 06:00.* The policy
artifact cannot express one. ADR-0010's guarantee is structural rather than conventional — no value
in the schema permits anything, and `factory_policy.py` imports nothing from `config`, so it cannot
see the hard off-switch and therefore cannot overrule it. A window has to arrive as the same shape
as everything else in that document: a reason to object.

## Decision

### 1. The window is written as a boundary and answered as a refusal

`[reach.<member>.change_window]` names the hours in which policy raises **no objection** to work of
that reach starting. Outside them the answer is `outside_change_window`. Same information,
opposite polarity, and the polarity is what keeps the guarantee: a grant is unwritable here.

A row with **no** window raises no objection at all. That is deliberately **not** confusable with a
window that failed to parse, which stops the whole document loading — and a document that does not
load permits nothing. Absent is silent; broken is loud and total.

### 2. Not every reach gets one, and the two that do not are reasoned, not overlooked

- `source_repository` — **no window.** Work of this reach is inert on arrival: writes land in a git
  repository and nothing outside it changes until something separately acts on the result. It is
  also what keeps this repository's other test suites independent of the time of day.
- `live_estate` — **02:00–06:00 America/New_York.** Something already serving is changed and the
  effect is immediate. Includes the orchestrator changing itself: a restart is invisible at 03:00
  and an outage at 15:00.
- `external_system` — **no window,** and the absence is the decision. A window moves *when* work
  runs; it does nothing about how hard the work is to undo, which is this reach's actual risk.
  Restraint here comes from a person reading the envelope — no known-good pattern is declared under
  this row, so every envelope of this reach draws the human objection. See §4 for why the
  alternative was worse than it looks.
- `operator_machine` — **02:00–06:00 America/New_York.** The original constraint. Identical to the
  `live_estate` window on purpose; see §4.

### 3. Time is local, the zone is explicit, and the question is asked about an instant

The window is declared in local time with an IANA zone in the artifact. A naive reading would be
wrong on a server, which runs in UTC, and wrong twice a year everywhere else.

Evaluation **converts an instant into the declared zone** and never constructs a local time from a
naive one. That single choice makes daylight saving total rather than ambiguous — all the ambiguity
lives in the other direction, where one local reading names two instants or none. Both awkward
hours then follow without a special case, and both are stated because neither is obvious:

- **The hour that happens twice** (first Sunday in November): both occurrences read as the same
  local time, so a window covering that hour is open across both — one extra hour of openness, once
  a year, in the *widening* direction.
- **The hour that does not happen** (second Sunday in March): no instant ever reads as a local time
  inside it, so a window confined to it is open for no time at all that day — the *narrowing*
  direction, which is the safe one to be surprised by. Work of that reach waits a day.

The clock is a **parameter**, defaulting to the transaction's own timestamp. Nothing that decides a
window may read a clock it was not given; a structural test asserts that of the production modules
and of the test module both. The related local trap is already recorded: `work_units.updated_at`
cannot be back-dated because a trigger rewrites it, so ageing data is not a way to exercise
time-dependent behaviour either. Injection is.

### 4. Windows compose by intersection, which constrains what may be declared

Work reaching two places must be inside **both** windows, exactly as it must clear both of their
other objections — the same union-of-refusals composition ADR-0009 requires, and for the same
reason: adding a member can only narrow.

The consequence is a real editing constraint. Two rows whose hours do **not** overlap make any
package reaching both of them **unrunnable** — not restrained, unrunnable. That is why the two
declared windows are identical, and it is the deciding argument against the office-hours window
`external_system` might otherwise have had: office hours do not overlap the night window, and a
package that rotates a credential in an external console *and* writes it to the operator's machine
would have become permanently unrunnable. The reach vocabulary names that shape as a real example.

### 5. The window governs admission, never execution

It is consulted when work is about to be sent and at no other moment. **A window closing over a run
in progress must not touch it.** The worker calls the orchestrator back at the *end* of its run and
the call that reports a failure fails the same way the call that reports success does, so cutting a
live run off at the boundary would not stop it cleanly — it would strand the unit in `executing`
with its attempt spent. That is a documented outage shape in this repository, not a hypothetical.

A unit that was admissible a second ago and is not now is simply not admitted: a record is written
with `status: skipped` and reason `outside_change_window`, and the next attempt succeeds when the
window opens. Nothing is cancelled and nothing is retried differently.

### 6. It is its own admission term, reported BELOW every standing one

Increment 4 established the shape of this mistake: specifying grandfathering as "a withheld
`reach_undeclared` refusal" was a **fail-open**, because inside `authority_refusals` an empty
refusal set does not soften the human requirement — it deletes it. The lesson generalises to *find
every consumer of the refusal set you extend and check what each does with empty.*

So the window does not extend `refusals_for` (which `authority_refusals` reads) and does not extend
`admission_refusals` (which grandfathering reads). It is a separate term with a separate reader,
placed **below** the reach term, the authority term and the envelope terms in `_blocked_reason`.
The ordering rule is the mirror of Increment 4's: reach sits high because approving cannot fix a
missing declaration, and the window sits lowest because it is the only term that clears without
anybody doing anything. Reporting a self-clearing condition ahead of a standing one sends an
operator away to wait for a moment at which nothing has changed.

### 7. A reach nobody declared draws no window objection — bounded, and stated

Every window hangs off a reach row, so an undeclared reach has no row to consult. There is no
honest way to pick one: reach is declared, never inferred (ADR-0009 R8).

The fail-closed-looking alternative — requiring *every* declared window at once — is worse than it
sounds, and §4 is why: two windows that do not overlap would make such work permanently unrunnable
rather than merely restrained, which is not a safety property but a brick. The exposure is exactly
the set the admission term still lets through, which is the named grandfathering list of Increment
4, which is one revision and which deletes itself.

## Consequences

- **Changing a window costs a release, and a release restarts the orchestrator.** Noticing an edit
  is free — the artifact is re-read per consultation and never cached — but getting new bytes onto
  a running process is not. The operational rule is already recorded and applies unchanged: edit
  policy only when no run is live (workflow concluded, unit out of `executing`, cost actuals
  recorded). Devon's model is *set the window once*, which is compatible; this is not designed for
  frequent edits.
- **Schema version 4**, an additive bump made in the same commit that teaches the loader and ships
  the code reading the new field. `SUPPORTED_SCHEMA_VERSIONS` remains an exact set, not a floor.
- **`tzdata` is now a declared dependency.** The artifact's windows name IANA zones, so a runtime
  without a zone database cannot load the artifact — fail-closed, and also a factory halt. The
  runtime base image ships one today; declaring the package makes that true of any image.
- **Day-of-week is deliberately not modelled.** A window is a daily local range and nothing else.
  Adding weekday selection later is an additive schema bump; adding it now would be a field with no
  decision behind it.
- **Execution locus remains unmodelled and is not this.** `local-heavy` in `routing-policy.toml`
  describes where work *executes*; reach describes what it *touches*. A window keyed on the second
  says nothing about the first.
