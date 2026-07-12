# Factory Next Steps — derived from the improvisation diagnostic

**Date:** 2026-07-12
**Source:** `docs/superpowers/evidence/2026-07-12-factory-improvisation-ledger.md` — one real feature
driven end to end through the declared contract, local-heavy lane.
**Result:** **13 improvisations. All 7 pre-registered predictions confirmed. 3 blocking. 1 credential
exposure.**

> **Every item below is an observed failure, not a proposal.** Nothing here is derived from reading the
> code and reasoning about it — that method produced a WS-P2.16 plan that was killed **six times in one
> day**, each time by a defect that had survived a careful read. These are things that broke, in
> production, on the first real unit.

---

## Verdict on program exit criterion #10

> *"Two consecutive real workflows complete without improvisation."*

**Not met, and not close.** One workflow cost **13** improvisations. The criterion is a Wave-3 gate; it
was run here as a **measurement** instead, and it has now produced the worklist it was always going to
produce — three waves early, and at the cost of one session instead of a wave.

**The factory can build software.** The feature is real, its tests pass (1240 green), and the helper
derives exactly the conformance claim a human had typed by hand. The problem is not capability. **The
problem is that the declared contract cannot be followed without leaving it thirteen times.**

---

## P0 — Fix these together or the factory stops

### 1. The masked pair: `evidence_type` × one-evidence-row-per-unit

**The single most important finding of the day, and it is not visible from the code alone.**

The verifier returned `judgment_required` for all six mapped ACs. AC-001 **has** evidence. AC-002..006
have **`evidence_id: null` — none at all.** They are *indistinguishable*, because the `automated_test`
vocabulary check fires **before** the evidence-is-missing check (`verifier_evaluators.py:51-53`).

**So the evidence-type bug is MASKING the missing-evidence bug.** Two defects propping each other up:

- `automated_test` is in neither `DETERMINISTIC_TYPES` nor `JUDGMENT_TYPES`, so every automated AC falls
  through to `judgment_required` regardless of evidence.
- factory-runner writes **exactly one evidence row per unit**, for AC #1 only (`cli.py:219`,
  `_first_ac_id`; a single `submit_evidence` call at `:569`), while a unit maps N ACs and the verifier
  looks evidence up **per AC**.

**Fix either one alone and the factory halts.** Making `automated_test` deterministic — which plan
revisions 1 and 2 proposed, and which the P1 backlog item *literally instructed a future session to do*
until it was corrected on 2026-07-12 — flips AC-002..006 to `failed_closed` → `REVISION_REQUIRED` → the
re-attempt writes the same single row → `REVISION_REQUIRED` → **`max_attempts` → FAILED. On every
multi-AC unit.**

**Scope, and it must be one workstream:**

- factory-runner writes **one evidence row per mapped AC** (not just `_first_ac_id`).
- The verifier keys on the **evidence row's** `evidence_type`, not the criterion's — the evidence-row /
  criterion vocabulary split is a known blind spot and is a **prerequisite**, not a follow-up.
- A **command-aware** evaluator: `exit_code` is a hardcoded `0` (`cli.py:486`) and `_run_command` raises
  on nonzero, so any exit-code-based predicate is **constant-true**. An AC reading *"the tests pass"*
  would auto-pass on evidence that `uv sync` ran. Do not ship that.
- Only then: map the five package `evidence_type` values into the verifier's vocabulary.

### 2. 🔴 factory-runner commits its own lease token into the pull request

`local-heavy-finalize` runs `git add -A`, which sweeps in `.sds-local-heavy/` — including `run.json`,
which contains **`lease_token`** — and pushes it. The contract's own docs call that directory *"a
gitignored local workspace."* **It is not gitignored, in this repo or any other, and factory-runner
never adds it.**

**Fires on every local-heavy run, in every repository.** Remediated for PR #51 (private repo, ephemeral
token, already expired and consumed — minimal practical risk), but **the fix belongs in factory-runner:
exclude its own workspace from `git add`.** Do not push this onto each repo's `.gitignore`; that is a
fix that has to be remembered N times.

---

## P1

### 3. The human-actor trilemma — a hard prerequisite for WS-P2.9

**Three gates require `ActorRole.HUMAN`. Only one has a form. Two CLI commands can never run.**

| gate | requires HUMAN | human surface |
|---|---|---|
| package intake | `package_intake.py::_require_human` | ❌ **none** — `web.py` has `GET /intakes/{id}`, no POST |
| **authority approval** | `packages.py::record_approval::_require_human` | ❌ **none** — `/review`'s Approve button hardcodes `subject_type="action"`; readiness demands `subject_type="authority"` |
| decomposition decision | `_require_decision_actor` | ✅ `/review` has approve/reject |

All three production M2M credentials are **worker / system / verifier**. **There is no HUMAN
credential.** So `orchestrator intake-package` and `orchestrator record-approval` — the CLI commands
built for exactly these gates — **cannot execute against production at all.** Both gates were crossed by
pasting `fetch()` into browser devtools. **The authority approval is required on every single unit.**

This is not missing polish (the `factory` CLI front door is deliberately Wave 3). It is the constraint
**WS-P2.9 will hit**: `factory submit` cannot satisfy `_require_human` by wrapping the API. **Decide
before scoping WS-P2.9:**

- a **human credential path** for the CLI (device/OIDC against Alobar ID yielding a HUMAN actor), or
- **`POST` routes** in `/review` for intake and authority approval (and `factory submit` prints a link), or
- an explicit ruling that these gates are **permanently browser-only**.

### 4. `local-heavy-renew` has never worked — and it explains a standing improvisation

`client.py::renew` declares `expected_version: int | None = None`; `cli.py::local_heavy_renew` **never
passes it**, so the body carries `null` where `CommandBase` requires an integer. **422, every time,
always.** Documented contract, CLI surface, client method — **and no working caller, ever.**

**This is why the practice is "claim at the evidence-submission push, not at build start."** That has
always been recorded as *Devon's preference*. **It is not a preference. It is a workaround for a dead
command.** WS-P2.2's improvisation counter would have been counting this every run, and nobody could
have said why.

One-line fix. Ship it with a test that actually calls it against a live-shaped API — the bug is in what
the client **omits**, and no unit test that mocks the transport can see it.

### 5. WS-P2.16 (PR-binding chain) — now empirically justified

The plan (rev 5, `2026-07-12-wsp216-pr-binding-chain.md`) stands, **and the diagnostic proved its
subject unaided**: the orchestrator recorded `head_sha 5c82a74…`; remediating the token leak
force-pushed the branch to `9e0288ec…`. **The PR head has already diverged from what the orchestrator
believes — and with no binding written, nothing will ever notice.** That is the exact condition the
binding + divergence alarm exists for, and it happened on the first real unit, by accident.

**Add to its scope, from this run:** the guard must bind the **session/local-heavy lane**, not just
dispatched units. Every unit that has ever run through this factory ran through that lane.

---

## P2 — real, bounded, and now evidenced

- **`ac_id` means two things.** `ac_mappings[].ac_id` wants the criterion's **database UUID**; evidence
  and adjudication want `"AC-001"`. Failure is a bare `package_acceptance_criterion_not_found` — **at
  the one gate that cannot be undone.** (Backlogged P2; first live confirmation.)
- **The package→unit authority projection is unchecked.** The package's grant normalises to
  `capabilities: {}` with `allowed`/`prohibited`/`requires_approval` in `unknown_fields` — so the
  revision's `authority_fingerprint` **does not attest what the package grants**. Nothing would have
  stopped the decomposition author granting the unit *more* than the package allows. (WS-P2.16's "too
  lax" half, confirmed on production data.)
- **The authority envelope is lane-blind.** One `allowed_commands` list must serve both a local machine
  (where the full suite would run) and a bare hosted runner (where it cannot), so it must be written to
  **the weakest lane**. A local-heavy unit is silently denied verification it could actually perform.
- **`profile_fields.branch` is decoration.** The runner uses `sds/{unit_id[:8]}-attempt-{n}` and never
  reads the declared branch.
- **The WS-5.1 verifier has no CLI command.** `orchestrator verify` is the *lifecycle transition*; the
  verifier itself is `POST /work-units/{id}/verify` and had to be hand-rolled with `httpx`.
- **No way to create an intent package.** Seven CLI commands, none of which makes one; the declared
  authoring front door hands you a blank page. Authoring starts by reverse-engineering a closed 20-key
  schema out of a prior package.

---

## What NOT to do

- **Do not "fix the factory" as one workstream.** The findings have different shapes and different
  blast radii; item 1 alone is a workstream. Sequence them.
- **Do not fix `evidence_type` alone.** It halts the factory. See item 1.
- **Do not treat the hosted-runner's narrowness as the problem.** Dispatch is deliberately one rung on a
  ladder (`dependency-update`, with docs-only the declared next). The problem is not that the hosted
  runner is limited — it is that **the lane doing all the real work has never been governed by the
  contract it claims to follow.**

---

## The meta-finding

**Nothing in this system measures its own improvisation, and the one metric that would have
(codex #7 / WS-P2.2's improvisation counter) is scheduled behind the work it would have redirected.**

`local-heavy-renew` has been broken since it shipped. Every operator worked around it. The workaround
was written into the handoffs as a *preference*, and the preference was inherited by every subsequent
session as though it were a design decision. **Nobody was lying and nobody was careless — the system
simply had no way to notice.**

That is the argument for running this diagnostic again, cheaply, whenever a lane changes: **a factory
that cannot count the times its own contract was abandoned will keep mistaking its scar tissue for its
design.**
