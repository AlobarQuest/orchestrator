# ADR-0013 — A lease is a refusal to reassign, and the artifact may only lengthen it

- **Status:** Accepted
- **Date:** 2026-08-02
- **Workstream:** WS-P2.18 Increment 6
- **Supersedes:** nothing. **Extends:** ADR-0009 (reach), ADR-0010 (the artifact refuses),
  ADR-0012 (a change window is a refusal about an instant).

## Context

Every claim in this system's history got the same fifteen minutes, written once in
`kernel/leases.py` and never revisited. The spec calls it *"one arbitrary 15 minutes"* and it is
exactly that: chosen before any work had run, applied identically to a dependency bump that
finishes in under a minute and to a change that waits on somebody else's console.

Moving it under the policy artifact runs straight into ADR-0010's guarantee. Every other value in
that document is a **reason to object**, and the guarantee is structural rather than conventional:
there is no way to write a permission, so nothing in the artifact can widen what the hard
off-switch allows. **A duration is a number.** Numbers do not obviously have a polarity, and a field
whose value could move in either direction is precisely the shape that guarantee was written to
exclude.

There is a second problem, and it is the one that decides the direction. **R6 is a warning, not a
feature request: the lease was never stall control.** Nothing in this system notices a worker that
has hung. A lapse does not transition anything and nothing reclaims on its own — it only makes the
unit available to a SYSTEM actor who asks. So "tune the lease" cannot be answered by asking what
bounds a hung worker, because the lease bounds no such thing and this increment must not pretend it
does. That hole is real and it is WS-P2.19's.

## Decision

### 1. A lease is the period in which reassignment is refused

`[reach.<member>.lease]` names how long this orchestrator **refuses to hand a unit of that reach to
a second claimant**. Read that way the value has a polarity after all: a longer lease is a longer
refusal, and a shorter one is a shorter refusal — which is to say, less restraint.

That reading is not a way of speaking. It is enforced by two bounds the build owns and the document
cannot reach:

- A declared lease must be **strictly longer** than `DEFAULT_LEASE`. There is no way to write a
  hold shorter than the one this build already applies, so no edit to the artifact can cause a unit
  to be handed away sooner than it would have been. It also means a row cannot restate the default,
  which would be a second copy of a number that already lives in the kernel.
- A declared lease may be **no longer** than `LEASE_CEILING`. Without an upper bound, "policy cannot
  switch reassignment off" would be true of the type and false of the values an operator can
  actually write: a lease of a year silences reclaim as completely as disabling it. This is the same
  reasoning that capped `dead_letter_stalled_approval_seconds`.

So the artifact's only expressible effect on a lease is *"refuse for longer than you otherwise
would, by an amount somebody decided and wrote down with a reason."*

### 2. A duration is not in the admission conjunction at all

The stronger claim, and the one that makes the first safe. `lease_for` is consulted **after** work
has been admitted, sent and claimed. It is never asked whether work may run, so it cannot answer
that question wrongly. The hard off-switch, the change window, the reach admission term and the
human authority gate are the terms that decide whether a unit is sent; a lease decides how long the
worker that already holds it keeps it. Nothing written here can make work happen that would not
otherwise have happened.

### 3. Longer is the conservative direction, so composition is the MAXIMUM

The two failure directions are not symmetric, and the asymmetry is what sets the direction of every
rule above.

- **A hold that is too short** lets go of a unit whose worker is still alive. Two claimants then act
  on the same target — and the first one cannot even record what it did, because
  `validate_active_claim` refuses an evidence write from an attempt whose lease has lapsed. That is
  a correctness failure, and its cost scales with reach: worst where the estate cannot put the
  result back.
- **A hold that is too long** delays a reclaim of a genuinely dead attempt. Per R6 that is not a
  regression in stall bounding, because there was none: nothing reclaims automatically, so the cost
  is an operator waiting before asking for a unit back.

Composition over a reach set is therefore the **maximum** of its members' durations, with a member
that declares nothing contributing the default. Adding a member can only **lengthen** the answer —
the same monotonicity the refusal sets have, in the same safe direction — so a set is shortened only
when every member of it was decided to need less, and none can be.

Note this is the opposite arrangement from the change window, which composes by intersection and
therefore *narrows* as members are added. Both compose toward more restraint; restraint just points
the other way for a hold than it does for an hour.

### 4. Undeclared reach gets the default, by name, and does not raise

A unit whose package declared no reach — or whose declaration this build cannot read whole — gets
`DEFAULT_LEASE`. That is the fifteen minutes every claim in this system's history has had, so the
population that exists today is unchanged, which is the point: only work somebody has described
moves.

Refusing instead would be refusing to grant a lease to a worker that **already holds the unit** —
restraint aimed at the wrong actor at the wrong moment. Whether such a unit should have been sent
at all is the admission question, and `reach_admission` answers it there by refusing outright
(ADR-0009, Increment 4). The same reasoning covers an artifact this process cannot read: the claim
path falls back to the default while the admission path refuses, so a broken document stops work
**arriving** rather than stranding work that already arrived.

### 5. Every path that grants or extends a hold reads the same source

There are **three**, and only the first is obvious:

| Path | Function |
|---|---|
| a fresh claim | `claim_unit` |
| an extension | `renew_claim` |
| a claim granted after a lapse | `reclaim_expired_claim` → `_acquire_reclaimed_claim` |

The third never calls `claim_unit`, so a duration read only there would be honoured on the first
attempt and dropped on every one after it — on precisely the path a lapsed lease leads to, since a
reclaim happens *because* the previous hold ran out. The second was not named in this increment's
plan at all, and a renewal that reset the hold to the kernel default would quietly undo a considered
one on the path a long-running attempt takes by definition.

All three call `services.lease_policy.claim_lease`, and the reclaim and renewal cases are each
proven against the reach's own duration rather than the default.

### 6. What the shipped artifact declares

| Reach | Lease | Why |
|---|---|---|
| `source_repository` | none — the default | The only reach whose work has been observed end to end here: a dependency bump ran prepare-to-submit in under a minute. A second claimant costs a duplicate pull request, not damage. |
| `live_estate` | **30 minutes** | The work waits on an image build, a container swap, a health check settling. Minutes of wall clock in which the worker is doing nothing a lease can see. |
| `external_system` | **60 minutes** | Its wall clock belongs to somebody else, and it is the reach where two claimants acting at once cannot be undone by this estate at all. |
| `operator_machine` | none — the default | This row's work is repository-shaped: what lands is an edit to a checkout, and the operator's agent configuration is itself a git repository. It earns a window, not a longer hold. |

Two declarations and two reasoned absences, on the ADR-0012 precedent. The absences are decisions,
recorded in each row's own rationale; declaring a longer hold for the other two would be a number
chosen to look decided.

## Consequences

- The lease is now keyed on reach and is no longer one constant. **It still bounds no hung worker,
  and no report from this workstream may say otherwise.**
- Changing a lease costs a release, exactly as changing a window does, and for the same reason: the
  document is re-read on every consultation and never cached, but getting new bytes onto a running
  process is a deployment. So a lease is edited only when no run is live.
- `RENEWAL_CADENCE`, a five-minute constant defined in `kernel/leases.py` since WS-3.1, had **no
  reader anywhere** in either repository. It is deleted here rather than carried forward: a dead
  constant implies a renewal discipline that does not exist, and the runner renews only on an
  explicit `local-heavy-renew` command. A dead knob is the same defect as a dead function.
- A future reach member with no `lease` inherits the default, which is the safe end. It does not
  need a row to be safe — but it does need a row to exist at all, because the artifact's total
  coverage rule is unchanged.
