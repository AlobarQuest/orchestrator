# ADR-0011 — A known-good pattern is a withheld refusal, not a permission

**Status:** Accepted · **Date:** 2026-08-01 · **Workstream:** WS-P2.18 (Increment 3)
**Supersedes nothing. Builds on:** ADR-0009 (reach is the key), ADR-0010 (the artifact can only refuse).

## Context

Every work unit needs a human approval bound to its authority fingerprint before it can be
admitted for work. R2 is that this should stop being true of work we already know how to do:
*"I will want to gate individual novel situations, and pre-authorize work once we know how to do
it."* R7 is why that is not a safety reduction — the review being lifted has been ceremony.

**But the concept is permissive and the artifact cannot express permission.** ADR-0010 settled that
`factory-policy.toml` has no value that permits anything: `refusals_for` returns the reasons policy
objects, and an empty tuple is *"raises no objection"*, deliberately weaker than *"go ahead"*. That
is R4 made structural rather than conventional — the off-switch cannot be outranked by a policy
that has nothing to grant. A `known_good` list that blessed envelopes would undo it in one commit.

And the trust root moves either way. As gating approaches zero, containment stops being *"a human
must click"* and becomes *"the detector decided this was not novel"*. The detector inherits the
whole job the gate was doing.

---

## Decision 1 — invert the gate rather than add a permission

**The human-authority requirement was unconditional in code. It becomes conditional on policy, and
policy still speaks only in refusals.**

Every envelope draws `authority_envelope_novel`. A declared known-good pattern **withholds** that
objection from an envelope it recognises. Nothing is ever permitted; an objection is merely absent,
and the requirement is one term in an admission conjunction whose other terms are untouched.

Three properties follow, and each is the reason for the shape rather than a bonus:

- **The default is asking.** A pattern that fails to load, a reach nobody declared, a member no
  pattern is declared under, and a field no pattern accounts for all resolve identically — to the
  requirement standing, which is the behaviour that existed before this increment.
- **R4 survives untouched.** The hard off-switch is still the first term of admission and
  `factory_policy.py` still cannot see it. The most a pattern can do is decline to add an
  objection to a list it never had the power to shorten.
- **Undeclared reach is benign here.** No authored package declares reach yet (see Consequences),
  so today every unit draws `reach_undeclared` and keeps its human gate. This increment cannot
  halt anything, because the state it produces for the whole existing population is the status quo.

## Decision 2 — a pattern is a total description of an envelope, keyed under a reach row

**What a pattern is.** A named, dated, reasoned description of one envelope shape, declared inside
a `[reach.<member>]` row. Reach alone would be far too coarse — every `source_repository` unit
would skip the gate whatever its envelope authorised — so the pattern describes the **envelope**,
and reach is the row it is declared under. A unit that reaches two places must be recognised under
**both**, which is ADR-0009's intersection-of-permission in the only vocabulary this artifact has.

**How a match is decided: totality.** `AuthorityEnvelope.normalized()` has exactly six fields and
the matcher checks all six. An envelope is recognised only when the pattern accounts for every one
of them, so anything the pattern did not describe falls through to a human. That includes
`unknown_fields`, which is how a field this build has never heard of is refused rather than
ignored — the fingerprint records such a field by *name* only, so its value was never attested by
anybody.

**What varies within a pattern**, and nothing else does:

| Envelope field | Declared as | Recognised when |
|---|---|---|
| `unknown_fields` | not declarable | it is empty |
| `change_class` | `change_class` | it is equal |
| `capabilities` | `capabilities` table | every (capability, level) pair of the envelope is one the pattern declares — a **subset**, because less authority is not novelty |
| `budgets` | `max_attempts`, `max_llm_calls` | declared and at or below the ceiling; `null` is unbounded, so it is not |
| `conformance` | `conformance_status` | complete, at that status, and `accepted_standards` empty — a waiver is exactly what a person should still look at |
| `constraints` | `target_repositories`, `command_prefixes` | keys are exactly the four the shape has; `work_unit_id` is this unit's own; the repository is one named; every command is inert and begins with a declared prefix |

**Commands are where the judgment is, and both halves of the rule are load-bearing.** A declared
prefix is compared as **tokens**, not as a string — `uv add` as a string prefix also matches
`uv address-book`. And a command is rejected if any shell control character appears **outside
quotes**, because a prefix bounds only the first tokens and `uv add x && curl … | sh` begins with
`uv add`. Quote tracking is what makes the rule usable rather than merely strict: real commands
carry version specifiers like `'ruff>=0.15.21'`, whose `>` is a redirection everywhere except
inside the quotes it is actually written in. The scan is character by character rather than lexed,
because a lexer resolves quoting and returns tokens in which a quoted `>` and an unquoted one are
indistinguishable — the direction that fails open.

**`KNOWN_FIELDS` was sufficient.** Every field the pattern reads is already a known field, so the
approval fingerprint attests all of them by value and nothing had to be added to that set — which
matters, because adding to it rewrites every authority fingerprint in the live ledger.

**Schema 2 describes one envelope shape: the command-executing one.** The constraint key set is
exact, so a unit that runs no commands is not describable and is not recognised. That is the
repo's *mechanism and consumer in the same commit* rule applied to data: a second shape lands in
the version that ships code needing it.

## Decision 3 — patterns are declared, never learned

R2 is *"pre-authorize work once we know how to do it"* — a deliberate act with a date and a
rationale on the record. No pattern is derived from the approval ledger. A detector that learned
from approvals it had itself caused to be suppressed would be a self-referential trust root, and
the ledger it would learn from is the one spec §8 records as already contaminated: construction-era
gates driven by an agent are attributed to a person.

Widening a pattern is therefore an edit to `factory-policy.toml`, reviewed as a diff like any other.

## Decision 4 — a lifted requirement is recorded, and never as an approval

**No approval row is ever written on a human's behalf.** Three independent reasons, each
sufficient: there is no standing human credential and never will be (ADR-0006); the graduation
ledger reasons over exactly this evidence; and an audit trail recording a decision nobody made is
worse than no record.

Instead, admission writes an **event**, `authority.human_gate_not_required`, carrying the patterns
that recognised the envelope, the artifact version and file they were read from, the envelope
fingerprint, and the attempt. It is attributed to the system actor that read the artifact. It says
the requirement did not apply — never that anybody agreed to anything — and `authority_approval_id`
stays null, so no query for a person's approval can return it.

**It is written where the lifted requirement is acted on**, not where it is merely reported.
Readiness and the review queue are reads, derived on demand and re-derivable from the artifact at
any time; writing a row per render would be noise. Admission is the point at which the absence of
the objection has an effect, and the record cites the artifact version precisely because the
artifact is editable — that is the difference between a record of what was decided and a guess
made from whatever the file says later.

**Cited only where used.** A unit carrying a human's approval passed the term on that approval, so
no suppression is recorded for it even if policy would also have recognised it.

## Consequences

- **The three consumers move together.** Admission (`services/dispatch.py`), readiness
  (`services/packages.py` → `kernel/readiness.py`) and the review queue
  (`services/pending_decisions.py`) all consult `services/authority_gate.py`. Leaving the queue
  alone would have delivered none of R2 however correct admission became — Devon would still be
  asked to approve every bump.
- **Readiness gained a second fact, not a redefined one.** `authority_recognised_by_policy` sits
  beside `authority_approved` and either satisfies the requirement. Folding them together would
  have made readiness report that somebody approved a unit nobody approved.
- **The artifact is read once per consulting call, and the queue consults it per unit.** That
  follows ADR-0010's no-cache rule; the queue holds a handful of units and the document is two
  kilobytes.
- **Nothing is recognised today.** `reach` is accepted by `intent-packages` (PR #51) but **no
  authored package declares it** — 24 of 24, of which 14 can never be edited because their YAML is
  hashed into lineage approvals. So the mechanism is inert until a package declares reach, and the
  first thing that makes R2 pay is a dependency-update package declaring
  `reach: [source_repository]`. The permanent 14 are ADR-0010's recorded consequence and
  Increment 4's problem.
- **One pattern is declared**, for uv dependency pin bumps into one named repository. It does not
  recognise the historical GAP-4 envelope, which ran `uv sync --locked` and `uv run make check` —
  correctly, since the delivery profile that emits these lists has itself since forbidden
  `make check` in an envelope.
- **Schema version is now an exact `{2}`.** A version-1 document no longer loads. Document and
  loader ship together in one image, so there is no process that can meet the older shape; and a
  set that accumulated versions would be a floor by another name.

## Alternatives considered

- **A `permitted: bool` (or `requires_approval: false`) per pattern.** Rejected — it is ADR-0010's
  already-rejected alternative wearing a new name. The moment a permission is expressible, R4
  depends on every future caller remembering an ordering rule.
- **Key the pattern on reach alone.** Rejected: every `source_repository` unit would skip the gate
  regardless of what its envelope authorised, which is most of the population and all of the risk.
- **Learn patterns from repeated approvals.** Rejected: Decision 3.
- **Fingerprint allowlisting** — pin the exact `authority_fingerprint` of previously approved
  envelopes. Rejected: every envelope carries a unit-specific stamped id and a version string, so
  no two fingerprints ever repeat. It would recognise nothing, ever.
- **Regular expressions over commands.** Rejected: unreviewable, and the failure direction of a
  `.*` in a policy file is silent and permissive. Token prefixes plus an inertness rule make both
  halves of the claim readable by the person deciding them.
- **A first-token executable allowlist** (`uv`) instead of prefixes. Rejected: it would bless
  `uv run <anything>`, which is arbitrary execution.
- **Write the suppression record at every surface that consults policy.** Rejected: reads would
  write rows on render, and the reads are re-derivable from a versioned artifact.
- **Synthesize the human approval and leave every consumer unchanged.** Rejected outright — it is
  the one thing §3.1 of this increment's brief forbids, and it would end the graduation ledger's
  usefulness.
