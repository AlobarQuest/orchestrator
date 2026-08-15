# ADR-0014 — Graduation evidence discounts the approver, and the boundary belongs to the change that closes the hole

- **Status:** Accepted
- **Date:** 2026-08-02
- **Workstream:** WS-P2.18 Increment 8
- **Supersedes:** nothing. **Extends:** ADR-0011 (a known-good pattern is a withheld refusal).

## Context

R2 is the programme goal — gate the novel, pre-authorize the routine once we know how to do it —
and Increment 3 built the mechanism: a **declared** known-good pattern withholds the
`authority_envelope_novel` objection, and the human requirement falls away for envelopes it
recognises completely. Nothing tells Devon **which patterns are worth declaring**. That is the
graduation ledger, and before it can be built there is a decision about the evidence it reasons
over.

**The evidence base is contaminated and it is known exactly how.** `/review` reads the actor from
the forward-auth header, so a construction-era gate driven by an agent in Devon's browser session
is recorded as `approved_by = "devon"`, indistinguishable from one he read and judged himself.

The production record on 2026-08-02 is exactly the shape that makes this matter:

| authority approvals | decisions | distinct approvers | rejections |
|---|---|---|---|
| 35 | all `approved` | 1 (`devon`) | 0 |

*"Devon approved thirty-five of these and rejected none"* is the strongest-sounding sentence
available and it is worth almost nothing, because a substantial share of those clicks were an
agent's.

The spec (§8) requires this be settled before the ledger exists, and offers two dispositions:
**mark** construction-era approvals, or **discount** them. The prior ruling — *graduate on
OUTCOMES, not approvals* — narrows but does not answer it, because **which** approvals to draw
outcomes from is still a question.

## Decision

**Discount, structurally: the graduation ledger never reads the approver.**

`services/graduation_ledger.py` selects `Approval` rows to learn *that* a gate was cleared and
*when*. It does not select, carry, aggregate or render `approved_by`, and no output of the ledger
varies with it. The discount is a property of the code, not a caveat in prose, and it is pinned by
a test that rewrites every approver identity in the database and asserts the report is unchanged.

**No construction-era boundary is invented.** There is no cutoff date, no marker column, and no
"rows before X are suspect" rule.

## Why not mark

**Marking by identity is unavailable.** There is one identity in the data. There is nothing to
key on.

**Marking by date would be worse than discounting, not merely weaker.** The attribution hole is
still open: `/review` reads the actor from the forward-auth header today exactly as it did in
July, and this increment does not change that. A cutoff would therefore assert that approvals
recorded after it are attributable, which is false.

That is Increment 4's lesson in mirror image, and it is worth stating in both directions because
only one of them is obvious:

- an **absence**-keyed marker ("rows with no marker are construction-era") never expires, and
  silently **absolves** everything future;
- a **date**-keyed marker, laid down while the hole is still open, silently **certifies**
  everything future.

Both encode a distinction that does not exist. The second is more dangerous because it looks like
diligence, and because the resulting ledger would grow a "clean" population that is clean by
definition rather than by construction.

**The boundary belongs to the change that closes the hole, not to this one.** Whenever an actual
attribution mechanism ships — a distinct human credential, a signed confirmation, an agent-driven
marker recorded per ADR-0006's browser-gate consent — *that mechanism's own record* is the
boundary, and it is inherently unforgeable backwards: a row written before it cannot carry it.
Inventing the boundary now, before the mechanism exists, places it in the only spot where it
cannot be checked. The right time to draw the line is when there is something real on one side of
it.

## Why discounting costs less than it appears

Under the standing ruling an approval is **not the evidence**. It is the **index**.

The evidence is what happened to the work after the gate let it through. The approval's job is to
say *the gate was cleared for this envelope shape, at this time* — and that record is
uncontaminated. Whoever pressed the button, the gate did not stop the work, and what followed is a
fact about the envelope rather than about the approver.

The contamination also cuts in the **safe** direction for outcome evidence. An unattributable
clear is a *lower*-quality gate than a scrutinised one. Outcomes that were fine anyway are, if
anything, a stronger case for graduation than outcomes that were fine after careful human review —
which is R7 restated (*"the human review, so far, has been ceremony"*) with the ledger as its
first quantitative expression rather than an assertion.

What is genuinely lost is the ability to say *"a human looked hard at this class of work and never
once objected."* That sentence cannot be recovered for the July–August 2026 population by any
means, and no marking scheme would have recovered it either. It is lost, and the honest response
is to stop offering it, not to launder it through a cutoff date.

## What this does not decide

- **It does not decide that approvals are worthless forever.** When attribution exists, an
  attributed approval is admissible evidence and the ledger may be extended to read it. This ADR
  forbids reading `approved_by` *as it exists today*, on the grounds that today it carries no
  information; it does not forbid a future field that does.
- **It does not graduate anything.** The ledger reports. Declaring a known-good pattern in
  `factory-policy.toml` stays a deliberate human act (ADR-0011), and the ledger writes nothing,
  suppresses nothing and declares nothing.
- **It does not close the attribution hole.** Doing so is a proxy-and-identity change, out of this
  increment's scope. This ADR records that the hole is open, that the ledger is built to be
  correct while it is open, and where the boundary goes when it closes.

## Consequences

- The ledger's report carries no approver identity, so it cannot be read as a claim about anyone's
  judgement. A reader who wants that claim will not find it, which is the intended outcome.
- The ledger is unaffected by the hole and needs no revision when it closes; only an extension.
- **A discount is invisible unless it is pinned.** "The code does not read this column" decays
  silently into "the code reads this column" one refactor later, so the invariance test is
  load-bearing rather than decorative, and it must mutate the column rather than assert its
  absence from a docstring.
