# ADR-0043 — The dependency-update budget is one number at four sites, and it is not 4

- **Status:** Accepted
- **Date:** 2026-09-08
- **Decided by:** Devon
- **Relates to:** ADR-0011 (a known-good pattern is a withheld refusal — the pattern this budget
  gates), ADR-0028 (approval by policy for standing dependency-update revisions — where the grant
  was created), ADR-0001 (the work-unit authority envelope contract)

## Decision

`budgets.max_llm_calls` for the dependency-update lane is **360**, and it has been expanded three
times from an original 4. **That history had no decision record**, which is why one site was left
at 4 for eighteen days after the others moved and nothing said so. This ADR is the trail.

The number appears at **four sites**, it means a **different thing** at each, and the correct
relation between them is **not the same relation** in each pair. That is the substance of this
decision; the value is the easy half.

## The trail, measured from the repositories rather than recalled

| date | site | value | commit | why |
|---|---|---|---|---|
| 2026-07-17 | profile `BUDGETS` | **4** | `cefbefb` | the profile is created; 4 is a placeholder nobody had run the lane to test |
| 2026-08-01 | orchestrator known-good ceiling | **4** | ADR-0011 | mirrors the profile default *of that day* |
| 2026-08-19 | grant `approval-policy.toml` | **120** | `ecce1b2` (#71) | ADR-0028's standing grant |
| 2026-08-19 | profile `BUDGETS` | **120** | `72239fa` (#72) | closes a grant-vs-stamp divergence that a comment had NAMED since #71 |
| 2026-09-03 | grant + profile | **240** | `811e8c7` (#82) | *a turn is not an LLM call* — 120 bought 1.8 attempts, not 3 |
| 2026-09-06 | grant + profile | **360** | `0600333` (#87) | factory-runner's `max_turns` moved 40 → 60 |
| 2026-09-06 | orchestrator known-good ceiling | **360** | #237 | eighteen days after the profile passed 4 |

## The four sites, and why the relations differ

**1. The GRANT** (`approval-policy.toml`, intent-packages) — the ceiling a package may *declare*.

**2. The STAMP** (`profiles/dependency_update.py::BUDGETS`) — what a unit envelope actually
*carries*. `build_envelope` stamps `dict(BUDGETS)` verbatim.

Grant and stamp are held **EQUAL**, by a test. Not `<=`: a stamp under the grant is exactly the
defect that existed for weeks — every unit the lane emitted carried a thirtieth of the budget its
own package had been approved for — so an ordering check passes against the very thing it would
exist to catch.

**3. The RUNNER'S `max_turns`** (factory-runner's workflow) — the only thing that actually caps one
attempt. The stamp must be **at or above** `max_attempts × max_turns × 2`; a **floor**, not an
equality. Over-provisioning costs nothing, because nothing checks spend mid-run, and the number's
whole job is to make the **recoverable** gate (`attempts_exhausted`, curable by `approve_retry`)
bind before the **unrecoverable** one (`budget_exceeded`, curable by nothing, on a write-once
envelope). Under-counting inverts precisely that.

**4. The RECOGNITION CEILING** (`factory-policy.toml`, this repository) — what may be recognised as
a known-good pattern *without a human looking at the envelope*. Held **EQUAL** to the stamp, and
the direction is the **opposite** of the floor above. Here the number decides what a person still
sees: a ceiling above the profile silently widens a human gate, and `_within` is
`envelope <= ceiling`, so a ceiling *below* it recognises nothing at all.

## Why site 4 sat at 4 for eighteen days

`_within(360, 4)` is False, so from the moment the profile passed 4 the known-good pattern
recognised **nothing**. Every uv pin bump drew `authority_envelope_novel` and went to the human
gate. ADR-0011 was Accepted and its mechanism was inert.

It **failed closed**, so nothing unsafe happened — and nothing said so. Its own control test was
green and false: `uv_bump()` deep-copied the byte-pinned cross-repo *specimen*, frozen at
`max_llm_calls: 4`, and overwrote only `constraints`, so the budgets under test were the
specimen's rather than the profile's. The docstring claimed the profile's provenance from the
start and the code did not honour it.

**A docstring asserting a provenance the code does not have is worse than no docstring** — it is
what a reader checks instead of the code.

## Consequences

- **Four sites move together or a check reds.** `scripts/check_profile_budget_agreement.py`
  compares the pattern, the test constant and intent-packages' `BUDGETS` at `main`, and
  intent-packages' own `check_routing_policy_compatibility.py` holds the stamp at or above the
  runner's `max_turns` at the recommended caller pin. Neither existed while the divergence did.
- **Raising the recognition ceiling is a standing-authority act, not maintenance.** Its only effect
  is to withhold `authority_envelope_novel` — to suppress a human gate — so it is decided here
  rather than inferred from the profile having moved.
- **This ADR is the trail that was missing.** The expansions were real, deliberate and individually
  well-reasoned; each lived in a pull request body and a code comment, and none in
  `docs/decisions/`. That is how the fourth site was able to sit at a superseded value while three
  others agreed.

## The transmissible part

**A comment that names a divergence is not a check.** It happened twice in this one number:
`approval-policy.toml`'s comment said *"120 rather than the profile default of 4"* and nothing
compared them for weeks; the known-good pattern's own prose said budgets track the profile's
defaults and nothing compared those either. Both were true statements sitting beside the thing they
described, and both stayed true while the values drifted apart.

When one value must hold in several places, write the comparison and let the comment explain it.
