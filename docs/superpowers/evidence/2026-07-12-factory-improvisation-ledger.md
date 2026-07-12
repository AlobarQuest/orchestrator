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

### #2 — A package can only enter the factory by pasting JavaScript into a browser console. `missing-command`. **BLOCKING — routed around.**

| | |
|---|---|
| **phase** | intake |
| **wanted** | Register the approved package with the orchestrator. |
| **contract offered** | **Nothing usable.** Three facts compose into a hole: (1) `services/package_intake.py::_require_human` demands `ActorRole.HUMAN`; (2) **all three production M2M credentials are worker / system / verifier** — *none* is HUMAN, so `orchestrator intake-package` **physically cannot intake against production**; (3) the human web app (`web.py`) has `GET /intakes/{revision_id}` — you can *view* an intake — but **no POST route to create one.** There is no human surface for the one step that requires a human. |
| **what we did instead** | Emitted the payload offline (`emit-intake-payload`, which works), then generated a `fetch()` snippet for Devon to **paste into browser devtools** while signed in via Alobar ID. Plus the documented quirk: the first same-origin POST behind forward-auth returns 401 (the fetch follows the auth 302 and degrades to GET); the retry works. |
| **root cause** | `package_intake.py::_require_human` + no HUMAN M2M credential + no `POST /intakes` in `web.py`. |
| **class** | `missing-command` (a required step has no implemented surface) |
| **blocking?** | **Yes** — routed around with devtools. **The factory's front door is a browser console.** |

**This is not a UI nicety.** Intake is step one of the governed lifecycle. Every package that has ever
entered this system entered it this way, and the previous handoff documented the workaround
(*"the human POSTs it from a browser"*) as though it were the design. **It is not the design; it is a
missing route that everyone has been routing around for long enough to forget it is missing.**

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
