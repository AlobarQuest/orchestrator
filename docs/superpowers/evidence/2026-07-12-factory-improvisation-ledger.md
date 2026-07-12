# Factory Improvisation Ledger — live

**Run:** the diagnostic defined in `docs/superpowers/plans/2026-07-12-factory-improvisation-diagnostic.md`
**Subject:** the `conformance-claim-helper` feature (orchestrator `PROJECT.md` P2)
**Lane:** local-heavy
**Started:** 2026-07-12
**Status:** RUN COMPLETE through `awaiting_review`. **13 improvisations. All 7 predictions CONFIRMED.** PR #51 open.

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

### #3 — `ac_id` means the database UUID at decomposition and the human string everywhere else. **P1 CONFIRMED.** `vocabulary`. Would have been blocking at the irreversible gate.

| | |
|---|---|
| **phase** | decomposition |
| **wanted** | Map package acceptance criteria to the unit. |
| **contract offered** | A field named **`ac_id`** (`AcMappingCommandModel.ac_id: str`, `RetainedAcCommandModel.ac_id: str`). |
| **what we did instead** | Passed the criterion's **database UUID**, because `services/decomposition.py` builds its lookup as `criteria_by_id = {str(criterion.id): criterion ...}` — keyed on `criterion.id` (UUID), while `criterion.ac_id` is the human string `"AC-001"`. **Same field name, opposite meaning.** Passing what the field is named yields a bare `package_acceptance_criterion_not_found` with no hint. |
| **root cause** | `services/decomposition.py`, `criteria_by_id` keyed on `str(criterion.id)`. |
| **class** | `vocabulary` |
| **blocking?** | Not for us — **only because the run predicted it and read the source first.** For anyone who trusts the field name, this fails **at the one gate that cannot be undone** (`decomposition_already_approved` has no supersede route; the recovery is a whole new package revision plus a fresh human approval per unit). |

**Already backlogged (P2) — this run is its first live confirmation in production.**

---

### #4 — The package→unit authority projection is manual, and NOTHING checks it. `unexpressible-in-envelope`. Routed around.

| | |
|---|---|
| **phase** | decomposition |
| **wanted** | Give the unit an authority envelope derived from the grant Devon approved at package level. |
| **contract offered** | Nothing. The package's grant is the **registry vocabulary** (`repository_read`, `repository_write`, `test_execution`, `pr_open`, `event_emit`); the unit envelope wants the **runner vocabulary** (`repo.read`, `repo.edit`, `command.run`, `github.pr.create`, …). ADR-0001 defers the projection to *"the decomposition author"* — i.e. to hand-typing. |
| **what we did instead** | Hand-projected the five registry terms into six runner capabilities and wrote them into the unit envelope. |
| **root cause** | ADR-0001's deferred projection; no code relates the two vocabularies. |
| **class** | `unexpressible-in-envelope` |
| **blocking?** | No — and that is the problem. |

**The live intake response proves the hole, in production data.** The package's authority normalised to:

```
"capabilities": {}, "unknown_fields": ["allowed", "prohibited", "requires_approval"]
```

**Every term Devon approved is in `unknown_fields`, and `capabilities` is empty.** Per this repo's own
invariant, unknown fields contribute **only their names** to the fingerprint, never their values. So the
revision's `authority_fingerprint` does not attest *what the package actually grants*. (The package
*content hash* still covers it, so this is not a tamper hole — but the fingerprint the unit approvals
chain to does not carry the grant.)

**Consequence:** nothing would have stopped me granting the unit **more** than the package allows. I
could have written `"infra_mutation": "allowed"` into the unit envelope and no gate in the system would
have related it to a package whose `prohibited` list I never touched. **This is exactly the "too lax"
half WS-P2.16 identified — now confirmed live, on real production data, rather than argued from code.**

---

### #5 — To author the unit I had to hand-type the very claim this feature exists to stop people hand-typing. `unexpressible-in-envelope`. Routed around.

| | |
|---|---|
| **phase** | decomposition |
| **wanted** | An `authority.conformance` claim for the unit. |
| **contract offered** | A free-form dict the dispatch gate then **trusts** (`_conformance_blocked_reason`). |
| **what we did instead** | Hand-typed `{"standards_touched": ["project","code","security"], "accepted_standards": [], "status": "green"}` — from memory, exactly as the backlog item complains. |
| **class** | `unexpressible-in-envelope` |
| **blocking?** | No. |

**The factory cannot yet build the thing that would stop it hand-typing.** That is not irony for its own
sake — it is the measurement: the helper under construction is the fix for the gap the run hit while
constructing it.

---

### #6 — `allowed_commands` cannot express "run the tests," and the envelope is LANE-BLIND. **P3 CONFIRMED at authoring time.** `unexpressible-in-envelope`.

| | |
|---|---|
| **phase** | decomposition |
| **wanted** | Authorize the unit to prove its own tests pass. |
| **contract offered** | `constraints.allowed_commands` — an ordered list re-executed at finalize, run **shell-less** (`subprocess.run(command.split())`), aborting on any nonzero exit. |
| **what we did instead** | Wrote `["uv sync", "uv run ruff check src tests", "uv run pyright"]` — lint and types only. **Test evidence had to be pushed out of the envelope entirely**, onto the `Quality` check on the pull-request head (AC-006). |
| **root cause** | This repo's tests need Postgres + `SECURITY_STANDARDS_DIR`; the envelope can provide neither. Per `CLAUDE.md`, `make check` **must never** appear in this repo's envelope. |
| **class** | `unexpressible-in-envelope` |
| **blocking?** | No — routed around via CI. |

**The sharper finding, which the prediction missed: the envelope has no notion of which LANE executes
it.** One `allowed_commands` list must serve both a local machine (where the full suite *would* run) and
a bare hosted runner (where it cannot). So it must be written to **the weakest lane**, and a local-heavy
unit is silently denied verification it could actually perform. There is no way to say *"run the full
suite when the lane can."*

---

### #7 — **The authority approval — required on EVERY unit — is human-only and has NO human surface.** `missing-command`. **BLOCKING — routed around with devtools.**

| | |
|---|---|
| **phase** | authority |
| **wanted** | Record the per-unit authority approval that readiness and dispatch both require. |
| **contract offered** | **A button that records the wrong kind of approval.** `web.py`'s `POST /units/{unit_id}/approval` **hardcodes `subject_type="action"`**. There is no authority-approval form anywhere in `/review`. |
| **what we did instead** | Pasted a second `fetch()` into devtools, POSTing `subject_type: "authority"` to `/api/v1/work-units/{id}/approvals`. |
| **root cause** | `web.py` approval handler hardcodes `subject_type="action"`; `services/packages.py::record_approval` calls `_require_human`; **no HUMAN M2M credential exists.** |
| **class** | `missing-command` |
| **blocking?** | **Yes** — routed around with devtools. |

**Why this is the most structurally serious finding of the run.**

Two *different* approvals exist and only one has a surface:

- **`action`** approval → what the `/review` button records. Satisfies the `AWAITING_APPROVAL → READY`
  transition guard.
- **`authority`** approval → `subject_type="authority"`, bound to `subject_revision_or_fingerprint ==
  unit.authority_fingerprint`, and it sets `unit.authority_approval_id`. **This is the one
  `exact_authority_approval()` demands** (`persistence/repositories.py:92-103`), the one readiness gates
  on, and the one `dispatch.py` gates on (`authority_approval_missing`).

Devon clicked Approve in the UI. The system recorded an `action` approval with
`context_fingerprint: None`. Readiness still returned **`authority_not_approved`** — *"no exact
authority approval is recorded."* **The human approval surface cannot authorize a unit.**

**And `orchestrator record-approval` cannot either.** It is the CLI command built for exactly this, it
takes `--subject-type authority`, and it calls `_require_human` — so with only worker/system/verifier
credentials it **can never run against production.**

**That is now TWO CLI commands that physically cannot execute against production
(`intake-package`, `record-approval`), and TWO mandatory gates reachable only by pasting JavaScript into
a browser console — one of them on _every single unit_.** This is not a missing convenience. The
authority approval is the attestation the entire authority chain hangs from: it is what makes the
envelope's fingerprint *mean* something. **It has no first-class way to be given.**

**This compounds finding #2 into a single root cause: `ActorRole.HUMAN` is required at three gates
(intake, authority approval, decomposition decision) and there is no way for a human to authenticate as
one except a browser session — for which only ONE of the three gates has a form.**

---

### #8 — **`local-heavy-renew` has NEVER worked.** It 422s on every call. **P5 CONFIRMED — and the cause is a dead command, not a short lease.** `doc-vs-reality`. **BLOCKING.**

| | |
|---|---|
| **phase** | build |
| **wanted** | Renew the 15-minute lease so a real build can outlive it. |
| **contract offered** | `factory-runner local-heavy-renew` — documented in `local-heavy-runtime.md` as *the* mechanism for exactly this. |
| **what actually happened** | `OrchestratorError: orchestrator request failed: 422 body.expected_version: Input should be a valid integer` |
| **root cause** | `client.py::renew` declares `expected_version: int \| None = None`; `cli.py::local_heavy_renew` **never passes it** → the body carries `"expected_version": null` → the API's `CommandBase` requires `expected_version: int = Field(ge=0)` → **422, always.** |
| **class** | `doc-vs-reality` |
| **blocking?** | **Yes.** There is no working way to extend a lease. |

**This is the run's most important finding so far, and it re-frames P5 entirely.**

The prediction was *"a 15-minute lease cannot survive a real build."* The truth is worse and more
specific: **the lease is renewable by design, and the renew command is dead.** It has a documented
contract, a CLI surface, and a client method — and it has *never once succeeded*, because it sends
`null` where an integer is required.

**This is exactly the WS-P2.1 defect class the whole program has been hardening against** — a surface
that exists, is documented, is reachable, and *has no working caller*. Green unit tests would not see
it: the bug is in what the client **omits**, and only a live 422 exposes it.

**And it explains the improvisation nobody could account for.** The established practice —
*"claim at the evidence-submission push, not at build start"* (Devon, option A) — has always been
described as a *preference*. **It is not a preference. It is a workaround for a broken command.** The
contract says claim-then-renew; renew doesn't work; so operators claim late. The improvisation counter
that WS-P2.2 wants to build would have been counting this every single run, without anyone knowing why.

**Routed around per the run's rule:** let the lease expire and use `local-heavy-reclaim` — which
**burns an attempt** (`claims.py::_acquire_reclaimed_claim` increments `attempt_count`), against
`max_attempts: 3`. So a real build in the declared lane costs one of its three attempts *just to exist*.

---

### #9 — 🔴 **The runner commits its own lease token into the pull request.** `doc-vs-reality`. **SECURITY. Remediated.**

| | |
|---|---|
| **phase** | finalize |
| **what happened** | `local-heavy-finalize` runs `git add -A`, which swept in its own workspace `.sds-local-heavy/` — including **`run.json`, which contains `lease_token`** — and committed and **pushed it** to PR #51. |
| **contract said** | `local-heavy-runtime.md`: *"writes a **gitignored** local workspace at `.sds-local-heavy/`"*. |
| **reality** | **`.sds-local-heavy/` is not in the orchestrator's `.gitignore`, and factory-runner never adds it.** The claim that it is gitignored is simply false. |
| **root cause** | `cli.py::_finalize_workspace` → `git add -A` with no exclusion of its own workspace dir; no repo-side gitignore; no runner-side gitignore write. |
| **class** | `doc-vs-reality` |
| **blocking?** | No — **but it is a credential exposure, and safety overrides the run's "do not fix" rule.** |

**Exposure assessment (disclosed to Devon immediately).** Repository is **private**. The token is
**ephemeral** — scoped to attempt 1, expired 20:16:33Z — and the unit had already reached `submitted`,
so the claim was consumed. Nothing rotatable. Practical risk: minimal. **A credential in git history is
still unacceptable**, so the workspace was removed from the branch (amend + `--force-with-lease`) and
`.sds-local-heavy/` added to `.gitignore`.

⚠ **This fires on EVERY local-heavy run, in EVERY repository that does not gitignore the directory.**
The fix belongs in factory-runner (exclude its own workspace from `git add`), not in each repo's
`.gitignore`. **The lane that does all of this factory's real work leaks its claim credential into the
pull request by default.**

---

### #10 — The declared branch is decoration. `doc-vs-reality`. Non-blocking.

The package declares `profile_fields.branch: sds/conformance-claim-helper`. The runner ignores it and
uses `sds/{unit_id[:8]}-attempt-{n}` (`cli.py`). **`profile_fields.branch` is never read by anything.**

---

### #11 — The head the orchestrator believes in is already wrong, and nothing can tell. **P6 CONFIRMED.** `missing-command`.

The orchestrator recorded evidence with `head_sha: 5c82a74…`. Remediating #9 force-pushed the branch;
the real head is now `9e0288ec…`. **The PR head has diverged from the orchestrator's expectation** —
which is *precisely* the condition the PR-binding + divergence alarm exists to detect.

**No `unit_pr_binding` row was written** (factory-runner's client has no such call), so
`arm_verification_head` was a no-op, and **nothing in the system will ever notice.** WS-P2.16's subject,
reproduced live and unaided, on the first real unit through the lane.

---

### #12 — The WS-5.1 verifier has no CLI command. `missing-command`. Routed around.

`orchestrator verify` is the **lifecycle transition** (→ `VERIFYING`). The **verifier itself** —
WS-5.1, which evaluates each AC against evidence — is `POST /work-units/{id}/verify`, and **the CLI does
not expose it.** Ran it by hand-rolling an `httpx` call with the verifier credential.

---

### #13 — 🔴 **Two bugs, each masking the other. P2 CONFIRMED — and it validates the WS-P2.16 review's kill, in production.** `vocabulary`.

The verifier's verdict on all six mapped ACs:

| AC | evidence | verifier says |
|---|---|---|
| AC-001 | **present** (`f645738a…`) | `judgment_required` — *"automated_test requires review"* |
| AC-002 … AC-006 | **`evidence_id: null` — NONE** | `judgment_required` — *"automated_test requires review"* |

**The vocabulary check fires BEFORE the evidence-is-missing check** (`verifier_evaluators.py:51-53`). So
an AC with **no evidence whatsoever** is *indistinguishable* from one that is fully evidenced. The
verifier cannot tell *"nothing was submitted"* from *"I cannot evaluate what was submitted."*

**Therefore the `evidence_type` bug is currently MASKING the one-evidence-row-per-unit bug (#P4).**

And therefore — **"just add `automated_test` to `DETERMINISTIC_TYPES`"**, which is what revisions 1 and 2
of the WS-P2.16 plan proposed, and what the P1 backlog item *literally instructed a future session to
do* until it was corrected today — **would flip AC-002..AC-006 from `judgment_required` to
`failed_closed`** → `REVISION_REQUIRED` → the re-attempt writes the same single evidence row →
`REVISION_REQUIRED` again → **`max_attempts` → FAILED. On every multi-AC unit. The factory stops.**

The fourth adversarial review predicted this from the code. **This run demonstrated it in production, on
a real unit, with real evidence.** The two defects have been holding each other up, and either one fixed
alone brings the factory down.

**This single result justifies the entire diagnostic.**

---

## Confirmations / falsifications of the pre-registered predictions

*(filled in as the run proceeds — see the plan's §7 for the seven predictions)*

| # | Prediction | Status |
|---|---|---|
| P1 | `ac_id` UUID vs `"AC-001"` at decomposition | **CONFIRMED** (#3) |
| P2 | every automated AC → `judgment_required` | **CONFIRMED** (#13) — and it MASKS P4 |
| P3 | `allowed_commands` cannot express "run the tests" | **CONFIRMED** (#6) — and it is worse: the envelope is lane-blind |
| P4 | a multi-AC unit cannot discharge ACs #2..N | **CONFIRMED** — 1 evidence row for 6 mapped ACs |
| P5 | the 15-minute lease cannot survive a real build | **CONFIRMED** (#8) — and the cause is that `local-heavy-renew` is DEAD, not that the lease is short |
| P6 | no `unit_pr_binding` row is written | **CONFIRMED** (#11) — and the head has ALREADY diverged, undetected |
| P7 | `finalize` docs claim `runner.verification` evidence; `cli.py` never writes it | **CONFIRMED** — only `runner.pr.opened` was written |

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
