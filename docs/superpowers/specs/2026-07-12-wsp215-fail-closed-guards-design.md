# WS-P2.15 — fail-closed lifecycle guards: design

**Package:** `ws-p2.15-fail-closed-lifecycle-guards` (rev 1 authored; **needs re-hash — this design changes families B and D**)
**Status:** design — pending Devon's approval
**Date:** 2026-07-12

## The thesis

> An obligation nobody is forced to discharge is not a guarantee. A guard nobody calls is not protection.

Both look identical to a healthy system from the outside, and both survive a green suite indefinitely. WS-P2.1 proved it: `upsert_pr_binding` had no production caller, and flushed without committing. Ten in-session unit tests saw nothing. A drill driving the real HTTP surface caught it in minutes.

## How this design was arrived at — and why that matters

This document is the **third** revision. Each earlier one was killed by adversarial review, and each died of *the very defect this workstream exists to eliminate*. That is not incidental; it is the strongest available evidence that the defect class is real, subtle, and not detectable by ordinary care.

1. **Rev 1** proposed a PR-binding submission guard. It would have been *simultaneously* **too strict** (no worker records a binding → every dispatched unit hard-fails at `SUBMITTED` → **the factory halts**) and **too lax** (`github.pr.create` appears nowhere in `src/` on either side; capability names are validated by nothing; the drills mint units in the *registry* vocabulary the guard would never see). **Every AC would have passed green**, because the tests wrote the binding from the harness. → Split to **WS-P2.16**.
2. **Rev 2** proposed wiring `age_out_human_gates`. Its own fix was **illegal at the database layer** (`CheckConstraint("runner_attempt > 0")`), and the alternative silently consumed a dispatch slot. → Replaced by a derived read (§1).
3. **Rev 3 (this one)** proposed a reachability guard keyed on **names**. It was **blind to 12 of its 93 subjects**, including the WS-P2.1 shape itself. → Import-resolved (§3).

**The lesson, stated once:** *authored intent is not executable reality.* Every claim below that could be checked, was checked — by running it.

---

## 1. Family B — the approval gate nobody answers

### The finding

Documented as "currently unspecified." It is not. `age_out_human_gates()` (`services/dispatch.py:230`) already exists, is unit-tested, has **zero production callers**, and is disabled by default (`config.py:29` → `None`). It is a **fourth instance of this workstream's own pattern**.

### Why wiring it is the wrong answer

Two independent facts make the written-record design unshippable:

- `models.py:481` — `CheckConstraint("runner_attempt > 0")`. The obvious fix (age-out records at `runner_attempt=0`) is **illegal at the DB layer**.
- `models.py:480` — `UniqueConstraint(work_unit_id, runner_attempt)`, and `_dispatch_work_unit` returns the **existing** record without dispatching when that pair exists. Keeping `_next_runner_attempt` means an age-out **consumes a dispatch slot**: unit stalls → aged out at attempt N → human approves → returns to `ready` → a later genuine dispatch at N is **silently short-circuited into returning the stale blocked record and never dispatches.**

Both bad. When every variant of a mechanism is bad, the mechanism is wrong.

### The design: a derived read, not a written record

`dead_letter.py:1-6` already states the correct contract:

> *"Derived LIVE from the source tables. There is no materialized dead-letter queue, so there is nothing to drift out of sync with the reality it reports."*

**A stalled approval gate is not an event that happened. It is a fact about the present**: this unit is in an approval state, and has been for too long. That is a *predicate over `(state, updated_at)`* — exactly like `_open_circuit_breakers` (`dead_letter.py:105-150`), which is **already** a derived, unpersisted entity. Stalled approvals become the **second** derived source: a precedent, not an exception.

**So:**
1. **Delete `age_out_human_gates`**, its `DispatchSettings.human_gate_age_out_seconds` field (`dispatch.py:36`), and the `config.py:29` knob. *(Safe: `SettingsConfigDict(env_prefix=…, extra="ignore")` means a stale `ORCHESTRATOR_DISPATCH_HUMAN_GATE_AGE_OUT_SECONDS` in Coolify will not fail startup closed — verified.)* **A dead config knob is the same defect as a dead function.**
2. **Add `_stalled_approvals(session, threshold)` to `dead_letter.py`**, a third source alongside `_terminal_units` and `_failed_dispatch_records`. Units in `awaiting_approval`/`awaiting_review` with `updated_at <= now - threshold`, as `DeadLetterEntry(source="stalled_approval", occurred_at=unit.updated_at, detail=<stall duration>)`.
3. **No new route, no new CLI command, no migration, no write, nothing to commit.** `GET /api/v1/dead-letter` and `orchestrator dead-letter` already exist and already carry it.
4. **`requeue_eligible` is correct for free:** `_requeue_eligible` (`:153-160`) returns `unit.state in REQUEUE_STATES` and `REQUEUE_STATES = ("failed","blocked")`. An approval state is not in it, so a stalled gate is **reported but not requeue-eligible**, with zero new code.
5. **Update the module docstring and the CLI/UI copy:** the view now enumerates terminal failures **and stalled approval gates**. A surface whose meaning silently widens is its own small version of this bug.

### The threshold is where this bug could be rebuilt

`config.py:29` is `int | None = None`, and **`None` is exactly why the guard stayed invisible for an entire workstream.** If the derived read takes `threshold: int | None` and returns `()` on `None`, **the identical defect ships in a new mechanism.**

**Therefore:** a plain `int` with a real default (**7 days**). No `None`. No "off" value. A query parameter may *narrow* the window but must not disable it. **Test:** the config default is not `None`, **and** `GET /dead-letter` with no parameters returns the stalled entry. That test is the negative control for the invisibility failure itself.

### What the derived read loses — named, not hidden

The written `DispatchRecord` bought exactly one thing: a durable record + `Event` proving *the system surfaced this at time T*. Two reasons that loss is acceptable, both of which belong in the ADR rather than passing silently:

- **Nothing reads that event today.** Writing an event nobody consumes is this workstream's own antipattern.
- Operator **notification** (as opposed to **queryability**) is genuinely lost and genuinely out of scope. That is **WS-P2.2's** problem (SLOs/observability). Say so; do not pretend it is covered.

### AC-002 needs a test this design would not otherwise write

Under the old design, the age-out was the thing proven report-only. Under this one, *"no code path transitions a unit out of `awaiting_approval`/`awaiting_review` without a named human actor"* rests entirely on the **pre-existing** transition table. That would make AC-002 satisfied by a test of code we did not write.

**So: add an explicit kernel test** enumerating every edge out of both approval states from `EDGE_ROLES` and asserting each requires `ActorRole.HUMAN`. Silence can never approve — proven against the kernel table, in the mould of `test_wsp21_invariant_scan.py`'s `test_no_worker_edge_reaches_completed`.

---

## 2. Family C — `is_expansion()`

### The finding, and the trap

`is_expansion()` (`kernel/authority.py:112`) has **zero `src/` callers**. The CLAUDE.md invariant it appears to serve **is** enforced — by a *different, independently-implemented* function: `classify_context_update()` (`kernel/context.py:57`) → `services/context.py::_effective_decision`, requiring an `Approval` with `approved_by != ""` bound to the exact `context_fingerprint`.

**They are not equivalent:**

| | `is_expansion()` | `classify_context_update()` |
|---|---|---|
| subject | two `AuthorityEnvelope`s | two standing contexts |
| capability **sets** | yes | yes |
| capability **levels** | **yes** | no |
| **budgets** (`max_attempts`, `max_llm_calls`) | **yes** | **no** |
| unknown fields → fail closed | **yes** | no |
| authority-profile rank | no | yes |

**The trap:** writing "the standing-context classifier enforces the authority-expansion invariant" into the ADR would be a **lie**, and precisely the costly kind — **WS-P2.4 (cost controls, this wave) is about budgets.** Telling its implementer that budget expansion is checked, when it is not, is worse than the dead function.

### Why deletion is nonetheless correct

Not equivalence — **structure**:

1. **`WorkUnit.authority` is write-once.** There are **exactly two** `WorkUnit(...)` construction sites: `services/packages.py:355` (`authority=…`, line 365) and `services/deployment_observations.py:250` (line 260). No `setattr`, no `update(WorkUnit).values(authority=…)`, no migration touches the column.
   *(An earlier draft of this design listed eight sites. Six of them assigned an `authority` kwarg on a **different object** — `WorkPackageRevision`, `ProposedUnit`, `DecompositionProposalUnit`, DTOs. That list was a grep for `authority=` that never checked the receiver type — the same failure that sank rev 1. The conclusion survived; the evidence did not. Recorded because the ADR's readers will trust the list.)*
2. **The live budget-raising path never used it.** `retry` (`services/claims.py:370`) raises the budget on the **column** (`unit.max_attempts = …`), guarded by `_require_retry_allowed` (HUMAN actor; unit FAILED; must strictly increase). The envelope's `budgets.max_attempts` is left stale — envelope and enforced budget **already** diverge, and `is_expansion` never saw it.

Deletion removes **zero live coverage**.

### What replaces it

- **Delete** `is_expansion()`, `AuthorityBudgets.expands`, `_limit_expands`, **and `RESTRICTION` (`kernel/authority.py:7`)** — whose only readers are `is_expansion:117-118`. *A deletion that leaves new dead code has not been done.*
- **Do NOT delete the tests wholesale.** `tests/kernel/test_authority.py:175,216` use `is_expansion(...) is True` as the *assertion vehicle* inside `test_non_mapping_constraints_fail_closed` and `test_invalid_change_class_fails_closed` — tests of **live `normalize_authority` behavior**. Delete only the `is_expansion` assertion *lines*; keep the tests (they already assert `parsed.unknown_fields` independently).
- **Fix two docstrings that are already false.** `kernel/authority.py:145` claims an unknown field is *"treated as expanding and **every admission gate treats as fail-closed**"*, and `:169` similar. **`unknown_fields` has zero consumers in `src/` outside `authority.py` itself.** The docstring is false *today*; deletion makes it flagrantly so. AC-005 is about documentation truthfulness — these are in scope.
- **Correct the docs truthfully:** `classify_context_update()` enforces **standing-context** authority expansion. **Work-unit envelope expansion — including budgets and capability levels — has NO detector, because the envelope is write-once after approval.**
- **Replace the guard with a structural guarantee:** an architecture test asserting `WorkUnit.authority` is assigned only at construction. Then if WS-P2.4 introduces a budget-raising path, **it trips the test and is forced to bring a fail-closed check with it.**

### The write-once test must be scoped correctly, or it is trivially green

An AST scan for `unit.authority = …` is **green today with zero coverage** and blind to the three ways a budget would actually be raised. It must scan **four** forms:

1. attribute assignment — `unit.authority = …`
2. `setattr(unit, "authority", …)`
3. `session.execute(update(WorkUnit).values(authority=…))` — **idiomatic here**: `session.execute(...)` appears in 12+ services (`lifecycle.py:333,346,390`; `evidence.py:745`; …). Not hypothetical.
4. **JSONB in-place item mutation** — `unit.authority["budgets"]["max_attempts"] = N`

**The negative control must plant form 4** — an *item* assignment, not an attribute assignment. It is the one a naive scan misses and the one a budget-raiser would most naturally write.

---

## 3. Family D — the guard that pays for the workstream

### The predicate IS the design, and getting it wrong is silent

Two predicates were prototyped and run against the real tree.

**v1 — reference-counting** (*"public function referenced nowhere in `src/` outside its defining module"*): **11 of 93 flagged.** Eight were false positives (public helpers whose callers live in the same module). Their allowlist entries would have read *"in fact it is called"* — **the predicate being wrong, masquerading as an exemption being justified.** That is precisely how an allowlist rots into a rule that guards nothing.

**v2 — reachability, keyed on NAMES:** 3 of 93 flagged. Looked excellent. **It was blind to 12 of its 93 subjects.**

`cli.py` is a **pure HTTP client** — `grep '^from orchestrator.services' src/orchestrator/cli.py` → **zero matches**. It reaches services only over HTTP. But its typer commands are **named identically** to the service functions they proxy (`cli.py:726 def dead_letter` vs `services/dead_letter.py:39 def dead_letter`). Seeding roots by *name* marks the **service** function reachable by the mere existence of its CLI command — which never calls it.

Permanently laundered: `dead_letter`, `status_ledger`, `check_consistency`, `reclaim_expired_claim`, `authorize_retry`, `record_approval`, `register_revision`, `resolve_dependency`, `list_evidence`, `append_evidence`, `recover_evidence`, `record_observation`. **Every one is the WS-P2.1 shape.**

**v3 — reachability, import-resolved `(module, symbol)`:** nodes are resolved through each module's import map; roots are *nodes*, not names; `ast.Attribute` edges resolve only through imported module aliases.

### The negative control that decides it

Delete the only production caller of `services/dead_letter.dead_letter` — the import at `routes.py:108` and the route body. This is **the WS-P2.1 defect, reconstructed**: a service with no production caller.

```
v2 (name-keyed):      flagged=3   dead_letter NOT flagged   ***  MISSES IT  ***
v3 (import-resolved): FLAGGED=4   dead_letter FLAGGED       ***  CATCHES IT ***
```

**This is AC-008's real test** — far stronger than the planted-isolated-function case, which *all three* predicates pass. **Ship v3.**

On the clean tree, v3 flags the same three as v2, so the fix costs no true positives:

```
roots=168 targets=93 FLAGGED=3
  age_out_human_gates      services/dispatch.py:230      -> DELETE (family B: wrong mechanism)
  is_expansion             kernel/authority.py:112       -> DELETE (family C)
  reset_token_providers    services/github_app.py:211    -> ALLOWLIST
```

### Honesty about what this guard is and is not

**It would have caught the original WS-P2.1 defect.** `git grep upsert_pr_binding` at the last commit before the fix (`c8602c2`) finds it **only in its own defining module**; `git log -S"def pr_binding" -- src/orchestrator/api/routes.py` returns exactly one commit — `c4be95d`, *"Task 16a: wire the PR binding's production writers."* **The route WAS the fix.** Before it, the function was unreachable from every root.

**But do not oversell the predicate on that bug.** `upsert_pr_binding` was the *trivially isolated* case — **reference-counting would have caught it too.** Reachability's extra power (the laundered case) is justified by the `dead_letter` control above, not by WS-P2.1. Claiming otherwise is the same species of overclaim this workstream exists to kill.

**Two things it cannot catch, stated plainly:**

| class | example | detected by |
|---|---|---|
| **internal** — no code path reaches the function | `is_expansion`, `age_out_human_gates`, `upsert_pr_binding` *pre-`c4be95d`* | **this guard** |
| **external** — reachable, but no *client* calls the endpoint | the pr-binding route *today* | **not this guard** → WS-P2.16's runner AC; and drills |
| **semantic** — reachable and called, but wrong | `upsert_pr_binding` flushing without committing | **not this guard** → the commit/re-read discipline in §4 |

The WS-P2.1 defect produced **two** failures. This guard covers **one**. Say so.

### Implementation

`tests/architecture/test_unreachable_guards.py`, in the mould of `test_wsp21_invariant_scan.py`:

- **Roots** (nodes, not names): every function in `api/routes.py`, `api/health.py`, `web.py`, `cli.py`, `main.py`, plus `src/reconciliation_runner/` (a separate program with its own CLI).
- **Edges:** calls resolved through the defining module's import map to `(module, symbol)`. Bare `ast.Name` references included (a function passed as a callback — e.g. the `after=` hook — is a real edge). Method-name `ast.Attribute` edges resolved **only** via imported module aliases, never by bare attribute name.
- **Mark:** BFS from roots. Only reachable nodes propagate — this is what refuses to launder a dead callee behind a dead caller.
- **Assert:** every public top-level function in `kernel/` and `services/` is reachable, or allowlisted with a written justification.
- **Anti-rot check** (as `test_wsp21_invariant_scan.py:130` does for the outbound allowlist): an allowlisted symbol that has *become* reachable must be removed. *An exemption nobody needs is an exemption nobody is watching.*
- **Self-tests (the guard must be shown to fail):** (a) the `dead_letter` control above; (b) a dead function laundered by a dead cross-module caller; (c) **a dead function sharing its name with a live function in another module** — the case v2 failed.

### Triage: one allowlist entry, two real fixes

| symbol | disposition | why |
|---|---|---|
| `age_out_human_gates` | **DELETE** | family B — the mechanism is wrong, not merely unwired |
| `is_expansion` | **DELETE** | family C |
| `reset_token_providers` | **ALLOWLIST** | a deliberate test-isolation seam. `github_app.py:191` holds a process-lifetime `_PROVIDERS` cache **by design**; `:211` clears it. There is **no production moment at which dropping the cache is correct**, and deleting it makes the suite order-dependent (`tests/api/test_dispatch_api.py:59`). A genuine exemption, not a masked defect. |

**The allowlist has exactly one entry, and the other two findings are fixed rather than exempted.** If this table were mostly allowlist, the guard would be theatre.

---

## 4. Transaction discipline (every writer added here)

- **A request entry point OWNS its transaction and must `session.commit()`.**
- **A function invoked INSIDE another transaction must never commit.**
- **A persistence assertion must `expire_all()` and re-read.** Asserting on the object a call returned proves only that the call returned an object — that is exactly how `upsert_pr_binding`'s missing commit passed ten tests.

*Note: §1's design adds **no writer at all**, which is the strongest form of compliance available.*

---

## 5. Drill

`scripts/drill-5-stalled-approval.sh`, in the existing mould (`drill_common.sh`; trapped teardown; throwaway DB; own ephemeral server; read-only toward production):

1. Drive a unit to `awaiting_approval` **through the public API**.
2. Age its `updated_at` past the threshold (deterministic fixture setup — permitted; runtime behavior must go through public surfaces).
3. `GET /api/v1/dead-letter` **with no threshold parameter** (this is also the invisibility control).
4. Assert: the unit appears with `source="stalled_approval"`; **its lifecycle state is unchanged**; it is **not** requeue-eligible.

`tests/architecture/test_drill_scripts.py` forbids a drill from writing its own preconditions with SQL or calling anything but its own throwaway server — the new drill must satisfy it.

**Why a drill and not just unit tests:** a test that calls a service is not evidence the service has a caller. The drill drives the real HTTP surface — the only thing that caught the WS-P2.1 defect.

---

## 6. Adjacent defect found, and its disposition

`services/deployment_observations.py:243-249` passes `"unknown_fields": []` into `normalize_authority`. But `"unknown_fields"` is **not in `KNOWN_FIELDS`**, so the minted envelope's `unknown_fields` becomes **`{"unknown_fields"}`** — verified against the real code. So `normalized()` is **not a fixed point** of `normalize_authority`: it invents exactly one unknown field, itself. `deployment_observations.py:260` stores that verbatim, so it is live in the database.

Harmless **only because** `is_expansion` is dead and nothing reads `unknown_fields`. But: **the moment anyone re-introduces an unknown-fields fail-closed gate, every post-deploy verification unit fails it.**

**Disposition:** drop the `"unknown_fields": []` key (it does nothing but corrupt the envelope) and correct the docstring. It is a two-line fix inside this package's blast radius, and this is the package that tells WS-P2.4 what is and is not checked — leaving a landmine under that message would be indefensible.

---

## 7. Definition of done

- Every guard has a **negative control**: with its body removed, a **named** test goes red.
- The reachability guard passes the **`dead_letter` control** (v2 fails it; v3 passes it) and the name-collision control.
- Every symbol it surfaces has a **disposition**, not an exemption. An allowlist entry reading *"in fact it is called"* means the predicate is wrong.
- The stalled-approval threshold has **no `None`/off value**, and `GET /dead-letter` with no parameters surfaces a stalled unit.
- Approval-state edges are proven **HUMAN-only** against `EDGE_ROLES`.
- `make check` green — **read the collected-test count**; exit 0 is not evidence (exit 5 = "no tests collected" is swallowed by the vendored Makefile).
- All drills green.
- ACs adjudicated by a **session distinct from the implementing session**, both ids recorded.
