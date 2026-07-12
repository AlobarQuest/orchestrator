# Factory Improvisation Ledger — live

**Run:** the diagnostic defined in `docs/superpowers/plans/2026-07-12-factory-improvisation-diagnostic.md`
**Subject:** the `conformance-claim-helper` feature (orchestrator `PROJECT.md` P2)
**Lane:** local-heavy
**Started:** 2026-07-12
**Status:** IN PROGRESS — phase: authoring → awaiting Devon's package approval

> **The rule:** follow the contract, do not fix it. Every wall gets recorded and routed around.
> Fixes are the *output* of this run, not part of it.

---

## Ledger

### #1 — No way to create a package. `missing-command`. Non-blocking.

| | |
|---|---|
| **phase** | authoring |
| **wanted** | Create a new intent package. |
| **contract offered** | Nothing. The CLI has exactly seven commands — `hash`, `validate`, `transition`, `approve`, `revise`, `supersede`, `verify-approval`. **None of them creates a package.** The declared front door (the `project-initiation` skill, built by WS-2.3 to be the intent-authoring entry point) says only: *"Write `packages/<id>/package.yaml` + `lineage.yaml`"* — it hands you a blank page too. |
| **what we did instead** | Read an existing package (WS-P2.15) to reverse-engineer the closed schema, hand-wrote both files, then hand-wrote `lineage.yaml` including the revision hash — which required running `hash` **first**, as a separate step, to learn the value to paste in. |
| **root cause** | `intent-packages` CLI has no `init`/`new`/`scaffold`. `~/.claude/skills/project-initiation/references/intent-package-authoring.md:41`. |
| **class** | `missing-command` |
| **blocking?** | No. Cost: one subagent's full read of a prior package to recover a closed 20-key schema, plus a hash/paste round-trip. |

**Note on fairness:** I checked whether this was *my* shortcut before logging it — the declared authoring
front door is the `project-initiation` skill, and not using it would have made this gap mine, not the
system's. The skill instructs hand-writing both files. **The gap is real.**

**Falsified nothing.** This was not predicted. It is a new finding.

---

### #2 — Intake is human-only, and no human credential exists. **This blocks WS-P2.9's planned CLI front door.** `unexpressible-in-envelope`. Routed around.

> **NOT a finding:** "there is no nice intake UI." That is **known, planned, and deliberate** — the
> front door is WS-P2.9 (`factory create/validate/submit/…`, codex recommendation #1, Wave 3), and the
> system is being built bottom-up on purpose. Logging that would be noise. **The finding below is the
> constraint that WS-P2.9 will hit, which nobody has written down.**

| | |
|---|---|
| **phase** | intake |
| **wanted** | Register the approved package with the orchestrator. |
| **contract offered** | Offline payload generation (`emit-intake-payload`) — which works cleanly. **But no way to submit it non-interactively.** Three facts compose: (1) `services/package_intake.py::_require_human` demands `ActorRole.HUMAN`; (2) **all three production M2M credentials are worker / system / verifier — none is HUMAN**, so `orchestrator intake-package` cannot intake against production *at all*; (3) the human web app has `GET /intakes/{revision_id}` but **no POST route**. |
| **what we did instead** | Pasted a generated `fetch()` into browser devtools, signed in via Alobar ID. (Plus the documented quirk: the first same-origin POST behind forward-auth 401s — the fetch follows the auth 302 and degrades to GET; the retry works.) |
| **root cause** | `package_intake.py::_require_human` + no HUMAN credential type + no `POST /intakes` in `web.py`. |
| **class** | `unexpressible-in-envelope` — the step *requires* a human actor and offers a human no way to act. |
| **blocking?** | Routed around. |

**Why this matters beyond today's friction — it is a design input for WS-P2.9.**

The planned front door is a **CLI** (`factory submit`). **A CLI cannot satisfy `_require_human`.** There
is no human credential to authenticate as, and the human-actor check is not a UI preference — it is the
thing that makes the approval attestation mean something. So WS-P2.9 cannot simply wrap the existing
API; it must *first* resolve one of:

- a **human credential path** for the CLI (a device/OIDC flow against Alobar ID that yields a HUMAN
  actor), or
- a **`POST /intakes` human web route** (and then `factory submit` prints a link rather than submitting), or
- an explicit decision that **intake is permanently browser-only** and the CLI's job stops at
  `emit-intake-payload`.

**This is a real fork, and it should be decided when WS-P2.9 is scoped — not discovered inside it.**

---

## Confirmations / falsifications of the pre-registered predictions

*(filled in as the run proceeds — see the plan's §7 for the seven predictions)*

| # | Prediction | Status |
|---|---|---|
| P1 | `ac_id` UUID vs `"AC-001"` at decomposition | not yet reached |
| P2 | every automated AC → `judgment_required` | not yet reached |
| P3 | `allowed_commands` cannot express "run the tests" | not yet reached |
| P4 | a multi-AC unit cannot discharge ACs #2..N | not yet reached |
| P5 | the 15-minute lease cannot survive a real build | not yet reached |
| P6 | no `unit_pr_binding` row is written | not yet reached |
| P7 | `finalize` docs claim `runner.verification` evidence; `cli.py` never writes it | not yet reached |

---

## Phase log

**Authoring — DONE.** `packages/conformance-claim-helper` authored; `validate` exits 0 with no
warnings. Package hash `6a753677334de371e55d7bfcb2e6d3adfca959de34bf0c277e85cbc4e6976608`.

Before authoring, both of the feature's dependencies were proven importable
(`portfolio.compliance.build_rows`, `security_scan.cli.scan`) — the dry-run discipline this repo
learned from WS-6.4, where authored intent was three times never validated against executable reality.

**Seven ACs, authored naturally and deliberately NOT contorted to one.** Prediction P4 says the runner
writes one evidence row per unit and therefore cannot discharge ACs #2..N. Collapsing this feature into
a single AC to dodge that would have hidden the defect the run exists to measure. **If P4 is right, this
package will prove it.**

**Next:** Devon runs `transition --to ready_for_review` then `approve --approver devon` (both are
human-only by the skill's rule). Then: intake → decomposition → authority approval → `local-heavy-prepare`.
