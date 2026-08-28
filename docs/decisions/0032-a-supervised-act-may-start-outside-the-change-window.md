# ADR-0032 — A supervised act may start outside the change window

- **Status:** Accepted
- **Date:** 2026-08-26
- **Decided by:** Devon
- **Extends:** ADR-0009 (reach is a declared set), ADR-0010 (factory policy can only refuse),
  ADR-0013 (reach composes toward restraint), ADR-0020 (the factory closes its own loop)

## Decision

**Each of the two acts that consult the change window may carry a reasoned override on its own
call, suppressing that act's window refusal and nothing else.**

- `POST /work-units/{id}/dispatch` may suppress `outside_change_window`.
- `POST /work-units/{id}/pr-merge` may suppress `merge_outside_change_window`.

**Neither implies the other.** **A reason is required.** **The override and its reason are
recorded on the act's own record, not only honoured.** There is no global switch, no schedule,
and no override of any other admission term.

## What was actually being decided

Devon, 2026-08-26, after the window correctly refused the estate's first factory landing into a
repository that deploys, at 09:50: *"The change window time exists to prevent unexpected,
impactful interruptions. This is a build session. We will always need a way to manually override
the change window."*

`factory-policy.toml`'s own rationale for `live_estate` is an argument about **unattended** work —
*"a restart is invisible at 03:00 and is an outage at 15:00"*. A supervised run is neither
unexpected nor unattended, and the policy had no way to express the difference. Measured the same
day: there was **no override of any kind**. `load_factory_policy()` reads a fixed packaged path,
and `change_window_refusal` composes the artifact, the revision's frozen reach and a clock. The
only routes around the hours were to rebuild the image with wider ones — heavy, and easy to forget
to revert — or to write policy bytes into the running container, which is untracked production
policy mutation that the next release silently reverses.

## Why per-act, and why that is the load-bearing part

Starting a run causes a coding action that opens a pull request. That pull request changes nothing
outside a repository until something separately lands it. Landing one changes what is already
serving. A single flag covering both would let a decision about *writing code now* silently grant
*changing production now* — the state-collapse this estate has paid for repeatedly, most recently
in the shape of a check that was correct about the wrong noun.

So the override is a field on each command, each act is handed only the one supplied on its own
call, and nothing reads the other act's record to find one. That last clause is asserted directly:
a unit whose dispatch record carries an applied override is still refused
`merge_outside_change_window` when the landing act is asked without one of its own.

## Why one named refusal and nothing else

`suppressed()` keys on the exact refusal string. The same term also reports a policy artifact this
process could not read, and — on the landing act — a policy declaring no hours at all. Both are
faults somebody has to fix here; an operator saying a run is watched has answered neither.
Suppressing whatever the term returned would turn a broken read into permission, and it is the
form the mistake would actually take, because "the window term objected" is the natural thing to
write.

Every other admission term is untouched: not the declared reach, not the repository allowlist, not
the human authority approval, not the change record, not `criteria_not_verifier_decided`, and not
the hard off-switch, which policy still cannot see.

## Who may override — the reasoned flag, and why the alternative was rejected

**The override is a reasoned flag on the call, and its attribution rests on the named human
authority approval every dispatchable unit already carries** — bound to the exact authority
fingerprint, naming the target repository, the capabilities, the change class and the budget. No
separate human act is added.

The alternative that attributes hardest — a second `/review` approval per run — buys attribution
the estate already has. A unit cannot reach either act without a human having approved its
envelope; the override does not decide *whether this work may happen*, only *whether it may start
now*. Adding a click for the second question when a human answered the first is the performative
approval ADR-0025 argues against, and a control that is structurally uninformative gets clicked
through. Devon objected to it twice on 2026-08-26.

**Rejected and recorded so it is not re-proposed:** a bare flag with no reason — unreadable later,
and the reason is the whole audit value; and a per-run human approval, for the reason above.

## What the record must therefore carry

Because attribution is **inherited** rather than captured, the flag rides a machine-credentialed
call and the record shows a machine. The record must make the inheritance legible, so it carries
the reason, the unit's human authority approval id, and the authority fingerprint that approval is
bound to. A reader asking *"who decided this could run at 09:50?"* answers it without leaving the
data.

It also carries whether the override was **applied** — whether it actually suppressed a refusal —
separately from having been carried. An act inside the declared hours needed no override, and one
refused by a term ordered above the window never reached the window at all. Recording either as
though the override had done something would assert a suppression that never happened, which is
the same discipline `authority_recognised_by` already follows one field over.

## Where it is written down

The dispatch record's `payload` (an existing JSON column) and the `dispatch.{status}` event; and,
for the landing act, the `pr_merge.{status}` event beside the authority fingerprint. No migration
ships. `unit_pr_merge` has no free-form column, and adding one was declined for this increment:
that table is written **only when the factory acted**, so every recordable moment has an event,
and the event is append-only at the database level.

**The trade is stated rather than hidden.** `estate_pr_merge` carries `change_record_id` and
`policy_version` as columns precisely because *"the instant at which it authorised an irreversible
act cannot be recovered later from anything else"*, and the same argument could be made here. It
is weaker here only because the event is written in the same transaction as the record and is
reachable from it by `event_id`. If a later reader needs the override as a queryable column, that
is a migration and its own decision.

## The evidence pack, and one deliberate asymmetry

The override appears in the evidence pack's JSON at full fidelity — that surface is
authenticated. It does **not** appear in the markdown rendering, which factory-runner relays onto
a pull request comment that may be public. The markdown reports that an override happened, and
whether it was applied, and never quotes the operator's words. Free text is what every other
section of that renderer redacts by hand.

## Consequences

- A supervised run can start when a person is watching it, and the record says why.
- The default stays fail-closed and per-act. Nothing is switched off for anybody else.
- A reason cannot be omitted: `ChangeWindowOverride` raises `change_window_override_reason_required`
  at construction, so the requirement holds for a caller reaching the services directly as well as
  for the routes, and it fires before an idempotency replay could answer a malformed request with
  a stale record at HTTP 200.
- The window's hours in `factory-policy.toml` are unchanged. What was missing was never the hours.
- Not retroactive: a dispatch already recorded `skipped / outside_change_window` stays as it is,
  and a later attempt uses a new ordinal.
