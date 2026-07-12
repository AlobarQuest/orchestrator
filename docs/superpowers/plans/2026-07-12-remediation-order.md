# Factory Remediation — the complete list, in the order it must be worked

**Date:** 2026-07-12
**Supersedes the ordering in:** `2026-07-12-factory-next-steps.md` (written before finding #14, which
reorders everything).
**Sources:** the improvisation diagnostic (15 findings, `docs/superpowers/evidence/2026-07-12-factory-improvisation-ledger.md`)
**and** the six adversarial reviews of the WS-P2.16 plan (8 further defects, `2026-07-12-wsp216-pr-binding-chain.md`).

> **Nothing here is a proposal.** Every item is a defect that was observed — either in production, or
> demonstrated in code by a reviewer who tried to break a plan and succeeded. Twenty-three items.

---

## The ordering principle

Three rules decide the sequence, and they are the reason this is not simply "P0 first":

1. **You cannot fix what you cannot see.** Production is running a months-old image. Until `prod ==
   main`, *every* diagnosis is against a system nobody is running, and every fix ships into a void.
   **Phase 0 is not negotiable and nothing may precede it.**
2. **Stop the bleeding before the surgery.** Two defects fire on *every single run* — one leaks a
   credential, one silently forces an improvisation. Both are ~10-line fixes. They come before any
   substantial work, because every subsequent phase runs the factory.
3. **A fix that halts the factory is worse than the bug.** The masked pair (§2) must ship whole. The
   PR-binding guard (§4) must ship in a fixed order. Where an item can halt the factory, that
   constraint outranks its priority.

---

## PHASE 0 — See reality. **Nothing may precede this.**

*One session. It is an infra mutation, so it gets its own session per the standing rule.*

| # | Item | Evidence |
|---|---|---|
| **0.1** | **Deploy `main` to production.** Production serves `d6d73b3-ws64-verifier-amd64`, a **WS-6.4-era image**. WS-P2.1 (PR #47) and WS-P2.15 (PR #50) have been merged for days and **never deployed**. | ledger #14 |
| **0.2** | **Verify the routes are live** against the OpenAPI, not against `main`. Absent from production today: `recover-evidence`, `dead-letter`, `requeue`, `reconciliation/detect`, `consistency-check`, **`pr-binding`**. | ledger #14 |
| **0.3** | **Re-run the five drills AGAINST PRODUCTION.** They currently run against a *local* orchestrator and have never touched production — yet they are what marked exit criterion #5 MET. | ledger #14 |
| **0.4** | **Re-baseline the program exit criteria.** #7 (*"operator status and recovery controls exist"*) is marked **MET**, citing five routes **production does not serve**. #5 and #13 need the same audit. | ledger #14 |
| **0.5** | **Add the guard that would have caught it:** a check that fails when a criterion is marked MET on a route production does not serve. *"Merged" was recorded as "done", and nothing checks.* | ledger #14 |

> **Why this outranks even the security leak:** the leak is bounded (private repo, ephemeral token).
> The deploy gap means **every other item on this list is being reasoned about against the wrong
> system** — including a whole workstream (WS-P2.16) whose subject turned out not to exist in
> production, after **six adversarial reviews missed it because every one read the repo instead of
> asking production.**

---

## PHASE 1 — Stop the bleeding. Two small fixes, both in factory-runner.

*Half a session. Both are ~10 lines. Every phase after this runs the factory, so these come first.*

| # | Item | Why now |
|---|---|---|
| **1.1** | 🔴 **The runner commits its own lease token into the PR.** `_finalize_workspace` runs `git add -A`, sweeping in `.sds-local-heavy/run.json`, which contains `lease_token`. The docs call that directory *"a gitignored local workspace"*; **it is not gitignored, in any repo.** Fix in factory-runner (exclude its own workspace), **not** in each repo's `.gitignore` — that is a fix you have to remember N times. **While you are in that file: `build/` is also not gitignored in factory-runner**, so the repo goes dirty on any `uv tool install` / build. Same class, same one-line fix, same commit. | Fires on **every** local-heavy run, in **every** repo. |
| **1.2** | **`local-heavy-renew` has never worked.** `client.renew` declares `expected_version: int \| None = None`; `cli.local_heavy_renew` never passes it → POSTs `null` → **422, always.** | This is why *"claim at the evidence push"* exists. It was **never a preference** — it is a workaround for a dead command, inherited through handoffs as design. Fixing it removes a standing, invisible improvisation. |

**Test both against a live-shaped API.** 1.2's bug is in what the client **omits** — no unit test that mocks the transport can see it. That is exactly how it survived.

---

## PHASE 2 — The masked pair. **Ships whole or not at all.**

*One to two sessions. The largest single piece of work on this list, and the one with a live landmine.*

**Two bugs are propping each other up.** The verifier returns `judgment_required` for every AC —
including ACs with **no evidence at all** — because the `automated_test` vocabulary check fires
**before** the evidence-is-missing check (`verifier_evaluators.py:51-53`). So the evidence-type bug is
**masking** the one-evidence-row-per-unit bug.

> ☠ **Fix either one alone and the factory halts.** Making `automated_test` deterministic — which two
> revisions of the WS-P2.16 plan proposed, and which the P1 backlog item **literally instructed a
> future session to do** until it was corrected on 2026-07-12 — flips every evidence-less AC to
> `failed_closed` → `REVISION_REQUIRED` → the retry writes the same single row → **`max_attempts` →
> FAILED. On every multi-AC unit.** Demonstrated in production, 2026-07-12.

| # | Item |
|---|---|
| **2.1** | **factory-runner writes one evidence row per MAPPED AC.** Today: `_first_ac_id` (`cli.py:219`), one `submit_evidence` call (`:569`) — AC #1 only, while a unit maps N. |
| **2.2** | **The verifier keys on the EVIDENCE ROW's `evidence_type`, not the criterion's.** This is the known evidence-row/criterion vocabulary split. It is a **prerequisite**, not a follow-up. |
| **2.3** | **A command-aware evaluator.** `exit_code` is a **hardcoded `0`** (`cli.py:486`) and `_run_command` **raises** on nonzero — so any exit-code predicate is **constant-true**. An AC reading *"the tests pass"* would auto-pass on evidence that `uv sync` ran. **Do not ship that.** |
| **2.4** | **Only then**, map the five legal package `evidence_type` values into the verifier's vocabulary. |

**Acceptance:** a unit mapped to 3+ ACs completes with real, per-AC evidence, and an AC whose evidence
is *absent* comes back **`failed_closed`** — distinguishable from one the verifier merely cannot judge.

---

## PHASE 3 — The human-actor trilemma. **A force multiplier: it makes every later phase cheaper.**

*One session, once the decision is made.*

**Three gates require `ActorRole.HUMAN`. One has a form. Two CLI commands can never run.**

| gate | human surface |
|---|---|
| package intake | ❌ none (`GET /intakes/{id}` exists; **no POST**) |
| **authority approval** — *on every unit* | ❌ none (`/review`'s Approve hardcodes `subject_type="action"`; readiness demands `"authority"`) |
| decomposition decision | ✅ `/review` |

All three M2M credentials are worker/system/verifier. **There is no HUMAN credential**, so
`orchestrator intake-package` and `orchestrator record-approval` **cannot execute against production
at all**. Both gates are crossed today by pasting `fetch()` into browser devtools.

| # | Item |
|---|---|
| **3.1** | **DECIDE** (Devon): (a) a **human credential path** for the CLI — device/OIDC against Alobar ID yielding a HUMAN actor; (b) **`POST` routes in `/review`** for intake and authority approval, with `factory submit` printing a link; or (c) an explicit ruling that these gates are **permanently browser-only**. |
| **3.2** | Implement it. **This is a hard prerequisite for WS-P2.9** (`factory submit` cannot satisfy `_require_human` by wrapping the API), and it should be settled *when WS-P2.9 is scoped, not discovered inside it.* |

---

## PHASE 4 — WS-P2.16, the PR-binding chain. **The order within it is not negotiable.**

*Plan is at rev 5 and is sound: `docs/superpowers/plans/2026-07-12-wsp216-pr-binding-chain.md`. It was
killed six times; read the dead revisions — why they were wrong is the most useful content in it.*

**Prerequisite: Phase 0.** The `pr-binding` route **is not deployed**. Every handoff says *"the route
exists, is reachable, and nothing calls it."* It exists **in code**; in production it **404s**.

| # | Item | Note |
|---|---|---|
| **4.1** | **Capability vocabulary as a SHIPPED package resource** — `src/<pkg>/capability_vocabulary.py`, **not** `tests/fixtures/`. `tests/` is in **neither** the orchestrator image (`Dockerfile:32-39`) nor factory-runner's wheel. Rev 4 put it in fixtures and **the container would not have booted.** | review #5 |
| **4.2** | **Assert DERIVATION, not a hash.** `SUPPORTED_CAPABILITIES` must be *loaded from* the vocabulary. A hash pin proves the file matches while proving nobody uses it — **the exact `can_create_pr` defect the workstream exists to fix.** | review #5 |
| **4.3** | **Ingress enforcement** of both unit fields (`authority.capabilities` **and** `required_capability`) in `register_approved_unit`. Migrate 117 fixtures across 32 files + both `drill_common.sh` seeds. | review #5 |
| **4.4** | **factory-runner: `pr_binding` client call before `submit`**; **wire `can_create_pr` to refuse at RUN START** (not at PR time — that strands the unit in `EXECUTING`); derive `pr_number` (only `pr_url` is in hand); send `expected_version=0` + an idempotency key. | reviews #2, #3 |
| **4.5** | **`binding_attempt` column** (nullable) + the **attempt-scoped** submit guard + **a new clause in `authorize_transition`** (a `TransitionGuards` field alone is inert — the kernel has only two hardcoded clauses). SYSTEM must supply the attempt or the operator repair path is locked out. | review #4 |
| **4.6** | **drill-2 and drill-4 must write bindings.** Only drill-3 does today — so either the migration reds them, or the guard gets **zero** drill coverage. | review #5 |

**Add to scope, from the diagnostic:** the guard must bind the **session/local-heavy lane**, not just
dispatched units. **Every unit that has ever run through this factory ran through that lane.**

---

## PHASE 5 — Vocabulary coherence. Fix the class, not just the instances.

| # | Item |
|---|---|
| **5.1** | **`ac_id` means two things.** `ac_mappings[].ac_id` wants the criterion's **database UUID**; evidence and adjudication want `"AC-001"`. Failure is a bare `package_acceptance_criterion_not_found` — **at the one gate that cannot be undone.** Either accept the human string (it is unique per revision — the constraint already exists) or rename the field `ac_uuid`. |
| **5.2** | **The package→unit authority projection is unchecked.** The package's grant normalises to `capabilities: {}` with `allowed`/`prohibited`/`requires_approval` in `unknown_fields` — so the revision's `authority_fingerprint` **does not attest what the package grants**, and nothing would stop a decomposition author granting a unit *more* than its package allows. (ADR-0001 deferred this projection to "the decomposition author". This is that deferral coming due.) |
| **5.3** | **The self-discovering vocabulary scan** (WS-P2.16's U5, deliberately split out): AST-scan `src/` for module-level string-constant collections used in a membership test; each must be **registered or explicitly marked**. Fail-closed by discovery, not by a registry someone must remember to update. ⚠ It is **~46 subjects, not ~9** — size it honestly, and expect the predicate to need another pass. |

---

## PHASE 6 — Ergonomics. Small, bounded, and each one removes a standing improvisation.

| # | Item |
|---|---|
| **6.1** | **`profile_fields.branch` is decoration** — the runner uses `sds/{unit_id[:8]}-attempt-{n}` and never reads it. Read it or delete it. |
| **6.2** | **The WS-5.1 verifier has no CLI command.** `orchestrator verify` is the *lifecycle transition*; the verifier itself is `POST /work-units/{id}/verify` and must be hand-rolled with `httpx`. |
| **6.3** | **No way to create an intent package.** Seven CLI commands, none of which makes one; the declared authoring front door hands you a blank page. Add `intent_packages init`. |
| **6.4** | **The authority envelope is lane-blind.** One `allowed_commands` list must serve a local machine (where the suite *would* run) and a bare hosted runner (where it cannot), so it is written to the **weakest lane**, and a local-heavy unit is denied verification it could actually perform. |
| **6.5** | **factory-runner has no `factory-runner-pilot.yml`**, so it can never be a dispatch target — `dispatch_workflow_id` is process-global and the workflow 404s → circuit breaker. Only matters if you ever want to dispatch changes *to* the runner. |

---

## ONGOING — the meta-fix

**WS-P2.2's improvisation counter** (codex #7: *"how often did an operator act outside the declared
contract"*). **Pull it forward to sit right after Phase 2.**

`local-heavy-renew` has been broken since it shipped. Every operator worked around it. The workaround
was written into the handoffs as a **preference**, and inherited by every subsequent session as though
it were a design decision. **Nobody was lying and nobody was careless — the system simply had no way to
notice.**

**A factory that cannot count the times its own contract was abandoned will keep mistaking its scar
tissue for its design.**

---

## What NOT to do

- **Do not start anywhere but Phase 0.** Every other item is currently being reasoned about against a
  system nobody is running.
- **Do not fix `evidence_type` alone.** It halts the factory. See Phase 2.
- **Do not "fix the factory" as one workstream.** Phase 2 alone is a workstream.
- **Do not treat the hosted runner's narrowness as a defect.** Dispatch is deliberately one rung on a
  ladder (`dependency-update`; docs-only is the declared next). The problem is not that the hosted
  runner is limited — it is that **the lane doing all the real work has never been governed by the
  contract it claims to follow.**
