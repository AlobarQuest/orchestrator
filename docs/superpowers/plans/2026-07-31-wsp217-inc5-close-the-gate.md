# WS-P2.17 Increment 5 — Close the gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three gaps Increment 4 left, so the gate can be tested by a human rather than by
a suite: the reversibility fact stops guessing, the evidence sits *inside* the decision, and a
`FAILED` unit stops being invisible.

**Architecture:** All three are small and already diagnosed — two by the Increment 4 build session,
one an HQ spec error it uncovered. No new module, no new route, no migration. This is the last
increment of WS-P2.17.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI, Jinja2, pytest. Repo: `~/Projects/orchestrator`.

**Spec:** `~/docs/software-delivery-system/2026-07-31-wsp217-human-gate-spec.md` §5.4, §5.5, §5.6.
**Predecessor:** Increment 4, merged `5ecf271`. Report:
`~/docs/software-delivery-system/2026-07-31-wsp217-inc4-build-report.md`.

---

## Global Constraints

- **NO NEW ROUTES, no new service module, no migration.** Increment 4 established that this keeps
  the change clear of the exact set-equality route inventories, the idempotency matrix, and
  `test_unreachable_guards`. **If you find yourself needing any of the three, stop and report.**
- **Every task lands its mechanism AND a production caller in the same commit.** HQ has drawn a
  "define now, wire later" boundary three times in this workstream and it cannot be green here —
  `test_unreachable_guards` reds a module with no production caller. Do not reproduce it.
- **Increment 2's form/service agreement pin must still pass.** Increment 4 kept it passing
  untouched by extracting the shared "which criteria may a person decide" rule verbatim. Task 2
  touches the same fieldset — if it reds the pin, fix your work, unless you can prove a strengthening
  the way Increment 3 did.
- **Do not weaken the markdown redaction.** `/review` HTML is human-only and full-fidelity;
  `render_evidence_pack_markdown` goes on a possibly-public PR and stays redacted.
- **Word guards.** `web.py` is in no allowlist: no bare `dispatch`, `deploy` or `merges` in route
  bodies, strings **or docstrings** — and note Increment 4 proved a **hyphenated compound tokenizes**,
  so `post-deploy` matches `deploy`. Reword; never add an allowlist entry.
- **A persistence assertion must re-read through a DIFFERENT session** — `expire_all()` on the
  writing session does not discriminate.
- **Read collected counts, never check colours.** Baseline after Increment 4: **1821 collected →
  1820 passed, 1 skipped.** `make check` exit 0 does not prove tests ran (exit 5 swallowed).
- **`make check` needs Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR`, and a migrated DB.**
  A bare clone fails ~18 tests unmodified — clean-clone control before attributing red.
- Never run two pytest suites against the test DB concurrently. `ruff format` before committing,
  never on a `.json` file.
- Merge per R12 (§5 of the handoff). **Do not deploy** — HQ drives deploys.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/orchestrator/services/decision_facts.py` | Prefer the declared rollback plan | Modify |
| `src/orchestrator/services/pending_decisions.py` | The `FAILED` kind | Modify |
| `src/orchestrator/web.py` | Carry each criterion's evidence into the form context | Modify |
| `src/orchestrator/templates/unit.html` | Evidence inside each criterion's fieldset | Modify |
| `tests/services/test_decision_facts.py`, `tests/services/test_pending_decisions.py` | Extend | Modify |
| `tests/web/test_adjudication_route.py`, `tests/web/test_queue.py` | Extend | Modify |
| `CLAUDE.md` | The word-guard refinement | Modify |

---

### Task 1: Reversibility reads the plan the author already wrote

**Files:** `src/orchestrator/services/decision_facts.py`, `tests/services/test_decision_facts.py`

**HQ got this wrong twice and the spec is corrected.** The Layer 1 finding said *"Can we back out —
no field exists."* It does. `profile_fields.rollback_plan` is a **required non-empty string** in
three of five profiles — `infrastructure_change.py:17`, `maintenance_remediation.py:21`,
`software_delivery.py:16` — absent only from `dependency-update` and `non-software-operational`. And
`package_sources.py:545` carries `"profile_fields": package.get("profile_fields")` into the
**enforcement snapshot**, so the orchestrator holds it verbatim from intake.

Increment 4 therefore renders a **class-level guess where a package-level, author-written answer is
already in the database.** `decision_facts.py`'s own docstring anticipates the override
("WS-P2.18's policy artifact is where an authoritative per-package override belongs") — the override
needs no policy artifact, only the snapshot.

**Precedence:** the declared `rollback_plan` wins; `REVERSIBILITY_BY_CHANGE_CLASS` is the fallback;
the explicit unknown is the floor. Keep the `known` flag semantics exactly as they are, and make the
**source distinguishable** — a reader must be able to tell an author's plan from an editorial
statement about a class, because only one of them is a commitment.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_declared_rollback_plan_wins_over_the_class_statement() -> None:
    # The author wrote a plan and it is required by their profile. Rendering a generic sentence
    # about the change class instead is strictly worse information.
    facts = decision_facts_for_unit(...)  # revision whose profile_fields carries rollback_plan
    assert facts["reversibility"]["known"] is True
    assert "<the declared plan text>" in facts["reversibility"]["detail"]


def test_the_class_statement_is_used_when_no_plan_is_declared() -> None:
    # dependency-update declares no rollback_plan; the class statement is the correct fallback.
    ...


def test_a_blank_or_whitespace_rollback_plan_falls_back_rather_than_rendering_empty() -> None:
    # The profile validators reject an empty string at authoring time, but the snapshot is data
    # from another repo — treat it as untrusted and fall back rather than render nothing.
    ...
```

Build the snapshot fixtures from `package_sources.py`'s actual shape, **not from this plan's prose.**

- [ ] **Step 2: Run and verify they FAIL.** Paste the verbatim output.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `.venv/bin/pytest tests/services/test_decision_facts.py -q`. Record the count.
- [ ] **Step 5: Commit** — `fix(review): prefer the author's declared rollback plan over a class guess`

---

### Task 2: The evidence sits inside the decision

**Files:** `src/orchestrator/web.py`, `src/orchestrator/templates/unit.html`,
`tests/web/test_adjudication_route.py`

Increment 4 rendered evidence content on the unit page — but the adjudication form is still **~60
lines below it**. The build session's own assessment: *"the split you deferred is the honest next
test of §5.4."* Evidence a screen away from the decision is the same defect in a milder form.

**The minimal correct fix is not a two-view split.** Increment 3 gave the adjudication form a
**fieldset per criterion**. Render *that criterion's current evidence inside its own fieldset*, so
the decision and the thing it is about are adjacent by construction and cannot drift apart again.

`web.py::_adjudicatable_criteria` **already calls `current_evidence(session, revision.id, unit.id,
criterion.ac_id)`** to compute `human_may_decide` — so carrying the row into the context costs one
extra key, not an extra query. Read that function before editing it.

Leave the page's existing Evidence section alone: it is the full, supersession-aware audit view and
serves a different reader.

- [ ] **Step 1: Write the failing tests**

Assert that, for a unit with two criteria backed by different evidence, **each criterion's fieldset
contains its own evidence content** — and that a criterion with no evidence says so inside its
fieldset rather than rendering nothing. Prove discrimination: the test must fail if both fieldsets
render the same evidence.

- [ ] **Step 2: Run and verify they FAIL.** Paste the output. *(Increment 4 shipped assertions that
      passed with nothing built because the page already printed the value elsewhere — make sure
      these are scoped to the fieldset, not the page.)*
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `.venv/bin/pytest tests/web -q`, **including Increment 2's agreement pin.**
- [ ] **Step 5: Commit** — `feat(review): render each criterion's evidence inside its own decision`

---

### Task 3: A failed unit is a decision, not a silence

**Files:** `src/orchestrator/services/pending_decisions.py`, `tests/services/test_pending_decisions.py`,
`tests/web/test_queue.py`

Increment 4's queue omits `FAILED` units, which the build session flagged rather than inventing a
seventh kind unasked. A `FAILED` unit needs a human decision: **authorize a retry, or cancel it.**
Both are existing forms on the unit page, and `(FAILED, CANCELLED)` is a HUMAN edge while
`authorize_retry` is a HUMAN action that raises `max_attempts` and returns the unit to `READY`.

This also closes a standing backlog item ("nothing surfaces FAILED units awaiting disposition").

**Read `pending_decisions.py` and follow its existing shape for a kind** — do not invent a second
pattern. Each entry names *the decision required*, not the state.

- [ ] **Step 1: Write the failing tests**

A `FAILED` unit appears, naming retry-or-cancel; it disappears once cancelled; it disappears once a
retry is authorized. Mind the queue's existing "nothing to decide does not appear" assertion.

- [ ] **Step 2: Run and verify they FAIL.** Paste the output.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `.venv/bin/pytest tests/services tests/web -q`. Record the count.
- [ ] **Step 5: Commit** — `feat(review): surface failed units awaiting disposition`

---

### Task 4: Guards, the full gate, and one doc note

- [ ] **Step 1:** `.venv/bin/pytest tests/architecture tests/idempotency -q`. Nothing should fire.
      If a route inventory or the matrix objects, a route changed — **stop and report.**

- [ ] **Step 2:** Add the word-guard refinement to `CLAUDE.md`. The existing bullet says only the
      exact bare token matches (`deployment` does not match `deploy`). Increment 4 proved a
      **hyphenated compound tokenizes**: `post-deploy` in a docstring reds the ws32 guard. Correct
      the bullet — do not delete the surrounding guidance.

- [ ] **Step 3:** `git status` clean, then `make check`. Record the collected count against 1821.

- [ ] **Step 4: Commit** — `docs: hyphenated compounds trip the scope word guard`

---

## Self-review notes

- **Closes:** the three gaps in the Increment 4 report — reversibility guessing, evidence at a
  distance from the decision, invisible failed units.
- **Deliberately still open, and named so it is not mistaken for an oversight:**
  - **AC-022**, the intake payload paste. The server structurally cannot fetch the payload
    (`caller_attested_cli_verified`), so removing it needs a staged-intake table plus two routes —
    its own increment, with the inventory and matrix cost that implies.
  - **The `waived`-authority narrowing**, with its recorded proposed rule (a human may waive only a
    criterion whose current evaluation is a failure).
  - **A full deciding/auditing split.** Task 2 makes the evidence adjacent to each decision, which
    is the substance of the complaint; two separate views remain a design question for later.
- **Known risk handed over:** Task 1's precedence rule changes what a human reads at a gate. If the
  declared plan and the class statement ever disagree in a way that looks wrong, that is a finding
  about the package's author, not about this code — **report it, do not reconcile it in code.**
