# WS-P2.15 — implementation plan

**Design:** `docs/superpowers/specs/2026-07-12-wsp215-fail-closed-guards-design.md`
**Package:** `ws-p2.15-fail-closed-lifecycle-guards` rev 1, hash `349e7784133b2ee06054fd6906ada51674baeb3972bfec0ec2bb053a19fabcdc`
**Branch:** `sds/wsp215-fail-closed-guards`

## Proposed decomposition into work units

Three units. They are independent — no unit reads another's output — so they can be claimed in any order, and a failure in one does not strand the others. Unit 3 is the one that pays for the workstream; it is deliberately **not** bundled with the deletions it motivates, so that a problem in a deletion cannot take the detector down with it.

| unit | key | change class |
|---|---|---|
| 1 | `wsp215-stalled-approvals` | the approval gate nobody answers (family B) |
| 2 | `wsp215-authority-expansion-deletion` | the guard with no caller (family C) |
| 3 | `wsp215-unreachable-guard` | the detector (family D) |

Each unit's envelope: `repo.read`, `repo.edit`, `command.run`, `github.pr.create`, `orchestrator.claim`, `orchestrator.evidence.write`; `constraints.target_repository = AlobarQuest/orchestrator`; `constraints.allowed_commands` ordered **mutators first, verifier last** (the finalize-run re-executes the whole list before `git status`).

**`make check` must NOT appear in any envelope.** It needs Postgres, `SECURITY_STANDARDS_DIR`, and a migrated database; on a bare runner it fails 18 tests *unmodified*, and with no `.venv` it exits 0 having verified nothing. The envelope verifies `uv sync` + `uv lock --check`; the tests are gated by the named **Quality** check on the PR head, which is where the evidence belongs.

---

## Unit 1 — `wsp215-stalled-approvals`

**Deletes more than it adds. That is the point.**

### Tasks

1. **Delete `age_out_human_gates`** (`services/dispatch.py:230-286`), its `DispatchSettings.human_gate_age_out_seconds` field (`dispatch.py:36`), the `config.py:29` knob `dispatch_human_gate_age_out_seconds`, the `routes.py:527` line that populates it, and its tests (`tests/services/test_dispatch.py:310,316`).
   - **Verify:** a stale `ORCHESTRATOR_DISPATCH_HUMAN_GATE_AGE_OUT_SECONDS` in the environment must **not** fail startup closed. `SettingsConfigDict` uses `extra="ignore"` — assert it with a test that instantiates `Settings` with the stale var set. *(Production Coolify may still carry it; a settings model that rejected it would be an outage.)*

2. **Add the threshold to config** as a plain `int` with a real default:
   `dead_letter_stalled_approval_seconds: int = 604800` (7 days). **No `None`. No zero-means-off.**

3. **Add `_stalled_approvals(session, threshold)` to `services/dead_letter.py`**, as a third source in `dead_letter()` alongside `_terminal_units` and `_failed_dispatch_records`:
   - select `WorkUnit` where `state IN ('awaiting_approval','awaiting_review')` and `updated_at <= now - threshold`
   - emit `DeadLetterEntry(source="stalled_approval", unit_state=..., reason_code="approval_unanswered", detail=<stall duration>, occurred_at=unit.updated_at, requeue_eligible=_requeue_eligible(unit), ...)`
   - `_requeue_eligible` already returns `False` for approval states (`REQUEUE_STATES = ("failed","blocked")`) — **do not special-case it**; let the existing predicate carry it.
   - **No write. No commit.** The module is read-only by contract; keep it that way.

4. **Thread the threshold** from `Settings` through the `GET /api/v1/dead-letter` route (`routes.py:968`) and the `dead-letter` CLI command. A query parameter may **narrow** the window; it must not disable the report.

5. **Update the module docstring** (`dead_letter.py:1-6`) and the CLI/UI copy: the view enumerates terminal failures **and stalled approval gates**; a stalled gate is reported, not requeue-eligible.

6. **Add the kernel test** (`tests/kernel/`, mould of `test_wsp21_invariant_scan.py::test_no_worker_edge_reaches_completed`):
   enumerate every edge out of `AWAITING_APPROVAL` and `AWAITING_REVIEW` in `EDGE_ROLES` and assert each requires `ActorRole.HUMAN`. **Nothing else in this unit carries AC-002** — without this, AC-002 would be satisfied by a test of code we did not write.

7. **Drill** `scripts/drill-5-stalled-approval.sh` — see design §5. Must satisfy `tests/architecture/test_drill_scripts.py` (own throwaway DB and server; trapped teardown; no SQL preconditions; state changes only through the public API).

### Negative controls (AC evidence)
- Set the threshold default to `None` → the "surfaces with no parameters" test goes red. *(This is the invisibility bug's own control.)*
- Remove `_stalled_approvals` from `dead_letter()` → the drill goes red.

### Traps
- **Do not** re-introduce a nullable/off-able threshold. That is the bug.
- **Do not** add a route or a write. If either appears, the shape is wrong again.

---

## Unit 2 — `wsp215-authority-expansion-deletion`

### Tasks

1. **Delete** from `kernel/authority.py`: `is_expansion` (`:112-123`), `AuthorityBudgets.expands` (`:21-24`), `_limit_expands` (`:126-129`), and **`RESTRICTION` (`:7`)** — whose only readers are `is_expansion:117-118`. *A deletion that leaves new dead code has not been done; unit 3's guard will catch it if it does.*

2. **Tests — surgical, not wholesale.** `tests/kernel/test_authority.py:175,216` use `is_expansion(...) is True` as the *assertion vehicle* inside `test_non_mapping_constraints_fail_closed` and `test_invalid_change_class_fails_closed`, which test **live `normalize_authority` behavior**. Delete only those two assertion lines; **keep the tests** (they already assert `parsed.unknown_fields` independently). Delete the tests whose subject genuinely *is* `is_expansion` (`:51,66,73,87,100`).

3. **Fix two docstrings that are already false.** `kernel/authority.py:145` claims an unknown field is *"treated as expanding and every admission gate treats as fail-closed"*; `:169` similar. **`unknown_fields` has zero consumers in `src/` outside `authority.py`.** The claim is false today; the deletion makes it flagrantly so.

4. **Fix `services/deployment_observations.py:247`** — drop the `"unknown_fields": []` key passed into `normalize_authority`. `"unknown_fields"` is not in `KNOWN_FIELDS`, so it mints every post-deploy unit's envelope with `unknown_fields == {"unknown_fields"}`. Add a test that the minted envelope carries **no** unknown fields and is a fixed point of `normalize_authority`.
   *Harmless only while nothing reads unknown fields — and this is the package telling WS-P2.4 what is and is not checked. Leaving a landmine under that message is indefensible.*

5. **Correct the documentation truthfully** — `docs/decisions/0001`, `CLAUDE.md`, and the open `PROJECT.md` backlog item:
   > `classify_context_update()` enforces **standing-context** authority expansion, bound to a named human approval and the exact standing-context fingerprint. **Work-unit envelope expansion — including budget and capability-level expansion — has NO detector, because the envelope is write-once after approval.**

   **Do not write that the classifier enforces envelope expansion.** It checks capability *sets* and profile *rank*; it never touches budgets. WS-P2.4 is about budgets. A false equivalence here is worse than the dead function.

6. **Add the write-once architecture test** (`tests/architecture/`): `WorkUnit.authority` is assigned only at construction. There are **exactly two** `WorkUnit(...)` sites: `services/packages.py:355` (line 365) and `services/deployment_observations.py:250` (line 260).
   Scan **four** mutation forms — an AST scan for only the first is trivially green and blind:
   1. `unit.authority = …` (attribute assignment)
   2. `setattr(unit, "authority", …)`
   3. `session.execute(update(WorkUnit).values(authority=…))` — **idiomatic here** (`session.execute` appears in 12+ services)
   4. `unit.authority["budgets"]["max_attempts"] = N` — **in-place JSONB item mutation**

   **The negative control plants form 4.** It is the one a naive scan misses and the one a budget-raiser would most naturally write.

### Traps
- **Do not** invent an envelope-mutation path so the deleted guard has something to guard. The absence is the point; the write-once test is what makes it safe.
- The `test_ws32_scope_guards` kernel scan reads **string literals including docstrings**. Keep forbidden vocabulary out of any new kernel docstring.

---

## Unit 3 — `wsp215-unreachable-guard`

**The unit that pays for the workstream.** Written so it would catch the next instance, not merely the two we know about.

### Tasks

1. **`tests/architecture/test_unreachable_guards.py`** — design §3:
   - **Nodes are `(module, symbol)`, import-resolved.** Build a per-module import map (`from X import f`, `from X import f as g`, `import X` + `X.f()`); resolve each `ast.Call` on `ast.Name` through it; resolve `ast.Attribute` **only** via imported module aliases, never by bare attribute name.
   - **Roots are nodes, not names:** every function in `api/routes.py`, `api/health.py`, `web.py`, `cli.py`, `main.py`, plus `src/reconciliation_runner/`. **Never add a root's bare name to the reachable-name set** — that is the bug that made the name-keyed version blind to 12 of 93 symbols.
   - Include bare `ast.Name` references as edges (a function passed as a callback — e.g. the `after=` hook at `routes.py:1190` — is a real edge).
   - BFS from roots; assert every public top-level function in `kernel/` and `services/` is reachable or allowlisted.
   - **Anti-rot check** (mould: `test_wsp21_invariant_scan.py:130`): an allowlisted symbol that has *become* reachable must be removed from the allowlist.

2. **Allowlist — exactly one entry, with its justification:**
   ```
   reset_token_providers  (services/github_app.py:211)
     A deliberate test-isolation seam. github_app.py:191 holds a process-lifetime
     _PROVIDERS cache BY DESIGN; :211 clears it. There is no production moment at
     which dropping the cache is correct, and deleting it makes the suite
     order-dependent (tests/api/test_dispatch_api.py:59).
   ```
   **No other entry.** `age_out_human_gates` and `is_expansion` are *deleted* by units 1 and 2, not exempted. If a fourth symbol appears, **triage it — do not allowlist it.** An allowlist entry that would read "in fact it is called" means the predicate is wrong.

3. **Self-tests — the guard must be shown to fail.** Three controls, in order of value:
   - **(a) The one that matters:** remove the only production caller of `services/dead_letter.dead_letter` (the import at `routes.py:108` and the route body) → the guard must **FLAG** it. This is the WS-P2.1 defect reconstructed. *A name-keyed graph does not flag it; an import-resolved one does. Verified.*
   - **(b)** a function laundered behind a dead cross-module caller → flagged.
   - **(c)** a function sharing its name with a live function in another module → flagged. *(This is the case the name-keyed version failed; `resolve_dependency` exists twice in this repo — `services/packages.py:699` and `cli.py:675` — so the collision class is live.)*

4. **Pre-change-tree evidence:** run the guard against `062b260` and record that it names `is_expansion` and `age_out_human_gates` unprompted. **Run it against `062b260`, not the branch** — by then both are deleted, and the evidence would be unreproducible.

### Honesty requirement (goes in the test's module docstring)
State what the guard does **not** catch, or it reads as broader protection than it is:
- **external** — a reachable endpoint no *client* calls (the pr-binding route today) → **WS-P2.16**, and drills.
- **semantic** — reachable, called, but wrong (`upsert_pr_binding` flushing without committing) → the commit/re-read discipline.

The WS-P2.1 defect produced **two** failures. This guard covers **one**.

---

## Sequencing and evidence

1. Claim at the **evidence-submission push**, not at build start (lease is a hard 15 min; a multi-hour build would burn the 3-attempt budget on lapses representing nothing).
2. `make check` **locally** — read the **collected-test count**, not the exit code. Exit 0 proves nothing (exit 5 = "no tests collected" is swallowed by the vendored Makefile).
3. All five drills green (`scripts/run-drills.sh`, ~90s + the new one).
4. `/code-review` on the diff.
5. PR → **Quality** green **on the exact head** → claim → evidence → adjudicate.
6. **Adjudication in a session distinct from the implementing session**, both ids recorded in the evidence index (AC-018). The role was separate in WS-P2.1; the judgment was not.
7. Devon reviews and merges. No agent merges.

## What would make me stop and re-plan

- A guard that cannot be shown to fail.
- An allowlist entry I cannot justify in a sentence that isn't "in fact it is called."
- A threshold, flag, or config value that can switch the stalled-approval report off.
- Any need to add a write, a route, or a migration to unit 1 — the shape would be wrong again.
- Any need to change the cross-repo envelope fixture or `CONTRACT_SHA256`.
